"""The memory file."""

from __future__ import annotations

from pathlib import Path

from evcc_smart_limit import memory as store
from evcc_smart_limit.decide import Memory

from .conftest import NOW


def test_round_trip(tmp_path: Path) -> None:
    memory = Memory(last_written=0.25, paused=True, last_soc=61.0, last_soc_at=NOW)
    path = tmp_path / "sub" / "memory.json"
    store.save(path, memory)
    assert store.load(path) == memory
    assert not path.with_suffix(".tmp").exists()


def test_round_trip_of_an_empty_memory(tmp_path: Path) -> None:
    store.save(tmp_path / "m.json", Memory())
    assert store.load(tmp_path / "m.json") == Memory()


def test_missing_file_is_a_fresh_memory(tmp_path: Path) -> None:
    assert store.load(tmp_path / "none.json") == Memory()


def test_unreadable_file_is_a_fresh_memory(tmp_path: Path, caplog) -> None:  # noqa: ANN001
    path = tmp_path / "m.json"
    path.write_text("{not json", encoding="utf-8")
    assert store.load(path) == Memory()
    assert "starting with a fresh memory" in caplog.text
