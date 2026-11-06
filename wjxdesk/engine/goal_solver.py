"""目标求解器：把「界面配置」编译成「测量模型」，再交给测量引擎。

职责只剩三件：
1. 把 ResearchGoal 解析成 MeasurementModel（domain/measurement.py）；
2. 调 generate_batch 生成一批答卷；
3. 用 diagnose 实测 α / KMO / 显著性，不达标就回调**原因参数**（载荷、构念间相关）。

为什么不再在这里算 α 和显著性：
- 这些指标由测量引擎在"同一批已生成的数据"上算出来，是结果而不是输入；
- 求解器只负责"结果不对时改哪个原因"，因此两个指标不可能互相拆台。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.goal import ResearchGoal, split_ref
from wjxdesk.domain.measurement import MeasurementModel, resolve_model
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.measurement import (
    MeasureResult,
    PredictorResult,
    adjust,
    diagnose,
    generate_batch,
)
from wjxdesk.engine.validity import project_correlation

__all__ = ["GoalSolver", "GoalReport", "PredictorResult"]

ALPHA_TOLERANCE_LOW = 0.02
ALPHA_TOLERANCE_HIGH = 0.04
KMO_TOLERANCE = 0.03


@dataclass(frozen=True, slots=True)
class GoalReport:
    alpha: float = 0.0
    kmo: float = 0.0
    predictors: tuple[PredictorResult, ...] = ()
    significant: int = 0
    attempts: int = 0
    achieved: bool = False
    alpha_by_construct: tuple[tuple[str, float], ...] = ()
    model_summary: str = ""
    # (自变量, 调节变量, 交互系数, p 值)
    interaction: tuple[int, int, float, float] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "alpha": round(self.alpha, 3),
            "kmo": round(self.kmo, 3),
            "significant": self.significant,
            "achieved": self.achieved,
            "attempts": self.attempts,
            "alpha_by_construct": [[name, value] for name, value in self.alpha_by_construct],
            "model": self.model_summary,
            "interaction": (
                {
                    "focal": self.interaction[0],
                    "moderator": self.interaction[1],
                    "beta": round(self.interaction[2], 3),
                    "p": round(self.interaction[3], 4),
                }
                if self.interaction
                else None
            ),
            "predictors": [p.to_dict() for p in self.predictors],
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        parts = [f"α={self.alpha:.2f}", f"KMO={self.kmo:.2f}"]
        if self.predictors:
            parts.append(f"显著 {self.significant}/{len(self.predictors)}")
        parts.append("达标" if self.achieved else "未达标")
        return " | ".join(parts)


class GoalSolver:
    def __init__(self, survey: Survey, strategy: Strategy) -> None:
        self.survey = survey
        self.strategy = strategy
        self.goal: ResearchGoal = strategy.goal
        self.seed = strategy.resolved_seed()
        self.model: MeasurementModel | None = (
            resolve_model(survey, self.goal) if self.goal.enabled else None
        )
        self.binary_outcome = self._is_binary_outcome()

    # -- 结构 ---------------------------------------------------------------

    def _is_binary_outcome(self) -> bool:
        """结局项全是量表/矩阵行时是连续分；否则按单选题做二分类切分。"""
        refs = tuple(self.goal.outcome.items)
        if not refs:
            return False
        return not all(self._ref_scorable(ref) for ref in refs)

    def _ref_scorable(self, ref: int) -> bool:
        question, row = split_ref(ref)
        try:
            item = self.survey.item(question)
        except KeyError:
            return False
        if not row and question in set(self.goal.ordinal_items):
            return item.option_count >= 2
        if row:
            return item.kind is ItemKind.MATRIX and 1 <= row <= item.row_count
        return item.kind is ItemKind.SCALE or self._is_likert(item)

    def _is_likert(self, item) -> bool:
        if item.kind is not ItemKind.SINGLE or item.option_count < 3:
            return False
        labels = [(o.label or "").strip() for o in item.options]
        return labels == [str(i) for i in range(1, len(labels) + 1)]

    @property
    def dimensions(self) -> tuple[tuple[int, ...], ...]:
        """兼容旧调用：构念的题项分组就是"维度"。"""
        if self.model is None:
            return ()
        return tuple(construct.items for construct in self.model.constructs)

    def _analysis_numbers(self) -> list[int]:
        """参与 α / KMO 的项目。

        与旧实现不同：**结局项不再被排除**——它属于某个构念，在因子分析里
        必须和同一构念的题一起出现，否则载荷会偏低、看起来"不属于该构念"。
        """
        if self.model is None:
            return []
        return list(self.model.all_items())

    @property
    def usable(self) -> bool:
        return bool(self.model is not None and self.model.constructs)

    # -- 主流程 -------------------------------------------------------------

    def solve(self, count: int) -> tuple[list, GoalReport]:
        if not self.usable:
            return [], GoalReport(notes=["未启用数据目标，或问卷里没有可测量的题（量表/矩阵）"])

        model = self.model
        assert model is not None

        effects = self._effects(model)
        best: tuple[list, GoalReport] | None = None

        for attempt in range(1, max(1, self.goal.attempts) + 1):
            sheets = generate_batch(
                model,
                self.survey,
                self.strategy,
                count,
                self.seed + attempt * 7919,
                binary_outcome=self.binary_outcome,
                event_rate=float(self.goal.event_rate),
            )
            result = diagnose(model, sheets)
            report = self._report(result, attempt, model, count)

            if best is None or self._score(report) < self._score(best[1]):
                best = (sheets, report)
            if report.achieved:
                return sheets, report

            model = adjust(model, result, self.goal.alpha_target, effects)

        assert best is not None
        return best

    # -- 组装 ---------------------------------------------------------------

    def _effects(self, model: MeasurementModel) -> dict[tuple[int, int], float]:
        """每条「自变量构念 - 结局构念」的目标题级相关。"""
        if model.outcome_index is None:
            return {}
        owner = model.item_owner()
        effects: dict[tuple[int, int], float] = {}
        for ref in self.goal.predictors:
            index = owner.get(ref)
            if index is None or index == model.outcome_index:
                continue
            direction = self.goal.direction_of(ref)
            target = 0.0
            if direction != "0":
                sign = -1.0 if direction == "-" else 1.0
                target = sign * float(self.goal.target_effect or 0.45)
            effects[(min(index, model.outcome_index), max(index, model.outcome_index))] = target
        return effects

    def _report(
        self, result: MeasureResult, attempt: int, model: MeasurementModel, count: int
    ) -> GoalReport:
        need = self._need_significant()
        kmo_ok = not result.kmo or result.kmo >= self.goal.kmo_target - KMO_TOLERANCE
        alpha_ok = bool(result.alpha_by_construct) and (
            self.goal.alpha_target - ALPHA_TOLERANCE_LOW
            <= result.alpha
            <= self.goal.alpha_target + ALPHA_TOLERANCE_HIGH
        )
        interaction_ok = True
        if model.moderation and result.interaction is not None:
            interaction_ok = result.interaction[3] < 0.05
        achieved = bool(
            alpha_ok
            and kmo_ok
            and (not self.goal.wants_conclusion or result.significant >= need)
            and interaction_ok
        )

        notes = list(result.notes)
        _, shrink = project_correlation(model.correlation_matrix())
        if shrink > 0.01:
            notes.append(
                f"构念间相关自相矛盾（矩阵不正定），已整体收缩 {shrink:.0%}；"
                "要保住某个方向，就把与它冲突的那几条同时调弱"
            )
        if count < 20:
            notes.append(f"样本只有 {count} 份，α 与 KMO 的估计波动很大，建议 ≥ 20 份")
        if result.alpha_by_construct and not alpha_ok:
            notes.append(f"α={result.alpha:.2f} 偏离目标 {self.goal.alpha_target:.2f}")
        if result.kmo and not kmo_ok:
            notes.append(f"KMO={result.kmo:.2f} 低于目标 {self.goal.kmo_target:.2f}")
        if model.moderation and result.interaction is not None and not interaction_ok:
            notes.append(
                f"调节交互不显著（β={result.interaction[2]:.3f}, p={result.interaction[3]:.3f}），"
                "可提高 # 调节 里的效应量或增加份数"
            )
        if self.goal.wants_conclusion and result.significant < need:
            notes.append(
                f"{result.significant}/{len(result.predictors)} 项显著，目标是 {need} 项；"
                "可提高 # 相关 里对应构念对的取值"
            )

        return GoalReport(
            alpha=result.alpha,
            kmo=result.kmo,
            predictors=result.predictors,
            significant=result.significant,
            attempts=attempt,
            achieved=achieved,
            alpha_by_construct=result.alpha_by_construct,
            model_summary=model.describe(),
            interaction=result.interaction,
            notes=notes,
        )

    def _need_significant(self) -> int:
        required = self.goal.significant_required
        if required > 0:
            return min(required, len(self.goal.predictors))
        return max(0, len(self.goal.predictors) - 1)

    def _score(self, report: GoalReport) -> float:
        need = self._need_significant()
        return (
            abs(self.goal.alpha_target - report.alpha) * 2.0
            + abs(self.goal.kmo_target - report.kmo)
            + max(0, need - report.significant)
        )

    # -- 自检 ---------------------------------------------------------------

    def debug_model(self) -> dict[str, Any]:
        """给自检报告用：当前真值源长什么样。"""
        if self.model is None:
            return {}
        return {
            "constructs": [
                {
                    "name": c.name,
                    "items": list(c.items),
                    "loading": c.loading,
                    "reverse": c.reverse,
                    "is_outcome": c.is_outcome,
                }
                for c in self.model.constructs
            ],
            "correlations": {
                f"{i}-{j}": round(value, 3)
                for (i, j), value in self.model.correlations.items()
            },
            "default_correlation": self.model.default_correlation,
            "outcome_items": list(self.model.outcome_items),
            "outcome_aggregate": self.model.outcome_aggregate,
            "moderation": [
                {
                    "focal": m.focal,
                    "moderator": m.moderator,
                    "outcome": m.outcome,
                    "effect": m.effect,
                }
                for m in self.model.moderation
            ],
        }
