"""目标求解器：按「想要的结果」直接构造答卷，再实测校验。

思路（和"随机生成碰运气"相反）：
1. 信度有解析解：k 道题、目标 α → 误差方差 e² = k/((k-1)α) - 1，
   按这个比例把「维度因子 + 误差」叠加，α 自然落在目标附近；
2. 显著性有临界值：皮尔逊 r 与 t 的换算决定 p<0.05 需要多大相关，
   按需要的 r 反推效应量，结局题再按事件占比切分；
3. 构造完仍然实测一遍（α / KMO / r / p），不达标就按差距微调重来，
   并把最接近的一次连同「差在哪」一起报出来。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.goal import ResearchGoal
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.controller import build_matrix
from wjxdesk.engine.diagnostics import cronbach_alpha, kmo
from wjxdesk.engine.rng import derive
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.engine.validity import _cholesky, flip_index


@dataclass(frozen=True, slots=True)
class PredictorResult:
    number: int
    correlation: float
    p_value: float
    direction: str
    expected: str

    @property
    def ok(self) -> bool:
        if self.expected == "0":
            return self.p_value >= 0.05
        return self.p_value < 0.05 and self.direction == self.expected

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.number,
            "r": round(self.correlation, 3),
            "p": round(self.p_value, 4),
            "direction": self.direction,
            "expected": self.expected,
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class GoalReport:
    alpha: float = 0.0
    kmo: float = 0.0
    predictors: tuple[PredictorResult, ...] = ()
    significant: int = 0
    attempts: int = 0
    achieved: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "alpha": round(self.alpha, 3),
            "kmo": round(self.kmo, 3),
            "significant": self.significant,
            "achieved": self.achieved,
            "attempts": self.attempts,
            "predictors": [p.to_dict() for p in self.predictors],
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        parts = [f"α={self.alpha:.2f}", f"KMO={self.kmo:.2f}"]
        if self.predictors:
            parts.append(f"显著 {self.significant}/{len(self.predictors)}")
        parts.append("达标" if self.achieved else "未达标")
        return " | ".join(parts)


# --------------------------------------------------------------------------- 工具


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0 or db <= 0:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


def _p_value(r: float, n: int) -> float:
    if n < 4 or abs(r) >= 0.999999:
        return 0.0 if abs(r) > 0.999999 else 1.0
    t = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    return 2.0 * (1.0 - _normal_cdf(abs(t)))


def _error_ratio(k: int, alpha: float) -> float:
    """由目标 α 反推「误差方差 / 题目数」的比值。

    α = k/(k-1) · 1/(1+e²)  ⇒  e² = k/((k-1)α) - 1
    """
    alpha = max(0.30, min(0.98, alpha))
    value = k / ((k - 1) * alpha) - 1.0 if k > 1 else 0.5
    return max(0.02, value)


def _scale_index(z: float, count: int) -> int:
    """把标准正态值映射到 1..count 的选项下标（保留正态形状）。"""
    p = min(0.999, max(0.001, _normal_cdf(z)))
    return int(p * count) + 1


# --------------------------------------------------------------------------- 求解器


class GoalSolver:
    def __init__(self, survey: Survey, strategy: Strategy) -> None:
        self.survey = survey
        self.strategy = strategy
        self.goal: ResearchGoal = strategy.goal
        # 可构造的项目：量表题本身，以及矩阵题的每一行（行号记作 题号*100+行号）
        self.scale_items = [i for i in survey.items if i.kind is ItemKind.SCALE]
        self.matrix_items = [i for i in survey.items if i.kind is ItemKind.MATRIX]
        self.dimensions = self._resolve_dimensions()
        self.seed = strategy.resolved_seed()

    # -- 结构 ---------------------------------------------------------------

    def _resolve_dimensions(self) -> tuple[tuple[int, ...], ...]:
        configured = tuple(
            tuple(n for n in group if self._is_scorable(n)) for group in self.goal.dimensions
        )
        configured = tuple(g for g in configured if g)
        if configured:
            return configured

        numbers = self._all_scorable()
        if not numbers:
            return ()
        # 默认全部算一个维度（题少时这样最能凑出 α）
        return (tuple(numbers),)

    def _is_likert(self, item) -> bool:
        """单选题但选项是 1..n 的，当作李克特量表。"""
        if item.kind is not ItemKind.SINGLE or item.option_count < 3:
            return False
        labels = [(o.label or "").strip() for o in item.options]
        return labels == [str(i) for i in range(1, len(labels) + 1)]

    def _all_scorable(self) -> list[int]:
        numbers = [item.number for item in self.scale_items]
        numbers.extend(
            i.number for i in self.survey.items if self._is_likert(i)
        )
        for item in self.matrix_items:
            for row in range(1, item.row_count + 1):
                numbers.append(item.number * 100 + row)
        return numbers

    def _is_scorable(self, number: int) -> bool:
        return number in set(self._all_scorable())

    def _is_scale(self, number: int) -> bool:
        return any(item.number == number for item in self.scale_items)

    @property
    def usable(self) -> bool:
        return bool(self.goal.enabled and self.dimensions)

    # -- 主流程 -------------------------------------------------------------

    def solve(self, count: int) -> tuple[list[AnswerSheet], GoalReport]:
        if not self.usable:
            return [], GoalReport(notes=["未启用数据目标，或问卷里没有量表题"])

        best: tuple[list[AnswerSheet], GoalReport] | None = None
        # α 对噪声不是线性的，用二分找「刚好达到目标」的噪声量，比乘系数稳得多
        low_scale, high_scale = 0.05, 3.0
        alpha_scale = 1.0
        # 每个自变量单独的效应量：不显著的单独加大，避免互相抵消
        beta_map = {
            number: (0.8 if self.goal.direction_of(number) != "-" else -0.8)
            for number in self.goal.predictors
        }

        for attempt in range(1, max(1, self.goal.attempts) + 1):
            sheets = self._build(count, attempt, alpha_scale, beta_map)
            if self.goal.wants_conclusion:
                sheets = self._rebalance_outcome(sheets, beta_map)
            report = self._measure(sheets, attempt)

            if best is None or self._score(report) < self._score(best[1]):
                best = (sheets, report)
            if report.achieved:
                return sheets, report

            # 按差距调整：α 二分收缩，显著不够就加效应量
            if report.alpha > 0:
                if report.alpha > self.goal.alpha_target:
                    low_scale = alpha_scale
                else:
                    high_scale = alpha_scale
                alpha_scale = (low_scale + high_scale) / 2.0
            # 谁不显著就把谁的权重调大（方向不变），逐个处理
            for item in report.predictors:
                if item.ok:
                    continue
                current = beta_map.get(item.number, 0.8)
                if item.expected == "0":
                    beta_map[item.number] = current * 0.3
                else:
                    beta_map[item.number] = current * 1.8
                if abs(beta_map[item.number]) > 6.0:
                    beta_map[item.number] = 6.0 * (1 if beta_map[item.number] > 0 else -1)

        assert best is not None
        return best

    def _need_significant(self) -> int:
        required = self.goal.significant_required
        if required > 0:
            return min(required, len(self.goal.predictors))
        return max(0, len(self.goal.predictors) - 1)

    # -- 构造 ---------------------------------------------------------------

    def _build(
        self, count: int, attempt: int, alpha_scale: float, beta_map: dict[int, float]
    ) -> list[AnswerSheet]:
        sheets: list[AnswerSheet] = []
        base = generate_sheet(self.survey, self.strategy, self.seed + attempt, 0)
        del base  # 只为触达解析/校验路径

        for unit in range(1, count + 1):
            sheets.append(self._build_one(unit, attempt, alpha_scale, beta_map))
        return sheets

    def _build_one(
        self, unit: int, attempt: int, alpha_scale: float, beta_map: dict[int, float]
    ) -> AnswerSheet:
        # 用固定种子：alpha_scale 是唯一变量，α 随噪声单调变化，二分才能收敛
        rng = derive(self.seed, "goal", unit)
        factors = self._draw_factors(rng)

        # 先要一份"所有题都填了"的完整答卷：否则单选/多选/填空全空，
        # 必答校验会把这些构造答卷全部判无效，补份机制又用随机答卷顶上，
        # 最终提交到平台的数据和本地算过的完全不是一批。
        base = generate_sheet(self.survey, self.strategy, self.seed, unit, latent=0.55)
        answers: dict[int, ItemAnswer] = dict(base.answers)
        scores: dict[int, float] = {}

        for index, group in enumerate(self.dimensions):
            k = len(group)
            error = math.sqrt(_error_ratio(k, self.goal.alpha_target) * alpha_scale)
            for number in group:
                z = factors[index] + error * rng.gauss(0.0, 1.0)
                if number >= 100:
                    item_no, row_no = divmod(number, 100)
                    item = self.survey.item(item_no)
                    picked = _scale_index(z, item.option_count or 5)
                    pairs = dict(answers[item_no].matrix) if item_no in answers else {}
                    pairs[row_no] = picked
                    answers[item_no] = ItemAnswer(
                        number=item_no,
                        picks=tuple(sorted(pairs.values())),
                        matrix=tuple(sorted(pairs.items())),
                    )
                    scores[number] = float(picked)
                    continue
                item = self.survey.item(number)
                picked = _scale_index(z, item.option_count or 5)
                if number in self.goal.reverse:
                    picked = flip_index(picked, item.option_count or 5)
                answers[number] = ItemAnswer(
                    number=number, picks=(picked,), scale=float(picked)
                )
                scores[number] = float(picked)

        sheet = AnswerSheet(seed=self.seed, unit=unit, answers=answers)
        if self.goal.wants_conclusion:
            sheet = self._apply_outcome(sheet, scores, unit, attempt, beta_map)
        return sheet

    def _draw_factors(self, rng: random.Random) -> list[float]:
        noise = [rng.gauss(0.0, 1.0) for _ in range(self.goal.dimension_count)]
        if len(self.dimensions) <= 1:
            return noise

        corr = [[0.0] * len(self.dimensions) for _ in self.dimensions]
        for i in range(len(self.dimensions)):
            for j in range(len(self.dimensions)):
                corr[i][j] = 1.0 if i == j else float(self.goal.dimension_correlation)
        try:
            lower = _cholesky(corr)
        except ValueError:
            return noise
        return [sum(lower[i][j] * noise[j] for j in range(i + 1)) for i in range(len(noise))]

    def _option_count(self, number: int) -> int:
        """行编号（题号*100+行号）也能取到对应题目的选项数。"""
        item_no = number // 100 if number >= 100 else number
        return self.survey.item(item_no).option_count or 5

    def _apply_outcome(
        self,
        sheet: AnswerSheet,
        scores: dict[int, float],
        unit: int,
        attempt: int,
        beta_map: dict[int, float],
    ) -> AnswerSheet:
        # 结局题本身是量表/矩阵行时，它已经有连续取值：
        # 一旦覆盖成「正面/非正面」两档，α 和 KMO 就会被压垮，所以保持原值。
        if self.goal.outcome_question in self._all_scorable():
            return sheet

        item = self.survey.item(self.goal.outcome_question)
        options = list(range(1, (item.option_count or 2) + 1))
        positives = [o for o in self.goal.positive_options if o in options] or [options[-1]]

        # 综合得分：按方向给权重，正负号决定相关性方向
        total = 0.0
        for number in self.goal.predictors:
            value = scores.get(number)
            if value is None:
                continue
            span = max(1.0, float(self._option_count(number)) - 1.0)
            normalized = (value - 1.0) / span * 2.0 - 1.0
            weight = beta_map.get(number, 0.8)
            if self.goal.direction_of(number) == "0":
                weight = 0.0
            total += weight * normalized

        rng = derive(self.seed, "outcome", unit)
        total += rng.gauss(0.0, 0.35)

        # 按事件占比切分：这条只决定"正面/非正面"，方向由权重符号保证
        positive = total > self._outcome_threshold()
        chosen = positives[0] if positive else next(
            (o for o in options if o not in positives), options[0]
        )
        answers = dict(sheet.answers)
        answers[item.number] = ItemAnswer(
            number=item.number, picks=(chosen,), scale=float(chosen)
        )
        return AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers)

    def _rescore(self, sheet: AnswerSheet, beta_map: dict[int, float] | None = None) -> float:
        """按自变量算综合得分（越大越偏「正面结局」）。矩阵行同样参与。"""
        numbers = self._all_scorable()
        total = 0.0
        for number in self.goal.predictors:
            value = self._value_of(sheet, number, numbers)
            if value is None:
                continue
            span = max(1.0, float(self._option_count(number)) - 1.0)
            normalized = (value - 1.0) / span * 2.0 - 1.0
            direction = self.goal.direction_of(number)
            default = 0.8 if direction != "-" else -0.8
            weight = 0.0 if direction == "0" else (beta_map or {}).get(number, default)
            total += weight * normalized
        return total

    def _rebalance_outcome(
        self, sheets: list[AnswerSheet], beta_map: dict[int, float] | None = None
    ) -> list[AnswerSheet]:
        """按得分排序决定谁拿到「正面结局」，让事件占比严格等于设定值。"""
        if self.goal.outcome_question in self._all_scorable():
            return sheets

        item = self.survey.item(self.goal.outcome_question)
        options = list(range(1, (item.option_count or 2) + 1))
        positives = [o for o in self.goal.positive_options if o in options] or [options[-1]]
        others = [o for o in options if o not in positives] or [options[0]]

        rate = min(0.9, max(0.1, float(self.goal.event_rate)))
        count = max(1, min(len(sheets), round(rate * len(sheets))))
        order = sorted(
            range(len(sheets)), key=lambda i: self._rescore(sheets[i], beta_map), reverse=True
        )
        winners = set(order[:count])

        result: list[AnswerSheet] = []
        for index, sheet in enumerate(sheets):
            chosen = positives[0] if index in winners else others[index % len(others)]
            answers = dict(sheet.answers)
            answers[item.number] = ItemAnswer(
                number=item.number, picks=(chosen,), scale=float(chosen)
            )
            result.append(AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers))
        return result

    def _outcome_threshold(self) -> float:
        rate = min(0.9, max(0.1, float(self.goal.event_rate)))
        # 标准正态分位点：让正面结局约占 rate
        return math.sqrt(2.0) * _erfinv(2.0 * (1.0 - rate) - 1.0)

    # -- 实测 ---------------------------------------------------------------

    def _measure(self, sheets: list[AnswerSheet], attempt: int) -> GoalReport:
        numbers = self._all_scorable()
        matrix: list[list[float]] = []
        for sheet in sheets:
            row: list[float] = []
            skip = False
            for number in numbers:
                value = self._value_of(sheet, number, numbers)
                if value is None:
                    skip = True
                    break
                row.append(value)
            if not skip:
                matrix.append(row)

        alphas: list[float] = []
        if matrix and len(numbers) >= 2:
            for group in self.dimensions:
                columns = [numbers.index(n) for n in group if n in numbers]
                if len(columns) < 2:
                    continue
                sub = [[row[i] for i in columns] for row in matrix]
                alphas.append(cronbach_alpha(sub))
        alpha = min(alphas) if alphas else 0.0
        kmo_value = kmo(matrix) if matrix and len(numbers) >= 3 else 0.0

        predictor_results: list[PredictorResult] = []
        significant = 0
        if self.goal.wants_conclusion:
            positives = set(self.goal.positive_options)
            outcome = []
            for sheet in sheets:
                answer = sheet.get(self.goal.outcome_question)
                picked = answer.picks[0] if answer and answer.picks else 0
                outcome.append(1.0 if picked in positives else 0.0)

            for number in self.goal.predictors:
                values = [self._value_of(sheet, number, numbers) or 1.0 for sheet in sheets]
                r = _pearson(values, outcome)
                p_value = _p_value(r, len(values))
                direction = "+" if r > 0 else ("-" if r < 0 else "0")
                result = PredictorResult(
                    number=number,
                    correlation=r,
                    p_value=p_value,
                    direction=direction,
                    expected=self.goal.direction_of(number),
                )
                predictor_results.append(result)
                if result.ok:
                    significant += 1

        notes: list[str] = []
        if len(sheets) < 20:
            notes.append(f"样本只有 {len(sheets)} 份，信度与 KMO 的估计波动很大，建议 ≥ 20 份")
        if len(numbers) < 2:
            notes.append("问卷里可参与信度分析的题目不足 2 个（量表题或矩阵行），无法构造 α")
        if len(numbers) < 3:
            notes.append("可参与效度分析的题目少于 3 个，KMO 会固定为 0.5，属统计学限制")
        if alpha and alpha < self.goal.alpha_target:
            notes.append(f"α={alpha:.2f} 低于目标 {self.goal.alpha_target:.2f}")
        if kmo_value and kmo_value < self.goal.kmo_target:
            notes.append(f"KMO={kmo_value:.2f} 低于目标 {self.goal.kmo_target:.2f}")

        achieved = bool(
            (not alphas or (self.goal.alpha_target - 0.02 <= alpha <= self.goal.alpha_target + 0.04))
            and (not self.goal.wants_conclusion or significant >= self._need_significant())
            and len(numbers) >= 2
        )

        return GoalReport(
            alpha=alpha,
            kmo=kmo_value,
            predictors=tuple(predictor_results),
            significant=significant,
            attempts=attempt,
            achieved=achieved,
            notes=notes,
        )

    def _value_of(self, sheet, number: int, numbers: list[int]) -> float | None:
        """取某份答卷在某个项目上的得分（量表题或矩阵行的某一格）。"""
        if number >= 100:
            item_no, row_no = divmod(number, 100)
            answer = sheet.get(item_no)
            if answer is None or not answer.matrix:
                return None
            table = dict(answer.matrix)
            if row_no not in table:
                return None
            value = float(table[row_no])
        else:
            answer = sheet.get(number)
            if answer is None or answer.scale is None:
                return None
            value = float(answer.scale)
        if number in self.goal.reverse:
            value = (self._option_count(number) + 1) - value
        return value

    def _matrix_numbers(self) -> list[int]:
        return [item.number for item in self.scale_items]

    @staticmethod
    def _score(report: GoalReport) -> float:
        return (
            abs(0.87 - report.alpha) * 2
            + abs(0.75 - report.kmo)
            + abs(3 - report.significant)
        )


def _erfinv(x: float) -> float:
    """erf 的反函数（近似，够用于分位数换算）。"""
    a = 0.147
    ln = math.log(max(1e-12, 1.0 - x * x))
    first = 2.0 / (math.pi * a) + ln / 2.0
    return math.copysign(
        math.sqrt(max(0.0, math.sqrt(first * first - ln / a) - first)), x
    )
