"""页面解析：HTML → Survey。

解析器只做结构识别，不做任何生成决策。所有选择器来自协议清单，
平台改版时改清单即可，不用动这里。
"""

from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.errors import ParseError
from wjxdesk.domain.survey import Gate, Item, Option, Survey
from wjxdesk.protocol.manifest import Manifest

_NUMBER_RE = re.compile(r"(\d+)")
_KIND_BY_NAME = {kind.value: kind for kind in ItemKind}


def _text(node: Tag | None) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _kind(raw_type: str, manifest: Manifest) -> ItemKind:
    name = manifest.parse.kind_map.get(str(raw_type).strip(), "")
    return _KIND_BY_NAME.get(name, ItemKind.UNKNOWN)


def _extract_number(container: Tag, manifest: Manifest, fallback: int) -> int:
    node = container.select_one(manifest.parse.number_selector)
    match = _NUMBER_RE.search(_text(node))
    return int(match.group(1)) if match else fallback


def _extract_title(container: Tag, manifest: Manifest) -> str:
    node = container.select_one(manifest.parse.title_selector)
    if node is None:
        node = container.select_one(manifest.parse.title_fallback)
    return _text(node)


def _option_rows(container: Tag, manifest: Manifest) -> list[Option]:
    options: list[Option] = []
    for index, row in enumerate(container.select(manifest.parse.option_container), start=1):
        label = _text(row.select_one(manifest.parse.option_label)) or _text(row)
        has_input = bool(row.select("textarea, input[type=text]"))
        options.append(
            Option(
                index=index,
                label=label,
                value=(row.get("data-value") or ""),
                requires_text=has_input or ("其他" in label and has_input),
            )
        )
    return options


def _matrix(container: Tag, manifest: Manifest) -> tuple[list[Option], list[Option]]:
    """返回 (列选项, 行标签)。列来自表头，行来自每一行的首列。"""
    table = container.select_one(manifest.parse.matrix_table)
    if table is None:
        return [], []
    rows = table.select("tr")
    if not rows:
        return [], []
    header_cells = rows[0].select("td, th")[1:]
    columns = [
        Option(index=i, label=_text(cell))
        for i, cell in enumerate(header_cells, start=1)
    ]

    row_labels: list[Option] = []
    for tr in rows[1:]:
        classes = tr.get("class") or []
        # 表头行与分组标题行（colspan 整行）都不是数据行
        if "trlabel" in classes or "rowtitle" in classes:
            continue
        cells = tr.select("td, th")
        if len(cells) < 2 and tr.get("fid") is None:
            continue
        title_cell = tr.select_one("td.scalerowtitletd") or tr.select_one("td.mleft")
        label = _text(title_cell) if title_cell is not None else _text(cells[0] if cells else None)
        if not label:
            continue
        row_labels.append(Option(index=len(row_labels) + 1, label=label))
    return columns, row_labels


def _blank_count(container: Tag) -> int:
    return max(1, len(container.select("textarea, input[type=text]")))


def _dropdown(container: Tag) -> list[Option]:
    options: list[Option] = []
    for index, node in enumerate(container.select("select option"), start=1):
        label = _text(node)
        if not label or "请选择" in label:
            continue
        options.append(Option(index=len(options) + 1, label=label, value=node.get("value") or ""))
    return options


def _slider_bounds(container: Tag) -> tuple[float | None, float | None]:
    node = container.select_one("input[type=range]")
    if node is None:
        return None, None
    try:
        low = float(node.get("min", 0))
        high = float(node.get("max", 100))
    except (TypeError, ValueError):
        return None, None
    return low, high


def _scale_rating(container: Tag) -> list[Option]:
    """量表题：评分档位在 ul>li>a 上，val 属性即选项序号，title 是档位文案。"""
    nodes = container.select("div.scale-rating ul li a") or container.select("ul li a[val]")
    options: list[Option] = []
    for node in nodes:
        raw = node.get("val") or node.get_text(strip=True)
        label = node.get("title") or node.get_text(strip=True) or str(raw)
        try:
            index = int(str(raw))
        except (TypeError, ValueError):
            index = len(options) + 1
        options.append(Option(index=index, label=label, value=str(raw)))
    return options


def _rank_options(container: Tag) -> list[Option]:
    nodes = container.select("ul li") or container.select("div.ui-checkbox")
    return [Option(index=i, label=_text(n)) for i, n in enumerate(nodes, start=1)]


def _required(container: Tag, manifest: Manifest) -> bool:
    marker = manifest.parse.required_class
    return bool(container.select(f".{marker}")) or "req" in (container.get("class") or [])


def _containers(soup: BeautifulSoup, manifest: Manifest) -> list[Tag]:
    found = soup.select(manifest.parse.field_selector)
    if found:
        return found
    # 兼容旧结构：题目容器带 id="div<number>"
    return [node for node in soup.select("div[id^=div]") if node.get("type")]


