# -*- coding: utf-8 -*-
"""离线验证合成引擎：用 Q4wUhCD 的真实题目结构跑一遍 _synth_prepare。

不启动浏览器、不联网，只验证「生成 + 校验 + 拒绝采样」这条链路。
"""
import sys
import time

sys.path.insert(0, "src/main/resources/scripts")
import wjx2  # noqa: E402

# 各题选项数（按 https://v.wjx.cn/vm/Q4wUhCD.aspx 的真实结构）
SIZES = {
    1: 2, 2: 9, 3: 3, 4: 11, 6: 4, 7: 3, 8: 3, 9: 5, 10: 3,
    11: 5, 12: 4, 13: 4, 14: 5, 15: 5, 16: 5, 18: 5, 19: 5, 20: 9, 21: 3,
    22: 5, 23: 5, 24: 5, 25: 5, 26: 5, 27: 5, 28: 5,
}

# 边缘分布：中间选项多、两端少，模拟真实作答
def edge(k):
    if k == 2:
        return [48, 52]
    if k == 3:
        return [40, 38, 22]
    if k == 4:
        return [24, 31, 27, 18]
    if k == 5:
        return [12, 20, 30, 24, 14]
    raw = [max(1, 100 // k)] * k
    return raw


single_prob = {str(q): edge(k) for q, k in SIZES.items()}

wjx2.config = {
    "target_num": 200,
    "single_prob": single_prob,
    "scale_prob": {},
    "droplist_prob": {},
    "synth": {
        "enabled": True,
        "outcome": {"question": 21, "positiveOption": [0, 1], "label": "是否考虑留在安徽"},
        "predictors": [6, 9, 11, 13, 14, 23, 25, 26],
        "dimensions": [
            {"name": "经济理性", "items": [22, 23, 24]},
            {"name": "情感与公共服务", "items": [25, 27]},
            {"name": "发展预期与政策", "items": [26, 28]},
        ],
        "dimCorr": [0.46, 0.42, 0.40],
        "targets": {"alphaMin": 0.75, "kmoMin": 0.60, "sigTotal": [2, 4]},
        "effects": {"23": 0.30, "25": 0.58, "26": 0.70, "11": 0.38},
        "constraints": [],
        "seedSearch": 80,
    },
}

t0 = time.time()
ok = wjx2._synth_prepare()
elapsed = time.time() - t0

print("-" * 60)
print("启用结果:", ok, " 耗时: %.2f 秒" % elapsed)
print("队列长度:", len(wjx2._synth_queue))

st = wjx2._synth_stats
if st:
    print("各维度 α:", {k: round(v, 3) for k, v in st["alpha"].items()})
    print("最低 α: %.3f   KMO: %.3f   事件率: %.2f" % (st["min_alpha"], st["kmo"], st["event_rate"]))
    print("显著项: %d 个 -> %s" % (st["sig_count"], ["Q%d" % q for q in st["sig"]]))
    if st["fit"]:
        preds = wjx2._synth_plan()["predictors"]
        print("\n%-6s %8s %8s %8s" % ("变量", "OR", "z", "p"))
        for j, q in enumerate(preds):
            beta, z, p = st["fit"][j + 1]
            import math
            print("Q%-5d %8.2f %8.2f %8.4f%s" % (q, math.exp(beta), z, p, "  **" if p < 0.05 else ""))

# 抽查队列里的前 3 份，确认答案都在合法范围内
print("\n抽样检查前 3 份答卷：")
for row in wjx2._synth_queue[:3]:
    bad = [q for q, v in row.items() if not (isinstance(v, int) and 1 <= v <= SIZES[q])]
    print("  Q21=%s  答案数=%d  越界=%s" % (row.get(21), len(row), bad or "无"))
