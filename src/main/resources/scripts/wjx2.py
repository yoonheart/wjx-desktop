import logging
import random
import re
import zlib
import threading
import traceback
from threading import Thread
import time
import json
import sys
import os
import math
import bisect

import requests
from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 软件目录内的 Edge 驱动路径（由 Java 端通过环境变量 WJX_EDGE_DRIVER 注入）。
# 存在时优先使用，避免依赖 Selenium Manager 联网自动下载驱动。
EDGE_DRIVER_PATH = os.environ.get("WJX_EDGE_DRIVER", "")

current_ip = None  # 当前使用的IP
ip_use_count = 0   # 当前IP已使用次数
ip_max_use = 10    # 从配置读取的单个IP最大使用次数
ip_lock = threading.Lock()  # IP操作锁
# 全局强制停止事件：失败次数超过阈值时置位，各线程据此优雅退出（替代原先的 quit() 强杀）
force_stop = threading.Event()

# 线程安全的计数器，替代全局变量
class ThreadSafeCounter:
    def __init__(self):
        self._value = 0
        self._lock = threading.Lock()

    def increment(self):
        with self._lock:
            self._value += 1
            return self._value

    def get(self):
        with self._lock:
            return self._value

    def try_increment(self, limit):
        """原子操作：仅在未达到上限时递增。返回True表示已递增，False表示已达上限"""
        with self._lock:
            if self._value >= limit:
                return False
            self._value += 1
            return True

    def decrement(self):
        """原子递减（不低于0），用于刷题失败时回退预定名额"""
        with self._lock:
            if self._value > 0:
                self._value -= 1
            return self._value

# 初始化线程安全计数器
counter_completed = ThreadSafeCounter()
counter_failed = ThreadSafeCounter()

_progress_write_lock = threading.Lock()
_last_progress_write = 0.0
# 进度写入最小间隔（秒）：避免每完成一份就写一次盘，Java 端 500ms 轮询足够及时
PROGRESS_WRITE_INTERVAL = 1.0


def update_progress(force=False):
    """原子写入进度文件

    :param force: True 时忽略节流立即写入，用于任务结束等关键节点
    """
    global _last_progress_write

    progress_file = config.get("progress_file", "progress.json")
    # 如果没有指定进度文件，则不进行进度更新
    if not progress_file:
        return

    # 节流：距离上次写入不足间隔时间则跳过
    now = time.time()
    with _progress_write_lock:
        if not force and now - _last_progress_write < PROGRESS_WRITE_INTERVAL:
            return
        _last_progress_write = now

    temp_file = progress_file + ".tmp"
    progress_data = {
        "taskId": config.get("taskId", "unknown"),
        "status": "RUNNING",
        "targetCount": config.get("target_num", 3),
        "completedCount": counter_completed.get(),
        "failedCount": counter_failed.get(),
        "currentThread": threading.current_thread().name,
        "timestamp": int(time.time() * 1000)
    }
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False)
        os.replace(temp_file, progress_file)
    except Exception as e:
        # 进度更新失败不应该影响主流程
        print(f"进度更新失败: {e}")


def check_stop_flag():
    """检查是否收到停止信号（外部停止文件，或内部强制停止事件）"""
    if force_stop.is_set():
        return True
    stop_file = config.get("stop_file", "stop.flag")
    # 如果没有指定停止文件，则不进行停止检查
    if not stop_file:
        return False
    return os.path.exists(stop_file)

# 代理 IP 提取链接：不再内置任何默认值。
# 原实现把作者账号凭证（accessName / accessPassword / uid / rid）硬编码在这里，
# 随源码或安装包公开会导致代理套餐被他人盗用，故改为一律由外部提供：
#   优先级：配置 JSON 的 ip_api_url（界面填写） > 环境变量 WJX_IP_API
# 两者都没有时不发请求，直接使用本机 IP。
DEFAULT_IP_API = os.environ.get("WJX_IP_API", "")

# ========== IP 提取节流 ==========
# 代理商普遍限制「每秒最多提取一次」（有的 1 次/5 秒），超限不返回 IP 而是返回一段提示文本。
# 多线程同时启动时，若不加节流，几个线程会在几十毫秒内连续请求，后面几个必然取不到 IP。
# 这里强制「全局同一时刻只有一个请求，且两次请求至少间隔 IP_FETCH_MIN_INTERVAL 秒」。
_ip_fetch_lock = threading.Lock()
_last_ip_fetch_at = 0.0
IP_FETCH_MIN_INTERVAL = 1.0   # 两次提取的最小间隔（秒）


def _wait_ip_fetch_slot():
    """等待到「距上次提取已满最小间隔」再返回，并记下本次提取时刻。

    调用方持有 _ip_fetch_lock 期间才会 sleep，等于把并发提取排成一条 1 秒一格的队。
    """
    global _last_ip_fetch_at
    with _ip_fetch_lock:
        elapsed = time.time() - _last_ip_fetch_at
        wait = IP_FETCH_MIN_INTERVAL - elapsed
        if wait > 0:
            print(f"IP提取节流：等待 {wait:.1f} 秒（避免触发提取频率限制）")
            time.sleep(wait)
        _last_ip_fetch_at = time.time()


def zanip():
    """优化版：加重试+错误处理，确保拿到有效IP"""
    # 界面填写的链接优先，其次环境变量；两者都没有时不发请求，直接使用本机 IP
    api = config.get("ip_api_url") or DEFAULT_IP_API
    if not api:
        print("未配置代理IP提取链接，使用本机IP")
        return None
    max_retry = 3  # 最多重试3次
    retry_count = 0

    while retry_count < max_retry:
        # 每次请求（含重试）都先排队，保证全局 1 秒一次，不并发打提取接口
        _wait_ip_fetch_slot()
        try:
            response = requests.get(api, timeout=10)
            response.raise_for_status()  # 触发HTTP错误
            ip = response.text.strip()
            if validate(ip):
                return ip
            else:
                print(f"IP格式不合法: {ip}")
        except Exception as e:
            print(f"获取IP失败（第{retry_count+1}次重试）: {e}")
        retry_count += 1

    print("多次获取IP失败，使用本机IP")
    return None


# ========== 全局配置（从JSON动态加载） ==========
config = {}

# 每个线程独立的开始填写时间（避免多线程互相覆盖）
thread_local_data = threading.local()

# 新增：全局变量提前初始化（解决多线程读取不到的问题）
lock = threading.Lock()


# ========== 提交来源伪装（链接 / 手机 / 微信） ==========
# 问卷星按 User-Agent 判定答卷来源：桌面 UA = 链接提交，移动 UA = 手机提交，
# UA 里含 MicroMessenger = 微信提交。前端给出三者比例后，脚本按比例给每份答卷挑一个 UA。
#
# ⚠️ 只改 `--user-agent` 字符串是**不够的，而且正是「前端说成功、后台没记录」的元凶**：
#    Chromium 除 UA 之外还会发送 Client Hints（Sec-CH-UA / Sec-CH-UA-Platform /
#    Sec-CH-UA-Mobile），这些头取自浏览器自身，与 --user-agent 无关。
#    一旦 UA 说自己是「iPhone 微信」而 Sec-CH-UA 仍报「Microsoft Edge / Windows」，
#    指纹自相矛盾，问卷星风控会静默丢弃该答卷（页面照样显示提交成功）。
#    正解：用 CDP 的 Network.setUserAgentOverride 把 UA 与 userAgentMetadata 一起下发，
#    让 UA / Client Hints / navigator.userAgentData 三者自洽。
#
# ⚠️ 另一个前提：iOS 的 Safari / 微信 UA **无法**与 Chromium 的 Client Hints 自洽
#    （真机 Safari 根本不发这些头），所以移动端点一律用 Android + Chromium 系 UA——
#    Android 上的 Chrome 和微信 WebView 本来就是 Chromium 内核，会发一致的 CH。
_UA_EDGE = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/118.0.0.0 Safari/537.36",
]
# 桌面 Chrome（问卷星判定为「链接」来源）
_UA_LINK = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]
# 移动 Chrome（「手机」来源）
_UA_MOBILE = [
    "Mozilla/5.0 (Linux; Android 13; SM-S9110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; V2118A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; 2211133C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
]
# 安卓微信内置浏览器（「微信」来源）
_UA_WECHAT = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.44(0x28002a30) NetType/WIFI Language/zh_CN",
    "Mozilla/5.0 (Linux; Android 13; SM-S9110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 MMWEBID/1234 MicroMessenger/8.0.43.2480(0x28002b2b) NetType/4G Language/zh_CN",
    "Mozilla/5.0 (Linux; Android 12; V2118A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.42(0x28002a2c) NetType/WIFI Language/zh_CN",
]

_UA_POOLS = {"link": _UA_LINK, "mobile": _UA_MOBILE, "wechat": _UA_WECHAT}

# 日志里用的来源中文名（与问卷星后台「来源」列的叫法保持一致，便于逐条核对）
_UA_KIND_LABEL = {"desktop": "桌面(链接)", "link": "链接", "mobile": "手机", "wechat": "微信"}


def _ua_device_label(ua):
    """把 UA 压成一句人话，日志里看一眼就知道伪装成了哪台设备。"""
    m = re.search(r"Android\s+([\d.]+);\s*([^;)]+)\)", ua)
    if m:
        return f"Android {m.group(1)} {m.group(2).strip()}"
    if "Macintosh" in ua:
        return "macOS Chrome"
    if "Edge/" in ua:
        return "Windows Edge"
    return "Windows Chrome"

# 注：早期版本这里有个「来源伪装熔断」——只要有一份用手机/微信 UA 的答卷被拒，
# 就把本次任务后续全部退回桌面 UA。实测证明它是错的：被拒的大多是**代理隧道失效**
# （同一时刻同一 IP 上 4 个线程一起被拒），跟伪装身份无关，结果一条坏 IP 就把整个
# 来源比例废掉 —— 后台自然只剩「链接」，看起来像「手机伪装不生效」。
# 现在失败只会重试并换 IP（不影响来源比例），所以熔断机制已移除。


def _stable_index(text, count):
    """按内容稳定取模（不能用 hash()：Python 字符串哈希每次进程启动都变）。"""
    if not text:
        return random.randrange(count)
    return zlib.crc32(str(text).encode("utf-8")) % count


def _ua_metadata(ua):
    """按 UA 字符串生成自洽的 Client Hints 元数据（与 UA 指向同一台设备）。"""
    is_android = "Android" in ua
    is_mac = "Macintosh" in ua

    if is_android:
        platform = "Android"
        m = re.search(r"Android\s+([\d.]+)", ua)
        platform_version = (m.group(1) if m else "13")
        if platform_version.count(".") == 0:
            platform_version += ".0.0"
        m = re.search(r"Android\s+[\d.]+;\s*([^)]+)\)", ua)
        model = (m.group(1).strip() if m else "")
        mobile = True
        architecture, bitness = "arm", ""
    elif is_mac:
        platform, platform_version, model = "macOS", "14.0.0", ""
        mobile = False
        architecture, bitness = "x86", "64"
    else:
        platform, platform_version, model = "Windows", "15.0.0", ""
        mobile = False
        architecture, bitness = "x86", "64"

    m = re.search(r"Chrome/(\d+)", ua)
    major = m.group(1) if m else "120"
    full = f"{major}.0.0.0"
    return {
        "brands": [
            {"brand": "Chromium", "version": major},
            {"brand": "Google Chrome", "version": major},
            {"brand": "Not_A Brand", "version": "8"},
        ],
        "fullVersionList": [
            {"brand": "Chromium", "version": full},
            {"brand": "Google Chrome", "version": full},
            {"brand": "Not_A Brand", "version": "8.0.0.0"},
        ],
        "fullVersion": full,
        "platform": platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": model,
        "mobile": mobile,
        "bitness": bitness,
        "wow64": False,
    }


def pick_ua_profile(local_ip):
    """挑一个 UA 并返回 (ua, client_hints 元数据或 None, 来源类型)。

    未开启「提交来源比例」或已熔断时返回桌面 Edge UA（元数据为 None，无需覆盖 CH）。
    """
    pool, kind = _UA_EDGE, "desktop"
    ratio = config.get("source_ratio")
    if ratio:
        kinds = ("mobile", "link", "wechat")
        weights = [max(0, int(ratio.get(k) or 0)) for k in kinds]
        if sum(weights) > 0:
            kind = random.choices(kinds, weights=weights, k=1)[0]
            pool = _UA_POOLS[kind]

    ua = pool[_stable_index(local_ip, len(pool))]
    # 桌面 Edge 不覆盖：浏览器自己发的 CH 与 UA 本来就一致
    return ua, (_ua_metadata(ua) if kind != "desktop" else None), kind


def pick_user_agent(local_ip):
    """兼容旧调用：只取 UA 字符串。"""
    return pick_ua_profile(local_ip)[0]


