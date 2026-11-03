from __future__ import annotations

from wjxdesk.domain.enums import ItemKind, SkewShape
from wjxdesk.domain.strategy import ItemRule, SkewRule, Strategy
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.sampling import generate_sheet, shape_weights


def _options(count: int) -> tuple[Option, ...]:
    return tuple(Option(index=i, label=f"O{i}") for i in range(1, count + 1))


def test_shape_weights_direction() -> None:
    right = shape_weights(5, SkewRule(SkewShape.RIGHT, 2.0))
    left = shape_weights(5, SkewRule(SkewShape.LEFT, 2.0))
    center = shape_weights(5, SkewRule(SkewShape.CENTER, 2.0))
    bimodal = shape_weights(5, SkewRule(SkewShape.BIMODAL, 2.0))

    assert right[0] > right[-1]
    assert left[-1] > left[0]
    assert center[2] == max(center)
    assert bimodal[0] > bimodal[2] and bimodal[-1] > bimodal[2]


def test_uniform_strength_zero_equals_flat() -> None:
    assert shape_weights(4, SkewRule(SkewShape.RIGHT, 0.0)) == [1.0, 1.0, 1.0, 1.0]


def _survey() -> Survey:
    return Survey(
        token="t",
        items=(
            Item(number=1, kind=ItemKind.SINGLE, options=_options(4)),
            Item(number=2, kind=ItemKind.MULTI, options=_options(6)),
            Item(number=3, kind=ItemKind.SCALE, options=_options(5), low=1, high=5),
            Item(
                number=4,
                kind=ItemKind.MATRIX,
                options=_options(5),
                rows=tuple(Option(i, f"r{i}") for i in range(1, 5)),
            ),
            Item(number=5, kind=ItemKind.BLANK, blanks=2),
        ),
    )


def test_generation_is_reproducible() -> None:
    strategy = Strategy(total=3, concurrency=1)
    a = generate_sheet(_survey(), strategy, seed=42, unit=1)
    b = generate_sheet(_survey(), strategy, seed=42, unit=1)
    assert a.to_plain() == b.to_plain()


def test_multi_respects_bounds() -> None:
    survey = _survey()
    strategy = Strategy(items={2: ItemRule(weights=(1, 2, 3, 4, 5, 6))})
    for unit in range(1, 60):
        sheet = generate_sheet(survey, strategy, seed=7, unit=unit)
        picks = sheet.picks_of(2)
        low, high = survey.item(2).choice_bounds()
        assert low <= len(picks) <= high


def test_latent_raises_consistency() -> None:
    """潜变量越高，同一份答卷内部的量表题越同向。"""
    survey = Survey(
        token="t",
        items=tuple(
            Item(number=i, kind=ItemKind.SCALE, options=_options(5), low=1, high=5)
            for i in range(1, 7)
        ),
    )
    strategy = Strategy(total=40, concurrency=1)

    def spread(latent: float) -> float:
        total = 0.0
        for unit in range(1, 25):
            sheet = generate_sheet(survey, strategy, seed=11, unit=unit, latent=latent)
            values = [sheet.get(i).scale for i in range(1, 7) if sheet.get(i)]
            total += max(values) - min(values)
        return total / 24

    assert spread(0.9) < spread(0.05)


def test_matrix_never_straight_line() -> None:
    survey = _survey()
    strategy = Strategy()
    for unit in range(1, 40):
        sheet = generate_sheet(survey, strategy, seed=3, unit=unit)
        answer = sheet.get(4)
        assert len({col for _, col in answer.matrix}) > 1
