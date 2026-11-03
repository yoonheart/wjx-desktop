"""任务执行器。

职责：把「生成答案 → 编码 → 提交 → 判定 → 记账」串起来，并守住三件事：
1. 并发受倍速与令牌桶共同约束；
2. 失败按分类处理，未知结果绝不冒充成功；
3. 每完成若干份做一次诊断，闭环修正生成参数。
"""

from __future__ import annotations

import asyncio
import math
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from wjxdesk.domain.answer import AnswerSheet, Stamp
from wjxdesk.domain.enums import FailureKind, TaskState, UnitState, Verdict
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Survey
from wjxdesk.domain.task import Counters, ProgressEvent, UnitRecord
from wjxdesk.engine.constraints import check, repair
from wjxdesk.engine.controller import LatentController, evaluate
from wjxdesk.engine.diagnostics import QualityReport
from wjxdesk.engine.quota import QuotaBalancer
from wjxdesk.engine.rng import derive
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.engine.goal_solver import GoalReport, GoalSolver
from wjxdesk.engine.synthesis import Synthesizer
from wjxdesk.engine.validity import build_plan, draw_factors
from wjxdesk.engine.unique_pool import UniquePool
from wjxdesk.protocol.contracts import WireRequest
from wjxdesk.protocol.proxy import ExitPool
from wjxdesk.runtime.gate import inspect_batch
from wjxdesk.runtime.pacing import Pacer

EventHook = Callable[[ProgressEvent], None]
RecordHook = Callable[[UnitRecord], None]
DryRunHook = Callable[[WireRequest], None]

DIAGNOSE_EVERY = 20
MAX_RETRIES = 3
# 连续多少份被要求人工验证才判整批不可行
MAX_VERIFY_STREAK = 3
# 被要求验证时换 IP 重试几次。
# 实测：换新 IP 仍然被要求验证，重试只会白烧 IP，所以默认不重试。
MAX_VERIFY_RETRIES = 0

logger = logging.getLogger("wjxdesk.runner")
# 单独的错误日志：用户反馈问题时只需要发 errors.log
error_log = logging.getLogger("wjxdesk.errors")


@dataclass
class RunResult:
    task_id: str
    state: TaskState
    counters: Counters
    records: list[UnitRecord] = field(default_factory=list)
    quality: QualityReport | None = None
    gate: object | None = None
    elapsed_s: float = 0.0


