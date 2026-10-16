import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ipdesk.domain.errors import QuotaError
from ipdesk.repo.repositories import Repository
from ipdesk.services.clock import now_kst

WARN_RATIO = 0.8


@dataclass(frozen=True)
class UsageState:
    used: int
    limit: int

    @property
    def level(self) -> str:
        """ok | warn(80% 이상) | blocked(100% 이상)"""
        if self.used >= self.limit:
            return "blocked"
        if self.used >= self.limit * WARN_RATIO:
            return "warn"
        return "ok"


class TokenBucket:
    """초당 rate건까지 허용하는 토큰 버킷 (버스트 = rate)."""

    def __init__(
        self,
        rate_per_sec: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.rate = rate_per_sec
        self._clock = clock
        self._sleep = sleep
        self._tokens = rate_per_sec
        self._last = clock()

    async def acquire(self) -> None:
        while True:
            now = self._clock()
            self._tokens = min(self.rate, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await self._sleep((1 - self._tokens) / self.rate)


class UsageGuard:
    """라이브 호출 직전마다 실행: 월 한도 차단 → 속도 제한 → 사용량 +1."""

    def __init__(
        self, repo: Repository, limit: int, rate_per_sec: float, bucket: TokenBucket | None = None
    ):
        self.repo = repo
        self.limit = limit
        self.bucket = bucket or TokenBucket(rate_per_sec)

    @staticmethod
    def _month() -> str:
        return now_kst().strftime("%Y-%m")

    def state(self) -> UsageState:
        return UsageState(used=self.repo.get_usage(self._month()), limit=self.limit)

    async def before_request(self) -> None:
        if self.state().level == "blocked":
            raise QuotaError(
                f"이번 달 호출 한도({self.limit:,}건)에 도달해 라이브 호출을 차단했습니다. "
                "샘플 모드로 전환하세요",
                result_code="local_limit",
            )
        await self.bucket.acquire()
        self.repo.incr_usage(self._month())
