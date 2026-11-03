"""统一响应信封。

沿用「code=1 表示成功」的约定，前端已有代码不用改判断逻辑。
"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, msg: str = "ok") -> dict[str, Any]:
    return {"code": 1, "msg": msg, "data": data}


def err(msg: str, data: Any = None) -> dict[str, Any]:
    return {"code": 0, "msg": msg, "data": data}
