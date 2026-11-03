from __future__ import annotations

import asyncio
import time

from wjxdesk.domain.enums import PacingProfile
from wjxdesk.domain.strategy import PacingConfig
from wjxdesk.runtime.pacing import Pacer


def test_estimate_matches_parallel_expectation() -> None:
    """20 份、21 路并发：预估应在一两分钟内，而不是串行的十几分钟。"""
    pacer = Pacer(PacingConfig(profile=PacingProfile.STANDARD, median_gap_s=20.0), seed=1)
    plan = pacer.estimate(total=20, item_count=20, concurrency=21, per_exit_quota=2)

    assert plan["seconds"] < 120, plan
    assert plan["per_slot_s"] <= 2.0, plan


def test_wait_turn_runs_in_parallel() -> None:
    """并发 N 时，N 份的总等待应远小于「每份都睡满一个完整冷却间隔」。"""
    pacer = Pacer(
        PacingConfig(profile=PacingProfile.STANDARD, median_gap_s=0.4, jitter_sigma=0.15),
        seed=7,
    )
    units = 8

    async def run(concurrency: int) -> float:
        gate = asyncio.Semaphore(concurrency)

        async def one() -> None:
            async with gate:
                await pacer.wait_turn(concurrency)

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(units)))
        return time.perf_counter() - started

    parallel = asyncio.run(run(units))
    serial = asyncio.run(run(1))

    assert parallel < serial / 2.0, (parallel, serial)
