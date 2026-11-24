# evcc-smart-limit

[![CI](https://github.com/jmerhar/evcc-smart-limit/actions/workflows/ci.yml/badge.svg)](https://github.com/jmerhar/evcc-smart-limit/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/jmerhar/evcc-smart-limit/branch/main/graph/badge.svg)](https://app.codecov.io/gh/jmerhar/evcc-smart-limit)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Keeps an [evcc](https://evcc.io) charging point's smart cost limit at the price of the cheapest
hours of the past week, so Smart mode charges the car when prices are at a local low, without a
charging plan, a departure time or a limit you have to keep adjusting.

It's for cars that don't follow a schedule: charged about once a week, at whatever time suits.
For a fixed departure, use evcc's charging plans; while a plan is active this tool stays out of
the way.

## How it decides

Every 15 minutes:

1. **The window:** the grid prices of the last 7 days, from evcc's stored price history, plus
   evcc's forecast (usually the rest of today and, from the afternoon, tomorrow).
2. **The hours needed:** the energy to the charge limit (evcc's, or the limit set in the vehicle
   when that is lower), divided by the charging power:
   `(limit − charge level) × battery capacity ÷ (charging power × efficiency)`. Multiplied by
   `FACTOR`, at least `MIN_HOURS`.
3. **The limit:** the price of the cheapest that many hours in the window. A coming slot passes
   when it's among the cheapest of the whole window, so a car that needs 3 hours gets the week's
   cheapest 3 hours or so, and an emptier car accepts more.
4. **Write** it to evcc when it differs from the current limit by more than `MIN_CHANGE`.

It changes nothing else: solar charging, the minimum charge level and plans work as evcc does
them.

It leaves the limit alone when:

- the mode isn't **Smart**, a plan is active, or no vehicle is connected;
- someone changed the limit by hand: it then pauses until the vehicle is next connected;
- the charge level is unknown and the last known one is more than `SOC_MAX_AGE_HOURS` old;
- the charging point is a heating device (not supported).

Last week's prices only roughly predict the coming ones: when prices trend up it lets fewer hours
through than intended, when they trend down more. Including the forecast softens this.

### Urgency

Optional, with `URGENCY_START` and `URGENCY_FLOOR` (charge levels in percent, e.g. 50 and 20).
Above the start level nothing changes. Below it, the hours asked for grow towards the whole window,
slowly at first and quickly near the floor (the gap closes with the cube of how far the charge
level has fallen from the start to the floor). At the floor the limit is the highest price in the
window, so the car charges at any known price.

After prices rise, the limit lags until the cheaper days leave the window; urgency lets an emptying
car accept the new prices instead of waiting for that. An example, a car that needs 10 hours over a
190-hour window, with 50 and 20:

| Charge level | 50% | 45% | 40% | 35% | 30% | 25% | 20% |
|---|---|---|---|---|---|---|---|
| Hours asked for | 10 | 11 | 17 | 32 | 63 | 114 | 190 |

evcc's own minimum charge level still applies: below it evcc charges at full power at any price,
up to the minimum, and then hands back to the limit. It can serve as a backstop for when this tool
steps aside or isn't running, set below `URGENCY_FLOOR`. While the vehicle's charge level is
unknown, though, evcc counts the minimum as energy charged since plug-in, and forces that much at
any price on every plug-in; with an unreliable charge level, leave it at 0% and monitor this tool
(`HEARTBEAT_URL`) instead.

## Requirements

- evcc 0.317 or newer (its price history API), with a dynamic grid tariff. evcc has stored prices since 0.312, so the window is full from the first start if evcc ran that long.
- A vehicle in evcc with its battery capacity set, or `CAPACITY_KWH`.

## Running it

Add the service to the compose file evcc runs in, on the same network:

```yaml
services:
  evcc-smart-limit:
    image: ghcr.io/jmerhar/evcc-smart-limit:0.1
    restart: unless-stopped
    read_only: true
    environment:
      EVCC_URL: http://evcc:7070
      EVCC_API_KEY_FILE: /run/secrets/evcc_api_key
      DRY_RUN: "true"   # log what it would do first; remove when happy
    volumes:
      - ./smart-limit:/data
      - ./secrets:/run/secrets:ro
```

`docker-compose.yml` in this repository is a complete example. With `DRY_RUN` the log shows each
decision without writing anything:

```
write: new limit | limit 0.21874 | charge level 60% (24.0 kWh to go) | cheapest 2.4 h of 190 h | next slot at or below it 2026-11-16T03:15+01:00 (0.21512)
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `EVCC_URL` | `http://localhost:7070` | evcc's address |
| `EVCC_API_KEY` / `EVCC_API_KEY_FILE` | none | evcc's API key (Configuration, API key) |
| `LOADPOINT` | `1` | the charging point, counted from 1 |
| `WINDOW_DAYS` | `7` | days of price history in the window |
| `MIN_HOURS` | `1` | the fewest hours it ever asks for, so a nearly full car still tops up at the very cheapest |
| `FACTOR` | `1` | multiplies the hours needed; above 1 charges more eagerly |
| `EFFICIENCY` | `0.9` | charging efficiency |
| `CAPACITY_KWH` | from evcc | the vehicle's battery capacity |
| `CHARGE_POWER_KW` | from evcc | charging power; by default the charging point's maximum current × phases × 230 V |
| `MIN_CHANGE` | `0.002` | the smallest change worth writing, in your currency per kWh |
| `SOC_MAX_AGE_HOURS` | `6` | how long a last known charge level stays usable |
| `URGENCY_START`, `URGENCY_FLOOR` | none | turn on [urgency](#urgency): the charge level where it starts and the one where any price passes; both or neither |
| `INTERVAL_SECONDS` | `900` | time between decisions |
| `DRY_RUN` | `false` | log decisions, write nothing |
| `HEARTBEAT_URL` / `HEARTBEAT_URL_FILE` | none | a push monitor (e.g. Uptime Kuma), called after each cycle that reached evcc |
| `TZ`, `LOG_LEVEL`, `DATA_DIR` | `UTC`, `INFO`, `/data` | |

## Commands

| Command | What it does |
|---|---|
| `evcc-smart-limit run` | decide every `INTERVAL_SECONDS` until stopped (the image's default) |
| `evcc-smart-limit once [--apply]` | decide once and print the decision; `--apply` also writes it |
| `evcc-smart-limit backtest --from … --to … --soc levels.csv` | replay evcc's price history with a simulated vehicle and compare with charging as soon as it's connected |

The backtest needs `CAPACITY_KWH` and `CHARGE_POWER_KW`, and a CSV with columns `time` (ISO 8601),
`soc` and optionally `connected` (1/0). The simulated vehicle loses what the real one lost between
rows and charges when the controller's limit allows; the forecast at each step is the real prices
of the next `--forecast-hours` (default 24).

## Development

```sh
make install   # virtualenv with the test tools
make check     # lint and the test suite with coverage, as CI runs them
```

## Licence

MIT
