"""协议清单。

把「平台会变的东西」从代码里挪出来：解析选择器、请求路径、固定参数、
答案串分隔符、响应判定特征。改版时改清单，改完用 verify 自检。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from wjxdesk.domain.errors import ProtocolError


@dataclass(frozen=True, slots=True)
class BlobSpec:
    item_sep: str = "}"
    option_sep: str = "|"
    blank_sep: str = "^"
    matrix_row_sep: str = "!"
    matrix_pair_sep: str = ","
    skip_token: str = "-3"
    # 排序题：选项值之间用英文逗号（线上脚本 d += ","）
    rank_sep: str = ","
    skip_token_text: str = "(跳过)"
    escape: Mapping[str, str] = field(default_factory=dict)

    def escape_text(self, text: str) -> str:
        out = []
        for ch in text:
            out.append(self.escape.get(ch, ch))
        return "".join(out)


@dataclass(frozen=True, slots=True)
class ParseSpec:
    field_selector: str = "div.field"
    type_attr: str = "type"
    number_selector: str = "div.topicnumber"
    title_selector: str = "div.topichtml"
    title_fallback: str = "div.field-label"
    option_container: str = "div.ui-radio, div.ui-checkbox"
    option_label: str = "div.label"
    required_class: str = "req"
    matrix_table: str = "table.matrixtable"
    matrix_header_row: str = "tr"
    kind_map: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WireSpec:
    submit_path: str = "/joinnew/processjq.ashx"
    method: str = "POST"
    content_type: str = "application/x-www-form-urlencoded; charset=UTF-8"
    body_field: str = ""
    # 问卷页地址模板：提交前先访问一次以建立会话
    page_template: str = "https://v.wjx.cn/vm/{shortid}.aspx"
    scene_field: str = ""
    fixed_params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerdictSpec:
    success: tuple[str, ...] = ()
    need_verify: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    limit: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Manifest:
    platform: str
    revision: str
    parse: ParseSpec
    wire: WireSpec
    blob: BlobSpec
    verdict: VerdictSpec
    device_profiles: Mapping[str, str] = field(default_factory=dict)
    source_tokens: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path | str) -> "Manifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Manifest":
        try:
            return cls(
                platform=str(raw["platform"]),
                revision=str(raw.get("revision", "0")),
                parse=ParseSpec(**raw.get("parse", {})),
                wire=WireSpec(**raw.get("wire", {})),
                blob=BlobSpec(**raw.get("blob", {})),
                verdict=VerdictSpec(
                    success=tuple(raw.get("verdict", {}).get("success", ())),
                    need_verify=tuple(raw.get("verdict", {}).get("need_verify", ())),
                    closed=tuple(raw.get("verdict", {}).get("closed", ())),
                    limit=tuple(raw.get("verdict", {}).get("limit", ())),
                ),
                device_profiles=dict(raw.get("device_profiles", {})),
                source_tokens=tuple(raw.get("source_tokens", ())),
            )
        except KeyError as exc:
            raise ProtocolError(f"协议清单缺少字段：{exc}") from exc
