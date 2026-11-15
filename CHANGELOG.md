# Changelog

Notable changes to evcc-smart-limit. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

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
