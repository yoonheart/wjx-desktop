"""答卷模型。

一份答卷对象**不可变**：重试时只换出口重发同一份答案，绝不重新生成，
以免失败重试把整批答案的统计分布拖偏。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.survey import Item


@dataclass(frozen=True, slots=True)
class ItemAnswer:
    """一道题的答案。字段按题型取用，未用到的保持空。"""

    number: int
    picks: tuple[int, ...] = ()
    texts: tuple[str, ...] = ()
    scale: float | None = None
    matrix: tuple[tuple[int, int], ...] = ()
    free_text: str = ""

    def is_empty(self) -> bool:
        return not (self.picks or self.texts or self.matrix or self.free_text) and self.scale is None


@dataclass(frozen=True, slots=True)
class AnswerSheet:
    """一份完整答卷。seed 与 unit 是它的身份，可据此精确复现。"""

    seed: int
    unit: int
    answers: Mapping[int, ItemAnswer] = field(default_factory=dict)

    def get(self, number: int) -> ItemAnswer | None:
        return self.answers.get(number)

    def picks_of(self, number: int) -> tuple[int, ...]:
        ans = self.answers.get(number)
        return tuple(ans.picks) if ans else ()

    def to_plain(self) -> dict[str, Any]:
        """序列化，用于任务详情与落库。"""
        return {
            "seed": self.seed,
            "unit": self.unit,
            "answers": [
                {
                    "number": a.number,
                    "picks": list(a.picks),
                    "texts": list(a.texts),
                    "scale": a.scale,
                    "matrix": [list(p) for p in a.matrix],
                    "free_text": a.free_text,
                }
                for _, a in sorted(self.answers.items())
            ],
        }


@dataclass(frozen=True, slots=True)
class Stamp:
    """提交时间标记。

    三个值必须与真实到达时间自洽：开始时刻、停留时长、请求发出时刻。
    事后改写时间戳是最容易被识别为机器的特征，因此只允许由节奏器产出。
    """

    started_at: float
    elapsed_s: float
    issued_at: float
    nonce: str = ""

    @property
    def started_ms(self) -> int:
        return int(self.started_at * 1000)

    @property
    def issued_ms(self) -> int:
        return int(self.issued_at * 1000)

    def is_coherent(self, tolerance_s: float = 2.0) -> bool:
        """自洽性检查：发出时刻不得早于「开始 + 停留」。"""
        expected = self.started_at + self.elapsed_s
        return abs(self.issued_at - expected) <= max(tolerance_s, self.elapsed_s * 0.05)


def blank_numbers(survey_items: tuple[Item, ...], sheet: AnswerSheet) -> tuple[int, ...]:
    """找出被逻辑跳过而留空的题号，供编码层生成跳过占位。"""
    skipped: list[int] = []
    for item in survey_items:
        if item.kind in {ItemKind.SCALE, ItemKind.MATRIX}:
            continue
        ans = sheet.get(item.number)
        if ans is None or ans.is_empty():
            skipped.append(item.number)
    return tuple(skipped)
