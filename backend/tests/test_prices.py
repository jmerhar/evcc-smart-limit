"""The price window and the cheapest-hours price."""

from __future__ import annotations

from datetime import timedelta

from evcc_smart_limit.prices import (
    Slot,
    Window,
    build_window,
    cheapest_hours_price,
    next_slot_at_or_below,
    slot_start,
)

from .conftest import NOW, QUARTER, slots


def test_slot_start_truncates_to_the_quarter() -> None:
    assert slot_start(NOW + timedelta(minutes=22, seconds=5, microseconds=7)) == NOW + QUARTER
    assert slot_start(NOW) == NOW


def test_window_keeps_history_inside_the_days_and_splits_at_the_current_slot() -> None:
    history = slots(NOW - timedelta(days=2), [0.1] * (2 * 96 + 4))  # runs past now
    forecast = slots(NOW, [0.2] * 8)
    window = build_window(history, forecast, NOW + timedelta(minutes=5), days=1)
    assert window.past[0].start == NOW - timedelta(days=1)
    assert window.past[-1].start == NOW - QUARTER
    assert len(window.past) == 96
    assert window.future[0].start == NOW
    assert window.hours == 24 + 2


def test_forecast_rate_spanning_the_current_slot_counts_only_from_it() -> None:
    hourly = slots(NOW - timedelta(minutes=30), [0.2, 0.3], timedelta(hours=1))
    window = build_window([], hourly, NOW, days=1)
    assert window.future[0] == Slot(NOW, NOW + timedelta(minutes=30), 0.2)
    assert window.future[0].hours == 0.5
    assert window.future[1].start == NOW + timedelta(minutes=30)


def test_forecast_rates_that_ended_are_dropped() -> None:
    old = slots(NOW - timedelta(hours=1), [0.2])
    assert build_window([], old, NOW, days=1).future == ()


def test_cheapest_hours_price_takes_the_cheapest_slots_first() -> None:
    prices = slots(NOW, [0.5, 0.1, 0.3, 0.2, 0.4])  # 15 minutes each
    assert cheapest_hours_price(tuple(prices), 0.25) == 0.1
    assert cheapest_hours_price(tuple(prices), 0.5) == 0.2
    assert cheapest_hours_price(tuple(prices), 0.6) == 0.3  # a partial slot takes the whole slot


def test_cheapest_hours_price_beyond_the_window_is_the_highest_price() -> None:
    prices = slots(NOW, [0.5, 0.1])
    assert cheapest_hours_price(tuple(prices), 10) == 0.5


def test_cheapest_hours_price_of_nothing_is_none() -> None:
    assert cheapest_hours_price((), 1) is None


def test_next_slot_at_or_below_includes_an_equal_price() -> None:
    future = tuple(slots(NOW, [0.4, 0.3, 0.2]))
    assert next_slot_at_or_below(future, 0.3) == future[1]
    assert next_slot_at_or_below(future, 0.1) is None


def test_window_slots_lists_past_before_future() -> None:
    past, future = tuple(slots(NOW - QUARTER, [0.1])), tuple(slots(NOW, [0.2]))
    assert Window(past, future).slots == past + future
