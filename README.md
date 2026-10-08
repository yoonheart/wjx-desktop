# 问卷星自动刷题系统带可视化界面

基于 Spring Boot + Python(Selenium) 的问卷星自动填写工具。

## 功能

- **问卷解析** — 输入问卷星链接，自动解析所有题型（单选、多选、填空、量表、矩阵、排序等）
- **概率配置** — 为每个选项独立配置分布概率，支持「一键随机概率」
- **异步任务** — 提交后后台执行，前端通过 SSE 实时展示进度，可随时停止
- **详细设置** — 无界面模式、代理开关、时间控制（每份耗时区间 + 一 IP 一份）

> 目标份数范围 1~1000 份/任务，系统全局累计上限 10000 份。

## 项目结构

```
src/main/java/top/yoonheart/
├── WjxSpringbootApplication.java     # 启动入口
├── config/
│   ├── PythonExecutor.java           # Python 脚本调用引擎（脚本定位/解释器探测/超时控制）
│   └── WebConfig.java                # 静态资源映射（/static/** → classpath:/static/）
├── controller/
│   ├── HiController.java             # 页面路由（转发到静态 HTML）
│   ├── AnalysisController.java       # 问卷解析 API
│   ├── BrushController.java          # 刷题任务 API
│   └── BrushProgressController.java  # 进度订阅(SSE) / 停止任务
├── service/
│   └── BrushTaskManager.java         # 任务池、异步执行、进度回传
├── model/
│   └── BrushTask.java                # 任务实体
└── po/
    └── Result.java                   # 统一响应模型

src/main/resources/
├── application.yml                   # 端口 / 日志配置
├── scripts/
│   ├── scan.py                       # 问卷解析脚本
│   └── wjx2.py                       # 自动刷题脚本
└── static/
    ├── index.html                    # 首页
    ├── analysis.html                 # 解析与配置页
    ├── js/                           # 前端 JS（request / api / alert / analysis / probability-validator）
    └── pictures/                     # 使用须知中的题型示例图
```

> 页面为纯静态 HTML，**不使用任何服务端模板引擎**（已从 JSP 迁移）。
> `/` 与 `/analysis` 由 `HiController` 转发到对应静态文件，对外 URL 保持不变；
> 页面数据全部通过 `/api/**` 接口异步获取。

## 环境要求

- **JDK 17+**
- **Maven 3.6+**
- **Python 3.x** 并安装依赖：

```bash
pip install selenium beautifulsoup4 numpy requests
```

- **Microsoft Edge 浏览器**

> Edge WebDriver 为**可选**：默认尝试 `D:\python\msedgedriver.exe`，
> 该文件不存在时自动交给 Selenium Manager 解析（可用环境变量 `WJX_EDGE_DRIVER` 覆盖路径）。
>
> Python 解释器默认依次探测 `python` / `py` / `python3`，
> 也可通过环境变量 `WJX_PYTHON` 或启动参数 `-Dwjx.python=<路径>` 显式指定。

## 使用流程

1. 打开首页，粘贴问卷星链接，点击「开始解析」
2. 在解析结果页为每道题的每个选项配置概率（或使用「一键随机概率」）
3. （可选）点击「功能详细设置」调整无界面模式、代理、时间控制
4. 点击「准备就绪！开刷」，页面切换为进度面板

## 代理 IP 配置

代理 IP 提取链接可在**「功能详细设置 → 代理」**输入框中填写；
未填写时使用 `wjx2.py` 中 `DEFAULT_IP_API` 的内置链接。

> 脚本按**三分钟短效 IP**设计，链接需自行申请。开启「时间控制」后一个 IP 仅填写一份。

## 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/YoonHeart/wjx-springboot.git
cd wjx-springboot

# 2. 安装 Python 依赖
pip install selenium beautifulsoup4 numpy requests

# 3. 配置 Python 解释器与代理 IP 提取链接（可选）

# 4. 启动 Spring Boot
mvn spring-boot:run

# 5. 浏览器访问 http://localhost:8080
```

## 免责声明

本工具仅用于技术研究和学习，不得用于任何非法用途或商业活动。
使用本工具产生的一切后果由使用者自行承担，与开发者无关。