# ========== 信效度优化（潜在因子 + 高斯 copula） ==========
# 目标：让量表题/矩阵题的作答内部一致，使其在 SPSS 里能算出合格的 Cronbach α 与因子结构。
# 做法：每份答卷为每个维度抽一个潜在因子 z，同维度的题目共用 z 再各叠加独立噪声，
# 最后用 copula 反查回选项序号 —— 既保留题目原有的边缘分布，又让同维度题目相关。
_validity_ctx = threading.local()   # 本线程当前这份答卷的潜在因子 {维度: z}
_dim_map = None                     # {题号: 维度索引}；None 表示尚未构建
_dim_lambda = {}                    # {维度索引: 载荷 λ}
_dim_corr_chol = None               # 维度相关矩阵的 Cholesky 下三角；None = 各维度互相独立
_dim_order = []                     # 与 _dim_corr_chol 的行列顺序一一对应的维度索引
_reverse_kset = None                # 反向计分题号集合（这些题按 k+1-r 翻转）
_scanned_types = None               # 首次预扫描到的 {题号: 题型码}，后续复用
_scanned_matrix_rows = None         # {矩阵题号: 子行数}，子行数=真实题项数，用于校准强度
_single_scale_qset = None           # 手动指定的「单选题形式的李克特量表」题号集合，None 表示未解析
_plan_lock = threading.Lock()


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_dim_spec(spec):
    """把 "1-5, 6-10" 这样的题号区间解析成 {题号: 维度索引}；留空返回 {}。

    一个逗号/分号/空格分隔的片段 = 一个维度。每个片段支持两种写法：
      · 连续区间：1-5
      · 非连续题号：1+3+5（用加号连接，适合维度题目不相邻的问卷）
    """
    result = {}
    if not spec:
        return result
    groups = [g for g in re.split(r"[,，;；\s]+", str(spec)) if g.strip()]
    for dim_idx, group in enumerate(groups):
        matched = re.match(r"^(\d+)\s*[-~－]\s*(\d+)$", group)
        if matched:
            start, end = int(matched.group(1)), int(matched.group(2))
            if start > end:
                start, end = end, start
            for q in range(start, end + 1):
                result[q] = dim_idx
        elif re.match(r"^\d+(\s*\+\s*\d+)+$", group):
            for q in re.split(r"\s*\+\s*", group):
                result[int(q)] = dim_idx
        elif group.isdigit():
            result[int(group)] = dim_idx
    return result


def _parse_question_numbers(spec):
    """把题号列表解析成集合。支持连续区间（1-5）、加号（1+3+5）、逗号/空格分隔。

    与 _parse_dim_spec 不同，这里不关心维度，只把「被点名的题号」收集成一个集合，
    用于「手动指定哪些单选题其实是李克特量表」这类场景。
    """
    result = set()
    if not spec:
        return result
    # 先去掉 + 号（加号本质是分隔符，且不会与区间冲突）
    text = re.sub(r"\s*\+\s*", ",", str(spec))
    groups = [g for g in re.split(r"[,，;；\s]+", text) if g.strip()]
    for group in groups:
        matched = re.match(r"^(\d+)\s*[-~－]\s*(\d+)$", group)
        if matched:
            start, end = int(matched.group(1)), int(matched.group(2))
            if start > end:
                start, end = end, start
            result.update(range(start, end + 1))
        elif group.isdigit():
            result.add(int(group))
    return result


def _alpha_to_lambda(target, item_count):
    """由目标 Cronbach α 反推潜在因子载荷 λ。

    先按 Spearman-Brown 从目标 α 算出所需的题间相关 ρ，再换成载荷 λ。
    0.88 是实测校准系数：选项被取整、量程有限，实际相关总比 λ² 低一截。
    （离线实测：目标 0.70/0.80/0.85 → 实得 0.70/0.81/0.86，略微偏高以保证达标。）
    """
    n = max(2, int(item_count))
    t = min(max(float(target), 0.30), 0.90)
    rho = t / (n - t * (n - 1))
    rho = min(max(rho, 0.02), 0.90)
    return min(math.sqrt(rho / 0.88), 0.88)


def _normal_edge(k):
    """量表题常用的对称边缘分布（中间选项概率高）。"""
    mid = (k + 1) / 2.0
    sigma = max(1.0, k / 3.0)
    weights = [math.exp(-((i + 1 - mid) ** 2) / (2 * sigma * sigma)) for i in range(k)]
    total = sum(weights)
    return [w / total for w in weights]


def _cum_probs(probs, k):
    """概率列表 -> 累积分布；长度不符或无有效权重时回退到对称边缘分布。"""
    values = None
    if probs:
        try:
            values = [max(0.0, float(x)) for x in probs]
            if len(values) != k or sum(values) <= 0:
                values = None
        except (TypeError, ValueError):
            values = None
    if values is None:
        values = _normal_edge(k)
    total = sum(values)
    acc = 0.0
    cum = []
    for v in values:
        acc += v / total
        cum.append(acc)
    cum[-1] = 1.0
    return cum


def _validity_pick(dim, k, probs):
    """按潜在因子生成一次作答，返回 1..k 的选项序号。"""
    lam = _dim_lambda.get(dim, 0.60)
    z = getattr(_validity_ctx, "factors", {}).get(dim)
    if z is None:
        z = random.gauss(0.0, 1.0)
    noise = random.gauss(0.0, 1.0)
    u = _norm_cdf(lam * z + math.sqrt(max(1e-9, 1.0 - lam * lam)) * noise)
    # copula 逆变换：落在哪个累积区间就选哪个选项
    idx = bisect.bisect_left(_cum_probs(probs, k), u)
    return min(idx + 1, k)


def _maybe_reverse(current, r, k):
    """反向计分题：把选项序号翻转（5 点量表选 1 记为 5、选 2 记为 4）。

    r 是 1..k 的选项序号，k 是这道题（矩阵题则是这一行）的选项数。
    """
    if _reverse_kset and current in _reverse_kset:
        return k + 1 - r
    return r


def _count_matrix_rows(driver, current):
    """数一道矩阵题有几个子行（每个子行 = 一个独立题项）。"""
    try:
        rows = driver.find_elements(By.XPATH, f'//*[@id="divRefTab{current}"]/tbody/tr')
        return sum(1 for tr in rows if tr.get_attribute("rowindex") is not None)
    except Exception:
        return 0


def _prescan_types(driver):
    """首次执行时记录每道可见题的题型码与矩阵题子行数（问卷结构固定，扫一次即可）。"""
    global _scanned_types, _scanned_matrix_rows
    if _scanned_types is not None:
        return
    found = {}
    matrix_rows = {}
    for cur in range(1, config.get("max_question_check", 200) + 1):
        try:
            if not is_question_visible(driver, cur):
                continue
            q_type = driver.find_element(By.CSS_SELECTOR, f"#div{cur}").get_attribute("type")
            if not q_type:
                continue
            found[cur] = q_type
            if q_type == "6":          # 矩阵题：子行数决定它贡献几个题项
                n = _count_matrix_rows(driver, cur)
                if n > 0:
                    matrix_rows[cur] = n
        except Exception:
            continue
    _scanned_types = found
    _scanned_matrix_rows = matrix_rows


def _parse_corr_spec(spec, n):
    """解析「维度间相关」文本，返回 n×n 相关系数矩阵。

    写法：按**上三角**顺序填，分号分行、逗号分列。
      3 个维度：0.45,0.30,0.35;0.40,0.25;0.50
                （ρ12 ρ13 ρ14 ; ρ23 ρ24 ; ρ34 —— 维度数 3 时就是 0.45,0.30;0.35）
    留空或数量对不上时返回 None（= 各维度独立），绝不因为填错就中断刷题。
    """
    if not spec:
        return None
    text = str(spec).strip()
    if not text:
        return None
    if n < 2:
        return None
    try:
        rows = [r for r in text.split(";") if r.strip()]
        values = []
        for row in rows:
            values.extend(float(x) for x in row.split(",") if x.strip())
    except ValueError:
        print("信效度：维度间相关的填写格式不对（只写数字，用逗号分隔、分号分行），本次按互不相关处理")
        return None

    need = n * (n - 1) // 2
    if len(values) != need:
        print("信效度：维度间相关需要 {} 个数值（{} 个维度），实际填了 {} 个，本次按互不相关处理"
              .format(need, n, len(values)))
        return None

    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    k = 0
    for i in range(n):
        for j in range(i + 1, n):
            v = values[k]
            k += 1
            if abs(v) >= 0.95:
                print("信效度：维度{} 与 维度{} 的相关填了 {}，超过 0.95 会变成同一个维度，本次按互不相关处理"
                      .format(i + 1, j + 1, v))
                return None
            matrix[i][j] = matrix[j][i] = v
    return matrix


def _cholesky(matrix):
    """对称正定矩阵的 Cholesky 分解（纯标准库，桌面版脚本没有 numpy）。

    返回下三角 L（满足 L·Lᵀ = R）；矩阵不正定时返回 None。
    """
    n = len(matrix)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                v = matrix[i][i] - s
                if v <= 1e-9:
                    return None
                low[i][j] = math.sqrt(v)
            else:
                if low[j][j] == 0:
                    return None
                low[i][j] = (matrix[i][j] - s) / low[j][j]
    return low


def _build_corr_plan():
    """按 validity_correlation 生成相关因子矩阵；填错/不正定就退回「各维度独立」。"""
    global _dim_corr_chol, _dim_order
    dims = sorted(_dim_lambda)
    _dim_order = dims
    _dim_corr_chol = None
    if len(dims) < 2:
        return
    matrix = _parse_corr_spec(config.get("validity_correlation"), len(dims))
    if not matrix:
        return
    low = _cholesky(matrix)
    if not low:
        print("信效度：维度间相关矩阵不是正定的（几个维度相关加起来太强），本次按互不相关处理"
              "（把其中几个相关调小一点再试）")
        return
    _dim_corr_chol = low
    pairs = []
    for i in range(len(dims)):
        for j in range(i + 1, len(dims)):
            pairs.append("维度{}↔维度{}={}".format(dims[i] + 1, dims[j] + 1, matrix[i][j]))
    print("信效度：维度间相关已启用 —— " + "、".join(pairs))


def _build_dim_plan(driver):
    """构建「维度 -> λ」计划，只在首份问卷做一次。

    规则：
      1. 只有量表题(5) 与矩阵题(6) 能参与信效度优化，其它题型即使被写进维度分组也自动忽略；
      2. 每个维度的强度按「真实题项数」校准 —— 矩阵题的一个子行算一个题项；
      3. 没被任何维度分组覆盖的题，不再默认塞进第 1 个维度，而是按普通概率填写。
    """
    global _dim_map, _dim_lambda, _single_scale_qset, _reverse_kset
    if _dim_map is not None:
        return
    with _plan_lock:
        if _dim_map is not None:
            return
        _prescan_types(driver)
        types = _scanned_types or {}
        rows = _scanned_matrix_rows or {}
        # 手动指定的「单选题形式的李克特量表」：问卷星里 1~5 打分常用单选题录入，
        # 编码是 3，默认被排除；这里允许用户点名，让它们也参与信效度优化。
        _single_scale_qset = _parse_question_numbers(config.get("validity_single_questions"))
        usable = sorted(
            q for q, t in types.items()
            if t in ("5", "6") or q in _single_scale_qset
        )
        usable_set = set(usable)

        raw = _parse_dim_spec(config.get("validity_dimensions"))
        if not raw:
            # 没填维度分组：所有量表题/矩阵题视为同一个维度
            dim_map = {q: 0 for q in usable}
        else:
            # 只保留真正能参与信效度的题
            dim_map = {q: d for q, d in raw.items() if q in usable_set}
            if not dim_map:
                # 填了但一道有效题都没圈到，退回「整体一个维度」，避免优化直接失效
                dim_map = {q: 0 for q in usable}

        counts = {}
        for q, d in dim_map.items():
            counts[d] = counts.get(d, 0) + (rows.get(q, 1) if types.get(q) == "6" else 1)
        target = config.get("validity_target", 0.80)
        _dim_lambda = {d: _alpha_to_lambda(target, c) for d, c in counts.items()}
        _dim_map = dim_map

        # 维度间相关（可选）：不填就是各维度互相独立，与旧版行为一致
        _build_corr_plan()

        # 反向计分题（可选）：这些题的原始作答会被翻转成 k+1-r
        _reverse_kset = _parse_question_numbers(config.get("validity_reverse"))
        if _reverse_kset:
            print("信效度：反向计分题 = {}".format(sorted(_reverse_kset)))


        plan_text = "、".join(
            "维度{}={}".format(d + 1, sorted(q for q, dd in dim_map.items() if dd == d))
            for d in sorted(set(dim_map.values()))
        )
        lam_text = "、".join("{}:{:.3f}".format(d + 1, v) for d, v in sorted(_dim_lambda.items()))
        print("信效度：目标 α={}，{}".format(target, plan_text))
        print("信效度：各维度题项数={}，载荷 λ={}".format(counts, lam_text))
        missing = sorted(q for q in usable if q not in dim_map)
        if missing:
            print("信效度：第 {} 题不在任何维度分组内，将按普通概率填写".format(missing))


# ========== 合成答卷引擎 ==========
# 与「信效度优化」的分工：
#   信效度优化：让量表题彼此一致（潜在因子 + copula），只管题目与题目之间；
#   合成引擎  ：在此之上再加一层「结果模型」——题目与因变量按指定效应量相关，
#               并整批校验 α / KMO / 显著项个数，不达标换种子整批重来。
#
# 为什么必须整批校验：Cronbach α、KMO、logistic 的 Wald p 都是「整批样本」的统计量，
# 单份答卷无论怎么填都控制不了。所以流程是「生成一批 -> 整体算指标 -> 命中就留下，
# 不中就整批丢掉重来」，而不是逐份打磨。
#
# 触发方式：前端导入配置时带 synth 段（见 analysis.js 的 buildSynthSpec / _guide），
# Java 侧原样透传，这里读到 config["synth"] 就接管全部题目作答。

