"""信效度：维度分组、维度间相关、反向计分。

这里只负责「把用户填的字符串变成可用的结构」以及「每份答卷抽一组维度因子」；
真正决定答案的是 sampling 层，诊断指标在 diagnostics 层。

与旧实现的关键差别：因子按维度分开抽，再用 Cholesky 让维度之间相关；
反向题在生成后翻转，而不是在采样公式里硬编码。
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.strategy import ValidityConfig
from wjxdesk.domain.survey import Survey

_NUM = re.compile(r"\d+")


def parse_numbers(spec: str) -> set[int]:
    """解析题号集合：支持 `22-28`、`1,2,3`、`1+2+3`、空串。"""
    text = (spec or "").strip()
    if not text:
        return set()
    numbers: set[int] = set()
    for chunk in re.split(r"[,，;；+＋\s]+", text):
        if not chunk:
            continue
        if "-" in chunk:
            head, _, tail = chunk.partition("-")
            if head.strip().isdigit() and tail.strip().isdigit():
                start, end = int(head), int(tail)
                if start > end:
                    start, end = end, start
                numbers.update(range(start, end + 1))
                continue
        if chunk.strip().isdigit():
            numbers.add(int(chunk))
    return numbers


def parse_dimensions(spec: str) -> dict[int, int]:
    """解析维度分组：`1-5;6-10` 或 `1,2,3|4,5,6`。

    返回 {题号: 维度下标}。没有分组时返回空字典（调用方按「单维度」处理）。
    """
    text = (spec or "").strip()
    if not text:
        return {}
    groups = [g for g in re.split(r"[;；|｜\n]+", text) if g.strip()]
    mapping: dict[int, int] = {}
    for index, group in enumerate(groups):
        for number in parse_numbers(group):
            mapping.setdefault(number, index)
    return mapping


def parse_correlation(spec: str, dimensions: int) -> list[list[float]]:
    """解析维度间相关矩阵（上三角给出，其余按对称补齐）。

    `0.5,0.3` 表示 3 个维度时：r12=0.5、r13=0.3、r23 缺省 0。
    """
    size = max(1, dimensions)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        matrix[i][i] = 1.0
    if size == 1:
        return matrix

    values: list[float] = []
    for chunk in (spec or "").replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(max(-0.95, min(0.95, float(chunk))))
        except ValueError:
            continue

    cursor = 0
    for i in range(size):
        for j in range(i + 1, size):
            value = values[cursor] if cursor < len(values) else 0.0
            cursor += 1
            matrix[i][j] = matrix[j][i] = value
    return _nearest_positive_definite(matrix)


def _nearest_positive_definite(matrix: list[list[float]]) -> list[list[float]]:
    """把相关矩阵拉成正定：向单位矩阵收缩（对角线保持 1）。

    旧做法是给对角线加阻尼（1 → 1.3），那会改变方差、把所有相关整体稀释，
    实测中会把整张矩阵压成独立阵。正确做法是
    `Σ(λ) = (1-λ)Σ + λI`：对角线恒为 1，只等比缩小相关系数，
    λ 取"刚好正定"的最小值，因此对结构的破坏最小。
    """
    size = len(matrix)
    for i in range(size):
        matrix[i][i] = 1.0
    try:
        _cholesky(matrix)
        return matrix
    except ValueError:
        pass

    low, high = 0.0, 1.0
    best = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    for _ in range(30):
        mid = (low + high) / 2.0
        candidate = [
            [
                1.0 if i == j else (1.0 - mid) * matrix[i][j]
                for j in range(size)
            ]
            for i in range(size)
        ]
        try:
            _cholesky(candidate)
        except ValueError:
            low = mid
            continue
        best = candidate
        high = mid
    return best


def project_correlation(matrix: list[list[float]]) -> tuple[list[list[float]], float]:
    """返回「投影后的相关矩阵」以及收缩强度 λ（0 表示原矩阵本来就正定）。

    调用方应当把 λ 报给用户：λ 越大，说明用户填的相关越自相矛盾，
    实际生成时被整体削弱得越多。
    """
    size = len(matrix)
    source = [row[:] for row in matrix]
    for i in range(size):
        source[i][i] = 1.0
    try:
        _cholesky(source)
        return source, 0.0
    except ValueError:
        pass
    projected = _nearest_positive_definite([row[:] for row in source])
    # 反推 λ：任取一个非对角元素比较
    for i in range(size):
        for j in range(size):
            if i != j and abs(source[i][j]) > 1e-9:
                lam = 1.0 - projected[i][j] / source[i][j]
                return projected, max(0.0, min(1.0, lam))
    return projected, 1.0



def _cholesky(matrix: list[list[float]]) -> list[list[float]]:
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            total = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 1e-9:
                    raise ValueError("矩阵非正定")
                lower[i][j] = math.sqrt(total)
            else:
                lower[i][j] = total / lower[j][j]
    return lower


@dataclass(frozen=True, slots=True)
class FactorPlan:
    """一份问卷的因子结构。"""

    dimensions: dict[int, int] = field(default_factory=dict)
    reverse: frozenset[int] = frozenset()
    cholesky: list[list[float]] = field(default_factory=list)
    alpha_target: float = 0.80

    @property
    def dimension_count(self) -> int:
        if not self.dimensions:
            return 1
        return max(self.dimensions.values()) + 1

    def dimension_of(self, number: int) -> int:
        return self.dimensions.get(number, 0)

    def is_reverse(self, number: int) -> bool:
        return number in self.reverse


def build_plan(survey: Survey, config: ValidityConfig) -> FactorPlan:
    """由配置与问卷结构算出因子结构。"""
    if not config.enabled:
        return FactorPlan(alpha_target=config.alpha_target)

    dimensions = parse_dimensions(config.dimensions)
    scorable = {
        item.number
        for item in survey.items
        if item.kind in {ItemKind.SCALE, ItemKind.MATRIX}
    } | parse_numbers(config.single_questions)

    # 只保留确实是量表/矩阵/被手动指名的题
    dimensions = {k: v for k, v in dimensions.items() if k in scorable}
    if not dimensions:
        # 没填分组：所有可计量表题算同一个维度
        dimensions = {number: 0 for number in sorted(scorable)}

    count = max(dimensions.values()) + 1 if dimensions else 1
    correlation = parse_correlation(config.correlation, count)
    try:
        cholesky = _cholesky(correlation)
    except ValueError:
        cholesky = [[1.0 if i == j else 0.0 for j in range(count)] for i in range(count)]

    return FactorPlan(
        dimensions=dimensions,
        reverse=frozenset(parse_numbers(config.reverse)),
        cholesky=cholesky,
        alpha_target=config.alpha_target,
    )


def draw_factors(plan: FactorPlan, rng: random.Random) -> dict[int, float]:
    """抽一组维度因子：独立正态 → 用 Cholesky 引入维度间相关。"""
    count = plan.dimension_count
    noise = [rng.gauss(0.0, 1.0) for _ in range(count)]
    if len(plan.cholesky) == count and count > 1:
        return {
            i: sum(plan.cholesky[i][j] * noise[j] for j in range(i + 1))
            for i in range(count)
        }
    return {i: noise[i] for i in range(count)}


def flip_index(index: int, count: int) -> int:
    """反向计分：把 1 基序号翻到对称位置。"""
    if count <= 1:
        return index
    return count + 1 - index
