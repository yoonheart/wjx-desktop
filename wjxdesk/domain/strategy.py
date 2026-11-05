"""方案（Profile）模型。

设计目标：
1. 人能读懂：字段名、层级、默认值都自解释，导出的 JSON 可以直接交给模型修改；
2. 结构可演进：带 version 与迁移函数链；
3. 校验前置：非法值在加载时就报错，而不是跑到一半才炸。

三层配置的边界：
    Profile（用户保存的方案） → TaskOverrides（本次覆盖） → RuntimeParams（派生）
RuntimeParams 不在这里定义，由 runtime 层按 Profile 计算。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from wjxdesk.domain.goal import ResearchGoal
from wjxdesk.domain.enums import ItemKind, PacingProfile, SkewShape

CONFIG_VERSION = 2

# 提交时刻的自然分布权重（0 点 → 23 点）。凌晨极低，上午与晚间为双峰。
DEFAULT_HOUR_WEIGHTS: tuple[float, ...] = (
    0.2, 0.1, 0.1, 0.1, 0.1, 0.2,
    0.4, 0.8, 1.4, 2.0, 2.2, 2.0,
    1.5, 1.8, 2.0, 1.9, 1.9, 2.0,
    2.2, 2.5, 2.6, 2.4, 1.6, 0.8,
)


def _plain(value: Any) -> Any:
    """把配置对象转成可 JSON 化的结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


def _coerce_mapping(raw: Any, value_type: Any = None) -> dict[Any, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"期望对象，实际是 {type(raw).__name__}")
    out: dict[Any, Any] = {}
    for k, v in raw.items():
        key: Any = k
        if isinstance(k, str) and k.lstrip("-").isdigit():
            key = int(k)
        out[key] = v
    return out


# --------------------------------------------------------------------------- 形状


@dataclass(frozen=True, slots=True)
class SkewRule:
    """选项分布形状。strength 为 0 时退化为均匀分布。"""

    shape: SkewShape = SkewShape.UNIFORM
    strength: float = 0.0

    def normalized(self) -> "SkewRule":
        strength = max(0.0, min(5.0, float(self.strength)))
        if self.shape is SkewShape.UNIFORM:
            strength = 0.0
        return replace(self, strength=strength)


# --------------------------------------------------------------------------- 单题规则


@dataclass(frozen=True, slots=True)
class ItemRule:
    """一道题的生成规则。所有字段留空时走默认规则。"""

    # 各选项的显式概率权重（不要求归一化，系统会归一化）
    weights: tuple[float, ...] = ()
    # 形状倾向；weights 非空时以 weights 为准
    skew: SkewRule = field(default_factory=SkewRule)
    # 填空/其他项候选文本
    texts: tuple[str, ...] = ()
    text_weights: tuple[float, ...] = ()
    # 唯一化分组名；同组内答案不重复
    unique_group: str = ""
    # 矩阵题按行给出的列权重：外层是行，内层是各列（不要求归一化）
    matrix_weights: tuple[tuple[float, ...], ...] = ()

    def weight_list(self) -> list[float]:
        return [float(w) for w in self.weights]

    def row_weights(self, row_index: int, column_count: int) -> list[float] | None:
        """取某一行(1 基)的列权重；没有配置则返回 None。"""
        if not self.matrix_weights:
            return None
        index = row_index - 1
        if index < 0 or index >= len(self.matrix_weights):
            return None
        row = list(self.matrix_weights[index])
        if not row:
            return None
        row = (row + [0.0] * column_count)[:column_count]
        return row if sum(row) > 0 else None


@dataclass(frozen=True, slots=True)
class TextFallback:
    """没有配置文本时的兜底。宁可短、宁可中性，也不要用同一句话刷满。"""

    values: tuple[str, ...] = ("无",)

    def pick_pool(self) -> tuple[str, ...]:
        pool = tuple(v for v in self.values if v)
        return pool or ("无",)


