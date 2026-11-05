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
from wjxdesk.domain.goal import AGGREGATE_MEAN, ResearchGoal, split_ref
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
    """由目标 α 反推「误差方差 / 因子方差」的比值。

    设单题信度 ρ = 1/(1+e²)，则 α = kρ / (1 + (k-1)ρ)，反解得
    ρ = α / (k - (k-1)α)，于是

        e² = 1/ρ - 1 = k(1/α - 1)

    注意 e² 与题目数 k 成正比：k 越大，同样的 e² 会得到更高的 α，
    所以不能写成与 k 无关的常数（早期版本少乘了 k，导致题目多的维度
    实际 α 偏高、与目标不符）。
    """
    alpha = max(0.30, min(0.98, alpha))
    if k <= 1:
        return 0.5
    return max(0.02, k * (1.0 / alpha - 1.0))


def _scale_index(z: float, count: int) -> int:
    """把标准正态值映射到 1..count 的选项下标（保留正态形状）。"""
    p = min(0.999, max(0.001, _normal_cdf(z)))
    return int(p * count) + 1


def _option_from_fraction(fraction: float, count: int) -> int:
    """把 0~1 的分位映射到 1..count 的档位（保序重排用）。"""
    n = max(2, int(count))
    return min(n, max(1, int(fraction * n) + 1))


def _merge_matrix(answer: ItemAnswer | None, number: int, row: int, value: int) -> ItemAnswer:
    """把一格的取值合并进矩阵题的答案，保持 `matrix` 与 `picks` 同步。"""
    pairs = dict(answer.matrix) if answer is not None else {}
    pairs[row] = int(value)
    return ItemAnswer(
        number=number,
        picks=tuple(sorted(set(pairs.values()))),
        matrix=tuple(sorted(pairs.items())),
    )


# --------------------------------------------------------------------------- 求解器