@dataclass
class TaskRunner:
    task_id: str
    bundle: object  # WjxBundle，协议层 bundle
    survey: Survey
    strategy: Strategy
    pool: ExitPool
    pacer: Pacer
    dry_run: bool = False
    on_event: EventHook | None = None
    on_record: RecordHook | None = None
    on_wire: DryRunHook | None = None

    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _records: list[UnitRecord] = field(default_factory=list)
    _all_sheets: list[AnswerSheet] = field(default_factory=list)
    _synth_queue: list[AnswerSheet] = field(default_factory=list)
    _verify_streak: int = 0
    _effective_concurrency: int = 1
    _stop_refills: int = 0
    _pool_misses: int = 0
    _bg_tasks: set = field(default_factory=set)
    _warmed: set[str] = field(default_factory=set)
    _plan_dimensions: dict[int, int] = field(default_factory=dict)
    _plan_reverse: frozenset[int] = frozenset()
    _counters: Counters = field(default_factory=Counters)
    _started_at: float = field(default_factory=time.time)

    # -- 对外 ---------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    async def run(self) -> RunResult:
        self._emit(TaskState.PREPARING)
        controller = LatentController(self.strategy.targets)
        pools = UniquePool()
        # 唯一池必须先把候选值装进去，否则 take() 永远落空，等于没开「答案唯一」
        for item in self.survey.items:
            rule = self.strategy.rule_for(item.number)
            group = getattr(rule, "unique_group", "")
            if not group:
                continue
            values = [str(v) for v in (getattr(rule, "texts", ()) or ())]
            if not values:
                continue
            pools.load(group, values)
            # 选项内填空（如「传奇【　】」）每项一个独立分组，保证同一列不重复
            for index, option in enumerate(item.options, start=1):
                if getattr(option, "requires_text", False):
                    pools.load(f"{group}#o{index}", values)
            logger.info(
                "答案唯一：第 %d 题，候选 %d 个（含 %d 个选项内填空）",
                item.number,
                len(values),
                sum(1 for o in item.options if getattr(o, "requires_text", False)),
            )
            if len(values) < self.strategy.total:
                logger.warning(
                    "答案唯一：第 %d 题只有 %d 个候选，但目标 %d 份，"
                    "超出的部分必然重复——要么加候选，要么把目标份数降到 %d 以内",
                    item.number,
                    len(values),
                    self.strategy.total,
                    len(values),
                )
        quota = QuotaBalancer(self.strategy.quota)
        if str(self.survey.extra.get("captcha_required", "")) == "1":
            logger.warning(
                "该问卷已开启智能验证（captchaType=%s）：服务端会按风险评分随机要求人机校验，"
                "纯协议通过率通常只有一半左右；想稳定通过请在问卷设置里关闭验证，或改走浏览器通道",
                self.survey.extra.get("captcha_type", "?"),
            )

        plan = build_plan(self.survey, self.strategy.validity)

        # 并发自适应：出口不够时降到池子能支撑的量，而不是干等
        self._emit(TaskState.PREPARING, note="正在获取出口 IP…")
        capacity = self.strategy.concurrency
        prefetch = getattr(self.pool, "prefetch", None)
        if callable(prefetch):
            # 需要的 IP 数 = 本次要提交的份数 / 每 IP 份数（向上取整）。
            # 开「IP 复用」时不该一次抓满并发数，否则每个 IP 还是只用一次。
            ip_quota = max(1, int(self.strategy.pacing.per_exit_quota))
            units = min(self.strategy.total, self.strategy.concurrency)
            want = max(1, math.ceil(units / ip_quota))
            try:
                got = await prefetch(want)
            except Exception:
                got = getattr(self.pool, "size", 0)
            cap_fn = getattr(self.pool, "capacity", None)
            limit = cap_fn() if callable(cap_fn) else got * self.strategy.pacing.per_exit_quota
            if got:
                capacity = min(self.strategy.concurrency, limit)
                if capacity < self.strategy.concurrency:
                    logger.warning(
                        "出口不足：并发从 %d 降到 %d（池内 %d 个出口 × 每出口 %d 份）",
                        self.strategy.concurrency,
                        capacity,
                        got,
                        self.strategy.pacing.per_exit_quota,
                    )
        self._effective_concurrency = max(1, capacity)
        size_now = getattr(self.pool, "size", 0)
        if size_now:
            self._emit(
                TaskState.PREPARING,
                note=f"出口已就绪（{size_now} 个），并发 {self._effective_concurrency}",
            )
        self._plan_dimensions = dict(plan.dimensions)
        self._plan_reverse = plan.reverse

        self._goal_report: GoalReport | None = None
        if self.strategy.goal.enabled:
            sheets, goal_report = GoalSolver(self.survey, self.strategy).solve(
                self.strategy.total
            )
            self._synth_queue = sheets
            self._goal_report = goal_report
            logger.info(
                "数据目标：预生成 %d 份，%s（尝试 %d 次）",
                len(sheets),
                goal_report.summary(),
                goal_report.attempts,
            )
            for note in goal_report.notes:
                logger.info("  · 差距：%s", note)
            if goal_report.alpha and goal_report.alpha < 0.5:
                logger.error(
                    "数据目标异常：预生成数据的 α=%.2f（应接近 %.2f）。"
                    "多半是「量表/矩阵行」没被当成同一维度，或进程还在跑旧逻辑，"
                    "请确认已重启后再刷。",
                    goal_report.alpha, self.strategy.goal.alpha_target,
                )
        elif self.strategy.synth.enabled:
            sheets, report = Synthesizer(self.survey, self.strategy).prepare(self.strategy.total)
            self._synth_queue = sheets
            logger.info(
                "合成答卷：预生成 %d 份（α=%.2f KMO=%.2f 显著=%d/%d 尝试=%d）",
                len(sheets),
                report.alpha,
                report.kmo,
                report.significant,
                len(report.predictors),
                report.attempts,
            )
        elif self.strategy.validity.enabled:
            logger.info(
                "信效度：%d 道题参与，%d 个维度，反向题 %d 道，目标 α=%.2f",
                len(plan.dimensions),
                plan.dimension_count,
                len(plan.reverse),
                plan.alpha_target,
            )
        gate = asyncio.Semaphore(max(1, self._effective_concurrency))
        seed = self.strategy.resolved_seed()
        sheets_for_diag: list[AnswerSheet] = []

        self._emit(TaskState.RUNNING)
        logger.info(
            "任务开始 task=%s 总量=%d 并发=%d 题数=%d 每出口=%d",
            self.task_id,
            self.strategy.total,
            self.strategy.concurrency,
            self.survey.size,
            self.strategy.pacing.per_exit_quota,
        )
        diagnose = getattr(self.pool, "diagnose", None)
        if callable(diagnose):
            logger.info("出口池：%s", diagnose())

        async def one(unit: int) -> None:
            async with gate:
                if self.stopped:
                    return
                await self._execute_unit(unit, seed, controller, pools, quota, sheets_for_diag, plan)

        # 补份：某份失败就自动追加一份，直到成功数达到目标。
        # 上限是目标的 3 倍，避免风控全灭时无限重试。
        target = max(1, int(self.strategy.total))
        max_attempts = target * 3
        queue: asyncio.Queue[int] = asyncio.Queue()
        for unit in range(1, target + 1):
            queue.put_nowait(unit)
        next_unit = target + 1

        async def worker() -> None:
            nonlocal next_unit
            while not self.stopped:
                try:
                    unit = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                before = self._counters.confirmed
                await one(unit)
                # 只有这一份没成功才补，否则会把目标份数刷爆
                if (
                    not self.stopped
                    and self._counters.confirmed == before
                    and self._counters.confirmed < target
                    and next_unit <= max_attempts
                ):
                    queue.put_nowait(next_unit)
                    next_unit += 1

        workers = max(1, min(self._effective_concurrency, target * 2))
        try:
            while True:
                await asyncio.gather(*(worker() for _ in range(workers)))
                if queue.empty() or self.stopped:
                    break
        except asyncio.CancelledError:
            self.request_stop()
            raise

        self._emit(TaskState.DRAINING)
        quality = self._final_report(sheets_for_diag)
        gate_report = inspect_batch(self.survey, self._all_sheets or sheets_for_diag, self.strategy)
        state = TaskState.STOPPED if self.stopped else TaskState.COMPLETED
        if self.dry_run:
            state = TaskState.STOPPED if self.stopped else TaskState.COMPLETED
        elif self._counters.confirmed == 0 and not self.stopped:
            state = TaskState.FAILED
        self._emit(state, note="；".join(gate_report.problems[:2]))

        elapsed = time.time() - self._started_at
        logger.info(
            "任务结束 task=%s 状态=%s 成功=%d 失败=%d 未知=%d 耗时=%.1fs 消耗IP=%s",
            self.task_id,
            state.value,
            self._counters.confirmed,
            self._counters.failed,
            self._counters.unknown,
            elapsed,
            getattr(self.pool, "fetched_total", 0),
        )
        for problem in gate_report.problems:
            logger.warning("整批体检：%s", problem)

        return RunResult(
            task_id=self.task_id,
            state=state,
            counters=self._counters,
            records=list(self._records),
            quality=quality,
            gate=gate_report,
            elapsed_s=time.time() - self._started_at,
        )

    # -- 单份 ---------------------------------------------------------------

    async def _execute_unit(
        self,
        unit: int,
        seed: int,
        controller: LatentController,
        pools: UniquePool,
        quota: QuotaBalancer,
        diag: list[AnswerSheet],
        plan: object,
    ) -> None:
        await self._stagger(unit)

        if self._synth_queue:
            sheet = self._synth_queue.pop(0)
        else:
            factors = None
            if strategy_validity_enabled(self.strategy):
                dims = draw_factors(plan, derive(seed, "factors", unit))
                factors = {n: dims[plan.dimension_of(n)] for n in plan.dimensions}
            sheet = generate_sheet(
                self.survey,
                self.strategy,
                seed,
                unit,
                latent=controller.latent,
                pools=pools,
                factors=factors,
                reverse=plan.reverse,
            )
        sheet, violations = repair(self.survey, sheet)
        if violations:
            self._mark(unit, seed, UnitState.FATAL, FailureKind.FORMAT, str(violations[0]))
            return
        sheet = quota.correct(self.survey, sheet)
        self._all_sheets.append(sheet)

        duration = self.pacer.sample_duration(self.survey.size)

        try:
            exit_node = await self.pool.acquire_waiting()
        except Exception as exc:
            # 拿不到出口不代表整批失败：把这一份退回队列，等别人腾出配额再试。
            self._pool_misses += 1
            if self._pool_misses <= 40 and not self.stopped:
                logger.warning(
                    "第 %d 份暂时没有可用出口（已第 %d 次），稍后重试：%s",
                    unit, self._pool_misses, exc,
                )
                await asyncio.sleep(min(20.0, 2.0 + self._pool_misses * 0.5))
                return await self._execute_unit(unit, seed, controller, pools, quota, diag, plan)

            logger.warning("多次仍无可用出口，任务停止：%s", exc)
            self._mark(unit, seed, UnitState.FATAL, FailureKind.EXIT_UNUSABLE, str(exc))
            self.request_stop()
            return

        if not self.dry_run:
            await self.pacer.wait_turn(self.strategy.concurrency)

        # 真正等够作答时长：服务端是按「页面打开 → 提交」的真实间隔记时的，
        # 只改时间戳会记成 5 秒，这是最明显的机器特征。
        if self.strategy.pacing.real_wait:
            await asyncio.sleep(min(duration, self.strategy.pacing.real_wait_cap_s))

        now = time.time()
        stamp = Stamp(
            started_at=now - duration,
            elapsed_s=duration,
            issued_at=now,
            nonce=uuid.uuid4().hex,
        )

        ua = self._ua_for(unit)
        # 预热只用来塞 cookie，绝不能阻塞提交（否则首份要等几十秒）
        _warmer = asyncio.create_task(self._warm(exit_node, ua))
        self._bg_tasks.add(_warmer)
        _warmer.add_done_callback(self._bg_tasks.discard)
        source_params = self._source_for(unit)
        encoder = getattr(self.bundle, "encoder")
        request = encoder.encode(
            self.survey,
            sheet,
            stamp,
            self.strategy.channel,
            exit_addr=exit_node.addr,
            ua=ua,
            source_params=source_params,
        )
        if unit == 1:
            # 只打第一份的原始答案串：出问题时这是唯一的地面真相
            logger.info("首份提交答案：%s", getattr(request, "body", ""))

        if self.dry_run:
            if self.on_wire:
                self.on_wire(request)
            self._mark(unit, seed, UnitState.ENCODED, None, "预演，未发送", exit_node.addr, ua)
            return

        await self._submit_with_retry(unit, seed, sheet, request, exit_node, ua)

        if unit % DIAGNOSE_EVERY == 0 or unit == self.strategy.total:
            diag.append(sheet)
            quality = evaluate(
                self.survey,
                diag[-300:],
                self.strategy.targets,
                plan.dimensions or None,
                plan.reverse,
            )
            controller.observe(quality)
            self._emit(TaskState.RUNNING, note=f"诊断：α={quality.alpha:.2f}")

    async def _submit_with_retry(
        self,
        unit: int,
        seed: int,
        sheet: AnswerSheet,
        request: WireRequest,
        exit_node: object,
        ua: str,
    ) -> None:
        """exit_node 是 Exit：proxy 用于真正建连接（含鉴权），addr 只用于记录。"""
        submitter = getattr(self.bundle, "submit")
        classifier = getattr(self.bundle, "classifier")
        attempts = 0
        while True:
            attempts += 1
            proxy = str(getattr(exit_node, "proxy", "") or "")
            addr = str(getattr(exit_node, "addr", "") or "")
            started = time.perf_counter()
            response = await submitter(request, exit_addr=proxy)
            outcome = classifier.classify(response)
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            self.pool.report(
                addr,
                ok=outcome.verdict is Verdict.SUCCESS,
                throttled=outcome.failure in {FailureKind.THROTTLED, FailureKind.NEED_VERIFY},
            )

            if outcome.verdict is Verdict.SUCCESS:
                self._verify_streak = 0
                release_ok = getattr(self.pool, "release", None)
                if callable(release_ok):
                    release_ok(addr, ok=True)
                self._mark(unit, seed, UnitState.CONFIRMED, None, "", addr, ua, elapsed_ms, attempts - 1)
                return

            if (
                outcome.failure
                and outcome.failure.retryable
                and outcome.failure is not FailureKind.NEED_VERIFY
                and attempts <= MAX_RETRIES
            ):
                self._mark(unit, seed, UnitState.RETRYABLE, outcome.failure, outcome.detail, addr, ua, elapsed_ms, attempts)
                await asyncio.sleep(min(30.0, 2.0**attempts))
                try:
                    exit_node = await self.pool.acquire_waiting()
                except Exception:
                    self._mark(unit, seed, UnitState.FATAL, FailureKind.EXIT_UNUSABLE, "无可用出口", addr, ua)
                    self.request_stop()
                    return
                continue

            if outcome.failure is FailureKind.NEED_VERIFY and attempts <= MAX_VERIFY_RETRIES:
                # 重新排队：换一个出口再试，任务不停（对齐「触发了就换 IP」的处理方式）
                self._mark(
                    unit, seed, UnitState.RETRYABLE, outcome.failure, outcome.detail,
                    addr, ua, elapsed_ms, attempts,
                )
                release_retry = getattr(self.pool, "release", None)
                if callable(release_retry):
                    release_retry(addr, ok=False)
                logger.warning(
                    "第 %d 份被要求验证，换 IP 重试（%d/%d）", unit, attempts, MAX_VERIFY_RETRIES
                )
                try:
                    exit_node = await self.pool.acquire_waiting()
                except Exception:
                    return
                continue

            if outcome.verdict is Verdict.UNKNOWN:
                self._mark(unit, seed, UnitState.UNKNOWN, outcome.failure, outcome.detail, addr, ua, elapsed_ms, attempts - 1)
                return

            self._mark(unit, seed, UnitState.FATAL, outcome.failure, outcome.detail, addr, ua, elapsed_ms, attempts - 1)

            release_bad = getattr(self.pool, "release", None)
            if callable(release_bad):
                release_bad(addr, ok=False)

            if outcome.failure is FailureKind.NEED_VERIFY:
                # 单个 IP 被要求验证很常见，换出口往往就过；只有连续多份都过不去才判不可行
                self._verify_streak += 1
                if self._verify_streak >= MAX_VERIFY_STREAK:
                    # 开了 IP 复用时，同一个 IP 连发几份很容易被要求验证；
                    # 只要池子里还有没用过的出口，就继续换 IP 而不是整批停掉。
                    # 直连没有「换 IP」这回事，连击就直接停，别空转
                    fresh = 0
                    if str(getattr(self.pool, "link", "") or "").strip():
                        for exit_node in getattr(self.pool, "_exits", ()):  # noqa: SLF001
                            state = getattr(self.pool, "_state", {}).get(
                                getattr(exit_node, "addr", ""), None
                            )
                            if state is not None and getattr(state, "used", 0) == 0:
                                fresh += 1
                    if fresh == 0 and str(getattr(self.pool, "link", "") or "").strip():
                        # 池子里都用过了：先试着再提取一个新 IP，拿得到就继续
                        prefetch = getattr(self.pool, "prefetch", None)
                        if callable(prefetch) and self._stop_refills < 3:
                            self._stop_refills += 1
                            size_before = getattr(self.pool, "size", 0)
                            try:
                                await prefetch(size_before + 1)
                            except Exception:
                                pass
                            if getattr(self.pool, "size", 0) > size_before:
                                logger.warning(
                                    "出口用完，已补充新 IP（第 %d 次），继续提交",
                                    self._stop_refills,
                                )
                                fresh = 1

                    if fresh > 0:
                        logger.warning(
                            "连续 %d 份要求验证，但还有 %d 个未使用的出口，继续换 IP 提交",
                            self._verify_streak,
                            fresh,
                        )
                        self._verify_streak = 0
                    else:
                        logger.warning(
                            "连续 %d 份都要求人工验证，任务停止（该问卷可能需要人机校验）",
                            self._verify_streak,
                        )
                        self.request_stop()
                else:
                    logger.warning(
                        "第 %d 份被要求人工验证，换出口继续（%d/%d）",
                        unit,
                        self._verify_streak,
                        MAX_VERIFY_STREAK,
                    )
                return

            if outcome.failure in {FailureKind.CLOSED, FailureKind.LIMIT}:
                logger.warning(
                    "任务因「%s」停止：第 %d 份触发，后续不再提交",
                    outcome.failure.value if outcome.failure else "UNKNOWN",
                    unit,
                )
                self.request_stop()
            return

    # -- 记账与通知 ---------------------------------------------------------

    def _page_url(self) -> str:
        template = ""
        wire = getattr(getattr(self.bundle, "classifier", None), "manifest", None)
        if wire is not None:
            template = str(getattr(wire.wire, "page_template", "") or "")
        if not template:
            template = "https://v.wjx.cn/vm/{shortid}.aspx"
        return template.format(shortid=self.survey.token)

    async def _warm(self, exit_node: object, ua: str) -> None:
        """同一出口第一次提交前先打开问卷页，让 cookie 落到这个连接上。"""
        transport = getattr(self.bundle, "transport", None)
        warm = getattr(transport, "warm_session", None)
        if not callable(warm):
            return
        addr = str(getattr(exit_node, "addr", "") or "")
        key = addr or "direct"
        if key in self._warmed:
            return
        self._warmed.add(key)

        headers = {"User-Agent": ua} if ua else {}
        try:
            from wjxdesk.protocol.wjx.encode import browser_headers

            if ua:
                headers.update(browser_headers(ua))
        except Exception:
            pass
        proxy = str(getattr(exit_node, "proxy", "") or "")
        try:
            count = await warm(self._page_url(), exit_addr=proxy, headers=headers)
        except Exception:
            return
        if count:
            logger.info("第 %s 号出口已建立会话（cookie %d 个）", addr or "直连", count)

    async def _stagger(self, unit: int) -> None:
        """错峰启动：第 1 份立刻开始，之后每份错开固定间隔。

        10 份压在同一毫秒发出去是最典型的机器特征；错开后既保留并发，
        又让提交在时间轴上散开。
        """
        delay = float(getattr(self.strategy.pacing, "start_stagger_s", 0.0) or 0.0)
        if delay <= 0 or unit <= 1:
            return
        deadline = self._started_at + (unit - 1) * delay
        wait = deadline - time.time()
        if wait > 0:
            await asyncio.sleep(wait)

    def _mark(
        self,
        unit: int,
        seed: int,
        state: UnitState,
        failure: FailureKind | None,
        detail: str = "",
        exit_addr: str = "",
        ua: str = "",
        ttf_ms: int = 0,
        retries: int = 0,
    ) -> None:
        record = UnitRecord(
            index=unit,
            seed=seed,
            state=state,
            exit_addr=exit_addr,
            ua_profile=ua,
            failure=failure,
            detail=detail[:200],
            ttf_ms=ttf_ms,
            retries=retries,
        )
        self._records.append(record)

        if state is UnitState.CONFIRMED:
            self._counters.confirmed += 1
            logger.info("第 %d 份：成功（出口 %s，%dms）", unit, exit_addr or "-", ttf_ms)
        elif state is UnitState.UNKNOWN:
            self._counters.unknown += 1
            logger.warning("第 %d 份：结果无法确认 —— %s", unit, detail or "响应无特征")
        elif state is UnitState.FATAL:
            self._counters.failed += 1
            logger.warning(
                "第 %d 份：失败 %s —— %s",
                unit,
                failure.value if failure else "UNKNOWN",
                detail or "无详情",
            )
        elif state is UnitState.RETRYABLE:
            logger.info("第 %d 份：第 %d 次重试（%s）", unit, retries, failure.value if failure else "")

        if self.on_record:
            self.on_record(record)
        self._emit(TaskState.RUNNING)

    def _emit(self, state: TaskState, note: str = "") -> None:
        if not self.on_event:
            return
        self.on_event(
            ProgressEvent(
                task_id=self.task_id,
                state=state,
                total=self.strategy.total,
                counters=self._counters,
                started_at=self._started_at,
                note=note,
            )
        )

    def _source_for(self, unit: int) -> dict[str, str]:
        """来源参数，与线上页面脚本一致。

        手机浏览器：source=directphone；微信内打开：iwx=1；PC：都不传。
        """
        channel = self.strategy.channel
        if not (channel.source_enabled and channel.source_mix):
            return {}
        kinds = [k for k, w in channel.source_mix.items() if w > 0]
        weights = [channel.source_mix[k] for k in kinds]
        if not kinds:
            return {}
        kind = derive(self.strategy.resolved_seed(), "source", unit).choices(
            kinds, weights=weights, k=1
        )[0]
        return {"mobile": {"source": "directphone"}, "wechat": {"iwx": "1"}}.get(kind, {})

    def _ua_for(self, unit: int) -> str:
        """按来源比例挑画像：手机→android、链接→pc、微信→wechat。"""
        channel = self.strategy.channel
        name = "pc"

        if channel.source_enabled and channel.source_mix:
            kinds = [k for k, w in channel.source_mix.items() if w > 0]
            weights = [channel.source_mix[k] for k in kinds]
            if kinds:
                picker = derive(self.strategy.resolved_seed(), "source", unit)
                kind = picker.choices(kinds, weights=weights, k=1)[0]
                name = {"mobile": "android", "link": "pc", "wechat": "wechat"}.get(kind, "pc")
        else:
            profiles = channel.ua_profiles or ("pc",)
            name = profiles[unit % len(profiles)]

        transport = getattr(self.bundle, "transport", None)
        return transport.ua_for(name) if transport is not None else name

    def _final_report(self, sheets: list[AnswerSheet]) -> QualityReport | None:
        if not sheets:
            return None
        return evaluate(
            self.survey,
            sheets,
            self.strategy.targets,
            self._plan_dimensions or None,
            self._plan_reverse,
        )


def strategy_validity_enabled(strategy: Strategy) -> bool:
    return bool(strategy.validity.enabled and strategy.validity.dimensions)
