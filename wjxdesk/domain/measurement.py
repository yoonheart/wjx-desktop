"""测量模型：统计侧的**唯一真值源**。

为什么需要这一层
----------------
以前 α、KMO、显著性各由一套机制单独去凑，三者互不知情：
抬高维度间相关能让 KMO 好看，却会把因子并成一个；把结局题排除出 α 能让
信度漂亮，却让它在因子分析里显得"不属于这个构念"。这就是"数据打架"。

现在只保留一组**原因**：

- 构念（哪些题测同一个东西、载荷多大、有没有反向题）
- 构念间相关（带符号，正负都能表达）
- 每题边际分布（各选项占比）

α、KMO、显著性全部由这三样**算出来**，不再作为输入参数。
两个不同指标因此不可能互相拆台——它们读的是同一份数据。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.goal import AGGREGATE_MEAN, ResearchGoal, split_ref
from wjxdesk.domain.survey import Item, Survey

ROW_STRIDE = 100
DEFAULT_LOADING = 0.75
DEFAULT_CORRELATION = 0.30
DEFAULT_EFFECT = 0.45


# --------------------------------------------------------------------------- 构念


@dataclass(frozen=True, slots=True)
class Construct:
    """一个潜变量：由若干题项共同测量。"""

    name: str
    items: tuple[int, ...]
    loading: float = DEFAULT_LOADING
    reverse: bool = False
    is_outcome: bool = False
    option_counts: tuple[int, ...] = ()

    def option_count(self, index: int) -> int:
        if 0 <= index < len(self.option_counts):
            value = int(self.option_counts[index] or 0)
            if value >= 2:
                return value
        return 5


@dataclass(frozen=True, slots=True)
class Moderation:
    """一条调节路径：结局 = …… + effect × (自变量 × 调节变量)。"""

    focal: int
    moderator: int
    outcome: int
    effect: float = 0.35


@dataclass(frozen=True, slots=True)
class MeasurementModel:
    """一次生成所需的全部输入。生成层只读它，不再读界面字段。"""

    constructs: tuple[Construct, ...]
    # 构念索引对 -> 相关（带符号）；缺省用 default_correlation
    correlations: Mapping[tuple[int, int], float] = field(default_factory=dict)
    default_correlation: float = DEFAULT_CORRELATION
    # 因变量：属于哪个构念、用哪些题合成
    outcome_index: int | None = None
    outcome_items: tuple[int, ...] = ()
    outcome_aggregate: str = "single"
    # 项目编号 -> 各选项目标占比（可选）
    marginals: Mapping[int, tuple[float, ...]] = field(default_factory=dict)
    # 需要构造方向的自变量项目，以及各自的期望方向
    predictors: tuple[int, ...] = ()
    directions: Mapping[int, str] = field(default_factory=dict)
    # 反向计分题：这些题的选项方向与量纲相反（如"不满意→满意"）
    reverse_items: frozenset[int] = frozenset()
    # 调节路径（可为空）
    moderation: tuple[Moderation, ...] = ()
    # 只对二分类结局有效
    positive_options: tuple[int, ...] = ()
    event_rate: float = 0.5
    notes: tuple[str, ...] = ()

    # -- 结构 ---------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self.constructs)

    def item_owner(self) -> dict[int, int]:
        owner: dict[int, int] = {}
        for index, construct in enumerate(self.constructs):
            for ref in construct.items:
                owner[ref] = index
        return owner

    def all_items(self) -> tuple[int, ...]:
        out: list[int] = []
        for construct in self.constructs:
            out.extend(construct.items)
        return tuple(out)

    def correlation(self, i: int, j: int) -> float:
        if i == j:
            return 1.0
        key = (i, j) if i < j else (j, i)
        return float(self.correlations.get(key, self.default_correlation))

    def correlation_matrix(self) -> list[list[float]]:
        n = self.size
        matrix = [[1.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                value = max(-0.98, min(0.98, self.correlation(i, j)))
                matrix[i][j] = value
                matrix[j][i] = value
        return matrix

    def outcome_construct(self) -> Construct | None:
        if self.outcome_index is None:
            return None
        if 0 <= self.outcome_index < self.size:
            return self.constructs[self.outcome_index]
        return None

    def describe(self) -> str:
        outcome = self.outcome_construct()
        parts = [f"{self.size} 个构念"]
        if outcome is not None:
            parts.append(f"因变量={outcome.name}")
        return " · ".join(parts)


# --------------------------------------------------------------------------- 边际


def default_marginal(option_count: int) -> tuple[float, ...]:
    """Likert 常见形状：中间高、两端低（相对温和，不做成尖峰）。"""
    n = max(2, int(option_count))
    if n == 2:
        return (0.5, 0.5)
    center = (n - 1) / 2.0
    raw = [1.0 - 0.55 * abs(i - center) / max(center, 1.0) for i in range(n)]
    total = sum(raw)
    return tuple(round(value / total, 6) for value in raw)


def normalize_marginal(values: Any, option_count: int) -> tuple[float, ...] | None:
    """把用户给的选项占比整理成和为 1 的元组；不合法返回 None。"""
    if not isinstance(values, (list, tuple)) or len(values) != option_count:
        return None
    try:
        numbers = [max(0.0, float(v)) for v in values]
    except (TypeError, ValueError):
        return None
    total = sum(numbers)
    if total <= 0:
        return None
    return tuple(round(v / total, 6) for v in numbers)


# --------------------------------------------------------------------------- 解析


def _is_likert(item: Item) -> bool:
    if item.kind is not ItemKind.SINGLE or item.option_count < 3:
        return False
    labels = [(option.label or "").strip() for option in item.options]
    return labels == [str(i) for i in range(1, len(labels) + 1)]


def scorable_items(survey: Survey, extra: Sequence[int] = ()) -> list[int]:
    """可参与测量的项目：量表题整题、矩阵题的每一行、李克特式单选题。

    `extra` 用于把普通单选题显式纳入测量（例如"使用时长 1~4 档"当调节变量）。
    """
    numbers: list[int] = []
    for item in survey.items:
        if item.kind is ItemKind.SCALE:
            numbers.append(item.number)
        elif item.kind is ItemKind.MATRIX:
            numbers.extend(
                item.number * ROW_STRIDE + row for row in range(1, item.row_count + 1)
            )
        elif _is_likert(item):
            numbers.append(item.number)
    for ref in extra:
        question, row = split_ref(int(ref))
        if row:
            continue
        try:
            item = survey.item(question)
        except KeyError:
            continue
        # 矩阵题/量表题本来就有自己的编号（整题或行号），再按整题加入会生成
        # 一个「整题」项目，写入时覆盖矩阵各行答案。这类题一律跳过。
        if item.kind in {ItemKind.MATRIX, ItemKind.SCALE}:
            continue
        if item.number not in numbers and item.option_count >= 2:
            numbers.append(item.number)
    return numbers


def _option_count_of(survey: Survey, ref: int) -> int:
    question, _ = split_ref(ref)
    try:
        return max(2, survey.item(question).option_count)
    except KeyError:
        return 5


def _auto_groups(survey: Survey, refs: list[int]) -> list[tuple[str, tuple[int, ...]]]:
    """按题型自动分维：一道矩阵题一维、一道量表题一维。"""
    allowed = set(refs)
    groups: list[tuple[str, tuple[int, ...]]] = []
    for item in survey.items:
        title = (item.title or f"第{item.number}题").strip()[:12]
        if item.kind is ItemKind.MATRIX:
            rows = tuple(
                item.number * ROW_STRIDE + row
                for row in range(1, item.row_count + 1)
                if item.number * ROW_STRIDE + row in allowed
            )
            if rows:
                groups.append((title, rows))
        elif item.kind is ItemKind.SCALE or _is_likert(item):
            if item.number in allowed:
                groups.append((title, (item.number,)))
    return groups


def _parse_correlation_keys(raw: Mapping[str, float], names: list[str]) -> dict[tuple[int, int], float]:
    """支持两种写法：索引对 "0-1" 或构念名 "部署-障碍"。"""
    index_of = {name: i for i, name in enumerate(names)}
    out: dict[tuple[int, int], float] = {}
    for key, value in raw.items():
        text = str(key).replace(":", "-").replace(",", "-").strip()
        left, _, right = text.partition("-")
        if not right:
            continue
        try:
            i = int(left) if left.isdigit() else index_of[left.strip()]
            j = int(right) if right.isdigit() else index_of[right.strip()]
            value_f = max(-0.98, min(0.98, float(value)))
        except (KeyError, TypeError, ValueError):
            continue
        if i == j:
            continue
        out[(min(i, j), max(i, j))] = value_f
    return out


def resolve_model(survey: Survey, goal: ResearchGoal) -> MeasurementModel:
    """把界面配置归一化成唯一的测量模型。

    原则：
    1. **因变量也是构念**，不再从测量里剔除——它在因子分析里必须和自己的题一起走；
    2. 构念间相关默认取 `dimension_correlation`，可用 `dimension_correlations`
       按对覆盖（带符号）；
    3. 没配维度时按题型自动分维，结局所在的题自动成为一个构念。
    """
    refs = scorable_items(survey, goal.ordinal_items)
    if not refs:
        return MeasurementModel(constructs=(), notes=("问卷里没有可参与测量的题（量表或矩阵）",))

    allowed = set(refs)
    groups: list[tuple[str, tuple[int, ...]]] = []

    # 先用题型自动分组，拿到的名字（如"第5题"）比"维度1"更可读；
    # 用户显式配的维度如果和自动分组重合，就沿用自动名字。
    auto_names = {
        tuple(items): name for name, items in _auto_groups(survey, refs)
    }

    for index, group in enumerate(goal.dimensions):
        items = tuple(n for n in group if n in allowed)
        if items:
            name = auto_names.get(items)
            if name is None and len(items) == 1:
                try:
                    name = (survey.item(split_ref(items[0])[0]).title or "").strip()[:12]
                except KeyError:
                    name = None
            groups.append((name or f"维度{index + 1}", items))

    covered = {ref for _, items in groups for ref in items}
    if not groups:
        groups = _auto_groups(survey, refs)
        covered = {ref for _, items in groups for ref in items}

    # 结局项若不在任何构念里（例如界面把它排除出去了），补一个构念，避免它悬空。
    # 注意：**所有**结局项都保留在 outcome_items 里，包括不可测量的单选题——
    # 二分类结局需要知道题号才能切分。
    raw_outcome = tuple(goal.outcome.items)
    dangling = tuple(ref for ref in raw_outcome if ref in allowed and ref not in covered)
    if dangling:
        name = "因变量" if goal.outcome.label else f"因变量{len(groups) + 1}"
        groups.append((name, dangling))

    # 显式声明的有序单选题（如使用时长）也要有自己的构念
    ordinal_dangling = tuple(
        ref for ref in goal.ordinal_items
        if ref in allowed and ref not in covered and ref not in dangling
    )
    if ordinal_dangling:
        try:
            label = survey.item(ordinal_dangling[0]).title or "调节变量"
        except KeyError:
            label = "调节变量"
        groups.append((label[:12], ordinal_dangling))

    if not groups:
        return MeasurementModel(constructs=(), notes=("问卷里没有可参与测量的题（量表或矩阵）",))

    # α -> 载荷：ρ = α/(k-α(k-1)), λ = √ρ
    constructs: list[Construct] = []
    for name, items in groups:
        k = len(items)
        alpha = max(0.30, min(0.98, float(goal.alpha_target)))
        rho = alpha / (k - alpha * (k - 1)) if k >= 2 else 0.5
        loading = math.sqrt(max(0.05, min(0.95, rho)))
        reversed_group = False  # 单题反向由 reverse_items 统一处理
        constructs.append(
            Construct(
                name=name,
                items=items,
                loading=round(loading, 4),
                reverse=reversed_group,
                option_counts=tuple(_option_count_of(survey, ref) for ref in items),
            )
        )

    names = [c.name for c in constructs]
    owner = {ref: idx for idx, c in enumerate(constructs) for ref in c.items}

    # 结局构念与合成方式
    outcome_index = None
    for ref in raw_outcome:
        if ref in owner:
            outcome_index = owner[ref]
            break
    if outcome_index is not None:
        constructs[outcome_index] = Construct(
            name=constructs[outcome_index].name,
            items=constructs[outcome_index].items,
            loading=constructs[outcome_index].loading,
            reverse=constructs[outcome_index].reverse,
            is_outcome=True,
            option_counts=constructs[outcome_index].option_counts,
        )

    # 相关：默认值 + 显式覆盖 + 方向推导
    correlations = _parse_correlation_keys(goal.dimension_correlations, names)
    default_corr = max(-0.95, min(0.95, float(goal.dimension_correlation)))
    effect_r = float(goal.target_effect) if goal.target_effect else DEFAULT_EFFECT

    if outcome_index is not None:
        outcome_loading = constructs[outcome_index].loading
        for number in goal.predictors:
            idx = owner.get(number)
            if idx is None or idx == outcome_index:
                continue
            direction = goal.direction_of(number)
            if direction == "0":
                target = 0.0
            else:
                sign = -1.0 if direction == "-" else 1.0
                loading = constructs[idx].loading
                denom = max(0.15, loading * outcome_loading)
                target = sign * min(0.95, effect_r / denom)
            key = (min(idx, outcome_index), max(idx, outcome_index))
            if key not in correlations:
                correlations[key] = round(target, 4)

    notes: list[str] = []
    moderation: list[Moderation] = []
    if goal.moderation and outcome_index is not None:
        try:
            focal = int(goal.moderation.get("focal") or 0)
            moderator = int(goal.moderation.get("moderator") or 0)
            effect = float(goal.moderation.get("effect") or 0.35)
        except (TypeError, ValueError):
            focal = moderator = 0
            effect = 0.35
        fi, mi = owner.get(focal), owner.get(moderator)
        if fi is not None and mi is not None and fi != mi and fi != outcome_index:
            moderation.append(
                Moderation(
                    focal=fi,
                    moderator=mi,
                    outcome=outcome_index,
                    effect=max(0.05, min(0.9, effect)),
                )
            )
        else:
            notes.append("调节配置不完整：自变量与调节变量都必须是可测量项目")

    if outcome_index is None and goal.outcome.used:
        notes.append("结局项不在任何构念里，已跳过结论构造")

    marginals: dict[int, tuple[float, ...]] = {}
    for ref in refs:
        custom = normalize_marginal(goal.marginals.get(ref), _option_count_of(survey, ref))
        if custom is not None:
            marginals[ref] = custom

    return MeasurementModel(
        constructs=tuple(constructs),
        correlations=correlations,
        default_correlation=default_corr,
        outcome_index=outcome_index,
        outcome_items=raw_outcome,
        outcome_aggregate=goal.outcome.aggregate or "single",
        marginals=marginals,
        reverse_items=frozenset(
            int(ref) for ref in goal.reverse if int(ref) in allowed
        ),
        moderation=tuple(moderation),
        predictors=tuple(goal.predictors),
        directions=dict(goal.directions),
        positive_options=tuple(goal.positive_options),
        event_rate=float(goal.event_rate),
        notes=tuple(notes),
    )
