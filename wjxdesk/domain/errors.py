"""领域异常。区分「可重试」与「必须停机」，便于上层统一处理。"""

from __future__ import annotations


class WjxDeskError(Exception):
    """本项目的异常基类。"""


class ConfigError(WjxDeskError):
    """配置结构不合法。"""


class ProtocolError(WjxDeskError):
    """与目标站点通信或编码时出错。"""


class ParseError(ProtocolError):
    """页面结构无法解析。"""


class CalibrationRequired(ProtocolError):
    """请求签名尚未标定，需要先跑 calibrate。"""


class ExitPoolEmpty(WjxDeskError):
    """出口池耗尽，且没有直连兜底。"""


class TaskAborted(WjxDeskError):
    """任务被用户或策略中止。"""
