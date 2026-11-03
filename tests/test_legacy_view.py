from __future__ import annotations

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.survey import Item, Option, Survey
from wjxdesk.protocol.manifest import Manifest
from wjxdesk.protocol.wjx import MANIFEST_PATH
from wjxdesk.protocol.wjx.parse import parse_survey
from wjxdesk.web.legacy import legacy_questions, strategy_from_legacy


def _options(count: int) -> tuple[Option, ...]:
    return tuple(Option(index=i, label=f"L{i}") for i in range(1, count + 1))


def test_dropdown_keeps_placeholder_for_frontend() -> None:
    """界面渲染下拉框时会跳过首项，所以视图层要补回占位项。"""
    survey = Survey(
        token="t",
        items=(Item(number=3, kind=ItemKind.DROPDOWN, options=_options(2)),),
    )
    options = legacy_questions(survey)[0]["选项"]
    assert options[0] == "请选择"
    assert options[1:] == ["1. L1", "2. L2"]


def test_scale_uses_frontend_prefix() -> None:
    """界面只认「量表选项:」这个前缀，别的写法会退化成纯文本、拿不到概率输入框。"""
    survey = Survey(
        token="t",
        items=(Item(number=4, kind=ItemKind.SCALE, options=_options(5), low=1, high=5),),
    )
    options = legacy_questions(survey)[0]["选项"]
    assert len(options) == 1
    assert options[0].startswith("量表选项:")
    assert options[0].count("|") == 4


def test_matrix_view_lists_columns_and_rows() -> None:
    survey = Survey(
        token="t",
        items=(
            Item(
                number=5,
                kind=ItemKind.MATRIX,
                options=_options(5),
                rows=(Option(1, "颜值"), Option(2, "性格")),
            ),
        ),
    )
    options = legacy_questions(survey)[0]["选项"]
    assert options[0].startswith("可选答案:")
    assert options[1:] == ["1. 颜值", "2. 性格"]


def test_scale_and_matrix_probabilities_reach_strategy() -> None:
    payload = {
        "targetCount": 2,
        "speedMultiplier": 1,
        "questions": [
            {
                "id": "4",
                "type": "量表题",
                "options": [
                    {"index": i, "text": str(i + 1), "probability": p}
                    for i, p in enumerate([5, 10, 20, 40, 25])
                ],
            },
            {
                "id": "5",
                "type": "矩阵题",
                "options": [
                    {
                        "rowIndex": 0,
                        "rowText": "颜值",
                        "options": [
                            {"colIndex": c, "probability": p}
                            for c, p in enumerate([5, 10, 20, 40, 20, 5])
                        ],
                    }
                ],
            },
        ],
    }
    strategy = strategy_from_legacy(payload)
    assert strategy.rule_for(4).weights == (5.0, 10.0, 20.0, 40.0, 25.0)
    assert strategy.rule_for(5).matrix_weights == ((5.0, 10.0, 20.0, 40.0, 20.0, 5.0),)
    assert strategy.rule_for(5).row_weights(1, 6) == [5.0, 10.0, 20.0, 40.0, 20.0, 5.0]


MATRIX_HTML = """
<div class="field" type="6" id="div5" req="1">
  <div class="field-label"><div class="topicnumber">5.</div><div class="topichtml">打分</div></div>
  <table class="matrix-rating matrixtable">
    <tbody>
      <tr class="trlabel"><th></th><th>1</th><th>2</th><th>3</th></tr>
      <tr class="rowtitle"><td colspan="4">组A</td></tr>
      <tr fid="q5_0"><td class="scalerowtitletd"><div class="rowtitlediv">颜值</div></td>
          <td><a class="rate-off" dval="1"></a></td><td><a class="rate-off" dval="2"></a></td>
          <td><a class="rate-off" dval="3"></a></td></tr>
      <tr class="rowtitle"><td colspan="4">组B</td></tr>
      <tr fid="q5_1"><td class="scalerowtitletd"><div class="rowtitlediv">性格</div></td>
          <td><a class="rate-off" dval="1"></a></td><td><a class="rate-off" dval="2"></a></td>
          <td><a class="rate-off" dval="3"></a></td></tr>
    </tbody>
  </table>
</div>
"""


def test_matrix_rows_skip_group_titles_and_header() -> None:
    """表头行和 colspan 的分组标题行都不是数据行，必须排除。"""
    survey = parse_survey(MATRIX_HTML, Manifest.load(MANIFEST_PATH), token="t")
    item = survey.item(5)
    assert len(item.options) == 3  # 列：1/2/3
    assert [row.label for row in item.rows] == ["颜值", "性格"]

def test_text_answers_from_objects_reach_strategy() -> None:
    """界面保存的文本答案是 [{text, probability}]，不是纯字符串。"""
    payload = {
        "targetCount": 3,
        "speedMultiplier": 1,
        "questions": [
            {
                "id": "9",
                "type": "填空题",
                "textAnswers": [
                    {"text": "希望多办活动", "probability": 60},
                    {"text": "增加停车位", "probability": 40},
                ],
                "textAnswersMode": "random",
            },
            {
                "id": "10",
                "type": "填空题",
                "textAnswers": [{"text": f"答案{i}", "probability": 1} for i in range(3)],
                "textAnswersMode": "unique",
            },
        ],
    }
    strategy = strategy_from_legacy(payload)

    assert strategy.rule_for(9).texts == ("希望多办活动", "增加停车位")
    assert strategy.rule_for(9).text_weights == (60.0, 40.0)
    assert strategy.rule_for(9).unique_group == ""
    assert strategy.rule_for(10).unique_group == "q10"
