from __future__ import annotations

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.goal import ResearchGoal
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.goal_solver import GoalSolver


def _opts(n: int = 5) -> tuple[Option, ...]:
    return tuple(Option(i, f"O{i}") for i in range(1, n + 1))


def _survey(*, matrix: bool = False) -> Survey:
    items = [
        Item(number=i, kind=ItemKind.SCALE, options=_opts(), low=1, high=5) for i in range(1, 7)
    ]
    if matrix:
        items.append(
            Item(
                number=7,
                kind=ItemKind.MATRIX,
                options=_opts(),
                rows=_opts(3),
                low=1,
                high=5,
            )
        )
        items.append(Item(number=8, kind=ItemKind.SINGLE, options=_opts(5)))
    else:
        items.append(Item(number=7, kind=ItemKind.SINGLE, options=_opts(5)))
    return Survey(token="t", items=tuple(items))


def _goal(**kwargs) -> ResearchGoal:
    base = dict(
        enabled=True,
        alpha_target=0.85,
        dimensions=((1, 2, 3), (4, 5, 6)),
        kmo_target=0.70,
        outcome_question=7,
        positive_options=(5,),
        predictors=(1, 4),
        directions={1: "+", 4: "+"},
        significant_required=2,
        event_rate=0.6,
        attempts=15,
    )
    base.update(kwargs)
    return ResearchGoal(**base)


def test_goal_rejects_bad_config() -> None:
    try:
        ResearchGoal(enabled=True, alpha_target=1.5).validate()
    except ValueError as exc:
        assert "α" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当拒绝非法的 α 目标")


def test_goal_roundtrip() -> None:
    goal = _goal()
    again = ResearchGoal.from_dict(goal.to_dict())
    assert again.alpha_target == goal.alpha_target
    assert again.dimensions == goal.dimensions
    assert again.directions == goal.directions


def test_solver_hits_reliability_and_significance() -> None:
    survey = _survey()
    strategy = Strategy(total=40, seed=20261102, goal=_goal())
    sheets, report = GoalSolver(survey, strategy).solve(40)

    assert len(sheets) == 40
    assert report.alpha >= 0.83, report.notes
    assert report.kmo >= 0.67, report.notes
    assert report.significant == 2
    assert all(p.ok for p in report.predictors)
    assert report.achieved


def test_solver_keeps_event_rate() -> None:
    survey = _survey()
    strategy = Strategy(total=20, seed=7, goal=_goal(event_rate=0.6))
    sheets, _ = GoalSolver(survey, strategy).solve(20)
    positives = sum(1 for s in sheets if (s.get(7).picks or (0,))[0] == 5)
    assert positives == 12


def test_solver_warns_on_small_sample() -> None:
    survey = _survey()
    strategy = Strategy(total=10, seed=11, goal=_goal())
    _, report = GoalSolver(survey, strategy).solve(10)
    assert any("建议" in note for note in report.notes)


def test_solver_without_goal_is_noop() -> None:
    survey = _survey()
    sheets, report = GoalSolver(survey, Strategy(total=5)).solve(5)
    assert sheets == []
    assert "未启用" in report.notes[0]


# --------------------------------------------------------------------------- 结局形态


def _matrix_goal(**kwargs) -> ResearchGoal:
    base = dict(
        enabled=True,
        alpha_target=0.85,
        dimensions=((1, 2, 3), (4, 5, 6)),
        kmo_target=0.70,
        predictors=(1, 4),
        directions={1: "+", 4: "+"},
        significant_required=2,
        attempts=12,
    )
    base.update(kwargs)
    return ResearchGoal(**base)


def test_outcome_matrix_row_is_continuous() -> None:
    """矩阵的一行可以直接当结局，按连续分构造，方向可控。"""
    survey = _survey(matrix=True)
    goal = _matrix_goal(outcome_items=(702,))
    sheets, report = GoalSolver(survey, Strategy(total=40, seed=2026, goal=goal)).solve(40)

    assert report.achieved, report.notes
    assert report.alpha >= 0.83
    assert report.significant == 2
    rows = [dict(sheet.get(7).matrix) for sheet in sheets]
    assert all(2 in row for row in rows), "每一份都要有第 2 行的答案"
    assert len({row[2] for row in rows}) > 1, "取值不能退化成常数"


def test_outcome_composite_three_rows() -> None:
    """多维因变量：矩阵多行取均值，等价于一个构念得分。"""
    survey = _survey(matrix=True)
    goal = _matrix_goal(
        outcome_items=(701, 702, 703), outcome_aggregate="mean"
    )
    sheets, report = GoalSolver(survey, Strategy(total=40, seed=2026, goal=goal)).solve(40)

    assert report.achieved, report.notes
    rows = [dict(sheet.get(7).matrix) for sheet in sheets]
    flat = sum(1 for row in rows if len(set(row.values())) == 1)
    assert flat < len(rows) // 3, "三行完全相同会被当成直线作答，必须加抖动"


