# -*- coding: utf-8 -*-
"""
合成答卷仿真器 —— 离线验证「信效度达标 + 显著项数量可控」能否同时成立。

纯标准库实现（项目已为体积移除 numpy，这里保持零依赖）。

核心思想
--------
1) 每份答卷抽一个潜在因子向量 F ~ N(0, Sigma)；
2) 量表题 = copula(F_d, 边缘分布)，同维度共用因子 -> 内部一致，alpha 可控；
3) 因变量 Y 由「观测题 + 人口学变量 + 残差」按 logistic 生成 -> Y 与题目天然相关，
   所以回归显著是构造出来的，不是碰运气碰出来的；
4) 通过对 beta 的标定 + 蒙特卡洛校验，把显著项数量钉在目标区间。

用法： python synth_sim.py [--n 200] [--reps 100]
"""

import math
import random
import statistics
import sys

# ============================================================
# 1. 基础数学（纯标准库）
# ============================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _gammainc_P(a, x):
    """正则化下不完全伽马函数 P(a,x) —— 卡方 CDF 用。"""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(300):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-14:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    # 连分式求 Q(a,x)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 300):
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
    q = math.exp(-x + a * math.log(x) - gln) * h
    return 1.0 - q


def chi2_sf(x, df):
    return max(0.0, min(1.0, 1.0 - _gammainc_P(df / 2.0, x / 2.0)))


# ============================================================
# 2. 矩阵工具
# ============================================================

def cholesky(A):
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                v = A[i][i] - s
                L[i][j] = math.sqrt(max(v, 1e-12))
            else:
                L[i][j] = (A[i][j] - s) / L[j][j]
    return L


