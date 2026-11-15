"""The container's health probe: ``python -m evcc_smart_limit.healthcheck``.

Healthy while the controller has completed a cycle that reached evcc within the last three
intervals. The probe reads only the status file, so a slow evcc never makes the probe itself hang.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

from evcc_smart_limit.controller import STATUS_FILE
from evcc_smart_limit.settings import Settings


def main() -> int:
    """Exit 0 when the last successful cycle is recent enough, 1 otherwise."""
    settings = Settings()
    try:
        status = json.loads((settings.data_dir / STATUS_FILE).read_text(encoding="utf-8"))
        at = datetime.fromisoformat(status["at"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"unhealthy: no usable status file ({error})", file=sys.stderr)
        return 1
    age = datetime.now(UTC) - at
    if age > timedelta(seconds=3 * settings.interval_seconds):
        print(f"unhealthy: last successful cycle {age} ago", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
