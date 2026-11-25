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

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        #: The HTTP status, when evcc answered with an error.
        self.status = status


#: evcc's answer to a smart cost limit while its optimizer controls the charging point.
OPTIMIZER_CONFLICT = 409


def charge_limit(lp: dict[str, Any]) -> float:
    """The charge level charging stops at: evcc's limit, or the vehicle's own when it is lower.

    evcc's ``effectiveLimitSoc`` does not include a limit set in the vehicle, which evcc reports
    separately as ``vehicleLimitSoc`` (0 when unknown).
    """
    limit = float(lp.get("effectiveLimitSoc") or 100)
    vehicle = lp.get("vehicleLimitSoc") or 0
    return min(limit, float(vehicle)) if vehicle > 0 else limit


def max_current(lp: dict[str, Any], vehicle: dict[str, Any]) -> float:
    """The most current the vehicle can draw: the charging point's, or the vehicle's if lower."""
    limit = float(lp.get("maxCurrent") or 16)
    own = vehicle.get("maxCurrent") or 0
    return min(limit, float(own)) if own > 0 else limit


def _rates(body: Any) -> list[Slot]:
    """The slots of one of evcc's tariff answers."""
    return [
        Slot(
            datetime.fromisoformat(r["start"]),
            datetime.fromisoformat(r["end"]),
            float(r["value"]),
        )
        for r in (body or {}).get("rates") or []
    ]


class Evcc:
    """A client for one evcc instance.

    The API key, when given, goes in an ``Authorization: Bearer`` header, for an evcc (or a proxy
    in front of it) that requires it.
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
            raise EvccError(f"{method} {path}: HTTP {response.status_code}", response.status_code)
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
            vehicle = (state.get("vehicles") or {}).get(lp.get("vehicleName") or "") or {}
            soc = lp.get("vehicleSoc")
            odometer = lp.get("vehicleOdometer")
            session_wh = lp.get("chargedEnergy")
            return Loadpoint(
                mode=str(lp.get("mode", "")),
                connected=bool(lp.get("connected")),
                plan_active=bool(lp.get("planActive")),
                heating=bool(lp.get("chargerFeatureHeating")),
                # evcc reports 0 when a vehicle's charge level is unknown.
                soc=float(soc) if soc else None,
                limit_soc=charge_limit(lp),
                smart_cost_limit=lp.get("smartCostLimit"),
                capacity_kwh=vehicle.get("capacity") or None,
                max_current=max_current(lp, vehicle),
                # The phases evcc expects to be active, given the charger and the vehicle.
                phases=int(lp.get("phasesActive") or 0),
                odometer_km=float(odometer) if odometer else None,
                session_kwh=float(session_wh) / 1000 if session_wh is not None else None,
            )
        except (KeyError, IndexError) as error:
            raise EvccError(f"evcc has no charging point {number}") from error
        except (TypeError, ValueError, AttributeError) as error:
            raise EvccError(f"GET /api/state: unexpected answer ({error})") from error

    async def history(self, since: datetime) -> list[Slot]:
        """Grid prices evcc stored since ``since``, one 15-minute slot each."""
        rows = await self._request(
            "GET", "/api/history/tariffs", params={"from": since.isoformat()}
        )
        try:
            slots = []
            for row in rows or []:
                if row.get("grid") is None:
                    continue
                start = datetime.fromisoformat(row["start"])
                slots.append(Slot(start, start + HISTORY_SLOT, float(row["grid"])))
            return slots
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise EvccError(f"GET /api/history/tariffs: unexpected answer ({error})") from error

    async def forecast(self, usage: str = "grid") -> list[Slot]:
        """evcc's forecast for ``usage``: ``grid``, or ``planner``, the one it charges by."""
        body = await self._request("GET", f"/api/tariff/{usage}")
        try:
            return _rates(body)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise EvccError(f"GET /api/tariff/{usage}: unexpected answer ({error})") from error

    async def set_smart_cost_limit(self, number: int, limit: float) -> None:
        """Set charging point ``number``'s smart cost limit."""
        await self._request(
            "POST", f"/api/loadpoints/{number}/smartcostlimit/{limit:.{LIMIT_DECIMALS}f}"
        )
