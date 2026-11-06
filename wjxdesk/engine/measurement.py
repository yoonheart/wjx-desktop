"""测量引擎：从 MeasurementModel 生成答卷，并在同一批数据上做诊断。

与旧实现的区别
--------------
旧实现里，α 由"误差方差"调、显著性由"结局覆盖"构造、KMO 由"维度间相关"调，
三条路径互不知情，任何一条改动都可能破坏另外两条。

新实现只有一个动作：**从相关矩阵生成潜变量，再按载荷投影到题目，最后按
边际分位离散化**。α、KMO、显著性都是这批数据算出来的结果，因此天然一致。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Sequence

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.goal import split_ref
from wjxdesk.domain.measurement import MeasurementModel, default_marginal
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.diagnostics import cronbach_alpha, inverse, kmo
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.engine.validity import _cholesky, project_correlation


# --------------------------------------------------------------------------- 结果


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
class MeasureResult:
    alpha: float
    alpha_by_construct: tuple[tuple[str, float], ...]
    kmo: float
    predictors: tuple[PredictorResult, ...]
    significant: int
    outcome_mean: float = 0.0
    outcome_sd: float = 0.0
    # 调节检验：(自变量, 调节变量, 交互项系数, p 值)
    interaction: tuple[int, int, float, float] | None = None
    notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- 工具


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
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
    if n < 4:
        return 1.0
    if abs(r) >= 0.999999:
        return 0.0
    t = r * math.sqrt((n - 2) / max(1e-12, 1.0 - r * r))
    return 2.0 * (1.0 - _normal_cdf(abs(t)))


def _interaction_beta(y: Sequence[float], x: Sequence[float], w: Sequence[float]) -> tuple[float, float]:
    """Y ~ x + w + x*w 的交互项系数与 p 值（最小二乘，纯标准库）。"""
    n = len(y)
    if n < 8:
        return 0.0, 1.0
    mx, mw, my = sum(x) / n, sum(w) / n, sum(y) / n
    xc = [v - mx for v in x]
    wc = [v - mw for v in w]
    yc = [v - my for v in y]
    xw = [a * b for a, b in zip(xc, wc)]
    cols = [xc, wc, xw]
    k = 3
    gram = [[sum(a * b for a, b in zip(cols[i], cols[j])) for j in range(k)] for i in range(k)]
    rhs = [sum(a * b for a, b in zip(cols[i], yc)) for i in range(k)]
    try:
        inv = inverse(gram)
    except ValueError:
        return 0.0, 1.0
    beta = [sum(inv[i][j] * rhs[j] for j in range(k)) for i in range(k)]
    resid = [yc[i] - sum(beta[j] * cols[j][i] for j in range(k)) for i in range(n)]
    dof = n - k - 1
    if dof <= 0:
        return beta[2], 1.0
    s2 = sum(r * r for r in resid) / dof
    se = math.sqrt(max(1e-12, s2 * inv[2][2]))
    t_value = beta[2] / se if se > 0 else 0.0
    return beta[2], 2.0 * (1.0 - _normal_cdf(abs(t_value)))


def _cuts(marginal: Sequence[float]) -> list[float]:
    cuts: list[float] = []
    acc = 0.0
    for value in marginal[:-1]:
        acc += float(value)
        cuts.append(min(0.999, max(0.001, acc)))
    return cuts


def _level(z: float, cuts: Sequence[float], option_count: int) -> int:
    u = min(0.9999, max(0.0001, _normal_cdf(z)))
    level = 1
    for cut in cuts:
        if u >= cut:
            level += 1
        else:
            break
    return max(1, min(option_count, level))


def _write(answers: dict[int, ItemAnswer], ref: int, level: int) -> None:
    question, row = split_ref(ref)
    value = int(level)
    if row:
        previous = answers.get(question)
        pairs = dict(previous.matrix) if previous is not None else {}
        pairs[row] = value
        answers[question] = ItemAnswer(
            number=question,
            picks=tuple(sorted(set(pairs.values()))),
            matrix=tuple(sorted(pairs.items())),
        )
    else:
        answers[question] = ItemAnswer(number=question, picks=(value,), scale=float(value))


def _read(sheet: AnswerSheet, ref: int) -> float | None:
    question, row = split_ref(ref)
    answer = sheet.get(question)
    if answer is None:
        return None
    if row:
        table = dict(answer.matrix)
        return float(table[row]) if row in table else None
    if answer.scale is not None:
        return float(answer.scale)
    return float(answer.picks[0]) if answer.picks else None


def _outcome_value(sheet: AnswerSheet, model: MeasurementModel) -> float | None:
    values = [_read(sheet, ref) for ref in model.outcome_items]
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


# --------------------------------------------------------------------------- 生成


def generate_batch(
    model: MeasurementModel,
    survey: Survey,
    strategy: Strategy,
    count: int,
    seed: int,
    *,
    binary_outcome: bool = False,
    event_rate: float = 0.5,
) -> list[AnswerSheet]:
    """按测量模型生成一批答卷。

    非测量题（单选、多选、填空…）仍交给常规抽样，保证必答与逻辑跳转成立；
    测量题（量表题、矩阵行）统一由潜变量 + 载荷 + 分位映射产出。
    """
    if not model.constructs or count <= 0:
        return []

    rng = random.Random(seed)
    matrix, _shrink = project_correlation(model.correlation_matrix())
    lower = _cholesky(matrix)
    size = model.size

    cuts_by_ref: dict[int, list[float]] = {}
    counts_by_ref: dict[int, int] = {}
    for construct in model.constructs:
        for index, ref in enumerate(construct.items):
            option_count = construct.option_count(index)
            counts_by_ref[ref] = option_count
            marginal = model.marginals.get(ref) or default_marginal(option_count)
            cuts_by_ref[ref] = _cuts(marginal)

    sheets: list[AnswerSheet] = []
    for unit in range(1, count + 1):
        base = generate_sheet(survey, strategy, seed, unit, latent=0.55)
        answers: dict[int, ItemAnswer] = dict(base.answers)

        noise = [rng.gauss(0.0, 1.0) for _ in range(size)]
        latent = [
            sum(lower[i][j] * noise[j] for j in range(i + 1)) for i in range(size)
        ]
        # 调节：在结局潜变量上叠加「自变量 × 调节变量」的乘积项
        for mod in model.moderation:
            center = model.correlation(mod.focal, mod.moderator)
            bump = mod.effect * (latent[mod.focal] * latent[mod.moderator] - center)
            latent[mod.outcome] = (latent[mod.outcome] + bump) / math.sqrt(
                1.0 + 2.0 * mod.effect * mod.effect
            )

        for ci, construct in enumerate(model.constructs):
            loading = max(0.05, min(0.95, construct.loading))
            residual = math.sqrt(max(1e-6, 1.0 - loading * loading))
            for ii, ref in enumerate(construct.items):
                z = loading * latent[ci] + residual * rng.gauss(0.0, 1.0)
                if construct.reverse or ref in model.reverse_items:
                    z = -z
                level = _level(z, cuts_by_ref[ref], counts_by_ref[ref])
                _write(answers, ref, level)

        sheet = AnswerSheet(seed=seed, unit=unit, answers=answers)
        sheets.append(sheet)

    if binary_outcome and model.outcome_index is None and model.outcome_items:
        sheets = _apply_binary_outcome(sheets, model, survey, event_rate)
    return sheets


def _apply_binary_outcome(
    sheets: list[AnswerSheet],
    model: MeasurementModel,
    survey: Survey,
    event_rate: float,
) -> list[AnswerSheet]:
    """二分类结局（单选题）：按自变量的综合得分排序，前 rate 比例拿「正面」选项。

    只在结局项不是量表类题目时才走这里；量表/矩阵行的结局是连续分。
    """
    question, _ = split_ref(model.outcome_items[0])
    try:
        item = survey.item(question)
    except KeyError:
        return sheets
    options = list(range(1, max(2, item.option_count) + 1))
    positives = [o for o in model.positive_options if o in options] or [options[-1]]
    others = [o for o in options if o not in positives] or [options[0]]

    scores: list[float] = []
    for sheet in sheets:
        total = 0.0
        for ref in model.predictors:
            value = _read(sheet, ref)
            if value is None:
                continue
            owner, _row = split_ref(ref)
            try:
                count = max(2, survey.item(owner).option_count)
            except KeyError:
                count = 5
            normalized = (value - 1.0) / max(1.0, count - 1.0) * 2.0 - 1.0
            direction = str(model.directions.get(ref, "+"))
            weight = 0.0 if direction == "0" else (1.0 if direction == "+" else -1.0)
            total += weight * normalized
        scores.append(total)

    rate = min(0.9, max(0.1, float(event_rate)))
    winners_n = max(1, min(len(sheets), round(rate * len(sheets))))
    order = sorted(range(len(sheets)), key=lambda i: scores[i], reverse=True)
    winners = set(order[:winners_n])

    result: list[AnswerSheet] = []
    for index, sheet in enumerate(sheets):
        chosen = positives[0] if index in winners else others[index % len(others)]
        answers = dict(sheet.answers)
        answers[question] = ItemAnswer(
            number=question, picks=(chosen,), scale=float(chosen)
        )
        result.append(AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers))
    return result


# --------------------------------------------------------------------------- 诊断


def diagnose(
    model: MeasurementModel,
    sheets: Sequence[AnswerSheet],
) -> MeasureResult:
    """在同一批答卷上实测 α / KMO / 显著性。"""
    if not sheets or not model.constructs:
        return MeasureResult(0.0, (), 0.0, (), 0)

    columns: dict[int, list[float]] = {}
    for ref in model.all_items():
        values: list[float] = []
        reversed_item = ref in model.reverse_items
        question, _row = split_ref(ref) if reversed_item else (0, 0)
        bound = 6
        if reversed_item:
            try:
                bound = max(2, model.constructs[model.item_owner()[ref]].option_count(
                    list(model.constructs[model.item_owner()[ref]].items).index(ref)
                )) + 1
            except (KeyError, IndexError, ValueError):
                bound = 6
        for sheet in sheets:
            value = _read(sheet, ref)
            if value is None:
                values.append(0.0)
                continue
            # 反向题按「反向计分后」的分数进入统计，这样 α/KMO 与 SPSS 里翻转后一致
            values.append(float(bound - value) if reversed_item else float(value))
        columns[ref] = values

    alphas: list[tuple[str, float]] = []
    for construct in model.constructs:
        block = [columns[ref] for ref in construct.items if ref in columns]
        if len(block) < 2:
            continue
        rows = [[block[j][i] for j in range(len(block))] for i in range(len(sheets))]
        alphas.append((construct.name, round(cronbach_alpha(rows), 4)))

    all_refs = [ref for construct in model.constructs for ref in construct.items]
    matrix = [[columns[ref][i] for ref in all_refs] for i in range(len(sheets))]
    kmo_value = kmo(matrix) if len(all_refs) >= 3 else 0.0

    outcome_values = [_outcome_value(sheet, model) for sheet in sheets]
    outcome = [v for v in outcome_values if v is not None]

    results: list[PredictorResult] = []
    if outcome:
        for ref in model.predictors:
            series = columns.get(ref)
            if not series:
                continue
            paired = [
                (series[i], outcome_values[i])
                for i in range(len(sheets))
                if outcome_values[i] is not None
            ]
            xs = [p[0] for p in paired]
            ys = [float(p[1]) for p in paired]
            r = _pearson(xs, ys)
            p = _p_value(r, len(xs))
            results.append(
                PredictorResult(
                    number=ref,
                    correlation=r,
                    p_value=p,
                    direction="+" if r > 0 else ("-" if r < 0 else "0"),
                    expected=str(model.directions.get(ref, "+")),
                )
            )

    alphas_sorted = sorted(alphas, key=lambda item: item[1])
    min_alpha = alphas_sorted[0][1] if alphas_sorted else 0.0
    notes: list[str] = []
    if len(all_refs) < 3:
        notes.append("可参与测量的题项少于 3 个，KMO 固定为 0.5，属统计学限制")

    interaction: tuple[int, int, float, float] | None = None
    if model.moderation and outcome:
        mod = model.moderation[0]
        try:
            focal_items = [r for r in model.constructs[mod.focal].items if r in columns]
            moderator_items = [r for r in model.constructs[mod.moderator].items if r in columns]
        except IndexError:
            focal_items, moderator_items = [], []
        if focal_items and moderator_items and len(outcome) == len(sheets):
            def _score(items: Sequence[int]) -> list[float]:
                return [
                    sum(columns[ref][i] for ref in items) / len(items)
                    for i in range(len(sheets))
                ]
            beta, p_value = _interaction_beta(outcome, _score(focal_items), _score(moderator_items))
            interaction = (focal_items[0], moderator_items[0], beta, p_value)

    mean_outcome = sum(outcome) / len(outcome) if outcome else 0.0
    sd = 0.0
    if len(outcome) > 1:
        sd = math.sqrt(sum((v - mean_outcome) ** 2 for v in outcome) / (len(outcome) - 1))

    return MeasureResult(
        alpha=min_alpha,
        alpha_by_construct=tuple(alphas),
        kmo=kmo_value,
        predictors=tuple(results),
        significant=sum(1 for r in results if r.ok),
        outcome_mean=mean_outcome,
        outcome_sd=sd,
        interaction=interaction,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- 调参


def adjust(
    model: MeasurementModel,
    result: MeasureResult,
    goal_alpha: float,
    effects: dict[tuple[int, int], float],
) -> MeasurementModel:
    """按实测偏差回调"原因"，而不是直接改结果。

    - α 偏低 → 抬高载荷；偏高 → 压低
    - 某条边的相关不够 → 放大对应构念对的相关（保留符号）
    """
    constructs = list(model.constructs)
    if result.alpha and result.alpha_by_construct:
        for index, construct in enumerate(constructs):
            measured = dict(result.alpha_by_construct).get(construct.name)
            if measured is None:
                continue
            gap = goal_alpha - measured
            step = 1.0 + max(-0.12, min(0.12, gap * 0.8))
            constructs[index] = replace(
                construct, loading=max(0.30, min(0.95, construct.loading * step))
            )

    correlations = dict(model.correlations)
    for (i, j), target in effects.items():
        measured = 0.0
        for item in result.predictors:
            owner = model.item_owner().get(item.number)
            if owner is None:
                continue
            if {owner, model.outcome_index} == {i, j}:
                measured = item.correlation
                break
        if abs(measured) < 0.02:
            continue
        ratio = target / measured
        ratio = max(0.85, min(1.15, ratio))
        key = (min(i, j), max(i, j))
        base = correlations.get(key, model.default_correlation)
        correlations[key] = max(-0.95, min(0.95, base * ratio))

    moderation = list(model.moderation)
    if moderation and result.interaction is not None and result.interaction[3] >= 0.05:
        current = moderation[0]
        moderation[0] = replace(
            current, effect=min(0.9, max(0.05, current.effect * 1.25))
        )

    return replace(
        model,
        constructs=tuple(constructs),
        correlations=correlations,
        moderation=tuple(moderation),
    )
