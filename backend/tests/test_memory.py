"""The memory file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evcc_smart_limit import memory as store
from evcc_smart_limit.decide import Memory

from .conftest import NOW


def test_round_trip(tmp_path: Path) -> None:
    memory = Memory(
        last_written=0.25,
        pending=0.26,
        paused=True,
        last_soc=61.0,
        last_soc_at=NOW,
        last_odometer_km=1200.5,
        last_session_kwh=3.2,
    )
    path = tmp_path / "sub" / "memory.json"
    store.save(path, memory, 2)
    assert store.load(path, 2) == memory
    assert not path.with_suffix(".tmp").exists()


def test_round_trip_of_an_empty_memory(tmp_path: Path) -> None:
    store.save(tmp_path / "m.json", Memory(), 1)
    assert store.load(tmp_path / "m.json", 1) == Memory()


def test_missing_file_is_a_fresh_memory(tmp_path: Path) -> None:
    assert store.load(tmp_path / "none.json", 1) == Memory()


def test_another_charging_points_memory_is_not_used(tmp_path: Path, caplog) -> None:  # noqa: ANN001
    caplog.set_level("INFO")
    store.save(tmp_path / "m.json", Memory(last_written=0.3, paused=True), 1)
    assert store.load(tmp_path / "m.json", 2) == Memory()
    assert "another charging point" in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        '{"loadpoint": 1, "last_written": "0.3"}',
        '{"loadpoint": 1, "last_soc": true}',
        '{"loadpoint": 1, "last_soc_at": "2026-11-15T12:00:00"}',
        '{"loadpoint": 1, "last_soc_at": 5}',
        "[]",
    ],
)
def test_malformed_file_is_a_fresh_memory(tmp_path: Path, caplog, content: str) -> None:  # noqa: ANN001
    path = tmp_path / "m.json"
    path.write_text(content, encoding="utf-8")
    assert store.load(path, 1) == Memory()
    assert "starting with a fresh memory" in caplog.text


def test_paused_must_be_true_not_merely_truthy(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"loadpoint": 1, "paused": "yes"}), encoding="utf-8")
    assert store.load(path, 1).paused is False
