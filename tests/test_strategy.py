from __future__ import annotations

import pytest

from wjxdesk.domain.enums import PacingProfile, SkewShape
from wjxdesk.domain.strategy import Strategy, default_strategy


def test_roundtrip_preserves_values() -> None:
    original = default_strategy()
    again = Strategy.from_dict(original.to_dict())
    assert again.to_dict() == original.to_dict()


def test_roundtrip_with_item_rules() -> None:
    raw = default_strategy().to_dict()
    raw["items"] = {"3": {"skew": {"shape": SkewShape.LEFT.value, "strength": 1.6}, "texts": ["甲", "乙"]}}
    raw["pacing"] = {"profile": PacingProfile.FAST.value, "median_gap_s": 8}
    strategy = Strategy.from_dict(raw)

    assert strategy.rule_for(3).skew.shape is SkewShape.LEFT
    assert strategy.pacing.profile is PacingProfile.FAST
    assert strategy.pacing.median_gap_s == 8.0
    assert strategy.to_dict()["items"]["3"]["skew"]["strength"] == 1.6


def test_invalid_values_raise() -> None:
    with pytest.raises(ValueError):
        Strategy(total=0).validate()
    with pytest.raises(ValueError):
        Strategy(concurrency=99).validate()

    raw = default_strategy().to_dict()
    raw["pacing"]["hour_weights"] = [1.0] * 10
    with pytest.raises(ValueError):
        Strategy.from_dict(raw)


def test_unknown_future_version_is_rejected() -> None:
    raw = default_strategy().to_dict()
    raw["version"] = 999
    with pytest.raises(ValueError):
        Strategy.from_dict(raw)
