"""任务与进度模型。

进度事件同时承担两个职责：
1. 内部状态推进（runtime 层使用）；
2. 对前端输出（web 层序列化）。

因此 `to_wire()` 负责把内部字段映射成前端既有字段名，保证旧界面无需改动。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from wjxdesk.domain.enums import FailureKind, TaskState, UnitState


@dataclass(slots=True)
class Counters:
    """计数。成功口径只认 CONFIRMED，UNKNOWN 单列。"""

    confirmed: int = 0
    failed: int = 0
    unknown: int = 0
    retrying: int = 0

    @property
    def processed(self) -> int:
        return self.confirmed + self.failed + self.unknown

    def as_dict(self) -> dict[str, int]:
        return {
            "confirmed": self.confirmed,
            "failed": self.failed,
            "unknown": self.unknown,
            "retrying": self.retrying,
        }


@dataclass(frozen=True, slots=True)
class UnitRecord:
    """一份答卷的执行记录。落库字段与之一一对应。"""

    index: int
    seed: int
    state: UnitState
    exit_addr: str = ""
    ua_profile: str = ""
    status_code: int = 0
    failure: FailureKind | None = None
    detail: str = ""
    encode_ms: int = 0
    ttf_ms: int = 0
    retries: int = 0
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "seed": self.seed,
            "state": self.state.value,
            "exit": self.exit_addr,
            "ua": self.ua_profile,
            "status": self.status_code,
            "failure": self.failure.value if self.failure else "",
            "detail": self.detail,
            "encode_ms": self.encode_ms,
            "ttf_ms": self.ttf_ms,
            "retries": self.retries,
            "at": self.at,
        }


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    task_id: str
    state: TaskState
    total: int
    counters: Counters
    started_at: float = field(default_factory=time.time)
    note: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.counters.confirmed / self.total * 100))

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.time() - self.started_at)

    @property
    def rate_per_min(self) -> float:
        elapsed = self.elapsed_s
        if elapsed < 1:
            return 0.0
        return round(self.counters.confirmed / elapsed * 60, 1)

    def elapsed_text(self) -> str:
        total = int(self.elapsed_s)
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"

    def to_wire(self) -> dict[str, Any]:
        """前端消费的结构。

        completed / failed / total / percent / status 五个字段沿用既有命名，
        其余为新增（前端可选择性展示）。
        """
        return {
            "taskId": self.task_id,
            "status": self.state.value,
            "completed": self.counters.confirmed,
            "failed": self.counters.failed,
            "unknown": self.counters.unknown,
            "retrying": self.counters.retrying,
            "total": self.total,
            "percent": self.percent,
            "elapsed": self.elapsed_text(),
            "rate": self.rate_per_min,
            "note": self.note,
        }
