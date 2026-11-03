"""合成答卷：按目标效应量生成一批「统计上说得通」的答案。

与「逐题独立抽样」的区别：这里先给每份答卷算一个综合得分，再按得分排序决定
结局题的取值，所以自变量与结局之间天然存在方向性关系；随后用诊断指标
（α / KMO / 显著项数）做整批校验，不达标就换一批。

刻意不做的事：不追求精确复现某个 β 系数，而是保证**方向正确、显著性落在目标区间**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import Strategy, SynthesisConfig
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.controller import build_matrix
from wjxdesk.engine.diagnostics import cronbach_alpha, kmo
from wjxdesk.engine.rng import derive
from wjxdesk.engine.sampling import generate_sheet

BUFFER_RATIO = 0.25


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    alpha: float
    kmo: float
    significant: int
    predictors: tuple[int, ...]
    attempts: int = 1
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "alpha": round(self.alpha, 3),
            "kmo": round(self.kmo, 3),
            "significant": self.significant,
            "predictors": list(self.predictors),
            "attempts": self.attempts,
            "notes": list(self.notes),
        }


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0 or db <= 0:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


def significance(scores: list[float], outcome: list[float]) -> float:
    """点二列相关 → t 统计量 → 双侧 p（正态近似，n≥20 足够）。"""
    r = _pearson(scores, outcome)
    n = len(scores)
    if n < 4 or abs(r) >= 0.999999:
        return 0.0
    t = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    return 2.0 * (1.0 - _normal_cdf(abs(t)))


class Synthesizer:
    """把「目标」变成一批现成答卷。"""

    def __init__(self, survey: Survey, strategy: Strategy) -> None:
        self.survey = survey
        self.strategy = strategy
        self.config: SynthesisConfig = strategy.synth
        self.predictors = self._resolve_predictors()
        self.outcome_item = (
            survey.item(self.config.outcome_question)
            if self.config.outcome_question
            else None
        )

    # -- 结构 ---------------------------------------------------------------

    def _resolve_predictors(self) -> tuple[int, ...]:
        configured = tuple(self.config.predictors)
        scorable = tuple(
            item.number
            for item in self.survey.items
            if item.kind in {ItemKind.SCALE, ItemKind.MATRIX}
            and item.number != self.config.outcome_question
        )
        if configured:
            return tuple(n for n in configured if n in {i.number for i in self.survey.items})
        return scorable

    @property
    def usable(self) -> bool:
        return bool(self.config.enabled and self.predictors and self.outcome_item is not None)

    # -- 主流程 -------------------------------------------------------------

    def prepare(self, count: int) -> tuple[list[AnswerSheet], SynthesisReport]:
        """生成略多于目标份数的队列（失败重试会消耗）。"""
        if not self.usable:
            return [], SynthesisReport(0.0, 0.0, 0, (), notes=["合成答卷未启用或结构不足"])

        pool_size = count + max(6, int(count * BUFFER_RATIO))
        best: tuple[list[AnswerSheet], SynthesisReport] | None = None

        for attempt in range(1, max(1, self.config.seed_search) + 1):
            scale = 1.0 + 0.25 * (attempt - 1) - 0.5 * (attempt % 2)  # 在 0.5~1.25 间摆动
            batch = self._build_batch(pool_size, attempt, scale)
            report = self._measure(batch, attempt)
            if best is None or self._score(report) < self._score(best[1]):
                best = (batch, report)
            if self._acceptable(report):
                return best

        assert best is not None
        return best

    def _build_batch(self, size: int, attempt: int, scale: float) -> list[AnswerSheet]:
        seed = self.strategy.resolved_seed()
        sheets: list[AnswerSheet] = []
        scores: list[float] = []

        for unit in range(1, size + 1):
            sheet = generate_sheet(
                self.survey,
                self.strategy,
                seed + attempt * 1000,
                unit,
                latent=0.65,
            )
            sheets.append(sheet)
            scores.append(
                self._composite(
                    sheet, scale, derive(seed + attempt * 1000, "synth", unit)
                )
            )

        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        positives = max(1, min(len(scores) - 1, round(self.config.event_rate * len(scores))))
        positive_set = set(order[:positives])

        result: list[AnswerSheet] = []
        for index, sheet in enumerate(sheets):
            result.append(self._apply_outcome(sheet, index in positive_set))
        return result

    def _composite(self, sheet: AnswerSheet, scale: float, rng: object) -> float:
        """由自变量题的取值算出综合得分（越大越偏向「正向结局」）。"""
        total = 0.0
        for number in self.predictors:
            item = self.survey.item(number)
            answer = sheet.get(number)
            if answer is None:
                continue
            value = float(answer.scale) if answer.scale is not None else float(
                answer.picks[0] if answer.picks else 1
            )
            span = max(1.0, float(item.option_count or 5) - 1.0)
            normalized = (value - 1.0) / span * 2.0 - 1.0  # 归一到 [-1,1]
            total += self.config.effects.get(number, 0.3) * normalized
        return total * scale + rng.gauss(0.0, 0.15)

    def _apply_outcome(self, sheet: AnswerSheet, positive: bool) -> AnswerSheet:
        item = self.outcome_item
        assert item is not None
        options = list(range(1, (item.option_count or 2) + 1))
        if not options:
            return sheet

        positives = tuple(o for o in self.config.positive_options if o in options)
        chosen = (positives or (options[-1],))[0] if positive else next(
            (o for o in options if o not in (positives or (options[-1],))),
            options[0],
        )
        answers = dict(sheet.answers)
        answers[item.number] = ItemAnswer(
            number=item.number, picks=(chosen,), scale=float(chosen)
        )
        return AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers)

    # -- 校验 ---------------------------------------------------------------

    def _measure(self, batch: list[AnswerSheet], attempt: int) -> SynthesisReport:
        matrix = build_matrix(self.survey, batch)
        alpha = cronbach_alpha(matrix) if matrix else 0.0
        kmo_value = kmo(matrix) if matrix else 0.0

        outcome: list[float] = []
        columns: dict[int, list[float]] = {n: [] for n in self.predictors}
        positive = set(self.config.positive_options)
        for sheet in batch:
            answer = sheet.get(self.config.outcome_question)
            picked = answer.picks[0] if answer and answer.picks else 0
            outcome.append(1.0 if picked in positive else 0.0)
            for number in self.predictors:
                item = sheet.get(number)
                columns[number].append(
                    float(item.scale) if item and item.scale is not None
                    else float(item.picks[0] if item and item.picks else 1)
                )

        significant = sum(
            1 for number, values in columns.items() if significance(values, outcome) < 0.05
        )
        return SynthesisReport(
            alpha=alpha,
            kmo=kmo_value,
            significant=significant,
            predictors=self.predictors,
            attempts=attempt,
        )

    def _acceptable(self, report: SynthesisReport) -> bool:
        if report.alpha < self.config.alpha_min:
            return False
        if report.kmo and report.kmo < self.config.kmo_min:
            return False
        return self.config.sig_min <= report.significant <= self.config.sig_max

    @staticmethod
    def _score(report: SynthesisReport) -> float:
        """越小越好：α 与显著项数偏离目标的加权距离。"""
        return abs(0.82 - report.alpha) * 2.0 + abs(3 - report.significant)