def test_outcome_scale_question_is_continuous() -> None:
    """量表题当结局：不再被静默跳过，而是按保序重排构造。"""
    survey = _survey(matrix=True)
    goal = _matrix_goal(outcome_items=(6,))
    sheets, report = GoalSolver(survey, Strategy(total=40, seed=2026, goal=goal)).solve(40)

    assert report.achieved, report.notes
    values = [sheet.get(6).scale for sheet in sheets]
    assert len(set(values)) > 2, "结局要是连续的，不能只有一两个档位"


def test_outcome_stays_inside_its_construct() -> None:
    """因变量属于某个构念，必须和同构念的题一起进 α / 因子分析。

    旧实现把结局项整体剔除出信度矩阵，导致它在 EFA 里"不属于自己的构念"；
    新架构里结局只是一个被标记的构念，不再特殊对待。
    """
    survey = _survey(matrix=True)
    goal = _matrix_goal(outcome_items=(6,))
    solver = GoalSolver(survey, Strategy(total=20, seed=7, goal=goal))

    assert 6 in solver._analysis_numbers()
    owner = [group for group in solver.dimensions if 6 in group]
    assert owner, "结局项必须归属某个构念"



def test_legacy_outcome_question_still_works() -> None:
    """旧方案只有 outcome_question，读进来等价于 outcome_items=[n]。"""
    goal = ResearchGoal.from_dict(
        {
            "enabled": True,
            "outcome_question": 7,
            "positive_options": [5],
            "predictors": [1, 4],
        }
    )
    assert goal.outcome.items == (7,)
    assert goal.outcome.aggregate == "single"


def test_config_v1_migrates_to_v2() -> None:
    """v1 的 goal.outcome_question 自动搬进 outcome_items。"""
    from wjxdesk.domain.strategy import CONFIG_VERSION, default_strategy

    data = default_strategy().to_dict()
    data["version"] = 1
    data["goal"] = {
        "enabled": True,
        "outcome_question": 7,
        "positive_options": [5],
        "predictors": [1],
    }
    strategy = Strategy.from_dict(data)
    assert CONFIG_VERSION >= 2
    assert strategy.goal.outcome.items == (7,)


def test_outcome_and_predictor_overlap_rejected() -> None:
    goal = _matrix_goal(outcome_items=(701,), predictors=(701, 4))
    try:
        goal.validate()
    except ValueError as exc:
        assert "重复" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("结局项与自变量重复应当报错")


# --------------------------------------------------------------------------- 自动分维


def _two_construct_survey() -> Survey:
    """两个矩阵构念 + 一道独立量表，模拟任意问卷的常见结构。"""
    items = [
        Item(number=5, kind=ItemKind.MATRIX, options=_opts(), rows=_opts(4), low=1, high=5),
        Item(number=6, kind=ItemKind.MATRIX, options=_opts(), rows=_opts(4), low=1, high=5),
        Item(number=7, kind=ItemKind.SCALE, options=_opts(), low=1, high=5),
        Item(number=8, kind=ItemKind.SINGLE, options=_opts()),
    ]
    return Survey(token="t", items=tuple(items))


def test_auto_dimensions_split_by_matrix_question() -> None:
    """没配维度时按题型自动分维：一道矩阵一维，量表题各自一维。"""
    goal = ResearchGoal(
        enabled=True,
        alpha_target=0.85,
        kmo_target=0.70,
        outcome_items=(602,),
        predictors=(501, 601),
        directions={501: "+", 601: "-"},
        significant_required=1,
        attempts=12,
    )
    solver = GoalSolver(_two_construct_survey(), Strategy(total=20, seed=5, goal=goal))
    assert solver.dimensions == ((501, 502, 503, 504), (601, 602, 603, 604), (7,))
    assert 602 in solver._analysis_numbers()


def test_auto_dimensions_raise_kmo_and_keep_alpha() -> None:
    """自动分维后每个维度 α 达标、KMO 也达标（KMO 现在真的参与判断）。"""
    goal = ResearchGoal(
        enabled=True,
        alpha_target=0.85,
        kmo_target=0.70,
        outcome_items=(602,),
        predictors=(501, 601),
        directions={501: "+", 601: "-"},
        significant_required=1,
        attempts=20,
    )
    sheets, report = GoalSolver(
        _two_construct_survey(), Strategy(total=120, seed=5, goal=goal)
    ).solve(120)

    assert report.alpha >= 0.83, report.notes
    assert report.kmo >= 0.67, report.notes
    assert report.achieved, report.notes
    assert len(sheets) == 120


def test_kmo_target_is_enforced() -> None:
    """KMO 低于目标时不能判达标：以前只写进 notes，从不参与 achieved。"""
    goal = ResearchGoal(
        enabled=True,
        alpha_target=0.85,
        kmo_target=0.99,  # 现实中到不了
        outcome_items=(602,),
        predictors=(501, 601),
        significant_required=1,
        attempts=3,
    )
    _, report = GoalSolver(
        _two_construct_survey(), Strategy(total=30, seed=5, goal=goal)
    ).solve(30)
    assert not report.achieved
    assert report.kmo < 0.99


# --------------------------------------------------------------------------- 测量引擎


