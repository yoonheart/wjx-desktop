# 架构说明

## 1. 分层与依赖方向

```
app/            进程入口：单实例 → 起服务 → 开窗口
shell/          桌面壳：窗口、原生文件对话框、端口守卫
web/            HTTP 边界：路由、SSE、统一信封
runtime/        任务编排：节奏控制、并发闸门、状态推进、事件
protocol/       与目标站点通信：抓取、编码、提交、判定
engine/         答案生成：采样、约束、诊断、闭环控制
domain/         纯模型与枚举，无 IO
storage/        本地持久化：SQLite、方案文件
```

依赖只能向下。`domain` 不 import 任何第三方包；`engine` 不 import 网络库；
`protocol` 不 import 统计模块。任一条被破坏都视为设计缺陷。

## 2. 三条贯穿全系统的约定

### 2.1 中间表示优先

答案不会直接从「配置」变成「一串拼接好的字符串」。中间永远存在 `WireRequest`：

```
Survey + AnswerSheet + Stamp
        │
        ▼  Encoder（纯函数，无网络）
   WireRequest(method, url, params, headers, body, meta)
        │
        ├─▶ 预演：直接序列化给用户看，不发送
        ├─▶ 回放：落盘后逐字段对比
        └─▶ 发送：Submitter 只认 WireRequest，不认问卷
```

因此「编码」可以被单测覆盖，「提交」可以换实现，「预演」是免费的。

### 2.2 状态必须显式

任务与单元各有一套状态机，禁止用布尔组合表达进度。

```
Task:  QUEUED → PREPARING → RUNNING → DRAINING → COMPLETED | STOPPED | FAILED
Unit:  PENDING → ENCODED → SENT → CONFIRMED
                              ├→ RETRYABLE（换出口重发）
                              ├→ FATAL（关闭/限次/需验证）
                              └→ UNKNOWN（进复核队列，不计成功）
```

`UNKNOWN` 是一等状态。任何「看起来成功但无法确认」的结果都进这里，
绝不并入成功计数——成功口径可信是这类工具唯一的价值基础。

### 2.3 配置分层

```
Profile（用户保存的方案，可导出/导入/交给模型改写）
   └─ TaskOverrides（本次运行的覆盖项）
        └─ RuntimeParams（派生：并发、令牌桶、IP 配额、日分布权重）
```

三层都有 schema，都有 `version` 字段与迁移函数链。旧格式通过适配器转换，
不污染主结构。

## 3. 生成器的三层解耦

```
TargetSpec     声明想要的统计性质（α、KMO、显著项数、分布形状、配额）
    │
Generator      怎么生成（独立采样 / 潜因子 / 序贯条件）
    │
Diagnostics    纯函数诊断（α、KMO、Bartlett、单因子占比、描述统计）
    │
Controller     闭环修正：滚动偏差超阈值时反向调整生成参数
```

关键取舍：**不用「整批生成 + 拒绝采样」**，改用「小批量标定 + 在线闭环」。
理由是前者内存占用随目标份数线性增长、首份可用时间晚、失败重试会打乱
已生成队列。闭环方案是 O(1) 内存，边跑边出指标。

## 4. 节奏控制

三个层级各自独立，可分别观测：

| 层级 | 机制 | 默认 |
|---|---|---|
| 全局吞吐 | 令牌桶 | 0.2 份/秒 |
| 份间冷却 | 对数正态（重尾），并叠加日分布权重 | 中位 20s，P90 ≈ 60s |
| 出口配额 | 每出口计数 + 冷却 | 每出口 1~3 份 / 10 分钟 |

用户面对的是「稳 / 标准 / 快」三档画像，不是一堆秒数。
换算与预估由 `runtime/pacing.py` 负责，界面只显示「预计耗时 / 预计出口数」。

## 5. 可观测性

每份提交落一条结构化记录（`storage/db.py` 的 `trace` 表）：

```
task_id, unit_index, seed, exit_addr, ua_profile, item_count,
encode_ms, ttf_ms, status_code, verdict, retry, final_state, reason
```

