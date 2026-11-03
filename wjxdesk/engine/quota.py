"""人群配额。

需求场景：问卷里有「性别 / 年龄段 / 城市等级」这类题，用户希望整批的选项占比
接近某个目标，而不是纯随机。

做法：生成后按「当前累计占比 vs 目标占比」做一次纠偏——哪个选项欠得最多就
把这份答案改成哪个。这样不需要重新生成，也不会破坏其它题的分布。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from wjxdesk.domain.answer import AnswerSheet, ItemAnswer
from wjxdesk.domain.strategy import QuotaConfig
from wjxdesk.domain.survey import Survey


@dataclass
class QuotaBalancer:
    config: QuotaConfig
    counts: dict[int, Counter] = field(default_factory=dict)
    total: int = 0

    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.targets)

    def correct(self, survey: Survey, sheet: AnswerSheet) -> AnswerSheet:
        if not self.enabled():
            return sheet

        answers = dict(sheet.answers)
        for number, distribution in self.config.targets.items():
            try:
                item = survey.item(number)
            except KeyError:
                continue
            if item.option_count <= 0:
                continue

            current = self.counts.setdefault(number, Counter())
            expected_total = self.total + 1
            normalized = _normalize(distribution)

            # 目标里权重最高、当前实际占比最落后的选项
            deficits = {
                option: normalized.get(option, 0.0) * expected_total - current.get(option, 0)
                for option in range(1, item.option_count + 1)
            }
            target_option = max(deficits, key=lambda key: deficits[key])

            existing = answers.get(number)
            if existing is None:
                answers[number] = ItemAnswer(number=number, picks=(target_option,))
                current[target_option] += 1
                continue

            # 已经抽到目标选项就不动；否则替换（多选题退化为单选，保证可预测）
            if target_option in existing.picks:
                for pick in existing.picks:
                    current[pick] += 1
                continue

            replacement = target_option if item.kind.value in {"single", "dropdown", "scale"} else existing.picks[0]
            if item.kind.value in {"single", "dropdown"}:
                answers[number] = ItemAnswer(
                    number=number, picks=(replacement,), free_text=existing.free_text
                )
                current[replacement] += 1
            else:
                for pick in existing.picks:
                    current[pick] += 1

        self.total += 1
        return AnswerSheet(seed=sheet.seed, unit=sheet.unit, answers=answers)

    def snapshot(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        total = max(1, self.total)
        for number, counter in self.counts.items():
            out[str(number)] = {
                str(option): round(count / total, 3) for option, count in sorted(counter.items())
            }
        return out


def _normalize(distribution: dict[int, float]) -> dict[int, float]:
    total = sum(distribution.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in distribution.items()}
