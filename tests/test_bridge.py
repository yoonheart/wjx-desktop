"""界面 → 后端的配置桥接：新 goal 必须原样到 Strategy。

以前 goal 只写进 localStorage，没随 /api/brush 下发，
后端 _goal() 只能从旧 synth/validity 字段猜，
相关矩阵、调节、反向题、有序题全部丢失。这组测试专门守住这条链路。
"""

from __future__ import annotations

import re
from pathlib import Path

from wjxdesk.domain.enums import ItemKind
from wjxdesk.web.legacy import strategy_from_legacy

ROOT = Path(__file__).resolve().parents[1]


def _payload(goal: dict) -> dict:
    return {
        "targetCount": 120,
        "speedMultiplier": 4,
        "questions": [
            {
                "id": 5,
                "type": "矩阵题",
                "options": [{"index": i, "text": f"O{i}"} for i in range(1, 6)],
                "rows": [{"index": i} for i in range(1, 5)],
            }
        ],
        # 旧字段故意留着，验证新 goal 优先
        "validityEnabled": False,
        "synth": None,
        "goal": goal,
    }


def test_new_goal_is_used_instead_of_legacy_fields() -> None:
    goal = {
        "enabled": True,
        "alpha_target": 0.85,
        "kmo_target": 0.70,
        "dimensions": [[501, 502, 503, 504]],
        "dimension_correlations": {"0-1": -0.45},
        "target_effect": 0.50,
        "ordinal_items": [10],
        "moderation": {"focal": 501, "moderator": 10, "effect": 0.30},
        "reverse": [503],
        "outcome_items": [901],
        "predictors": [501],
        "directions": {"501": "+"},
        "significant_required": 1,
    }
    strategy = strategy_from_legacy(_payload(goal))

    assert strategy.goal.enabled
    assert strategy.goal.alpha_target == 0.85
    assert strategy.goal.dimension_correlations == {"0-1": -0.45}
    assert strategy.goal.target_effect == 0.50
    assert strategy.goal.ordinal_items == (10,)
    assert strategy.goal.moderation["effect"] == 0.30
    assert strategy.goal.reverse == (503,)
    assert strategy.goal.outcome.items == (901,)


def test_frontend_sends_goal_in_brush_request() -> None:
    """静态断言：开刷请求里必须带 goal，防止再次只写 localStorage。"""
    source = (ROOT / "frontend" / "js" / "analysis.js").read_text(encoding="utf-8")
    block = source[source.find("const requestData = {"):]
    block = block[: block.find("};") + 2]
    assert re.search(r"\bgoal:\s*newGoal\b", block), "请求体里缺少 goal"


def test_legacy_synth_validation_is_skipped_when_goal_exists() -> None:
    source = (ROOT / "frontend" / "js" / "analysis.js").read_text(encoding="utf-8")
    assert "if (synthSpec && !newGoal)" in source, "旧合成答卷校验仍会拦住新数据目标"


def test_reverse_items_survive_the_bridge() -> None:
    """界面上填的「反向题」必须原样传到后端，否则翻转不会生效。"""
    goal = {
        "enabled": True,
        "dimensions": [[501, 502, 503, 504]],
        "reverse": [503, 504],
        "outcome_items": [901],
        "outcome_aggregate": "mean",
        "predictors": [501],
        "directions": {"501": "+"},
    }
    strategy = strategy_from_legacy(_payload(goal))
    assert strategy.goal.reverse == (503, 504)


def test_frontend_payload_carries_reverse() -> None:
    """静态断言：面板取值时要把反向题写进 payload（否则界面上填了也没用）。"""
    source = (ROOT / "frontend" / "js" / "goal-panel.js").read_text(encoding="utf-8")
    assert "reverse: parseRefList(" in source, "payload 里缺少 reverse"