def mat_inv(A):
    """Gauss-Jordan 求逆（带部分主元）。"""
    n = len(A)
    M = [list(A[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("矩阵奇异")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and abs(M[r][col]) > 1e-15:
                f = M[r][col]
                M[r] = [M[r][c] - f * M[col][c] for c in range(2 * n)]
    return [row[n:] for row in M]


def mat_solve(A, b):
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("矩阵奇异")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and abs(M[r][col]) > 1e-15:
                f = M[r][col]
                M[r] = [M[r][c] - f * M[col][c] for c in range(n + 1)]
    return [row[n] for row in M]


def corr_matrix(data):
    """data: 每列一个变量。返回相关矩阵。"""
    p = len(data[0])
    cols = [[row[j] for row in data] for j in range(p)]
    mu = [statistics.fmean(c) for c in cols]
    sd = []
    for c, m in zip(cols, mu):
        v = statistics.fmean([(x - m) ** 2 for x in c])
        sd.append(math.sqrt(v) if v > 1e-12 else 1e-12)
    R = [[0.0] * p for _ in range(p)]
    for i in range(p):
        R[i][i] = 1.0
        for j in range(i + 1, p):
            s = statistics.fmean([(cols[i][k] - mu[i]) * (cols[j][k] - mu[j]) for k in range(len(cols[i]))])
            r = s / (sd[i] * sd[j])
            R[i][j] = R[j][i] = max(-0.999, min(0.999, r))
    return R


def mat_det(A):
    n = len(A)
    M = [list(r) for r in A]
    det = 1.0
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            return 0.0
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
            det = -det
        det *= M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for c in range(col, n):
                M[r][c] -= f * M[col][c]
    return det


# ============================================================
# 3. 问卷结构定义（对应 v.wjx.cn/vm/Q4wUhCD.aspx）
# ============================================================

# 22-28 是 7 道 5 点李克特题，按 push-pull 理论分 3 个维度
DIMENSIONS = {
    "经济理性":   [22, 23, 24],   # 专业对口 / 起薪 / 生活成本
    "情感与公共服务": [25, 27],   # 离家近 / 公共服务
    "发展预期与政策": [26, 28],   # 机会增长 / 政策知晓
}

# 每题的边缘分布（真实作答不会是均匀的，中间选项更密，且整体略有偏向）
EDGE = {
    22: [0.06, 0.14, 0.28, 0.31, 0.21],
    23: [0.10, 0.20, 0.30, 0.25, 0.15],
    24: [0.12, 0.22, 0.30, 0.24, 0.12],
    25: [0.16, 0.20, 0.24, 0.24, 0.16],
    26: [0.07, 0.15, 0.29, 0.29, 0.20],
    27: [0.08, 0.17, 0.30, 0.28, 0.17],
    28: [0.14, 0.21, 0.28, 0.23, 0.14],
}

ITEMS = sorted(EDGE.keys())
ITEM_IDX = {q: i for i, q in enumerate(ITEMS)}
ITEM_DIM = {}
for d, (_, qs) in enumerate(DIMENSIONS.items()):
    for q in qs:
        ITEM_DIM[q] = d

# 维度间相关（区别效度的来源；0.30-0.40 是比较自然的量级）
DIM_CORR = [
    [1.00, 0.46, 0.42],
    [0.46, 1.00, 0.40],
    [0.42, 0.40, 1.00],
]

# 人口学变量：[名称, 取值数, 边缘分布权重, 与其他变量的相关(可选)]
COVARIATES = [
    ("gender",   [0.48, 0.52]),                       # 1=男
    ("degree",   [0.72, 0.22, 0.06]),                  # 本科/硕士/专科
    ("origin",   [0.14, 0.26, 0.32, 0.28]),            # 省会/地级市/县城/乡镇农村
    ("p_edu",    [0.18, 0.30, 0.24, 0.22, 0.06]),      # 1-5
    ("income",   [0.16, 0.30, 0.30, 0.18, 0.06]),      # 1-5
    ("intern",   [0.28, 0.34, 0.24, 0.14]),            # 0-3 次
    ("digital",  [0.07, 0.18, 0.38, 0.27, 0.10]),      # 1-5
    ("rank",     [0.26, 0.30, 0.26, 0.18]),            # 后20% -> 前20%
]

# 真实效应（logit 尺度）。这是唯一的"标定旋钮"。
# 只给每个维度里的一道题设效应：同维度其余题会因为共线被压制，自然呈现"一维一显著"。
# 注意：同维度共线会把单题系数放大，标定值要按实测回推，不能直接照搬目标 z。
TRUE_BETA = {
    ("item", 23): 0.30,   # 起薪   （共线放大后实测 beta≈0.62，z≈2.6）
    ("item", 25): 0.58,   # 离家近
    ("item", 26): 0.70,   # 机会增长
    ("cov", "income"): 0.38,
    ("cov", "intern"): 0.10,     # 明确做成噪声项
    ("cov", "digital"): -0.10,   # 明确做成噪声项
    ("cov", "gender"): 0.05,
    ("cov", "degree"): -0.12,
    ("cov", "origin"): -0.08,
    ("cov", "p_edu"): 0.06,
    ("cov", "rank"): 0.05,
}
INTERCEPT = -0.15


# ============================================================
# 4. 生成器
# ============================================================

def _pick_weighted(weights, rng):
    u = rng.random()
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if u <= acc:
            return i
    return len(weights) - 1


def _cum(probs):
    out, acc = [], 0.0
    for p in probs:
        acc += p
        out.append(acc)
    out[-1] = 1.0
    return out


def alpha_to_lambda(target, k):
    """与 wjx2.py 的 _alpha_to_lambda 完全一致，保证离线验证的就是线上逻辑。"""
    n = max(2, int(k))
    t = min(max(float(target), 0.30), 0.90)
    rho = t / (n - t * (n - 1))
    rho = min(max(rho, 0.02), 0.90)
    return min(math.sqrt(rho / 0.88), 0.88)


def build_lambdas(target=0.80):
    lam = {}
    for d, (name, qs) in enumerate(DIMENSIONS.items()):
        lam[d] = alpha_to_lambda(target, len(qs))
    return lam


def generate(n, seed=0, target_alpha=0.80, link_outcome=True):
    """生成 n 份答卷。

    link_outcome=False 复现当前线上行为：Y 独立于因子，只按边缘概率随机。
    返回 (rows, y)：rows 是 [{题号: 1..5, 人口学名: 取值}]，y 是 0/1。
    """
    rng = random.Random(seed)
    lam = build_lambdas(target_alpha)
    ndim = len(DIMENSIONS)
    L = cholesky(DIM_CORR)

    rows, ys = [], []
    for _ in range(n):
        # --- 潜在因子 F ~ N(0, DIM_CORR)，用 Cholesky 展开 ---
        z = [rng.gauss(0.0, 1.0) for _ in range(ndim)]
        F = [sum(L[i][j] * z[j] for j in range(i + 1)) for i in range(ndim)]

        rec = {}
        # --- 量表题：copula ---
        for q in ITEMS:
            d = ITEM_DIM[q]
            u = norm_cdf(lam[d] * F[d] + math.sqrt(max(1e-9, 1 - lam[d] ** 2)) * rng.gauss(0.0, 1.0))
            cum = _cum(EDGE[q])
            r = 1
            for i, c in enumerate(cum):
                if u <= c:
                    r = i + 1
                    break
            rec[q] = r

        # --- 人口学 ---
        for name, weights in COVARIATES:
            rec[name] = _pick_weighted(weights, rng) + 1

        # 父教与收入正相关（真实数据几乎必然如此），顺带检验 VIF 是否仍可控
        if rec["p_edu"] >= 4 and rec["income"] < 4 and rng.random() < 0.55:
            rec["income"] += 1

        # --- 因变量 ---
        if link_outcome:
            eta = INTERCEPT
            for (kind, key), b in TRUE_BETA.items():
                if kind == "item":
                    eta += b * (rec[key] - 3)     # 以中点 3 为中心，避免截距被抬高
                else:
                    n_level = len(dict(COVARIATES)[key])
                    eta += b * (rec[key] - (n_level + 1) / 2.0)
            p = 1.0 / (1.0 + math.exp(-eta))
            y = 1 if rng.random() < p else 0
        else:
            y = 1 if rng.random() < 0.58 else 0   # 与任何题都无关

        rows.append(rec)
        ys.append(y)
    return rows, ys


# ============================================================
# 5. 统计检验
# ============================================================

def cronbach_alpha(rows, qs):
    k = len(qs)
    if k < 2:
        return float("nan")
    total = [sum(r[q] for q in qs) for r in rows]
    var_total = statistics.variance(total)
    var_sum = sum(statistics.variance([r[q] for r in rows]) for q in qs)
    if var_total <= 0:
        return 0.0
    return k / (k - 1) * (1 - var_sum / var_total)


def kmo_and_bartlett(rows, qs):
    data = [[r[q] for q in qs] for r in rows]
    R = corr_matrix(data)
    p = len(qs)
    try:
        Rinv = mat_inv(R)
    except ValueError:
        return float("nan"), float("nan"), 0.0
    # 偏相关矩阵 q_ij = -r_ij^-1 / sqrt(r_ii^-1 r_jj^-1)
    sum_r2 = 0.0
    sum_q2 = 0.0
    for i in range(p):
        for j in range(p):
            if i == j:
                continue
            sum_r2 += R[i][j] ** 2
            qij = -Rinv[i][j] / math.sqrt(Rinv[i][i] * Rinv[j][j])
            sum_q2 += qij ** 2
    kmo = sum_r2 / (sum_r2 + sum_q2) if (sum_r2 + sum_q2) > 0 else 0.0
    det = mat_det(R)
    chi2 = 0.0
    if det > 0:
        chi2 = -(len(rows) - 1 - (2 * p + 5) / 6.0) * math.log(det)
    dfb = p * (p - 1) // 2
    return kmo, chi2, chi2_sf(chi2, dfb)


def vif_all(rows, names):
    data = [[r[n] for n in names] for r in rows]
    R = corr_matrix(data)
    try:
        Rinv = mat_inv(R)
    except ValueError:
        return [float("inf")] * len(names)
    return [Rinv[i][i] for i in range(len(names))]


def logistic_fit(rows, y, item_qs, cov_names):
    """IRLS 拟合 logistic，返回 [(名称, beta, se, z, p, OR)]。"""
    names = ["(常量)"] + ["Q%d" % q for q in item_qs] + cov_names
    X = []
    for r in rows:
        row = [1.0]
        for q in item_qs:
            row.append(float(r[q]) - 3.0)      # 中心化，与生成一致
        for c in cov_names:
            row.append(float(r[c]))
        X.append(row)
    p = len(names)
    beta = [0.0] * p
    for _ in range(60):
        eta = [sum(X[i][j] * beta[j] for j in range(p)) for i in range(len(X))]
        pr = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e)))) for e in eta]
        A = [[sum(X[i][a] * X[i][b] * pr[i] * (1 - pr[i]) for i in range(len(X))) for b in range(p)] for a in range(p)]
        g = [sum(X[i][a] * (y[i] - pr[i]) for i in range(len(X))) for a in range(p)]
        try:
            delta = mat_solve(A, g)
        except ValueError:
            break
        beta = [beta[j] + delta[j] for j in range(p)]
        if max(abs(d) for d in delta) < 1e-9:
            break
    try:
        Ainv = mat_inv(A)
    except ValueError:
        return [(n, b, float("nan"), float("nan"), 1.0, float("nan")) for n, b in zip(names, beta)]
    out = []
    for j, n in enumerate(names):
        se = math.sqrt(max(Ainv[j][j], 1e-12))
        z = beta[j] / se
        pv = 2 * (1 - norm_cdf(abs(z)))
        out.append((n, beta[j], se, z, pv, math.exp(beta[j])))
    return out


