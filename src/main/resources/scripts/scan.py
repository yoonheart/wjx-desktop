import json
import time
import re
import sys
import os
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from bs4 import BeautifulSoup
import io

# 设置标准输出为UTF-8编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def log(message):
    """诊断信息统一写到 stderr。

    stdout 只允许出现最终那一段 JSON —— Java 端会原样把它当作接口返回值交给前端，
    一旦混进任何一行日志，前端的 JSON.parse 就会失败。
    """
    print(message, file=sys.stderr, flush=True)


# ---------------------- 配置 ----------------------
# 默认问卷星网址（当没有传入参数时使用）。
# 不再内置作者本人的问卷链接：留空表示必须由调用方传入，或经环境变量 WJX_DEFAULT_URL 指定。
DEFAULT_WJX_URL = os.environ.get("WJX_DEFAULT_URL", "")
# Edge 驱动路径（可选）。可通过环境变量 WJX_EDGE_DRIVER 覆盖；
# 未配置时留空，直接交给 Selenium Manager 自动解析 —— 不把本机绝对路径写进源码。
EDGE_DRIVER_PATH = os.environ.get("WJX_EDGE_DRIVER", "")


# ----------------------------------------------------------

def init_browser():
    """初始化浏览器（无头模式+防反爬）"""
    options = Options()
    # 启用无头模式（后台运行，不显示浏览器窗口）
    options.add_argument("--headless=new")
    # 模拟真实浏览器，关闭自动化提示
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # 禁用图片/视频加载，加快爬取速度
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.video": 2
    }
    options.add_experimental_option("prefs", prefs)

    try:
        # 优先使用显式配置的驱动路径；文件不存在时交给 Selenium Manager 自动解析
        if EDGE_DRIVER_PATH and os.path.exists(EDGE_DRIVER_PATH):
            service = Service(executable_path=EDGE_DRIVER_PATH)
            driver = webdriver.Edge(service=service, options=options)
        else:
            if EDGE_DRIVER_PATH:
                log(f"⚠️ 未找到Edge驱动 {EDGE_DRIVER_PATH}，改用 Selenium Manager 自动解析")
            driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(20)  # 延长页面加载超时时间
        return driver
    except WebDriverException as e:
        log(f"❌ 浏览器初始化失败：{str(e)}")
        log("请检查：1. Edge驱动版本是否匹配 2. 驱动路径是否正确")
        return None


def get_wjx_page_source(driver, url):
    """获取问卷星完整页面源码（自动翻页）"""
    try:
        driver.get(url)

        # 核心：自动翻页（遍历所有问卷页面）
        page_num = 1
        while True:
            try:
                # 定位“下一页”按钮（兼容问卷星不同样式）
                next_btn = driver.find_element(By.XPATH, '//a[text()="下一页"]')
                # 点击下一页并等待加载
                next_btn.click()
                page_num += 1
                log(f"📄 正在加载第 {page_num} 页...")
            except NoSuchElementException:
                break

        # 等待核心题目容器加载完成
        # 注意：不能写成 find_element(divQuestion) or find_element(fieldset) ——
        # WebDriverWait 默认会吞掉 NoSuchElementException 并重试，or 的右侧永远不会执行。
        # 用 CSS 选择器分组才能让「两种结构任一出现」真正生效。
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#divQuestion, fieldset"))
        )
        # 返回完整渲染后的页面源码
        return driver.page_source
    except TimeoutException:
        log("❌ 页面加载超时，可能是网址无效或需要登录")
        return ""
    except Exception as e:
        log(f"❌ 获取页面源码失败：{str(e)}")
        return ""


def extract_question_type_with_codes(page_source):
    """提取每道题的详细信息并返回，包括题型、题目内容和选项"""
    if not page_source:
        return []

    soup = BeautifulSoup(page_source, "lxml")
    question_index = 1  # 题号（从1开始）
    type_info_list = []  # 存储题型编码+中文的列表

    # 题型编码-中文对照表（问卷星标准）
    code_explain = {
        "1": "填空题", "2": "填空题", "3": "单选题",
        "4": "多选题", "5": "量表题", "6": "矩阵题",
        "7": "下拉框", "8": "滑块题", "11": "排序题", "0": "未知题型"
    }

    # 匹配所有可能的题目容器（覆盖所有版本）
    question_divs = []
    # 容器1：新版 - divQuestion下的field
    if soup.find("div", id="divQuestion"):
        question_divs = soup.find("div", id="divQuestion").find_all("div", class_="field")
    # 容器2：旧版 - div_question
    if not question_divs:
        question_divs = soup.find_all("div", class_="div_question")
    # 容器3：兜底 - 带topic属性的div
    if not question_divs:
        question_divs = soup.find_all("div", attrs={"topic": True})

    if not question_divs:
        log("❌ 未找到题目容器，可能是页面结构更新")
        return []

    for q_div in question_divs:
        # 单题解析失败不能拖垮整份问卷：任何异常都降级成「未知题型」，
        # 保证其余题目照常返回，前端会据此给出明确的题型提示。
        try:
            _parse_one_question(q_div, question_index, code_explain, type_info_list)
        except Exception as e:
            log(f"⚠️ 第 {question_index} 题解析失败，已标记为未知题型：{e}")
            type_info_list.append({
                "题号": question_index,
                "编码": "0",
                "题型": "未知题型",
                "题目内容": q_div.get_text(strip=True)[:100],
                "选项": []
            })

        question_index += 1

    return type_info_list


