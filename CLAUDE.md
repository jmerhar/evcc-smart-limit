# evcc-smart-limit

A small controller that sets an evcc charging point's smart cost limit. See README.md for
behaviour and settings.

## Data in the repository

Tests use synthetic data and a stand-in evcc. A deployment's addresses, API keys, push URLs and
price or charging data never belong in the repository, in code, tests, examples or commit
messages.

## Writing for readers of this repository

Commit messages, comments and docs describe evcc-smart-limit in its own terms, for someone who has
only this repository, evcc and the public repositories it links to.

## Layout

- `backend/src/evcc_smart_limit/decide.py` and `prices.py` hold the whole decision as pure
  functions; the controller, `once` and the backtest all go through them. Keep evcc and I/O out
  of them.
- `evcc.py` is the only module that talks to evcc's API.
- `bin/` holds the project's own scripts; lint, versioning and releases come from jmerhar/devtools,
  fetched into `.devtools/` as `devtools.toml` lists.

## jigkit

jigkit is maintained in its own repository (github.com/jmerhar/jigkit). If something is missing
or wrong there, fix it in jigkit and release it, rather than working around it here.

## Checks

`make check` (lint + coverage, gate in `coverage.toml`) must pass before a commit.
