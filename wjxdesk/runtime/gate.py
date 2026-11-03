"""发送前体检与成本预估。

两个作用：
1. 让用户在点「开始」之前就知道要花多久、要多少个出口；
2. 让「整批会不会被清洗掉」在开跑前就能看见，而不是跑完才发现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wjxdesk.domain.answer import AnswerSheet
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.controller import evaluate
from wjxdesk.engine.diagnostics import QualityReport


@dataclass(frozen=True, slots=True)
class GateReport:
    samples: int
    items: int
    matrix_items: int
    blank_items: int
    gate_items: int
    straight_lines: int = 0
    duplicate_answers: int = 0
    quality: QualityReport | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "items": self.items,
            "matrix_items": self.matrix_items,
            "blank_items": self.blank_items,
            "gate_items": self.gate_items,
            "straight_lines": self.straight_lines,
            "duplicate_answers": self.duplicate_answers,
            "quality": self.quality.to_dict() if self.quality else None,
            "problems": list(self.problems),
            "passed": self.passed,
        }


def inspect_batch(survey: Survey, sheets: list[AnswerSheet], strategy: Strategy) -> GateReport:
    """整批体检：单份合格不代表整批合理。"""
    matrix_items = sum(1 for it in survey.items if it.kind is ItemKind.MATRIX)
    blank_items = sum(1 for it in survey.items if it.kind is ItemKind.BLANK)
    gate_items = sum(1 for it in survey.items if it.gate is not None)

    straight = 0
    fingerprints: set[tuple] = set()
    duplicates = 0
    for sheet in sheets:
        for item in survey.items:
            if item.kind is not ItemKind.MATRIX:
                continue
            answer = sheet.get(item.number)
            if answer and len(answer.matrix) >= 3 and len({col for _, col in answer.matrix}) == 1:
                straight += 1
        key = tuple(
            (number, tuple(answer.picks), tuple(answer.texts))
            for number, answer in sorted(sheet.answers.items())
        )
        if key in fingerprints:
            duplicates += 1
        fingerprints.add(key)

    quality = evaluate(survey, sheets, strategy.targets) if sheets else None
    problems: list[str] = []
    if straight:
        problems.append(f"{straight} 处矩阵直线作答")
    if duplicates:
        problems.append(f"{duplicates} 份答案与前面的完全重复")
    if quality is not None:
        problems.extend(quality.notes)

    return GateReport(
        samples=len(sheets),
        items=survey.size,
        matrix_items=matrix_items,
        blank_items=blank_items,
        gate_items=gate_items,
        straight_lines=straight,
        duplicate_answers=duplicates,
        quality=quality,
        problems=problems,
    )


def estimate(survey: Survey, strategy: Strategy) -> dict[str, Any]:
    """耗时与出口预估，界面直接展示。"""
    from wjxdesk.runtime.pacing import Pacer

    pacer = Pacer(strategy.pacing, seed=strategy.resolved_seed())
    plan = pacer.estimate(
        total=strategy.total,
        item_count=survey.size,
        concurrency=strategy.concurrency,
        per_exit_quota=strategy.pacing.per_exit_quota,
    )
    plan.update(
        {
            "items": survey.size,
            "total": strategy.total,
            "concurrency": strategy.concurrency,
            "scale_items": sum(1 for it in survey.items if it.kind is ItemKind.SCALE),
            "matrix_items": sum(1 for it in survey.items if it.kind is ItemKind.MATRIX),
            "blank_items": sum(1 for it in survey.items if it.kind is ItemKind.BLANK),
            "gate_items": sum(1 for it in survey.items if it.gate is not None),
        }
    )
    return plan