def parse_survey(html: str, manifest: Manifest, *, origin: str = "", token: str = "") -> Survey:
    soup = BeautifulSoup(html, "html.parser")
    containers = _containers(soup, manifest)
    if not containers:
        raise ParseError("页面中没有识别到题目容器，可能是空壳页或结构已变化")

    items: list[Item] = []
    for fallback, container in enumerate(containers, start=1):
        raw_type = str(container.get(manifest.parse.type_attr) or "").strip()
        kind = _kind(raw_type, manifest)
        if kind is ItemKind.UNKNOWN:
            continue

        number = _extract_number(container, manifest, fallback)
        title = _extract_title(container, manifest)

        options: list[Option] = []
        rows: list[Option] = []
        blanks = 0
        low = high = None

        if kind in {ItemKind.SINGLE, ItemKind.MULTI}:
            options = _option_rows(container, manifest)
        elif kind is ItemKind.DROPDOWN:
            options = _dropdown(container)
        elif kind is ItemKind.RANK:
            options = _rank_options(container)
        elif kind in {ItemKind.SCALE, ItemKind.MATRIX}:
            options, rows = _matrix(container, manifest)
            if not options and kind is ItemKind.SCALE:
                # 量表题不一定是表格结构，常见的是 rating 列表
                options = _scale_rating(container)
            if not options and kind is ItemKind.SCALE:
                options = _option_rows(container, manifest)
        elif kind is ItemKind.BLANK:
            blanks = _blank_count(container)
        elif kind is ItemKind.SLIDER:
            low, high = _slider_bounds(container)

        if kind in {ItemKind.SINGLE, ItemKind.MULTI, ItemKind.SCALE, ItemKind.MATRIX} and not options:
            continue

        items.append(
            Item(
                number=number,
                kind=kind,
                title=title,
                required=_required(container, manifest),
                options=tuple(options),
                rows=tuple(rows),
                blanks=blanks,
                low=low,
                high=high,
                gate=_infer_gate(number, container),
                raw_type=raw_type,
            )
        )

    if not items:
        raise ParseError("识别到题目容器但没有可用题目")

    items.sort(key=lambda it: it.number)
    title = _text(soup.select_one("div.survey-title, h1, title"))
    token = token or _extract_token(html)
    extra: dict[str, str] = {}
    if origin:
        extra["origin"] = origin
    for key, value in (
        ("scene", _extract_scene(html)),
        ("jqnonce", _extract_nonce(html)),
        ("page_starttime", _extract_page_starttime(html)),
    ):
        if value:
            extra[key] = value
    # 问卷的验证配置：captchaType=0 不验证、2 阿里智能验证、3 腾讯验证
    captcha = _captcha_type(html)

    extra["captcha_type"] = captcha
    if captcha != "0":
        extra["captcha_required"] = "1"

    return Survey(token=token, title=title, items=tuple(items), extra=extra)


def _infer_gate(number: int, container: Tag) -> Gate | None:
    """从样式/属性推断逻辑显隐。

    结构化程度有限，这里只处理最明确的写法：容器带 data-gate 属性，
    形如 "2:1,3" 表示依赖第 2 题选中 1 或 3。
    """
    raw = container.get("data-gate") or container.get("gate")
    if not raw:
        return None
    try:
        head, tail = str(raw).split(":", 1)
        picks = tuple(int(x) for x in re.split(r"[,|]", tail) if x.strip().isdigit())
        return Gate(item_number=int(head), any_of=picks)
    except (ValueError, AttributeError):
        return None


def _extract_token(html: str) -> str:
    for pattern in (
        r"shortid\s*[:=]\s*['\"](\w+)",
        r"/vm/(\w+)\.aspx",
        r"curid\s*[:=]\s*['\"](\w+)",
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ""


def _extract_scene(html: str) -> str:
    """场景标识。页面没有该字段时返回空串，编码层会跳过它。"""
    match = re.search(r"sceneId\s*[:=]\s*['\"]([^'\"]+)", html)
    return match.group(1) if match else ""


def _extract_nonce(html: str) -> str:
    """页面自带的随机串。优先用它，而不是自己造一个。"""
    match = re.search(r"var\s+jqnonce\s*=\s*['\"]([^'\"]+)['\"]", html)
    return match.group(1) if match else ""


def _extract_page_starttime(html: str) -> str:
    """页面渲染时刻，格式固定为 YYYY/M/D HH:MM:SS，直接沿用可保证与服务端一致。

    注意属性顺序不固定（value 可能写在 id 前面），所以先定位标签再取 value。
    """
    for tag in re.finditer(r"<input[^>]*starttime[^>]*>", html):
        value = re.search(r'value=["\']([^"\']+)', tag.group(0))
        if value:
            return value.group(1)
    return ""


def _captcha_type(html: str) -> str:
    """从页面脚本里取问卷的验证配置（0/2/3）。"""
    match = re.search(r"captchaType\s*=\s*['\"]?(\d)", html)
    return match.group(1) if match else "0"


def iter_scored_items(survey: Survey) -> Iterable[Item]:
    """可用于统计诊断的题目（量表/矩阵）。"""
    for item in survey.items:
        if item.kind in {ItemKind.SCALE, ItemKind.MATRIX}:
            yield item
