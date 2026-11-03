"""目标平台的协议实现绑定。

把五件事装配成一个可用的 bundle：拿页面、解析、编码、提交、判定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from wjxdesk.domain.errors import ProtocolError
from wjxdesk.domain.survey import Survey
from wjxdesk.protocol.contracts import PreflightReport, RawResponse, WireRequest
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.transport import HttpTransport
from wjxdesk.protocol.wjx.classify import WjxClassifier
from wjxdesk.protocol.wjx.encode import WjxEncoder
from wjxdesk.protocol.wjx.parse import parse_survey
from wjxdesk.protocol.wjx.preflight import examine

MANIFEST_PATH = Path(__file__).with_name("manifest.json")


@dataclass
class WjxBundle:
    manifest: Manifest
    encoder: WjxEncoder
    classifier: WjxClassifier
    transport: HttpTransport | None = None

    # -- 抓取与解析 ---------------------------------------------------------

    async def fetch_survey(self, url: str, *, exit_addr: str = "") -> Survey:
        if self.transport is None:
            raise ProtocolError("未配置传输层")
        response = await self.transport.fetch_page(
            url, exit_addr=exit_addr, headers={"User-Agent": self.transport.ua_for("pc")}
        )
        if not response.ok:
            raise ProtocolError(f"页面获取失败：{response.error or response.status_code}")
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
        return parse_survey(response.text, self.manifest, origin=origin, token=_token_from_url(url))

    async def preflight(self, url: str, *, exit_addr: str = "") -> PreflightReport:
        if self.transport is None:
            raise ProtocolError("未配置传输层")
        response = await self.transport.fetch_page(
            url, exit_addr=exit_addr, headers={"User-Agent": self.transport.ua_for("pc")}
        )
        if not response.ok:
            return PreflightReport(reachable=False, notes=[response.error or "无法访问"])
        return examine(response.text, self.manifest, url)

    # -- 提交 ---------------------------------------------------------------

    async def submit(self, request: WireRequest, *, exit_addr: str = "") -> RawResponse:
        if self.transport is None:
            raise ProtocolError("未配置传输层")
        return await self.transport.send(request, exit_addr=exit_addr)


def _token_from_url(url: str) -> str:
    """问卷标识以链接为准：/vm/<token>.aspx 或 ?shortid=<token>。"""
    match = re.search(r"/(?:vm|jq|m)/([A-Za-z0-9]+)", url) or re.search(
        r"[?&]shortid=([A-Za-z0-9]+)", url
    )
    return match.group(1) if match else ""


def build(transport: HttpTransport | None = None, manifest_path: Path | None = None) -> WjxBundle:
    manifest = Manifest.load(manifest_path or MANIFEST_PATH)
    return WjxBundle(
        manifest=manifest,
        encoder=WjxEncoder(manifest),
        classifier=WjxClassifier(manifest),
        transport=transport,
    )
