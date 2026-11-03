from __future__ import annotations

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer, Stamp
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import ChannelConfig
from wjxdesk.domain.survey import Gate, Item, Option, Survey
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.wjx import MANIFEST_PATH
from wjxdesk.protocol.wjx.encode import WjxEncoder, format_starttime


def _options(count: int) -> tuple[Option, ...]:
    return tuple(Option(index=i, label=f"选项{i}") for i in range(1, count + 1))


def _survey() -> Survey:
    return Survey(
        token="s1",
        title="示例",
        items=(
            Item(number=1, kind=ItemKind.SINGLE, options=_options(3)),
            Item(number=2, kind=ItemKind.MULTI, options=_options(3)),
            Item(number=3, kind=ItemKind.BLANK, blanks=2),
            Item(number=4, kind=ItemKind.SCALE, options=_options(5), low=1, high=5),
            Item(
                number=5,
                kind=ItemKind.MATRIX,
                options=_options(5),
                rows=(Option(1, "行1"), Option(2, "行2")),
            ),
        ),
    )


def _encoder() -> WjxEncoder:
    return WjxEncoder(Manifest.load(MANIFEST_PATH))


def test_blob_covers_every_kind() -> None:
    sheet = AnswerSheet(
        seed=1,
        unit=1,
        answers={
            1: ItemAnswer(number=1, picks=(2,)),
            2: ItemAnswer(number=2, picks=(1, 3)),
            3: ItemAnswer(number=3, texts=("a", "b")),
            4: ItemAnswer(number=4, picks=(3,), scale=3.0),
            5: ItemAnswer(number=5, matrix=((1, 2), (2, 4))),
        },
    )
    assert _encoder().build_blob(_survey(), sheet) == "1$2}2$1|3}3$a^b}4$3}5$1!2,2!4"


def test_text_is_escaped_before_joining() -> None:
    survey = Survey(token="s", items=(Item(number=1, kind=ItemKind.BLANK, blanks=1),))
    sheet = AnswerSheet(seed=1, unit=1, answers={1: ItemAnswer(number=1, texts=("改!进^A$B}C|D<",))})
    blob = _encoder().build_blob(survey, sheet)
    assert blob == "1$改！进ˆAξB｝C¦D＜"


def test_hidden_item_emits_skip_token() -> None:
    survey = Survey(
        token="s",
        items=(
            Item(number=1, kind=ItemKind.SINGLE, options=_options(2)),
            Item(number=2, kind=ItemKind.SINGLE, options=_options(2), gate=Gate(1, (2,))),
        ),
    )
    sheet = AnswerSheet(seed=1, unit=1, answers={1: ItemAnswer(number=1, picks=(1,))})
    assert _encoder().build_blob(survey, sheet) == "1$1}2$-3"


def test_request_carries_expected_params() -> None:
    survey = _survey()
    sheet = AnswerSheet(seed=7, unit=3, answers={1: ItemAnswer(number=1, picks=(1,))})
    stamp = Stamp(started_at=1_700_000_000.0, elapsed_s=95.4, issued_at=1_700_000_095.4)
    request = _encoder().encode(survey, sheet, stamp, ChannelConfig())

    assert request.method == "POST"
    assert request.params["shortid"] == "s1"

    # ktimes 现在就是采样出来的作答时长，校验串取它个位做异或
    ktimes = int(request.params["ktimes"])
    assert ktimes == 95

    nonce = request.params["jqnonce"]
    key = ktimes % 10 or 1
    expected = "".join(chr(ord(ch) ^ key) for ch in nonce)
    assert request.params["jqsign"] == expected

    # 开始时间必须回填到「提交时刻 - 作答时长」，而不是用页面自己的时间
    assert request.params["starttime"] == format_starttime(1_700_000_000.4)
    assert request.params["cst"] == "1700000000400"

    assert "submitdata=" in request.body
    assert request.meta["seal_mode"] == "xor_ktimes"


def test_seal_matches_reference_algorithm() -> None:
    from wjxdesk.protocol.wjx import seal

    nonce = "9ced4b6d-7ebb"
    for ktimes in (1, 6, 10, 13):
        key = ktimes % 10 or 1
        expected = "".join(chr(ord(ch) ^ key) for ch in nonce)
        assert seal.compute("xor_ktimes", nonce=nonce, ktimes=ktimes) == expected


def test_seal_none_returns_empty() -> None:
    from wjxdesk.protocol.wjx import seal

    assert seal.compute("none", nonce="abc", ktimes=3) == ""


def test_stamp_coherence() -> None:
    stamp = Stamp(started_at=100.0, elapsed_s=60.0, issued_at=160.0)
    assert stamp.is_coherent()
    assert not Stamp(started_at=100.0, elapsed_s=60.0, issued_at=100.0).is_coherent()
