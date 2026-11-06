"""代理体检：解析、判定、以及白名单被拒时不白等。

批处理脚本能做这四步，但只能在 Windows 上跑、也不进测试；
这里用纯 Python 实现，且把判定抽成纯函数，保证可回归。
"""

from __future__ import annotations

import asyncio

import httpx

from wjxdesk.protocol.proxy_probe import _parse_exit, check, judge


def test_parse_json_exit() -> None:
    raw = '{"code":0,"data":[{"server":"1.2.3.4:8080","deadline":"2026-11-06 12:00:00"}]}'
    exit_node, _deadline = _parse_exit(raw)
    assert exit_node is not None
    assert exit_node.addr == "1.2.3.4:8080"
    assert exit_node.expires_at > 0


def test_parse_plain_text_exit() -> None:
    exit_node, _ = _parse_exit("1.2.3.4:8080\n")
    assert exit_node is not None
    assert exit_node.addr == "1.2.3.4:8080"


def test_parse_rejects_text_without_address() -> None:
    assert _parse_exit('{"code":10101,"msg":"未匹配到对应白名单用户名"}')[0] is None


def test_judge_ok_reads_exit_ip() -> None:
    verdict, ip, hint = judge(200, '{"ip":"9.9.9.9"}')
    assert verdict == "ok"
    assert ip == "9.9.9.9"
    assert hint == ""


def test_judge_407_points_to_whitelist() -> None:
    verdict, _ip, hint = judge(407, "")
    assert verdict == "proxy_auth"
    assert "白名单" in hint


def test_judge_403_mentions_quota() -> None:
    verdict, _ip, hint = judge(403, "")
    assert verdict == "proxy_refused"
    assert "额度" in hint


def test_judge_unknown_code() -> None:
    verdict, _ip, hint = judge(502, "")
    assert verdict == "probe_failed"
    assert "502" in hint


def test_whitelist_rejection_is_reported_with_local_ip() -> None:
    """白名单被拒时要直接说清「把哪个 IP 加进去」，而不是让用户干等。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ip":
            return httpx.Response(200, text="14.106.132.41")
        return httpx.Response(
            200,
            text='{"code":10101,"msg":"14.106.132.41未匹配到对应白名单用户名!","data":""}',
        )

    result = asyncio.run(
        check("https://example.com/getapi", transport=httpx.MockTransport(handler))
    )

    assert result["verdict"] == "extract_empty"
    assert result["local_ip"] == "14.106.132.41"
    assert "白名单" in str(result["hint"])
    assert "14.106.132.41" in str(result["hint"])


def test_empty_link_reports_direct_mode() -> None:
    result = asyncio.run(
        check("", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="1.1.1.1")))
    )
    assert result["verdict"] == "no_link"
    assert result["local_ip"] == "1.1.1.1"
