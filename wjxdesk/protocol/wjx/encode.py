"""答案编码：AnswerSheet → WireRequest。

这是全项目最需要被测试覆盖的函数：它是纯函数，输入确定则输出确定。
编码规则全部来自协议清单，代码里不出现裸分隔符。
"""

from __future__ import annotations

import uuid

import random
import time
from urllib.parse import quote, urljoin

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer, Stamp
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import ChannelConfig
from wjxdesk.domain.survey import Item, Survey
from wjxdesk.protocol.contracts import WireRequest
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.wjx import seal


class WjxEncoder:
    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest

    # -- 答案串 -------------------------------------------------------------

    def build_blob(self, survey: Survey, sheet: AnswerSheet) -> str:
        spec = self.manifest.blob
        parts: list[str] = []
        for item in survey.items:
            answer = sheet.get(item.number)
            if not item.is_visible(sheet.answers):
                parts.append(f"{item.number}${self._skip(item)}")
                continue
            content = self._content(item, answer)
            if content is None:
                continue
            parts.append(f"{item.number}${content}")
        return spec.item_sep.join(parts)

    def _skip(self, item: Item) -> str:
        spec = self.manifest.blob
        if item.kind is ItemKind.BLANK:
            return spec.skip_token_text
        return spec.skip_token

    def _content(self, item: Item, answer: ItemAnswer | None) -> str | None:
        spec = self.manifest.blob
        if answer is None:
            return None

        if item.kind in {ItemKind.SINGLE, ItemKind.DROPDOWN}:
            if not answer.picks:
                return None
            base = str(answer.picks[0])
            if answer.free_text:
                return f"{base}{spec.blank_sep}{spec.escape_text(answer.free_text)}"
            return base

        if item.kind is ItemKind.MULTI:
            if not answer.picks:
                return None
            base = spec.option_sep.join(str(p) for p in answer.picks)
            if answer.free_text:
                return f"{base}{spec.blank_sep}{spec.escape_text(answer.free_text)}"
            return base

        if item.kind is ItemKind.RANK:
            if not answer.picks:
                return None
            # 线上是逗号分隔（页面脚本：d += ","），不是竖线
            return spec.rank_sep.join(str(p) for p in answer.picks)

        if item.kind is ItemKind.MATRIX:
            if not answer.matrix:
                return None
            pairs = [
                f"{row}{spec.matrix_row_sep}{col}" for row, col in answer.matrix
            ]
            return spec.matrix_pair_sep.join(pairs)

        if item.kind in {ItemKind.SCALE, ItemKind.SLIDER}:
            if answer.scale is not None:
                value = float(answer.scale)
                return str(int(value)) if value.is_integer() else f"{value:g}"
            if answer.texts:
                return spec.escape_text(answer.texts[0])
            return None

        if item.kind is ItemKind.BLANK:
            if not answer.texts:
                return None
            return spec.blank_sep.join(spec.escape_text(t) for t in answer.texts)

        return None

    # -- 请求 ---------------------------------------------------------------

    def encode(
        self,
        survey: Survey,
        sheet: AnswerSheet,
        stamp: Stamp,
        channel: ChannelConfig,
        *,
        exit_addr: str = "",
        ua: str = "",
        source_value: str = "",
        source_params: Mapping[str, str] | None = None,
    ) -> WireRequest:
        wire = self.manifest.wire
        origin = survey.extra.get("origin") or "https://www.wjx.cn"
        url = urljoin(origin + "/", wire.submit_path.lstrip("/"))

        blob = self.build_blob(survey, sheet)
        # jqnonce 是一次性随机串：浏览器每次打开页面都会重新生成。
        # 复用同一个 nonce 等于告诉服务端「同一串被多个 IP 重复提交」，是重放特征。
        nonce = str(uuid.uuid4())

        # 作答时长靠「回填开始时间」实现，不真的等待：
        # 服务端用「现在 - starttime」算所用时间，cst 是同一时刻的毫秒值，
        # ktimes 与之一致（校验串正好取它的个位做异或）。
        ktimes = max(1, int(round(stamp.elapsed_s)))
        start_seconds = stamp.issued_at - ktimes

        params: dict[str, str] = dict(wire.fixed_params)
        params.update(
            {
                "shortid": survey.token,
                "starttime": format_starttime(start_seconds),
                "cst": str(int(start_seconds * 1000)),
                "ktimes": str(ktimes),
                # 客户端时间戳：线上页面每次提交都会带，缺了会被当成重放
                "t": str(stamp.issued_ms),
                "jcn": survey.token,
                # 与线上一致：2e9 起的小数，不是整数
                "rn": _random_number(nonce),
                "jqnonce": nonce,
                "jqsign": seal.compute(
                    channel.seal_mode or "xor_ktimes",
                    nonce=nonce,
                    ktimes=ktimes,
                    token=survey.token,
                ),
            }
        )
        # 验证配置：只有开了验证的问卷才带 capt（页面脚本：captchaType==2 → capt=2）
        captcha_type = str(survey.extra.get("captcha_type", "0") or "0")
        if captcha_type in {"2", "3"}:
            params["capt"] = captcha_type

        # 来源按线上脚本处理：手机=source=directphone，微信=iwx=1，PC=不传。
        # 注意微信不是 source=wechat——那个值服务端不认识，后台会显示成裸的 "wechat"。
        if source_params:
            params.update({k: str(v) for k, v in source_params.items()})
        elif source_value:
            params["source"] = source_value
        elif channel.source_tokens:
            params["source"] = channel.source_tokens[0]

        body_pairs = {wire.body_field: blob}
        scene = survey.extra.get("scene", "")
        if wire.scene_field and scene:
            body_pairs[wire.scene_field] = scene
        body = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in body_pairs.items())

        headers = {
            "Content-Type": wire.content_type,
            "Accept": "text/plain, */*; q=0.01",
            "Origin": origin,
            "Referer": f"{origin}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if ua:
            headers["User-Agent"] = ua
            headers.update(browser_headers(ua))

        return WireRequest(
            method=wire.method,
            url=url,
            params=params,
            headers=headers,
            body=body,
            meta={
                "blob_length": str(len(blob)),
                "ktimes": str(ktimes),
                "seal_mode": channel.seal_mode,
                "exit": exit_addr,
            },
        )


def _stable_nonce(seed: int, unit: int) -> str:
    """无外部 nonce 时，用种子派生一个稳定的 32 位十六进制串。"""
    rng = random.Random(f"{seed}:{unit}:nonce")
    return "".join(rng.choice("0123456789abcdef") for _ in range(32))


def browser_headers(ua: str) -> dict[str, str]:
    """按 UA 生成与浏览器一致的客户端提示头。

    线上真实请求会带 sec-ch-ua*、sec-fetch-*，缺这些的请求在风控眼里是「非浏览器」。
    """
    if not ua:
        return {}
    mobile = "Mobile" in ua or "Android" in ua or "iPhone" in ua
    if "MicroMessenger" in ua:
        platform, plat_name = "?1", "Android"
    elif mobile:
        platform, plat_name = "?1", "Android"
    else:
        platform, plat_name = "?0", "Windows"

    return {
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "Sec-Ch-Ua": '"Not:A-Brand";v="8", "Chromium";v="156", "Microsoft Edge";v="156"',
        "Sec-Ch-Ua-Mobile": platform,
        "Sec-Ch-Ua-Platform": f'"{plat_name}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _random_number(nonce: str) -> str:
    """线上 rn 形如 2123456789.123456：可复现但格式一致。"""
    rng = random.Random(nonce)
    # 实抓样本形如 240935036.52232742：9 位整数 + 一串小数
    return f"{rng.random() * 1_000_000_000:.8f}"


def _ktimes_for(seed: int, unit: int) -> int:
    """交互次数的替代值：1~9 的小整数，校验串用它取个位做异或。"""
    return 1 + (abs(seed * 31 + unit * 7) % 9)


def format_starttime(timestamp: float) -> str:
    """页面使用的开始时间格式：YYYY/M/D HH:MM:SS（不补零，与服务端一致）。"""
    local = time.localtime(timestamp)
    return f"{local.tm_year}/{local.tm_mon}/{local.tm_mday} {local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d}"
