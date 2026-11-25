"""One cycle: read evcc, decide, write the limit when the decision says so."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evcc_smart_limit import memory as memory_store
from evcc_smart_limit.decide import Action, Decision, Memory, Parameters, decide
from evcc_smart_limit.evcc import OPTIMIZER_CONFLICT, Evcc, EvccError
from evcc_smart_limit.prices import HISTORY_SLOT, Slot, build_window

logger = logging.getLogger(__name__)

#: Seconds between decisions: one per slot of evcc's price history.
INTERVAL_SECONDS = 900

MEMORY_FILE = "memory.json"
#: Written after every cycle of the running service that reached evcc; the container's health
#: probe checks its age.
STATUS_FILE = "status.json"


def describe(decision: Decision) -> str:
    """One log line for a decision."""
    parts = [f"{decision.action.value}: {decision.reason}"]
    if decision.limit is not None:
        parts.append(f"limit {decision.limit:g}")
    if decision.soc is not None and decision.energy_kwh is not None:
        parts.append(f"charge level {decision.soc:g}% ({decision.energy_kwh:.1f} kWh to go)")
    if decision.hours is not None and decision.window_hours is not None:
        urgency = f", urgency {decision.urgency:.0%}" if decision.urgency else ""
        parts.append(f"cheapest {decision.hours:.1f} h of {decision.window_hours:.0f} h{urgency}")
    if decision.limit is not None:
        slot = decision.next_slot
        parts.append(
            f"next slot at or below it {slot.start.isoformat(timespec='minutes')} ({slot.price:g})"
            if slot
            else "no forecast slot at or below it"
        )
    return " | ".join(parts)


def same_rates(a: list[Slot], b: list[Slot]) -> bool:
    """Whether two forecasts give the same price wherever both have one."""
    prices = {(s.start, s.end): s.price for s in b}
    common = [(s, prices[(s.start, s.end)]) for s in a if (s.start, s.end) in prices]
    return bool(common) and all(abs(s.price - p) < 1e-9 for s, p in common)


class Controller:
    """Keeps one charging point's smart cost limit up to date, one :meth:`tick` at a time."""

    def __init__(
        self,
        evcc: Evcc,
        loadpoint: int,
        params: Parameters,
        data_dir: Path,
        *,
        on_success: Callable[[], None] = lambda: None,
        record_status: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._evcc = evcc
        self._loadpoint = loadpoint
        self._params = params
        self._data_dir = data_dir
        self._on_success = on_success
        self._record_status = record_status
        self._clock = clock

    @property
    def _memory_path(self) -> Path:
        return self._data_dir / MEMORY_FILE

    async def decide(self) -> Decision:
        """Read evcc and decide, without writing anything or touching the memory file."""
        now = self._clock()
        memory = memory_store.load(self._memory_path, self._loadpoint)
        lp = await self._evcc.loadpoint(self._loadpoint)
        forecast = await self._evcc.forecast()
        try:
            planner = await self._evcc.forecast("planner")
        except EvccError as error:
            if error.status != 404:
                raise
            planner = []
        if not same_rates(forecast, planner):
            # evcc compares the smart cost limit with its planner tariff, so a limit ranked from
            # grid prices would mean something else there.
            return Decision(
                Action.SKIP,
                "evcc charges by a planner tariff that isn't the grid price; not supported",
                memory,
            )
        since = now - timedelta(days=self._params.window_days) - HISTORY_SLOT
        history = await self._evcc.history(since)
        window = build_window(history, forecast, now, self._params.window_days)
        return decide(lp, window, memory, self._params, now)

    async def tick(self) -> Decision | None:
        """One cycle. A failure to reach evcc is logged and leaves everything as it was."""
        try:
            decision = await self.decide()
            if decision.action is Action.WRITE and not self._params.dry_run:
                decision = await self._write(decision)
        except EvccError as error:
            logger.warning("evcc: %s", error)
            return None

        prefix = "dry run, " if self._params.dry_run and decision.action is Action.WRITE else ""
        logger.info("%s%s", prefix, describe(decision))
        memory_store.save(self._memory_path, decision.memory, self._loadpoint)
        if self._record_status:
            self._write_status(decision)
        self._on_success()
        return decision

    async def _write(self, decision: Decision) -> Decision:
        """Write the decision's limit, remembering it first so a lost answer is recognised."""
        assert decision.limit is not None
        pending: Memory = replace(decision.memory, pending=decision.limit)
        memory_store.save(self._memory_path, pending, self._loadpoint)
        try:
            await self._evcc.set_smart_cost_limit(self._loadpoint, decision.limit)
        except EvccError as error:
            if error.status != OPTIMIZER_CONFLICT:
                raise
            return replace(
                decision,
                action=Action.SKIP,
                reason="evcc's optimizer controls this charging point",
                memory=replace(decision.memory, pending=None),
            )
        return replace(decision, memory=replace(pending, last_written=decision.limit, pending=None))

    def _write_status(self, decision: Decision) -> None:
        """Record the cycle for the health probe."""
        status = {"at": self._clock().isoformat(), "action": decision.action.value}
        path = self._data_dir / STATUS_FILE
        path.write_text(json.dumps(status), encoding="utf-8")