# ============================================================
# 6. 报告
# ============================================================

def run_once(n=200, seed=0, link=True, target_alpha=0.80):
    rows, y = generate(n, seed=seed, target_alpha=target_alpha, link_outcome=link)
    report = {}

    # 信度
    per_dim = {}
    for d, (name, qs) in enumerate(DIMENSIONS.items()):
        per_dim[name] = cronbach_alpha(rows, qs)
    report["alpha_dim"] = per_dim
    report["alpha_all"] = cronbach_alpha(rows, ITEMS)

    # 效度
    kmo, chi2, pval = kmo_and_bartlett(rows, ITEMS)
    report["kmo"] = kmo
    report["bartlett_chi2"] = chi2
    report["bartlett_p"] = pval

    # 共线性
    cov_names = [c[0] for c in COVARIATES]
    report["vif"] = dict(zip(["Q%d" % q for q in ITEMS] + cov_names,
                             vif_all(rows, ITEMS + cov_names)))

    # 回归
    fit = logistic_fit(rows, y, ITEMS, cov_names)
    report["fit"] = fit
    report["sig_items"] = [r for r in fit if r[0].startswith("Q") and r[4] < 0.05]
    report["sig_covs"] = [r for r in fit if not r[0].startswith("Q") and r[0] != "(常量)" and r[4] < 0.05]
    # 模型整体
    eta = []
    for r in rows:
        e = fit[0][1]
        for q in ITEMS:
            e += next(x[1] for x in fit if x[0] == "Q%d" % q) * (r[q] - 3.0)
        for c in cov_names:
            e += next(x[1] for x in fit if x[0] == c) * r[c]
        eta.append(e)
    ll1 = sum(math.log(max(1e-12, (1 / (1 + math.exp(-max(-30, min(30, e))))) if yy else (1 - 1 / (1 + math.exp(-max(-30, min(30, e))))))) for e, yy in zip(eta, y))
    p0 = sum(y) / len(y)
    l0 = sum(math.log(max(1e-12, p0 if yy else 1 - p0)) for yy in y)
    report["lr_chi2"] = max(0.0, 2 * (ll1 - l0))
    report["lr_p"] = chi2_sf(report["lr_chi2"], len(ITEMS) + len(cov_names))
    report["y_rate"] = p0
    report["_rows"] = rows
    report["_y"] = y
    return report


