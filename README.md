# evcc-smart-limit

[![CI](https://github.com/jmerhar/evcc-smart-limit/actions/workflows/ci.yml/badge.svg)](https://github.com/jmerhar/evcc-smart-limit/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/jmerhar/evcc-smart-limit/branch/main/graph/badge.svg)](https://app.codecov.io/gh/jmerhar/evcc-smart-limit)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A small companion for [evcc](https://evcc.io) that charges your car in the cheapest hours of the
week, without a charging plan.

It sets one thing in evcc: a charging point's **price limit** (evcc's *Cheap Grid Charging*, used
in **Smart** mode). Every 15 minutes it works out how much charging the car needs, looks at the
past week's prices and evcc's forecast, and sets the limit so the car charges only in the cheapest
hours of that week. You keep using evcc as before; solar charging, plans and the minimum charge
level work as usual.

**It's for you if** you have a dynamic electricity tariff in evcc, your car reports its charge
level to evcc, and you don't charge to a schedule: you plug in when it suits you and want the car
charged cheaply by the time it's needed again.

**It's not for you if** you need the car charged by a fixed time (use evcc's charging plans), or
evcc doesn't know your car's charge level.

## How it decides

- **How many hours the car needs.** From the car's charge level, its charge limit (evcc's, or the
  one set in the car if lower), its battery and the charging power. A 40 kWh top-up at 11 kW is
  about 4 hours.
- **Which hours are cheap.** It takes the last 7 days of prices that evcc stored, plus evcc's
  forecast, and sets the limit to the price that only the car's number of cheapest hours stayed
  at or below. So the car charges in roughly the week's cheapest hours, and an emptier car, which
  needs more hours, accepts a little more.
