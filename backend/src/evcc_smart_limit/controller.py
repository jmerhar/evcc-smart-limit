"""One cycle: read evcc, decide, write the limit when the decision says so."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evcc_smart_limit import memory as memory_store
from evcc_smart_limit.decide import Action, Decision, Parameters, decide
from evcc_smart_limit.evcc import Evcc, EvccError
from evcc_smart_limit.prices import HISTORY_SLOT, build_window

logger = logging.getLogger(__name__)

MEMORY_FILE = "memory.json"
#: Written after every cycle that reached evcc; the container's health probe checks its age.
STATUS_FILE = "status.json"


def describe(decision: Decision) -> str:
    """One log line for a decision."""
    parts = [f"{decision.action.value}: {decision.reason}"]
    if decision.limit is not None:
        parts.append(f"limit {decision.limit:g}")
    if decision.soc is not None and decision.energy_kwh is not None:
        parts.append(f"charge level {decision.soc:g}% ({decision.energy_kwh:.1f} kWh to go)")
    if decision.hours is not None and decision.window_hours is not None:
        parts.append(f"cheapest {decision.hours:.1f} h of {decision.window_hours:.0f} h")
    if decision.limit is not None:
        slot = decision.next_slot
        parts.append(
            f"next slot at or below it {slot.start.isoformat(timespec='minutes')} ({slot.price:g})"
            if slot
            else "no forecast slot at or below it"
        )
    return " | ".join(parts)


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._evcc = evcc
        self._loadpoint = loadpoint
        self._params = params
        self._data_dir = data_dir
        self._on_success = on_success
        self._clock = clock

    async def decide(self) -> Decision:
        """Read evcc and decide, without writing anything or touching the memory file."""
        now = self._clock()
        lp = await self._evcc.loadpoint(self._loadpoint)
        since = now - timedelta(days=self._params.window_days) - HISTORY_SLOT
        history = await self._evcc.history(since)
        forecast = await self._evcc.forecast()
        window = build_window(history, forecast, now, self._params.window_days)
        memory = memory_store.load(self._data_dir / MEMORY_FILE)
        return decide(lp, window, memory, self._params, now)

    async def tick(self) -> Decision | None:
        """One cycle. A failure to reach evcc is logged and leaves everything as it was."""
        try:
            decision = await self.decide()
            if decision.action is Action.WRITE and not self._params.dry_run:
                assert decision.limit is not None
                await self._evcc.set_smart_cost_limit(self._loadpoint, decision.limit)
                decision = replace(
                    decision, memory=replace(decision.memory, last_written=decision.limit)
                )
        except EvccError as error:
            logger.warning("evcc: %s", error)
            return None

        prefix = "dry run, " if self._params.dry_run and decision.action is Action.WRITE else ""
        logger.info("%s%s", prefix, describe(decision))
        memory_store.save(self._data_dir / MEMORY_FILE, decision.memory)
        self._write_status(decision)
        self._on_success()
        return decision

    def _write_status(self, decision: Decision) -> None:
        """Record the cycle for the health probe."""
        status = {"at": self._clock().isoformat(), "action": decision.action.value}
        path = self._data_dir / STATUS_FILE
        path.write_text(json.dumps(status), encoding="utf-8")
