"""约束系统。

把「什么样的一份答卷才算合格」写成一条条独立规则，而不是散落在生成逻辑里的 if。
```
check(survey, sheet)  →  [Violation]
repair(survey, sheet) →  逐条修，修不动就留给上层决定
```
新增一条约束只需要写一个函数并注册，不需要改生成器。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.survey import Survey


@dataclass(frozen=True, slots=True)
class Violation:
    number: int
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"Q{self.number} {self.code} {self.detail}".strip()


CheckFn = Callable[[Survey, AnswerSheet], list[Violation]]
FixFn = Callable[[Survey, AnswerSheet, Violation], AnswerSheet]


# --------------------------------------------------------------------------- 规则


def check_required(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    out: list[Violation] = []
    for item in survey.items:
        if not item.required or not item.is_visible(sheet.answers):
            continue
        answer = sheet.get(item.number)
        if answer is None or answer.is_empty():
            out.append(Violation(item.number, "missing", "必答题为空"))
    return out


def check_gate(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    """逻辑题：不可见的题必须留空，可见的题必须有答案。"""
    out: list[Violation] = []
    for item in survey.items:
        if item.gate is None:
            continue
        answer = sheet.get(item.number)
        visible = item.is_visible(sheet.answers)
        if not visible and answer is not None and not answer.is_empty():
            out.append(Violation(item.number, "hidden_answered", "逻辑未触发的题却有答案"))
        if visible and (answer is None or answer.is_empty()):
            out.append(Violation(item.number, "missing_gate", "逻辑已触发但缺答案"))
    return out


def check_choice_bounds(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    out: list[Violation] = []
    for item in survey.items:
        if item.kind is not ItemKind.MULTI:
            continue
        answer = sheet.get(item.number)
        if answer is None or not answer.picks:
            continue
        low, high = item.choice_bounds()
        if not low <= len(answer.picks) <= high:
            out.append(
                Violation(
                    item.number,
                    "choice_bounds",
                    f"选择数 {len(answer.picks)} 不在 {low}~{high}",
                )
            )
    return out


def check_matrix_straight(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    """矩阵题全部同行同列 = 直线作答，是数据清洗的头号剔除对象。"""
    out: list[Violation] = []
    for item in survey.items:
        if item.kind is not ItemKind.MATRIX:
            continue
        answer = sheet.get(item.number)
        if answer is None or len(answer.matrix) < 3:
            continue
        columns = {col for _, col in answer.matrix}
        if len(columns) == 1:
            out.append(Violation(item.number, "straight_line", "矩阵题全选同一列"))
    return out


def check_numeric(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    out: list[Violation] = []
    for item in survey.items:
        if item.kind not in {ItemKind.SLIDER, ItemKind.SCALE}:
            continue
        answer = sheet.get(item.number)
        if answer is None or answer.scale is None:
            continue
        low = item.low if item.low is not None else float("-inf")
        high = item.high if item.high is not None else float("inf")
        if not low <= answer.scale <= high:
            out.append(
                Violation(item.number, "out_of_range", f"{answer.scale} 越界 {low}~{high}")
            )
    return out


def check_blank(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    out: list[Violation] = []
    for item in survey.items:
        if item.kind is not ItemKind.BLANK or not item.required:
            continue
        answer = sheet.get(item.number)
        if answer is None or not any(t.strip() for t in answer.texts):
            out.append(Violation(item.number, "empty_text", "必答填空为空"))
    return out


RULES: tuple[tuple[str, CheckFn], ...] = (
    ("required", check_required),
    ("gate", check_gate),
    ("choice_bounds", check_choice_bounds),
    ("matrix_straight", check_matrix_straight),
    ("numeric", check_numeric),
    ("blank", check_blank),
)


def check(survey: Survey, sheet: AnswerSheet) -> list[Violation]:
    out: list[Violation] = []
    for _, fn in RULES:
        out.extend(fn(survey, sheet))
    return out


# --------------------------------------------------------------------------- 修复


def _with(sheet: AnswerSheet, number: int, answer: ItemAnswer | None) -> AnswerSheet:
    answers = dict(sheet.answers)
    if answer is None:
        answers.pop(number, None)
    else:
        answers[number] = answer
    return replace(sheet, answers=answers)


def repair(survey: Survey, sheet: AnswerSheet, rounds: int = 3) -> tuple[AnswerSheet, list[Violation]]:
    """尽力修复。修不动的违规原样返回，由上层决定丢弃或标记。"""
    current = sheet
    for _ in range(max(1, rounds)):
        violations = check(survey, current)
        if not violations:
            return current, []
        fixed_any = False
        for violation in violations:
            item = survey.item(violation.number)
            answer = current.get(violation.number)

            if violation.code == "hidden_answered":
                current = _with(current, item.number, None)
                fixed_any = True
            elif violation.code == "straight_line" and answer is not None:
                pairs = list(answer.matrix)
                row, col = pairs[0]
                alternatives = [c for c in range(1, item.option_count + 1) if c != col]
                if alternatives:
                    pairs[0] = (row, alternatives[-1])
                    current = _with(current, item.number, replace(answer, matrix=tuple(pairs)))
                    fixed_any = True
            elif violation.code == "choice_bounds" and answer is not None:
                low, high = item.choice_bounds()
                picks = list(answer.picks)[:high]
                while len(picks) < low:
                    for candidate in range(1, item.option_count + 1):
                        if candidate not in picks:
                            picks.append(candidate)
                            break
                    else:
                        break
                current = _with(current, item.number, replace(answer, picks=tuple(sorted(picks))))
                fixed_any = True
            elif violation.code == "out_of_range" and answer is not None:
                low = item.low if item.low is not None else 1.0
                current = _with(current, item.number, replace(answer, scale=float(low)))
                fixed_any = True
            elif violation.code == "empty_text" and answer is not None:
                current = _with(current, item.number, replace(answer, texts=("无",)))
                fixed_any = True
            elif violation.code == "missing" and item.kind is ItemKind.MULTI:
                current = _with(current, item.number, ItemAnswer(number=item.number, picks=(1,)))
                fixed_any = True
        if not fixed_any:
            break
    return current, check(survey, current)