_synth_spec = None            # 当前生效的合成规格（None = 未启用）
_synth_queue = []             # 预生成好的答卷队列，每项 {题号(int): 答案}
_synth_lock = threading.Lock()
_synth_stats = None           # 最近一次的校验结果，用于日志


# ---------- 合成引擎用的数学工具（纯标准库，不引入 numpy） ----------

def _sm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sm_gamma_p(a, x):
    """正则化下不完全伽马 P(a,x)，用于卡方分布的尾概率。"""
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
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def _sm_chi2_sf(x, df):
    return max(0.0, min(1.0, 1.0 - _sm_gamma_p(df / 2.0, x / 2.0)))


def _sm_chol(A):
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(A[i][i] - s, 1e-12))
            else:
                L[i][j] = (A[i][j] - s) / L[j][j]
    return L


def _sm_solve(A, b):
    """高斯消元解 Ax=b（带部分主元）。"""
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


def _sm_inv(A):
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


def _sm_corr(cols):
    """cols: 每个变量一列。返回相关矩阵。"""
    p = len(cols)
    n = len(cols[0])
    means = [sum(c) / n for c in cols]
    sds = []
    for c, m in zip(cols, means):
        sds.append(math.sqrt(max(1e-12, sum((x - m) ** 2 for x in c) / n)))
    R = [[1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]
    for i in range(p):
        for j in range(i + 1, p):
            s = sum((cols[i][k] - means[i]) * (cols[j][k] - means[j]) for k in range(n)) / n
            r = max(-0.999, min(0.999, s / (sds[i] * sds[j])))
            R[i][j] = R[j][i] = r
    return R


def _sm_alpha(cols):
    """Cronbach α。cols 是一组题项，每个是长度 n 的作答序列。"""
    k = len(cols)
    if k < 2:
        return float("nan")
    n = len(cols[0])
    total = [sum(cols[i][r] for i in range(k)) for r in range(n)]
    mt = sum(total) / n
    vt = sum((x - mt) ** 2 for x in total) / (n - 1) if n > 1 else 0.0
    if vt <= 1e-12:
        return 0.0
    vs = 0.0
    for c in cols:
        m = sum(c) / n
        vs += sum((x - m) ** 2 for x in c) / (n - 1) if n > 1 else 0.0
    return k / (k - 1.0) * (1.0 - vs / vt)


def _sm_kmo(cols):
    """KMO 取样适切性。偏相关由相关矩阵的逆得到。"""
    R = _sm_corr(cols)
    p = len(R)
    if p < 2:
        return float("nan")
    try:
        Ri = _sm_inv(R)
    except ValueError:
        return float("nan")
    sr = sq = 0.0
    for i in range(p):
        for j in range(p):
            if i == j:
                continue
            sr += R[i][j] ** 2
            q = -Ri[i][j] / math.sqrt(max(1e-12, Ri[i][i] * Ri[j][j]))
            sq += q * q
    return sr / (sr + sq) if (sr + sq) > 0 else 0.0


def _sm_logistic(X, y):
    """IRLS 拟合 logistic 回归。返回 [(beta, z, p)]，第一个是截距。"""
    n = len(X)
    p = len(X[0])
    beta = [0.0] * p
    A = None
    for _ in range(50):
        eta = [sum(X[i][j] * beta[j] for j in range(p)) for i in range(n)]
        pr = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e)))) for e in eta]
        A = [[sum(X[i][a] * X[i][b] * pr[i] * (1.0 - pr[i]) for i in range(n))
              for b in range(p)] for a in range(p)]
        g = [sum(X[i][a] * (y[i] - pr[i]) for i in range(n)) for a in range(p)]
        try:
            delta = _sm_solve(A, g)
        except ValueError:
            return None
        beta = [beta[j] + delta[j] for j in range(p)]
        if max(abs(v) for v in delta) < 1e-9:
            break
    try:
        Ai = _sm_inv(A)
    except ValueError:
        return None
    out = []
    for j in range(p):
        se = math.sqrt(max(Ai[j][j], 1e-12))
        z = beta[j] / se
        out.append((beta[j], z, 2.0 * (1.0 - _sm_cdf(abs(z)))))
    return out


# ---------- 结构解析 ----------

def _synth_marginals():
    """从各题型概率表里取出「可作答且有边缘分布」的题。

    返回 {题号(int): (选项数 k, 累积分布 list)}。
    量表题优先用 scale_prob，其余用 single_prob / droplist_prob —— 与前端导出一致。
    """
    result = {}
    for key in ("scale_prob", "single_prob", "droplist_prob"):
        table = config.get(key) or {}
        if not isinstance(table, dict):
            continue
        for qid, probs in table.items():
            try:
                qi = int(qid)
            except (TypeError, ValueError):
                continue
            if qi in result or not isinstance(probs, list) or len(probs) < 2:
                continue
            vals = []
            for v in probs:
                try:
                    vals.append(max(0.0, float(v)))
                except (TypeError, ValueError):
                    vals.append(0.0)
            total = sum(vals)
            if total <= 0:
                vals = _normal_edge(len(probs))
                total = sum(vals)
            cum, acc = [], 0.0
            for v in vals:
                acc += v / total
                cum.append(acc)
            cum[-1] = 1.0
            result[qi] = (len(probs), cum)

    # 矩阵题：每一行相当于一道独立的 1..k 量表题（"薪资待遇 1-5 分"就是一道题）。
    # 键编码成「题号 * 100 + 行号」，这样它能像普通量表题那样进维度、进回归、
    # 参与 α / KMO 计算；真实题号一般 < 100，不会撞。
    matrix = config.get("matrix_prob") or {}
    if isinstance(matrix, dict):
        for qid, rows in matrix.items():
            qi = _safe_int(qid, None)
            if qi is None or not isinstance(rows, list):
                continue
            for ri, row in enumerate(rows):
                if not isinstance(row, list) or len(row) < 2:
                    continue
                key = qi * 100 + ri
                if key in result:
                    continue
                vals = []
                for v in row:
                    try:
                        vals.append(max(0.0, float(v)))
                    except (TypeError, ValueError):
                        vals.append(0.0)
                total = sum(vals)
                if total <= 0:
                    vals = _normal_edge(len(row))
                    total = sum(vals)
                cum, acc = [], 0.0
                for v in vals:
                    acc += v / total
                    cum.append(acc)
                cum[-1] = 1.0
                result[key] = (len(row), cum)
    return result


def _synth_apply_constraints(ans, rules, marg, rng, rounds=3):
    """按 constraints 修正答案，让作答在人口学上自洽。

    规则形如 {"when": {"2": [0,1,2]}, "then": {"8": [0]}}（选项索引均 0 基）。
    when 里出现 "*" 表示兜底分支。跑多轮是为了处理 A->B、B->C 这种链式规则。
    """
    if not isinstance(rules, list) or not rules:
        return
    for _ in range(rounds):
        changed = False
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            when = rule.get("when") or {}
            then = rule.get("then") or {}
            if not isinstance(when, dict) or not isinstance(then, dict) or not then:
                continue
            matched, wildcard_only = True, True
            for q, allowed in when.items():
                if str(q) == "*":
                    continue
                wildcard_only = False
                try:
                    qi = int(q)
                except (TypeError, ValueError):
                    matched = False
                    break
                v = ans.get(qi)
                if v is None or not isinstance(allowed, list):
                    matched = False
                    break
                if v not in [_safe_int(x) + 1 for x in allowed]:
                    matched = False
                    break
            if not matched or (wildcard_only and "*" not in when):
                continue
            for q, allowed in then.items():
                try:
                    qi = int(q)
                except (TypeError, ValueError):
                    continue
                if qi not in marg or not isinstance(allowed, list):
                    continue
                k = marg[qi][0]
                opts = []
                for x in allowed:
                    xi = _safe_int(x)
                    if 0 <= xi < k:
                        opts.append(xi + 1)
                if opts:
                    new_v = rng.choice(opts)
                    if ans.get(qi) != new_v:
                        ans[qi] = new_v
                        changed = True
        if not changed:
            break


def _safe_int(v, default=-1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _synth_plan():
    """解析 synth 规格，返回生成所需的全部结构；配置不可用时返回 None。"""
    spec = config.get("synth")
    if not isinstance(spec, dict) or not spec.get("enabled"):
        return None

    outcome = spec.get("outcome") or {}
    yq = _safe_int(outcome.get("question"), None)
    # positiveOption 可以写单个索引，也可以写数组 —— 数组表示「这几个选项合并记为 1」。
    # 例：Q21 有「是，第一选择 / 是，会考虑 / 倾向省外」三档，
    # 做「是否考虑留安徽」的二元回归时写 [0, 1] 即可。
    raw_pos = outcome.get("positiveOption", outcome.get("positiveOptions"))
    if yq is None or raw_pos is None:
        print("合成答卷：缺少因变量或正向选项（synth.outcome），本次不启用")
        return None
    pos_set = set()
    for v in (raw_pos if isinstance(raw_pos, list) else [raw_pos]):
        vi = _safe_int(v, None)
        if vi is not None:
            pos_set.add(vi + 1)  # 前端 0 基 -> 站内 1 基

    marg = _synth_marginals()
    if yq not in marg:
        print(f"合成答卷：因变量 Q{yq} 没有可用的选项概率配置，本次不启用")
        return None
    k_y = marg[yq][0]
    if not pos_set or not pos_set <= set(range(1, k_y + 1)):
        print(f"合成答卷：正向选项 {sorted(v - 1 for v in pos_set)} 超出 Q{yq} 的 {k_y} 个选项，本次不启用")
        return None
    if len(pos_set) >= k_y:
        print(f"合成答卷：Q{yq} 的 {k_y} 个选项全被划为正向，没有对照组，本次不启用")
        return None

    # 维度：只保留至少 2 个有效题项的分组，编号重排，避免空维度把 λ 编号搞乱
    dim_list, dim_map = [], {}
    for d in (spec.get("dimensions") or []):
        # 维度有两种常见写法：[{"name":"经济","items":[22,23]}] 和 [[22,23],[25,27]]。
        # 后者是「一组题号就是一个维度」的简写，AI 和人手写时都爱这么写，
        # 以前只认前者，遇到简写会直接抛 AttributeError 让整份配置作废。
        if isinstance(d, dict):
            raw_items = d.get("items") or []
        elif isinstance(d, (list, tuple)):
            raw_items = d
        else:
            continue
        items = []
        for q in raw_items:
            qi = _safe_int(q, None)
            if qi is not None and qi in marg:
                items.append(qi)
        if len(items) >= 2:
            di = len(dim_list)
            dim_list.append(items)
            for qi in items:
                dim_map[qi] = di

    # 目标 α 略高于下限，抵消选项取整带来的衰减（_alpha_to_lambda 内已有 0.88 校准）
    targets = spec.get("targets") or {}
    alpha_min = float(targets.get("alphaMin", 0.75) or 0.75)
    kmo_min = float(targets.get("kmoMin", 0.60) or 0.60)
    lam = {di: _alpha_to_lambda(min(0.90, alpha_min + 0.05), len(items))
           for di, items in enumerate(dim_list)}

    # 维度相关矩阵：按上三角顺序读取，不是正定就退回独立
    nd = len(dim_list)
    sigma = [[1.0 if i == j else 0.0 for j in range(nd)] for i in range(nd)]
    corr_vals = spec.get("dimCorr") or []
    need = nd * (nd - 1) // 2
    if nd > 1 and isinstance(corr_vals, list) and len(corr_vals) >= need:
        idx = 0
        for i in range(nd):
            for j in range(i + 1, nd):
                v = max(-0.95, min(0.95, float(corr_vals[idx])))
                sigma[i][j] = sigma[j][i] = v
                idx += 1
        try:
            _sm_chol(sigma)
        except (ValueError, ZeroDivisionError):
            print("合成答卷：维度间相关矩阵不是正定的，本次按维度独立处理")
            sigma = [[1.0 if i == j else 0.0 for j in range(nd)] for i in range(nd)]

    # 效应量
    effects = {}
    for k, v in (spec.get("effects") or {}).items():
        qi = _safe_int(k, None)
        if qi is None:
            continue
        try:
            effects[qi] = float(v)
        except (TypeError, ValueError):
            continue

    # 自变量：以 predictors 为准（effects 里多出来的忽略）
    predictors, dropped = [], []
    for q in (spec.get("predictors") or []):
        qi = _safe_int(q, None)
        if qi is None:
            continue
        if qi in marg and qi != yq and qi not in predictors:
            predictors.append(qi)
        else:
            dropped.append(qi)
    # 静默丢弃最危险：配置写「8 个自变量」，实际只跑了 4 个，显著项自然对不上，
    # 而日志里看不出任何异常。所以这里必须把被丢的题号喊出来。
    if dropped:
        print("⚠️ 合成答卷：这些自变量在问卷里找不到对应题项，已忽略：%s"
              % "、".join("Q%d" % q for q in dropped))
        if any(q >= 100 for q in dropped):
            print("⚠️    形如 700 / 1200 的是「矩阵行」写法（题号×100+行号），"
                  "只有当该题确实是矩阵题时才有效；本问卷里它不是矩阵题，"
                  "请改回真实题号。")
    if predictors and len(dropped) > len(predictors):
        print("⚠️ 合成答卷：被忽略的自变量比有效的还多，配置可能来自另一份问卷，建议核对题号")
    if not predictors:
        print("合成答卷：predictors 为空（或因变量被误写进自变量），本次不启用")
        return None

    sig = targets.get("sigTotal") or [2, 4]
    lo = _safe_int(sig[0] if len(sig) > 0 else 2, 2)
    hi = _safe_int(sig[1] if len(sig) > 1 else 4, 4)
    if lo > hi:
        lo, hi = hi, lo

    # 指定必须显著的题：光控制「个数」不够 —— 同样 3 个显著，可能是三道有故事的题，
    # 也可能混进两个噪声变量，后者在论文里讲不通。这里把「哪几题必须显著」也作为硬条件。
    sig_items = []
    for q in (targets.get("sigItems") or []):
        qi = _safe_int(q, None)
        if qi is not None and qi in predictors and qi not in sig_items:
            sig_items.append(qi)
        elif qi is not None:
            print(f"⚠️ 合成答卷：sigItems 里的 Q{qi} 不在自变量列表中，已忽略")
    if len(sig_items) > hi:
        print(f"合成答卷：sigItems 有 {len(sig_items)} 个，超过显著项上限 {hi}，自动放宽上限")
        hi = len(sig_items)

    return {
        "yq": yq, "pos_set": pos_set, "marg": marg,
        "dim_list": dim_list, "dim_map": dim_map, "nd": nd,
        "lam": lam, "chol": _sm_chol(sigma),
        "effects": effects, "predictors": predictors,
        "constraints": spec.get("constraints") or [],
        "alpha_min": alpha_min, "kmo_min": kmo_min,
        "sig_lo": lo, "sig_hi": hi, "sig_items": sig_items,
        "tries": max(10, min(300, _safe_int(spec.get("seedSearch"), 80))),
        "label": str(outcome.get("label") or f"Q{yq}"),
    }


# ---------- 生成与校验 ----------

def _synth_rate_of(cum, subset):
    """配置的边缘分布下，subset（1 基选项）合计占的概率。

    用于回答「按这份问卷的概率设置，因变量取 1 的比例应该是多少」。
    """
    prev, total = 0.0, 0.0
    for i in range(1, len(cum) + 1):
        w = max(0.0, cum[i - 1] - prev)
        prev = cum[i - 1]
        if i in subset:
            total += w
    return total


def _synth_solve_intercept(etas, target):
    """二分搜索截距，使 mean(sigmoid(eta + b0)) == target。

    直接取 logit(target) - mean(eta) 是近似值，因为 sigmoid 是非线性的，
    会在 eta 方差大时偏移几个百分点。二分 50 轮成本极低，直接算准。
    """
    lo, hi = -30.0, 30.0
    n = float(len(etas)) or 1.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        s = 0.0
        for e in etas:
            s += 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, e + mid))))
        if s / n < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _synth_pick_subset(rng, cum, subset):
    """在 subset 内按该题原有边缘分布加权抽一个 1 基选项。

    因变量常常要把多个选项合并成「1」（如三档里的前两档），
    合并后仍按原始概率分配，才不会被压成均匀分布。
    """
    subset = sorted(subset)
    if not subset:
        return 1
    weights, prev = [], 0.0
    for i in range(1, len(cum) + 1):
        w = max(0.0, cum[i - 1] - prev)
        prev = cum[i - 1]
        if i in subset:
            weights.append(w)
    total = sum(weights)
    if total <= 0 or len(weights) != len(subset):
        return rng.choice(subset)
    u = rng.random() * total
    acc = 0.0
    for i, w in zip(subset, weights):
        acc += w
        if u <= acc:
            return i
    return subset[-1]


