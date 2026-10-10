from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class AIRateLimiter:
    """Small in-process sliding-window limiter keyed by household."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.requests_per_minute = requests_per_minute
        self.clock = clock
        self._requests: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, household_id: int) -> bool:
        now = self.clock()
        requests = self._requests[household_id]
        while requests and requests[0] <= now - 60:
            requests.popleft()
        if len(requests) >= self.requests_per_minute:
            return False
        requests.append(now)
        return True


__all__ = ["AIRateLimiter"]
