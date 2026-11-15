"""The evcc client against a stand-in evcc."""

from __future__ import annotations

from datetime import timedelta

import httpx2
import pytest

from evcc_smart_limit.evcc import Evcc, EvccError

from .conftest import NOW, QUARTER, FakeEvcc, evcc_loadpoint

URL = "http://evcc.test"


async def test_loadpoint_reads_the_state_and_the_vehicle(fake: FakeEvcc) -> None:
    async with fake.client() as client:
        lp = await Evcc(client, URL + "/", api_key="secret").loadpoint(1)
    assert lp.mode == "smart" and lp.connected and not lp.plan_active and not lp.heating
    assert lp.soc == 50 and lp.limit_soc == 100 and lp.capacity_kwh == 100
    assert lp.max_current == 16 and lp.phases == 3 and lp.smart_cost_limit is None
    request = fake.requests[0]
    assert str(request.url) == URL + "/api/state"
    assert request.headers["Authorization"] == "Bearer secret"


async def test_loadpoint_without_api_key_sends_no_authorization(fake: FakeEvcc) -> None:
    async with fake.client() as client:
        await Evcc(client, URL).loadpoint(1)
    assert "Authorization" not in fake.requests[0].headers


async def test_loadpoint_fills_gaps_with_defaults(fake: FakeEvcc) -> None:
    fake.loadpoints = [
        evcc_loadpoint(
            vehicleSoc=0,
            effectiveLimitSoc=None,
            vehicleName=None,
            vehicleCapacity=77,
            maxCurrent=None,
            phasesConfigured=None,
        )
    ]
    fake.vehicles = {}
    async with fake.client() as client:
        lp = await Evcc(client, URL).loadpoint(1)
    assert lp.soc is None  # evcc reports 0 for an unknown charge level
    assert lp.limit_soc == 100 and lp.capacity_kwh == 77
    assert lp.max_current == 16 and lp.phases == 0


@pytest.mark.parametrize("number", [0, 2])
async def test_loadpoint_that_does_not_exist(fake: FakeEvcc, number: int) -> None:
    async with fake.client() as client:
        with pytest.raises(EvccError, match=f"no charging point {number}"):
            await Evcc(client, URL).loadpoint(number)


async def test_history_skips_slots_without_a_grid_price(fake: FakeEvcc) -> None:
    fake.history = [
        {"start": NOW.isoformat(), "grid": 0.2},
        {"start": (NOW + QUARTER).isoformat(), "grid": None},
    ]
    async with fake.client() as client:
        slots = await Evcc(client, URL).history(NOW - timedelta(hours=1))
    assert [(s.start, s.end, s.price) for s in slots] == [(NOW, NOW + QUARTER, 0.2)]
    assert fake.requests[0].url.params["from"] == (NOW - timedelta(hours=1)).isoformat()


async def test_history_of_nothing(fake: FakeEvcc) -> None:
    fake.history = []
    async with fake.client() as client:
        assert await Evcc(client, URL).history(NOW) == []


async def test_forecast(fake: FakeEvcc) -> None:
    async with fake.client() as client:
        slots = await Evcc(client, URL).forecast()
    assert len(slots) == 96 and slots[0].start == NOW and slots[0].end == NOW + QUARTER


async def test_forecast_without_rates(fake: FakeEvcc) -> None:
    fake.rates = []
    async with fake.client() as client:
        assert await Evcc(client, URL).forecast() == []


async def test_set_smart_cost_limit(fake: FakeEvcc) -> None:
    async with fake.client() as client:
        await Evcc(client, URL).set_smart_cost_limit(1, 0.123456789)
    assert fake.posts == ["/api/loadpoints/1/smartcostlimit/0.12346"]


async def test_http_error(fake: FakeEvcc) -> None:
    fake.fail = {"/api/state": 503}
    async with fake.client() as client:
        with pytest.raises(EvccError, match="GET /api/state: HTTP 503"):
            await Evcc(client, URL).loadpoint(1)


async def test_connection_error() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)) as client:
        with pytest.raises(EvccError, match="ConnectError"):
            await Evcc(client, URL).forecast()


async def test_body_that_is_not_json() -> None:
    def garbage(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(garbage)) as client:
        with pytest.raises(EvccError, match="not JSON"):
            await Evcc(client, URL).forecast()


@pytest.mark.parametrize(
    ("evcc_limit", "vehicle_limit", "expected"),
    [(100, 80, 80), (70, 80, 70), (90, 0, 90), (90, None, 90), (None, None, 100)],
)
async def test_the_lower_of_evccs_and_the_vehicles_limit(
    fake: FakeEvcc, evcc_limit: int | None, vehicle_limit: int | None, expected: float
) -> None:
    fake.loadpoints = [evcc_loadpoint(effectiveLimitSoc=evcc_limit, vehicleLimitSoc=vehicle_limit)]
    async with fake.client() as client:
        lp = await Evcc(client, URL).loadpoint(1)
    assert lp.limit_soc == expected
