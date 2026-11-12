"""出口（代理）池。

外部只给一个提取链接，本模块负责：
1. 拉取列表、解析成 `Exit`；
2. 按配额与冷却分配；
3. 记录可用性，坏出口自动隔离。

不做地区筛选——地区由提取链接本身决定。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from wjxdesk.domain.errors import ExitPoolEmpty, ProxyConfigError

_DIRECT = "direct"

# 这些词出现在提取返回里，说明是配置问题而不是"暂时没货"
_CONFIG_HINTS = ("白名单", "未匹配", "10101", "余额不足", "额度", "认证失败", "用户名")
_WHITELIST_HINTS = ("白名单", "未匹配", "10101")


def _looks_like_config_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint.lower() in lowered for hint in _CONFIG_HINTS)


def _mentions_whitelist(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint.lower() in lowered for hint in _WHITELIST_HINTS)

log = logging.getLogger("wjxdesk.proxy")


@dataclass(frozen=True, slots=True)
class Exit:
    host: str
    port: int = 0
    username: str = ""
    password: str = ""
    # 到期时间（epoch 秒）。0 表示未知，由池子按默认有效期补
    expires_at: float = 0.0

    @property
    def addr(self) -> str:
        """用于日志与记录（不含账号密码）。"""
        return _DIRECT if self.port == 0 else f"{self.host}:{self.port}"

    @property
    def proxy(self) -> str:
        """用于真正建连接：带鉴权信息，直连时为空串。"""
        if self.port == 0:
            return ""
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{auth}{self.host}:{self.port}"

    @property
    def url(self) -> str:
        if self.port == 0:
            return ""
        return f"http://{self.proxy}"

    @classmethod
    def _parse_json(cls, text: str) -> "Exit | None":
        """从 JSON 返回里取出 IP:端口，并读取 deadline（有效期）。"""
        import datetime as _dt
        import json as _json
        import re as _re

        try:
            payload = _json.loads(text)
        except Exception:
            payload = None

        server = ""
        deadline = ""
        if isinstance(payload, dict):
            items = payload.get("data")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                item = items[0]
                server = str(item.get("server") or "")
                deadline = str(item.get("deadline") or "")
                if not server and item.get("proxy_ip"):
                    port = item.get("port") or item.get("proxy_port") or ""
                    server = f"{item.get('proxy_ip')}:{port}" if port else str(item.get("proxy_ip"))
        if not server:
            # 兜底：直接从文本里抠 "server":"x.x.x.x:port"
            found = _re.search(r'"(?:server|proxy|addr)"\s*:\s*"([^"]+)"', text)
            if found:
                server = found.group(1)

        if not server:
            return None

        base = cls.parse(server) if not server.startswith("{") else None
        if base is None:
            return None

        if deadline:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    moment = _dt.datetime.strptime(deadline.strip(), fmt)
                    return Exit(
                        host=base.host,
                        port=base.port,
                        username=base.username,
                        password=base.password,
                        expires_at=moment.timestamp(),
                    )
                except Exception:
                    continue
        return base

    @classmethod
    def parse(cls, line: str) -> "Exit | None":
        text = line.strip()
        if not text or text.startswith("#"):
            return None
        # JSON 形式的返回（例如 {"data":[{"server":"1.2.3.4:8080","deadline":"..."}]}）
        if text.startswith("{") or text.startswith("["):
            parsed = cls._parse_json(text)
            if parsed is not None:
                return parsed
            return None
        if text.startswith("http://") or text.startswith("https://"):
            text = text.split("://", 1)[1]
        username = password = ""
        if "@" in text:
            cred, text = text.split("@", 1)
            if ":" in cred:
                username, password = cred.split(":", 1)
        parts = text.replace("/", "").split(":")
        if len(parts) < 2 or not parts[1].isdigit():
            return None
        host, port = parts[0], int(parts[1])
        if len(parts) >= 4 and not username:
            username, password = parts[2], parts[3]
        return cls(host=host, port=port, username=username, password=password)


@dataclass(slots=True)
class _ExitState:
    used: int = 0
    blocked_until: float = 0.0
    failures: int = 0


@dataclass(slots=True)
class ExitPool:
    link: str = ""
    per_exit_quota: int = 2
    cooldown_s: float = 600.0
    allow_direct: bool = True
    # 出口用完后是否自动重新调用提取链接补货
    auto_refill: bool = True
    refill_interval_s: float = 3.0
    _exits: list[Exit] = field(default_factory=list)
    _state: dict[str, _ExitState] = field(default_factory=dict)
    _cursor: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_refresh: float = 0.0
    _last_refill_attempt: float = 0.0
    # 补货锁：避免多个协程同时判定「池子空了」而重复提取（按个计费，重复就是浪费）
    _refill_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 本次任务一共提取过多少个 IP，用于成本核对
    fetched_total: int = 0
    # 本次任务最多允许提取多少个 IP（0 = 不限）。点一次「开刷」算一个任务。
    max_fetch: int = 0
    # 熔断：连续失败多少份就判定"代理不可用"
    # 1~2 个失败是正常的（风控、网络抖动），连续 10 个就一定是出问题了
    fuse_threshold: int = 10
    consecutive_failures: int = 0
    fused: bool = False
    # 到期丢弃 / 失败淘汰的计数，任务结束时给用户一张账单
    expired_total: int = 0
    dropped_total: int = 0
    used_total: int = 0

    # -- 拉取 ---------------------------------------------------------------

    def budget_left(self) -> int:
        """本次任务还能再提取多少个 IP；返回 -1 表示不限。"""
        if self.max_fetch <= 0:
            return -1
        return max(0, self.max_fetch - self.fetched_total)

    def note_success(self) -> None:
        """成功一份：连续失败计数清零。"""
        self.consecutive_failures = 0

    def note_failure(self, detail: str = "") -> None:
        """失败一份：累加；到达阈值就熔断，避免继续白买 IP。"""
        self.consecutive_failures += 1
        if self.fuse_threshold > 0 and self.consecutive_failures >= self.fuse_threshold and not self.fused:
            self.fused = True
            log.error(
                "已连续 %d 份失败（最后一次原因：%s），判定代理不可用，停止继续提取 IP",
                self.consecutive_failures,
                detail or "未知",
            )

    def fuse_message(self) -> str:
        return (
            f"连续 {self.consecutive_failures} 份都失败，判定代理不可用，任务已停止："
            "请检查代理链接（白名单 / 额度 / 是否返回了需要账密的 IP），"
            "或先用「IP代理测试.bat」跑一遍再刷"
        )

    async def refresh(self, timeout_s: float = 15.0) -> int:
        """从提取链接拉一批出口。链接为空时退化为直连。"""
        # 熔断后一律不再向上游要 IP
        if self.fused:
            raise ExitPoolEmpty(self.fuse_message())

        # 预算用尽就不要再花钱了：IP 买了用不上，等于白付
        if self.max_fetch > 0 and self.fetched_total >= self.max_fetch:
            log.warning(
                "提取预算已用完（本次最多 %d 个，已提取 %d 个），不再向上游要 IP",
                self.max_fetch, self.fetched_total,
            )
            raise ExitPoolEmpty(
                f"已达到本次任务的提取上限（{self.max_fetch} 个）。"
                "如果目标份数还没刷完，请检查代理是否可用，或重新点一次开刷（会重新计算预算）"
            )

        if not self.link.strip():
            async with self._lock:
                self._exits = [Exit(host=_DIRECT)]
                self._state.setdefault(_DIRECT, _ExitState())
            return 1

        import httpx

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            response = await client.get(self.link)
            response.raise_for_status()
            text = response.text

        parsed = [e for e in (Exit.parse(line) for line in text.splitlines()) if e]
        async with self._lock:
            if parsed:
                self.fetched_total += len(parsed)
                known = {e.addr for e in self._exits}
                fresh = [e for e in parsed if e.addr not in known]
                if fresh:
                    self._exits.extend(fresh)
                for item in parsed:
                    self._state.setdefault(item.addr, _ExitState())
            elif not self._exits and self.allow_direct:
                self._exits = [Exit(host=_DIRECT)]
                self._state.setdefault(_DIRECT, _ExitState())
            self._last_refresh = time.time()
            count = len(self._exits)

        if not parsed and text.strip():
            # 链接有内容但没有 IP：多半是代理商的提示语（白名单/余额/频率）
            snippet = " ".join(text.strip().split())[:120]
            log.warning("提取链接没有返回可用 IP，原始返回：%s", snippet)

        # 配了链接却一个 IP 都没解析出来：说清楚，别悄悄退回本机 IP
        if self.link.strip() and not parsed:
            detail = ""
            if text.strip():
                detail = "；代理返回：" + " ".join(text.strip().split())[:100]
            if _looks_like_config_error(text):
                raise ProxyConfigError(
                    "代理配置有问题，重试无效："
                    + ("白名单未包含本机公网 IP" if _mentions_whitelist(text)
                       else "账号或额度异常")
                    + detail
                    + "。请到代理后台加白名单/充值，或改用账密模式的提取链接"
                )
            raise ExitPoolEmpty(
                "代理链接没有返回可用 IP（检查是否白名单未加当前公网 IP、额度是否用完、提取是否过于频繁）"
                + detail
            )
        return count

    # -- 分配 ---------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self._exits)

    def diagnose(self) -> str:
        """一句话说清池子现状，出错时跟着消息一起抛给用户。"""
        now = time.time()
        usable = sum(
            1
            for e in self._exits
            if e.addr == _DIRECT
            or (
                (s := self._state.get(e.addr, _ExitState())).used < self.per_exit_quota
                and s.blocked_until <= now
            )
        )
        cooling = [
            s.blocked_until - now
            for e in self._exits
            if (s := self._state.get(e.addr, _ExitState())).blocked_until > now
        ]
        wait = f"，最近一个 {max(0.0, min(cooling)):.0f} 秒后可用" if cooling else ""
        return (
            f"解析到 {len(self._exits)} 个出口、每出口 {self.per_exit_quota} 份，"
            f"当前可用 {usable} 个{wait}"
        )

    async def acquire_waiting(self, max_wait_s: float = 60.0) -> "Exit":
        """取出口。

        取不到时按这个顺序处理，而不是直接判整批失败：
        1. 还有出口只是「在冷却」→ 等它冷却结束；
        2. 配了提取链接 → 重新拉一批（同一个 IP 用不成就换一个）。
        """
        try:
            return await self.acquire()
        except ExitPoolEmpty as exc:
            deadline = time.time() + max(0.0, max_wait_s)
            while time.time() < deadline:
                await self._maybe_refill()
                try:
                    return await self.acquire()
                except ExitPoolEmpty:
                    await asyncio.sleep(1.0)
            raise ExitPoolEmpty(
                f"等待 {max_wait_s:.0f} 秒仍无可用出口（{self.diagnose()}）；"
                "建议检查代理链接是否还能提取到新 IP"
            ) from exc

    def capacity(self) -> int:
        """池子还能支撑多少路并发（直连视为不受限）。"""
        if any(e.addr == _DIRECT for e in self._exits):
            return 10 ** 6
        now = time.time()
        total = 0
        for exit_node in self._exits:
            state = self._state.get(exit_node.addr, _ExitState())
            if state.blocked_until > now:
                continue
            total += max(0, self.per_exit_quota - state.used)
        return total

    async def prefetch(self, target: int, *, max_rounds: int = 12) -> int:
        """把池子补到至少 target 个出口。

        关键点：提取链接一次只给一个 IP 时，要**并发**多调几次，
        串行调用会让 10 个 IP 花掉十几秒，提交自然就"一个一个来"。
        """
        target = max(1, int(target))
        if not self.link.strip():
            return self.size

        stalls = 0
        for round_no in range(1, max_rounds + 1):
            missing = target - self.size
            if missing <= 0:
                break

            before = self.size
            # 并发提取：上游对"每秒提取次数"的限制远高于本程序的并发数
            # （例如 51 是每秒 30 次，而最高倍速才 12），串行等待只会拖慢启动。
            # 同时并发数也做了上限，避免一次打出过多请求。
            await asyncio.gather(
                *(self._safe_refresh() for _ in range(min(missing, 12))),
                return_exceptions=True,
            )

            if self.size > before:
                stalls = 0
                log.info("出口预取：第 %d 轮拿到新 IP，当前 %d/%d", round_no, self.size, target)
                continue

            # 提取链接通常有「去重窗口」：短时间内反复调会返回同一批 IP，
            # 这时候再调只是空转，直接退出，用现有的 IP 开跑。
            stalls += 1
            log.info(
                "出口预取：第 %d 轮没有新增 IP（当前 %d/%d），连续空转 %d 次",
                round_no, self.size, target, stalls,
            )
            if stalls >= 2:
                log.warning(
                    "提取链接没有返回新的 IP（已连续 %d 轮），用现有 %d 个出口开始",
                    stalls, self.size,
                )
                break
        return self.size

    async def _safe_refresh(self) -> None:
        try:
            await self.refresh()
        except Exception as exc:  # 提取失败不该让任务起不来
            log.debug("预取失败：%s", exc)

    def release(self, exit_addr: str, *, ok: bool) -> None:
        """归还一次租约：成功继续留在池子里，失败直接淘汰。"""
        if not exit_addr or exit_addr == _DIRECT:
            return
        state = self._state.setdefault(exit_addr, _ExitState())
        if ok:
            self.consecutive_failures = 0
            return
        state.failures += 1
        state.blocked_until = time.time() + self.cooldown_s
        # 坏 IP 立刻淘汰，别占着位置再去排队
        self._drop(exit_addr, reason="failed")

    async def _maybe_refill(self) -> None:
        """按最小间隔重新提取，避免把代理接口打爆。

        整段加锁：多个单位同时无出口时，只能有一个真正去提取，
        否则一次空转就会按「并发数」份数重复扣费。
        """
        if not self.auto_refill or not self.link.strip():
            return

        async with self._refill_lock:
            now = time.time()
            if now - self._last_refill_attempt < self.refill_interval_s:
                return
            self._last_refill_attempt = now

            before = {e.addr for e in self._exits}
            try:
                await self.refresh()
            except Exception as exc:  # 提取失败不该中断任务，下一轮再试
                log.debug("补货失败：%s", exc)
                return

            fresh = [e for e in self._exits if e.addr not in before]
            self.fetched_total += len(fresh)
            # 新 IP 直接可用：把配额清零，避免沿用旧 IP 的计数
            for exit_node in fresh:
                state = self._state.setdefault(exit_node.addr, _ExitState())
                state.used = 0
                state.blocked_until = 0.0

    def _drop(self, addr: str, *, reason: str) -> None:
        """把出口从池子里彻底移除，并记一笔账。"""
        before = len(self._exits)
        self._exits = [e for e in self._exits if e.addr != addr]
        if len(self._exits) != before:
            if reason == "expired":
                self.expired_total += 1
            else:
                self.dropped_total += 1
            log.debug("淘汰出口 %s（%s），池内剩 %d 个", addr, reason, len(self._exits))

    def _drop_expired(self, now: float) -> None:
        """有效期到点的出口直接删掉，绝不再复用。"""
        for item in list(self._exits):
            if item.expires_at and item.expires_at <= now:
                self._drop(item.addr, reason="expired")

    def bill(self) -> str:
        """任务结束时给用户看的一张账单。"""
        remain = len(self._exits)
        return (
            f"提取 {self.fetched_total} 个 IP，用掉 {self.used_total} 个，"
            f"过期丢弃 {self.expired_total} 个，失败淘汰 {self.dropped_total} 个，"
            f"池内剩余 {remain} 个"
        )

    async def acquire(self) -> Exit:
        now = time.time()
        async with self._lock:
            if not self._exits:
                if not self.allow_direct:
                    raise ExitPoolEmpty("出口池为空，请先配置提取链接")
                # 未配置链接时退化为直连，避免任务因为「没填代理」而直接失败
                self._exits = [Exit(host=_DIRECT)]
                self._state.setdefault(_DIRECT, _ExitState())
            self._drop_expired(now)
            count = len(self._exits)
            best: tuple[int, int] | None = None
            for index in range(count):
                candidate = self._exits[index]
                state = self._state.setdefault(candidate.addr, _ExitState())
                # 注意：这里不再「冷却到期就复活配额」。
                # 配额用尽 / 到期 / 失败的出口都已被 _drop 移除，复活只会把过期 IP 重新拿出来用。
                if candidate.addr == _DIRECT:
                    # 直连就是用户自己的网络：不计配额、不冷却，否则少量任务会被自己卡死
                    return candidate
                if state.blocked_until > now or state.used >= self.per_exit_quota:
                    continue
                # 复用优先：先用掉已经开过工的出口，把它用到配额上限再换新的
                if best is None or state.used > best[0]:
                    best = (state.used, index)

            if best is not None:
                chosen = self._exits[best[1]]
                state = self._state[chosen.addr]
                state.used += 1
                self.used_total += 1
                # 配额用尽 → 立刻从池子里移除（真正的「用完就丢」）
                if state.used >= self.per_exit_quota:
                    self._exits = [e for e in self._exits if e.addr != chosen.addr]
                return chosen
            raise ExitPoolEmpty(
                f"所有出口都在配额或冷却中（{self.diagnose()}）；"
                "请调大「IP 复用」或在提取链接里要求更多 IP"
            )

    def report(self, exit_addr: str, *, ok: bool, throttled: bool = False) -> None:
        """回填结果。限流或失败会冷却该出口。"""
        state = self._state.setdefault(exit_addr, _ExitState())
        if ok:
            return
        state.failures += 1
        if exit_addr == _DIRECT:
            return  # 直连只记次数，不做冷却
        penalty = self.cooldown_s * (2.0 if throttled else 1.0) * min(4, state.failures)
        state.blocked_until = time.time() + penalty

    def reset_quota(self) -> None:
        for state in self._state.values():
            state.used = 0

    def snapshot(self) -> dict[str, object]:
        now = time.time()
        return {
            "total": len(self._exits),
            "available": sum(
                1
                for e in self._exits
                if (s := self._state.get(e.addr, _ExitState())).used < self.per_exit_quota
                and s.blocked_until <= now
            ),
            "quota": self.per_exit_quota,
            "last_refresh": self._last_refresh,
        }
