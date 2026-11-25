"""The ``evcc-smart-limit`` command: ``run``, ``once`` and ``backtest``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Sequence

import httpx2
from jigkit.heartbeat import Heartbeat
from jigkit.logging import configure_logging
from jigkit.scheduler import TickLoop
from pydantic import ValidationError

from evcc_smart_limit import backtest
from evcc_smart_limit.controller import INTERVAL_SECONDS, STATUS_FILE, Controller, describe
from evcc_smart_limit.evcc import Evcc, EvccError
from evcc_smart_limit.settings import Settings

logger = logging.getLogger("evcc_smart_limit")


def data_dir_problem(settings: Settings) -> str | None:
    """Why the data directory cannot hold the controller's memory, or None when it can.

    Checked before the first cycle, so a directory the container cannot write stops it with a
    clear message instead of failing after a limit was written. A status file left by an earlier
    run is removed, so the health probe waits for this run's first cycle.
    """
    probe = settings.data_dir / ".write-test"
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        (settings.data_dir / STATUS_FILE).unlink(missing_ok=True)
    except OSError as error:
        return (
            f"cannot write to {settings.data_dir}: {error.strerror}. Give the container's user "
            f"(uid {os.getuid()}) write access to the data volume."
        )
    return None


def _controller(settings: Settings, client: httpx2.AsyncClient, **kwargs: object) -> Controller:
    """A controller for the configured evcc and charging point."""
    evcc = Evcc(client, settings.evcc_url, settings.evcc_api_key)
    return Controller(evcc, settings.loadpoint, settings.parameters(), settings.data_dir, **kwargs)


async def run(settings: Settings) -> None:
    """Decide every INTERVAL_SECONDS until SIGTERM or SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with httpx2.AsyncClient() as client:
        heartbeat = None
        beat_task = None
        if settings.heartbeat_url:
            heartbeat = Heartbeat(
                client, settings.heartbeat_url, interval_seconds=INTERVAL_SECONDS / 2
            )
            beat_task = asyncio.create_task(heartbeat.run(), name="heartbeat")
        controller = _controller(
            settings,
            client,
            on_success=heartbeat.beat if heartbeat else lambda: None,
            record_status=True,
        )
        ticker = TickLoop(controller.tick, INTERVAL_SECONDS, name="controller")
        logger.info(
            "controlling charging point %d at %s%s",
            settings.loadpoint,
            settings.evcc_url,
            " (dry run: nothing is written)" if settings.dry_run else "",
        )
        ticker.start()
        await stop.wait()
        await ticker.stop()
        if beat_task:
            beat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat_task
    logger.info("stopped")


async def once(settings: Settings, apply: bool) -> int:
    """Decide once and print the decision; write it only with ``apply``."""
    if not apply:
        settings = settings.model_copy(update={"dry_run": True})
    async with httpx2.AsyncClient() as client:
        controller = _controller(settings, client)
        if apply:
            decision = await controller.tick()
            return 0 if decision else 1
        try:
            decision = await controller.decide()
        except EvccError as error:
            print(f"evcc: {error}", file=sys.stderr)
            return 1
    print(describe(decision))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the chosen command."""
    parser = argparse.ArgumentParser(prog="evcc-smart-limit", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="decide every 15 minutes until stopped")
    once_parser = commands.add_parser("once", help="decide once and print the decision")
    once_parser.add_argument("--apply", action="store_true", help="also write the limit to evcc")
    backtest.add_parser(commands)
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError as error:
        print(f"invalid settings:\n{error}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)

    if args.command == "run":
        problem = data_dir_problem(settings)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        asyncio.run(run(settings))
        return 0
    if args.command == "once":
        return asyncio.run(once(settings, args.apply))
    return asyncio.run(backtest.main(settings, args))