# --------------------------------------------------------------------------- 目标


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """想要的统计性质。留空表示不追指标，只按题目规则填。"""

    enabled: bool = False
    alpha_min: float = 0.70
    alpha_max: float = 0.92
    kmo_min: float = 0.70
    # 显著项数量的目标区间，刻意不要求「全部显著」
    sig_min: int = 2
    sig_max: int = 4
    # 题项数与样本量比例，低于此值提示样本不足
    participant_ratio: int = 10
    # 单因子解释占比上限（共同方法偏差）
    harman_max: float = 0.40
    # 刻意保留的噪声比例，避免数据过于完美
    noise: float = 0.05

    def validate(self) -> None:
        if not 0.0 < self.alpha_min <= self.alpha_max <= 1.0:
            raise ValueError("alpha 区间不合法")
        if not 0.0 < self.kmo_min <= 1.0:
            raise ValueError("kmo_min 不合法")
        if self.sig_min > self.sig_max or self.sig_min < 0:
            raise ValueError("显著项区间不合法")


# --------------------------------------------------------------------------- 节奏


@dataclass(frozen=True, slots=True)
class PacingConfig:
    """节奏。秒数可以微调，但通常只需要选画像。"""

    profile: PacingProfile = PacingProfile.STANDARD
    qps: float = 0.2
    median_gap_s: float = 20.0
    jitter_sigma: float = 0.6
    per_exit_quota: int = 2
    cooldown_s: float = 600.0
    hour_weights: tuple[float, ...] = DEFAULT_HOUR_WEIGHTS
    # 单份停留时长 = 题量 × 每秒下限~上限，再乘随机系数
    seconds_per_item_low: float = 6.0
    seconds_per_item_high: float = 16.0
    # 协议层提交不需要真的等待：开始时间直接回填即可。
    # 只有走浏览器兜底时才需要打开它。
    real_wait: bool = False
    # 单份最多等多久，防止时间控制填太大把任务拖死
    real_wait_cap_s: float = 240.0
    # 整份作答时长区间（秒）：填了就按它采样，不再用「每题秒数 × 题数」估算
    duration_low_s: float = 0.0
    duration_high_s: float = 0.0
    # 错峰启动：第 N 份比第 1 份晚 (N-1) x 这个秒数开始，避免同一毫秒全打出去
    start_stagger_s: float = 0.2

    def validate(self) -> None:
        if self.qps <= 0:
            raise ValueError("qps 必须为正")
        if self.median_gap_s <= 0:
            raise ValueError("median_gap_s 必须为正")
        if self.per_exit_quota < 1:
            raise ValueError("per_exit_quota 至少为 1")
        if len(self.hour_weights) != 24:
            raise ValueError("hour_weights 必须是 24 个值")


