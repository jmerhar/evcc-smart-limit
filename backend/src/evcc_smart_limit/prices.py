"""The price window: past slots from evcc's tariff history joined with its forecast.

Everything here is pure, so the controller, ``once`` and the backtest all build the window the same
way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: The length of one slot in evcc's tariff history.
HISTORY_SLOT = timedelta(minutes=15)


@dataclass(frozen=True)
class Slot:
    """A price that holds from ``start`` until ``end``."""

    start: datetime
    end: datetime
    price: float

    @property
    def hours(self) -> float:
        """The slot's length in hours."""
        return (self.end - self.start).total_seconds() / 3600


@dataclass(frozen=True)
class Window:
    """The slots one decision ranks, split at the start of the current slot."""

    past: tuple[Slot, ...]
    future: tuple[Slot, ...]

    @property
    def slots(self) -> tuple[Slot, ...]:
        """Every slot, past first."""
        return self.past + self.future

    @property
    def hours(self) -> float:
        """The hours the window covers."""
        return sum(slot.hours for slot in self.slots)


def slot_start(now: datetime) -> datetime:
    """The start of the 15-minute slot ``now`` falls in."""
    return now - timedelta(
        minutes=now.minute % 15, seconds=now.second, microseconds=now.microsecond
    )


def build_window(history: list[Slot], forecast: list[Slot], now: datetime, days: float) -> Window:
    """Join history and forecast into one window ending with the forecast.

    History covers ``days`` back from the start of the current slot; the forecast covers the
    current slot onwards. A forecast rate that began before the current slot counts only from it,
    so no hour is counted twice where the two overlap.
    """
    cut = slot_start(now)
    begin = cut - timedelta(days=days)
    past = tuple(s for s in sorted(history, key=lambda s: s.start) if begin <= s.start < cut)
    future = tuple(
        Slot(max(s.start, cut), s.end, s.price)
        for s in sorted(forecast, key=lambda s: s.start)
        if s.end > cut
    )
    return Window(past, future)


def cheapest_hours_price(slots: tuple[Slot, ...], hours: float) -> float | None:
    """The price that admits the cheapest ``hours`` of ``slots``.

    Slots are taken cheapest first until they add up to ``hours``; the price of the last one taken
    is the answer, so every slot at or below it is among the cheapest. With fewer hours than asked
    for, the most expensive slot's price is returned. None when there are no slots.
    """
    ordered = sorted(slots, key=lambda s: s.price)
    if not ordered:
        return None
    total = 0.0
    for slot in ordered:
        total += slot.hours
        if total >= hours - 1e-9:
            return slot.price
    return ordered[-1].price


def next_slot_at_or_below(future: tuple[Slot, ...], limit: float) -> Slot | None:
    """The first future slot whose price is at or below ``limit``."""
    return next((s for s in future if s.price <= limit), None)
