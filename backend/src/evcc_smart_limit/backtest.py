"""Replay evcc's price history to see what the limit would have done.

The vehicle is simulated: it starts at the first charge level in the CSV, loses what the real
vehicle lost while driving (every drop between two CSV rows), and charges whenever it is connected,
below its limit, and the slot's price is at or below the limit the controller would have set then.
The same vehicle charging as soon as it is connected is the baseline.

The forecast at each step is the real prices of the next ``--forecast-hours``, standing in for the
day-ahead prices evcc would have known.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx2

from evcc_smart_limit.decide import Action, Loadpoint, Memory, Parameters, decide
from evcc_smart_limit.evcc import Evcc, EvccError
from evcc_smart_limit.prices import HISTORY_SLOT, Slot, build_window
from evcc_smart_limit.settings import Settings


@dataclass(frozen=True)
class Reading:
    """The real vehicle's charge level at a time, and whether it was connected."""

    at: datetime
    soc: float
    connected: bool = True


@dataclass
class Result:
    """What one strategy charged over the replay."""

    kwh: float = 0.0
    cost: float = 0.0
    hours: float = 0.0
    min_soc: float = 100.0
    end_soc: float = 0.0

    @property
    def average_price(self) -> float | None:
        """The average price paid per kWh, or None when nothing was charged."""
        return self.cost / self.kwh if self.kwh else None


def read_csv(path: Path) -> list[Reading]:
    """Readings from a CSV with columns ``time`` (ISO 8601), ``soc`` and optionally ``connected``.

    ``connected`` takes 1/0 or true/false; without the column the vehicle counts as connected
    throughout.
    """
    readings = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            connected = str(row.get("connected", "1")).strip().lower() in ("1", "true", "yes")
            readings.append(
                Reading(datetime.fromisoformat(row["time"]), float(row["soc"]), connected)
            )
    return sorted(readings, key=lambda r: r.at)


def simulate(
    prices: list[Slot],
    readings: list[Reading],
    start: datetime,
    end: datetime,
    params: Parameters,
    capacity_kwh: float,
    power_kw: float,
    limit_soc: float = 100.0,
    forecast_hours: float = 24.0,
) -> tuple[Result, Result]:
    """Replay ``start`` to ``end`` in 15-minute steps; returns (controller, baseline)."""
    by_start = {slot.start: slot for slot in prices}
    lp_template = Loadpoint(
        mode="smart",
        connected=True,
        plan_active=False,
        heating=False,
        soc=None,
        limit_soc=limit_soc,
        smart_cost_limit=None,
        capacity_kwh=capacity_kwh,
        max_current=0,
        phases=3,
    )
    params = replace(params, charge_power_kw=power_kw, dry_run=True)
    gain = power_kw * HISTORY_SLOT.total_seconds() / 3600  # kWh drawn from the grid per step
    idx = next((i for i, r in enumerate(readings) if r.at >= start), None)
    if idx is None:
        return Result(), Result()
    first = readings[idx]
    socs = {"controller": first.soc, "baseline": first.soc}
    results = {"controller": Result(), "baseline": Result()}

    previous = first
    t = start
    while t < end:
        connected = previous.connected
        while idx < len(readings) and readings[idx].at <= t:
            reading = readings[idx]
            drop = previous.soc - reading.soc
            if drop > 0:
                for key in socs:
                    socs[key] = max(0.0, socs[key] - drop)
            previous, connected = reading, reading.connected
            idx += 1
        # The lowest level is taken after driving and before charging in the step.
        for key, result in results.items():
            result.min_soc = min(result.min_soc, socs[key])
        slot = by_start.get(t)
        if slot is not None and connected:
            horizon = [s for s in prices if t <= s.start < t + timedelta(hours=forecast_hours)]
            window = build_window(prices, horizon, t, params.window_days)
            for key, result in results.items():
                if socs[key] >= limit_soc:
                    continue
                if key == "controller":
                    lp = replace(lp_template, soc=socs[key])
                    decision = decide(lp, window, Memory(), params, t)
                    if decision.action is not Action.WRITE or slot.price > (decision.limit or 0):
                        continue
                added = min(gain * params.efficiency / capacity_kwh * 100, limit_soc - socs[key])
                drawn = added / 100 * capacity_kwh / params.efficiency
                socs[key] += added
                result.kwh += drawn
                result.cost += drawn * slot.price
                result.hours += HISTORY_SLOT.total_seconds() / 3600
        t += HISTORY_SLOT
    for key, result in results.items():
        result.end_soc = socs[key]
    return results["controller"], results["baseline"]


def add_parser(commands: argparse._SubParsersAction) -> None:
    """Register the ``backtest`` command."""
    p = commands.add_parser(
        "backtest", help="replay evcc's price history with a vehicle's charge levels"
    )
    p.add_argument("--from", dest="start", required=True, type=datetime.fromisoformat)
    p.add_argument("--to", dest="end", required=True, type=datetime.fromisoformat)
    p.add_argument("--soc", required=True, type=Path, help="CSV with time, soc[, connected]")
    p.add_argument("--forecast-hours", type=float, default=24.0)
    p.add_argument("--limit-soc", type=float, default=100.0)


def _line(name: str, result: Result) -> str:
    """One row of the report."""
    avg = result.average_price
    return (
        f"{name:<11} {result.kwh:8.1f} kWh  {result.hours:6.2f} h  cost {result.cost:8.2f}  "
        f"average {avg:.4f}/kWh  lowest charge level {result.min_soc:.0f}%  "
        f"at the end {result.end_soc:.0f}%"
        if avg is not None
        else f"{name:<11} nothing charged  lowest charge level {result.min_soc:.0f}%  "
        f"at the end {result.end_soc:.0f}%"
    )


async def main(settings: Settings, args: argparse.Namespace) -> int:
    """Fetch the prices from evcc, replay, and print both strategies."""
    params = settings.parameters()
    if not settings.capacity_kwh or not settings.charge_power_kw:
        print("backtest needs CAPACITY_KWH and CHARGE_POWER_KW", file=sys.stderr)
        return 2
    readings = read_csv(args.soc)
    since = args.start - timedelta(days=params.window_days) - HISTORY_SLOT
    async with httpx2.AsyncClient() as client:
        try:
            prices = await Evcc(client, settings.evcc_url, settings.evcc_api_key).history(since)
        except EvccError as error:
            print(f"evcc: {error}", file=sys.stderr)
            return 1
    controller, baseline = simulate(
        prices,
        readings,
        args.start,
        args.end,
        params,
        settings.capacity_kwh,
        settings.charge_power_kw,
        args.limit_soc,
        args.forecast_hours,
    )
    print(_line("controller", controller))
    print(_line("baseline", baseline))
    return 0
