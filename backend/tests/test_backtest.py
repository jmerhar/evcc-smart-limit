"""The backtest: the simulated vehicle and the report."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from evcc_smart_limit import backtest
from evcc_smart_limit.backtest import Reading, Result, read_csv, simulate
from evcc_smart_limit.decide import Parameters
from evcc_smart_limit.settings import Settings

from .conftest import NOW, FakeEvcc, history_rows, slots

PARAMS = Parameters(window_days=1, min_hours=0)


def day_of_prices(cheap_from: int = 8) -> list:
    """Two days at 0.30 with the second day's quarters ``cheap_from``..+8 at 0.10."""
    second = [0.30] * cheap_from + [0.10] * 8 + [0.30] * (96 - cheap_from - 8)
    return slots(NOW - timedelta(days=1), [0.30] * 96 + second)


def test_controller_charges_in_the_cheap_slots_and_the_baseline_at_once() -> None:
    readings = [Reading(NOW, 90.0)]
    controller, baseline = simulate(
        day_of_prices(),
        readings,
        NOW,
        NOW + timedelta(days=1),
        PARAMS,
        capacity_kwh=10,
        power_kw=10,
    )
    # 10% of 10 kWh needs 1 kWh into the battery: the baseline takes it at once at 0.30, the
    # controller waits for the cheap hours.
    assert baseline.kwh == pytest.approx(1 / 0.9)
    assert baseline.average_price == pytest.approx(0.30)
    assert controller.kwh == pytest.approx(1 / 0.9)
    assert controller.average_price == pytest.approx(0.10)
    assert controller.min_soc == 90 and baseline.min_soc == 90
    assert controller.end_soc == pytest.approx(100) and baseline.end_soc == pytest.approx(100)


def test_driving_drops_both_vehicles_and_disconnection_stops_charging() -> None:
    readings = [
        Reading(NOW, 100.0),
        Reading(NOW + timedelta(minutes=30), 80.0, connected=False),
        Reading(NOW + timedelta(hours=1), 80.0, connected=False),
    ]
    controller, baseline = simulate(
        day_of_prices(),
        readings,
        NOW,
        NOW + timedelta(days=1),
        PARAMS,
        capacity_kwh=10,
        power_kw=10,
    )
    assert controller.kwh == 0 and baseline.kwh == 0
    assert controller.min_soc == 80 and controller.average_price is None


def test_readings_before_the_start_are_ignored() -> None:
    readings = [Reading(NOW - timedelta(hours=1), 100.0), Reading(NOW, 50.0)]
    _, baseline = simulate(
        day_of_prices(),
        readings,
        NOW,
        NOW + timedelta(minutes=15),
        PARAMS,
        capacity_kwh=10,
        power_kw=10,
    )
    assert baseline.min_soc == 50


def test_no_readings_after_the_start() -> None:
    assert simulate([], [Reading(NOW, 1)], NOW + timedelta(hours=1), NOW, PARAMS, 10, 10) == (
        Result(),
        Result(),
    )


def test_read_csv(tmp_path: Path) -> None:
    path = tmp_path / "soc.csv"
    path.write_text(
        "time,soc,connected\n2026-11-15T13:00:00+00:00,60,false\n2026-11-15T12:00:00+00:00,70,1\n"
    )
    assert read_csv(path) == [Reading(NOW, 70, True), Reading(NOW + timedelta(hours=1), 60, False)]
    plain = tmp_path / "plain.csv"
    plain.write_text("time,soc\n2026-11-15T12:00:00+00:00,70\n")
    assert read_csv(plain) == [Reading(NOW, 70, True)]


def test_report_lines() -> None:
    assert backtest._line("baseline", Result(kwh=10, cost=3, hours=1, min_soc=40, end_soc=75)) == (
        "baseline        10.0 kWh    1.00 h  cost     3.00  average 0.3000/kWh  "
        "lowest charge level 40%  at the end 75%"
    )
    assert backtest._line("controller", Result()) == (
        "controller  nothing charged  lowest charge level 100%  at the end 0%"
    )


def args(tmp_path: Path) -> argparse.Namespace:
    csv = tmp_path / "soc.csv"
    csv.write_text(f"time,soc\n{NOW.isoformat()},90\n")
    return argparse.Namespace(
        start=NOW, end=NOW + timedelta(days=1), soc=csv, forecast_hours=24, limit_soc=100
    )