def _synth_generate(n, plan, rng):
    """生成 n 份答卷。返回 [{题号: 1 基选项序号}]。"""
    marg = plan["marg"]
    dim_map = plan["dim_map"]
    lam = plan["lam"]
    chol = plan["chol"]
    nd = plan["nd"]
    yq = plan["yq"]
    pos_opts = sorted(plan["pos_set"])
    effects = plan["effects"]

    batch, etas = [], []
    for _ in range(n):
        # 潜在因子：维度间相关由 Cholesky 展开
        if nd:
            z = [rng.gauss(0.0, 1.0) for _ in range(nd)]
            factors = [sum(chol[i][j] * z[j] for j in range(i + 1)) for i in range(nd)]
        else:
            factors = []

        ans = {}
        for qid, (k, cum) in marg.items():
            if qid == yq:
                continue
            d = dim_map.get(qid)
            if d is not None:
                l = lam.get(d, 0.6)
                u = _sm_cdf(l * factors[d]
                            + math.sqrt(max(1e-9, 1.0 - l * l)) * rng.gauss(0.0, 1.0))
            else:
                u = rng.random()
            ans[qid] = bisect.bisect_left(cum, u) + 1

        _synth_apply_constraints(ans, plan["constraints"], marg, rng)

        # 结果模型：Y 由题目取值线性组合后过 sigmoid
        eta = 0.0
        for qid, b in effects.items():
            v = ans.get(qid)
            if v is None or qid not in marg:
                continue
            k = marg[qid][0]
            eta += b * (v - (k + 1) / 2.0)   # 以量表中点为中心，避免截距被抬高
        etas.append(eta)
        batch.append(ans)

    # 截距标定：让事件率贴合「因变量概率配置」推出来的比例，而不是固定 50%。
    #
    # 固定 50% 会让软件生成的「留皖比例」和用户配的分布完全对不上
    # （配 38/36/26 = 74%，实际出来 50%），描述统计第一张表就露了。
    # 但事件率太极端时 EPV 不足、模型会完全分离，所以限制在 [0.20, 0.80] 并明确告警。
    target_rate = _synth_rate_of(marg[yq][1], plan["pos_set"])
    if target_rate <= 0.0:
        target_rate = 0.5
    lo_r, hi_r = 0.20, 0.80
    if target_rate < lo_r or target_rate > hi_r:
        clamped = max(lo_r, min(hi_r, target_rate))
        print(f"合成答卷：因变量配置的概率对应的正向比例是 {target_rate:.0%}，"
              f"过于极端会让回归无法收敛，已按 {clamped:.0%} 生成")
        target_rate = clamped
    b0 = _synth_solve_intercept(etas, target_rate)

    other_opts = [i for i in range(1, marg[yq][0] + 1) if i not in plan["pos_set"]]
    for i, ans in enumerate(batch):
        p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, etas[i] + b0))))
        target = pos_opts if rng.random() < p else other_opts
        ans[yq] = _synth_pick_subset(rng, marg[yq][1], target)

    # 矩阵题：把逐行的答案（键 = 题号*100+行号）聚合成 {行号: 选项}。
    # matrix() 提交时就是按这个 dict 逐行点选，所以伪键只用于统计，提交靠这里。
    matrix_qids = set()
    for qid in (config.get("matrix_prob") or {}):
        qi = _safe_int(qid, None)
        if qi is not None:
            matrix_qids.add(qi)
    if matrix_qids:
        for ans in batch:
            for qi in matrix_qids:
                row_vals, ri = {}, 0
                while (qi * 100 + ri) in ans:
                    row_vals[ri] = ans[qi * 100 + ri]
                    ri += 1
                if row_vals:
                    ans[qi] = row_vals
    return batch


def _synth_measure(batch, plan):
    """算一批答卷的信效度与显著项个数。"""
    dim_list = plan["dim_list"]
    alphas = {}
    for di, items in enumerate(dim_list):
        cols = [[ans[q] for ans in batch] for q in items]
        alphas[di] = _sm_alpha(cols)
    valid = [a for a in alphas.values() if a == a]
    min_alpha = min(valid) if valid else 0.0

    all_items = sorted(plan["dim_map"])
    kmo = _sm_kmo([[ans[q] for ans in batch] for q in all_items]) if len(all_items) >= 2 else 0.0
    if kmo != kmo:
        kmo = 0.0
    # 总量表 α：把全部量表题当一个量表算（SPSS 里最常见的一种做法）。
    # 分维度都达标不代表合并后也达标，有些用户就是直接对 7 道题跑信度，所以单独算一遍。
    total_alpha = _sm_alpha([[ans[q] for ans in batch] for q in all_items]) if len(all_items) >= 2 else float("nan")

    preds = plan["predictors"]
    yq, pos_set = plan["yq"], plan["pos_set"]
    X = [[1.0] + [float(ans[q]) for q in preds] for ans in batch]
    y = [1 if ans[yq] in pos_set else 0 for ans in batch]
    fit = _sm_logistic(X, y)

    sig = []
    if fit:
        for j, q in enumerate(preds):
            if fit[j + 1][2] < 0.05:
                sig.append(q)
    return {
        "alpha": alphas, "min_alpha": min_alpha, "total_alpha": total_alpha, "kmo": kmo,
        "sig": sig, "sig_count": len(sig),
        "event_rate": (sum(y) / float(len(y))) if y else 0.0,
        "fit": fit,
    }


def _synth_diagnose(plan, st, url=""):
    """把「差在哪、该怎么改」整理成一段可以直接复制给 AI 的文本。

    排错信息最怕只说「没达标」—— 用户要么自己猜，要么把一堆日志粘给 AI 还得解释半天。
    这里直接生成一段自包含的说明：目标是什么、实测多少、差在哪、对应改哪个字段、当前值是多少。
    """
    def f(v, nd=3):
        return ("%." + str(nd) + "f") % v if isinstance(v, float) and v == v else str(v)

    lines = ["", "========== 以下整段可复制给 AI，让它改 JSON =========="]
    lines.append("【合成答卷配置需要调整】" + (("问卷 " + str(url)) if url else ""))
    req = ("目标：α≥%.2f、KMO≥%.2f、显著 %d-%d 个"
           % (plan["alpha_min"], plan["kmo_min"], plan["sig_lo"], plan["sig_hi"]))
    if plan["sig_items"]:
        req += "，其中 " + "、".join("Q%d" % q for q in plan["sig_items"]) + " 必须显著"
    lines.append(req)

    total_alpha = st.get("total_alpha", float("nan"))
    lines.append("实测最好的一批：α最低维度 %s、α总量表 %s、KMO %s、显著 %d 个 [%s]"
                 % (f(st.get("min_alpha")), f(total_alpha), f(st.get("kmo")),
                    st.get("sig_count", 0),
                    ",".join("Q%d" % q for q in st.get("sig") or [])))
    lines.append("差距与改法：")

    tips = []
    if st.get("min_alpha", 0) < plan["alpha_min"]:
        tips.append("α 最低维度 %s < %.2f：把 dimensions 里不足 3 个题项的组补到 3 个"
                    "（2 个题项的组 α 上不去），或把 targets.alphaMin 降到 %.2f"
                    % (f(st.get("min_alpha")), plan["alpha_min"],
                       max(0.60, plan["alpha_min"] - 0.05)))
    if total_alpha == total_alpha and total_alpha < plan["alpha_min"]:
        tips.append("α 总量表 %s < %.2f：维度之间太不相关时合并 α 会掉，"
                    "把 dimCorr 的各值提到 0.40~0.50" % (f(total_alpha), plan["alpha_min"]))
    if st.get("kmo", 0) < plan["kmo_min"]:
        tips.append("KMO %s < %.2f：把 dimCorr 提到 0.40~0.50，并保证每个维度至少 3 个题项"
                    % (f(st.get("kmo")), plan["kmo_min"]))
    if st.get("sig_count", 0) < plan["sig_lo"]:
        tips.append("显著只有 %d 个，少于下限 %d：调大 effects 里关键变量的系数"
                    "（5 点量表 0.55~0.70、3~5 级人口学 0.35~0.40、二分类 0.70 左右）"
                    % (st.get("sig_count", 0), plan["sig_lo"]))
    if st.get("sig_count", 0) > plan["sig_hi"]:
        tips.append("显著有 %d 个，超过上限 %d：把次要变量的 effects 系数降到 0.15 以下；"
                    "同维度共线会把系数放大 1.5~2 倍，主要变量也别堆太高"
                    % (st.get("sig_count", 0), plan["sig_hi"]))
    missing = [q for q in plan["sig_items"] if q not in (st.get("sig") or [])]
    if missing:
        tips.append("指定必须显著的 %s 没显著：把这几题的 effects 系数调大（量表题先试 0.65）"
                    % "、".join("Q%d" % q for q in missing))
    if not tips:
        tips.append("各项都接近目标，把 targets.seedSearch 从 %d 提到 %d 再试"
                    % (plan["tries"], min(300, plan["tries"] * 2)))
    for t in tips:
        lines.append("  - " + t)

    lines.append("当前 effects = " + json.dumps(
        {str(k): v for k, v in plan["effects"].items()}, ensure_ascii=False))
    lines.append("当前 targets = " + json.dumps({
        "alphaMin": plan["alpha_min"], "kmoMin": plan["kmo_min"],
        "sigTotal": [plan["sig_lo"], plan["sig_hi"]],
        "sigItems": plan["sig_items"]}, ensure_ascii=False))
    lines.append("字段说明：effects 的键是题号、值是 logit 系数（正数=提高 Y=1 的概率）。")
    lines.append("请只修改 JSON 里的 synth 段（effects / targets / dimensions / dimCorr），")
    lines.append("questions 里的 id、type、title、options 不要动。改完输出完整 JSON 文件。")
    lines.append("（以上连同 JSON 文件一起发给 AI）")
    lines.append("==========")
    return lines


