"""统计诊断。

纯标准库实现，不引入数值库——这既让打包体积可控，也让结果可逐位复核。
提供：Cronbach's α、KMO、Bartlett 球形检验、描述统计、单因子解释占比。

约定：输入矩阵为「行=答卷，列=题项」，只放量表/矩阵类题的数值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

Matrix = Sequence[Sequence[float]]


# --------------------------------------------------------------------------- 基础


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def variance(values: Sequence[float], ddof: int = 1) -> float:
    n = len(values)
    if n - ddof <= 0:
        return 0.0
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (n - ddof)


def columns(matrix: Matrix) -> list[list[float]]:
    if not matrix:
        return []
    return [list(col) for col in zip(*matrix)]


def correlation_matrix(matrix: Matrix) -> list[list[float]]:
    cols = columns(matrix)
    k = len(cols)
    result = [[1.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i + 1, k):
            r = _pearson(cols[i], cols[j])
            result[i][j] = result[j][i] = r
    return result


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 2:
        return 0.0
    ma, mb = mean(a), mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


# --------------------------------------------------------------------------- 线性代数


def determinant(matrix: list[list[float]]) -> float:
    """高斯消元求行列式（带部分主元）。"""
    n = len(matrix)
    if n == 0:
        return 1.0
    a = [row[:] for row in matrix]
    det = 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return 0.0
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            det = -det
        det *= a[col][col]
        for row in range(col + 1, n):
            factor = a[row][col] / a[col][col]
            if factor == 0.0:
                continue
            for j in range(col, n):
                a[row][j] -= factor * a[col][j]
    return det


def inverse(matrix: list[list[float]]) -> list[list[float]]:
    """高斯-约当求逆。奇异时抛 ValueError。"""
    n = len(matrix)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("相关矩阵奇异，无法求逆（题项可能完全共线或样本量不足）")
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        a[col] = [v / div for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor == 0.0:
                continue
            a[row] = [v - factor * w for v, w in zip(a[row], a[col])]
    return [row[n:] for row in a]


# --------------------------------------------------------------------------- 指标


def cronbach_alpha(matrix: Matrix) -> float:
    """Cronbach's α。题项数 < 2 或总方差为 0 时返回 0。"""
    cols = columns(matrix)
    k = len(cols)
    if k < 2:
        return 0.0
    item_vars = sum(variance(col) for col in cols)
    totals = [sum(row) for row in matrix]
    total_var = variance(totals)
    if total_var <= 0:
        return 0.0
    return max(0.0, min(1.0, k / (k - 1) * (1.0 - item_vars / total_var)))


def kmo(matrix: Matrix) -> float:
    """KMO 取样适切性。"""
    r = correlation_matrix(matrix)
    k = len(r)
    if k < 2:
        return 0.0
    try:
        inv = inverse(r)
    except ValueError:
        return 0.0
    r_sum = 0.0
    p_sum = 0.0
    for i in range(k):
        for j in range(k):
            if i == j:
                continue
            r_sum += r[i][j] ** 2
            denom = math.sqrt(max(1e-12, inv[i][i] * inv[j][j]))
            p_sum += (-inv[i][j] / denom) ** 2
    total = r_sum + p_sum
    return 0.0 if total <= 0 else r_sum / total


def bartlett(matrix: Matrix) -> tuple[float, int, float]:
    """Bartlett 球形检验，返回 (卡方, 自由度, p)。"""
    r = correlation_matrix(matrix)
    k = len(r)
    n = len(matrix)
    if k < 2 or n < 3:
        return 0.0, 0, 1.0
    det = determinant(r)
    if det <= 0:
        return float("inf"), k * (k - 1) // 2, 0.0
    chi2 = -((n - 1) - (2 * k + 5) / 6.0) * math.log(det)
    df = k * (k - 1) // 2
    return chi2, df, chi2_sf(chi2, df)


def dominant_share(matrix: Matrix) -> float:
    """第一主成分解释占比（幂迭代近似），用于共同方法偏差的粗判。"""
    r = correlation_matrix(matrix)
    k = len(r)
    if k < 3:
        return 0.0
    vector = [1.0] * k
    for _ in range(80):
        nxt = [sum(r[i][j] * vector[j] for j in range(k)) for i in range(k)]
        norm = math.sqrt(sum(v * v for v in nxt)) or 1.0
        vector = [v / norm for v in nxt]
    lam = sum(vector[i] * sum(r[i][j] * vector[j] for j in range(k)) for i in range(k))
    return max(0.0, min(1.0, lam / k))