def _five_construct_survey() -> Survey:
    """5 个矩阵构念（含因变量）+ 4 道人口学题，贴近真实问卷。"""
    items = [Item(number=i, kind=ItemKind.SINGLE, options=_opts(4)) for i in range(1, 5)]
    for q in range(5, 10):
        items.append(
            Item(number=q, kind=ItemKind.MATRIX, options=_opts(), rows=_opts(4), low=1, high=5)
        )
    return Survey(token="t", items=tuple(items))


def _path_goal(**kwargs) -> ResearchGoal:
    base = dict(
        enabled=True,
        alpha_target=0.85,
        kmo_target=0.70,
        attempts=6,
        outcome_items=(901, 902, 903, 904),
        outcome_aggregate="mean",
        predictors=(501, 601, 701, 801),
        directions={501: "+", 601: "-", 701: "-", 801: "-"},
        significant_required=4,
    )
    base.update(kwargs)
    return ResearchGoal(**base)


def test_signed_paths_are_negative_when_direction_is_minus() -> None:
    """核心能力：方向写 - 时，数据里必须真的出现负相关。

    旧实现只有一个正的 dimension_correlation，永远造不出负向路径。
    """
    survey = _five_construct_survey()
    sheets, report = GoalSolver(
        survey, Strategy(total=200, seed=20261105, goal=_path_goal())
    ).solve(200)

    assert report.achieved, report.notes
    by_number = {item.number: item for item in report.predictors}
    assert by_number[501].correlation > 0.20
    for number in (601, 701, 801):
        assert by_number[number].correlation < -0.15, (number, by_number[number])


def test_all_constructs_meet_alpha() -> None:
    survey = _five_construct_survey()
    _, report = GoalSolver(
        survey, Strategy(total=120, seed=7, goal=_path_goal())
    ).solve(120)
    assert report.alpha_by_construct
    for name, value in report.alpha_by_construct:
        assert 0.78 <= value <= 0.95, (name, value)


def test_impossible_correlation_reports_shrinkage() -> None:
    """相关矩阵自相矛盾时必须报告，不能静默把结构清零。"""
    goal = _path_goal(
        dimension_correlations={"0-1": -0.9, "0-2": -0.9, "1-2": 0.9},
        attempts=2,
    )
    _, report = GoalSolver(
        _five_construct_survey(), Strategy(total=60, seed=11, goal=goal)
    ).solve(60)
    assert any("收缩" in note for note in report.notes), report.notes


def test_marginals_are_exact() -> None:
    """给了选项占比就按它生成：只允许落在前两档。"""
    goal = _path_goal(
        marginals={501: (0.5, 0.5, 0.0, 0.0, 0.0)},
        attempts=2,
    )
    sheets, _ = GoalSolver(
        _five_construct_survey(), Strategy(total=80, seed=3, goal=goal)
    ).solve(80)
    levels = {dict(sheet.get(5).matrix)[1] for sheet in sheets}
    assert levels <= {1, 2}, levels


def test_generation_is_reproducible() -> None:
    """同一 seed 两次生成的答案必须完全一致。"""
    survey = _five_construct_survey()
    first, _ = GoalSolver(
        survey, Strategy(total=40, seed=99, goal=_path_goal())
    ).solve(40)
    second, _ = GoalSolver(
        survey, Strategy(total=40, seed=99, goal=_path_goal())
    ).solve(40)
    assert [sheet.to_plain() for sheet in first] == [sheet.to_plain() for sheet in second]





def test_moderation_interaction_becomes_significant() -> None:
    """调节：把一条单选题声明为有序变量后，交互项要能显著。"""
    goal = _path_goal(
        ordinal_items=(4,),
        moderation={"focal": 501, "moderator": 4, "effect": 0.30},
    )
    _, report = GoalSolver(
        _five_construct_survey(), Strategy(total=250, seed=20261105, goal=goal)
    ).solve(250)

    assert report.interaction is not None
    focal, moderator, beta, p_value = report.interaction
    assert (focal, moderator) == (501, 4)
    assert p_value < 0.05, (beta, p_value)
    assert report.achieved, report.notes


def test_reverse_item_is_flipped_in_statistics() -> None:
    """反向题：原始答案与同维其它题负相关，翻转后 α 依然达标。"""
    survey = Survey(
        token="t",
        items=(
            Item(number=5, kind=ItemKind.MATRIX, options=_opts(), rows=_opts(4), low=1, high=5),
        ),
    )
    goal = ResearchGoal(
        enabled=True,
        alpha_target=0.85,
        kmo_target=0.70,
        dimensions=((501, 502, 503, 504),),
        reverse=(503,),
        attempts=6,
    )
    sheets, report = GoalSolver(survey, Strategy(total=120, seed=21, goal=goal)).solve(120)
    assert report.alpha >= 0.80, report.notes

    def column(row: int) -> list[float]:
        return [float(dict(sheet.get(5).matrix)[row]) for sheet in sheets]

    def corr(a: list[float], b: list[float]) -> float:
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        db = sum((y - mb) ** 2 for y in b) ** 0.5
        return num / (da * db) if da and db else 0.0

    assert corr(column(1), column(3)) < -0.2, "反向题的原始分应与同维题负相关"
