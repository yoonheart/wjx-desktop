"""节奏控制。

三个层级互不干扰：
1. 全局吞吐（令牌桶）：与并发无关的硬上限；
2. 份间冷却（对数正态 + 日分布权重）：让提交时刻像人；
3. 出口配额与冷却：由 ExitPool 负责。

用户面对的是「稳 / 标准 / 快」三档，秒数由这里换算。
"""

from __future__ import annotations

import asyncio
import math
import random
import time

from wjxdesk.domain.enums import PacingProfile
from wjxdesk.domain.strategy import PacingConfig

_PROFILE_SCALE = {
    PacingProfile.STEADY: 1.8,
    PacingProfile.STANDARD: 1.0,
    PacingProfile.FAST: 0.55,
}


class TokenBucket:
    """异步令牌桶。允许一定突发，之后按恒定速率补充。"""

    def __init__(self, rate_per_s: float, burst: float = 3.0) -> None:
        self._rate = max(1e-6, rate_per_s)
        self._burst = max(1.0, burst)
        self._tokens = self._burst
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> float:
        """取令牌，返回实际等待秒数。"""
        waited = 0.0
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self._burst, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= amount:
                    self._tokens -= amount
                    return waited
                missing = amount - self._tokens
                delay = missing / self._rate
                waited += delay
                await asyncio.sleep(delay)


class Pacer:
    """份间节奏 + 时间标记生成。"""

    def __init__(self, config: PacingConfig, *, seed: int = 0) -> None:
        self.config = config
        self._rng = random.Random(seed or int(time.time()))
        self._scale = _PROFILE_SCALE.get(config.profile, 1.0)
        self._bucket: TokenBucket | None = None
        self._bucket_rate = 0.0

    def _bucket_for(self, rate: float) -> TokenBucket:
        """按当前并发重建令牌桶：并发越高，全局放行速率也越高。"""
        if self._bucket is None or abs(self._bucket_rate - rate) > 1e-9:
            self._bucket_rate = rate
            self._bucket = TokenBucket(rate, burst=max(2.0, rate * 3))
        return self._bucket

    # -- 份间 ---------------------------------------------------------------

    def base_gap(self) -> float:
        """按画像缩放后的中位间隔。"""
        return max(1.0, self.config.median_gap_s * self._scale)

    def hour_factor(self, now: float | None = None) -> float:
        weights = self.config.hour_weights
        if len(weights) != 24:
            return 1.0
        hour = time.localtime(now or time.time()).tm_hour
        peak = max(weights) or 1.0
        return max(0.12, weights[hour] / peak)

    def next_gap(self) -> float:
        """对数正态间隔：重尾，避免等距提交。"""
        median = self.base_gap()
        sigma = max(0.05, self.config.jitter_sigma)
        sample = self._rng.lognormvariate(math.log(median), sigma)
        return min(median * 8.0, max(0.5, sample / self.hour_factor()))

    async def wait_turn(self, concurrency: int = 1) -> None:
        """轮到自己发一份。

        份间冷却按并发摊薄：N 路并行时，平均每 1/N 个冷却间隔就放行一份，
        否则并发形同虚设（每份都睡满一个完整间隔，总耗时与串行一样）。
        令牌桶作全局上限，速率同样随并发提高，避免低速配置把并行卡成串行。
        """
        concurrency = max(1, int(concurrency))
        gap = self.next_gap()
        if self.config.profile is PacingProfile.FAST:
            gap *= 0.25
        rate = max(self.config.qps, concurrency / max(1.0, self.base_gap()))
        await self._bucket_for(rate).acquire(1.0)
        await asyncio.sleep(gap / concurrency)

    # -- 单份时长 -----------------------------------------------------------

    def sample_duration(self, item_count: int) -> float:
        """采样一份答卷的作答时长（秒）。

        优先用「整份区间」——用户填的是 140~150 秒，就该给出 140~150 秒；
        没有区间时才退回「每题秒数 × 题数」的估算。
        """
        low = float(getattr(self.config, "duration_low_s", 0.0) or 0.0)
        high = float(getattr(self.config, "duration_high_s", 0.0) or 0.0)
        if high > 0 and high >= low > 0:
            return self._rng.uniform(low, high)

        low_i = float(self.config.seconds_per_item_low)
        high_i = float(self.config.seconds_per_item_high)
        if high_i < low_i:
            low_i, high_i = high_i, low_i
        per_item = self._rng.uniform(low_i, high_i)
        return max(20.0, per_item * max(1, item_count))
    def estimate(self, total: int, item_count: int, concurrency: int, per_exit_quota: int) -> dict[str, object]:
        """开跑前的成本预估，界面直接展示。

        实际墙钟由「份间冷却 ÷ 并发」决定；单份停留时长只是时间戳上的声明，
        不占用真实等待，所以不计入总耗时（否则预估会虚高一个数量级）。
        """
        duration = self.sample_duration(item_count)
        gap = self.base_gap()
        # 与 wait_turn 保持一致：令牌桶速率随并发提高，实际每份耗时取两者较大值
        rate = max(self.config.qps, concurrency / max(1.0, gap))
        per_slot = max(1.0 / rate, gap / max(1, concurrency))
        seconds = total * per_slot
        return {
            "seconds": int(seconds),
            "human": f"{int(seconds) // 3600} 小时 {int(seconds) % 3600 // 60} 分 {int(seconds) % 60} 秒",
            "duration_per_sheet": duration,
            "gap_median": round(gap, 1),
            "per_slot_s": round(per_slot, 2),
            "exits_needed": math.ceil(total / max(1, per_exit_quota)),
            "profile": self.config.profile.value,
        }
