"""The controller's memory, kept in a small JSON file in the data directory.

It survives restarts so a manual override stays respected and the last known charge level stays
usable. A missing or unreadable file means starting fresh, which costs at most one overwritten
manual limit.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from evcc_smart_limit.decide import Memory

logger = logging.getLogger(__name__)


def load(path: Path) -> Memory:
    """The memory stored at ``path``, or a fresh one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        at = data.get("last_soc_at")
        return Memory(
            last_written=data.get("last_written"),
            paused=bool(data.get("paused", False)),
            last_soc=data.get("last_soc"),
            last_soc_at=datetime.fromisoformat(at) if at else None,
        )
    except FileNotFoundError:
        return Memory()
    except OSError, ValueError, TypeError, AttributeError:
        logger.warning("could not read %s; starting with a fresh memory", path, exc_info=True)
        return Memory()


def save(path: Path, memory: Memory) -> None:
    """Store ``memory`` at ``path``, replacing the file in one step."""
    data = {
        "last_written": memory.last_written,
        "paused": memory.paused,
        "last_soc": memory.last_soc,
        "last_soc_at": memory.last_soc_at.isoformat() if memory.last_soc_at else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
