"""领域枚举。

所有状态与分类都在这里定义，其它层只引用、不再自行发明字符串。
"""

from __future__ import annotations

from enum import Enum


class ItemKind(str, Enum):
    """题型。编码值对应线上观测到的容器属性，见 docs/protocol-notes.md。"""

    SINGLE = "single"
    MULTI = "multi"
    BLANK = "blank"
    SCALE = "scale"
    MATRIX = "matrix"
    DROPDOWN = "dropdown"
    SLIDER = "slider"
    RANK = "rank"
    UNKNOWN = "unknown"

    @property
    def has_options(self) -> bool:
        return self in {
            ItemKind.SINGLE,
            ItemKind.MULTI,
            ItemKind.SCALE,
            ItemKind.DROPDOWN,
            ItemKind.RANK,
        }


class SkewShape(str, Enum):
    """选项分布形状。用于把「均匀随机」换成可解释的倾向。"""

    UNIFORM = "uniform"
    RIGHT = "right"  # 质量堆在靠前选项
    LEFT = "left"  # 质量堆在靠后选项
    CENTER = "center"  # 钟形
    BIMODAL = "bimodal"  # 两极分化


class PacingProfile(str, Enum):
    """节奏画像。用户选档位，秒数由系统换算。"""

    STEADY = "steady"
    STANDARD = "standard"
    FAST = "fast"


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {TaskState.COMPLETED, TaskState.STOPPED, TaskState.FAILED}


class UnitState(str, Enum):
    """单份答卷的生命周期。UNKNOWN 表示结果无法确认，不计入成功。"""

    PENDING = "PENDING"
    ENCODED = "ENCODED"
    SENT = "SENT"
    CONFIRMED = "CONFIRMED"
    RETRYABLE = "RETRYABLE"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"

    @property
    def counts_as_success(self) -> bool:
        return self is UnitState.CONFIRMED


class FailureKind(str, Enum):
    NETWORK = "NETWORK"
    EXIT_UNUSABLE = "EXIT_UNUSABLE"
    THROTTLED = "THROTTLED"
    NEED_VERIFY = "NEED_VERIFY"
    CLOSED = "CLOSED"
    LIMIT = "LIMIT"
    FORMAT = "FORMAT"
    UNKNOWN = "UNKNOWN"

    @property
    def retryable(self) -> bool:
        return self in {FailureKind.NETWORK, FailureKind.EXIT_UNUSABLE, FailureKind.THROTTLED}

    @property
    def advice(self) -> str:
        return _ADVICE.get(self, "未知原因，请查看任务详情")


_ADVICE: dict[FailureKind, str] = {
    FailureKind.NETWORK: "网络不稳，正在自动重试",
    FailureKind.EXIT_UNUSABLE: "出口不可用，已换线重试",
    FailureKind.THROTTLED: "触发频率限制，已自动降速并冷却该出口",
    FailureKind.NEED_VERIFY: "需要人工验证，任务已暂停",
    FailureKind.CLOSED: "问卷已关闭或尚未开放",
    FailureKind.LIMIT: "已达到份数或次数上限",
    FailureKind.FORMAT: "问卷结构变化，解析失败，请更新协议清单",
    FailureKind.UNKNOWN: "结果无法确认，已转入复核队列",
}


class Verdict(str, Enum):
    """一次响应的判定结果。由 protocol 层的分类器产出。"""

    SUCCESS = "success"
    NEED_VERIFY = "need_verify"
    CLOSED = "closed"
    LIMIT = "limit"
    FORMAT = "format"
    UNKNOWN = "unknown"
