"""旧界面请求兼容层。

界面沿用原来的字段名（targetCount / speedMultiplier / questions[]），
这里把它们翻译成新方案对象，从而不必先改前端就能跑通。
"""

from __future__ import annotations

from typing import Any, Mapping

from wjxdesk.domain.enums import ItemKind, PacingProfile
from wjxdesk.domain.goal import ResearchGoal
from wjxdesk.domain.strategy import (
    ChannelConfig,
    SynthesisConfig,
    ValidityConfig,
    ItemRule,
    PacingConfig,
    SkewRule,
    Strategy,
    TextFallback,
    default_strategy,
)

_TYPE_MAP = {
    "单选题": ItemKind.SINGLE,
    "多选题": ItemKind.MULTI,
    "填空题": ItemKind.BLANK,
    "量表题": ItemKind.SCALE,
    "矩阵题": ItemKind.MATRIX,
    "下拉框": ItemKind.DROPDOWN,
    "滑块题": ItemKind.SLIDER,
    "排序题": ItemKind.RANK,
}

_KIND_CN = {value: key for key, value in _TYPE_MAP.items()}

FREE_TEXT_MARK = "【填空题输入框】"


def legacy_questions(survey: Any) -> list[dict[str, Any]]:
    """把 Survey 渲染成界面认识的老结构。

    界面按「题号 / 题型 / 题目内容 / 选项(字符串数组)」组织，
    这里负责把结构化模型压平成这套展示格式，逻辑都在后端，前端一行不用改。
    """
    out: list[dict[str, Any]] = []
    for item in survey.items:
        labels = [option.label for option in item.options]
        rows = [row.label for row in item.rows]
        options: list[str] = []

        if item.kind in {ItemKind.SINGLE, ItemKind.MULTI, ItemKind.DROPDOWN, ItemKind.RANK}:
            for option in item.options:
                text = f"{option.index}. {option.label}"
                if option.requires_text:
                    text += FREE_TEXT_MARK
                options.append(text)
            # 界面按下拉框渲染时会跳过首项（视为「请选择」占位），这里补回去
            if item.kind is ItemKind.DROPDOWN:
                options.insert(0, "请选择")
        elif item.kind is ItemKind.BLANK:
            options = [FREE_TEXT_MARK]
        elif item.kind is ItemKind.SCALE:
            # 界面只认这个前缀：拆成若干列，每列一个概率输入框
            if labels:
                options = ["量表选项: " + " | ".join(labels)]
            else:
                options = ["量表选项: " + " | ".join(str(i) for i in range(1, 6))]
        elif item.kind is ItemKind.MATRIX:
            if labels:
                options.append("可选答案: " + " | ".join(labels))
            options.extend(f"{i}. {label}" for i, label in enumerate(rows, start=1))
            if not options:
                options = ["[矩阵题，请在网页中查看详细结构]"]
        elif item.kind is ItemKind.SLIDER:
            low = item.low if item.low is not None else 0
            high = item.high if item.high is not None else 100
            options = [f"1. {low}", f"2. {high}"]

        out.append(
            {
                "题号": item.number,
                "题型": _KIND_CN.get(item.kind, "未知题型"),
                "题目内容": item.title,
                "选项": options,
                "必答": item.required,
            }
        )
    return out


def _matrix_weights(question: Mapping[str, Any]) -> tuple[tuple[float, ...], ...]:
    """矩阵题的每行列权重。

    界面提交的结构是：[{rowIndex, rowText, options:[{colIndex, probability}]}]
    这里压平成「行 → 各列权重」，交给生成器按行独立抽样。
    """
    rows_raw = question.get("options") or []
    rows: list[tuple[float, ...]] = []
    for row in rows_raw:
        if not isinstance(row, Mapping):
            continue
        cells = row.get("options") or []
        if not cells:
            continue
        values = [0.0] * len(cells)
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            try:
                index = int(cell.get("colIndex") or 0)
                probability = float(cell.get("probability") or 0.0)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(values):
                values[index] = max(0.0, probability)
        if sum(values) > 0:
            rows.append(tuple(values))
    return tuple(rows)


def _kind(raw: Any) -> ItemKind:
    return _TYPE_MAP.get(str(raw or "").strip(), ItemKind.UNKNOWN)


def _weights(options: list[Mapping[str, Any]]) -> tuple[float, ...]:
    values: list[float] = []
    for option in options:
        raw = option.get("probability")
        try:
            value = float(raw) if raw is not None else -1.0
        except (TypeError, ValueError):
            value = -1.0
        values.append(max(0.0, value))
    if not values or sum(values) <= 0:
        return ()
    return tuple(values)


