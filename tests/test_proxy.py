from __future__ import annotations

import asyncio

import httpx
import pytest

from wjxdesk.domain.errors import ExitPoolEmpty
from wjxdesk.protocol.proxy import Exit, ExitPool


def test_exit_proxy_keeps_credentials() -> None:
    plain = Exit(host="1.2.3.4", port=8080)
    assert plain.addr == "1.2.3.4:8080"
    assert plain.proxy == "1.2.3.4:8080"

    authed = Exit(host="5.6.7.8", port=9000, username="u", password="p")
    assert authed.addr == "5.6.7.8:9000"          # 记录/日志里不含密码
    assert authed.proxy == "u:p@5.6.7.8:9000"     # 真正建连接时带鉴权


def test_direct_exit_has_no_proxy() -> None:
    direct = Exit(host="direct")
    assert direct.addr == "direct"
    assert direct.proxy == ""


def _patch_response(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @property
        def text(self) -> str:
            return text

    async def fake_get(self, url, **kwargs):  # noqa: ANN001
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def test_refresh_parses_lines_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_response(monkeypatch, "1.2.3.4:8080\nu:p@5.6.7.8:9000\n# 注释\n")
    pool = ExitPool(link="http://example.invalid/get", allow_direct=False)

    count = asyncio.run(pool.refresh())
    assert count == 2

    first = asyncio.run(pool.acquire())
    assert first.addr == "1.2.3.4:8080"
    assert first.proxy == "1.2.3.4:8080"


def test_refresh_raises_when_link_returns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """配了链接却取不到 IP 时必须报错，不能悄悄退回本机 IP。"""
    _patch_response(monkeypatch, "no ip here\n")
    pool = ExitPool(link="http://example.invalid/get", allow_direct=False)

    with pytest.raises(ExitPoolEmpty) as exc:
        asyncio.run(pool.refresh())
    assert "代理链接" in str(exc.value)


def test_refresh_without_link_uses_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = ExitPool(link="", allow_direct=True)
    assert asyncio.run(pool.refresh()) == 1
    assert asyncio.run(pool.acquire()).addr == "direct"


def test_per_exit_quota_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_response(monkeypatch, "1.2.3.4:8080\n")
    pool = ExitPool(link="http://example.invalid/get", per_exit_quota=1, allow_direct=False)
    asyncio.run(pool.refresh())

    async def take_two() -> None:
        await pool.acquire()
        await pool.acquire()

    with pytest.raises(ExitPoolEmpty):
        asyncio.run(take_two())
