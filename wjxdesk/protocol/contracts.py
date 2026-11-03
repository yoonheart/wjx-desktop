"""协议层抽象。

关键约定：编码产出 `WireRequest` 这个中间表示，提交只认它。
于是「预演」「回放」「替换传输实现」都不需要改业务代码。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from wjxdesk.domain.answer import AnswerSheet, Stamp
from wjxdesk.domain.enums import FailureKind, Verdict
from wjxdesk.domain.strategy import ChannelConfig
from wjxdesk.domain.survey import Survey


@dataclass(frozen=True, slots=True)
class WireRequest:
    """一次待发送请求的完整描述。"""

    method: str
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""
    meta: Mapping[str, str] = field(default_factory=dict)

    def summarize(self) -> dict[str, object]:
        """给「协议自检 / 预演」用：隐藏敏感头，只给结构。"""
        return {
            "method": self.method,
            "url": self.url,
            "params": dict(self.params),
            "body_preview": self.body[:400],
            "body_length": len(self.body),
            "headers": {
                k: v for k, v in self.headers.items() if k.lower() in {"content-type", "origin", "referer", "user-agent"}
            },
            "meta": dict(self.meta),
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "m": self.method,
                "u": self.url,
                "p": dict(sorted(self.params.items())),
                "b": self.body,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=12).hexdigest()


@dataclass(frozen=True, slots=True)
class RawResponse:
    status_code: int
    text: str = ""
    elapsed_ms: int = 0
    exit_addr: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    verdict: Verdict
    failure: FailureKind | None = None
    detail: str = ""

    @property
    def success(self) -> bool:
        return self.verdict is Verdict.SUCCESS


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """解析阶段顺手产出的可行性体检。"""

    reachable: bool
    item_count: int = 0
    title: str = ""
    requires_login: bool = False
    requires_wechat: bool = False
    needs_verify: bool = False
    closed: bool = False
    gate_items: tuple[int, ...] = ()
    matrix_items: tuple[int, ...] = ()
    blank_items: tuple[int, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def viable(self) -> bool:
        return self.reachable and not (self.requires_login or self.requires_wechat or self.closed)

    def to_dict(self) -> dict[str, object]:
        return {
            "reachable": self.reachable,
            "item_count": self.item_count,
            "title": self.title,
            "requires_login": self.requires_login,
            "requires_wechat": self.requires_wechat,
            "needs_verify": self.needs_verify,
            "closed": self.closed,
            "viable": self.viable,
            "gate_items": list(self.gate_items),
            "matrix_items": list(self.matrix_items),
            "blank_items": list(self.blank_items),
            "notes": list(self.notes),
        }


@runtime_checkable
class Fetcher(Protocol):
    async def fetch(self, url: str, *, exit_addr: str = "") -> Survey: ...


@runtime_checkable
class Encoder(Protocol):
    def encode(
        self,
        survey: Survey,
        sheet: AnswerSheet,
        stamp: Stamp,
        channel: ChannelConfig,
        *,
        exit_addr: str = "",
    ) -> WireRequest: ...


@runtime_checkable
class Submitter(Protocol):
    async def submit(self, request: WireRequest, *, exit_addr: str = "") -> RawResponse: ...


@runtime_checkable
class Classifier(Protocol):
    def classify(self, response: RawResponse) -> SubmissionOutcome: ...


@runtime_checkable
class Preflighter(Protocol):
    async def check(self, url: str, *, exit_addr: str = "") -> PreflightReport: ...
