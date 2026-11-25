"""The health probe."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evcc_smart_limit import healthcheck
from evcc_smart_limit.controller import STATUS_FILE


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def write(data_dir: Path, age: timedelta) -> None:
    at = datetime.now(UTC) - age
    (data_dir / STATUS_FILE).write_text(json.dumps({"at": at.isoformat(), "action": "keep"}))


def test_recent_cycle_is_healthy(data_dir: Path) -> None:
    write(data_dir, timedelta(minutes=20))
    assert healthcheck.main() == 0


def test_old_cycle_is_unhealthy(data_dir: Path, capsys) -> None:  # noqa: ANN001
    write(data_dir, timedelta(minutes=46))
    assert healthcheck.main() == 1
    assert "last successful cycle" in capsys.readouterr().err


def test_missing_status_is_unhealthy(capsys) -> None:  # noqa: ANN001
    assert healthcheck.main() == 1
    assert "no usable status file" in capsys.readouterr().err
