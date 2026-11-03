"""答案生成。

职责边界：只吃 Survey + Strategy，只吐 AnswerSheet。不联网、不读文件、不碰界面。

三件事：
1. 分布形状（uniform / 左偏 / 右偏 / 居中 / 双峰）→ 选项权重；
2. 潜变量（latent）→ 让同一份答卷在量表类题目上表现出一致的倾向，
   这是信度（α）能被控制的前提；
3. 题型分派 → 每种题型各自的取值策略。
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import ItemKind, SkewShape
from wjxdesk.domain.strategy import ItemRule, SkewRule, Strategy, TextFallback
from wjxdesk.domain.survey import Item, Survey
from wjxdesk.engine.rng import derive
from wjxdesk.engine.unique_pool import UniquePool


# --------------------------------------------------------------------------- 权重


def shape_weights(count: int, skew: SkewRule) -> list[float]:
    """按形状生成相对权重。strength 为 0 时全部为 1。"""
    if count <= 0:
        return []
    if count == 1:
        return [1.0]
    strength = max(0.0, min(5.0, skew.strength))
    if skew.shape is SkewShape.UNIFORM or strength == 0.0:
        return [1.0] * count

    weights: list[float] = []
    last = count - 1
    for i in range(count):
        pos = i / last  # 0 → 1
        if skew.shape is SkewShape.RIGHT:
            w = math.exp(-strength * pos)
        elif skew.shape is SkewShape.LEFT:
            w = math.exp(strength * pos)
        elif skew.shape is SkewShape.CENTER:
            w = math.exp(-strength * (2.0 * pos - 1.0) ** 2)
        else:  # BIMODAL
            w = math.exp(-strength * (1.0 - abs(2.0 * pos - 1.0)))
        weights.append(w)
    return weights


def normalize(weights: list[float]) -> list[float]:
    total = sum(weights)
    if total <= 0:
        return [1.0 / len(weights)] * len(weights) if weights else []
    return [w / total for w in weights]


def base_probabilities(item: Item, rule: ItemRule) -> list[float]:
    """一题的基准分布：显式权重优先，其次形状，最后均匀。"""
    n = item.option_count
    if n <= 0:
        return []
    explicit = rule.weight_list()
    if explicit:
        if len(explicit) != n:
            explicit = (explicit + [1.0] * n)[:n]
        return normalize(explicit)
    return normalize(shape_weights(n, rule.skew))


def pick_weighted(weights: list[float], rng: random.Random) -> int:
    """按权重抽取 0 基索引。"""
    if not weights:
        return 0
    point = rng.random()
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += weight
        if point <= cumulative:
            return index
    return len(weights) - 1


# --------------------------------------------------------------------------- 潜变量


def latent_value(rng: random.Random) -> float:
    """标准正态潜变量。"""
    return rng.gauss(0.0, 1.0)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def position_from_latent(z: float, latent: float, rng: random.Random) -> float:
    """把潜变量与噪声合成一个 [0,1] 的位置。

    latent=0 → 纯噪声（各份之间不相关，α 低）
    latent=1 → 完全由 z 决定（各份高度一致，α 高）
    """
    if latent <= 0.0:
        return rng.random()
    noise = rng.gauss(0.0, 1.0) * math.sqrt(max(0.0, 1.0 - latent * latent))
    return min(0.999999, max(0.000001, _normal_cdf(z * latent + noise)))


# --------------------------------------------------------------------------- 题型


def _option_text(
    item: Item,
    rule: ItemRule,
    rng: random.Random,
    fallback: TextFallback,
    option_index: int,
    pools: UniquePool | None,
    unit: int,
) -> str:
    """选项内填空（如「传奇【　】」）：开了唯一就从池里领，否则随机。"""
    group = getattr(rule, "unique_group", "")
    if group and pools is not None:
        value, hit = pools.take(f"{group}#o{option_index}", derive(unit, item.number, option_index), "")
        if hit:
            return value
    return _short_text(rule, fallback, rng)


def pick_single(
    item: Item,
    rule: ItemRule,
    rng: random.Random,
    pools: UniquePool | None = None,
    unit: int = 0,
) -> ItemAnswer:
    index = pick_weighted(base_probabilities(item, rule), rng) + 1
    free = ""
    option = item.options[index - 1] if item.options else None
    if option is not None and option.requires_text:
        free = _option_text(item, rule, rng, TextFallback(), index + 1, pools, unit)
    return ItemAnswer(number=item.number, picks=(index,), free_text=free)


def pick_multi(
    item: Item,
    rule: ItemRule,
    rng: random.Random,
    fallback: TextFallback,
    pools: UniquePool | None = None,
    unit: int = 0,
) -> ItemAnswer:
    """逐项伯努利 + 数量区间约束。

    期望命中数由区间中点决定，避免「全选」或「只选一个」的极端答案。
    """
    probs = base_probabilities(item, rule)
    low, high = item.choice_bounds()
    target = (low + high) / 2.0
    # probs 已归一化（和为 1），乘 target 后期望命中数正好等于 target
    picks = [
        i + 1
        for i, p in enumerate(probs)
        if rng.random() < min(0.9, p * target)
    ]
    if not picks:
        picks = [pick_weighted(probs, rng) + 1]
    while len(picks) < low:
        candidate = pick_weighted(probs, rng) + 1
        if candidate not in picks:
            picks.append(candidate)
    if len(picks) > high:
        rng.shuffle(picks)
        picks = sorted(picks[:high])
    free = ""
    option = item.options[picks[-1] - 1] if item.options and picks[-1] <= item.option_count else None
    if option is not None and option.requires_text:
        free = _option_text(item, rule, rng, fallback, picks[-1], pools, unit)
    return ItemAnswer(number=item.number, picks=tuple(sorted(picks)), free_text=free)


def pick_scale(
    item: Item,
    rule: ItemRule,
    rng: random.Random,
    z: float,
    latent: float,
    *,
    reverse: bool = False,
) -> ItemAnswer:
    """量表/评分：界面上填了概率就按概率走，否则由潜变量 + 形状决定。"""
    n = item.option_count or int((item.high or 5) - (item.low or 1) + 1)
    if n <= 0:
        return ItemAnswer(number=item.number, scale=float(item.low or 1))

    explicit = rule.weight_list()
    if explicit and sum(explicit) > 0:
        index = pick_weighted(normalize(explicit[:n]), rng)
        base = float(item.low) if item.low is not None else 1.0
        return ItemAnswer(number=item.number, picks=(index + 1,), scale=base + index)

    position = position_from_latent(z, latent, rng)

    # 形状倾斜：把位置向所选方向再推一点
    strength = rule.skew.strength / 5.0
    if rule.skew.shape is SkewShape.RIGHT:
        position = position ** (1.0 + 2.0 * strength)
    elif rule.skew.shape is SkewShape.LEFT:
        position = 1.0 - (1.0 - position) ** (1.0 + 2.0 * strength)

    index = min(n - 1, max(0, int(position * n)))
    if reverse:
        index = n - 1 - index
    base = float(item.low) if item.low is not None else 1.0
    return ItemAnswer(number=item.number, picks=(index + 1,), scale=base + index)


def pick_matrix(
    item: Item,
    rule: ItemRule,
    rng: random.Random,
    z: float,
    latent: float,
    *,
    reverse: bool = False,
) -> ItemAnswer:
    """矩阵题：每行独立取值。

    界面上给了每行的列概率就按概率走；否则由潜变量 + 行内噪声决定。
    无论哪种，都要主动避免「整列直线作答」——那是数据清洗的头号剔除对象。
    """
    n = item.option_count or 5
    rows = item.row_count or 1
    pairs: list[tuple[int, int]] = []

    for row in range(rows):
        row_weights = rule.row_weights(row + 1, n)
        if row_weights is not None:
            column = pick_weighted(normalize(row_weights), rng) + 1
            pairs.append((row + 1, column))
            continue
        row_noise = rng.gauss(0.0, 1.0)
        position = position_from_latent(z + row_noise * 0.35, latent, rng)
        column = min(n - 1, max(0, int(position * n))) + 1
        if reverse:
            column = n + 1 - column
        pairs.append((row + 1, column))

    if rows >= 3:
        columns = {col for _, col in pairs}
        if len(columns) == 1:
            # 全部同列 = 直线作答，强制打散一行
            target_row = rng.randrange(rows)
            row, col = pairs[target_row]
            alternatives = [c for c in range(1, n + 1) if c != col]
            if alternatives:
                pairs[target_row] = (row, rng.choice(alternatives))
    return ItemAnswer(number=item.number, matrix=tuple(pairs))


def pick_numeric(item: Item, rule: ItemRule, rng: random.Random) -> ItemAnswer:
    low = item.low if item.low is not None else 1.0
    high = item.high if item.high is not None else 5.0
    if rule.value_range is not None:
        low, high = rule.value_range
    if high < low:
        low, high = high, low
    value = rng.uniform(low, high)
    text = str(int(round(value))) if float(value).is_integer() else f"{value:.1f}"
    return ItemAnswer(number=item.number, texts=(text,))


def pick_text(
    item: Item,
    rule: ItemRule,
    fallback: TextFallback,
    rng: random.Random,
    pools: UniquePool | None,
    unit: int,
) -> ItemAnswer:
    """填空/开放题：优先唯一池，其次规则文本，最后兜底文本。"""
    blanks = max(1, item.effective_blanks)
    values: list[str] = []
    pool = rule.texts or fallback.pick_pool()
    weights = normalize([float(w) for w in rule.text_weights]) if rule.text_weights else None

    for blank in range(blanks):
        group = rule.unique_group or (f"q{item.number}" if len(set(pool)) < 2 else "")
        if group and pools is not None:
            value, hit = pools.take(group, derive(unit, item.number, blank), "")
            if hit:
                values.append(value)
                continue
        index = pick_weighted(weights, rng) if weights else rng.randrange(len(pool))
        values.append(pool[index % len(pool)])

    return ItemAnswer(number=item.number, texts=tuple(values))


def _short_text(rule: ItemRule, fallback: TextFallback, rng: random.Random) -> str:
    pool = rule.texts or fallback.pick_pool()
    return pool[rng.randrange(len(pool))] if pool else ""


# --------------------------------------------------------------------------- 入口


def generate_sheet(
    survey: Survey,
    strategy: Strategy,
    seed: int,
    unit: int,
    *,
    latent: float = 0.55,
    pools: UniquePool | None = None,
    factors: Mapping[int, float] | None = None,
    reverse: frozenset[int] = frozenset(),
) -> AnswerSheet:
    """生成一份答卷。

    latent 控制「同一份答卷内部的一致性」：0 表示各题独立，1 表示完全一致。
    factors 是「题号 → 该题所属维度的因子值」，用于让不同维度各自成团；
    reverse 里的题在生成后按 k+1-r 翻转（反向计分）。
    """
    answers: dict[int, ItemAnswer] = {}
    z = latent_value(derive(seed, "latent", unit))

    for item in survey.items:
        if not item.is_visible(answers):
            continue
        rng = derive(seed, unit, item.number)
        rule = strategy.rule_for(item.number)
        z_item = factors.get(item.number, z) if factors else z
        flip = item.number in reverse

        if item.kind is ItemKind.SINGLE:
            answers[item.number] = pick_single(item, rule, rng, pools, unit)
        elif item.kind is ItemKind.MULTI:
            answers[item.number] = pick_multi(item, rule, rng, strategy.texts, pools, unit)
        elif item.kind is ItemKind.SCALE:
            answers[item.number] = pick_scale(item, rule, rng, z_item, latent, reverse=flip)
        elif item.kind is ItemKind.MATRIX:
            answers[item.number] = pick_matrix(item, rule, rng, z_item, latent, reverse=flip)
        elif item.kind is ItemKind.SLIDER:
            answers[item.number] = pick_numeric(item, rule, rng)
        elif item.kind is ItemKind.DROPDOWN:
            answers[item.number] = pick_single(item, rule, rng, pools, unit)
        elif item.kind is ItemKind.RANK:
            answers[item.number] = _pick_rank(item, rng)
        elif item.kind is ItemKind.BLANK:
            answers[item.number] = pick_text(
                item, rule, strategy.texts, rng, pools, unit
            )
        else:
            continue

    return AnswerSheet(seed=seed, unit=unit, answers=answers)


def _pick_rank(item: Item, rng: random.Random) -> ItemAnswer:
    order = list(range(1, item.option_count + 1))
    rng.shuffle(order)
    return ItemAnswer(number=item.number, picks=tuple(order))
