# -*- coding: utf-8 -*-
"""按目标标定 synth 参数：读导出的 JSON，跑蒙特卡洛，输出达标率。

用法： python tune_synth.py <json路径> [--reps 30] [--write]
"""
import io
import json
import sys
import contextlib
import statistics
import pathlib

sys.path.insert(0, r"D:\zaWu\代码\wjx-springboot\src\main\resources\scripts")
import wjx2  # noqa: E402


def build_config(data, synth):
    single, scale, drop, matrix = {}, {}, {}, {}
    for q in data["questions"]:
        opts = q.get("options") or []
        key = q["id"]
        if q["type"] == "单选题" and opts:
            single[key] = [o.get("probability", 0) for o in opts]
        elif q["type"] == "量表题" and opts:
            scale[key] = [o.get("probability", 0) for o in opts]
        elif q["type"] == "下拉框" and opts:
            drop[key] = [o.get("probability", 0) for o in opts]
        elif q["type"] == "矩阵题" and opts:
            matrix[key] = [[c.get("probability", 0) for c in row.get("options", [])] for row in opts]
    return {"target_num": 200, "single_prob": single, "scale_prob": scale,
            "droplist_prob": drop, "matrix_prob": matrix, "synth": synth}


def run_once(cfg, quiet=True):
    wjx2.config = cfg
    wjx2._synth_spec = None
    wjx2._synth_queue = []
    buf = io.StringIO()
    if quiet:
        with contextlib.redirect_stdout(buf):
            ok = wjx2._synth_prepare()
    else:
        ok = wjx2._synth_prepare()
    return ok, wjx2._synth_stats, buf.getvalue()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\27816\Desktop\Q4wUhCD.json"
    reps = 30
    if "--reps" in sys.argv:
        reps = int(sys.argv[sys.argv.index("--reps") + 1])
    write_back = "--write" in sys.argv

    data = json.load(open(path, encoding="utf-8"))

    synth = {
        "_说明": "合成答卷规格。软件按这里的参数生成每一份答卷并整批校验统计性质。",
        "_toFill": [],
        "enabled": True,
        "outcome": {"question": 21, "positiveOption": [0, 1],
                    "label": "是否考虑留在安徽"},
        "predictors": [6, 9, 11, 13, 14, 23, 25, 26],
        "dimensions": [
            {"name": "经济理性", "items": [22, 23, 24]},
            {"name": "情感与公共服务", "items": [25, 27]},
            {"name": "发展预期与政策", "items": [26, 28]},
        ],
        "dimCorr": [0.46, 0.42, 0.40],
        "targets": {"alphaMin": 0.80, "kmoMin": 0.65, "sigTotal": [3, 4], "sigItems": []},
        "effects": {"23": 0.30, "25": 0.58, "26": 0.70, "11": 0.38},
        "constraints": [
            {"when": {"2": [0]}, "then": {"8": [1]}},
            {"when": {"2": [1, 4]}, "then": {"8": [0]}},
            {"when": {"2": [2, 3, 5, 6, 7]}, "then": {"8": [2]}},
            {"when": {"6": [0, 1]}, "then": {"7": [1]}},
            {"when": {"6": [3]}, "then": {"7": [0]}},
            {"when": {"16": [1]}, "then": {"18": [4]}},
        ],
        "seedSearch": 150,
    }

    cfg = build_config(data, synth)

    print("标定参数：alphaMin=0.80  kmoMin=0.65  sigTotal=[3,4]  n=200")
    print("effects:", synth["effects"])
    print("-" * 66)

    alphas, kmos, counts, hits, times = [], [], [], 0, []
    for i in range(reps):
        ok, st, log = run_once(cfg)
        a = st["min_alpha"]; k = st["kmo"]; c = st["sig_count"]
        good = ok and a >= 0.80 and k >= 0.65 and 3 <= c <= 4
        hits += good
        alphas.append(a); kmos.append(k); counts.append(c)
        if i < 8 or not good:
            attempt = "?"
            for line in log.splitlines():
                if "命中" in line:
                    attempt = line.split("第")[1].split("批")[0]
            print("#%02d α=%.3f KMO=%.3f 显著=%d 命中批次=%s %s"
                  % (i + 1, a, k, c, attempt, "" if good else "  <-- 未达标"))

    print("-" * 66)
    print("达标率 %d/%d = %.0f%%" % (hits, reps, 100.0 * hits / reps))
    print("α  最低 %.3f  均值 %.3f" % (min(alphas), statistics.fmean(alphas)))
    print("KMO 最低 %.3f  均值 %.3f" % (min(kmos), statistics.fmean(kmos)))
    dist = {}
    for c in counts:
        dist[c] = dist.get(c, 0) + 1
    print("显著项分布:", "  ".join("%d个:%d次" % (k, dist[k]) for k in sorted(dist)))

    # 抽一批看完整回归表
    ok, st, log = run_once(cfg)
    print("\n最近一批的回归结果：")
    preds = synth["predictors"]
    for j, q in enumerate(preds):
        beta, z, p = st["fit"][j + 1]
        import math
        print("  Q%-4d OR=%5.2f  z=%6.2f  p=%.4f %s"
              % (q, math.exp(beta), z, p, "**" if p < 0.05 else ""))

    if write_back:
        data["targetCount"] = 200
        data["settings"]["targetCount"] = 200
        data["settings"]["validitySwitch"] = True
        data["settings"]["validityDimensions"] = "22-24,25+27,26+28"
        data["settings"]["validityTarget"] = "0.80"
        data["settings"]["validitySingleQuestions"] = "22-28"
        data["settings"]["validityCorrelation"] = "0.46,0.42;0.40"
        data["settings"]["synthSwitch"] = True
        data["settings"]["synthOutcomeQ"] = "21"
        data["settings"]["synthPositive"] = "0,1"
        data["settings"]["synthPredictors"] = ",".join(str(q) for q in synth["predictors"])
        data["settings"]["synthEffects"] = ", ".join("%s:%s" % (k, v) for k, v in synth["effects"].items())
        data["settings"]["synthSigLo"] = "3"
        data["settings"]["synthSigHi"] = "4"
        data["synth"] = synth

        backup = pathlib.Path(path).with_suffix(".json.bak")
        backup.write_bytes(pathlib.Path(path).read_bytes())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("\n已写回:", path)
        print("已备份:", backup)


if __name__ == "__main__":
    main()