def _entries(raw: Any) -> list[tuple[str, float]]:
    """文本答案有两种可能的形状：纯字符串，或 {text, probability} 对象。"""
    out: list[tuple[str, float]] = []
    for item in raw or ():
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append((text, 1.0))
        elif isinstance(item, Mapping):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                weight = float(item.get("probability") or 1.0)
            except (TypeError, ValueError):
                weight = 1.0
            out.append((text, max(0.0, weight)))
    return out


def _texts(options: list[Mapping[str, Any]], question: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """收集填空题/「其他」项的候选文本与权重（去重保序）。"""
    entries: list[tuple[str, float]] = []
    for option in options:
        entries.extend(_entries(option.get("optionTextAnswers")))
    entries.extend(_entries(question.get("textAnswers")))

    seen: set[str] = set()
    texts: list[str] = []
    weights: list[float] = []
    for text, weight in entries:
        if text in seen:
            continue
        seen.add(text)
        texts.append(text)
        weights.append(weight)
    return tuple(texts), tuple(weights)


def _is_unique(options: list[Mapping[str, Any]], question: Mapping[str, Any]) -> bool:
    """界面上的「答案唯一」开关：按题或按选项记录为 unique。"""
    if question.get("textAnswersMode") == "unique":
        return True
    return any(option.get("optionTextAnswersMode") == "unique" for option in options)


def _pacing(payload: Mapping[str, Any]) -> PacingConfig:
    profile_raw = str(payload.get("pacingProfile") or "").strip()
    profile = PacingProfile(profile_raw) if profile_raw in {"steady", "standard", "fast"} else None
    base = PacingConfig()
    speed = payload.get("speedMultiplier")
    median = base.median_gap_s
    if isinstance(speed, (int, float)) and speed > 1:
        # 倍速越高，份间冷却越短（但不会小于 3 秒）
        median = max(3.0, base.median_gap_s / float(speed) * 2.0)

    # 每个 IP 最多填几份：关闭复用时为 1
    quota = 1
    raw_quota = payload.get("ipMaxUse")
    if isinstance(raw_quota, (int, float)):
        quota = max(1, min(10, int(raw_quota)))

    low = payload.get("minFillTime")
    high = payload.get("maxFillTime")
    seconds_low, seconds_high = base.seconds_per_item_low, base.seconds_per_item_high
    # 开关关掉时不要偷偷沿用输入框里的旧数值
    time_control = bool(payload.get("timeControl", False))
    duration_low = 0.0
    duration_high = 0.0
    if time_control and isinstance(low, (int, float)) and isinstance(high, (int, float)) and high >= low > 0:
        # 直接按整份时长采样，不再除以 10 折成「每题秒数」
        duration_low = max(1.0, float(low))
        duration_high = max(duration_low, float(high))

    return PacingConfig(
        profile=profile or base.profile,
        # 全局限速要跟并发一起放大：否则 20 路并发被 0.2 次/秒卡成 5 秒一份
        qps=max(base.qps, float(speed or 1) * 0.5),
        median_gap_s=median,
        jitter_sigma=base.jitter_sigma,
        per_exit_quota=quota,
        cooldown_s=base.cooldown_s,
        hour_weights=base.hour_weights,
        seconds_per_item_low=seconds_low,
        seconds_per_item_high=seconds_high,
        duration_low_s=duration_low,
        duration_high_s=duration_high,
    )


def _channel(payload: Mapping[str, Any]) -> ChannelConfig:
    """代理开关、提取链接、来源比例。

    来源伪装在线上是靠 UA 判定的，所以这里存的是「三种来源的权重」，
    由 runtime 层按份抽签决定用哪套 UA。
    """
    use_proxy = bool(payload.get("useProxy", True))
    link = str(payload.get("ipApiUrl") or "").strip() if use_proxy else ""

    mix: dict[str, int] = {}
    if bool(payload.get("sourceEnabled", False)):
        raw = payload.get("sourceRatio") or {}
        if isinstance(raw, Mapping):
            for kind in ("mobile", "link", "wechat"):
                try:
                    weight = int(float(raw.get(kind) or 0))
                except (TypeError, ValueError):
                    weight = 0
                if weight > 0:
                    mix[kind] = weight

    return ChannelConfig(exit_link=link, source_enabled=bool(mix), source_mix=mix)


def _goal(payload: Mapping[str, Any], synth: SynthesisConfig, validity: ValidityConfig) -> ResearchGoal:
    """界面上的「数据目标」。

    新界面直接传 goal；旧界面只有 validityEnabled / synth，这里做一次等价转换，
    保证旧配置也能走新的求解器。
    """
    raw = payload.get("goal")
    if isinstance(raw, Mapping) and raw:
        return ResearchGoal.from_dict(raw)

    if not (validity.enabled or synth.enabled):
        return ResearchGoal()

    dimensions = tuple(
        tuple(int(x) for x in group.replace(" ", "").split("-") if x.isdigit())
        if "-" in group and len([p for p in group.split("-") if p.strip().isdigit()]) == 2
        else tuple(int(x) for x in group.replace(" ", "").split(",") if x.isdigit())
        for group in (validity.dimensions.replace("|", ";").split(";") if validity.dimensions else [])
    )

    directions: dict[int, str] = {}
    for number, effect in synth.effects.items():
        directions[int(number)] = "+" if effect >= 0 else "-"

    return ResearchGoal(
        enabled=True,
        alpha_target=validity.alpha_target,
        dimensions=tuple(g for g in dimensions if g),
        reverse=tuple(int(x) for x in validity.reverse.replace("，", ",").split(",") if x.strip().isdigit()),
        outcome_question=synth.outcome_question,
        positive_options=synth.positive_options,
        outcome_label=synth.label,
        predictors=synth.predictors,
        directions=directions,
        significant_required=synth.sig_min,
    )


def _validity(payload: Mapping[str, Any]) -> ValidityConfig:
    """信效度：目标信度 + 维度分组 + 维度间相关 + 反向题 + 单选量表题号。"""
    return ValidityConfig(
        enabled=bool(payload.get("validityEnabled", False)),
        alpha_target=float(payload.get("validityTarget") or 0.80),
        dimensions=str(payload.get("validityDimensions") or "").strip(),
        single_questions=str(payload.get("validitySingleQuestions") or "").strip(),
        correlation=str(payload.get("validityCorrelation") or "").strip(),
        reverse=str(payload.get("validityReverse") or "").strip(),
    )


def _synth(payload: Mapping[str, Any]) -> SynthesisConfig:
    """合成答卷：结局题 + 自变量 + 效应量 + 目标指标。"""
    raw = payload.get("synth")
    if not isinstance(raw, Mapping) or not raw.get("enabled"):
        return SynthesisConfig()

    outcome = raw.get("outcome") or {}
    targets = raw.get("targets") or {}
    effects_raw = raw.get("effects") or {}

    positive = outcome.get("positiveOption")
    if isinstance(positive, (int, float)):
        positives = (int(positive),)
    elif isinstance(positive, (list, tuple)):
        positives = tuple(int(x) for x in positive if str(x).strip().isdigit())
    elif isinstance(positive, str):
        positives = tuple(int(x) for x in positive.replace("，", ",").split(",") if x.strip().isdigit())
    else:
        positives = ()

    effects: dict[int, float] = {}
    if isinstance(effects_raw, Mapping):
        for key, value in effects_raw.items():
            try:
                effects[int(key)] = float(value)
            except (TypeError, ValueError):
                continue

    sig_total = targets.get("sigTotal") or []
    return SynthesisConfig(
        enabled=True,
        outcome_question=int(outcome.get("question") or 0),
        positive_options=positives,
        label=str(outcome.get("label") or ""),
        predictors=tuple(int(x) for x in (raw.get("predictors") or ()) if str(x).strip().isdigit()),
        effects=effects,
        alpha_min=float(targets.get("alphaMin") or 0.75),
        kmo_min=float(targets.get("kmoMin") or 0.60),
        sig_min=int(sig_total[0]) if len(sig_total) > 0 else 2,
        sig_max=int(sig_total[1]) if len(sig_total) > 1 else 4,
        seed_search=int(raw.get("seedSearch") or 40),
    )


def strategy_from_legacy(payload: Mapping[str, Any]) -> Strategy:
    """把旧界面提交的表单翻译成方案对象。"""
    base = default_strategy()
    questions = payload.get("questions") or []

    items: dict[int, ItemRule] = {}
    texts: list[str] = []
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        try:
            number = int(question.get("id") or question.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        kind = _kind(question.get("type"))
        options = [o for o in (question.get("options") or []) if isinstance(o, Mapping)]
        weights = _weights(options) if kind.has_options else ()
        found, text_weights = _texts(options, question)
        texts.extend(found)
        items[number] = ItemRule(
            weights=weights,
            skew=SkewRule(),
            texts=found,
            text_weights=text_weights,
            unique_group=f"q{number}" if _is_unique(options, question) else "",
            matrix_weights=_matrix_weights(question) if kind is ItemKind.MATRIX else (),
        )

    total = int(payload.get("targetCount") or base.total)
    # 倍速上限 12：太快容易触发风控，前端已限制，这里再兜一层
    concurrency = max(1, min(12, int(payload.get("speedMultiplier") or 1)))

    strategy = Strategy(
        total=max(1, total),
        concurrency=concurrency,
        default_rule=base.default_rule,
        items=items,
        texts=TextFallback(values=tuple(dict.fromkeys(texts)) or base.texts.values),
        targets=base.targets,
        pacing=_pacing(payload),
        channel=_channel(payload),
        quota=base.quota,
        validity=_validity(payload),
        synth=_synth(payload),
        goal=_goal(payload, _synth(payload), _validity(payload)),
    )
    strategy.validate()
    return strategy
