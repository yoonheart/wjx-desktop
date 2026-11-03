"""解析阶段顺手做的可行性体检。

目的：把「跑起来才发现刷不了」提前到「解析时就告诉用户为什么不行」。
"""

from __future__ import annotations

from urllib.parse import urlparse

from wjxdesk.domain.survey import Survey
from wjxdesk.protocol.contracts import PreflightReport
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.wjx.parse import parse_survey

LOGIN_MARKERS = ("请先登录", "登录后", "微信登录", "扫码登录", "未登录")
WECHAT_MARKERS = ("请在微信", "微信中打开", "已停止微信", "公众号")
CLOSED_MARKERS = ("已停止", "已暂停", "未开放", "已结束", "问卷不存在")


def examine(html: str, manifest: Manifest, url: str = "") -> PreflightReport:
    parsed = urlparse(url) if url else None
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed and parsed.netloc else ""

    closed = any(marker in html for marker in CLOSED_MARKERS)
    login = any(marker in html for marker in LOGIN_MARKERS)
    wechat = any(marker in html for marker in WECHAT_MARKERS)

    survey: Survey | None = None
    notes: list[str] = []
    try:
        survey = parse_survey(html, manifest, origin=origin)
    except Exception as exc:  # 解析失败不等于不可访问，要区分开
        notes.append(f"题目解析失败：{exc}")

    if survey is None:
        return PreflightReport(
            reachable=not closed,
            requires_login=login,
            requires_wechat=wechat,
            closed=closed,
            notes=notes,
        )

    gate_items = tuple(it.number for it in survey.items if it.gate is not None)
    matrix_items = tuple(it.number for it in survey.items if it.kind.value == "matrix")
    blank_items = tuple(it.number for it in survey.items if it.kind.value == "blank")

    if gate_items:
        notes.append(f"含逻辑跳转题 {len(gate_items)} 道，答案需按条件留空")
    if login:
        notes.append("疑似需要登录后才能提交")
    if wechat:
        notes.append("疑似限定微信环境，本工具不支持")

    return PreflightReport(
        reachable=True,
        item_count=survey.size,
        title=survey.title,
        requires_login=login,
        requires_wechat=wechat,
        closed=closed,
        gate_items=gate_items,
        matrix_items=matrix_items,
        blank_items=blank_items,
        notes=notes,
    )
