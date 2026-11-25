"""A stand-in evcc and builders for loadpoints and prices."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from evcc_smart_limit.decide import Loadpoint
from evcc_smart_limit.prices import Slot

#: A fixed "now" on a slot boundary, so windows are easy to reason about.
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
QUARTER = timedelta(minutes=15)


def slots(start: datetime, prices: list[float], length: timedelta = QUARTER) -> list[Slot]:
    """Consecutive slots of ``length`` from ``start`` with the given prices."""
    return [Slot(start + i * length, start + (i + 1) * length, p) for i, p in enumerate(prices)]


def loadpoint(**overrides: object) -> Loadpoint:
    """A connected vehicle in Smart mode at 50% of 100 kWh, on 16 A over three phases."""
    values: dict[str, object] = {
        "mode": "smart",
        "connected": True,
        "plan_active": False,
        "heating": False,
        "soc": 50.0,
        "limit_soc": 100.0,
        "smart_cost_limit": None,
        "capacity_kwh": 100.0,
        "max_current": 16.0,
        "phases": 3,
    }
    values.update(overrides)
    return Loadpoint(**values)  # type: ignore[arg-type]


@dataclass
class FakeEvcc:
    """Answers the evcc endpoints the client uses, from editable fields."""

    loadpoints: list[dict] = field(default_factory=list)
    vehicles: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    rates: list[dict] = field(default_factory=list)
    #: The planner forecast; None serves the grid forecast, as evcc does for a price tariff.
    planner: list[dict] | None = None
    fail: dict[str, int] = field(default_factory=dict)  # path prefix -> status code
    posts: list[str] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        """The MockTransport handler."""
        self.requests.append(request)
        path = request.url.path
        for prefix, status in self.fail.items():
            if path.startswith(prefix):
                return httpx2.Response(status, text="failed")
        if path == "/api/state":
            body: object = {"loadpoints": self.loadpoints, "vehicles": self.vehicles}
        elif path == "/api/history/tariffs":
            since = datetime.fromisoformat(request.url.params["from"])
            body = [r for r in self.history if datetime.fromisoformat(r["start"]) >= since]
        elif path == "/api/tariff/grid":
            body = {"rates": self.rates}
        elif path == "/api/tariff/planner":
            body = {"rates": self.rates if self.planner is None else self.planner}
        elif request.method == "POST" and "/smartcostlimit/" in path:
            self.posts.append(path)
            value = float(path.rsplit("/", 1)[1])
            number = int(path.split("/")[3])
            self.loadpoints[number - 1]["smartCostLimit"] = value
            body = {"result": value}
        else:
            return httpx2.Response(404, text="not found")
        return httpx2.Response(200, text=json.dumps(body))

    def client(self) -> httpx2.AsyncClient:
        """An HTTP client wired to this fake."""
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))


def evcc_loadpoint(**overrides: object) -> dict:
    """A loadpoint as evcc's /api/state shows it."""
    values: dict[str, object] = {
        "mode": "smart",
        "connected": True,
        "planActive": False,
        "chargerFeatureHeating": False,
        "vehicleSoc": 50,
        "effectiveLimitSoc": 100,
        "smartCostLimit": None,
        "vehicleName": "db:1",
        "maxCurrent": 16,
        "phasesActive": 3,
        "vehicleOdometer": 1000.0,
        "chargedEnergy": 0,
    }
    values.update(overrides)
    return values


def history_rows(start: datetime, prices: list[float]) -> list[dict]:
    """Tariff history rows as evcc's /api/history/tariffs returns them."""
    return [
        {"start": (start + i * QUARTER).isoformat(), "grid": p, "feedIn": None}
        for i, p in enumerate(prices)
    ]


def rate_rows(start: datetime, prices: list[float], length: timedelta = QUARTER) -> list[dict]:
    """Forecast rates as evcc's /api/tariff/grid returns them."""
    return [
        {
            "start": (start + i * length).isoformat(),
            "end": (start + (i + 1) * length).isoformat(),
            "value": p,
        }
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def fake() -> FakeEvcc:
    """An evcc with one connected vehicle of 100 kWh, a day of history and a day of forecast."""
    return FakeEvcc(
        loadpoints=[evcc_loadpoint()],
        vehicles={"db:1": {"title": "Car", "capacity": 100}},
        history=history_rows(NOW - timedelta(days=1), [0.30] * 96),
        rates=rate_rows(NOW, [0.30] * 96),
    )