def _synth_prepare():
    """在 run() 开头调用：整批生成 + 校验 + 拒绝采样，结果放进 _synth_queue。"""
    global _synth_spec, _synth_queue, _synth_stats
    plan = _synth_plan()
    if plan is None:
        return False

    n = max(1, int(config.get("target_num", 10)))
    # 多备一截：提交失败重试会从队列里取走答卷，如果恰好只生成 n 份，
    # 尾部几份就会因为队列空了而退回普通概率模式，变成纯噪声样本、拉低 α 和显著性。
    # 统计指标仍按「目标份数」的前 n 份来算，保证验收口径和用户实际拿到的一致。
    n_pool = n + max(10, int(n * 0.35))
    rng = random.Random()
    pos_text = "+".join(str(v - 1) for v in sorted(plan["pos_set"]))
    print(f"合成答卷：Y={plan['label']}（Q{plan['yq']}，选项 {pos_text} 记为 1），"
          f"自变量 {len(plan['predictors'])} 个，维度 {plan['nd']} 个，目标 {n} 份")
    req_text = ("，其中 " + "、".join("Q%d" % q for q in plan["sig_items"]) + " 必须显著"
                if plan["sig_items"] else "")
    print(f"合成答卷：目标 α≥{plan['alpha_min']:.2f}，KMO≥{plan['kmo_min']:.2f}，"
          f"显著项 {plan['sig_lo']}-{plan['sig_hi']} 个{req_text}，最多重试 {plan['tries']} 批")

    best, best_score = None, None
    mid = (plan["sig_lo"] + plan["sig_hi"]) / 2.0
    for attempt in range(1, plan["tries"] + 1):
        batch = _synth_generate(n_pool, plan, rng)
        st = _synth_measure(batch[:n], plan)
        score = abs(st["sig_count"] - mid)
        if best is None or score < best_score:
            best, best_score = (batch, st), score
        # 总量表 α 偶尔比最低维度还低，所以两个都要卡（nan 表示没有量表题，跳过）
        total_ok = (st["total_alpha"] != st["total_alpha"]) or st["total_alpha"] >= plan["alpha_min"]
        # 指定的题必须全部显著
        required_ok = all(q in st["sig"] for q in plan["sig_items"])
        if (st["min_alpha"] >= plan["alpha_min"]
                and total_ok
                and required_ok
                and st["kmo"] >= plan["kmo_min"]
                and plan["sig_lo"] <= st["sig_count"] <= plan["sig_hi"]):
            print(f"合成答卷：第 {attempt} 批命中 —— "
                  f"α最低 {st['min_alpha']:.3f}（总量表 {st['total_alpha']:.3f}），KMO {st['kmo']:.3f}，"
                  f"显著 {st['sig_count']} 个 {['Q%d' % q for q in st['sig']]}，"
                  f"事件率 {st['event_rate']:.2f}")
            best = (batch, st)
            break
    else:
        # 没命中时不能只丢一句「用最接近的」—— 用户会误以为达标了。
        # 直接生成一段可复制的说明：目标、实测、差距、改哪个字段、当前值，粘给 AI 就能改。
        print(f"⚠️ 合成答卷：{plan['tries']} 批都没完全命中目标，改用最接近的一批继续刷")
        for line in _synth_diagnose(plan, best[1], config.get("url", "")):
            print(line)

    batch, st = best
    _synth_spec = plan
    _synth_stats = st
    print(f"合成答卷：已备好 {len(batch)} 份（目标 {n} 份，其余 {len(batch) - n} 份为提交失败重试预留）")
    with _synth_lock:
        _synth_queue = list(batch)
    return True


def _synth_take():
    """线程取一份预生成的答卷；队列空了返回 None（调用方回退到普通概率模式）。"""
    with _synth_lock:
        if _synth_queue:
            return _synth_queue.pop()
    return None


def _synth_answer(current):
    """取当前这份答卷对某题的预设答案（原样返回：序号 / 列表 / 文本）。"""
    answers = getattr(_validity_ctx, "answers", None)
    if not answers:
        return None
    return answers.get(int(current))


def _synth_override(current, default, limit=None):
    """单选题 / 量表题 / 下拉框：命中预设就用它覆盖，否则保留原逻辑算出的值。

    limit 是本题实际选项数：预设值越界时宁可回退到默认值，也不能点空指针。
    """
    v = _synth_answer(current)
    if isinstance(v, int) and v > 0 and (limit is None or v <= limit):
        return v
    return default


def _start_respondent(driver):
    """每份问卷开始填写前调用：准备这一份的作答依据。

    合成模式（synth）下，整份答卷的答案已在 _synth_prepare() 里算好并校验过，
    这里只是从队列取一份；信效度模式下才现抽潜在因子。
    """
    # 无论哪种模式都先清空上一份的残留（threading.local 会被同线程复用）
    _validity_ctx.answers = None

    if _synth_spec is not None:
        answers = _synth_take()
        _validity_ctx.answers = answers
        _validity_ctx.factors = {}
        if answers is None:
            print("⚠️ 合成答卷队列已空（失败重试消耗超出预生成数量），本份回退到普通概率填写")
        return

    if not config.get("validity_optimize"):
        return
    _build_dim_plan(driver)
    dims = sorted(_dim_lambda)
    noise = [random.gauss(0.0, 1.0) for _ in dims]
    if _dim_corr_chol and len(_dim_order) == len(dims):
        factors = [
            sum(_dim_corr_chol[i][j] * noise[j] for j in range(i + 1))
            for i in range(len(dims))
        ]
    else:
        factors = noise
    _validity_ctx.factors = dict(zip(dims, factors))


# ========== 工具函数 ==========
# 网络层错误（代理不通 / 隧道建不起来）：这类失败换条 IP 就能解决，不该计入问卷失败次数
NET_FAIL_LIMIT = 20
_NET_ERROR_MARKERS = (
    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED", "ERR_TIMED_OUT",
    "ERR_CONNECTION_FAILED", "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_REFUSED", "ERR_NAME_NOT_RESOLVED", "ERR_EMPTY_RESPONSE",
    "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_ADDRESS_UNREACHABLE",
    "net::ERR_", "connection refused", "max retries exceeded", "proxy error",
)


def _is_network_error(message):
    """判断异常是否属于「这条代理不可用」，而不是问卷本身的问题。"""
    if not message:
        return False
    text = message.lower()
    return any(marker.lower() in text for marker in _NET_ERROR_MARKERS)


def _short_error(e):
    """从异常信息里摘出一句短提示，避免日志被整段堆栈淹没。"""
    text = str(e) or ""
    m = re.search(r"net::ERR_[A-Z_]+", text)
    if m:
        return m.group(0)
    first = text.strip().splitlines()[0] if text.strip() else ""
    return (first or e.__class__.__name__)[:60]


def validate(ip):
    pattern = r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?):(\d{1,5})$"
    return bool(re.match(pattern, ip))


# 检测题目是否可见（核心：适配条件逻辑题）
def is_question_visible(driver: WebDriver, question_id: int) -> bool:
    try:
        element = driver.find_element(By.ID, f"div{question_id}")
        return element.is_displayed() and element.size['width'] > 0 and element.size['height'] > 0
    except NoSuchElementException:
        return False


# 概率归一化（避免除以0）
def normalize_prob(prob_list):
    prob_sum = sum(prob_list)
    if prob_sum == 0 or len(prob_list) == 0:
        return [1 / len(prob_list)] * len(prob_list) if len(prob_list) > 0 else [1]
    return [x / prob_sum for x in prob_list]


# ========== 「答案唯一」支持：同一份问卷之间不重复地使用文本答案 ==========
# 前端把开了「答案唯一」的空放在 config["texts_unique"] 里（值为 True），键与
# config["texts"] / config["single_other_texts"] / config["other_texts"] 的键一致：
#   填空题      -> "题号"        （如 "5"）
#   单选其他项  -> "题号_选项序号"（如 "3_1"，0-based）
#   多选其他项  -> 同上
# 实现方式：为每个键建一个「答案池」，每份问卷从池里领一条，提交失败/未提交时归还池中，
# 保证同一个空在不同问卷里不会重复使用同一条答案。
_unique_pools = {}          # key -> 剩余可用答案列表
_unique_lock = threading.Lock()
_unique_initialized = False  # 答案池是否已初始化（多线程下只初始化一次）
_thread_picked = threading.local()   # 本线程当前这份问卷已领取的答案：[(key, text), ...]


def _unique_source(key):
    """按 key 在三个文本配置表里定位答案列表，返回 (答案列表, 是否找到)。"""
    for table_name in ("texts", "single_other_texts", "other_texts"):
        table = config.get(table_name) or {}
        if key in table:
            return table[key], True
    return None, False


def init_unique_pools():
    """按 config["texts_unique"] 初始化答案池。

    会被多线程调用（run() 开头），因此内部只真正执行一次——否则后启动的线程
    会把池重置，导致已经领取的答案被"复活"，出现重复。
    """
    global _unique_initialized
    if _unique_initialized:
        return

    unique_keys = config.get("texts_unique") or {}
    if not isinstance(unique_keys, dict):
        unique_keys = {k: True for k in (unique_keys or [])}

    prepared = {}
    for key in unique_keys:
        if not unique_keys.get(key):
            continue
        source, found = _unique_source(key)
        if found and source:
            prepared[key] = list(source)

    with _unique_lock:
        if _unique_initialized:
            return
        _unique_pools.update(prepared)
        _unique_initialized = True

    if _unique_pools:
        detail = ", ".join(f"{k}={len(v)}" for k, v in _unique_pools.items())
        print(f"答案唯一：已启用 {len(_unique_pools)} 个空的作答不重复（{detail}）")


def pick_answer_text(text_list, prob_list, unique_key):
    """取一条文本答案。

    unique_key 属于「答案唯一」时空从答案池领取（并登记，便于失败时归还），
    否则维持原行为——按概率随机抽取。
    """
    if unique_key in _unique_pools:
        with _unique_lock:
            pool = _unique_pools.get(unique_key)
            if pool:
                text = pool.pop(0)
                picked = getattr(_thread_picked, "items", None)
                if picked is None:
                    picked = []
                    _thread_picked.items = picked
                picked.append((unique_key, text))
                return text
        # 池已空（正常不该发生：条数与目标份数一致）——降级为随机，避免整题空白
        print(f"⚠️ 第{unique_key}题的「答案唯一」答案已用尽，本次降级为随机抽取")

    if prob_list and len(prob_list) == len(text_list):
        norm_prob = normalize_prob(prob_list)
        index = random.choices(range(len(text_list)), weights=norm_prob, k=1)[0]
        return text_list[index]
    return random.choice(text_list)


def reset_picked_answers():
    """开始新一份问卷前清空本线程的领取记录。"""
    _thread_picked.items = []


def release_picked_answers():
    """本份问卷未成功提交时，把已领取的答案归还池中（不浪费任何一条）。"""
    picked = getattr(_thread_picked, "items", None)
    if not picked:
        return
    _thread_picked.items = []
    with _unique_lock:
        for key, text in picked:
            pool = _unique_pools.get(key)
            if pool is not None:
                pool.append(text)


# ========== 题型处理函数（修复版） ==========
# 填空题处理函数
def vacant(driver: WebDriver, current):
    try:
        # 从配置中按实际题号取参数
        current_str = str(current)
        content = config.get("texts", {}).get(current_str, ["默认内容"])
        p = config.get("texts_prob", {}).get(current_str, [1] * len(content))

        # 「答案唯一」时从答案池领取（同一条答案只在一份问卷里出现），否则按概率随机
        selected_text = pick_answer_text(content, p, current_str)

        input_elem = driver.find_element(By.CSS_SELECTOR, f"#q{current}")
        input_elem.clear()  # 清空原有内容
        input_elem.send_keys(selected_text)
    except Exception as e:
        print(f"处理填空题{current}失败: {e}")


# 选项定位辅助：按题目块内的选项容器取选项元素列表（DOM 顺序 = 选项序号）
def find_option_elements(driver: WebDriver, current, option_class: str):
    """定位某题的选项元素列表。

    旧实现用 `//*[@id="divN"]/div[2]/div` 数选项：当题目块内除选项容器外还有
    题干(topichtml)、提示语(qinsert)等前置 div 时，div[2] 会指向它们而不是
    ui-controlgroup，数出 0 个选项导致整题漏选（单选/多选最典型）。

    这里改为直接按类名定位选项容器（单选 .ui-radio / 多选 .ui-checkbox），
    取到的列表即按 DOM 顺序排列，用同一个列表做「计数」和「点击」，
    彻底避免序号错位。
    """
    selectors = [
        f'#div{current} > .ui-controlgroup > .{option_class}',
        f'#div{current} > .ui-controlgroup > div',
        f'#div{current} .ui-controlgroup > .{option_class}',
        f'#div{current} .{option_class}',
    ]
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            elements = []
        if elements:
            return elements
    return []