def generate_valid_batch(n=200, lo=3, hi=4, max_tries=80, target_alpha=0.80, seed0=None):
    """整批生成 + 整体校验 + 拒绝采样。

    这是把「显著项数量」从"碰运气"变成"确定性"的关键：
    一批 200 份是一个整体，校验不达标就换种子整批重来，而不是逐份修补。
    返回 (report, 尝试次数, 采用种子)。
    """
    rng = random.Random(seed0) if seed0 is not None else None
    for attempt in range(1, max_tries + 1):
        seed = rng.randrange(1 << 30) if rng else random.randrange(1 << 30)
        rep = run_once(n=n, seed=seed, link=True, target_alpha=target_alpha)
        cnt = len(rep["sig_items"]) + len(rep["sig_covs"])
        min_alpha = min(rep["alpha_dim"].values())
        if lo <= cnt <= hi and min_alpha >= 0.70 and rep["kmo"] >= 0.60:
            return rep, attempt, seed
    return None, max_tries, None


def fmt(v, nd=3):
    if isinstance(v, float):
        if v != v:
            return "  nan"
        return ("%." + str(nd) + "f") % v
    return str(v)


def main():
    n = 200
    reps = 60
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    if "--reps" in sys.argv:
        reps = int(sys.argv[sys.argv.index("--reps") + 1])

    print("=" * 74)
    print("合成答卷仿真  n=%d  目标 alpha=0.80" % n)
    print("=" * 74)

    # ---------- 单次详细报告 ----------
    rep = run_once(n=n, seed=42, link=True)
    print("\n[1] 信度 Cronbach alpha")
    for name, a in rep["alpha_dim"].items():
        flag = "OK " if a >= 0.70 else "低 "
        print("    %-14s %s  alpha=%.3f" % (name, flag, a))
    print("    总量表(7题)         alpha=%.3f" % rep["alpha_all"])

    print("\n[2] 效度")
    print("    KMO = %.3f   %s" % (rep["kmo"], "OK" if rep["kmo"] >= 0.60 else "不足"))
    print("    Bartlett chi2=%.2f  df=%d  p=%.3g  %s"
          % (rep["bartlett_chi2"], 21, rep["bartlett_p"],
             "OK" if rep["bartlett_p"] < 0.001 else "不显著"))

    print("\n[3] 共线性 VIF")
    bad = [(k, v) for k, v in rep["vif"].items() if v >= 5.0]
    print("    最大 VIF = %.2f  (%s)" % (max(rep["vif"].values()),
                                        "可接受" if not bad else "超 5：" + ", ".join(k for k, _ in bad)))

    print("\n[4] 二元 logistic 回归（Y = 是否留在安徽）")
    print("    %-10s %8s %8s %7s %9s %7s" % ("变量", "beta", "SE", "z", "p", "OR"))
    for name, b, se, z, pv, orv in rep["fit"]:
        mark = ""
        if name.startswith("Q") or name not in ("(常量)",):
            if pv < 0.01:
                mark = "  **"
            elif pv < 0.05:
                mark = "  *"
        print("    %-10s %8.3f %8.3f %7.2f %9.4f %7.2f%s" % (name, b, se, z, pv, orv, mark))
    print("    模型 LR chi2=%.2f  p=%.3g   事件率=%.2f"
          % (rep["lr_chi2"], rep["lr_p"], rep["y_rate"]))
    print("    -> 量表题显著：%d 个   人口学显著：%d 个"
          % (len(rep["sig_items"]), len(rep["sig_covs"])))

    # ---------- 对照组：当前线上行为 ----------
    rep0 = run_once(n=n, seed=42, link=False)
    print("\n[5] 对照：Y 与量表无关（= 当前线上实现的行为）")
    print("    量表题显著：%d 个   人口学显著：%d 个   alpha(总量表)=%.3f"
          % (len(rep0["sig_items"]), len(rep0["sig_covs"]), rep0["alpha_all"]))

    # ---------- 蒙特卡洛稳定性 ----------
    print("\n[6] 蒙特卡洛：连续做 %d 次 200 份，看结果稳定性" % reps)
    counts, alphas, kmos = [], [], []
    for s in range(reps):
        r = run_once(n=n, seed=1000 + s, link=True)
        counts.append(len(r["sig_items"]) + len(r["sig_covs"]))
        alphas.append(r["alpha_all"])
        kmos.append(r["kmo"])
    dist = {}
    for c in counts:
        dist[c] = dist.get(c, 0) + 1
    print("    显著项总数分布：", "  ".join("%d个:%d次" % (k, dist[k]) for k in sorted(dist)))
    print("    均值 %.2f  最小 %d  最大 %d" % (statistics.fmean(counts), min(counts), max(counts)))
    print("    alpha 均值 %.3f  最低 %.3f" % (statistics.fmean(alphas), min(alphas)))
    print("    KMO   均值 %.3f  最低 %.3f" % (statistics.fmean(kmos), min(kmos)))
    ok = sum(1 for i in range(reps) if alphas[i] >= 0.70 and kmos[i] >= 0.60 and 2 <= counts[i] <= 4)
    print("    同时满足「alpha>=0.70 且 KMO>=0.60 且 显著项 2-4 个」的比例：%d/%d = %.0f%%"
          % (ok, reps, 100.0 * ok / reps))

    # ---------- 拒绝采样：把合格率钉到 100% ----------
    print("\n[7] 拒绝采样（整批校验，不达标换种子重来）")
    tries = []
    hit = 0
    for t in range(10):
        rep_v, k, seed = generate_valid_batch(n=n, lo=3, hi=4, seed0=7000 + t)
        if rep_v is not None:
            hit += 1
            tries.append(k)
            cnt = len(rep_v["sig_items"]) + len(rep_v["sig_covs"])
            if t == 0:
                print("    第 1 批：尝试 %d 次命中，显著 %d 个，"
                      "alpha 最低 %.3f，KMO %.3f，VIF 最大 %.2f"
                      % (k, cnt, min(rep_v["alpha_dim"].values()),
                         rep_v["kmo"], max(rep_v["vif"].values())))
    if tries:
        print("    10 批全部命中：%d/10，平均尝试 %.1f 次，最多 %d 次"
              % (hit, statistics.fmean(tries), max(tries)))


if __name__ == "__main__":
    main()
