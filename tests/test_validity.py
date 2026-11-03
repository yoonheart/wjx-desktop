from __future__ import annotations

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import Strategy, TargetSpec, ValidityConfig
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.controller import build_matrix, evaluate
from wjxdesk.engine.rng import derive
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.engine.validity import (
    build_plan,
    draw_factors,
    flip_index,
    parse_correlation,
    parse_dimensions,
    parse_numbers,
)


def _opts(n: int = 5) -> tuple[Option, ...]:
    return tuple(Option(i, f"L{i}") for i in range(1, n + 1))


def _survey(count: int = 6) -> Survey:
    return Survey(
        token="t",
        items=tuple(
            Item(number=i, kind=ItemKind.SCALE, options=_opts(), low=1, high=5)
            for i in range(1, count + 1)
        ),
    )


def test_parse_number_specs() -> None:
    assert parse_numbers("1-3,7") == {1, 2, 3, 7}
    assert parse_numbers("5+6+8") == {5, 6, 8}
    assert parse_numbers("") == set()


def test_parse_dimensions_and_correlation() -> None:
    dims = parse_dimensions("1-3;4-6")
    assert dims == {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1}

    matrix = parse_correlation("0.5", 2)
    assert matrix[0][1] == 0.5 and matrix[1][0] == 0.5
    assert matrix[0][0] == 1.0


def test_flip_index() -> None:
    assert [flip_index(i, 5) for i in range(1, 6)] == [5, 4, 3, 2, 1]


def test_reverse_scoring_keeps_alpha_high() -> None:
    """反向题必须在统计前反向编码，否则整卷 α 会被拖低。"""
    survey = _survey(6)
    strategy = Strategy(
        validity=ValidityConfig(enabled=True, alpha_target=0.8, dimensions="1-3;4-6", reverse="3")
    )
    plan = build_plan(survey, strategy.validity)
    assert plan.reverse == frozenset({3})

    sheets = []
    for unit in range(1, 41):
        dims = draw_factors(plan, derive(7, "f", unit))
        factors = {n: dims[plan.dimension_of(n)] for n in plan.dimensions}
        sheets.append(
            generate_sheet(
                survey, strategy, 7, unit, latent=0.8, factors=factors, reverse=plan.reverse
            )
        )

    recoded = evaluate(survey, sheets, TargetSpec(), plan.dimensions, plan.reverse)
    raw_only = evaluate(survey, sheets, TargetSpec(), plan.dimensions, frozenset())

    assert recoded.alpha > raw_only.alpha
    assert recoded.alpha > 0.6


def test_build_matrix_matches_reverse_recoding() -> None:
    survey = _survey(2)
    strategy = Strategy()

    sheets = [
        generate_sheet(survey, strategy, 1, unit, latent=0.6) for unit in range(1, 12)
    ]
    plain = build_matrix(survey, sheets)
    flipped = build_matrix(survey, sheets, frozenset({1}))

    for raw_row, rev_row in zip(plain, flipped):
        assert rev_row[0] == (5 + 1) - raw_row[0]
        assert rev_row[1] == raw_row[1]


def test_dimension_factors_are_correlated_by_design() -> None:
    plan = build_plan(_survey(6), ValidityConfig(enabled=True, dimensions="1-3;4-6", correlation="0.8"))
    pairs = [draw_factors(plan, derive(3, "x", i)) for i in range(200)]

    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / len(a)
    sd_a = (sum((x - mean_a) ** 2 for x in a) / len(a)) ** 0.5
    sd_b = (sum((y - mean_b) ** 2 for y in b) / len(b)) ** 0.5
    assert cov / (sd_a * sd_b) > 0.6
