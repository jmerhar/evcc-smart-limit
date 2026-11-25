"""The controller's memory, kept in a small JSON file in the data directory.

It survives restarts so a manual override stays respected and the last known charge level stays
usable. A missing, unreadable or malformed file, or one written for another charging point, means
starting fresh, which costs at most one overwritten manual limit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from evcc_smart_limit.decide import Memory

logger = logging.getLogger(__name__)


def _number(data: dict, key: str) -> float | None:
    """A numeric field, or None; anything else is malformed."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} is not a number")
    return float(value)


def _time(data: dict, key: str) -> datetime | None:
    """A time with its UTC offset, or None; anything else is malformed."""
    value = data.get(key)
    if value is None:
        return None
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError(f"{key} has no UTC offset")
    return at


def load(path: Path, loadpoint: int) -> Memory:
    """The memory stored at ``path`` for charging point ``loadpoint``, or a fresh one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("loadpoint") != loadpoint:
            logger.info("%s belongs to another charging point; starting fresh", path)
            return Memory()
        return Memory(
            last_written=_number(data, "last_written"),
            pending=_number(data, "pending"),
            paused=data.get("paused") is True,
            last_soc=_number(data, "last_soc"),
            last_soc_at=_time(data, "last_soc_at"),
            last_odometer_km=_number(data, "last_odometer_km"),
            last_session_kwh=_number(data, "last_session_kwh"),
        )
    except FileNotFoundError:
        return Memory()
    except OSError, ValueError, TypeError, AttributeError:
        logger.warning("could not read %s; starting with a fresh memory", path, exc_info=True)
        return Memory()


def save(path: Path, memory: Memory, loadpoint: int) -> None:
    """Store ``memory`` for charging point ``loadpoint`` at ``path``, replacing it in one step."""
    data = {
        "loadpoint": loadpoint,
        "last_written": memory.last_written,
        "pending": memory.pending,
        "paused": memory.paused,
        "last_soc": memory.last_soc,
        "last_soc_at": memory.last_soc_at.isoformat() if memory.last_soc_at else None,
        "last_odometer_km": memory.last_odometer_km,
        "last_session_kwh": memory.last_session_kwh,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
