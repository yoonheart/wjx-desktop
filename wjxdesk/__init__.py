"""wjx-desktop 核心包。

分层约定见 docs/architecture.md：domain 只依赖标准库，engine 不感知网络，
protocol 不感知统计，runtime 只做编排。
"""

from wjxdesk.version import APP_NAME, APP_NAME_CN, VERSION

__all__ = ["APP_NAME", "APP_NAME_CN", "VERSION"]
