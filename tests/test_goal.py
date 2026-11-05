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


def test_outcome_excluded_from_reliability() -> None:
    """因变量不能进 α 矩阵：结局项不应出现在维度里。"""
    survey = _survey(matrix=True)
    goal = _matrix_goal(outcome_items=(6,))
    solver = GoalSolver(survey, Strategy(total=20, seed=7, goal=goal))

    assert 6 not in solver.dimensions[0] + solver.dimensions[1]
    assert 6 not in solver._analysis_numbers()


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
    assert solver.dimensions == ((501, 502, 503, 504), (601, 603, 604), (7,))
    assert 602 not in solver._analysis_numbers()


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