class GoalSolver:
    def __init__(self, survey: Survey, strategy: Strategy) -> None:
        self.survey = survey
        self.strategy = strategy
        self.goal: ResearchGoal = strategy.goal
        # 可构造的项目：量表题本身，以及矩阵题的每一行（行号记作 题号*100+行号）
        self.scale_items = [i for i in survey.items if i.kind is ItemKind.SCALE]
        self.matrix_items = [i for i in survey.items if i.kind is ItemKind.MATRIX]
        # 结局：一个或多个项目；连续（量表/矩阵行）还是二分类（单选）由题型决定
        self.outcome = self.goal.outcome
        self.outcome_refs = self._normalize_outcome_refs(self.outcome.items)
        self.outcome_numbers = set(self.outcome_refs)
        self.outcome_mixed = len(self.outcome_refs) < len(self.outcome.items)
        self.outcome_continuous = self._outcome_is_continuous()
        self.outcome_positives = self._resolve_positives()
        self.dimensions = self._resolve_dimensions()
        self.seed = strategy.resolved_seed()

    # -- 结构 ---------------------------------------------------------------

    def _normalize_outcome_refs(self, refs: tuple[int, ...]) -> tuple[int, ...]:
        """混着连续项（量表/矩阵行）与二分类项（单选）时，只保留连续项。

        正常配置不会出现（界面一次只选一项或同一矩阵的几行），这里防手写配置
        json 把两类混在一起——那种情况下二分类项没法参与合成。
        """
        if len(refs) <= 1:
            return refs
        scorable = tuple(r for r in refs if self._ref_scorable(r))
        return scorable if 0 < len(scorable) < len(refs) else refs

    def _outcome_is_continuous(self) -> bool:
        """结局项全是量表/矩阵行时按连续取值构造，否则按二分类切分。"""
        if not self.outcome_refs:
            return False
        return all(self._ref_scorable(ref) for ref in self.outcome_refs)

    def _ref_scorable(self, ref: int) -> bool:
        question, row = split_ref(ref)
        try:
            item = self.survey.item(question)
        except KeyError:
            return False
        if row:
            return item.kind is ItemKind.MATRIX and 1 <= row <= item.row_count
        return item.kind is ItemKind.SCALE or self._is_likert(item)

    def _resolve_dimensions(self) -> tuple[tuple[int, ...], ...]:
        # 结局项不进信度/效度矩阵：因变量不能算进自变量的 α，这是方法学要求
        analysis = set(self._analysis_numbers())
        configured = [
            [n for n in group if n in analysis] for group in self.goal.dimensions
        ]
        configured = [g for g in configured if g]
        if configured:
            # 没被任何维度覆盖的可评分项挂到最后一组，避免它们退化成纯噪声，
            # 把 KMO 拖下去（界面上只会传一组，这里主要防手写配置漏项）
            covered = {n for group in configured for n in group}
            leftovers = [n for n in self._analysis_numbers() if n not in covered]
            if leftovers:
                configured[-1].extend(leftovers)
            return tuple(tuple(g) for g in configured)

        numbers = self._analysis_numbers()
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

    def _analysis_numbers(self) -> list[int]:
        """参与 α / KMO 的项目：可评分项里去掉结局项。"""
        return [n for n in self._all_scorable() if n not in self.outcome_numbers]

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
            # 谁不显著就把谁的权重调大（方向不变），逐个处理。
            # 幅度取 1.4 而不是 1.8：调太猛会让另一个自变量掉出显著区，
            # 两个方向交替震荡，永远收敛不到「都显著」。
            failed = [p for p in report.predictors if not p.ok]
            if len(failed) < len(report.predictors):
                for item in report.predictors:
                    if item.ok and item.expected != "0":
                        beta_map[item.number] = beta_map.get(item.number, 0.8) * 0.95
            for item in failed:
                current = beta_map.get(item.number, 0.8)
                if item.expected == "0":
                    beta_map[item.number] = current * 0.3
                else:
                    beta_map[item.number] = current * 1.4
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
        # 结局必须在整批范围内构造：二分类要按占比切分，连续结局要按排名映射
        if self.goal.wants_conclusion:
            sheets = self._apply_outcome(sheets, beta_map)
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

        for index, group in enumerate(self.dimensions):
            k = len(group)
            error = math.sqrt(_error_ratio(k, self.goal.alpha_target) * alpha_scale)
            for number in group:
                z = factors[index] + error * rng.gauss(0.0, 1.0)
                if number >= 100:
                    item_no, row_no = divmod(number, 100)
                    item = self.survey.item(item_no)
                    picked = _scale_index(z, item.option_count or 5)
                    answers[item_no] = _merge_matrix(answers.get(item_no), item_no, row_no, picked)
                    continue
                item = self.survey.item(number)
                picked = _scale_index(z, item.option_count or 5)
                if number in self.goal.reverse:
                    picked = flip_index(picked, item.option_count or 5)
                answers[number] = ItemAnswer(
                    number=number, picks=(picked,), scale=float(picked)
                )

        return AnswerSheet(seed=self.seed, unit=unit, answers=answers)

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
        self, sheets: list[AnswerSheet], beta_map: dict[int, float]
    ) -> list[AnswerSheet]:
        """结局构造的统一入口：连续结局走保序重排，二分类走占比切分。"""
        if not self.outcome_refs or not sheets:
            return sheets
        if self.outcome_continuous:
            return self._apply_continuous(sheets, beta_map)
        return self._apply_binary(sheets, beta_map)

    def _apply_binary(
        self, sheets: list[AnswerSheet], beta_map: dict[int, float]
    ) -> list[AnswerSheet]:
        """单选结局：按综合得分排序，前 rate 比例拿「正面」选项，占比严格可控。"""
        question, _ = split_ref(self.outcome_refs[0])
        try:
            item = self.survey.item(question)
        except KeyError:
            return sheets
        options = list(range(1, (item.option_count or 2) + 1))
        positives = list(self.outcome_positives) or [options[-1]]
        others = [o for o in options if o not in positives] or [options[0]]

        rate = min(0.9, max(0.1, float(self.goal.event_rate)))
        winners_n = max(1, min(len(sheets), round(rate * len(sheets))))
        order = sorted(
            range(len(sheets)), key=lambda i: self._rescore(sheets[i], beta_map), reverse=True
        )
        winners = set(order[:winners_n])

        result: list[AnswerSheet] = []
        for index, sheet in enumerate(sheets):
            chosen = positives[0] if index in winners else others[index % len(others)]
            result.append(self._set_value(sheet, question, 0, chosen))
        return result

    def _apply_continuous(
        self, sheets: list[AnswerSheet], beta_map: dict[int, float]
    ) -> list[AnswerSheet]:
        """量表 / 矩阵行结局：按综合得分排名映射回 1..n 档位（保序重排）。

        直接覆盖成 0/1 会把 Likert 压成两档，α 与 KMO 一起崩；保序重排只重排、
        不改变分布形状，结局仍是连续量表，方向由排名决定。每个项目加一点抖动，
        避免矩阵的多行完全一样（那样会被当成直线作答）。
        """
        scores = [self._rescore(sheet, beta_map) for sheet in sheets]
        order = sorted(range(len(sheets)), key=lambda i: scores[i])
        total = max(1, len(order))
        # 单项结局抖动要小（保住分位分布）；多项合成时抖动要大一点，
        # 否则同一人的几行完全一样，会被当成直线作答剔除
        spread = 0.18 if len(self.outcome_refs) > 1 else 0.06
        result = list(sheets)
        for rank, index in enumerate(order):
            base = (rank + 0.5) / total
            for ref in self.outcome_refs:
                jitter = derive(self.seed, "outcome", result[index].unit, ref).gauss(0.0, spread)
                frac = min(0.999, max(0.001, base + jitter))
                level = _option_from_fraction(frac, self._option_count(ref))
                question, row = split_ref(ref)
                result[index] = self._set_value(result[index], question, row, level)
        return result

    def _set_value(
        self, sheet: AnswerSheet, question: int, row: int, value: int
    ) -> AnswerSheet:
        """把一个取值写进答卷：row=0 写整题，否则合并进矩阵的对应行。"""
        answers = dict(sheet.answers)
        if row:
            answers[question] = _merge_matrix(answers.get(question), question, row, value)
        else:
            answers[question] = ItemAnswer(
                number=question, picks=(int(value),), scale=float(value)
            )
        return AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers)

    def _outcome_value(self, sheet: AnswerSheet, numbers: list[int]) -> float | None:
        """取一份答卷的结局值：连续取（多项目）均值，二分类取 0/1。"""
        if self.outcome_continuous:
            values = [self._value_of(sheet, ref, numbers) for ref in self.outcome_refs]
            usable = [v for v in values if v is not None]
            if not usable:
                return None
            return sum(usable) / len(usable)
        question, _ = split_ref(self.outcome_refs[0])
        answer = sheet.get(question)
        picked = answer.picks[0] if answer and answer.picks else 0
        return 1.0 if picked in set(self.outcome_positives) else 0.0

    def _resolve_positives(self) -> tuple[int, ...]:
        """二分类结局的「正面」选项；没配就退回最后一个（与旧行为一致）。"""
        if self.outcome_continuous or not self.outcome_refs:
            return ()
        try:
            item = self.survey.item(split_ref(self.outcome_refs[0])[0])
        except KeyError:
            return ()
        options = list(range(1, (item.option_count or 2) + 1))
        picked = [o for o in self.outcome.positive if o in options]
        return tuple(picked or [options[-1]])

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

    # -- 实测 ---------------------------------------------------------------

    def _measure(self, sheets: list[AnswerSheet], attempt: int) -> GoalReport:
        # 信度/效度只算自变量侧项目：因变量混进去会把 α 抬高，方法上不成立
        numbers = self._analysis_numbers()
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
            outcome = [self._outcome_value(sheet, numbers) or 0.0 for sheet in sheets]

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
        if self.outcome_mixed:
            notes.append("结局项里混有连续项与二分类项，已只用连续项参与构造")

        need = self._need_significant()
        if self.goal.wants_conclusion and significant < need and len(sheets) < 80:
            notes.append(
                f"{len(sheets)} 份只让 {significant}/{len(self.goal.predictors)} 项显著；"
                f"要让 {need} 项同时显著，建议份数 ≥ 80"
                "（反向自变量之间会互相抵消，小样本下统计功效不足）"
            )

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