async def test_main_fetches_prices_and_prints(
    fake: FakeEvcc,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,  # noqa: ANN001
) -> None:
    fake.history = history_rows(NOW - timedelta(days=1), [0.30] * 104 + [0.10] * 8 + [0.30] * 80)
    real = backtest.httpx2.AsyncClient
    monkeypatch.setattr(
        backtest.httpx2, "AsyncClient", lambda: real(transport=httpx2.MockTransport(fake.handler))
    )
    settings = Settings(
        evcc_url="http://evcc.test", capacity_kwh=10, charge_power_kw=10, window_days=1
    )
    assert await backtest.main(settings, args(tmp_path)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("controller       1.1 kWh") and "average 0.1000" in out[0]
    assert out[1].startswith("baseline         1.1 kWh") and "average 0.3000" in out[1]


async def test_main_needs_capacity_and_power(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    assert await backtest.main(Settings(), args(tmp_path)) == 2
    assert "CAPACITY_KWH and CHARGE_POWER_KW" in capsys.readouterr().err


async def test_main_reports_evcc_failing(
    fake: FakeEvcc,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,  # noqa: ANN001
) -> None:
    fake.fail = {"/api/history": 500}
    real = backtest.httpx2.AsyncClient
    monkeypatch.setattr(
        backtest.httpx2, "AsyncClient", lambda: real(transport=httpx2.MockTransport(fake.handler))
    )
    settings = Settings(evcc_url="http://evcc.test", capacity_kwh=10, charge_power_kw=10)
    assert await backtest.main(settings, args(tmp_path)) == 1
    assert "evcc: GET /api/history/tariffs: HTTP 500" in capsys.readouterr().err


def test_real_charging_does_not_raise_the_simulated_level() -> None:
    readings = [
        Reading(NOW, 50.0, connected=False),
        Reading(NOW + timedelta(minutes=30), 90.0, connected=False),  # the real car charged
        Reading(NOW + timedelta(hours=1), 70.0, connected=False),  # and then drove 20%
    ]
    controller, _ = simulate(
        day_of_prices(),
        readings,
        NOW,
        NOW + timedelta(hours=2),
        PARAMS,
        capacity_kwh=10,
        power_kw=10,
    )
    assert controller.min_soc == 30
    assert controller.end_soc == 30


async def test_main_aligns_the_start_and_attaches_the_time_zone(
    fake: FakeEvcc, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = {}
    real_simulate = backtest.simulate

    def spy(prices, readings, start, end, *rest):  # noqa: ANN001, ANN002, ANN202
        seen.update(start=start, end=end, reading=readings[0].at)
        return real_simulate(prices, readings, start, end, *rest)

    monkeypatch.setattr(backtest, "simulate", spy)
    real = backtest.httpx2.AsyncClient
    monkeypatch.setattr(
        backtest.httpx2, "AsyncClient", lambda: real(transport=httpx2.MockTransport(fake.handler))
    )
    csv = tmp_path / "soc.csv"
    csv.write_text("time,soc\n2026-11-15T13:00:00,90\n")
    settings = Settings(
        evcc_url="http://evcc.test",
        capacity_kwh=10,
        charge_power_kw=10,
        timezone="Europe/Amsterdam",
    )
    a = argparse.Namespace(
        start=datetime(2026, 11, 15, 13, 7),
        end=datetime(2026, 11, 16, 13, 0),
        soc=csv,
        forecast_hours=24,
        limit_soc=100,
    )
    assert await backtest.main(settings, a) == 0
    assert seen["start"] == datetime(2026, 11, 15, 13, 0, tzinfo=settings.zone)
    assert seen["start"] == NOW  # 13:00 in Amsterdam is 12:00 UTC
    assert seen["end"].tzinfo is not None and seen["reading"] == NOW


async def test_main_rejects_an_end_before_the_start(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    a = args(tmp_path)
    a.end = a.start
    settings = Settings(capacity_kwh=10, charge_power_kw=10)
    assert await backtest.main(settings, a) == 2
    assert "--to must be after --from" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content", ["when,level\n2026-11-15T12:00:00Z,50\n", "time,soc\nnoon,50\n"]
)
async def test_main_reports_an_unreadable_csv(tmp_path: Path, capsys, content: str) -> None:  # noqa: ANN001
    a = args(tmp_path)
    a.soc.write_text(content)
    settings = Settings(capacity_kwh=10, charge_power_kw=10)
    assert await backtest.main(settings, a) == 2
    assert f"cannot read {a.soc}" in capsys.readouterr().err
