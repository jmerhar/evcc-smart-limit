"""Settings, read from the environment (and ``*_FILE`` for secrets)."""

from __future__ import annotations

from datetime import timedelta

from jigkit.settings import AppSettings
from pydantic import Field

from evcc_smart_limit.decide import Parameters


class Settings(AppSettings):
    """Everything the controller can be told."""

    #: evcc's address, e.g. http://evcc:7070.
    evcc_url: str = "http://localhost:7070"
    #: evcc's API key (Configuration, API key), or EVCC_API_KEY_FILE with a path to it.
    evcc_api_key: str | None = None
    evcc_api_key_file: str | None = None
    #: The charging point, counted from 1 as in evcc's API.
    loadpoint: int = Field(1, ge=1)

    window_days: float = Field(7.0, gt=0)
    min_hours: float = Field(1.0, ge=0)
    factor: float = Field(1.0, gt=0)
    efficiency: float = Field(0.9, gt=0, le=1)
    #: Overrides the vehicle's battery capacity from evcc.
    capacity_kwh: float | None = Field(None, gt=0)
    #: Overrides the charging power estimated from the charging point's maximum current.
    charge_power_kw: float | None = Field(None, gt=0)
    min_change: float = Field(0.002, ge=0)
    soc_max_age_hours: float = Field(6.0, ge=0)
    #: Seconds between decisions.
    interval_seconds: int = Field(900, ge=60)
    #: Log decisions without writing anything to evcc.
    dry_run: bool = False

    #: A push monitor's URL, called after each decision that reached evcc.
    heartbeat_url: str | None = None
    heartbeat_url_file: str | None = None

    def parameters(self) -> Parameters:
        """The decision parameters these settings describe."""
        return Parameters(
            window_days=self.window_days,
            min_hours=self.min_hours,
            factor=self.factor,
            efficiency=self.efficiency,
            capacity_kwh=self.capacity_kwh,
            charge_power_kw=self.charge_power_kw,
            min_change=self.min_change,
            soc_max_age=timedelta(hours=self.soc_max_age_hours),
            dry_run=self.dry_run,
        )
