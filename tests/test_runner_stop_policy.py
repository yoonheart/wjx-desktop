from __future__ import annotations

import asyncio

import httpx
import pytest

from wjxdesk.domain.enums import ItemKind, TaskState
from wjxdesk.domain.strategy import PacingConfig, Strategy
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.protocol.contracts import RawResponse, WireRequest
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.proxy import ExitPool
from wjxdesk.protocol.wjx import MANIFEST_PATH
from wjxdesk.protocol.wjx.classify import WjxClassifier
from wjxdesk.protocol.wjx.encode import WjxEncoder
from wjxdesk.runtime.pacing import Pacer
from wjxdesk.runtime.runner import MAX_VERIFY_RETRIES, MAX_VERIFY_STREAK, TaskRunner

# 测试里不要真的等 20 秒冷却
FAST_PACING = PacingConfig(median_gap_s=0.01, qps=500.0, jitter_sigma=0.05, real_wait=False, start_stagger_s=0.0)


def _patch_proxy(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @property
        def text(self) -> str:
            return text

    async def fake_get(self, url, **kwargs):  # noqa: ANN001
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def test_acquire_waiting_survives_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """出口只是在冷却时，应等它一下，而不是让整批任务当场失败。"""
    _patch_proxy(monkeypatch, "1.2.3.4:8080\n")
    pool = ExitPool(link="http://x", per_exit_quota=1, cooldown_s=0.6, allow_direct=False)
    asyncio.run(pool.refresh())

    async def flow() -> str:
        first = await pool.acquire()
        pool.report(first.addr, ok=False, throttled=True)  # 进入冷却
        second = await pool.acquire_waiting(max_wait_s=5)
        return second.addr

    assert asyncio.run(flow()) == "1.2.3.4:8080"


def test_diagnose_reports_pool_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_proxy(monkeypatch, "1.2.3.4:8080\n5.6.7.8:9000\n")
    pool = ExitPool(link="http://x", per_exit_quota=3, allow_direct=False)
    asyncio.run(pool.refresh())
    text = pool.diagnose()
    assert "2 个出口" in text and "每出口 3 份" in text


# --------------------------------------------------------------------------- 验证连击


SURVEY = Survey(
    token="t",
    items=(
        Item(number=1, kind=ItemKind.SINGLE, options=tuple(Option(i, f"O{i}") for i in (1, 2))),
    ),
)


class FakeBundle:
    def __init__(self, response_text: str) -> None:
        manifest = Manifest.load(MANIFEST_PATH)
        self.classifier = WjxClassifier(manifest)
        self.encoder = WjxEncoder(manifest)
        self.transport = None
        self.response_text = response_text

    async def submit(self, request: WireRequest, *, exit_addr: str = "") -> RawResponse:
        return RawResponse(status_code=200, text=self.response_text, exit_addr=exit_addr)


def _run(text: str, total: int = 6) -> tuple[TaskState, list[str]]:
    runner = TaskRunner(
        task_id="t",
        bundle=FakeBundle(text),
        survey=SURVEY,
        strategy=Strategy(total=total, concurrency=1, pacing=FAST_PACING),
        pool=ExitPool(link=""),
        pacer=Pacer(FAST_PACING, seed=1),
    )
    failures: list[str] = []
    runner.on_record = lambda r: failures.append(r.failure.value if r.failure else "")
    result = asyncio.run(runner.run())
    return result.state, failures


def test_verify_requeues_then_stops_after_streak() -> None:
    """单份被要求验证时换 IP 重试；连续多份都过不去才停整批。"""
    state, failures = _run("22，请完成验证", total=3)
    assert state is TaskState.STOPPED
    # 每份先重试 MAX_VERIFY_RETRIES 次，最后一次判失败并累计连击
    assert failures.count("NEED_VERIFY") == MAX_VERIFY_STREAK * (MAX_VERIFY_RETRIES + 1)


def test_success_resets_streak() -> None:
    state, failures = _run("10", total=3)
    assert state is TaskState.COMPLETED
    assert all(item == "" for item in failures)
