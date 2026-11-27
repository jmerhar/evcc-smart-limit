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

#: A drive of more than this many kilometres between two decisions means the vehicle was away.
ODOMETER_STEP_KM = 0.5

#: A drop of more than this in the energy charged in the session means a new session began.
SESSION_ENERGY_STEP_KWH = 0.05


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
    odometer_km: float | None = None
    session_kwh: float | None = None


@dataclass(frozen=True)
class Parameters:
    """The settings that shape a decision."""

    window_days: float = 7.0
    min_hours: float = 1.0
    factor: float = 1.0
    efficiency: float = 0.9
    capacity_kwh: float | None = None
    charge_power_kw: float | None = None
    #: Changes smaller than this share of the limit are not written.
    min_change_ratio: float = 0.005
    soc_max_age: timedelta = timedelta(hours=6)
    #: Urgency, on when both are set: below ``urgency_start`` the hours asked for grow towards the
    #: whole window, which they reach at ``urgency_floor``.
    urgency_start: float | None = None
    urgency_floor: float | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Memory:
    """What the controller remembers between decisions (and restarts).

    ``last_written`` is the limit it last set, and ``pending`` one it is about to set, whose answer
    from evcc may be lost. A limit in evcc that matches neither means someone changed it, and the
    controller then stays out of the way (``paused``) until the limit is removed or the vehicle is
    next connected. The other fields recognise a new connection and keep the vehicle's last known
    charge level.
    """

    last_written: float | None = None
    pending: float | None = None
    paused: bool = False
    last_soc: float | None = None
    last_soc_at: datetime | None = None
    last_odometer_km: float | None = None
    last_session_kwh: float | None = None
    #: Whether the vehicle was connected at the last look at evcc.
    last_connected: bool | None = None


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
    """The charging power: the configured one, else the maximum current on the active phases."""
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


def same_limit(a: float | None, b: float | None) -> bool:
    """Whether two limits are the same, allowing for float round trips through evcc's JSON."""
    if a is None or b is None:
        return a is b
    return abs(a - b) < 1e-6


def _close_enough(limit: float, current: float, ratio: float) -> bool:
    """Whether ``current`` is within ``ratio`` of ``limit``, so writing would change little.

    Relative, so the same setting suits any currency's price scale.
    """
    return abs(limit - current) <= ratio * max(abs(limit), abs(current))


def new_session(lp: Loadpoint, memory: Memory) -> bool:
    """Whether the vehicle has been away since the last decision, though it is connected now.

    evcc reports no session identifier, and a quick unplug and replug can fall between two
    decisions, so a drive (the odometer moved) or a restarted session energy counts as well.
    """
    if not lp.connected:
        return False
    drove = (
        lp.odometer_km is not None
        and memory.last_odometer_km is not None
        and lp.odometer_km - memory.last_odometer_km > ODOMETER_STEP_KM
    )
    restarted = (
        lp.session_kwh is not None
        and memory.last_session_kwh is not None
        and memory.last_session_kwh - lp.session_kwh > SESSION_ENERGY_STEP_KWH
    )
    return drove or restarted


def _session_ended(memory: Memory) -> Memory:
    """The memory with everything that belongs to one connection forgotten.

    A manual override ends with the connection, and the charge level of a vehicle that has been
    driven (or of another vehicle) no longer applies.
    """
    return replace(
        memory,
        last_written=None,
        pending=None,
        paused=False,
        last_soc=None,
        last_soc_at=None,
    )


def observe(lp: Loadpoint, memory: Memory, now: datetime) -> Memory:
    """The memory after a look at the charging point, without deciding anything.

    Ends what belongs to a connection when the vehicle is unplugged or was away, and keeps the
    vehicle's charge level, odometer and session energy for the next look. Looking twice at the
    same state changes nothing the second time, so the controller can look often and decide less
    often.
    """
    if not lp.connected or new_session(lp, memory):
        memory = _session_ended(memory)
    memory = replace(
        memory,
        last_odometer_km=lp.odometer_km if lp.odometer_km is not None else memory.last_odometer_km,
        last_session_kwh=lp.session_kwh,
        last_connected=lp.connected,
    )
    if lp.soc is not None:
        memory = replace(memory, last_soc=lp.soc, last_soc_at=now)
    return memory


def decide(
    lp: Loadpoint, window: Window, memory: Memory, params: Parameters, now: datetime
) -> Decision:
    """Decide the charging point's smart cost limit.

    The limit is the price of the cheapest N hours in the window, where N is the time the vehicle
    needs to charge to its limit (times ``factor``, at least ``min_hours``). With the window a week
    of history plus the forecast, a coming slot passes when it is among the week's cheapest N
    hours, so a vehicle that needs N hours gets about that many over a week of similar prices.
    """
    memory = observe(lp, memory, now)

    def skip(reason: str) -> Decision:
        return Decision(Action.SKIP, reason, memory)

    if lp.heating:
        return skip("heating devices are not supported")
    if not lp.connected:
        return skip("vehicle not connected")
    if lp.mode != "smart":
        return skip(f"mode is {lp.mode}, not smart")
    if lp.plan_active:
        return skip("a charging plan is active")

    removed = lp.smart_cost_limit is None
    if removed and (memory.paused or memory.last_written is not None or memory.pending is not None):
        # Removing the limit in evcc hands control back: Smart mode without a limit never charges
        # from the grid, which is not something to protect while this tool runs.
        memory = replace(memory, paused=False, last_written=None, pending=None)
    if memory.paused:
        return skip("limit changed by hand; paused until it is removed or the vehicle unplugged")
    expected = [v for v in (memory.last_written, memory.pending) if v is not None]
    if expected:
        if not any(same_limit(lp.smart_cost_limit, v) for v in expected):
            memory = replace(memory, paused=True, pending=None)
            return Decision(
                Action.SKIP,
                f"limit changed by hand (expected {expected[0]}, found "
                f"{lp.smart_cost_limit}); paused until it is removed or the vehicle unplugged",
                memory,
            )
        # Whichever matched is what evcc holds, including a write whose answer was lost.
        memory = replace(memory, last_written=lp.smart_cost_limit, pending=None)

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
    if current is not None and _close_enough(limit, current, params.min_change_ratio):
        # The limit in evcc stands in for the one this would write, so treat it as written.
        if not params.dry_run:
            memory = replace(memory, last_written=current)
        return Decision(Action.KEEP, "current limit is close enough", memory, **common)
    return Decision(Action.WRITE, "new limit", memory, **common)
