# Changelog

Notable changes to evcc-smart-limit. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Changed

- It looks at evcc every minute and decides every 15 minutes, and straight away when a car is
  plugged in or the limit in evcc changes. A quick unplug and replug is no longer missed.
- Removing the price limit in evcc (**Remove limit**) hands control back, also during a pause after
  a manual change, so taking over again needs no unplugging.

## 0.3.0 — 2026-11-25

### Added

- Images for arm64 (e.g. Raspberry Pi) as well as amd64.
- Stays out of the way, with a clear log line, when evcc's optimizer controls the charging point
  or evcc charges by a planner tariff other than the grid price.
- Refuses to start when the data directory isn't writable, instead of failing after writing a
  limit.

### Changed

- The charging power takes the phases evcc expects to be active and the vehicle's own maximum
  current into account, so a car that charges on fewer phases than the charging point offers is
  given the hours it really needs.
- `EVCC_URL` defaults to `http://evcc:7070`, the usual address from a container next to evcc.
- The example compose file keeps the data in a named volume and starts in dry-run mode.
- Fewer settings: `EFFICIENCY`, `SOC_MAX_AGE_HOURS`, `INTERVAL_SECONDS` and `MIN_CHANGE` are gone
  (fixed at 90%, 6 hours and 15 minutes; a change is written when it moves the limit by more than
  0.5%, which suits any currency). Old values are ignored.
- The README is reorganised around installing and using it, with the settings in three groups and
  a troubleshooting table.

### Fixed

- A write whose answer from evcc was lost no longer looks like a manual change the next time.
- A manual-override pause also ends when the car was driven or a new session started between two
  decisions, not only when the car was seen unplugged.
- The car's last known charge level is forgotten when it is unplugged, so a car that comes back
  with an unknown charge level isn't taken to be as full as when it left.
- `once` shows a manual-override pause instead of the limit it would otherwise set.
- The memory file belongs to one charging point; another one's, or a malformed one, starts fresh.
- The health check waits for the current run's first decision instead of trusting a status file
  left by an earlier run or by `once --apply`.
- An unexpected answer from evcc is reported as such instead of ending the command with a
  traceback.
- `backtest` aligns its start to a quarter hour, takes times without a UTC offset as `TZ`, and
  reports a malformed CSV or an end before the start.

## 0.2.0 — 2026-11-20

### Added

- Urgency (`URGENCY_START`, `URGENCY_FLOOR`): below the start level the limit widens towards any
  price, which it reaches at the floor, so an emptying car accepts the current prices instead of
  waiting for older, cheaper days to leave the window. Off unless both are set.

## 0.1.1 — 2026-11-15

### Added

- The backtest reports each strategy's charge level at the end, so one that charged less does not
  look cheaper for it.

## 0.1.0 — 2026-11-15

### Added

- The controller: keeps a charging point's smart cost limit at the price of the cheapest hours of
  a rolling window (evcc's stored price history plus its forecast), sized to the energy the vehicle
  needs to reach its charge limit (evcc's, or the limit set in the vehicle when that is lower).
- Stays out of the way when the mode isn't Smart, no vehicle is connected, a plan is active, or
  someone changed the limit by hand (until the vehicle is next connected).
- `once` prints a single decision; `backtest` replays evcc's price history against a CSV of charge
  levels and compares with charging as soon as the vehicle is connected.
- A container image with a health probe, and a push-monitor heartbeat.
