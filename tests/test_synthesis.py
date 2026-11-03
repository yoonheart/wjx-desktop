from __future__ import annotations

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import Strategy, SynthesisConfig
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.synthesis import Synthesizer, significance


def _opts(n: int = 5) -> tuple[Option, ...]:
    return tuple(Option(i, f"L{i}") for i in range(1, n + 1))


def _survey() -> Survey:
    items = [
        Item(number=i, kind=ItemKind.SCALE, options=_opts(), low=1, high=5) for i in range(1, 5)
    ]
    items.append(Item(number=5, kind=ItemKind.SINGLE, options=_opts(3)))
    return Survey(token="t", items=tuple(items))


def _strategy() -> Strategy:
    return Strategy(
        total=20,
        seed=20261102,
        synth=SynthesisConfig(
            enabled=True,
            outcome_question=5,
            positive_options=(3,),
            label="满意",
            predictors=(1, 2, 3, 4),
            effects={1: 0.6, 2: -0.4, 3: 0.5, 4: 0.3},
            alpha_min=0.70,
            kmo_min=0.55,
            sig_min=1,
            sig_max=4,
        ),
    )


def test_significance_detects_strong_relation() -> None:
    scores = [float(i) for i in range(30)]
    outcome = [1.0 if i >= 20 else 0.0 for i in range(30)]
    assert significance(scores, outcome) < 0.01


def test_synthesis_produces_batch_with_targets() -> None:
    survey = _survey()
    synth = Synthesizer(survey, _strategy())
    assert synth.usable

    batch, report = synth.prepare(20)
    assert len(batch) >= 20
    assert report.alpha >= 0.70
    assert 1 <= report.significant <= 4

    positives = sum(1 for s in batch if (s.get(5).picks or (0,))[0] == 3)
    assert 0 < positives < len(batch)


def test_synthesis_is_reproducible() -> None:
    survey = _survey()
    first, _ = Synthesizer(survey, _strategy()).prepare(10)
    second, _ = Synthesizer(survey, _strategy()).prepare(10)
    assert [s.to_plain() for s in first] == [s.to_plain() for s in second]


def test_disabled_synthesis_returns_empty() -> None:
    synth = Synthesizer(_survey(), Strategy())
    assert not synth.usable
    batch, report = synth.prepare(5)
    assert batch == []
    assert "未启用" in report.notes[0]