def _parse_one_question(q_div, question_index, code_explain, type_info_list):
    """解析单道题并把结果追加到 type_info_list（异常由调用方兜底）"""
    # 1. 检查是否是"其他"选项的单独div，如果是则跳过
    div_id = q_div.get("id", "")
        
    # 获取题目内容的预览，用于检查是否是"其他"选项
    preview_text = q_div.get_text(strip=True)[:20].lower()
        
    # 排除条件：
    # 1. 题目内容包含"其他"且带有"请注明"等关键字
    # 2. 没有实际的题目编号（通常主题目会有编号）
    # 3. 是填空题但内容是"其他"选项
    if ("其他" in preview_text and ("请注明" in q_div.text or "other" in preview_text)):
        # 检查是否是主题目下的"其他"选项（如果是主题目则保留）
        has_topic_number = q_div.find("div", class_="topicnumber")
        if not has_topic_number:
            log(f"⏭️  跳过'其他'选项div: {div_id}")
            return
        
    # 2. 获取原始题型编码（优先从div的type属性获取）
    raw_type_code = q_div.get("type", "")

    # 3. 兜底识别（如果type属性为空，通过元素特征推断）
    if not raw_type_code:
        if "checkbox" in str(q_div) or "ui-checkbox" in str(q_div):
            raw_type_code = "4"  # 多选题
        elif "radio" in str(q_div) or "ui-radio" in str(q_div):
            raw_type_code = "3"  # 单选题
        elif "input" in str(q_div) and "text" in str(q_div):
            raw_type_code = "1"  # 填空题
        elif "scale" in str(q_div) or "量表" in q_div.text:
            raw_type_code = "5"  # 量表题
        elif "matrix" in str(q_div) or "矩阵" in q_div.text:
            raw_type_code = "6"  # 矩阵题
        elif "select" in str(q_div) or "下拉" in q_div.text:
            raw_type_code = "7"  # 下拉框
        elif "slider" in str(q_div) or "滑块" in q_div.text:
            raw_type_code = "8"  # 滑块题
        elif "排序" in q_div.text:
            raw_type_code = "11"  # 排序题
        else:
            raw_type_code = "0"  # 未知题型

    # 3. 获取中文说明
    type_chinese = code_explain.get(raw_type_code, "未定义题型")

    # 4. 提取题目内容
    question_content = ""
    # 尝试从div.topichtml获取（新版）
    topic_html = q_div.find("div", class_="topichtml")
    if topic_html:
        question_content = topic_html.get_text(strip=True)
    else:
        # 尝试从label或其他文本元素获取（旧版）
        field_label = q_div.find("div", class_="field-label")
        if field_label:
            # 移除topicnumber和req等元素
            for elem in field_label.find_all(["div", "span"]):
                elem.extract()
            question_content = field_label.get_text(strip=True)
        else:
            # 兜底：获取整个题目的文本内容
            question_content = q_div.get_text(strip=True)[:100]  # 限制长度

    # 5. 提取选项信息
    options = []
    if raw_type_code in ["3", "4"]:  # 单选题/多选题
        # 查找所有选项
        option_elements = q_div.find_all(["div", "li"], class_=["ui-radio", "ui-checkbox"])
        for i, option in enumerate(option_elements, 1):
            # 提取选项文本
            option_text = option.find("div", class_="label").get_text(strip=True)
                
            # 检查是否是"其他"选项（带文本框）
            if "其他" in option_text and "请注明" in str(option):
                # 在选项后直接添加【填空题输入框】标记
                options.append(f"{i}. {option_text}【填空题输入框】")
            else:
                options.append(f"{i}. {option_text}")
    elif raw_type_code == "1":  # 填空题
        options.append("[填空题输入框]")
    elif raw_type_code == "5":  # 量表题
        # 先检查是否有表格结构（优先处理复杂量表）
        matrix_table = q_div.find("table", class_="matrixtable")
        if matrix_table:
            # 提取量表评分选项（表头）
            header_row = matrix_table.find("tr")
            if header_row:
                score_cells = header_row.find_all("td")[1:]  # 跳过第一列
                if score_cells:
                    scores = []
                    for cell in score_cells:
                        label = cell.find("div", class_="label") or cell
                        scores.append(label.get_text(strip=True))
                    options.append(f"评分选项: {' | '.join(scores)}")

            # 提取量表子维度（行主题）
            seen_topics = set()  # 用于去重
            row_counter = 1       # 独立计数器
                
            # 先找tbody，如果没有则跳过第一行（表头）
            tbody = matrix_table.find("tbody")
            if tbody:
                rows = tbody.find_all("tr")
            else:
                all_rows = matrix_table.find_all("tr")
                rows = all_rows[1:] if len(all_rows) > 1 else all_rows  # 跳过表头
                
            # 按顺序添加子维度，确保序号从1开始
            for row in rows:
                # 只找第一列（主题列）
                first_td = row.find("td", class_="mleft") or row.find("td")
                if first_td:
                    sub_topic = first_td.get_text(strip=True)
                    # 过滤空内容和重复内容
                    if sub_topic and sub_topic not in seen_topics:
                        seen_topics.add(sub_topic)
                        options.append(f"{row_counter}. {sub_topic}")
                        row_counter += 1
            
        # 如果没有表格结构，再尝试简单量表
        elif q_div.find_all("div", class_="ui-radio") or q_div.find_all("li"):
            simple_scale = q_div.find_all("div", class_="ui-radio") or q_div.find_all("li")
            scale_texts = []
            for i, opt in enumerate(simple_scale, 1):
                label = opt.find("div", class_="label")
                if label:
                    scale_texts.append(label.get_text(strip=True))
                else:
                    scale_texts.append(str(i))
            options.append(f"量表选项: {' | '.join(scale_texts)}")
            
        # 默认显示
        if not options:
            options.append("[量表题评分选项]")
    elif raw_type_code == "6":  # 矩阵题
        # 尝试提取矩阵题结构
        matrix_table = q_div.find("table", class_="matrixtable")
        if matrix_table:
            # 提取矩阵列选项（表头）
            header_row = matrix_table.find("tr")
            if header_row:
                # 查找所有列（包括第一列，后面再过滤）
                all_cells = header_row.find_all(["td", "th"])
                if all_cells and len(all_cells) > 1:  # 确保至少有两列（主题列+至少一个选项列）
                    # 提取列选项（跳过第一列主题列）
                    col_options = []
                    for i, cell in enumerate(all_cells[1:], 1):
                        # 尝试多种方式提取列标签
                        label = cell.find("div", class_="label")
                        if not label:
                            label = cell.find("span", class_="label")
                        if not label:
                            label = cell
                                
                        col_text = label.get_text(strip=True)
                        # 如果没有文本，使用序号
                        if not col_text:
                            col_text = str(i)
                        col_options.append(col_text)
                        
                    if col_options:
                        options.append(f"可选答案: {' | '.join(col_options)}")
                
            # 提取矩阵行主题
            seen_topics = set()  # 用于去重
            row_counter = 1       # 独立计数器
                
            # 先找tbody，如果没有则跳过第一行（表头）
            tbody = matrix_table.find("tbody")
            if tbody:
                rows = tbody.find_all("tr")
            else:
                all_rows = matrix_table.find_all("tr")
                rows = all_rows[1:] if len(all_rows) > 1 else all_rows  # 跳过表头
                
            # 按顺序添加行主题，确保序号从1开始
            for row in rows:
                # 只找第一列（主题列）
                first_td = row.find("td", class_="mleft") or row.find("td") or row.find("th")
                if first_td:
                    sub_topic = first_td.get_text(strip=True)
                    # 过滤空内容和重复内容
                    if sub_topic and sub_topic not in seen_topics:
                        seen_topics.add(sub_topic)
                        options.append(f"{row_counter}. {sub_topic}")
                        row_counter += 1
            
        # 如果没有提取到任何结构，尝试其他方式
        elif q_div.find_all("div", class_="ui-radio") or q_div.find_all("div", class_="ui-checkbox"):
            # 查找所有行容器
            row_containers = q_div.find_all("div", class_="ui-controlgroup")
            if row_containers:
                # 提取可选答案（通常是第一行的选项）
                first_row = row_containers[0]
                options_list = first_row.find_all(["div", "li"], class_=["ui-radio", "ui-checkbox"])
                if options_list:
                    col_options = []
                    for i, opt in enumerate(options_list, 1):
                        label = opt.find("div", class_="label")
                        if label:
                            col_options.append(label.get_text(strip=True))
                        else:
                            col_options.append(str(i))
                    options.append(f"可选答案: {' | '.join(col_options)}")
                    
                # 提取行主题
                seen_topics = set()
                row_counter = 1
                for row in row_containers:
                    # 查找行主题（通常在选项之前）
                    topic = row.find_previous_sibling("div", class_="topic") or row.find("div", class_="label")
                    if topic:
                        sub_topic = topic.get_text(strip=True)
                        if sub_topic and sub_topic not in seen_topics:
                            seen_topics.add(sub_topic)
                            options.append(f"{row_counter}. {sub_topic}")
                            row_counter += 1
            
        else:
            options.append("[矩阵题，请在网页中查看详细结构]")
    elif raw_type_code == "7":  # 下拉框
        # 尝试提取下拉框选项
        select = q_div.find("select")
        if select:
            option_elements = select.find_all("option")
            for i, opt in enumerate(option_elements, 1):
                if opt.get("value"):
                    options.append(f"{i}. {opt.get_text(strip=True)}")
        else:
            options.append("[下拉选择框]")
    elif raw_type_code == "8":  # 滑块题
        # 查找滑块容器（支持多种类名）
        slider_containers = q_div.find_all(["div", "ul"], class_=["slider", "ui-slider", "slidercontainer", "rangeSlider"])
            
        if slider_containers:
            # 尝试提取滑块的标签文本
            slider_labels = []
            for container in slider_containers:
                # 查找文本标签
                labels = container.find_all(["span", "div", "li"], class_=["label", "slider-label"])
                for label in labels:
                    text = label.get_text(strip=True)
                    if text and text not in slider_labels:
                        slider_labels.append(text)
                
            # 查找滑块范围数字
            min_val = q_div.find(["span", "div"], class_=["min", "slider-min"])
            max_val = q_div.find(["span", "div"], class_=["max", "slider-max"])
                
            if slider_labels:
                options.append(f"滑块标签: {' | '.join(slider_labels)}")
            elif min_val and max_val:
                options.append(f"滑块范围: {min_val.get_text(strip=True)} - {max_val.get_text(strip=True)}")
            else:
                # 兜底：提取所有数字作为范围
                numbers = re.findall(r'\d+', q_div.get_text())
                if numbers:
                    options.append(f"滑块范围: {numbers[0]} - {numbers[-1]}")
                else:
                    options.append("[滑块输入]")
        else:
            # 尝试查找其他滑块结构
            slider_els = q_div.find_all("input", type="range")
            if slider_els:
                options.append("[滑块输入]")
            else:
                options.append("[滑块输入]")
    elif raw_type_code == "11":  # 排序题
        # 查找排序选项
        sort_options = q_div.find_all("li")
        if sort_options:
            for i, opt in enumerate(sort_options, 1):
                options.append(f"{i}. {opt.get_text(strip=True)}")
        else:
            options.append("[排序题选项]")

    # 6. 检查是否有"其他"选项需要添加输入框标记（简化处理）
    if raw_type_code in ["3", "4"] and not any("其他（请注明）" in opt for opt in options):
        # 检查是否存在"其他"选项且带有文本输入框
        if "其他" in str(q_div) and q_div.find("input", type="text"):
            # 查找最后一个选项并添加标记
            for i in range(len(options)-1, -1, -1):
                if "其他" in options[i]:
                    options[i] = options[i] + "【填空题输入框】"
                    break

    # 7. 保存题目信息
    type_info_list.append(
        {"题号": question_index, "编码": raw_type_code, "题型": type_chinese, "题目内容": question_content,
         "选项": options})


def main():
    # 从命令行参数获取网址，如果没有参数则使用环境变量提供的默认值
    wjx_url = DEFAULT_WJX_URL
    if len(sys.argv) > 1:
        wjx_url = sys.argv[1]

    if not wjx_url:
        log("未提供问卷链接：请通过命令行参数传入，或设置环境变量 WJX_DEFAULT_URL")
        sys.exit(1)

    # 1. 初始化浏览器（无头模式，不显示窗口）
    driver = init_browser()
    if not driver:
        # 以非 0 退出码结束，Java 端会把 stderr 的最后内容当作失败原因展示给用户
        log("浏览器初始化失败，请检查 Edge 驱动是否可用")
        sys.exit(1)

    try:
        # 2. 获取完整页面源码（自动翻页）
        page_source = get_wjx_page_source(driver, wjx_url)
    finally:
        driver.quit()

    if not page_source:
        log("页面加载失败或超时，请检查问卷链接是否有效、是否需要登录")
        sys.exit(1)

    # 3. 解析题目（单题异常已在内部降级，不会中断整体解析）
    questions = extract_question_type_with_codes(page_source)

    # 4. 唯一的 stdout 输出：JSON 数组，Java 端原样透传给前端
    print(json.dumps(questions, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()