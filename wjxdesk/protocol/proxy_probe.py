"""代理链路体检：把「粘贴提取链接 → 能不能真的用」这条路走一遍。

为什么放在协议层
----------------
这是对代理服务的探测，属于协议能力，不属于 Web 层；Web 接口只做参数校验与
结果包装，避免把 httpx 调用散落在路由里。

四步与批处理脚本一致，但返回结构化结果：
1. 本机公网 IP（直连）——白名单鉴权失败时，用户需要知道该把哪个 IP 加进去；
2. 调提取链接，保留原始返回（截断），用于分辨「账号问题」还是「格式问题」；
3. 解析 IP:端口（复用 Exit 的解析器，支持 JSON 与纯文本）；
4. 走该出口请求一次 ipinfo，按状态码判断可用性。

检查是只读的：只提取一次 IP，不提交问卷、不写库。
"""

from __future__ import annotations

import re
import time

import httpx

from wjxdesk.protocol.proxy import Exit

IP_ECHO = "https://ipinfo.io/ip"
IP_JSON = "https://ipinfo.io/json"
_ADDR_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})")
_WHITELIST_HINTS = ("白名单", "10101", "未匹配", "whitelist", "not allowed")
_AUTH_HINTS = ("认证", "auth", "password", "用户名")

RAW_LIMIT = 300


def _clip(text: str) -> str:
    text = (text or "").strip().replace("\r", " ").replace("\n", " ")
    return text if len(text) <= RAW_LIMIT else text[:RAW_LIMIT] + "…"


def _looks_like(fragment: str, hints: tuple[str, ...]) -> bool:
    lowered = fragment.lower()
    return any(hint.lower() in lowered for hint in hints)


def _parse_exit(raw: str) -> tuple[Exit | None, str]:
    """返回 (出口, 有效期文本)。解析器与刷问卷时完全一致。"""
    exit_node = Exit._parse_json(raw)
    if exit_node is not None:
        return exit_node, ""
    found = _ADDR_RE.search(raw or "")
    if found:
        return Exit.parse(found.group(0)), ""
    return None, ""


def judge(code: int, body: str) -> tuple[str, str, str]:
    """把「出口探测结果」翻译成 (verdict, exit_ip, hint)。

    纯函数，便于单测：不建连接、不读全局状态。
    """
    if code == 200:
        found = re.search(r'"ip"\s*:\s*"([^"]+)"', body or "")
        return "ok", (found.group(1) if found else ""), ""
    if code == 407:
        return "proxy_auth", "", (
            "407：代理要求认证。白名单里没有这台机器的公网 IP，"
            "或账密模式但链接没带账号密码。"
        )
    if code == 403:
        return "proxy_refused", "", "403：被代理拒绝，常见原因是额度用完、并发超限，或该 IP 已被拉黑。"
    return "probe_failed", "", f"出口返回异常状态码 {code}，建议换一个提取方式再试。"


async def check(
    link: str,
    *,
    timeout_s: float = 20.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """跑一遍四步体检。任何一步失败都返回结构化结果，不抛给前端。"""
    link = (link or "").strip()
    result: dict[str, object] = {
        "link": link,
        "local_ip": "",
        "raw": "",
        "exit_addr": "",
        "extract_ok": False,
        "probe_ok": False,
        "http_code": 0,
        "exit_ip": "",
        "elapsed_ms": 0,
        "verdict": "unknown",
        "hint": "",
    }

    headers = {"Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(
        timeout=timeout_s, follow_redirects=True, headers=headers, transport=transport
    ) as client:
        # 1) 本机公网 IP
        try:
            response = await client.get(IP_ECHO, timeout=8.0)
            if response.status_code == 200:
                result["local_ip"] = response.text.strip()[:40]
        except Exception:
            pass

        if not link:
            result["verdict"] = "no_link"
            result["hint"] = "没有填写提取链接。留空时会直接使用本机 IP，不需要代理。"
            return result

        # 2) 调提取链接
        started = time.perf_counter()
        try:
            response = await client.get(link, timeout=15.0)
            raw = response.text or ""
        except Exception as exc:
            result["verdict"] = "extract_error"
            result["hint"] = f"提取链接请求失败：{exc}"
            return result

        result["raw"] = _clip(raw)

        # 3) 解析出口
        exit_node, deadline = _parse_exit(raw)
        if exit_node is None or not exit_node.port:
            result["verdict"] = "extract_empty"
            if _looks_like(raw, _WHITELIST_HINTS):
                local = result["local_ip"] or "本机公网 IP"
                result["hint"] = (
                    f"代理后台拒绝了提取：{local} 不在白名单里。\n"
                    "去代理后台把这个 IP 加进白名单，或改用「账密模式」的提取链接。"
                )
            elif _looks_like(raw, _AUTH_HINTS):
                result["hint"] = "提取链接缺少账号信息或鉴权失败，请检查链接是否完整。"
            elif raw.strip():
                result["hint"] = "提取链接有返回，但里面没有 IP:端口，请确认链接是「提取」而不是「查询余额」。"
            else:
                result["hint"] = "提取链接返回为空，通常是额度用完或链接已失效。"
            return result

        result["exit_addr"] = exit_node.addr
        result["extract_ok"] = True
        if deadline:
            result["deadline"] = deadline

        # 4) 走出口验证
        proxy_url = f"http://{exit_node.proxy}" if exit_node.proxy else None
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers=headers,
                proxy=proxy_url,
                transport=transport,
            ) as proxied:
                response = await proxied.get(IP_JSON, timeout=15.0)
            code = response.status_code
            result["http_code"] = code
            result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)

            verdict, exit_ip, hint = judge(code, response.text or "")
            result["verdict"] = verdict
            result["exit_ip"] = exit_ip
            result["probe_ok"] = verdict == "ok"
            result["hint"] = hint or (
                f"代理可用，出口 IP {exit_ip or exit_node.addr}，"
                f"耗时 {result['elapsed_ms']} ms。"
            )
        except Exception as exc:
            result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            result["verdict"] = "proxy_dead"
            result["hint"] = f"连不上这个出口（{exit_node.addr}）：IP 可能已过期，或端口被网络拦截。\n{exc}"

    return result
