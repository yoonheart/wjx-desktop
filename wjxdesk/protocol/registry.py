"""平台注册表。

目前只有一家平台，但接口留好：新增平台只需要提供 bundle 构造器，
runtime 与 web 层完全不用改。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from wjxdesk.domain.errors import ProtocolError
from wjxdesk.protocol.transport import HttpTransport

_BUILDERS: dict[str, Callable[[HttpTransport | None], Any]] = {}


def register(name: str, builder: Callable[[HttpTransport | None], Any]) -> None:
    _BUILDERS[name] = builder


def available() -> tuple[str, ...]:
    return tuple(sorted(_BUILDERS))


def load(name: str, transport: HttpTransport | None = None) -> Any:
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProtocolError(f"未注册的平台：{name}，可用：{', '.join(available()) or '无'}")
    return builder(transport)


def _bootstrap() -> None:
    from wjxdesk.protocol.wjx import build as build_wjx

    register("wjx", build_wjx)


_bootstrap()