因为存在 `WireRequest`，出问题时可以完整还原「当时发了什么」。
界面上的「任务详情」直接读这张表，按 `final_state` 分组、按 `reason` 聚合。

## 6. 失败分类

| 枚举 | 用户可见文案 | 建议动作 |
|---|---|---|
| `NETWORK` | 网络不稳，正在重试 | 自动重试 |
| `EXIT_UNUSABLE` | 出口不可用，已换线 | 自动换出口 |
| `THROTTLED` | 触发频率限制，已降速 | 自动降速并冷却 |
| `NEED_VERIFY` | 需要人工验证 | 暂停任务，提示处理 |
| `CLOSED` | 问卷已关闭或未开放 | 终止任务 |
| `LIMIT` | 达到份数或次数上限 | 终止任务 |
| `FORMAT` | 问卷结构变化，解析失败 | 终止并提示升级 |

分类的唯一出口是 `protocol/*/classify.py`，其它层不得再做字符串匹配。

## 7. 命名与词汇

平台字段名属于协议事实，必须照用（`submitdata`、`jqnonce`、`shortid`…）。
其余内部标识统一走下面这张表，避免同一个概念出现三四种叫法：

| 词汇 | 含义 |
|---|---|
| `WireRequest` | 一次待发送请求的完整中间表示 |
| `answer_blob` | 编码后的答案串 |
| `Stamp` | 提交时间标记三元组（开始、停留、发出） |
| `Verdict` | 响应判定结果 |
| `Item` / `AnswerSheet` | 题目 / 答卷 |
| `Pacer` | 节奏控制器 |
| `Seal` | 请求签名钩子 |
| `ExitPool` | 出口（代理）池 |

## 8. 协议事实与实现分离

凡是「平台长什么样」的东西都不写进代码，一律放 `protocol/wjx/manifest.json`：

- DOM 选择器与题型编码映射
- 提交路径、方法、表单字段名、固定参数
- 答案串的分隔符与转义表
- 成功 / 需要验证 / 已关闭 / 达上限 的响应特征串
- 设备 UA 画像

理由是这类东西改版频率远高于业务逻辑：选择器换了只改清单，不动 Python；
换平台则新增一份清单，`protocol/registry.py` 按名字加载。

字段含义与观测记录见 `docs/protocol-notes.md`。

## 9. 测试数据

解析测试用的页面样本自行采集并脱敏：去掉问卷编号、账号、手机号、cookie，
文本替换为占位符，只保留 HTML 结构。断言按自身结构写，不引入外部数据集。

## 10. 结局（因变量）的三种形态

一个结局必须是**一个数**。问卷里能表达这个数的写法有三种，`OutcomeSpec` 统一收口：

| 形态 | 项目编号 | 构造方式 | 统计口径 |
|---|---|---|---|
| 单选题 | `8` | 按综合得分排序，前 `event_rate` 比例记「正面」 | 二分类 / 点二列 |
| 量表题、矩阵行 | `8` 或 `503` | 排名 → 1..n 的**保序重排** | 连续 / Pearson |
| 多维构念 | `[501,502,503]` + `mean` | 每个项目独立重排，再取均值 | 连续 / Pearson |

三条约束：

1. **整道矩阵不能当结局**。一题多行就是多个变量，要么挑一行，要么用 `mean` 合成。
2. **连续结局不能打成 0/1**。二值化会让 Likert 的方差塌掉，α 与 KMO 一起崩；
   保序重排只重排不改变分布形状，方向仍由排名控制。
3. **结局项不计入 α / KMO**。因变量算进自变量量表的信度是方法学错误，
   `GoalSolver._analysis_numbers()` 会把结局项目剔除后再算。

矩阵多行合成时每个项目会加一点独立抖动，否则同一人的几行完全一样，
会被 `constraints.check_matrix_straight` 当成直线作答。

配置侧版本号 `CONFIG_VERSION = 2`：v1 的 `goal.outcome_question` 在读取时
自动搬进 `outcome_items`，老方案文件不需要手工改。

