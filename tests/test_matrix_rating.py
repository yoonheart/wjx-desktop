"""type=9 矩阵滑块题：解析 / 编码 / 生成，以及答案格式错误的停批判定。

线上样本：https://v.wjx.cn/vm/Q0rhKy2.aspx 第15题
  div#div15[type=9] > table#divRefTab15.matrix-rating
  4 个 tr.rowtitletr（口味/原料/健康可信度/价格合理性）
  4 个 input.ui-slider-input#q15_0..3，min=0 max=100 rowid=1..4
答案串：15$1!85^2!70^3!60^4!90（行间 ^，行内 rowid!value）
"""

from __future__ import annotations

from pathlib import Path

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.enums import FailureKind, ItemKind, Verdict
from wjxdesk.domain.strategy import Strategy
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.protocol.contracts import RawResponse
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.wjx.classify import WjxClassifier
from wjxdesk.protocol.wjx.encode import WjxEncoder
from wjxdesk.protocol.wjx.parse import parse_survey

MANIFEST = Path(__file__).resolve().parents[1] / "wjxdesk" / "protocol" / "wjx" / "manifest.json"

HTML = """
<div class="survey-title">测试问卷</div>
<div class="field ui-field-contain" id="div15" type="9">
  <div class="topicnumber">15.</div>
  <div class="topichtml">因素重要度评价</div>
  <table class="matrix-rating">
    <tr class="rowtitletr"><td>口味</td></tr>
    <tr><td><input class="ui-slider-input" id="q15_0" rowid="1" min="0" max="100"></td></tr>
    <tr class="rowtitletr"><td>原料</td></tr>
    <tr><td><input class="ui-slider-input" id="q15_1" rowid="2" min="0" max="100"></td></tr>
    <tr class="rowtitletr"><td>健康可信度</td></tr>
    <tr><td><input class="ui-slider-input" id="q15_2" rowid="3" min="0" max="100"></td></tr>
    <tr class="rowtitletr"><td>价格合理性</td></tr>
    <tr><td><input class="ui-slider-input" id="q15_3" rowid="4" min="0" max="100"></td></tr>
  </table>
</div>
"""


def test_parse_matrix_rating():
    manifest = Manifest.load(MANIFEST)
    survey = parse_survey(HTML, manifest, token="t")
    assert survey.size == 1
    item = survey.item(15)
    assert item.kind is ItemKind.MATRIX_RATING
    assert item.row_count == 4
    assert item.low == 0.0 and item.high == 100.0
    assert [r.label for r in item.rows] == ["口味", "原料", "健康可信度", "价格合理性"]


def test_encode_matrix_rating_blob():
    manifest = Manifest.load(MANIFEST)
    survey = parse_survey(HTML, manifest, token="t")
    answer = ItemAnswer(number=15, matrix=((1, 85), (2, 70), (3, 60), (4, 90)))
    sheet = AnswerSheet(seed=1, unit=1, answers={15: answer})
    blob = WjxEncoder(manifest).build_blob(survey, sheet)
    assert blob == "15$1!85^2!70^3!60^4!90"


def test_generate_matrix_rating_in_bounds_and_not_straight():
    manifest = Manifest.load(MANIFEST)  # noqa: F841 - 确保清单可加载
    survey = Survey(
        token="t",
        items=(
            Item(
                number=15,
                kind=ItemKind.MATRIX_RATING,
                rows=tuple(Option(i, f"行{i}") for i in range(1, 5)),
                low=0,
                high=100,
            ),
        ),
    )
    for unit in range(1, 21):
        sheet = generate_sheet(survey, Strategy(), seed=20261119, unit=unit)
        pairs = sheet.get(15).matrix
        assert len(pairs) == 4
        assert all(0 <= value <= 100 for _, value in pairs)
        assert len({value for _, value in pairs}) > 1, "不应出现整行同分的直线作答"


def test_invalid_answer_is_format_and_stops():
    manifest = Manifest.load(MANIFEST)
    classifier = WjxClassifier(manifest)
    outcome = classifier.classify(
        RawResponse(status_code=200, text="9*14*1提交的答案不符合要求，请检查并修正后重新提交!", error="")
    )
    assert outcome.verdict is Verdict.FORMAT
    assert outcome.failure is FailureKind.FORMAT