def skewness(values: Sequence[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    m = mean(values)
    s = math.sqrt(variance(values)) or 1e-12
    return sum(((v - m) / s) ** 3 for v in values) / n


def kurtosis(values: Sequence[float]) -> float:
    n = len(values)
    if n < 4:
        return 0.0
    m = mean(values)
    s = math.sqrt(variance(values)) or 1e-12
    return sum(((v - m) / s) ** 4 for v in values) / n - 3.0


# --------------------------------------------------------------------------- 卡方尾概率


def _gamma_p(a: float, x: float) -> float:
    """下不完全伽马 P(a,x)，级数展开。"""
    if x <= 0:
        return 0.0
    term = 1.0 / a
    total = term
    ap = a
    for _ in range(500):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-14:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_q_cf(a: float, x: float) -> float:
    """上不完全伽马 Q(a,x)，连分式。"""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if abs(b) > tiny else 1.0 / tiny
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, df: int) -> float:
    """卡方分布上尾概率 P(X > x)。"""
    if x <= 0:
        return 1.0
    if df <= 0:
        return 1.0
    a = df / 2.0
    x2 = x / 2.0
    if x2 < a + 1.0:
        return max(0.0, min(1.0, 1.0 - _gamma_p(a, x2)))
    return max(0.0, min(1.0, _gamma_q_cf(a, x2)))


# --------------------------------------------------------------------------- 报告


@dataclass(frozen=True, slots=True)
class QualityReport:
    samples: int
    items: int
    alpha: float = 0.0
    kmo: float = 0.0
    bartlett_chi2: float = 0.0
    bartlett_df: int = 0
    bartlett_p: float = 1.0
    dominant: float = 0.0
    mean_score: float = 0.0
    sd_score: float = 0.0
    skew: float = 0.0
    kurt: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "items": self.items,
            "alpha": round(self.alpha, 4),
            "kmo": round(self.kmo, 4),
            "bartlett": {
                "chi2": round(self.bartlett_chi2, 3),
                "df": self.bartlett_df,
                "p": self.bartlett_p,
            },
            "dominant_share": round(self.dominant, 4),
            "mean": round(self.mean_score, 3),
            "sd": round(self.sd_score, 3),
            "skew": round(self.skew, 3),
            "kurt": round(self.kurt, 3),
            "notes": list(self.notes),
        }


def report(matrix: Matrix, alpha_min: float = 0.70, kmo_min: float = 0.70) -> QualityReport:
    cols = columns(matrix)
    totals = [sum(row) for row in matrix]
    a = cronbach_alpha(matrix)
    k_measure = kmo(matrix)
    chi2, df, p = bartlett(matrix)
    dominant = dominant_share(matrix)
    notes: list[str] = []

    # 样本太少或题项太少时，指标本身不可靠，只给「样本不足」这一条提示
    enough = len(matrix) >= 20 and len(cols) >= 3
    if not enough:
        notes.append(f"样本量 {len(matrix)} / 题项 {len(cols)} 偏少，暂不做统计判断")
    else:
        if a and a < alpha_min:
            notes.append(f"α={a:.3f} 低于目标 {alpha_min:.2f}")
        if a > 0.95:
            notes.append(f"α={a:.3f} 过高，题项几乎重复，容易被判异常")
        if k_measure and k_measure < kmo_min:
            notes.append(f"KMO={k_measure:.3f} 低于 {kmo_min:.2f}，不适合因子分析")
        if dominant > 0.40:
            notes.append(f"单因子解释 {dominant:.1%} 超过 40%，共同方法偏差风险")
        if len(matrix) < 10 * len(cols):
            notes.append(f"样本量 {len(matrix)} 少于题项数×10，统计效力偏低")

    return QualityReport(
        samples=len(matrix),
        items=len(cols),
        alpha=a,
        kmo=k_measure,
        bartlett_chi2=chi2,
        bartlett_df=df,
        bartlett_p=p,
        dominant=dominant,
        mean_score=mean(totals),
        sd_score=math.sqrt(variance(totals)),
        skew=skewness(totals),
        kurt=kurtosis(totals),
        notes=notes,
    )
