"""配置说明（供人和模型阅读）。

为什么要有这个文件：方案文件是用户直接编辑、也经常丢给模型改写的对象，
所以字段含义必须能被机器读到——`wjxdesk schema` 直接输出下面的结构。
"""

from __future__ import annotations

from typing import Any

from wjxdesk.domain.strategy import CONFIG_VERSION, default_strategy

_DOCS: dict[str, Any] = {
    "version": {"type": "int", "desc": "配置结构版本，由程序写入，用户不要改"},
    "total": {"type": "int", "desc": "目标份数", "min": 1},
    "concurrency": {"type": "int", "desc": "界面上的「倍速」：同时进行几份", "min": 1, "max": 32},
    "seed": {"type": "int", "desc": "随机种子；0 表示每次运行自动生成。填固定值可复现整批答案"},
    "default_rule": {"type": "object", "desc": "所有题的默认规则，见 item_rule"},
    "items": {"type": "object", "desc": "按题号覆盖规则，键为题号字符串，值见 item_rule"},
    "texts": {"type": "object", "desc": "填空兜底文本池", "fields": {
        "values": {"type": "list[str]", "desc": "候选文本"},
        "min_length": {"type": "int", "desc": "最短长度提示，仅用于校验"},
        "max_length": {"type": "int", "desc": "最长长度提示，仅用于校验"},
    }},
    "targets": {"type": "object", "desc": "统计目标；enabled=false 时只按题目规则填", "fields": {
        "enabled": {"type": "bool", "desc": "是否启用闭环控制"},
        "alpha_min": {"type": "float", "desc": "信度下限"},
        "alpha_max": {"type": "float", "desc": "信度上限；过高反而可疑"},
        "kmo_min": {"type": "float", "desc": "KMO 下限"},
        "sig_min": {"type": "int", "desc": "显著项数下限"},
        "sig_max": {"type": "int", "desc": "显著项数上限"},
        "participant_ratio": {"type": "int", "desc": "样本量/题项数比例建议值"},
        "harman_max": {"type": "float", "desc": "单因子解释占比上限"},
        "noise": {"type": "float", "desc": "刻意保留的噪声，避免数据过于完美"},
    }},
    "pacing": {"type": "object", "desc": "节奏；一般只改 profile", "fields": {
        "profile": {"type": "str", "desc": "steady | standard | fast", "enum": ["steady", "standard", "fast"]},
        "qps": {"type": "float", "desc": "全局每秒份数上限"},
        "median_gap_s": {"type": "float", "desc": "份间冷却中位数（秒）"},
        "jitter_sigma": {"type": "float", "desc": "对数正态抖动，越大越不规则"},
        "per_exit_quota": {"type": "int", "desc": "每个出口最多提交几份"},
        "cooldown_s": {"type": "float", "desc": "出口失败后的冷却秒数"},
        "hour_weights": {"type": "list[24]", "desc": "一天 24 小时的活跃权重，影响提交时刻分布"},
        "seconds_per_item_low": {"type": "float", "desc": "每题停留秒数下限"},
        "seconds_per_item_high": {"type": "float", "desc": "每题停留秒数上限"},
    }},
    "channel": {"type": "object", "desc": "设备与来源画像", "fields": {
        "ua_profiles": {"type": "list[str]", "desc": "取值为 pc / android / ios 的组合"},
        "source_tokens": {"type": "list[str]", "desc": "来源标识候选值"},
        "seal_mode": {"type": "str", "desc": "请求校验策略：none | md5_concat | md5_list"},
    }},
    "quota": {"type": "object", "desc": "人群配额", "fields": {
        "enabled": {"type": "bool", "desc": "是否启用"},
        "targets": {"type": "object", "desc": "{题号: {选项序号: 占比}}"},
    }},
    "goal": {"type": "object", "desc": "数据目标：信效度 + 结论（因变量与影响因素）", "fields": {
        "enabled": {"type": "bool", "desc": "是否按目标构造，而不是纯随机"},
        "alpha_target": {"type": "float", "desc": "目标 Cronbach α，逐维度保证"},
        "kmo_target": {"type": "float", "desc": "目标 KMO"},
        "dimensions": {"type": "list[list[int]]",
                       "desc": "维度分组；项目编号见 goal.outcome_items 的说明。留空=全部可评分项一组"},
        "reverse": {"type": "list[int]", "desc": "反向计分的项目编号"},
        "dimension_correlation": {"type": "float", "desc": "维度间相关，影响 KMO"},
        "outcome_items": {"type": "list[int]",
                          "desc": "因变量项目：题号；矩阵行用 题号*100+行号（第5题第3行=503）；"
                                  "多个项目=多维构念，按 outcome_aggregate 合成"},
        "outcome_aggregate": {"type": "str", "enum": ["single", "mean"],
                              "desc": "single=单题/单行；mean=多项目取均值"},
        "outcome_question": {"type": "int", "desc": "旧字段，等于 outcome_items 首项的题号，读旧方案时自动迁移"},
        "positive_options": {"type": "list[int]",
                             "desc": "只有单选题结局才用：哪些选项算「正面」（记 1，其余记 0）"},
        "predictors": {"type": "list[int]", "desc": "自变量项目编号（量表题或矩阵行）"},
        "directions": {"type": "object", "desc": "{项目编号: \"+\" 正向 / \"-\" 负向 / \"0\" 故意不显著}"},
        "significant_required": {"type": "int", "desc": "至少几项要显著"},
        "event_rate": {"type": "float", "desc": "二分类结局里「正面」的占比，0~1"},
        "attempts": {"type": "int", "desc": "最多重算几次来找达标组合"},
    }},
    "item_rule": {"type": "object", "desc": "单题规则", "fields": {
        "weights": {"type": "list[float]", "desc": "各选项权重，不必归一化"},
        "skew": {"type": "object", "desc": "形状倾向", "fields": {
            "shape": {"type": "str", "desc": "uniform | right | left | center | bimodal"},
            "strength": {"type": "float", "desc": "强度 0~5，0 等于均匀"},
        }},
        "texts": {"type": "list[str]", "desc": "该题的候选文本"},
        "text_weights": {"type": "list[float]", "desc": "文本权重"},
        "unique_group": {"type": "str", "desc": "唯一化分组名，同组内不重复"},
        "value_range": {"type": "list[float,float]", "desc": "数值题取值区间"},
    }},
}


def schema() -> dict[str, Any]:
    """返回可直接交给模型的结构说明 + 一份默认值示例。"""
    return {
        "config_version": CONFIG_VERSION,
        "fields": _DOCS,
        "example": default_strategy().to_dict(),
        "notes": [
            "items 的键是题号字符串，例如 \"3\"。",
            "skew.shape 只对选择题与量表题生效；weights 非空时以 weights 为准。",
            "targets.enabled=true 时，程序会按每 20 份做一次诊断并自动调整生成参数。",
            "seal_mode 需要在完成一次抓包标定后再改，默认 xor_ktimes。",
            "goal.outcome_items 用项目编号：题号，或 题号*100+行号（矩阵行）。"
            "整道矩阵不能当结局——结局必须收成一个数，用单行或 outcome_aggregate=mean 合成。",
            "结局项会自动排除在 α / KMO 之外（因变量不计入自变量量表的信度）。",
        ],
    }
