"""领域层公开接口。"""

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer, Stamp, blank_numbers
from wjxdesk.domain.enums import (
    FailureKind,
    ItemKind,
    PacingProfile,
    SkewShape,
    TaskState,
    UnitState,
    Verdict,
)
from wjxdesk.domain.errors import (
    CalibrationRequired,
    ConfigError,
    ExitPoolEmpty,
    ParseError,
    ProtocolError,
    TaskAborted,
    WjxDeskError,
)
from wjxdesk.domain.strategy import (
    CONFIG_VERSION,
    ChannelConfig,
    ItemRule,
    PacingConfig,
    QuotaConfig,
    SkewRule,
    Strategy,
    TargetSpec,
    TextFallback,
    default_strategy,
)
from wjxdesk.domain.survey import Gate, Item, Option, Survey
from wjxdesk.domain.task import Counters, ProgressEvent, UnitRecord

__all__ = [
    "CONFIG_VERSION",
    "AnswerSheet",
    "CalibrationRequired",
    "ChannelConfig",
    "ConfigError",
    "Counters",
    "ExitPoolEmpty",
    "FailureKind",
    "Gate",
    "Item",
    "ItemAnswer",
    "ItemKind",
    "ItemRule",
    "Option",
    "PacingConfig",
    "PacingProfile",
    "ParseError",
    "ProgressEvent",
    "ProtocolError",
    "QuotaConfig",
    "SkewRule",
    "SkewShape",
    "Stamp",
    "Strategy",
    "Survey",
    "TargetSpec",
    "TaskAborted",
    "TaskState",
    "TextFallback",
    "UnitRecord",
    "UnitState",
    "Verdict",
    "WjxDeskError",
    "blank_numbers",
    "default_strategy",
]
