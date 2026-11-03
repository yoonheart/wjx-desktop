from __future__ import annotations

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.survey import Gate, Item, Option, Survey
from wjxdesk.engine.constraints import check, repair


def _options(count: int) -> tuple[Option, ...]:
    return tuple(Option(index=i, label=f"O{i}") for i in range(1, count + 1))


def test_matrix_straight_line_is_detected_and_fixed() -> None:
    survey = Survey(
        token="t",
        items=(
            Item(
                number=1,
                kind=ItemKind.MATRIX,
                options=_options(5),
                rows=(Option(1, "r1"), Option(2, "r2"), Option(3, "r3")),
            ),
        ),
    )
    sheet = AnswerSheet(seed=1, unit=1, answers={1: ItemAnswer(number=1, matrix=((1, 3), (2, 3), (3, 3)))})

    codes = [v.code for v in check(survey, sheet)]
    assert "straight_line" in codes

    fixed, remaining = repair(survey, sheet)
    assert not remaining
    assert len({col for _, col in fixed.get(1).matrix}) > 1


def test_hidden_item_with_answer_is_flagged() -> None:
    survey = Survey(
        token="t",
        items=(
            Item(number=1, kind=ItemKind.SINGLE, options=_options(3)),
            Item(number=2, kind=ItemKind.SINGLE, options=_options(3), gate=Gate(1, (2,))),
        ),
    )
    sheet = AnswerSheet(
        seed=1,
        unit=1,
        answers={1: ItemAnswer(number=1, picks=(1,)), 2: ItemAnswer(number=2, picks=(2,))},
    )
    assert "hidden_answered" in [v.code for v in check(survey, sheet)]

    fixed, remaining = repair(survey, sheet)
    assert fixed.get(2) is None
    assert remaining == []


def test_multi_bounds_are_enforced() -> None:
    survey = Survey(
        token="t",
        items=(Item(number=1, kind=ItemKind.MULTI, options=_options(5), max_choices=2),),
    )
    sheet = AnswerSheet(seed=1, unit=1, answers={1: ItemAnswer(number=1, picks=(1, 2, 3, 4))})

    codes = [v.code for v in check(survey, sheet)]
    assert "choice_bounds" in codes

    fixed, _ = repair(survey, sheet)
    assert len(fixed.get(1).picks) <= 2


def test_required_blank_is_flagged() -> None:
    survey = Survey(token="t", items=(Item(number=1, kind=ItemKind.BLANK, required=True, blanks=1),))
    sheet = AnswerSheet(seed=1, unit=1, answers={})
    codes = [v.code for v in check(survey, sheet)]
    assert "missing" in codes and "empty_text" in codes
