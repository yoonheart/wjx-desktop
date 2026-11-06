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


class ProxyConfigError(ExitPoolEmpty):
    """代理配置本身有问题（白名单未加、额度用完、鉴权失败）。

    这类错误重试多少次都不会好，必须立刻停下来告诉用户改配置，
    不能当成"暂时没货"反复等。
    """


class TaskAborted(WjxDeskError):
    """任务被用户或策略中止。"""
