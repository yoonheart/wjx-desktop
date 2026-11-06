"""数据目标：把「信效度」和「合成答卷」合成一个用户能理解的概念。

三块内容，全部可选：
- 信度：目标 α（逐维度保证）
- 效度：目标 KMO（维度间相关可达）
- 结论：结局题 + 影响因素 + 期望方向（按目标显著性构造）

设计要点：不做「随机生成 + 不达标重抽」，而是按目标**解析反推**参数：
α 与「载荷/误差」比值有闭式关系，显著性由「相关系数临界值」决定，
所以能稳定达标，而不是碰运气。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# 矩阵行编号：第 5 题第 3 行记作 503。与 engine/goal_solver 里的约定保持一致，
# 空着的行号 0 表示「整题」（只对量表/单选有意义）。
ROW_STRIDE = 100

AGGREGATE_SINGLE = "single"
AGGREGATE_MEAN = "mean"
AGGREGATES = (AGGREGATE_SINGLE, AGGREGATE_MEAN)


def make_ref(question: int, row: int = 0) -> int:
    """题号 + 行号 → 项目编号。"""
    return int(question) * ROW_STRIDE + int(row) if row else int(question)


def split_ref(ref: int) -> tuple[int, int]:
    """项目编号 → (题号, 行号)；行号为 0 表示整题。"""
    value = int(ref)
    if value >= ROW_STRIDE:
        question, row = divmod(value, ROW_STRIDE)
        return question, row
    return value, 0


def parse_refs(raw: Any) -> tuple[int, ...]:
    """接受 [8, 801] / "8,801" / "8 801" / "8;801"，统一成整数元组。"""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple, set)):
        out: list[int] = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return tuple(out)
    import re

    return tuple(int(x) for x in re.split(r"[,;\s、]+", str(raw)) if x.strip().isdigit())


@dataclass(frozen=True, slots=True)
class OutcomeSpec:
    """因变量：一个或几个项目，按需合成一个数值。

    - items：项目编号（题号；矩阵行用 题号*100+行号）
    - aggregate：single（单题/单行）或 mean（多行取均值，用于多维构念）
    - positive：哪些选项算「正面」，只有二分类结局（单选）才用
    """

    items: tuple[int, ...] = ()
    aggregate: str = AGGREGATE_SINGLE
    positive: tuple[int, ...] = ()
    label: str = ""

    @property
    def question(self) -> int:
        """第一个项目所属的题号，供展示与旧字段使用。"""
        return split_ref(self.items[0])[0] if self.items else 0

    @property
    def used(self) -> bool:
        return bool(self.items)

    @property
    def is_composite(self) -> bool:
        return len(self.items) > 1 or self.aggregate == AGGREGATE_MEAN

    def validate(self) -> None:
        if self.aggregate not in AGGREGATES:
            raise ValueError(f"结局合成方式不合法：{self.aggregate}")
        for ref in self.items:
            if ref <= 0:
                raise ValueError(f"结局项目编号不合法：{ref}")
            question, row = split_ref(ref)
            if question <= 0 or (ref >= ROW_STRIDE and row <= 0):
                raise ValueError(f"结局项目编号不合法：{ref}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "aggregate": self.aggregate,
            "positive": list(self.positive),
            "label": self.label,
        }



def _parse_groups(spec: Any) -> tuple[tuple[int, ...], ...]:
    """把维度分组解析成「题号元组的元组」。

    支持多种写法，随便一种都能懂：
      "1-3;4-6"      → ((1,2,3),(4,6,...))
      [[1,2,3],[4,5]]→ 原样
      ""             → 空（表示自动：所有量表题一个维度）
    """
    if not spec:
        return ()
    if isinstance(spec, (list, tuple)):
        groups: list[tuple[int, ...]] = []
        for group in spec:
            if isinstance(group, (list, tuple)):
                groups.append(tuple(int(x) for x in group))
            elif isinstance(group, str):
                groups.append(tuple(_parse_numbers(group)))
        return tuple(g for g in groups if g)

    text = str(spec)
    groups = []
    for chunk in text.replace("|", ";").replace("，", ";").split(";"):
        numbers = tuple(_parse_numbers(chunk))
        if numbers:
            groups.append(numbers)
    return tuple(groups)


def _parse_numbers(spec: str) -> list[int]:
    import re

    text = (spec or "").strip()
    if not text:
        return []
    found: list[int] = []
    for chunk in re.split(r"[,\s+、]+", text):
        if not chunk:
            continue
        if "-" in chunk:
            head, _, tail = chunk.partition("-")
            if head.strip().isdigit() and tail.strip().isdigit():
                start, end = int(head), int(tail)
                if start > end:
                    start, end = end, start
                found.extend(range(start, end + 1))
                continue
        if chunk.strip().isdigit():
            found.append(int(chunk))
    return found


@dataclass(frozen=True, slots=True)
class ResearchGoal:
    """用户想要的数据长什么样。"""

    enabled: bool = False

    # -- 信度 --
    alpha_target: float = 0.85
    dimensions: tuple[tuple[int, ...], ...] = ()
    reverse: tuple[int, ...] = ()
    # 维度间相关：0=独立维度，0.6=强相关（影响 KMO）
    dimension_correlation: float = 0.45
    # 按「构念对」覆盖相关，带符号。键支持 "0-1" 或 "部署-障碍"
    dimension_correlations: Mapping[str, float] = field(default_factory=dict)
    # 期望的题级效应量（相关系数绝对值），用于把方向换算成构念间相关
    target_effect: float = 0.45
    # 每题的选项占比（项目编号 -> 占比元组），留空用默认形状
    marginals: Mapping[int, tuple[float, ...]] = field(default_factory=dict)
    # 把指定单选题当成有序测量（例如"使用时长 1~4 档"要当调节变量）
    ordinal_items: tuple[int, ...] = ()
    # 调节：{"focal": 自变量项目, "moderator": 调节项目, "effect": 0.35}
    moderation: Mapping[str, float] = field(default_factory=dict)

    # -- 效度 --
    kmo_target: float = 0.70

    # -- 结论 --
    # 结局（因变量）。新写法用 outcome_items（可含矩阵行，可多项目合成）；
    # outcome_question 是旧写法，保留是为了读老方案文件，两者择一即可。
    outcome_items: tuple[int, ...] = ()
    outcome_aggregate: str = AGGREGATE_SINGLE
    outcome_question: int = 0
    positive_options: tuple[int, ...] = ()
    outcome_label: str = ""
    predictors: tuple[int, ...] = ()
    # 题号 → "+" 正向 / "-" 负向 / "0" 不显著
    directions: Mapping[int, str] = field(default_factory=dict)
    # 至少几个自变量要显著
    significant_required: int = 0
    # 结局事件的占比（如"满意"占 50%）
    event_rate: float = 0.5

    # -- 求解 --
    attempts: int = 20

    def dimension_of(self, number: int) -> int:
        for index, group in enumerate(self.dimensions):
            if number in group:
                return index
        return 0

    @property
    def outcome(self) -> OutcomeSpec:
        """把新旧两种写法收敛成同一个结局描述。"""
        items = tuple(self.outcome_items) or (
            (int(self.outcome_question),) if self.outcome_question else ()
        )
        return OutcomeSpec(
            items=items,
            aggregate=self.outcome_aggregate or AGGREGATE_SINGLE,
            positive=tuple(self.positive_options),
            label=self.outcome_label or "",
        )

    @property
    def dimension_count(self) -> int:
        return max(1, len(self.dimensions))

    @property
    def wants_reliability(self) -> bool:
        return bool(self.enabled)

    @property
    def wants_conclusion(self) -> bool:
        return bool(self.enabled and self.outcome.used and self.predictors)

    def validate(self) -> None:
        if not self.enabled:
            return
        if not 0.0 < self.alpha_target <= 1.0:
            raise ValueError("目标 α 需要在 (0,1] 之间")
        if not 0.0 < self.kmo_target <= 1.0:
            raise ValueError("目标 KMO 需要在 (0,1] 之间")
        self.outcome.validate()
        if not 0.0 < self.event_rate < 1.0:
            raise ValueError("事件占比需要在 0 和 1 之间")
        # 结局项与自变量不能是同一个项目，否则自己解释自己
        overlap = set(self.outcome.items) & set(self.predictors)
        if overlap:
            raise ValueError(f"结局项与自变量重复：{sorted(overlap)}")

    def direction_of(self, number: int) -> str:
        return str(self.directions.get(number, "+"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "alpha_target": self.alpha_target,
            "dimensions": [list(g) for g in self.dimensions],
            "reverse": list(self.reverse),
            "dimension_correlation": self.dimension_correlation,
            "dimension_correlations": dict(self.dimension_correlations),
            "target_effect": self.target_effect,
            "marginals": {str(k): list(v) for k, v in self.marginals.items()},
            "ordinal_items": list(self.ordinal_items),
            "moderation": dict(self.moderation),
            "kmo_target": self.kmo_target,
            "outcome_question": self.outcome.question,
            "outcome_items": list(self.outcome.items),
            "outcome_aggregate": self.outcome.aggregate,
            "positive_options": list(self.positive_options),
            "outcome_label": self.outcome_label,
            "predictors": list(self.predictors),
            "directions": dict(self.directions),
            "significant_required": self.significant_required,
            "event_rate": self.event_rate,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ResearchGoal":
        if not isinstance(raw, Mapping):
            return cls()

        directions: dict[int, str] = {}
        for key, value in (raw.get("directions") or {}).items():
            try:
                directions[int(key)] = str(value)
            except (TypeError, ValueError):
                continue

        # 结局：新写法 outcome_items / outcome_aggregate；也接受
        # {"outcome": {"items": [...], "aggregate": "mean"}} 这种嵌套形式
        nested = raw.get("outcome")
        if isinstance(nested, Mapping):
            items = parse_refs(nested.get("items"))
            aggregate = str(nested.get("aggregate") or AGGREGATE_SINGLE)
            label = str(nested.get("label") or raw.get("outcome_label") or "")
            positives = parse_refs(nested.get("positive")) or parse_refs(
                raw.get("positive_options")
            )
        else:
            items = parse_refs(raw.get("outcome_items"))
            aggregate = str(raw.get("outcome_aggregate") or AGGREGATE_SINGLE)
            label = str(raw.get("outcome_label") or "")
            positives = parse_refs(raw.get("positive_options"))

        correlations: dict[str, float] = {}
        for key, value in (raw.get("dimension_correlations") or {}).items():
            try:
                correlations[str(key)] = max(-0.98, min(0.98, float(value)))
            except (TypeError, ValueError):
                continue

        marginals: dict[int, tuple[float, ...]] = {}
        for key, value in (raw.get("marginals") or {}).items():
            try:
                ref = int(key)
                values = tuple(float(x) for x in value)
            except (TypeError, ValueError):
                continue
            if len(values) >= 2 and sum(values) > 0:
                marginals[ref] = values

        goal = cls(
            enabled=bool(raw.get("enabled", False)),
            alpha_target=float(raw.get("alpha_target", 0.85)),
            dimensions=_parse_groups(raw.get("dimensions")),
            reverse=tuple(int(x) for x in (raw.get("reverse") or ()) if str(x).isdigit()),
            dimension_correlation=float(raw.get("dimension_correlation", 0.45)),
            dimension_correlations=correlations,
            target_effect=float(raw.get("target_effect", 0.45)),
            marginals=marginals,
            ordinal_items=parse_refs(raw.get("ordinal_items")),
            moderation={
                str(k): float(v)
                for k, v in (raw.get("moderation") or {}).items()
                if str(v).replace(".", "", 1).replace("-", "", 1).isdigit()
            },
            kmo_target=float(raw.get("kmo_target", 0.70)),
            outcome_items=items,
            outcome_aggregate=aggregate,
            outcome_question=int(raw.get("outcome_question") or 0) if not items else 0,
            positive_options=positives,
            outcome_label=label,
            predictors=parse_refs(raw.get("predictors")),
            directions=directions,
            significant_required=int(raw.get("significant_required") or 0),
            event_rate=float(raw.get("event_rate", 0.5)),
            attempts=int(raw.get("attempts", 12)),
        )
        goal.validate()
        return goal
