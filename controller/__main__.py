"""Command-line entry point for the controller runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

from controller.clients import SimulatorClient
from controller.config import Settings
from controller.errors import ControllerError
from controller.observability import configure_logging
from controller.runtime import run_controller

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="controller",
        description="Run the controller against a simulator HTTP endpoint.",
    )
    parser.add_argument(
        "sim_url", help="absolute simulator URL, for example http://127.0.0.1:8700"
    )
    return parser


async def _run(settings: Settings) -> int:
    stop_event = asyncio.Event()
    received_signal: int | None = None

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal received_signal
        received_signal = signum
        stop_event.set()

    watched_signals = (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {
        signum: signal.signal(signum, request_stop) for signum in watched_signals
    }
    try:
        async with SimulatorClient(settings) as simulator:
            outcome = await run_controller(settings, simulator, stop_event)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if outcome.stopped:
        logger.info(
            "controller stopped after termination request",
            extra={"event": "shutdown_requested", "signal_number": received_signal},
        )
        return 128 + received_signal if received_signal is not None else 0
    logger.info("controller completed scenario", extra={"event": "run_completed"})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_environment(args.sim_url)
    except ControllerError as exc:
        _parser().error(str(exc))
    configure_logging(settings.log_level)
    try:
        return asyncio.run(_run(settings))
    except ControllerError as exc:
        logger.error("controller stopped because a required operation failed: %s", exc)
        return 1
    except OSError as exc:
        logger.error("controller could not initialize runtime resources: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
