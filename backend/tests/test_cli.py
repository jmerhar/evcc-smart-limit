"""The command line: run, once, settings errors."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import httpx2
import pytest

from evcc_smart_limit import cli
from evcc_smart_limit.settings import Settings

from .conftest import FakeEvcc


@pytest.fixture
def wired(fake: FakeEvcc, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeEvcc:
    """Route the command's HTTP client to the fake evcc, with its data in a temporary directory."""
    transport = httpx2.MockTransport(fake.handler)
    real = httpx2.AsyncClient
    monkeypatch.setattr(cli.httpx2, "AsyncClient", lambda: real(transport=transport))
    monkeypatch.setenv("EVCC_URL", "http://evcc.test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return fake


def test_once_prints_the_decision_and_writes_nothing(wired: FakeEvcc, capsys) -> None:  # noqa: ANN001
    assert cli.main(["once"]) == 0
    assert capsys.readouterr().out.startswith("write: new limit | limit 0.3")
    assert wired.posts == []


def test_once_apply_writes(wired: FakeEvcc) -> None:
    assert cli.main(["once", "--apply"]) == 0
    assert wired.posts == ["/api/loadpoints/1/smartcostlimit/0.30000"]


def test_once_apply_reports_a_failure(wired: FakeEvcc) -> None:
    wired.fail = {"/api/state": 500}
    assert cli.main(["once", "--apply"]) == 1


def test_once_reports_evcc_failing(wired: FakeEvcc, capsys) -> None:  # noqa: ANN001
    wired.fail = {"/api/state": 500}
    assert cli.main(["once"]) == 1
    assert "evcc: GET /api/state: HTTP 500" in capsys.readouterr().err


def test_invalid_settings(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("LOADPOINT", "0")
    assert cli.main(["once"]) == 2
    assert "invalid settings" in capsys.readouterr().err


def test_backtest_is_dispatched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = {}

    async def fake_main(settings: Settings, args) -> int:  # noqa: ANN001
        seen["soc"] = args.soc
        return 7

    monkeypatch.setattr(cli.backtest, "main", fake_main)
    argv = ["backtest", "--from", "2026-11-01", "--to", "2026-11-02", "--soc", str(tmp_path)]
    assert cli.main(argv) == 7
    assert seen["soc"] == tmp_path


@pytest.mark.parametrize("heartbeat", [False, True])
async def test_run_ticks_until_sigterm(
    wired: FakeEvcc, monkeypatch: pytest.MonkeyPatch, heartbeat: bool
) -> None:
    if heartbeat:
        monkeypatch.setenv("HEARTBEAT_URL", "http://kuma.test/push")
    task = asyncio.create_task(cli.run(Settings()))
    for _ in range(200):
        if wired.posts and (
            not heartbeat or any(r.url.host == "kuma.test" for r in wired.requests)
        ):
            break
        await asyncio.sleep(0.01)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)
    assert wired.posts == ["/api/loadpoints/1/smartcostlimit/0.30000"]
    assert any(r.url.host == "kuma.test" for r in wired.requests) is heartbeat


def test_main_run_calls_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def fake_run(settings: Settings) -> None:
        called.append(settings.dry_run)

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setenv("DRY_RUN", "true")
    assert cli.main(["run"]) == 0
    assert called == [True]


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"URGENCY_START": "50"}, "set both URGENCY_START and URGENCY_FLOOR"),
        ({"URGENCY_FLOOR": "20"}, "set both URGENCY_START and URGENCY_FLOOR"),
        ({"URGENCY_START": "20", "URGENCY_FLOOR": "20"}, "URGENCY_FLOOR must be below"),
    ],
)
def test_urgency_settings_are_checked(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    env: dict,
    message: str,  # noqa: ANN001
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert cli.main(["once"]) == 2
    assert message in capsys.readouterr().err


def test_urgency_settings_reach_the_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URGENCY_START", "50")
    monkeypatch.setenv("URGENCY_FLOOR", "20")
    params = Settings().parameters()
    assert (params.urgency_start, params.urgency_floor) == (50, 20)
