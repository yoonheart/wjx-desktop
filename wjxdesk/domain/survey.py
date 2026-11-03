"""问卷结构模型。

这里是全系统的「唯一真相源」：解析层产出它，生成层消费它，编码层读取它。
任何地方都不允许绕过它直接操作原始 HTML 或原始 JSON。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from wjxdesk.domain.enums import ItemKind


@dataclass(frozen=True, slots=True)
class Option:
    """一个选项。序号从 1 开始，与线上编码保持一致。"""

    index: int
    label: str
    value: str = ""
    requires_text: bool = False


@dataclass(frozen=True, slots=True)
class Gate:
    """逻辑显示条件：只有前置题选中了指定选项时，本题才展示。"""

    item_number: int
    any_of: tuple[int, ...] = ()

    def satisfied_by(self, picked: tuple[int, ...]) -> bool:
        if not self.any_of:
            return bool(picked)
        return bool(set(self.any_of) & set(picked))


@dataclass(frozen=True, slots=True)
class Item:
    number: int
    kind: ItemKind
    title: str = ""
    required: bool = False
    options: tuple[Option, ...] = ()
    rows: tuple[Option, ...] = ()
    blanks: int = 0
    min_choices: int = 0
    max_choices: int | None = None
    low: float | None = None
    high: float | None = None
    page: int = 0
    gate: Gate | None = None
    raw_type: str = ""

    @property
    def option_count(self) -> int:
        return len(self.options)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def effective_blanks(self) -> int:
        if self.blanks:
            return self.blanks
        return 1 if self.kind is ItemKind.BLANK else 0

    def choice_bounds(self) -> tuple[int, int]:
        """多选的合法选择数量区间。"""
        n = self.option_count
        low = self.min_choices or 1
        high = self.max_choices or n
        return max(1, min(low, n)), max(1, min(high, n))

    def is_visible(self, answers: Mapping[int, "object"]) -> bool:
        """按已生成的答案判断本题是否展示。

        未挂载条件的题一律可见；挂载条件但前置题缺席时按「不可见」处理，
        避免生成无法自洽的答案。
        """
        if self.gate is None:
            return True
        prev = answers.get(self.gate.item_number)
        if prev is None:
            return False
        picks = tuple(getattr(prev, "picks", ()) or ())
        return self.gate.satisfied_by(picks)


@dataclass(frozen=True, slots=True)
class Survey:
    """一份问卷。"""

    token: str
    title: str = ""
    items: tuple[Item, ...] = ()
    page_count: int = 1
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numbers = [it.number for it in self.items]
        if len(numbers) != len(set(numbers)):
            raise ValueError("题目序号重复，解析结果不可信")

    @property
    def size(self) -> int:
        return len(self.items)

    def item(self, number: int) -> Item:
        for it in self.items:
            if it.number == number:
                return it
        raise KeyError(f"题号不存在：{number}")

    def by_kind(self, kind: ItemKind) -> tuple[Item, ...]:
        return tuple(it for it in self.items if it.kind is kind)

    def kind_map(self) -> dict[int, ItemKind]:
        return {it.number: it.kind for it in self.items}

    def matrix_row_map(self) -> dict[int, int]:
        return {it.number: it.row_count for it in self.items if it.kind is ItemKind.MATRIX}
