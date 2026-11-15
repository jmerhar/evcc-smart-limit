"""The parts of evcc's REST API the controller uses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx2

from evcc_smart_limit.decide import LIMIT_DECIMALS, Loadpoint
from evcc_smart_limit.prices import HISTORY_SLOT, Slot

TIMEOUT_SECONDS = 15.0


class EvccError(RuntimeError):
    """evcc answered with something the controller cannot use."""


class Evcc:
    """A client for one evcc instance.

    The API key, when given, goes in an ``Authorization: Bearer`` header. evcc requires it for
    configuration endpoints; sending it everywhere keeps working if it starts requiring it for
    more.
    """

    def __init__(self, client: httpx2.AsyncClient, url: str, api_key: str | None = None) -> None:
        self._client = client
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a request and return its JSON body, raising EvccError on any failure."""
        try:
            response = await self._client.request(
                method, self._url + path, headers=self._headers, timeout=TIMEOUT_SECONDS, **kwargs
            )
        except httpx2.RequestError as error:
            raise EvccError(f"{method} {path}: {type(error).__name__}") from error
        if not response.is_success:
            raise EvccError(f"{method} {path}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise EvccError(f"{method} {path}: not JSON") from error

    async def loadpoint(self, number: int) -> Loadpoint:
        """The state of charging point ``number`` (counted from 1) and its vehicle."""
        if number < 1:
            raise EvccError(f"evcc has no charging point {number}")
        state = await self._request("GET", "/api/state")
        try:
            lp = state["loadpoints"][number - 1]
        except (KeyError, IndexError, TypeError) as error:
            raise EvccError(f"evcc has no charging point {number}") from error
        vehicle = (state.get("vehicles") or {}).get(lp.get("vehicleName") or "") or {}
        soc = lp.get("vehicleSoc")
        return Loadpoint(
            mode=str(lp.get("mode", "")),
            connected=bool(lp.get("connected")),
            plan_active=bool(lp.get("planActive")),
            heating=bool(lp.get("chargerFeatureHeating")),
            # evcc reports 0 when a vehicle's charge level is unknown.
            soc=float(soc) if soc else None,
            limit_soc=float(lp.get("effectiveLimitSoc") or 100),
            smart_cost_limit=lp.get("smartCostLimit"),
            capacity_kwh=vehicle.get("capacity") or lp.get("vehicleCapacity") or None,
            max_current=float(lp.get("maxCurrent") or 16),
            phases=int(lp.get("phasesConfigured") or 0),
        )

    async def history(self, since: datetime) -> list[Slot]:
        """Grid prices evcc stored since ``since``, one 15-minute slot each."""
        rows = await self._request(
            "GET", "/api/history/tariffs", params={"from": since.isoformat()}
        )
        slots = []
        for row in rows or []:
            if row.get("grid") is None:
                continue
            start = datetime.fromisoformat(row["start"])
            slots.append(Slot(start, start + HISTORY_SLOT, float(row["grid"])))
        return slots

    async def forecast(self) -> list[Slot]:
        """evcc's grid price forecast."""
        body = await self._request("GET", "/api/tariff/grid")
        rates = (body or {}).get("rates") or []
        return [
            Slot(
                datetime.fromisoformat(r["start"]),
                datetime.fromisoformat(r["end"]),
                float(r["value"]),
            )
            for r in rates
        ]

    async def set_smart_cost_limit(self, number: int, limit: float) -> None:
        """Set charging point ``number``'s smart cost limit."""
        await self._request(
            "POST", f"/api/loadpoints/{number}/smartcostlimit/{limit:.{LIMIT_DECIMALS}f}"
        )
