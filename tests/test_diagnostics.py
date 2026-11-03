from __future__ import annotations

import math

from wjxdesk.engine.diagnostics import (
    chi2_sf,
    cronbach_alpha,
    determinant,
    dominant_share,
    inverse,
    kmo,
    report,
)


def test_alpha_of_identical_columns_is_one() -> None:
    matrix = [[1, 2, 3, 4], [2, 3, 4, 5], [3, 4, 5, 6], [4, 5, 6, 7]]
    matrix = [row + [row[0]] for row in matrix]
    assert cronbach_alpha(matrix) > 0.99


def test_alpha_of_independent_columns_is_low() -> None:
    matrix = [[1, 5], [2, 1], [3, 4], [4, 2], [5, 3], [1, 1], [2, 5], [3, 2]]
    assert cronbach_alpha(matrix) < 0.5


def test_kmo_and_dominant_in_range() -> None:
    matrix = [
        [i % 5 + 1, (i * 2) % 5 + 1, (i * 3) % 5 + 1, (i * 4) % 5 + 1]
        for i in range(30)
    ]
    value = kmo(matrix)
    assert 0.0 <= value <= 1.0
    assert 0.0 <= dominant_share(matrix) <= 1.0


def test_chi2_tail_matches_known_value() -> None:
    assert abs(chi2_sf(3.841, 1) - 0.05) < 0.005
    assert chi2_sf(0.0, 3) == 1.0


def test_inverse_and_determinant() -> None:
    matrix = [[4.0, 1.0], [2.0, 3.0]]
    assert abs(determinant(matrix) - 10.0) < 1e-9
    inv = inverse(matrix)
    assert abs(inv[0][0] - 0.3) < 1e-9
    assert abs(inv[1][1] - 0.4) < 1e-9


def test_report_flags_small_sample() -> None:
    matrix = [[1, 2, 3], [2, 3, 4], [3, 4, 5]]
    result = report(matrix)
    assert result.samples == 3
    assert result.items == 3
    assert any("样本量" in note for note in result.notes)
    payload = result.to_dict()
    assert math.isfinite(float(payload["alpha"]))
