# -*- coding: utf-8 -*-
"""合成答卷配置验收器 —— 任何问卷的导出 JSON 都能验。

用法：
    python verify_synth.py <配置.json> [--reps 20]

它做两件事：
1) 结构检查：因变量/自变量/维度/效应是否指向真实存在的题、类型是否合法；
2) 统计验收：用引擎真实生成 N 批，统计 α、KMO、Bartlett、显著项个数，判断是否达到
   配置里写的 targets。模拟的是脚本实际跑的那条路径（同一套 _synth_prepare）。

为什么需要它：AI 能写出看起来合理的 effects，但 α 和 Wald p 必须真算才知道。
不验一遍，等于把「3 个显著」托付给运气。
"""
import io
import json
import sys
import math
import contextlib
import statistics
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                       / "src" / "main" / "resources" / "scripts"))
import wjx2  # noqa: E402


def build_config(data, synth, n=None):
    """按 Java 侧 applyQuestionConfig 的规则把 questions 拆成各题型概率表。"""
    single, scale, drop, matrix = {}, {}, {}, {}
    for q in data.get("questions") or []:
        opts = q.get("options") or []
        key = str(q.get("id"))
        probs = [o.get("probability", 0) for o in opts]
        if q.get("type") == "单选题" and opts:
            single[key] = probs
        elif q.get("type") == "量表题" and opts:
            scale[key] = probs
        elif q.get("type") == "下拉框" and opts:
            drop[key] = probs
        elif q.get("type") == "矩阵题" and opts:
            matrix[key] = [[c.get("probability", 0) for c in row.get("options", [])] for row in opts]
    return {
        "target_num": n or int(data.get("targetCount") or 200),
        "single_prob": single, "scale_prob": scale,
        "droplist_prob": drop, "matrix_prob": matrix,
        "synth": synth,
    }


