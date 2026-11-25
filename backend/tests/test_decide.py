"""Decisions: the limit, when to write, and when to stay out of the way."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from evcc_smart_limit.decide import (
    Action,
    Memory,
    Parameters,
    charge_power_kw,
    decide,
    hours_wanted,
    new_session,
    round_up,
    urgency,
)
from evcc_smart_limit.prices import build_window

from .conftest import NOW, loadpoint, slots

#: A day of history at 0.30 with four cheap hours at 0.10, and a forecast with one cheap hour.
HISTORY = slots(NOW - timedelta(days=1), [0.10] * 16 + [0.30] * 80)
FORECAST = slots(NOW, [0.30] * 8 + [0.10] * 4 + [0.30] * 84)
WINDOW = build_window(HISTORY, FORECAST, NOW, days=1)
PARAMS = Parameters(window_days=1)


def test_limit_is_the_price_of_the_hours_the_vehicle_needs() -> None:
    # 50 kWh to go at 16 A x 3 x 230 V = 11.04 kW and 90% efficiency is 5.03 h: more than the five
    # cheap hours, so the limit rises to the next price.
    decision = decide(loadpoint(), WINDOW, Memory(), PARAMS, NOW)
    assert decision.action is Action.WRITE
    assert decision.energy_kwh == pytest.approx(50)
    assert decision.hours == pytest.approx(50 / (11.04 * 0.9))
    assert decision.limit == 0.3
    assert decision.window_hours == 48


def test_a_fuller_vehicle_waits_for_the_cheapest_slots() -> None:
    decision = decide(loadpoint(soc=80), WINDOW, Memory(), PARAMS, NOW)
    assert decision.limit == 0.1
    assert decision.next_slot is not None and decision.next_slot.start == NOW + timedelta(hours=2)


def test_min_hours_applies_when_little_is_needed() -> None:
    lp = loadpoint(soc=99.9)
    small = decide(lp, WINDOW, Memory(), replace(PARAMS, min_hours=0), NOW)
    assert small.hours == pytest.approx(0.1 / (11.04 * 0.9))
    full = decide(loadpoint(soc=100), WINDOW, Memory(), replace(PARAMS, min_hours=6), NOW)
    assert full.energy_kwh == 0
    assert full.hours == 6
    assert full.limit == 0.3


def test_factor_scales_the_hours_and_the_window_caps_them() -> None:
    eager = decide(loadpoint(soc=95), WINDOW, Memory(), replace(PARAMS, factor=3), NOW)
    assert eager.hours == pytest.approx(3 * 5 / (11.04 * 0.9))
    capped = decide(loadpoint(soc=0), WINDOW, Memory(), replace(PARAMS, factor=100), NOW)
    assert capped.hours == 48


def test_configured_capacity_and_power_override_evcc() -> None:
    params = replace(PARAMS, capacity_kwh=50, charge_power_kw=5, efficiency=1)
    decision = decide(loadpoint(capacity_kwh=None), WINDOW, Memory(), params, NOW)
    assert decision.energy_kwh == 25
    assert decision.hours == 5


@pytest.mark.parametrize(
    ("phases", "kw"), [(1, 3.68), (2, 7.36), (3, 11.04), (0, 11.04), (4, 11.04)]
)
def test_charge_power_follows_the_phases_and_assumes_three_for_automatic(
    phases: int, kw: float
) -> None:
    assert charge_power_kw(loadpoint(phases=phases), PARAMS) == pytest.approx(kw)


def test_round_up_keeps_slots_priced_at_the_limit() -> None:
    assert round_up(0.123451) == 0.12346
    assert round_up(0.25) == 0.25
    assert round_up(0.1 + 0.2) == 0.3


def test_close_enough_keeps_the_limit_and_adopts_it() -> None:
    decision = decide(loadpoint(smart_cost_limit=0.299), WINDOW, Memory(), PARAMS, NOW)
    assert decision.action is Action.KEEP
    assert decision.memory.last_written == 0.299


def test_close_enough_in_a_dry_run_adopts_nothing() -> None:
    params = replace(PARAMS, dry_run=True)
    decision = decide(loadpoint(smart_cost_limit=0.3), WINDOW, Memory(), params, NOW)
    assert decision.action is Action.KEEP
    assert decision.memory.last_written is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"heating": True}, "heating devices are not supported"),
        ({"mode": "pv"}, "mode is pv, not smart"),
        ({"plan_active": True}, "a charging plan is active"),
        ({"capacity_kwh": None}, "battery capacity unknown; set CAPACITY_KWH"),
    ],
)
def test_skips(overrides: dict, reason: str) -> None:
    decision = decide(loadpoint(**overrides), WINDOW, Memory(), PARAMS, NOW)
    assert decision.action is Action.SKIP
    assert decision.reason == reason
    assert decision.limit is None


def test_no_prices_skips() -> None:
    empty = build_window([], [], NOW, days=1)
    assert decide(loadpoint(), empty, Memory(), PARAMS, NOW).reason == "no prices in the window"


def test_a_manual_change_pauses_until_the_vehicle_is_next_connected() -> None:
    memory = Memory(last_written=0.3)
    changed = decide(loadpoint(smart_cost_limit=0.5), WINDOW, memory, PARAMS, NOW)
    assert changed.action is Action.SKIP
    assert "changed by hand" in changed.reason
    assert changed.memory.paused

    still = decide(loadpoint(smart_cost_limit=0.3), WINDOW, changed.memory, PARAMS, NOW)
    assert still.action is Action.SKIP
    assert still.reason.startswith("limit changed by hand; paused")

    unplugged = decide(loadpoint(connected=False), WINDOW, still.memory, PARAMS, NOW)
    assert unplugged.reason == "vehicle not connected"
    assert unplugged.memory == replace(still.memory, paused=False, last_written=None)

    plugged = decide(loadpoint(smart_cost_limit=0.5), WINDOW, unplugged.memory, PARAMS, NOW)
    assert plugged.action is Action.WRITE


def test_a_removed_limit_counts_as_a_manual_change() -> None:
    decision = decide(
        loadpoint(smart_cost_limit=None), WINDOW, Memory(last_written=0.3), PARAMS, NOW
    )
    assert decision.memory.paused


def test_the_limit_written_counts_as_unchanged() -> None:
    lp = loadpoint(smart_cost_limit=0.3 + 1e-9)
    decision = decide(lp, WINDOW, Memory(last_written=0.3), PARAMS, NOW)
    assert decision.action is Action.KEEP


def test_a_dry_run_respects_a_manual_pause() -> None:
    params = replace(PARAMS, dry_run=True)
    memory = Memory(last_written=0.3, paused=True)
    decision = decide(loadpoint(smart_cost_limit=0.5), WINDOW, memory, params, NOW)
    assert decision.action is Action.SKIP
    assert decision.reason.startswith("limit changed by hand; paused")


def test_the_charge_level_is_remembered_and_used_while_fresh() -> None:
    seen = decide(loadpoint(soc=80), WINDOW, Memory(), PARAMS, NOW)
    assert seen.memory.last_soc == 80
    assert seen.memory.last_soc_at == NOW

    later = NOW + timedelta(hours=5)
    unknown = decide(loadpoint(soc=None), WINDOW, seen.memory, PARAMS, later)
    assert unknown.action is Action.WRITE
    assert unknown.soc == 80

    stale = decide(loadpoint(soc=None), WINDOW, seen.memory, PARAMS, NOW + timedelta(hours=7))
    assert stale.reason == "charge level unknown"
    assert (
        decide(loadpoint(soc=None), WINDOW, Memory(), PARAMS, NOW).reason == "charge level unknown"
    )


@pytest.mark.parametrize(
    ("current", "action"),
    [
        (0.3, Action.KEEP),
        (0.3015, Action.KEEP),
        (0.299, Action.KEEP),
        (0.302, Action.WRITE),
        (0.297, Action.WRITE),
    ],
)
def test_changes_below_half_a_percent_are_not_written(current: float, action: Action) -> None:
    decision = decide(loadpoint(smart_cost_limit=current), WINDOW, Memory(), PARAMS, NOW)
    assert decision.limit == 0.3
    assert decision.action is action


def test_a_limit_of_zero_matches_a_current_zero() -> None:
    free = build_window(slots(NOW - timedelta(days=1), [0.0] * 96), [], NOW, days=1)
    decision = decide(loadpoint(smart_cost_limit=0.0), free, Memory(), PARAMS, NOW)
    assert (decision.limit, decision.action) == (0.0, Action.KEEP)


def test_a_pending_write_found_in_evcc_counts_as_written() -> None:
    memory = Memory(last_written=0.25, pending=0.3)
    decision = decide(loadpoint(smart_cost_limit=0.3), WINDOW, memory, PARAMS, NOW)
    assert decision.action is Action.KEEP
    assert (decision.memory.last_written, decision.memory.pending) == (0.3, None)


def test_the_old_limit_still_in_evcc_after_a_failed_write_is_not_a_manual_change() -> None:
    memory = Memory(last_written=0.25, pending=0.3)
    decision = decide(loadpoint(smart_cost_limit=0.25), WINDOW, memory, PARAMS, NOW)
    assert decision.action is Action.WRITE
    assert (decision.memory.last_written, decision.memory.pending) == (0.25, None)


def test_a_limit_matching_neither_written_nor_pending_pauses() -> None:
    memory = Memory(last_written=0.25, pending=0.3)
    decision = decide(loadpoint(smart_cost_limit=0.4), WINDOW, memory, PARAMS, NOW)
    assert decision.memory.paused and decision.memory.pending is None


def test_unplugging_forgets_the_charge_level() -> None:
    memory = Memory(last_soc=90, last_soc_at=NOW)
    gone = decide(loadpoint(connected=False, soc=None), WINDOW, memory, PARAMS, NOW)
    assert gone.memory.last_soc is None
    back = decide(loadpoint(soc=None), WINDOW, gone.memory, PARAMS, NOW + timedelta(hours=1))
    assert back.reason == "charge level unknown"


def test_a_drive_between_two_decisions_is_a_new_session() -> None:
    memory = Memory(
        last_written=0.3, paused=True, last_soc=90, last_soc_at=NOW, last_odometer_km=1000
    )
    lp = loadpoint(smart_cost_limit=0.5, soc=None, odometer_km=1012)
    decision = decide(lp, WINDOW, memory, PARAMS, NOW + timedelta(minutes=15))
    assert not decision.memory.paused
    assert decision.memory.last_soc is None and decision.memory.last_odometer_km == 1012
    assert decision.reason == "charge level unknown"


def test_a_restarted_session_energy_is_a_new_session() -> None:
    memory = Memory(last_written=0.3, paused=True, last_session_kwh=4.0)
    decision = decide(loadpoint(smart_cost_limit=0.5, session_kwh=0.0), WINDOW, memory, PARAMS, NOW)
    assert not decision.memory.paused
    assert decision.action is Action.WRITE


def test_standing_still_is_the_same_session() -> None:
    memory = Memory(last_written=0.3, paused=True, last_odometer_km=1000, last_session_kwh=4.0)
    lp = loadpoint(smart_cost_limit=0.5, odometer_km=1000.3, session_kwh=4.2)
    decision = decide(lp, WINDOW, memory, PARAMS, NOW)
    assert decision.memory.paused
    assert decision.memory.last_odometer_km == 1000.3


def test_an_unknown_odometer_keeps_the_last_one() -> None:
    memory = Memory(last_odometer_km=1000)
    decision = decide(loadpoint(odometer_km=None), WINDOW, memory, PARAMS, NOW)
    assert decision.memory.last_odometer_km == 1000


def test_new_session_needs_a_connected_vehicle() -> None:
    memory = Memory(last_odometer_km=1000)
    assert not new_session(loadpoint(connected=False, odometer_km=2000), memory)


def test_a_vehicle_above_its_limit_needs_nothing() -> None:
    params = replace(PARAMS, min_hours=0)
    decision = decide(loadpoint(soc=90, limit_soc=80), WINDOW, Memory(), params, NOW)
    assert decision.energy_kwh == 0
    assert decision.hours == 0


def test_configured_capacity_wins_over_the_vehicle() -> None:
    params = replace(PARAMS, capacity_kwh=40)
    decision = decide(loadpoint(capacity_kwh=100), WINDOW, Memory(), params, NOW)
    assert decision.energy_kwh == 20


URGENT = replace(PARAMS, urgency_start=50, urgency_floor=20, min_hours=0)


@pytest.mark.parametrize(
    ("soc", "expected"), [(60, 0.0), (50, 0.0), (35, 0.5), (20, 1.0), (10, 1.0)]
)
def test_urgency_runs_from_the_start_to_the_floor(soc: float, expected: float) -> None:
    assert urgency(soc, URGENT) == pytest.approx(expected)


def test_urgency_is_off_unless_both_levels_are_set() -> None:
    assert urgency(10, PARAMS) == 0
    assert urgency(10, replace(PARAMS, urgency_start=50)) == 0
    assert urgency(10, replace(PARAMS, urgency_floor=20)) == 0


def test_hours_wanted_closes_the_gap_with_the_cube_of_urgency() -> None:
    # base 8 h of a 100 h window; halfway to the floor closes an eighth of the 92 h gap
    assert hours_wanted(8, 50, URGENT, 100) == 8
    assert hours_wanted(8, 35, URGENT, 100) == pytest.approx(8 + 92 / 8)
    assert hours_wanted(8, 20, URGENT, 100) == 100


def test_hours_wanted_keeps_factor_min_hours_and_the_window_cap() -> None:
    params = replace(PARAMS, factor=2, min_hours=3)
    assert hours_wanted(1, 70, params, 100) == 3
    assert hours_wanted(5, 70, params, 100) == 10
    assert hours_wanted(80, 70, params, 100) == 100


def test_at_the_floor_any_price_in_the_window_passes() -> None:
    decision = decide(loadpoint(soc=20), WINDOW, Memory(), URGENT, NOW)
    assert decision.urgency == 1
    assert decision.hours == decision.window_hours
    assert decision.limit == 0.3  # the window's highest price


def test_above_the_start_urgency_changes_nothing() -> None:
    plain = decide(
        loadpoint(soc=80),
        WINDOW,
        Memory(),
        replace(URGENT, urgency_start=None, urgency_floor=None),
        NOW,
    )
    urgent = decide(loadpoint(soc=80), WINDOW, Memory(), URGENT, NOW)
    assert urgent.urgency == 0
    assert (urgent.hours, urgent.limit) == (plain.hours, plain.limit)