# --------------------------------------------------------------------------- 渠道


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """设备与来源画像。

    exit_link 是出口（代理）提取链接：空串表示直连。它随方案保存/导出，
    换机器不用重新粘一次。
    """

    ua_profiles: tuple[str, ...] = ("pc",)
    # 渠道来源标识的取值集合；空表示由协议清单决定
    source_tokens: tuple[str, ...] = ()
    # 请求校验策略：xor_ktimes 为线上算法（异或），none 用于个别不需要校验的活动
    seal_mode: str = "xor_ktimes"
    # 出口提取链接；空 = 直连本机
    exit_link: str = ""
    # 来源伪装：是否按比例分配来源画像
    source_enabled: bool = False
    # {"mobile": 60, "link": 20, "wechat": 20}，权重不必归一化
    source_mix: Mapping[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- 配额


@dataclass(frozen=True, slots=True)
class QuotaConfig:
    """人群配额。键为题目编号，值为「选项 -> 期望占比」。"""

    enabled: bool = False
    # {题号: {选项序号: 占比}}，占比之和不必为 1，系统会归一化
    targets: Mapping[int, Mapping[int, float]] = field(default_factory=dict)

    def validate(self) -> None:
        for number, dist in self.targets.items():
            if not dist:
                raise ValueError(f"第 {number} 题配额为空")
            if any(v < 0 for v in dist.values()):
                raise ValueError(f"第 {number} 题配额出现负值")


# --------------------------------------------------------------------------- 信效度


@dataclass(frozen=True, slots=True)
class ValidityConfig:
    """信效度优化：维度分组 + 维度间相关 + 反向计分 + 目标信度。

    dimensions 写法：`1-5;6-10` 表示第 1~5 题一个维度、6~10 题另一个维度；
    也支持逗号与加号分隔题号（`1,2,3|4,5,6`）。
    correlation 写法：维度间的相关，`0.5,0.3` 之类，按上三角给出。
    """

    enabled: bool = False
    alpha_target: float = 0.80
    dimensions: str = ""
    single_questions: str = ""
    correlation: str = ""
    reverse: str = ""

    def validate(self) -> None:
        if not 0.0 < self.alpha_target <= 1.0:
            raise ValueError("目标信度需在 (0,1] 之间")


# --------------------------------------------------------------------------- 合成答卷


@dataclass(frozen=True, slots=True)
class SynthesisConfig:
    """合成答卷：给定结局题与自变量，按目标效应量生成整批数据。

    outcome:    {"question": 8, "positive_option": [1], "label": "满意度"}
    predictors: [5, 6, 7]          自变量题号（量表/矩阵题更稳）
    effects:    {"5": 0.6, "6": -0.4}   期望方向与强度
    targets:    {"alpha_min":0.75,"kmo_min":0.6,"sig_min":2,"sig_max":4}
    """

    enabled: bool = False
    outcome_question: int = 0
    positive_options: tuple[int, ...] = ()
    label: str = ""
    predictors: tuple[int, ...] = ()
    effects: Mapping[int, float] = field(default_factory=dict)
    alpha_min: float = 0.75
    kmo_min: float = 0.60
    sig_min: int = 2
    sig_max: int = 4
    event_rate: float = 0.5
    seed_search: int = 40

    def validate(self) -> None:
        if self.enabled and self.outcome_question <= 0:
            raise ValueError("合成答卷：必须指定结局题号")


# --------------------------------------------------------------------------- 总配置


@dataclass(frozen=True, slots=True)
class Strategy:
    """一份完整方案。这是唯一从界面 / 文件 / 模型三处进来的配置对象。"""

    version: int = CONFIG_VERSION
    total: int = 10
    # 界面上仍叫「倍速」：同时进行 N 份
    concurrency: int = 2
    seed: int = 0
    default_rule: ItemRule = field(default_factory=ItemRule)
    items: Mapping[int, ItemRule] = field(default_factory=dict)
    texts: TextFallback = field(default_factory=TextFallback)
    targets: TargetSpec = field(default_factory=TargetSpec)
    pacing: PacingConfig = field(default_factory=PacingConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    validity: ValidityConfig = field(default_factory=ValidityConfig)
    goal: ResearchGoal = field(default_factory=ResearchGoal)
    synth: SynthesisConfig = field(default_factory=SynthesisConfig)

    # -- 校验 ---------------------------------------------------------------

    def validate(self) -> None:
        if self.total < 1:
            raise ValueError("目标份数至少为 1")
        if not 1 <= self.concurrency <= 50:
            raise ValueError("倍速（并发）需在 1~50 之间")
        self.targets.validate()
        self.pacing.validate()
        self.quota.validate()
        self.validity.validate()
        self.goal.validate()
        self.synth.validate()

    def rule_for(self, number: int) -> ItemRule:
        return self.items.get(number, self.default_rule)

    def resolved_seed(self) -> int:
        return self.seed if self.seed else int(time.time() * 1000) & 0x7FFFFFFF

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Strategy":
        data = dict(raw or {})
        version = int(data.get("version", 0) or 0)
        if version and version > CONFIG_VERSION:
            raise ValueError(f"配置版本 {version} 高于程序支持的 {CONFIG_VERSION}，请升级程序")
        data = _migrate(data, version)

        return cls(
            version=CONFIG_VERSION,
            total=int(data.get("total", 10)),
            concurrency=int(data.get("concurrency", 2)),
            seed=int(data.get("seed", 0) or 0),
            default_rule=_item_rule(data.get("default_rule")),
            items={int(k): _item_rule(v) for k, v in _coerce_mapping(data.get("items")).items()},
            texts=_texts(data.get("texts")),
            targets=_targets(data.get("targets")),
            pacing=_pacing(data.get("pacing")),
            channel=_channel(data.get("channel")),
            quota=_quota(data.get("quota")),
            validity=_validity(data.get("validity")),
            goal=ResearchGoal.from_dict(data.get("goal")),
            synth=_synth(data.get("synth")),
        )


# --------------------------------------------------------------------------- 子结构构造


def _item_rule(raw: Any) -> ItemRule:
    if raw is None:
        return ItemRule()
    if not isinstance(raw, Mapping):
        raise ValueError("items 的每一项必须是对象")
    skew_raw = raw.get("skew") or {}
    matrix_raw = raw.get("matrix_weights") or ()
    return ItemRule(
        weights=tuple(float(x) for x in raw.get("weights", ()) or ()),
        skew=SkewRule(
            shape=SkewShape(skew_raw.get("shape", SkewShape.UNIFORM)),
            strength=float(skew_raw.get("strength", 0.0) or 0.0),
        ).normalized(),
        texts=tuple(str(x) for x in raw.get("texts", ()) or ()),
        text_weights=tuple(float(x) for x in raw.get("text_weights", ()) or ()),
        unique_group=str(raw.get("unique_group", "") or ""),
        matrix_weights=tuple(
            tuple(float(v) for v in row) for row in matrix_raw if isinstance(row, (list, tuple))
        ),
    )


def _texts(raw: Any) -> TextFallback:
    if not isinstance(raw, Mapping):
        return TextFallback()
    return TextFallback(
        values=tuple(str(x) for x in raw.get("values", ()) or ()) or TextFallback().values,
    )


def _targets(raw: Any) -> TargetSpec:
    if not isinstance(raw, Mapping):
        return TargetSpec()
    base = TargetSpec()
    spec = TargetSpec(
        enabled=bool(raw.get("enabled", False)),
        alpha_min=float(raw.get("alpha_min", base.alpha_min)),
        alpha_max=float(raw.get("alpha_max", base.alpha_max)),
        kmo_min=float(raw.get("kmo_min", base.kmo_min)),
        sig_min=int(raw.get("sig_min", base.sig_min)),
        sig_max=int(raw.get("sig_max", base.sig_max)),
        participant_ratio=int(raw.get("participant_ratio", base.participant_ratio)),
        harman_max=float(raw.get("harman_max", base.harman_max)),
        noise=float(raw.get("noise", base.noise)),
    )
    spec.validate()
    return spec


def _pacing(raw: Any) -> PacingConfig:
    if not isinstance(raw, Mapping):
        return PacingConfig()
    base = PacingConfig()
    hours = raw.get("hour_weights")
    cfg = PacingConfig(
        profile=PacingProfile(raw.get("profile", base.profile)),
        qps=float(raw.get("qps", base.qps)),
        median_gap_s=float(raw.get("median_gap_s", base.median_gap_s)),
        jitter_sigma=float(raw.get("jitter_sigma", base.jitter_sigma)),
        per_exit_quota=int(raw.get("per_exit_quota", base.per_exit_quota)),
        cooldown_s=float(raw.get("cooldown_s", base.cooldown_s)),
        hour_weights=tuple(float(x) for x in hours) if hours else base.hour_weights,
        seconds_per_item_low=float(raw.get("seconds_per_item_low", base.seconds_per_item_low)),
        seconds_per_item_high=float(raw.get("seconds_per_item_high", base.seconds_per_item_high)),
        real_wait=bool(raw.get("real_wait", base.real_wait)),
        real_wait_cap_s=float(raw.get("real_wait_cap_s", base.real_wait_cap_s)),
        duration_low_s=float(raw.get("duration_low_s", base.duration_low_s)),
        duration_high_s=float(raw.get("duration_high_s", base.duration_high_s)),
        start_stagger_s=float(raw.get("start_stagger_s", base.start_stagger_s)),
    )
    cfg.validate()
    return cfg


def _channel(raw: Any) -> ChannelConfig:
    if not isinstance(raw, Mapping):
        return ChannelConfig()
    base = ChannelConfig()
    mix = {}
    for key, value in _coerce_mapping(raw.get("source_mix")).items():
        try:
            mix[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return ChannelConfig(
        ua_profiles=tuple(str(x) for x in raw.get("ua_profiles", ()) or ()) or base.ua_profiles,
        source_tokens=tuple(str(x) for x in raw.get("source_tokens", ()) or ()),
        seal_mode=str(raw.get("seal_mode", base.seal_mode) or "none"),
        exit_link=str(raw.get("exit_link", "") or "").strip(),
        source_enabled=bool(raw.get("source_enabled", False)),
        source_mix=mix,
    )


def _quota(raw: Any) -> QuotaConfig:
    if not isinstance(raw, Mapping):
        return QuotaConfig()
    targets = {
        int(number): {int(k): float(v) for k, v in _coerce_mapping(dist).items()}
        for number, dist in _coerce_mapping(raw.get("targets")).items()
    }
    cfg = QuotaConfig(enabled=bool(raw.get("enabled", False)), targets=targets)
    cfg.validate()
    return cfg


def _validity(raw: Any) -> ValidityConfig:
    if not isinstance(raw, Mapping):
        return ValidityConfig()
    cfg = ValidityConfig(
        enabled=bool(raw.get("enabled", False)),
        alpha_target=float(raw.get("alpha_target", 0.80)),
        dimensions=str(raw.get("dimensions", "") or ""),
        single_questions=str(raw.get("single_questions", "") or ""),
        correlation=str(raw.get("correlation", "") or ""),
        reverse=str(raw.get("reverse", "") or ""),
    )
    cfg.validate()
    return cfg


def _synth(raw: Any) -> SynthesisConfig:
    if not isinstance(raw, Mapping):
        return SynthesisConfig()

    outcome = raw.get("outcome") or {}
    targets = raw.get("targets") or {}
    effects_raw = raw.get("effects") or {}
    effects: dict[int, float] = {}
    for key, value in _coerce_mapping(effects_raw).items():
        try:
            effects[int(key)] = float(value)
        except (TypeError, ValueError):
            continue

    cfg = SynthesisConfig(
        enabled=bool(raw.get("enabled", False)),
        outcome_question=int(outcome.get("question") or 0),
        positive_options=tuple(int(x) for x in (outcome.get("positive_option") or ())),
        label=str(outcome.get("label", "") or ""),
        predictors=tuple(int(x) for x in (raw.get("predictors") or ())),
        effects=effects,
        alpha_min=float(targets.get("alpha_min", 0.75)),
        kmo_min=float(targets.get("kmo_min", 0.60)),
        sig_min=int(targets.get("sig_min", 2)),
        sig_max=int(targets.get("sig_max", 4)),
        event_rate=float(raw.get("event_rate", 0.5)),
        seed_search=int(raw.get("seed_search", 40)),
    )
    cfg.validate()
    return cfg


# --------------------------------------------------------------------------- 迁移


def _migrate(data: dict[str, Any], version: int) -> dict[str, Any]:
    """旧版本配置迁移链。新增版本时在这里追加一步。"""
    if version >= CONFIG_VERSION:
        return data

    migrated = dict(data)
    if version < 2:
        # v1 → v2：结局从「一个题号」升级为 outcome_items（支持矩阵行与多项目合成）。
        # 只做字段搬运，不动其它结构；老文件读进来行为与以前一致。
        goal = migrated.get("goal")
        if isinstance(goal, Mapping):
            goal = dict(goal)
            if not goal.get("outcome_items") and goal.get("outcome_question"):
                goal["outcome_items"] = [int(goal["outcome_question"])]
                goal.setdefault("outcome_aggregate", "single")
            migrated["goal"] = goal
    migrated["version"] = CONFIG_VERSION
    return migrated


# --------------------------------------------------------------------------- 默认方案


def default_strategy() -> Strategy:
    """界面首次打开时的方案。"""
    return Strategy(
        total=10,
        concurrency=2,
        pacing=PacingConfig(profile=PacingProfile.STANDARD),
        channel=ChannelConfig(ua_profiles=("pc",)),
    )


def demo_strategy_for(survey_kinds: Mapping[int, ItemKind]) -> Strategy:
    """按题目类型给出一份可用的初始方案（供「一键配置」使用）。"""
    items = {
        number: ItemRule(skew=SkewRule(SkewShape.UNIFORM, 0.0))
        for number, kind in survey_kinds.items()
        if kind.has_options
    }
    return replace(default_strategy(), items=items)