def test_proxy_config_error_is_a_pool_error() -> None:
    """配置类错误要能被单独捕获，才能立刻停而不是重试 40 次。"""
    from wjxdesk.domain.errors import ExitPoolEmpty, ProxyConfigError

    assert issubclass(ProxyConfigError, ExitPoolEmpty)
    assert ProxyConfigError("x").args[0] == "x"


def test_analysis_helpers_are_defined() -> None:
    """调用到的工具函数必须真的有定义。

    analysis.js 里 `syncProxyControls()` / `refreshCheckButton()` 曾经只写了调用、
    没写定义，解析问卷时直接 ReferenceError 整页报错。这里做一次低成本兜底。
    """
    source = (ROOT / "frontend" / "js" / "analysis.js").read_text(encoding="utf-8")
    for name in ("syncProxyControls", "refreshCheckButton"):
        assert f"function {name}(" in source, f"{name} 只被调用、没有定义"


def test_explicit_correlation_beats_direction() -> None:
    """# 相关 里写过的构念对优先于 # 影响因素 的方向。

    这是提示词里最容易配错的地方：只把方向改成负号，若相关里已有正值，
    方向不会生效。这里把该约定固化成测试。
    """
    from wjxdesk.domain.enums import ItemKind
    from wjxdesk.domain.goal import ResearchGoal
    from wjxdesk.domain.measurement import resolve_model
    from wjxdesk.domain.survey import Item, Option, Survey

    def opts(n=5):
        return tuple(Option(i, f"O{i}") for i in range(1, n + 1))

    survey = Survey(
        token="t",
        items=(
            Item(number=5, kind=ItemKind.MATRIX, options=opts(), rows=opts(4), low=1, high=5),
            Item(number=9, kind=ItemKind.MATRIX, options=opts(), rows=opts(4), low=1, high=5),
        ),
    )
    goal = ResearchGoal(
        enabled=True,
        dimensions=((501, 502, 503, 504), (901, 902, 903, 904)),
        dimension_correlations={"0-1": 0.60},
        outcome_items=(901, 902, 903, 904),
        outcome_aggregate="mean",
        predictors=(501,),
        directions={501: "-"},  # 只改方向
    )
    model = resolve_model(survey, goal)
    assert model.correlation(0, 1) == 0.60, "显式相关性应压过方向"


def test_prompt_covers_critical_rules() -> None:
    """提示词必须持续覆盖几条硬约束，避免以后被改丢。"""
    source = (ROOT / "frontend" / "js" / "answers-csv.js").read_text(encoding="utf-8")
    for keyword in (
        "相关默认", "一律以它为准", "只做信效度", "改完自检", "没有填空题",
        "最容易忽略", "理论维度数", "不要求写进 # 有序题",
    ):
        assert keyword in source, f"提示词缺少关键约束：{keyword}"


def test_prompt_is_a_generic_template() -> None:
    """提示词是通用模板：不夹带给人类的元指令，也不绑定某一份问卷的目标。"""
    source = (ROOT / "frontend" / "js" / "answers-csv.js").read_text(encoding="utf-8")
    start = source.index("const AI_PROMPT_LINES = [")
    prompt = source[start:source.index("];", start)]
    for meta in ("按你的实际要求替换", "改完删掉这句"):
        assert meta not in prompt, f"模板里混入了给人类的元指令：{meta}"
    assert "- 信度：___" in prompt and "- 效度：___" in prompt
    for bound in ("Cronbach α ≈ 0.85", "KMO ≈ 0.70 以上", "X → M → Y", "901*801"):
        assert bound not in prompt, f"模板绑定了某份问卷的内容：{bound}"


def test_quick_start_explains_template_usage() -> None:
    """给人看的用法/举例放在弹窗上方，不塞进模板。"""
    html = (ROOT / "frontend" / "analysis.html").read_text(encoding="utf-8")
    for keyword in ("通用提示词模板", "例：", "___", "发给 AI", "最容易忽略"):
        assert keyword in html, f"快速上手缺少使用说明：{keyword}"