# 单选题处理函数（新增其他选项文本输入功能）
def _clear_page_mask(driver):
    """隐藏问卷星的整页透明遮罩。

    问卷星在页面加载阶段会铺一层 `#divS`（position:fixed; z-index:30000; 全透明），
    加载完才移除。资源没加载完时它会一直挂着，Selenium 的 click 会被它拦下来
    （element click intercepted），表现为「某题没选上 → 提交被必答校验拦在原地」。
    """
    try:
        driver.execute_script(
            "['divS','divS2','layui-layer-shade1','layui-layer-shade'].forEach(function(id){"
            "  var e = document.getElementById(id);"
            "  if (e) { e.style.display = 'none'; }"
            "});"
        )
    except Exception:
        pass


def _safe_click(driver, element, retry=2):
    """点击元素；被遮罩/加载层拦截时先隐藏遮罩再点，最后退回 JS 点击。"""
    for _ in range(retry):
        try:
            element.click()
            return True
        except ElementClickInterceptedException:
            _clear_page_mask(driver)
            time.sleep(0.3)
        except (ElementNotInteractableException, StaleElementReferenceException):
            time.sleep(0.3)
        except Exception:
            break
    try:
        _clear_page_mask(driver)
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception as e:
        print(f"⚠️ 点击失败（{type(e).__name__}: {e}）")
        return False


def single(driver: WebDriver, current):
    try:
        options = find_option_elements(driver, current, "ui-radio")
        if not options:
            print(f"⚠️ 第{current}题（单选）未定位到选项，跳过以免整题漏选")
            return

        # 从配置中按实际题号取参数
        p = config.get("single_prob", {}).get(str(current), -1)
        # 信效度模式：若该题被手动指定为「单选题形式的量表」，且进入了维度计划，
        # 则与本维度其它题共用潜在因子，保证作答内部一致。
        dim = _dim_map.get(current) if _dim_map else None
        if dim is not None:
            probs = p if isinstance(p, list) else None
            r = _validity_pick(dim, len(options), probs)
        elif p == -1:
            r = random.randint(1, len(options))
        else:
            norm_p = normalize_prob(p)
            if len(norm_p) != len(options):
                raise ValueError(f"第{current}题参数长度({len(p)})与选项数({len(options)})不匹配！")
            r = random.choices(range(1, len(options) + 1), weights=norm_p, k=1)[0]

        # 点击选中选项（直接用定位到的元素，序号与 DOM 顺序一致）
        # 反向计分题在这里翻转：让「高分」仍然代表构念上的高，SPSS 里勾反向题也是同样效果
        r = _maybe_reverse(current, r, len(options))
        # 合成模式：预设值就是最终要点的选项，放在翻转之后覆盖（不再二次翻转）
        r = _synth_override(current, r, len(options))
        option_element = options[r - 1]
        _safe_click(driver, option_element)

        # 新增：检查是否是"其他"选项并处理文本输入
        option_text = option_element.text
        if "其他" in option_text:
            # 查找对应的文本输入框
            try:
                # 问卷星"其他"选项的文本框通常有以下几种选择器
                other_input_selectors = [
                    f"#q{current}_other",  # 常见格式
                    f"#div{current} input[type='text']",  # 兜底
                    f"#div{current} textarea"  # 可能是多行文本
                ]

                other_input = None
                for selector in other_input_selectors:
                    try:
                        other_input = driver.find_element(By.CSS_SELECTOR, selector)
                        break
                    except NoSuchElementException:
                        continue

                if other_input:
                    # 优先取「题号_选项序号」的配置，取不到再回退到只按题号存的旧格式。
                    # r 是 1-based 的第几个选项，前端按 0-based 序号存储，所以减 1。
                    single_other_texts = config.get("single_other_texts", {})
                    single_other_texts_prob = config.get("single_other_texts_prob", {})
                    option_key = f"{current}_{r - 1}"
                    # 定位真正取到答案的那个键，供「答案唯一」按同一个键领取
                    text_key = option_key if option_key in single_other_texts else str(current)
                    other_text_list = single_other_texts.get(text_key, ["其他情况"])
                    other_text_prob = single_other_texts_prob.get(text_key)

                    # 由「答案唯一」答案池领取，或按概率随机选择
                    other_text = pick_answer_text(other_text_list, other_text_prob, text_key)

                    # 输入文本
                    other_input.clear()
                    other_input.send_keys(other_text)
            except Exception as e:
                print(f"  处理第{current}题'其他'选项文本失败: {e}")

    except Exception as e:
        print(f"处理单选题{current}失败: {e}")


# 下拉框处理函数
def droplist(driver: WebDriver, current):
    """下拉框：兼容 select2 自定义控件与原生 select 两种实现，选完必须确认已生效。

    旧实现只认 `#select2-q{current}-container`，碰到原生 select 的问卷整题留空，
    提交时被问卷星前端校验拦住、页面原地不动 → 被误判成功（漏卷的主因之一）。
    """
    prob = config.get("droplist_prob", {}).get(str(current))

    # 1) 展开下拉：挨个尝试可能的结构（select2 容器 / 原生 select / 题目自带控件）
    for sel in (
        f"#select2-q{current}-container",
        f"#div{current} .select2-selection",
        f"#div{current} .select2-container",
        f"#div{current} select",
        f"#q{current}",
    ):
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            _safe_click(driver, el)
            time.sleep(0.35)
            break
        except Exception:
            continue

    # 2) select2 分支：展开后会生成 li 列表，第一项是「请选择」占位
    try:
        options = driver.find_elements(By.XPATH, f"//*[@id='select2-q{current}-results']/li")
    except Exception:
        options = []
    if len(options) > 1:
        p = prob or [1] * (len(options) - 1)
        norm_p = normalize_prob(p[:len(options) - 1])
        r = random.choices(range(1, len(options)), weights=norm_p, k=1)[0]
        r = _synth_override(current, r, len(options) - 1)
        try:
            driver.find_element(By.XPATH, f"//*[@id='select2-q{current}-results']/li[{r + 1}]").click()
            return
        except Exception as e:
            print(f"  第{current}题（下拉框）点击选项失败: {_short_error(e)}")

    # 3) 原生 select 兜底：直接改 selectedIndex 并派发 change（select2 会同步跟随）
    try:
        sel_el = driver.find_element(By.CSS_SELECTOR, f"#div{current} select")
        total = len(sel_el.find_elements(By.TAG_NAME, "option"))
        if total > 1:
            p = prob or [1] * (total - 1)
            norm_p = normalize_prob(p[:total - 1])
            r = random.choices(range(1, total), weights=norm_p, k=1)[0]
            r = _synth_override(current, r, total - 1)
            driver.execute_script(
                "arguments[0].selectedIndex = arguments[1];"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                sel_el, r,
            )
            return
    except Exception as e:
        print(f"  第{current}题（下拉框）原生 select 兜底失败: {_short_error(e)}")

    # 选不上就把展开的下拉收起来，否则遮罩会挡住后面的提交按钮
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass
    # 把题目的真实 DOM 打进日志：下次一眼就能看出问卷星用的是哪种下拉实现
    try:
        dom = driver.find_element(By.CSS_SELECTOR, f"#div{current}").get_attribute("outerHTML") or ""
        print(f"⚠️ 第{current}题（下拉框）未能选中，DOM: {dom[:400]}")
    except Exception:
        print(f"⚠️ 第{current}题（下拉框）未能选中，提交很可能被问卷星拦截")


def _refill_droplists(driver):
    """把所有可见的下拉框重新选一遍。

    问卷星的必答题校验在前端，只要有一个下拉框留在「请选择」，点提交就原地不动、
    不会报错也不会跳转 —— 旧版因此把「没提交」记成成功。提交前先补一遍，成本极低。
    """
    fixed = 0
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, '[id^="div"][type="7"]')
    except Exception:
        return 0
    for block in blocks:
        try:
            qid = (block.get_attribute("id") or "").replace("div", "")
            if not qid.isdigit():
                continue
            if not is_question_visible(driver, int(qid)):
                continue
            # 已有选中值就不动（select2 容器文本会从「请选择」变成具体选项）
            try:
                box = driver.find_element(By.CSS_SELECTOR, f"#select2-q{qid}-container")
                if "请选择" not in (box.text or "") and (box.text or "").strip():
                    continue
            except Exception:
                pass
            droplist(driver, int(qid))
            fixed += 1
        except Exception:
            continue
    if fixed:
        print(f"↩️ 提交前补填了 {fixed} 个下拉框")
    return fixed


# 多选题处理函数
def multiple(driver: WebDriver, current):
    try:
        options = find_option_elements(driver, current, "ui-checkbox")
        if not options:
            print(f"⚠️ 第{current}题（多选）未定位到选项，跳过以免整题漏选")
            return

        # 从配置中按实际题号取参数
        p = config.get("multiple_prob", {}).get(str(current), [50] * len(options))
        # 确保概率长度和选项一致
        p = p[:len(options)] if len(p) > len(options) else p + [50] * (len(options) - len(p))

        mul_list = []
        preset = _synth_answer(current)
        if isinstance(preset, list) and preset:
            # 合成模式：预设答案直接决定勾选哪些选项
            mul_list = [1 if (i + 1) in preset else 0 for i in range(len(options))]
        else:
            max_attempts = 100  # 防止无限循环
            attempts = 0
            while sum(mul_list) <= 0 and attempts < max_attempts:
                # 每个选项按自身概率独立判定是否勾选（等价于原先的 numpy 伯努利抽样）
                mul_list = [1 if random.random() < (item / 100) else 0 for item in p]
                attempts += 1

            # 至少选一个（兜底）
            if sum(mul_list) == 0:
                mul_list[random.randint(0, len(mul_list) - 1)] = 1

        for idx, item in enumerate(mul_list):
            if item == 1:
                option_element = options[idx]
                _safe_click(driver, option_element)

                # 检查是否是"其他"选项并处理文本输入
                option_text = option_element.text
                if "其他" in option_text:
                    # 查找对应的文本输入框
                    try:
                        # 问卷星"其他"选项的文本框通常有以下几种选择器
                        other_input_selectors = [
                            f"#q{current}_other",  # 常见格式
                            f"#div{current} input[type='text']",  # 兜底
                            f"#div{current} textarea"  # 可能是多行文本
                        ]

                        other_input = None
                        for selector in other_input_selectors:
                            try:
                                other_input = driver.find_element(By.CSS_SELECTOR, selector)
                                break
                            except NoSuchElementException:
                                continue

                        if other_input:
                            # 同上：优先按「题号_选项序号」取，取不到回退到纯题号
                            other_texts = config.get("other_texts", {})
                            other_texts_prob = config.get("other_texts_prob", {})
                            option_key = f"{current}_{idx}"
                            # 定位真正取到答案的那个键，供「答案唯一」按同一个键领取
                            text_key = option_key if option_key in other_texts else str(current)
                            other_text_list = other_texts.get(text_key, ["其他情况"])
                            other_text_prob = other_texts_prob.get(text_key)

                            # 由「答案唯一」答案池领取，或按概率随机选择
                            other_text = pick_answer_text(other_text_list, other_text_prob, text_key)

                            # 输入文本
                            other_input.clear()
                            other_input.send_keys(other_text)
                    except Exception as e:
                        print(f"  处理第{current}题'其他'选项文本失败: {e}")
    except Exception as e:
        print(f"处理多选题{current}失败: {e}")


# 矩阵题处理函数
def matrix(driver: WebDriver, current):
    try:
        xpath1 = f'//*[@id="divRefTab{current}"]/tbody/tr'
        rows = driver.find_elements(By.XPATH, xpath1)
        q_num = sum(1 for tr in rows if tr.get_attribute("rowindex") is not None)
        if q_num == 0:
            return

        xpath2 = f'//*[@id="drv{current}_1"]/td'
        cols = driver.find_elements(By.XPATH, xpath2)
        if not cols:
            return

        # 从配置中按实际题号取矩阵题参数
        matrix_sub_probs = config.get("matrix_prob", {}).get(str(current), [-1] * q_num)

        # 信效度模式：矩阵题的每个子行是同一维度下的一个题项，共用本份答卷的潜在因子
        validity_dim = _dim_map.get(current) if _dim_map else None

        for i in range(1, q_num + 1):
            # 取对应子题的参数
            p = matrix_sub_probs[i - 1] if (i - 1 < len(matrix_sub_probs)) else -1

            # 计算实际选项数量（cols[0]通常是标题列，后面是选项列）
            actual_options = len(cols) - 1

            if validity_dim is not None:
                # copula 生成 1..actual_options，再换算成 td 的列序号（选项从第 2 列开始）
                probs = p if isinstance(p, list) else None
                opt = _validity_pick(validity_dim, actual_options, probs) + 1
            elif p == -1:
                # 无配置时随机选择
                opt = random.randint(2, len(cols))
            elif isinstance(p, list):
                # 有配置时使用配置的概率
                if len(p) == actual_options:
                    # 概率长度匹配时正常使用
                    norm_p = normalize_prob(p)
                    opt = random.choices(range(2, len(cols) + 1), weights=norm_p, k=1)[0]
                else:
                    # 概率长度不匹配时，优先使用配置的第一个选项
                    print(f"  概率长度不匹配，使用第一个选项")
                    opt = 2  # 选择第一个选项（对应cols[1]）

            # 反向计分：opt 是 td 列序号（2 起），先换算成 1..options 再翻转
            opt = _maybe_reverse(current, opt - 1, actual_options) + 1
            # 合成模式：预设写成 {子行序号: 选项序号}，换算成 td 列序号（选项从第 2 列起）
            preset = _synth_answer(current)
            if isinstance(preset, dict):
                pv = preset.get(str(i), preset.get(i))
                if isinstance(pv, int) and 1 <= pv <= actual_options:
                    opt = pv + 1
            cell = driver.find_element(By.CSS_SELECTOR, f"#drv{current}_{i} > td:nth-child({opt})")
            _safe_click(driver, cell)
    except Exception as e:
        print(f"处理矩阵题{current}失败: {e}")


