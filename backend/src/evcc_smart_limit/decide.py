"""One decision: which smart cost limit a charging point should have right now.

Pure, so it is tested without evcc and the backtest replays exactly what the controller does. The
caller reads evcc, calls :func:`decide`, and writes the limit when the decision says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from evcc_smart_limit.prices import Slot, Window, cheapest_hours_price, next_slot_at_or_below

#: Decimals of a written limit. Rounding is upwards, so a slot priced at the limit still passes.
LIMIT_DECIMALS = 5

#: Volts per phase, for estimating the charging power from the charging point's current.
PHASE_VOLTS = 230.0


class Action(StrEnum):
    """What the caller does with a decision."""

    WRITE = "write"  # set the limit to Decision.limit
    KEEP = "keep"  # the current limit is close enough
    SKIP = "skip"  # leave the limit alone, for Decision.reason


@dataclass(frozen=True)
class Loadpoint:
    """What a decision needs to know about the charging point and its vehicle."""

    mode: str
    connected: bool
    plan_active: bool
    heating: bool
    soc: float | None
    limit_soc: float
    smart_cost_limit: float | None
    capacity_kwh: float | None
    max_current: float
    phases: int


@dataclass(frozen=True)
class Parameters:
    """The settings that shape a decision."""

    window_days: float = 7.0
    min_hours: float = 1.0
    factor: float = 1.0
    efficiency: float = 0.9
    capacity_kwh: float | None = None
    charge_power_kw: float | None = None
    min_change: float = 0.002
    soc_max_age: timedelta = timedelta(hours=6)
    #: Urgency, on when both are set: below ``urgency_start`` the hours asked for grow towards the
    #: whole window, which they reach at ``urgency_floor``.
    urgency_start: float | None = None
    urgency_floor: float | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Memory:
    """What the controller remembers between decisions (and restarts).

    ``last_written`` is the limit it last set; a different limit in evcc means someone changed it,
    and the controller then stays out of the way (``paused``) until the vehicle is next connected.
    """

    last_written: float | None = None
    paused: bool = False
    last_soc: float | None = None
    last_soc_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    """The outcome of :func:`decide`, with what led to it for the log."""

    action: Action
    reason: str
    memory: Memory
    limit: float | None = None
    soc: float | None = None
    energy_kwh: float | None = None
    hours: float | None = None
    urgency: float = 0.0
    window_hours: float | None = None
    next_slot: Slot | None = None


def charge_power_kw(lp: Loadpoint, params: Parameters) -> float:
    """The charging power: the configured one, else the charging point's maximum current."""
    if params.charge_power_kw:
        return params.charge_power_kw
    phases = lp.phases if lp.phases in (1, 2, 3) else 3
    return lp.max_current * phases * PHASE_VOLTS / 1000


def round_up(price: float) -> float:
    """``price`` rounded up to LIMIT_DECIMALS, the precision a limit is written with."""
    scale = 10**LIMIT_DECIMALS
    # The small allowance keeps a price that is already exact (0.25 stored as 0.25000000000000006)
    # from being bumped to the next step.
    return math.ceil(price * scale - 1e-6) / scale


def urgency(soc: float, params: Parameters) -> float:
    """How far the charge level has fallen from ``urgency_start`` towards ``urgency_floor``.

    0 at or above the start (and whenever urgency is off), 1 at or below the floor.
    """
    start, floor = params.urgency_start, params.urgency_floor
    if start is None or floor is None or soc >= start:
        return 0.0
    return min(1.0, (start - soc) / (start - floor))


def hours_wanted(needed: float, soc: float, params: Parameters, window_hours: float) -> float:
    """The hours whose price becomes the limit.

    The charging time ``needed``, times ``factor`` and at least ``min_hours``. With urgency, the
    gap between that and the whole window closes with the cube of :func:`urgency`: little at
    first, then quickly near the floor, where the whole window is asked for and any known price
    passes.
    """
    base = min(max(params.min_hours, needed * params.factor), window_hours)
    return base + (window_hours - base) * urgency(soc, params) ** 3


def _same(a: float | None, b: float | None) -> bool:
    """Whether two limits are the same, allowing for float round trips through evcc's JSON."""
    if a is None or b is None:
        return a is b
    return abs(a - b) < 1e-6


def decide(
    lp: Loadpoint, window: Window, memory: Memory, params: Parameters, now: datetime
) -> Decision:
    """Decide the charging point's smart cost limit.

    The limit is the price of the cheapest N hours in the window, where N is the time the vehicle
    needs to charge to its limit (times ``factor``, at least ``min_hours``). With the window a week
    of history plus the forecast, a coming slot passes when it is among the week's cheapest N
    hours, so a vehicle that needs N hours gets about that many over a week of similar prices.
    """
    if lp.soc is not None:
        memory = replace(memory, last_soc=lp.soc, last_soc_at=now)

    def skip(reason: str) -> Decision:
        return Decision(Action.SKIP, reason, memory)

    if lp.heating:
        return skip("heating devices are not supported")
    if not lp.connected:
        # Unplugging ends a manual override: on the next connection the controller takes over
        # again, whatever the limit is by then.
        return Decision(
            Action.SKIP,
            "vehicle not connected",
            replace(memory, paused=False, last_written=None),
        )
    if lp.mode != "smart":
        return skip(f"mode is {lp.mode}, not smart")
    if lp.plan_active:
        return skip("a charging plan is active")

    if not params.dry_run:
        if memory.paused:
            return skip("limit changed by hand; paused until the vehicle is next connected")
        if memory.last_written is not None and not _same(lp.smart_cost_limit, memory.last_written):
            memory = replace(memory, paused=True)
            return Decision(
                Action.SKIP,
                f"limit changed by hand (expected {memory.last_written}, found "
                f"{lp.smart_cost_limit}); paused until the vehicle is next connected",
                memory,
            )

    soc = lp.soc
    if (
        soc is None
        and memory.last_soc is not None
        and memory.last_soc_at is not None
        and now - memory.last_soc_at <= params.soc_max_age
    ):
        soc = memory.last_soc
    if soc is None:
        return skip("charge level unknown")

    capacity = params.capacity_kwh or lp.capacity_kwh
    if not capacity:
        return skip("battery capacity unknown; set CAPACITY_KWH")

    if not window.slots:
        return skip("no prices in the window")

    energy = max(0.0, lp.limit_soc - soc) / 100 * capacity
    needed = energy / (charge_power_kw(lp, params) * params.efficiency)
    hours = hours_wanted(needed, soc, params, window.hours)
    price = cheapest_hours_price(window.slots, hours)
    assert price is not None  # the window has slots
    limit = round_up(price)

    common = {
        "limit": limit,
        "soc": soc,
        "energy_kwh": energy,
        "hours": hours,
        "urgency": urgency(soc, params),
        "window_hours": window.hours,
        "next_slot": next_slot_at_or_below(window.future, limit),
    }
    current = lp.smart_cost_limit
    if current is not None and abs(limit - current) <= params.min_change:
        # The limit in evcc stands in for the one this would write, so treat it as written.
        if not params.dry_run:
            memory = replace(memory, last_written=current)
        return Decision(Action.KEEP, "current limit is close enough", memory, **common)
    return Decision(Action.WRITE, "new limit", memory, **common)
