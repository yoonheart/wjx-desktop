"""闭环控制器。

不用「整批生成再拒绝采样」，而是小批量标定 + 在线修正：
每完成若干份就重新算一次诊断指标，偏差超阈值就把生成参数推回去。

好处：内存 O(1)、首份立即可用、失败重试不会打乱已生成的分布。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from wjxdesk.domain.answer import AnswerSheet
from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import TargetSpec
from wjxdesk.domain.survey import Survey
from wjxdesk.engine.diagnostics import QualityReport, cronbach_alpha, report

LATENT_MIN = 0.05
LATENT_MAX = 0.95


@dataclass(slots=True)
class LatentController:
    """把「同一份答卷内部的一致性」当作被控量。

    latent 高 → 各题答案同向，α 上升
    latent 低 → 各题接近独立，α 下降
    """

    spec: TargetSpec
    latent: float = 0.55
    updates: int = 0
    history: list[tuple[int, float, float]] = field(default_factory=list)

    @property
    def target_alpha(self) -> float:
        return (self.spec.alpha_min + self.spec.alpha_max) / 2.0

    def observe(self, quality: QualityReport) -> float:
        """喂入一次诊断结果，返回调整后的 latent。"""
        if not self.spec.enabled or quality.items < 2:
            return self.latent

        error = self.target_alpha - quality.alpha
        # 比例控制 + 一阶平滑，避免抖动
        proposal = self.latent + 1.2 * error
        smoothed = 0.7 * self.latent + 0.3 * proposal

        if quality.kmo and quality.kmo < self.spec.kmo_min:
            smoothed += 0.04
        if quality.dominant > self.spec.harman_max:
            smoothed -= 0.06

        self.latent = max(LATENT_MIN, min(LATENT_MAX, smoothed))
        self.updates += 1
        self.history.append((quality.samples, quality.alpha, self.latent))
        return self.latent

    def status(self) -> dict[str, object]:
        return {
            "latent": round(self.latent, 3),
            "target_alpha": round(self.target_alpha, 3),
            "updates": self.updates,
            "last": self.history[-3:],
        }


# --------------------------------------------------------------------------- 矩阵构建

SCALE_KINDS = {ItemKind.SCALE}


def scale_items(survey: Survey) -> tuple[int, ...]:
    return tuple(it.number for it in survey.items if it.kind in SCALE_KINDS)


def build_matrix(
    survey: Survey, sheets: list[AnswerSheet], reverse: frozenset[int] = frozenset()
) -> list[list[float]]:
    """把一批答卷摊成「行=答卷，列=量表题」的数值矩阵。

    reverse 里的题会先做 k+1-r 反向编码——信度/效度指标必须建立在
    「分数方向一致」的数据上，否则反向题等于噪声。
    """
    numbers = scale_items(survey)
    if not numbers:
        return []
    spans = {n: max(1, survey.item(n).option_count or 5) for n in numbers}

    rows: list[list[float]] = []
    for sheet in sheets:
        row: list[float] = []
        skipped = False
        for number in numbers:
            answer = sheet.get(number)
            if answer is None or answer.scale is None:
                skipped = True
                break
            value = float(answer.scale)
            if number in reverse:
                value = (spans[number] + 1) - value
            row.append(value)
        if not skipped:
            rows.append(row)
    return rows


def evaluate(
    survey: Survey,
    sheets: list[AnswerSheet],
    spec: TargetSpec,
    dimensions: dict[int, int] | None = None,
    reverse: frozenset[int] = frozenset(),
) -> QualityReport:
    """整卷诊断。

    传了维度分组时，α 取「各维度内部 α 的均值」——把一个二维量表的所有题混在
    一起算，会得到远低于实际的信度，据此调参只会越调越偏。
    """
    matrix = build_matrix(survey, sheets, reverse)
    if not matrix:
        return QualityReport(samples=0, items=0, notes=["尚无足够的量表题数据"])

    base = report(matrix, alpha_min=spec.alpha_min, kmo_min=spec.kmo_min)
    if not dimensions:
        return base

    numbers = scale_items(survey)
    groups: dict[int, list[int]] = {}
    for index, number in enumerate(numbers):
        groups.setdefault(dimensions.get(number, 0), []).append(index)

    alphas: list[float] = []
    for columns in groups.values():
        if len(columns) < 2:
            continue
        sub = [[row[i] for i in columns] for row in matrix]
        alphas.append(cronbach_alpha(sub))
    if not alphas:
        return base

    alpha = sum(alphas) / len(alphas)
    return replace(
        base,
        alpha=alpha,
        notes=[n for n in base.notes if not n.startswith("α=")]
        + ([f"α={alpha:.3f} 低于目标 {spec.alpha_min:.2f}"] if alpha < spec.alpha_min else []),
    )
