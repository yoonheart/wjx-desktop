"""运行时上下文：把存储、协议、任务注册表装配在一起。

web 层只与这个对象交互，不直接碰协议细节。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.task import ProgressEvent, UnitRecord
from wjxdesk.protocol.proxy import ExitPool
from wjxdesk.protocol.registry import load as load_platform
from wjxdesk.protocol.transport import HttpTransport
from wjxdesk.runtime.pacing import Pacer
from wjxdesk.runtime.runner import TaskRunner
from wjxdesk.storage.db import Store

SaveDialog = Callable[[str, str], Awaitable[str]]
OpenDialog = Callable[[], Awaitable[str]]


@dataclass
class TaskHandle:
    task_id: str
    runner: TaskRunner
    task: asyncio.Task | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    last_event: ProgressEvent | None = None
    finished: bool = False
    created_at: float = field(default_factory=time.time)
    title: str = ""
    url: str = ""

    def publish(self, event: ProgressEvent) -> None:
        self.last_event = event
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)


@dataclass
class AppContext:
    store: Store
    profile: Strategy
    exit_link: str = ""
    save_dialog: SaveDialog | None = None
    open_dialog: OpenDialog | None = None
    tasks: dict[str, TaskHandle] = field(default_factory=dict)
    _pool: ExitPool | None = None
    _transport: HttpTransport | None = None

    # -- 装配 ---------------------------------------------------------------

    @property
    def pool(self) -> ExitPool:
        if self._pool is None:
            self._pool = ExitPool(
                link=self.exit_link,
                per_exit_quota=self.profile.pacing.per_exit_quota,
                cooldown_s=self.profile.pacing.cooldown_s,
            )
        return self._pool

    @property
    def transport(self) -> HttpTransport:
        if self._transport is None:
            bundle_manifest = load_platform("wjx", None).manifest
            self._transport = HttpTransport(
                self.pool, ua_profiles=bundle_manifest.device_profiles
            )
        return self._transport

    def bundle(self) -> Any:
        return load_platform("wjx", self.transport)

    @property
    def platform(self) -> Any:
        return self.bundle()

    # -- 任务 ---------------------------------------------------------------

    async def create_task(
        self, url: str, strategy: Strategy, *, dry_run: bool = False
    ) -> str:
        platform = self.platform
        # 页面直连取：落地页不需要代理，也能在代理失效时先给出「问卷打不开」的明确错误
        survey = await platform.fetch_survey(url)
        task_id = uuid.uuid4().hex[:12]

        # 每个任务独立一个出口池：配额与冷却不跨任务累计，
        # 且代理链接来自本次表单（channel.exit_link），不再依赖启动参数。
        pool = ExitPool(
            link=strategy.channel.exit_link,
            per_exit_quota=strategy.pacing.per_exit_quota,
            cooldown_s=strategy.pacing.cooldown_s,
            allow_direct=not strategy.channel.exit_link,
        )
        # 不在这里同步拉 IP：runner 内部有并发预取，页面能立刻进到进度界面

        transport = HttpTransport(pool, ua_profiles=platform.manifest.device_profiles)
        bundle = load_platform("wjx", transport)

        runner = TaskRunner(
            task_id=task_id,
            bundle=bundle,
            survey=survey,
            strategy=strategy,
            pool=pool,
            pacer=Pacer(strategy.pacing, seed=strategy.resolved_seed()),
            dry_run=dry_run,
        )
        handle = TaskHandle(task_id=task_id, runner=runner, url=url, title=survey.title)
        runner.on_event = handle.publish
        runner.on_record = lambda record: self._persist_record(task_id, record)
        self.tasks[task_id] = handle
        self.store.create_task(task_id, url, survey.title, strategy.total)
        self.store.touch_history(url, survey.title)

        async def _run() -> None:
            try:
                result = await runner.run()
                self.store.finish_task(task_id, result.state.value)
                handle.finished = True
            except Exception:
                self.store.finish_task(task_id, "FAILED")
                handle.finished = True
                raise
            finally:
                await transport.aclose()

        handle.task = asyncio.create_task(_run())
        return task_id

    def stop_task(self, task_id: str) -> bool:
        handle = self.tasks.get(task_id)
        if handle is None:
            return False
        handle.runner.request_stop()
        return True

    def shutdown(self) -> None:
        for handle in self.tasks.values():
            handle.runner.request_stop()
        if self._transport is not None:
            asyncio.get_event_loop().create_task(self._transport.aclose())

    def _persist_record(self, task_id: str, record: UnitRecord) -> None:
        self.store.add_records(task_id, [record.to_dict()])


def default_paths(data_root: Path) -> dict[str, Path]:
    return {
        "db": data_root / "wjxdesk.db",
        "export": data_root / "export",
    }
