"""The controller's cycle against a stand-in evcc."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from evcc_smart_limit import memory as store
from evcc_smart_limit.controller import MEMORY_FILE, STATUS_FILE, Controller, describe
from evcc_smart_limit.decide import Action, Decision, Memory, Parameters
from evcc_smart_limit.evcc import Evcc

from .conftest import NOW, FakeEvcc, history_rows, rate_rows, slots


def controller(fake: FakeEvcc, client, tmp_path: Path, beats: list, **params) -> Controller:  # noqa: ANN001
    return Controller(
        Evcc(client, "http://evcc.test"),
        1,
        Parameters(**params),
        tmp_path,
        on_success=lambda: beats.append(1),
        clock=lambda: NOW,
    )


async def test_writes_the_limit_and_remembers_it(fake: FakeEvcc, tmp_path: Path) -> None:
    fake.history = history_rows(NOW - timedelta(days=1), [0.10] * 8 + [0.30] * 88)
    beats: list = []
    async with fake.client() as client:
        decision = await controller(fake, client, tmp_path, beats).tick()
    assert decision is not None and decision.action is Action.WRITE
    assert fake.posts == ["/api/loadpoints/1/smartcostlimit/0.30000"]
    assert store.load(tmp_path / MEMORY_FILE).last_written == 0.3
    assert json.loads((tmp_path / STATUS_FILE).read_text())["action"] == "write"
    assert beats == [1]


async def test_the_next_cycle_keeps_what_it_wrote(fake: FakeEvcc, tmp_path: Path) -> None:
    beats: list = []
    async with fake.client() as client:
        ctl = controller(fake, client, tmp_path, beats)
        await ctl.tick()
        second = await ctl.tick()
    assert second is not None and second.action is Action.KEEP
    assert len(fake.posts) == 1


async def test_a_dry_run_writes_nothing_to_evcc(fake: FakeEvcc, tmp_path: Path, caplog) -> None:  # noqa: ANN001
    caplog.set_level("INFO")
    beats: list = []
    async with fake.client() as client:
        decision = await controller(fake, client, tmp_path, beats, dry_run=True).tick()
    assert decision is not None and decision.action is Action.WRITE
    assert fake.posts == []
    assert store.load(tmp_path / MEMORY_FILE).last_written is None
    assert "dry run, write: new limit" in caplog.text


async def test_evcc_failing_leaves_everything_as_it_was(
    fake: FakeEvcc,
    tmp_path: Path,
    caplog,  # noqa: ANN001
) -> None:
    fake.fail = {"/api/tariff": 500}
    beats: list = []
    async with fake.client() as client:
        assert await controller(fake, client, tmp_path, beats).tick() is None
    assert beats == [] and fake.posts == []
    assert not (tmp_path / STATUS_FILE).exists()
    assert "evcc: GET /api/tariff/grid: HTTP 500" in caplog.text


async def test_a_failed_write_is_not_remembered(fake: FakeEvcc, tmp_path: Path) -> None:
    fake.fail = {"/api/loadpoints": 500}
    beats: list = []
    async with fake.client() as client:
        assert await controller(fake, client, tmp_path, beats).tick() is None
    assert not (tmp_path / MEMORY_FILE).exists()


async def test_decide_reads_the_memory_file(fake: FakeEvcc, tmp_path: Path) -> None:
    store.save(tmp_path / MEMORY_FILE, Memory(last_written=0.25))
    fake.loadpoints[0]["smartCostLimit"] = 0.4
    async with fake.client() as client:
        decision = await controller(fake, client, tmp_path, []).decide()
    assert decision.action is Action.SKIP and decision.memory.paused


async def test_history_is_fetched_from_a_slot_before_the_window(
    fake: FakeEvcc, tmp_path: Path
) -> None:
    async with fake.client() as client:
        await controller(fake, client, tmp_path, [], window_days=2).decide()
    since = [r for r in fake.requests if r.url.path == "/api/history/tariffs"][0].url.params["from"]
    assert since == (NOW - timedelta(days=2, minutes=15)).isoformat()


def test_describe_a_write() -> None:
    slot = slots(NOW, [0.2])[0]
    text = describe(
        Decision(
            Action.WRITE,
            "new limit",
            Memory(),
            limit=0.2,
            soc=60,
            energy_kwh=40,
            hours=4.03,
            window_hours=190,
            next_slot=slot,
        )
    )
    assert text == (
        "write: new limit | limit 0.2 | charge level 60% (40.0 kWh to go) | cheapest 4.0 h of "
        "190 h | next slot at or below it 2026-11-15T12:00+00:00 (0.2)"
    )


def test_describe_without_a_coming_slot_and_a_skip() -> None:
    write = describe(Decision(Action.WRITE, "new limit", Memory(), limit=0.2))
    assert write.endswith("no forecast slot at or below it")
    assert describe(Decision(Action.SKIP, "vehicle not connected", Memory())) == (
        "skip: vehicle not connected"
    )


@pytest.mark.parametrize("hours", [24])
async def test_forecast_hours_reach_the_window(fake: FakeEvcc, tmp_path: Path, hours: int) -> None:
    fake.rates = rate_rows(NOW, [0.30] * (hours * 4))
    async with fake.client() as client:
        decision = await controller(fake, client, tmp_path, [], window_days=1).decide()
    assert decision.window_hours == 24 + hours
