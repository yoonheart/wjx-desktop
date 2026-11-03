from __future__ import annotations

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.goal import ResearchGoal
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.goal_solver import GoalSolver


def _opts(n: int = 5) -> tuple[Option, ...]:
    return tuple(Option(i, f"O{i}") for i in range(1, n + 1))


def _survey() -> Survey:
    items = [
        Item(number=i, kind=ItemKind.SCALE, options=_opts(), low=1, high=5) for i in range(1, 7)
    ]
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