- **Optionally, urgency.** After prices rise, last week's cheap hours keep the limit low for days.
  With [urgency](#urgency) an emptying car stops waiting for them and accepts this week's prices.

It checks evcc every minute, so it also reacts within a minute when a car is plugged in or the
limit in evcc changes.

Each decision is one line in the log, for example:

```
write: new limit | limit 0.21874 | charge level 60% (24.0 kWh to go) | cheapest 2.4 h of 190 h | next slot at or below it 2026-11-16T03:15+01:00 (0.21512)
```

## When it leaves the limit alone

- The charging point isn't in **Smart** mode, a charging plan is active, or no car is plugged in.
- **You changed the price limit yourself** in evcc: it stops touching it until you remove the limit
  (**Remove limit** in evcc) or unplug the car, and takes over again within a minute.
- The car's charge level is unknown. While the car stays plugged in, it keeps using the last
  known one for up to 6 hours.
- evcc's optimizer controls the charging point, or evcc charges by a planner tariff that isn't
  the grid price.
- The charging point is a heat pump or another heating device (not supported).

When it steps aside, the last limit it set stays in evcc.

## Requirements

- evcc 0.317 or newer, with a dynamic grid tariff.
- A car whose charge level evcc knows (a vehicle integration), and its battery capacity set in
  evcc (or `CAPACITY_KWH`).
- Docker, on any machine that can reach evcc (amd64 or arm64, such as a Raspberry Pi).

If evcc has stored less than a week of prices, the window is shorter at first, as little as today
and tomorrow, and fills up over the following days.

## Installing

### 1. Add the service

If evcc runs with Docker Compose, add this to the same compose file:

```yaml
services:
  evcc-smart-limit:
    image: ghcr.io/jmerhar/evcc-smart-limit:0.4
    restart: unless-stopped
    environment:
      EVCC_URL: http://evcc:7070   # evcc's service name and port
      TZ: Europe/Amsterdam         # your time zone, for the log
      DRY_RUN: "true"              # only log for now
    volumes:
      - smart-limit-data:/data

volumes:
  smart-limit-data:
```

[`docker-compose.yml`](docker-compose.yml) is the same with a few hardening options, and works on
its own.

Where evcc runs decides `EVCC_URL`:

| evcc runs | `EVCC_URL` |
|---|---|
| in the same compose file | `http://evcc:7070` (evcc's service name) |
| in another compose project on the same host | `http://evcc:7070`, after joining evcc's network: `networks: [evcc_default]` on this service and `evcc_default: {external: true}` under `networks:` (`docker network ls` shows its name) |
| directly on the host, or with `network_mode: host` | `http://host.docker.internal:7070`, with `extra_hosts: ["host.docker.internal:host-gateway"]` on this service |
| on another machine, or as a Home Assistant add-on | `http://<its address>:7070`; run this container on any Docker host that can reach it |

An API key is not needed: evcc doesn't require one for what this tool reads and writes. If your
evcc or a proxy in front of it does, create one under **Configuration → API Key** and pass it as
`EVCC_API_KEY` (or as a file with `EVCC_API_KEY_FILE`).

### 2. Check the log

```sh
docker compose up -d evcc-smart-limit
docker compose logs -f evcc-smart-limit
```

It starts with `controlling charging point 1 at http://evcc:7070 (dry run: nothing is written)`
and logs a decision every 15 minutes, and straight away when a car is plugged in or the limit in
evcc changes. In a dry run, decisions that would change evcc start with `dry run,`. After the first
decision `docker ps` shows the container as healthy. If something looks wrong, see
[Troubleshooting](#troubleshooting).

### 3. Let it set the limit

When the decisions look sensible, remove `DRY_RUN` (or set it to `"false"`) and run
`docker compose up -d evcc-smart-limit` again. You can watch the limit change in evcc, in the
charging point's settings under **Cheap Grid Charging**.

### Several charging points

Run one container per charging point, each with its own `LOADPOINT` and its own data volume.

## Settings

All settings are environment variables. Most installations only need the first group.

**Essential**

| Variable | Default | What it does |
|---|---|---|
| `EVCC_URL` | `http://evcc:7070` | Where this container reaches evcc (see the table above). |
| `TZ` | `UTC` | Your time zone, for the log. |
| `DRY_RUN` | `false` | `true` logs the decisions without changing evcc. |
| `LOADPOINT` | `1` | Which charging point, counted from 1 in the order evcc's main page shows them. |

**Often used**

| Variable | Default | What it does |
|---|---|---|
| `URGENCY_START`, `URGENCY_FLOOR` | off | Turn on [urgency](#urgency), e.g. `50` and `20`. Set both or neither. |
| `FACTOR` | `1` | How eager it is. `2` lets twice the needed hours through, so the car charges sooner and fuller at slightly higher prices; raise it if the car is often less full than you'd like. |
| `CHARGE_POWER_KW` | from evcc | The car's real charging power. Set it if the car charges slower than evcc expects, e.g. a car that charges on one phase. |
| `CAPACITY_KWH` | from evcc | The car's usable battery capacity, if evcc doesn't know it. |
| `HEARTBEAT_URL` | none | A push monitor (e.g. Uptime Kuma or healthchecks.io), called every few minutes while it reaches evcc. Give the monitor 30 minutes before it alerts. Or `HEARTBEAT_URL_FILE`. |

**Advanced**

| Variable | Default | What it does |
|---|---|---|
| `WINDOW_DAYS` | `7` | Days of past prices it compares with. Longer is pickier and slower to follow a price trend. |
| `MIN_HOURS` | `1` | The fewest hours it asks for, so a nearly full car still tops up at the very cheapest. |
| `EVCC_API_KEY` | none | evcc's API key, only for an evcc that requires one. Or `EVCC_API_KEY_FILE`. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail. |

### Urgency

Without urgency the tool waits for prices like last week's cheapest. That works well while prices
are steady, but after prices rise those cheap hours keep the limit low for up to a week, and the
car can run quite low while it waits.

Urgency fixes that with two charge levels:

- **`URGENCY_START`**, e.g. `50`: above it nothing changes. Below it, the tool becomes less picky,
  a little at first and more as the car empties.
- **`URGENCY_FLOOR`**, e.g. `20`: at this level it accepts any price, so the car charges straight
  away.

With 50 and 20, a car at 45% still waits for the week's cheap hours, at 35% it takes about the
cheapest sixth of the week, at 25% most of it, and at 20% anything. As it charges back above the
start, it becomes picky again. Choose the start where you'd begin to care, and the floor where the
car has to charge whatever it costs.

## Day to day

- **To take over yourself**, change the price limit in evcc. The tool leaves it alone until you
  remove the limit or unplug the car; then it takes over again within a minute. A charging plan or
  another mode also takes precedence while active.
- **To stop it setting a limit at all**, switch the charging point to another mode, set
  `DRY_RUN: "true"`, or stop the container. Removing the limit is not enough: that hands control
  back, and it sets a new one within a minute.
- **evcc's minimum charge level** still works: below it evcc charges at full power at any price,
  up to that level, and then hands back to the price limit. As a safety net for when this tool
  isn't running, set it below `URGENCY_FLOOR`. If your car's charge level is often unknown to
  evcc, leave it at 0%: evcc then charges that share of the battery on every plug-in, whatever the
  price. `HEARTBEAT_URL` is the other way to notice the tool has stopped.

## Updating and removing

- **Update:** `docker compose pull evcc-smart-limit && docker compose up -d evcc-smart-limit`.
  The `0.4` tag follows fixes; a new feature release (0.5) needs the tag changed.
- **Remove:** `docker compose rm -sf evcc-smart-limit`, then delete the service from the compose
  file and `docker volume rm` its volume. The last limit it set stays in evcc: remove it in the
  charging point's settings under **Cheap Grid Charging**.

## Troubleshooting

| Log line | What it means |
|---|---|
| `evcc: GET /api/state: ConnectError` | It can't reach evcc: check `EVCC_URL` and the network (see [Installing](#1-add-the-service)). |
| `evcc: GET /api/history/tariffs: HTTP 404` | evcc is older than 0.317. |
| `evcc: … HTTP 401` | evcc (or a proxy) requires an API key: set `EVCC_API_KEY`. |
| `cannot write to /data` | The data volume isn't writable by the container's user (uid 1000). Use a named volume as above, or `chown 1000:1000` the directory you mount. |
| `skip: mode is …, not smart` | Switch the charging point to **Smart**. |
| `skip: charge level unknown` | evcc doesn't know the car's charge level. |
| `skip: battery capacity unknown; set CAPACITY_KWH` | Set the capacity in evcc's vehicle settings, or `CAPACITY_KWH`. |
| `skip: limit changed by hand …; paused …` | You changed the limit. Remove it in evcc (or unplug the car) to hand control back. |
| `skip: evcc charges by a planner tariff …` | evcc's planner tariff isn't the grid price (e.g. a CO₂ tariff), so a price limit would mean something else. Not supported. |
| `skip: evcc's optimizer controls this charging point` | evcc's optimizer sets the limit itself. |
| `… no forecast slot at or below it` | Not an error: no price in the forecast is low enough yet, so the car waits. If it waits too long after a price rise, turn on urgency. |

To see a single decision without waiting or changing anything:

```sh
docker compose run --rm evcc-smart-limit once
```

## Testing your settings against past prices

`backtest` replays evcc's stored prices with a record of your car's charge levels and compares the
result with charging as soon as the car is plugged in. It needs a CSV with columns `time` (e.g.
`2026-11-15T12:00:00+01:00`) and `soc` (the charge level in %), and optionally `connected` (1 or
0); evcc's InfluxDB export (`vehicleSoc`) is one source. Then:

```sh
docker compose run --rm -v "$PWD/levels.csv:/levels.csv:ro" \
  -e CAPACITY_KWH=77 -e CHARGE_POWER_KW=11 evcc-smart-limit \
  backtest --from 2026-11-01T00:00:00 --to 2026-11-15T00:00:00 --soc /levels.csv --limit-soc 80
```

It prints the energy, cost, average price and lowest and final charge level of both strategies.
Other settings such as `FACTOR` and urgency apply as in the running service, so you can compare
them.

## Tested setups

Verified in regular use:

- evcc 0.319.x;
- a Kia EV9 (99.8 kWh) through evcc's Kia vehicle integration, reporting its charge level and limit;
- a Zaptec Go 2 wallbox through a user-defined charger, three-phase at 11 kW;
- Frank Energie's dynamic tariff, which publishes 15-minute prices.

The amd64 image runs in that setup. The arm64 image is built but has not been run anywhere yet.

Reports from other setups are welcome: open an issue from the
[setup report form](https://github.com/jmerhar/evcc-smart-limit/issues/new/choose).

## Limitations

- It looks back a week, so it follows a lasting change in prices with some delay; urgency covers
  the case where that matters.
- One charging point per container; heating devices are not supported.
- It doesn't manage Fast charging: while a charging point is in Fast mode, it steps aside.

## Development

```sh
make install   # virtualenv with the test tools
make check     # lint and the test suite with coverage, as CI runs them
```

## Licence

MIT
