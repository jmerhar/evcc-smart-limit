"""Settings, read from the environment (and ``*_FILE`` for secrets)."""

from __future__ import annotations

from typing import Self

from jigkit.settings import AppSettings
from pydantic import Field, model_validator

from evcc_smart_limit.decide import Parameters


class Settings(AppSettings):
    """Everything the controller can be told."""

    #: evcc's address, as this container reaches it.
    evcc_url: str = "http://evcc:7070"
    #: evcc's API key, or EVCC_API_KEY_FILE with a path to it, for an evcc that requires one.
    evcc_api_key: str | None = None
    evcc_api_key_file: str | None = None
    #: The charging point, counted from 1 as in evcc's API.
    loadpoint: int = Field(1, ge=1)

    window_days: float = Field(7.0, gt=0)
    min_hours: float = Field(1.0, ge=0)
    factor: float = Field(1.0, gt=0)
    #: Overrides the vehicle's battery capacity from evcc.
    capacity_kwh: float | None = Field(None, gt=0)
    #: Overrides the charging power estimated from the charging point's maximum current.
    charge_power_kw: float | None = Field(None, gt=0)
    #: Urgency (both or neither): below URGENCY_START the limit widens towards any price, which it
    #: reaches at URGENCY_FLOOR, both charge levels in percent.
    urgency_start: float | None = Field(None, gt=0, le=100)
    urgency_floor: float | None = Field(None, ge=0, lt=100)
    #: Log decisions without writing anything to evcc.
    dry_run: bool = False

    #: A push monitor's URL, called after each decision that reached evcc.
    heartbeat_url: str | None = None
    heartbeat_url_file: str | None = None

    @model_validator(mode="after")
    def _urgency_levels(self) -> Self:
        """Urgency needs both levels, with the floor below the start."""
        start, floor = self.urgency_start, self.urgency_floor
        if (start is None) != (floor is None):
            raise ValueError("set both URGENCY_START and URGENCY_FLOOR, or neither")
        if start is not None and floor is not None and floor >= start:
            raise ValueError("URGENCY_FLOOR must be below URGENCY_START")
        return self

    def parameters(self) -> Parameters:
        """The decision parameters these settings describe."""
        return Parameters(
            window_days=self.window_days,
            min_hours=self.min_hours,
            factor=self.factor,
            capacity_kwh=self.capacity_kwh,
            charge_power_kw=self.charge_power_kw,
            urgency_start=self.urgency_start,
            urgency_floor=self.urgency_floor,
            dry_run=self.dry_run,
        )
