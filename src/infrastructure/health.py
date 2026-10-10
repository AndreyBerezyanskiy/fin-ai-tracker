from __future__ import annotations

import argparse
import asyncio
import os
import time
from contextlib import suppress
from pathlib import Path

DEFAULT_HEARTBEAT_FILE = "/tmp/family-finance-worker.heartbeat"
DEFAULT_MAX_AGE_SECONDS = 45.0


class WorkerHeartbeat:
    """Keep a liveness marker fresh from the worker's asyncio event loop."""

    def __init__(self, path: str, *, interval_seconds: float = 10.0) -> None:
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="worker-heartbeat")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.path.unlink(missing_ok=True)

    async def _run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            self.path.touch()
            await asyncio.sleep(self.interval_seconds)


def heartbeat_is_fresh(path: str, *, max_age_seconds: float) -> bool:
    try:
        age = time.time() - Path(path).stat().st_mtime
    except OSError:
        return False
    return 0 <= age <= max_age_seconds


def run() -> None:
    parser = argparse.ArgumentParser(description="Check the Telegram worker event-loop heartbeat")
    parser.add_argument(
        "--heartbeat-file",
        default=os.getenv("WORKER_HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE),
    )
    parser.add_argument(
        "--max-age-seconds",
        type=float,
        default=float(os.getenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS))),
    )
    args = parser.parse_args()
    raise SystemExit(
        0 if heartbeat_is_fresh(args.heartbeat_file, max_age_seconds=args.max_age_seconds) else 1
    )


__all__ = ["WorkerHeartbeat", "heartbeat_is_fresh", "run"]