# 排序题处理函数
def reorder(driver: WebDriver, current):
    try:
        xpath = f'//*[@id="div{current}"]/ul/li'
        options = driver.find_elements(By.XPATH, xpath)
        for j in range(1, len(options) + 1):
            b = random.randint(j, len(options))
            _safe_click(driver, driver.find_element(By.CSS_SELECTOR, f"#div{current} > ul > li:nth-child({b})"))
            time.sleep(0.4)
    except Exception as e:
        print(f"处理排序题{current}失败: {e}")


# 量表题处理函数
def scale(driver: WebDriver, current):
    try:
        xpath = f'//*[@id="div{current}"]/div[2]/div/ul/li'
        options = driver.find_elements(By.XPATH, xpath)
        if not options:
            return

        # 从配置中按实际题号取参数
        p = config.get("scale_prob", {}).get(str(current), -1)
        dim = _dim_map.get(current) if _dim_map else None
        if dim is not None:
            # 信效度模式：同维度的题共用本份答卷的潜在因子，作答自然内部一致
            probs = p if isinstance(p, list) else None
            b = _validity_pick(dim, len(options), probs)
        elif p == -1:
            b = random.randint(1, len(options))
        else:
            norm_p = normalize_prob(p)
            if len(norm_p) != len(options):
                raise ValueError(f"第{current}题参数长度({len(p)})与选项数({len(options)})不匹配！")
            b = random.choices(range(1, len(options) + 1), weights=norm_p, k=1)[0]

        b = _maybe_reverse(current, b, len(options))
        # 合成模式：预设值即最终要点的选项
        b = _synth_override(current, b, len(options))
        _safe_click(driver, driver.find_element(
            By.CSS_SELECTOR, f"#div{current} > div.scale-div > div > ul > li:nth-child({b})"))
    except Exception as e:
        print(f"处理量表题{current}失败: {e}")


# 提交结果判定
# 依据问卷星提交后的页面文案 / URL 判断这一次到底有没有被收下：
#   ok       —— 出现「提交成功」等文案，或跳到了完成页
#   rejected —— 出现「网络连接异常」「请完成验证」等明确失败文案
#   unknown  —— 两者都不是（页面结构变化等），由调用方按旧行为处理并打印原文
_SUCCESS_KEYWORDS = ("提交成功", "答卷已提交", "感谢您的参与", "感谢您的填写", "已经提交")
_FAILURE_KEYWORDS = (
    "网络连接异常", "请完成所有必答题", "请完成验证", "验证失败", "请勿重复提交",
    "提交失败", "系统繁忙", "请不要重复提交", "提交太快",
)


def _detect_submit_result(driver):
    """读取当前页面，返回 (状态, 依据)。"""
    try:
        url = driver.current_url or ""
    except Exception:
        url = ""
    try:
        text = (driver.find_element(By.TAG_NAME, "body").text or "").replace("\n", " ")
    except Exception:
        text = ""

    for keyword in _SUCCESS_KEYWORDS:
        if keyword in text:
            return "ok", keyword
    lower_url = url.lower()
    if "complete" in lower_url or "joinsuccess" in lower_url:
        return "ok", url[:80]

    for keyword in _FAILURE_KEYWORDS:
        if keyword in text:
            return "rejected", keyword

    # 页面仍停在问卷填写页 —— 提交压根没生效。
    # 这是「前端显示 13 份、问卷星后台只有 7 份」的主因：旧逻辑把「点过提交」当成功，
    # 而必答题没答完时问卷星用前端校验拦住，页面原地不动，于是静默漏卷。
    try:
        if driver.find_elements(By.XPATH, '//*[@id="ctlNext"]'):
            return "rejected", "页面仍停留在问卷页（提交未生效）"
    except Exception:
        pass
    # 提交按钮都找不到时，退一步看题目容器是否还在（题目 DOM 由主 JS 生成）
    try:
        if driver.find_elements(By.CSS_SELECTOR, '[id^="div"][type]'):
            return "rejected", "页面仍是问卷表单（提交未生效）"
    except Exception:
        pass

    return "unknown", text[:80]


# ========== 刷题逻辑（修复版） ==========
def brush(driver: WebDriver):
    """填写整份问卷（自动逐页翻页），返回 True=已提交，False=未提交"""
    max_question_check = config.get("max_question_check", 200)  # 从配置读取最大检测题号

    # 信效度优化：先为本份答卷生成本份的潜在因子（同维度题目才会具有内部一致性）
    _start_respondent(driver)

    # 循环处理每一页，直到找不到"下一页"按钮为止
    while True:
        current = 0
        while current < max_question_check:
            current += 1

            # 核心：只处理可见的题目（适配条件逻辑）
            if not is_question_visible(driver, current):
                continue

            try:
                q_type = driver.find_element(By.CSS_SELECTOR, f"#div{current}").get_attribute("type")

                if q_type == "1" or q_type == "2":  # 填空题
                    vacant(driver, current)
                elif q_type == "3":  # 单选
                    single(driver, current)
                elif q_type == "4":  # 多选
                    multiple(driver, current)
                elif q_type == "5":  # 量表题
                    scale(driver, current)
                elif q_type == "6":  # 矩阵题
                    matrix(driver, current)
                elif q_type == "7":  # 下拉框
                    droplist(driver, current)
                elif q_type == "8":  # 滑块题
                    score = random.randint(1, 100)
                    driver.find_element(By.CSS_SELECTOR, f"#q{current}").send_keys(score)
                elif q_type == "11":  # 排序题
                    reorder(driver, current)
                else:
                    print(f"第{current}题为不支持题型（类型码：{q_type}），跳过")

            except Exception as e:
                print(f"处理第{current}题时出错: {e}")
                continue

        # 尝试翻到下一页；找不到"下一页"说明已到最后一页
        time.sleep(0.5)
        try:
            driver.find_element(By.CSS_SELECTOR, "#divNext").click()
            time.sleep(0.5)
        except Exception:
            break

    # 已到最后一页：时间控制模式下，先等待目标时长再提交（模拟真人填写耗时）
    min_fill = config.get("min_fill_time")
    max_fill = config.get("max_fill_time")
    fill_start = getattr(thread_local_data, 'fill_start_time', None)
    if min_fill and max_fill and fill_start:
        target_duration = random.uniform(float(min_fill), float(max_fill))
        actual_duration = time.time() - fill_start
        # 分段等待（每5秒检查一次停止信号）
        while actual_duration < target_duration:
            if check_stop_flag():
                print("等待期间收到停止信号，放弃提交")
                return False
            chunk = min(5.0, target_duration - actual_duration)
            time.sleep(chunk)
            actual_duration = time.time() - fill_start
        print(f"填写时长{target_duration:.0f}秒，开始提交")

    # 提交前先把漏选的下拉框补上：只要有一题留在「请选择」，问卷星就会原地拦下，
    # 既不报错也不跳转，直接造成「前端算成功、后台没记录」。
    _refill_droplists(driver)

    try:
        _safe_click(driver, driver.find_element(By.XPATH, '//*[@id="ctlNext"]'))
        submit(driver)
    except Exception:
        print("未找到提交按钮，尝试直接提交")
        submit(driver)

    # ========== 提交结果校验 ==========
    # 以前这里无条件 return True，导致「点过提交」就算成功：被验证拦住、被问卷星拒收
    # （网络连接异常）时前端照样 +1，后台却没有答卷。现在按页面实际文案判定。
    time.sleep(4)
    status, detail = _detect_submit_result(driver)

    # 明确判定为失败时，补填必答项 + 原地点一次提交 + 补过验证（滑块/智能验证），给一次补救机会
    if status == "rejected":
        print(f"⚠️ 第{getattr(thread_local_data, 'local_ip', '-')} 提交未通过（{detail}），补填后重试一次")
        _refill_droplists(driver)
        try:
            driver.find_element(By.XPATH, '//*[@id="ctlNext"]').click()
        except Exception:
            pass
        submit(driver)
        time.sleep(5)
        status, detail = _detect_submit_result(driver)

    name = threading.current_thread().name
    local_ip = getattr(thread_local_data, 'local_ip', '-')
    kind_label = _UA_KIND_LABEL.get(getattr(thread_local_data, 'ua_kind', 'desktop'), '-')
    # 单份耗时（从打开页面到提交判定结束）：对比二倍速/六倍速时直接看这个数，
    # 比后台「所用时间」列更准（后台那列不含验证/提交等待）。
    _start = getattr(thread_local_data, 'fill_start_time', None)
    cost = f"{(time.time() - _start):.1f}s" if _start else "-"
    if status == "ok":
        print(f"✅ {name} 提交成功（{detail}） 来源={kind_label} 耗时={cost} IP: {local_ip}")
        return True
    if status == "rejected":
        print(f"❌ {name} 提交被拒（{detail}） 来源={kind_label} 耗时={cost} IP: {local_ip} —— 本份不计入成功")
        return False
    # 无法判定（页面既没有成功文案也没有失败文案）：沿用旧行为按成功计，但打印原文便于排查
    print(f"⚠️ {name} 提交结果无法确认，来源={kind_label} 耗时={cost}，按成功计。页面原文：{detail}")
    return True


# 提交函数
def submit(driver: WebDriver):
    time.sleep(1)
    # 点击确认弹窗
    try:
        driver.find_element(By.XPATH, '//*[@id="layui-layer1"]/div[3]/a').click()
        time.sleep(1)
    except:
        pass
    # 智能检测
    try:
        driver.find_element(By.XPATH, '//*[@id="SM_BTN_1"]').click()
        time.sleep(3)
    except:
        pass
    # 滑块验证
    try:
        slider = driver.find_element(By.XPATH, '//*[@id="nc_1__scale_text"]/span')
        sliderButton = driver.find_element(By.XPATH, '//*[@id="nc_1_n1z"]')
        if str(slider.text).startswith("请按住滑块"):
            width = slider.size.get("width")
            ActionChains(driver).drag_and_drop_by_offset(sliderButton, width, 0).perform()
    except:
        pass