def bartlett_chi2(batch, qs):
    cols = [[r[q] for r in batch] for q in qs]
    R = wjx2._sm_corr(cols)
    L = wjx2._sm_chol(R)
    det = 1.0
    for i in range(len(L)):
        det *= L[i][i] ** 2
    n, p = len(batch), len(qs)
    chi2 = -(n - 1 - (2 * p + 5) / 6.0) * math.log(max(det, 1e-300))
    return chi2, wjx2._sm_chi2_sf(chi2, p * (p - 1) // 2)


def resolve_item(qi, by_id):
    """题号 -> 题目；矩阵行伪键（题号*100+行号）-> (矩阵题, 行号)。

    返回 (kind, question, row)，kind 是 "q" / "row" / None。
    引擎把矩阵的每一行当成一道独立量表题，所以 700 等价于「第 7 题第 1 行」。
    """
    if qi >= 100:
        qid, row = divmod(qi, 100)
        q = by_id.get(qid)
        if q and q.get("type") == "矩阵题" and row < len(q.get("options") or []):
            return "row", q, row
        return None, None, None
    q = by_id.get(qi)
    return ("q", q, None) if q else (None, None, None)


def inspect(data):
    """结构检查：能不能喂给引擎。返回 (errors, warnings)。"""
    errors, warnings = [], []
    synth = data.get("synth")
    if not isinstance(synth, dict):
        return ["文件里没有 synth 段（可能是老版本导出的，或没走导出流程）"], []
    if not synth.get("enabled"):
        errors.append("synth.enabled 是 false —— 导入后不会启用合成答卷")

    # 样本量：最常见的坑。目标份数还停在默认的 1 时，α / KMO / p 值全是噪声，
    # 后面 effects 调得再准也没意义（实测 1 份时 α=0、KMO=0、显著 0 个）。
    try:
        n_count = int(data.get("targetCount") or 0)
    except (TypeError, ValueError):
        n_count = 0
    if n_count < 100:
        warnings.append("targetCount 只有 %d 份 —— 这个量级下 α、KMO、显著性都只是随机噪声，"
                        "统计目标不可能达成。跑二元回归和信度分析一般要 150~200 份。" % n_count)

    by_id = {}
    for q in data.get("questions") or []:
        try:
            by_id[int(q.get("id"))] = q
        except (TypeError, ValueError):
            continue
    if not by_id:
        return ["questions 为空或题号不是数字"], []

    outcome = synth.get("outcome") or {}
    yq = outcome.get("question")
    try:
        yq = int(yq)
    except (TypeError, ValueError):
        yq = None
    if yq is None:
        errors.append("outcome.question 没填：不知道该拿哪道题当因变量")
    elif yq not in by_id:
        errors.append("outcome.question 指向第 %s 题，但问卷里没有这道题" % yq)
    else:
        q = by_id[yq]
        if q.get("type") not in ("单选题", "量表题", "下拉框"):
            errors.append("因变量第 %s 题是「%s」，二值化只能用单选题/量表题/下拉框"
                          % (yq, q.get("type")))
        k = len(q.get("options") or [])
        pos = outcome.get("positiveOption")
        pos_list = pos if isinstance(pos, list) else [pos]
        if not pos_list or pos_list == [None]:
            errors.append("outcome.positiveOption 没填：无法确定哪个选项记为 1")
        else:
            bad = []
            for v in pos_list:
                try:
                    vi = int(v)
                except (TypeError, ValueError):
                    bad.append(v)
                    continue
                if not (0 <= vi < k):
                    bad.append(v)
            if bad:
                errors.append("positiveOption 里的 %s 超出第 %s 题的选项范围（共 %d 个选项，索引 0~%d）"
                              % (bad, yq, k, k - 1))
            elif len(set(int(v) for v in pos_list)) >= k:
                errors.append("第 %s 题 %d 个选项全被划成 1，没有对照组" % (yq, k))
            elif len(set(int(v) for v in pos_list)) == k - 1:
                warnings.append("第 %s 题只有 1 个选项归入对照组，样本会很偏（建议至少留 2 个）" % yq)

    preds = synth.get("predictors") or []
    if not preds:
        errors.append("predictors 是空的：没有自变量可进回归")
    else:
        missing = [q for q in preds if resolve_item(int(q), by_id)[0] is None]
        if missing:
            errors.append("predictors 里的 %s 在问卷里不存在（矩阵行请写成 题号*100+行号，"
                          "如第 7 题第 1 行是 700）。这些自变量会被引擎静默忽略，"
                          "「N 个变量」会实际变小，显著项也对不上" % missing)
        too_few = [q for q in preds if int(q) in by_id and len(by_id[int(q)].get("options") or []) < 2]
        if too_few:
            errors.append("predictors 里的 %s 选项不足 2 个，无法作为自变量" % too_few)
        if yq is not None and int(yq) in [int(q) for q in preds]:
            errors.append("因变量 Q%s 同时出现在 predictors 里" % yq)
        if len(preds) > 20:
            warnings.append("自变量有 %d 个，样本量不足时不容易收敛（建议 ≤15）" % len(preds))

    effects = synth.get("effects") or {}
    if not effects:
        warnings.append("effects 是空的：所有变量按无效应处理，回归大概率零显著")
    else:
        stray = [k for k in effects if int(k) not in [int(q) for q in preds]]
        if stray:
            warnings.append("effects 里的 %s 不在 predictors 中，会被忽略" % stray)

    dims = synth.get("dimensions") or []
    if not dims:
        warnings.append("dimensions 是空的：没有维度结构，信度只有总量表一个 α")
    for i, d in enumerate(dims):
        # 维度允许两种写法：{"name":..,"items":[..]} 或直接 [[22,23],[25,27]]
        if isinstance(d, dict):
            items = d.get("items") or []
            name = d.get("name") or ("维度%d" % (i + 1))
        else:
            items = list(d or [])
            name = "维度%d" % (i + 1)
        miss = [q for q in items if resolve_item(int(q), by_id)[0] is None]
        if miss:
            errors.append("%s 里的 %s 在问卷里不存在（矩阵行请写成 题号*100+行号）"
                          % (name, miss))
        if len(items) < 2:
            errors.append("%s 只有 %d 个题项，至少需要 2 个" % (name, len(items)))
        elif len(items) < 3:
            warnings.append("%s 只有 2 个题项，KMO 会偏低（3~4 个更稳）" % name)

    targets = synth.get("targets") or {}
    sig = targets.get("sigTotal") or [2, 4]
    try:
        lo, hi = int(sig[0]), int(sig[1])
    except (TypeError, ValueError, IndexError):
        errors.append("targets.sigTotal 格式不对，应写成 [下限, 上限]")
        lo, hi = 2, 4
    if lo < 0 or hi < lo:
        errors.append("targets.sigTotal 区间不合法：%s" % sig)
    elif len(preds) and hi > max(1, int(len(preds) * 0.6)):
        warnings.append("目标显著项上限 %d 相对 %d 个自变量偏多，可能搜不到解"
                        % (hi, len(preds)))
    return errors, warnings


def pseudo_plan(synth, targets, a_req, k_req, lo, hi):
    """把 JSON 里的参数整理成 _synth_diagnose 需要的形状，复用同一套诊断话术。"""
    effects = {}
    for key, val in (synth.get("effects") or {}).items():
        try:
            effects[int(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return {
        "alpha_min": a_req, "kmo_min": k_req, "sig_lo": lo, "sig_hi": hi,
        "sig_items": [int(q) for q in (targets.get("sigItems") or [])],
        "effects": effects,
        "tries": int(synth.get("seedSearch") or 80),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else ""
    if not path:
        print("用法: python verify_synth.py <配置.json> [--reps 20]")
        return 1
    reps = 20
    if "--reps" in sys.argv:
        reps = int(sys.argv[sys.argv.index("--reps") + 1])

    data = json.load(open(path, encoding="utf-8"))
    synth = data.get("synth") or {}

    print("=" * 66)
    print("验收:", path)
    print("=" * 66)

    errors, warnings = inspect(data)
    if warnings:
        print("\n[提示]")
        for w in warnings:
            print("  ·", w)
    if errors:
        print("\n[错误] 这份配置不能直接用：")
        for e in errors:
            print("  ×", e)
        print("\n改完再验一次。")
        return 2
    print("\n[结构] 通过")

    targets = synth.get("targets") or {}
    a_req = float(targets.get("alphaMin", 0.75) or 0.75)
    k_req = float(targets.get("kmoMin", 0.60) or 0.60)
    sig = targets.get("sigTotal") or [2, 4]
    lo, hi = int(sig[0]), int(sig[1])
    n = int(data.get("targetCount") or 200)

    dims = synth.get("dimensions") or []
    dim_items = {d.get("name") or ("维度%d" % (i + 1)): [int(q) for q in (d.get("items") or [])]
                 for i, d in enumerate(dims)}
    all_scale = sorted({q for items in dim_items.values() for q in items})

    cfg = build_config(data, synth, n)
    print("\n目标：α≥%.2f  KMO≥%.2f  显著 %d-%d 个  n=%d" % (a_req, k_req, lo, hi, n))
    print("运行 %d 批验收中..." % reps)

    rows = []
    for _ in range(reps):
        wjx2.config = cfg
        wjx2._synth_spec = None
        wjx2._synth_queue = []
        with contextlib.redirect_stdout(io.StringIO()):
            ok = wjx2._synth_prepare()
        st = wjx2._synth_stats
        if not st:
            print("\n[失败] 引擎没能生成任何数据，检查 synth 配置")
            return 3
        batch = wjx2._synth_queue[:n]
        total_alpha = wjx2._sm_alpha([[r[q] for r in batch] for q in all_scale]) if len(all_scale) >= 2 else float("nan")
        if all_scale:
            chi2, bp = bartlett_chi2(batch, all_scale)
        else:
            chi2, bp = 0.0, 1.0
        good = (st["min_alpha"] >= a_req and st["kmo"] >= k_req and lo <= st["sig_count"] <= hi)
        rows.append({"a": st["min_alpha"], "a_total": total_alpha, "k": st["kmo"],
                     "sig": st["sig_count"], "items": st["sig"], "chi2": chi2, "bp": bp,
                     "good": good, "hit": ok, "st": st})

    ok_n = sum(1 for r in rows if r["good"])
    print("\n[统计验收]")
    print("  α 最低维度   最低 %.3f  均值 %.3f   %s"
          % (min(r["a"] for r in rows), statistics.fmean(r["a"] for r in rows),
             "OK" if min(r["a"] for r in rows) >= a_req else "未达标"))
    tot_vals = [r["a_total"] for r in rows if r["a_total"] == r["a_total"]]
    if tot_vals:
        print("  α 总量表     最低 %.3f  均值 %.3f"
              % (min(tot_vals), statistics.fmean(tot_vals)))
    print("  KMO          最低 %.3f  均值 %.3f   %s"
          % (min(r["k"] for r in rows), statistics.fmean(r["k"] for r in rows),
             "OK" if min(r["k"] for r in rows) >= k_req else "未达标"))
    print("  Bartlett     χ²≈%.0f  p<0.001" % statistics.fmean(r["chi2"] for r in rows))

    dist = {}
    for r in rows:
        dist[r["sig"]] = dist.get(r["sig"], 0) + 1
    print("  显著项个数   " + "  ".join("%d个:%d次" % (k, dist[k]) for k in sorted(dist)))
    always = set.intersection(*[set(r["items"]) for r in rows]) if rows else set()
    sometimes = set.union(*[set(r["items"]) for r in rows]) - always if rows else set()
    print("  每次显著     " + ("、".join("Q%d" % q for q in sorted(always)) or "无"))
    if sometimes:
        print("  偶尔显著     " + "、".join("Q%d" % q for q in sorted(sometimes)))

    print("\n[结论] %d/%d 达标（%.0f%%）" % (ok_n, reps, 100.0 * ok_n / reps))
    if ok_n == reps:
        print("  可以导入使用。")
        return 0
    if ok_n >= reps * 0.7:
        print("  大部分能达标，但不够稳。建议：提高 seedSearch、缩小 sigTotal 区间，")
        print("  或调整 effects（不显著就加大、显著太多就减小）。")
        for line in wjx2._synth_diagnose(pseudo_plan(synth, targets, a_req, k_req, lo, hi),
                                         rows[-1]["st"], data.get("url", "")):
            print(line)
        return 1
    print("  达标率偏低。")
    for line in wjx2._synth_diagnose(pseudo_plan(synth, targets, a_req, k_req, lo, hi),
                                     rows[-1]["st"], data.get("url", "")):
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
