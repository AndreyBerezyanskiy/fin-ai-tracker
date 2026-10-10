from __future__ import annotations

import asyncio
import os
import time

import pytest

from infrastructure.health import WorkerHeartbeat, heartbeat_is_fresh


def test_missing_or_stale_heartbeat_is_unhealthy(tmp_path) -> None:
    heartbeat = tmp_path / "worker.heartbeat"

    assert not heartbeat_is_fresh(str(heartbeat), max_age_seconds=30)

    heartbeat.touch()
    stale_time = time.time() - 60
    os.utime(heartbeat, (stale_time, stale_time))

    assert not heartbeat_is_fresh(str(heartbeat), max_age_seconds=30)


@pytest.mark.asyncio
async def test_worker_heartbeat_is_created_and_removed(tmp_path) -> None:
    heartbeat_file = tmp_path / "runtime" / "worker.heartbeat"
    heartbeat = WorkerHeartbeat(str(heartbeat_file), interval_seconds=0.01)

    heartbeat.start()
    await asyncio.sleep(0.02)

    assert heartbeat_is_fresh(str(heartbeat_file), max_age_seconds=1)

    await heartbeat.stop()

    assert not heartbeat_file.exists()