# 运行函数
def run():
    global config, current_ip, ip_use_count, ip_max_use
    target_num = config.get("target_num", 3)
    fail_threshold = config.get("fail_threshold", target_num / 4 + 1)
    # 网络层失败（代理不通）单独计数：只用于防止「代理全废还在死循环」，不影响 fail_threshold
    net_fail_count = 0
    use_ip = config.get("use_ip", False)
    # 单个IP最大使用次数（时间控制模式下为1，即一个IP只填一份）
    ip_max_use = int(config.get("ip_max_use", 10))
    # 注：填写时长区间在 brush() 提交前直接从 config 读取

    # 「答案唯一」：初始化答案池（每个开启该模式的空一份问卷领一条，互不重复）
    init_unique_pools()

    # 合成答卷：在起浏览器之前就把整批答案算好并校验。
    # 必须放在多线程启动前 —— 拒绝采样要以「整批 200 份」为单位重来，
    # 边刷边生成的话，先提交的那几份收不回来，统计指标就没法收敛。
    _synth_prepare()

    # 移除全局变量，使用线程安全的计数器对象
    while True:
        # 检查是否收到停止信号
        if check_stop_flag():
            print("收到停止信号，正在优雅退出...")
            break

        # 原子预定一个名额：若已达目标份数则退出
        # （关键修复：在开始刷之前占用配额，避免双线程各刷一份导致超出目标）
        if not counter_completed.try_increment(target_num):
            break

        # ========== IP复用逻辑（核心修复：IP副本+原子操作） ==========
        local_ip = None
        with ip_lock:
            # 首次/IP已用完/无IP时，获取新IP
            if not use_ip or current_ip is None or ip_use_count >= ip_max_use:
                if use_ip:
                    new_ip = zanip()
                    if new_ip:
                        current_ip = new_ip
                        ip_use_count = 0  # 重置使用次数
                    else:
                        current_ip = None
                else:
                    current_ip = None

            # 给当前线程分配「IP副本」（关键！避免后续被其他线程篡改）
            local_ip = current_ip

            # 只有拿到有效IP时，才累加全局使用次数
            if local_ip:
                ip_use_count += 1

        # ========== Edge驱动配置（补全无头+超时+反检测） ==========
        temp_option = Options()

        # 1. 无头模式（从配置读取，默认开启——不弹出浏览器窗口）
        headless = config.get("headless", True)
        if headless:
            temp_option.add_argument("--headless=new")
            temp_option.add_argument("--window-size=1920,1080")  # 无头模式必须指定窗口大小

        # 2. 基础反检测（隐藏webdriver特征）
        temp_option.add_experimental_option("excludeSwitches", ["enable-automation"])
        temp_option.add_experimental_option("useAutomationExtension", False)

        # 3. 指纹伪装：同一IP期间，UA固定；换IP时随机（更真实）
        #    开启「提交来源比例」后，这里会按比例伪装成手机/链接/微信来源。
        #    注意：只设 UA 字符串还不够，Client Hints 必须在驱动起来后用 CDP 一起覆盖，
        #    否则 UA 说 iPhone、Sec-CH-UA 说 Edge，问卷星风控会静默丢弃答卷。
        ua, ua_meta, ua_kind = pick_ua_profile(local_ip)
        temp_option.add_argument(f"user-agent={ua}")
        print(f"🧭 线程 {threading.current_thread().name} 来源={_UA_KIND_LABEL.get(ua_kind, ua_kind)}"
              f" 伪装设备={_ua_device_label(ua)}")

        # 4. 禁用WebRTC/扩展/GPU（减少特征，防止真实IP泄露）
        temp_option.add_argument("--disable-webrtc")
        temp_option.add_argument("--disable-extensions")
        temp_option.add_argument("--disable-gpu")

        # 5. 开启无痕模式（每次运行清空缓存/Cookie）
        temp_option.add_argument("--incognito")

        # 6. 禁用图片加载（加快页面加载，减少资源消耗）
        temp_option.add_argument("--blink-settings=imagesEnabled=false")

        # 绑定代理IP（用local_ip副本，不再依赖全局变量）
        if use_ip and local_ip:
            temp_option.add_argument(f"--proxy-server={local_ip}")
        else:
            print(f"⚠️ 线程 {threading.current_thread().name} 未使用代理IP，使用本机IP")

        # 驱动初始化必须放进 try：初始化失败（驱动版本不匹配、浏览器缺失）若抛到外面，
        # 整个线程会静默退出，脚本最终仍以退出码 0 结束，Java 端就会把「一份都没成功」
        # 误判成任务正常完成。放进 try 后走统一的失败分支，至少能被正确计数。
        driver = None

        # ========== 刷题逻辑（补全超时+失败处理） ==========
        try:
            # 初始化Edge驱动 + 超时配置（防止卡死）
            # 优先使用软件目录内匹配版本的驱动；不存在时回退到 Selenium Manager
            if EDGE_DRIVER_PATH and os.path.exists(EDGE_DRIVER_PATH):
                service = EdgeService(executable_path=EDGE_DRIVER_PATH)
                driver = webdriver.Edge(service=service, options=temp_option)
            else:
                driver = webdriver.Edge(options=temp_option)
            driver.set_window_size(550, 650)
            driver.set_page_load_timeout(15)  # 页面加载超时15秒
            driver.set_script_timeout(10)     # 脚本执行超时10秒

            # ===== 来源伪装：把 UA 与 Client Hints 对齐 =====
            # --user-agent 只改了 UA 字符串，Sec-CH-UA / platform / mobile 仍是真实浏览器
            # （Edge + Windows）。不覆盖的话，UA 说「安卓微信」而 CH 说「Microsoft Edge」，
            # 问卷星风控会判指纹矛盾并静默丢弃答卷 —— 这正是「前端说成功、后台没记录」的原因。
            if ua_meta:
                try:
                    driver.execute_cdp_cmd("Network.enable", {})
                    driver.execute_cdp_cmd("Network.setUserAgentOverride", {
                        "userAgent": ua,
                        "acceptLanguage": "zh-CN,zh;q=0.9",
                        "platform": ua_meta.get("platform", "Android"),
                        "userAgentMetadata": ua_meta,
                    })
                    # 移动端必须开触摸模拟，否则移动版页面的控件点不动
                    driver.execute_cdp_cmd(
                        "Emulation.setTouchEmulationEnabled",
                        {"enabled": True, "maxTouchPoints": 5},
                    )
                    # 屏幕尺寸也要像手机：问卷星的来源判定除了 UA，还会看窗口/屏幕尺寸，
                    # 一边说自己是安卓一边报 1920×1080 桌面屏，同样会被当成「链接」。
                    if ua_meta.get("mobile"):
                        driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                            "width": 412,
                            "height": 915,
                            "deviceScaleFactor": 2.625,
                            "mobile": True,
                        })
                except Exception as e:
                    print(f"⚠️ 覆盖 Client Hints 失败（{e}），本份指纹可能与 UA 不一致")

            # 关键：隐藏navigator.webdriver（核心反检测）
            # navigator.platform 必须跟随伪装身份，否则桌面 Win32 与移动 UA 又对不上
            nav_platform = "Linux armv8l" if (ua_meta and ua_meta.get("mobile")) else "Win32"
            stealth_js = """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                Object.defineProperty(navigator, 'platform', {get: () => '__NAV_PLATFORM__'});
                Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
            """.replace("__NAV_PLATFORM__", nav_platform)
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": stealth_js}
            )

            # 记录本份问卷开始填写时间（线程独立，时间控制模式在提交前按此补足时长）
            thread_local_data.fill_start_time = time.time()
            # 记录本份使用的 IP，供提交结果日志定位是哪条代理出的问题
            thread_local_data.local_ip = local_ip
            # 记录本份的来源伪装类型，提交被拒时用于熔断
            thread_local_data.ua_kind = ua_kind

            # 显式等待页面加载完成（最多15秒）
            driver.get(config["url"])
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # 页面加载后随机延迟（模拟真人等待）
            time.sleep(random.uniform(1.0, 2.5))

            # 指纹自检：确认 CDP 覆盖真的生效。
            # 只要这里打印的 UA 与伪装不一致，问卷星就会按真实浏览器（Edge/Windows）判定来源，
            # 后台自然看不到「手机」这一档 —— 日志里一眼可查，不用再靠猜。
            if ua_kind != "desktop":
                try:
                    actual_ua = driver.execute_script("return navigator.userAgent;")
                    mobile_flag = driver.execute_script(
                        "return navigator.userAgentData ? navigator.userAgentData.mobile : null;")
                    same = (actual_ua or "").strip() == ua.strip()
                    print(f"🔎 指纹自检（{_UA_KIND_LABEL.get(ua_kind, ua_kind)}）："
                          f"{'UA 一致' if same else 'UA 不一致！实际=' + str(actual_ua)[:110]}"
                          f"，userAgentData.mobile={mobile_flag}")
                except Exception as e:
                    print(f"🔎 指纹自检失败：{e}")

            # 新的一份问卷开始：清空本线程上一次的领取记录
            reset_picked_answers()
            submitted = brush(driver)  # 内部在最终提交前会按时间控制配置等待，返回是否已提交
            time.sleep(random.uniform(4.0, 5.0))

            if submitted:
                # 名额已在循环开头预定，这里只打印和更新进度
                print(f"已填写{counter_completed.get()}份 - 失败{counter_failed.get()}次  -执行时间: {time.strftime('%H:%M:%S', time.localtime())}")

                # 已提交：本份领取的答案视为已消耗，清掉记录即可
                reset_picked_answers()

                # 更新进度文件
                update_progress()
            else:
                # 未提交：等待期间收到停止信号，或提交被问卷星拒收（见 brush 里的判定）
                counter_completed.decrement()
                # 未提交 = 这份答案没用上，全部归还池中
                release_picked_answers()

                if not check_stop_flag():
                    # 不是停止信号，那就是提交被拒：计入失败，并弃用这条 IP（下次换新 IP）。
                    # 注意：不再因为「用了伪装 UA 被拒」就放弃来源比例——实测那些失败几乎
                    # 都是代理隧道失效（同时刻同 IP 多线程一起被拒），与伪装无关。
                    counter_failed.increment()
                    print(f"❌ 线程 {threading.current_thread().name} 提交未成功，已弃用 IP {local_ip}")
                    with ip_lock:
                        current_ip = None
                        ip_use_count = 0
                update_progress()
        except TimeoutException:
            # 页面加载超时 = 这条代理太慢或不通，属于网络层问题，**不计入问卷失败次数**。
            # 否则 4 次超时就会触发全局停止（fail_threshold = 目标份数/4+1），
            # 把还剩一半的刷题任务直接杀掉 —— 这是六倍速只刷出 2~4 份的主因。
            counter_completed.decrement()
            release_picked_answers()
            net_fail_count += 1
            print(f"⏱️ 线程 {threading.current_thread().name} 页面加载超时（IP: {local_ip}），换 IP 重试（不计失败）")

            # 更新进度文件
            update_progress()

            # 超时直接强制换IP
            with ip_lock:
                current_ip = None
                ip_use_count = 0
        except Exception as e:
            # 原有异常处理逻辑保留：回退预定名额
            counter_completed.decrement()
            release_picked_answers()

            if _is_network_error(str(e)):
                # 代理隧道建不起来（ERR_TUNNEL_CONNECTION_FAILED 等）：这条 IP 是废的，
                # 换一条继续，不算问卷失败 —— 绝不能因为代理商给坏 IP 就把任务停掉。
                net_fail_count += 1
                print(f"🌐 线程 {threading.current_thread().name} 代理不可用"
                      f"（{_short_error(e)}，IP: {local_ip}），换 IP 重试（不计失败）")
                update_progress()
                with ip_lock:
                    current_ip = None
                    ip_use_count = 0
                if net_fail_count >= NET_FAIL_LIMIT and not force_stop.is_set():
                    logging.critical(f"网络层连续失败 {net_fail_count} 次，代理基本不可用，停止任务")
                    force_stop.set()
                continue

            traceback.print_exc()
            counter_failed.increment()
            print("\033[42m", f"❌ 线程 {threading.current_thread().name} 已失败{counter_failed.get()}次,失败超过{int(fail_threshold)}次将强制停止", "\033[0m")

            # 更新进度文件
            update_progress()

            # 失败时，当前IP使用次数不累计（避免浪费）
            with ip_lock:
                if current_ip and ip_use_count > 0:
                    ip_use_count -= 1

            if counter_failed.get() >= fail_threshold and not force_stop.is_set():
                logging.critical("失败次数过多，触发全局停止，等待其他线程优雅退出")
                # 不再使用 quit() 强杀进程：置位全局事件，各线程在循环开头检查后自行退出，
                # 保证浏览器驱动能被正常释放
                force_stop.set()
        finally:
            # 无论成功/失败，都确保关闭浏览器（初始化失败时 driver 还是 None）
            if driver is not None:
                try:
                    driver.quit()
                except:
                    pass
        continue


# ========== 主函数（补全线程启动延迟） ==========
if __name__ == "__main__":
    # 从命令行参数读取配置
    if len(sys.argv) < 2:
        print("请传入配置参数")
        sys.exit(1)

    try:
        config_json = None

        # 尝试判断第一个参数是文件路径还是直接的JSON字符串
        first_arg = sys.argv[1]

        # 检查是否是文件路径（如果文件存在）
        if os.path.exists(first_arg):
            # 读取临时文件中的JSON配置
            config_file_path = first_arg
            with open(config_file_path, 'r', encoding='utf-8') as f:
                config_json = f.read()
        else:
            # 直接使用命令行参数作为JSON字符串
            config_json = first_arg

        # 解析JSON配置
        config = json.loads(config_json)

        # 初始化全局变量
        target_num = config.get("target_num", 3)
        fail_threshold = config.get("fail_threshold", target_num / 4 + 1)
        # 注意：我们不再使用cur_num和cur_fail全局变量，而是使用线程安全的计数器
        # counter_completed和counter_failed已经在文件顶部初始化

        # 获取线程数配置
        num_threads = config.get("num_threads", 2)  # 默认双线程

        # 本次生效的来源比例只在任务开头打印一次（run() 是每个线程的入口，写在那里会重复 N 遍）
        _ratio = config.get("source_ratio")
        if _ratio:
            print("来源伪装：手机 {}% / 链接 {}% / 微信 {}%（按每份独立抽签）".format(
                _ratio.get("mobile", 0), _ratio.get("link", 0), _ratio.get("wechat", 0)))
        else:
            print("来源伪装：未开启（全部使用桌面 Edge UA，问卷星记为「链接」）")

        # 启动多线程（加延迟，避免同时竞争）
        threads: list[Thread] = []
        for _ in range(num_threads):
            thread = Thread(target=run)
            threads.append(thread)
            time.sleep(0.5)  # 线程启动延迟0.5秒，减少资源竞争
            thread.start()

        for thread in threads:
            thread.join()

        # 所有线程已结束，强制写一次最终进度，确保 Java 端读到完整结果
        update_progress(force=True)

        # 一份都没成功、又不是用户主动停止时，以非 0 退出码结束，
        # 让 Java 端把任务标记为 FAILED，而不是给用户一个空的「运行结束」页面。
        stop_file = config.get("stop_file", "stop.flag")
        stopped_by_user = bool(stop_file) and os.path.exists(stop_file)
        if counter_completed.get() == 0 and not stopped_by_user:
            print("本次任务没有成功提交任何一份问卷（退出码 2）", file=sys.stderr)
            sys.exit(2)
            
    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"程序执行出错: {e}")
        traceback.print_exc()
        sys.exit(1)