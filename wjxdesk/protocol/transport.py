"""HTTP 传输。

只做两件事：按出口建立连接、把 WireRequest 发出去。
不认识问卷、不认识答案、不做判定——那些属于上层。
"""

from __future__ import annotations

import time
from typing import Mapping

import httpx

from wjxdesk.protocol.contracts import RawResponse, WireRequest
from wjxdesk.protocol.proxy import ExitPool

DEFAULT_TIMEOUT_S = 20.0


class HttpTransport:
    def __init__(
        self,
        pool: ExitPool,
        *,
        ua_profiles: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._pool = pool
        self._ua_profiles = dict(ua_profiles or {"pc": "Mozilla/5.0"})
        self._timeout = timeout_s
        self._clients: dict[str, httpx.AsyncClient] = {}

    # -- 连接管理 -----------------------------------------------------------

    def _client_for(self, exit_addr: str) -> httpx.AsyncClient:
        client = self._clients.get(exit_addr)
        if client is not None:
            return client
        proxy = None
        if exit_addr and exit_addr != "direct":
            auth = ""
            if "@" in exit_addr:
                exit_addr, auth = exit_addr.split("@", 1)
            proxy = f"http://{auth + '@' if auth else ''}{exit_addr}"
        client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            proxy=proxy,
            headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        self._clients[exit_addr] = client
        return client

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    # -- 抓取 ---------------------------------------------------------------

    async def fetch_page(self, url: str, *, exit_addr: str = "", headers: Mapping[str, str] | None = None) -> RawResponse:
        started = time.perf_counter()
        try:
            client = self._client_for(exit_addr)
            response = await client.get(url, headers=dict(headers or {}))
            return RawResponse(
                status_code=response.status_code,
                text=response.text,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                exit_addr=exit_addr,
            )
        except httpx.HTTPError as exc:
            return RawResponse(
                status_code=0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                exit_addr=exit_addr,
                error=f"{type(exc).__name__}: {exc}",
            )

    # -- 提交 ---------------------------------------------------------------

    async def warm_session(self, url: str, *, exit_addr: str = "", headers: Mapping[str, str] | None = None) -> int:
        """先打开问卷页，把 cookie 收集到该出口的连接里（等价于浏览器先访问页面）。"""
        client = self._client_for(exit_addr)
        try:
            await client.get(url, headers=dict(headers or {}))
        except httpx.HTTPError:
            return 0
        return len(client.cookies)

    async def send(self, request: WireRequest, *, exit_addr: str = "") -> RawResponse:
        started = time.perf_counter()
        headers = dict(request.headers)
        try:
            client = self._client_for(exit_addr)
            response = await client.request(
                request.method,
                request.url,
                params=dict(request.params),
                content=request.body.encode("utf-8") if request.body else None,
                headers=headers,
            )
            return RawResponse(
                status_code=response.status_code,
                text=response.text,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                exit_addr=exit_addr,
            )
        except httpx.HTTPError as exc:
            return RawResponse(
                status_code=0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                exit_addr=exit_addr,
                error=f"{type(exc).__name__}: {exc}",
            )

    # -- UA 画像 ------------------------------------------------------------

    def ua_for(self, profile: str) -> str:
        return self._ua_profiles.get(profile) or self._ua_profiles.get("pc", "Mozilla/5.0")
