// 分析页面主逻辑

// 当前编辑的题目ID
let currentQuestionId = null;
let currentOptionIndex = null;

// 存储已配置的文本答案（只保存在内存中，刷新后消失）
let textAnswersConfig = {};

// 将textAnswersConfig暴露到全局作用域，供概率验证器使用
window.textAnswersConfig = textAnswersConfig;

/**
 * 统一的显隐开关：所有面板/按钮的显示与隐藏都走 .is-hidden 这一个类。
 * 之前 JS 里散落着几十处 style.display = 'block' / 'none'，
 * 与 CSS 的 .is-hidden 并存，两套机制容易互相打架。
 * @param {Element|string} target 元素或元素 id
 * @param {boolean} visible 是否显示
 */
function setVisible(target, visible) {
    const el = typeof target === 'string' ? document.getElementById(target) : target;
    if (el) {
        el.classList.toggle('is-hidden', !visible);
    }
    return el;
}

// 页面加载完成后执行
    document.addEventListener('DOMContentLoaded', function() {
        // 清空文本答案配置，确保每次刷新都是新的开始
        textAnswersConfig = {};

        // 代理开关：控制API输入框显隐（默认开启显示）
        const proxySwitch = document.getElementById('proxySwitch');
        const apiUrlGroup = document.getElementById('apiUrlGroup');
        if (proxySwitch && apiUrlGroup) {
            proxySwitch.addEventListener('change', function() {
                setVisible(apiUrlGroup, proxySwitch.checked);
            });
        }

        // 时间控制开关：控制填写区间设置显隐（默认关闭）
        const timeControlSwitch = document.getElementById('timeControlSwitch');
        const timeControlGroup = document.getElementById('timeControlGroup');
        if (timeControlSwitch && timeControlGroup) {
            timeControlSwitch.addEventListener('change', function() {
                setVisible(timeControlGroup, timeControlSwitch.checked);
            });
        }

        // 详细设置按钮：打开弹窗
        const detailSettingsBtn = document.getElementById('detailSettingsBtn');
        if (detailSettingsBtn) {
            detailSettingsBtn.addEventListener('click', function() {
                document.getElementById('detailSettingsModal').classList.add('is-open');
            });
        }

        // 填写区间输入框验证（30~150秒，超出取最小/最大值并提醒，与目标份数逻辑一致）
        const minFillTimeInput = document.getElementById('minFillTime');
        const maxFillTimeInput = document.getElementById('maxFillTime');

        // 单个输入框按 30~150 秒区间纠正自身取值
        function clampFillTime(input, fallback) {
            let value = parseInt(input.value);
            if (isNaN(value)) {
                showAlert('请输入有效的数字，填写时间应在30~150秒之间', 'error');
                value = fallback;
            } else if (value < 30) {
                showAlert('填写时间应在30~150秒之间', 'error');
                value = 30;
            } else if (value > 150) {
                showAlert('填写时间应在30~150秒之间', 'error');
                value = 150;
            }
            input.value = value;
            return value;
        }

        [minFillTimeInput, maxFillTimeInput].forEach(input => {
            if (input) {
                input.addEventListener('change', function() {
                    const isMin = this === minFillTimeInput;
                    // 1. 先保证自身在 30~150 秒之内
                    let value = clampFillTime(this, isMin ? 30 : 150);

                    // 2. 再保证「最小填写时间 <= 最大填写时间」：谁被改谁让步
                    const otherInput = isMin ? maxFillTimeInput : minFillTimeInput;
                    const otherValue = otherInput ? parseInt(otherInput.value) : NaN;
                    if (!isNaN(otherValue)) {
                        if (isMin && value > otherValue) {
                            value = otherValue;
                            this.value = value;
                            showAlert('最小填写时间不能大于最大填写时间，已调整为 ' + otherValue + ' 秒', 'error');
                        } else if (!isMin && value < otherValue) {
                            value = otherValue;
                            this.value = value;
                            showAlert('最大填写时间不能小于最小填写时间，已调整为 ' + otherValue + ' 秒', 'error');
                        }
                    }
                });
            }
        });

        // 目标份数输入框验证
        const targetCountInput = document.getElementById('targetCount');
        if (targetCountInput) {
            targetCountInput.addEventListener('change', function() {
                let value = parseInt(this.value);
                if (isNaN(value)) {
                    value = 1;
                    showAlert('请输入有效的数字，份数应在1~1000之间', 'error');
                } else if (value < 1) {
                    value = 1;
                    showAlert('份数应在1~1000之间', 'error');
                } else if (value > 1000) {
                    value = 1000;
                    showAlert('份数应在1~1000之间', 'error');
                }
                this.value = value;
            });
        }
        
        // 获取URL中的问卷星URL参数
        const urlParams = new URLSearchParams(window.location.search);
        const wjxUrl = urlParams.get('url');
    
    // 如果有问卷星URL，调用后端API获取解析结果
    if (wjxUrl) {
        // 调用封装好的API获取解析结果
        api.wjx.analyzeUrl(wjxUrl)
            .then(data => {
                const analysisContent = document.getElementById('analysisContent');
                const systemConfig = document.querySelector('.system-config');
                const brushBtn = document.getElementById('brushBtn');

                // 后端把脚本 stdout 原样透传回来，正常情况下就是一段 JSON 数组
                let questions = null;
                try {
                    questions = typeof data.data === 'string' ? JSON.parse(data.data) : data.data;
                } catch (e) {
                    console.error('后端返回的题目数据不是合法 JSON:', e);
                }

                // 数据格式不对：给出明确提示，而不是静默卡在「解析中」
                if (!Array.isArray(questions)) {
                    analysisContent.innerHTML = '<div class="state-msg is-error">问卷解析结果格式异常，无法识别题目结构，请重试或更换链接</div>';
                    setVisible(systemConfig, false);
                    setVisible(brushBtn, false);
                    return;
                }

                // 检查是否解析到题目
                if (questions.length === 0) {
                    // 没有解析到题目，显示错误信息
                    analysisContent.innerHTML = '<div class="state-msg is-error">问卷星链接有误，请检查链接</div>';

                    // 隐藏系统配置和刷问卷按钮
                    setVisible(systemConfig, false);
                    setVisible(brushBtn, false);

                    return;
                }

                // 检查是否有不支持的题型，并把具体题目号点出来
                const supportedTypes = ['单选题', '多选题', '填空题', '矩阵题', '量表题', '排序题'];
                const unsupported = questions.filter(question => {
                    const type = question.题型 || question.type;
                    return !supportedTypes.includes(type);
                });

                if (unsupported.length > 0) {
                    // 存在不支持的题型，显示错误信息
                    const detail = unsupported
                        .map(question => '第' + (question.题号 || '?') + '题（' + (question.题型 || question.type || '未知') + '）')
                        .join('、');
                    analysisContent.innerHTML = '<div class="state-msg is-error">目前系统只支持单选，多选，填空，矩阵，量表，排序这六种题型。<br>无法处理的题目：'
                        + detail
                        + '。<br>请检查您的问卷是否存在其他题型，如果确有其余题型需求，请联系管理员</div>';

                    // 隐藏系统配置和刷问卷按钮
                    setVisible(systemConfig, false);
                    setVisible(brushBtn, false);

                    return;
                }

                // 解析成功，先显示系统配置和刷问卷按钮
                setVisible(systemConfig, true);
                setVisible(brushBtn, true);

                // 然后显示解析结果
                displayQuestions(questions);

                // 触发questionsLoaded事件，通知概率验证器初始化实时验证
                window.dispatchEvent(new Event('questionsLoaded'));
            })
            .catch(error => {
                // 显示错误信息
                const analysisContent = document.getElementById('analysisContent');
                analysisContent.innerHTML = '<div class="state-msg is-error">解析失败：' + error.message + '</div>';
                console.error('解析错误:', error);

                // 解析失败，隐藏系统配置和刷问卷按钮
                setVisible(document.querySelector('.system-config'), false);
                setVisible('brushBtn', false);
            });
    } else {
        // 没有问卷星URL，显示错误信息
        const analysisContent = document.getElementById('analysisContent');
        analysisContent.innerHTML = '<div class="state-msg is-error">未获取到问卷星URL参数</div>';

        // 没有问卷星URL，隐藏系统配置和刷问卷按钮
        setVisible(document.querySelector('.system-config'), false);
        setVisible('brushBtn', false);
    }
    
    // 关闭弹窗 - 通用逻辑
    const closeBtns = document.getElementsByClassName('close');
    for (let i = 0; i < closeBtns.length; i++) {
        closeBtns[i].onclick = function() {
            const modal = this.closest('.modal');
            if (modal) {
                modal.classList.remove('is-open');
            }
        };
    }
    
    // 注：不再支持点击弹窗外部关闭（按用户要求，弹窗只能通过关闭按钮关闭）

    // 系统使用简介按钮点击事件
    const usageIntroBtn = document.getElementById('usageIntroBtn');
    if (usageIntroBtn) {
        usageIntroBtn.addEventListener('click', function() {
            const modal = document.getElementById('usageIntroModal');
            modal.classList.add('is-open');
        });
    }

    
    // 一键随机概率按钮点击事件
    const randomProbBtn = document.getElementById('randomProbBtn');
    if (randomProbBtn) {
        randomProbBtn.addEventListener('click', function() {
            generateRandomProbabilities();
        });
    }
    
    // 历史运行结果按钮点击事件
    const historyBtn = document.getElementById('historyBtn');
    if (historyBtn) {
        historyBtn.addEventListener('click', function() {
            // 更新历史记录弹窗内容
            updateHistoryModal();
            const modal = document.getElementById('historyModal');
            modal.classList.add('is-open');
        });
    }
    
    // 页面加载时更新历史记录弹窗内容
    updateHistoryModal();
    
    // 开始刷问卷按钮点击事件（添加概率检测）
    const brushBtn = document.getElementById('brushBtn');
    if (brushBtn) {
        brushBtn.addEventListener('click', function() {
            console.log('点击开刷按钮，开始验证...');
            // 执行概率检测
            const result = ProbabilityValidator.validateAll();
            
            console.log('验证结果:', result);
            
            if (result.isValid) {
                // 概率配置合法，可以继续执行刷问卷操作
                // 获取当前设置的参数
            const targetCount = document.getElementById('targetCount').value;
            const urlParams = new URLSearchParams(window.location.search);
            const url = urlParams.get('url');
            const speedMultiplier = 2; // 默认使用2线程
            // 从页面上的设置项直接读取（仅本次使用）
            // 界面模式开关：勾选=弹出浏览器窗口；默认关闭=无界面（后台静默运行）
            const guiMode = document.getElementById('guiMode').checked;
            const headless = !guiMode;
            const useProxy = document.getElementById('proxySwitch').checked; // 默认开启代理
            const ipApiUrl = document.getElementById('apiUrl').value.trim();
            // 时间控制（默认关闭）
            const timeControl = document.getElementById('timeControlSwitch').checked;
            let minFillTime = parseInt(document.getElementById('minFillTime').value) || 30;
            let maxFillTime = parseInt(document.getElementById('maxFillTime').value) || 150;
            // 兜底：确保「最小填写时间 <= 最大填写时间」
            if (minFillTime > maxFillTime) {
                const tmp = minFillTime;
                minFillTime = maxFillTime;
                maxFillTime = tmp;
            }

            // 自定义确认对话框（替代阻塞式 confirm），确认后再真正启动任务
            askConfirm({
                title: '确认开始任务',
                rows: [
                    ['问卷链接', url],
                    ['目标份数', targetCount + ' 份'],
                    ['ip代理', useProxy ? '开启' : '关闭'],
                    ['界面模式', guiMode ? '开启' : '关闭'],
                    ['时间控制', timeControl ? `${minFillTime} ~ ${maxFillTime} 秒（一个 IP 只填一份）` : '关闭']
                ],
                message: '任务开始后无法中途暂停，请确认以上参数无误。',
                okText: '开始执行'
            }).then(function (ok) {
                if (!ok) return;
                // 收集所有题目配置
                const questionsConfig = collectQuestionsConfig();

                // 构建请求数据
                const requestData = {
                    url: url,
                    // 目标份数直接使用用户输入的值
                    targetCount: parseInt(targetCount),
                    speedMultiplier: speedMultiplier,
                    questions: questionsConfig,
                    headless: headless,
                    useProxy: useProxy,
                    ipApiUrl: ipApiUrl,
                    timeControl: timeControl,
                    minFillTime: minFillTime,
                    maxFillTime: maxFillTime
                };
                    
                    // 保存当前配置状态
                    const currentConfig = {
                        targetCount: targetCount,
                        speedMultiplier: speedMultiplier,
                        questionsConfig: questionsConfig
                    };
                    localStorage.setItem('wjxCurrentConfig', JSON.stringify(currentConfig));
                    
                    // 调用后端API启动异步任务
                    // 历史记录改到后端确认启动成功之后再写：任务号用后端返回的真实 taskId，
                    // 启动失败时也不会留下一条假的「运行中」记录
                    api.wjx.brushStart(requestData)
                        .then(response => {
                            if (response.code === 1) {
                                addHistoryRecord({
                                    url: url,
                                    targetCount: targetCount,
                                    speedMultiplier: speedMultiplier,
                                    result: { message: '任务正在运行中...' },
                                    timestamp: new Date().toLocaleString(),
                                    taskId: response.data.taskId
                                });
                                // 启动成功，开始监控进度
                                startProgressMonitoring(response.data.taskId);
                            } else {
                                showAlert('任务启动失败: ' + response.msg, 'error');
                                // 恢复界面
                                setVisible('analysisContent', true);
                                setVisible('brushBtn', true);
                            }
                        })
                        .catch(error => {
                            console.error('任务启动失败:', error);
                            showAlert('任务启动失败: ' + error.message, 'error');
                            // 恢复界面
                            setVisible('analysisContent', true);
                            setVisible('brushBtn', true);
                        });
            });
            } else {
                console.log('验证失败，显示错误信息:', result.errors);
                // 显示错误信息
                ProbabilityValidator.showErrors(result.errors);
                
                // 定位到第一个有问题的题目或选项
                if (result.errors.length > 0) {
                    console.log('定位到第一个错误:', result.errors[0]);
                    // 从第一个错误信息中提取题目ID和可能的选项索引
                    const firstError = result.errors[0];
                    const questionIdMatch = firstError.match(/第(\d+)题/);
                    const optionIndexMatch = firstError.match(/选项(\d+)/);
                    
                    if (questionIdMatch) {
                        const questionId = questionIdMatch[1];
                        console.log('定位到题目:', questionId);
                        const questionElement = document.querySelector(`[data-question-id="${questionId}"]`);
                        
                        if (questionElement) {
                            let targetElement = questionElement;
                            
                            // 如果错误信息中包含选项索引，尝试定位到具体选项
                            if (optionIndexMatch) {
                                const optionIndex = parseInt(optionIndexMatch[1]) - 1; // 转换为0-based索引
                                console.log('定位到选项索引:', optionIndex);
                                const optionElements = questionElement.querySelectorAll('.option-item');
                                console.log('题目选项数量:', optionElements.length);
                                
                                if (optionElements.length > optionIndex) {
                                    targetElement = optionElements[optionIndex];
                                    console.log('成功定位到选项元素');
                                }
                            }
                            
                            // 滚动到目标元素位置
                            console.log('滚动到目标元素');
                            targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            
                            // 添加视觉提示，高亮显示目标元素
                            targetElement.classList.add('is-invalid');
                            
                            // 3秒后移除视觉提示
                            setTimeout(() => {
                                targetElement.classList.remove('is-invalid');
                            }, 3000);
                        } else {
                            console.log('未找到题目元素:', questionId);
                        }
                    } else {
                        console.log('无法从错误信息中提取题目ID:', firstError);
                    }
                }
            }
        });
    }
});

/**
 * 自定义确认对话框，替代阻塞式 window.confirm。
 * @param {Object}   opts
 * @param {string}  [opts.title]   标题
 * @param {Array}   [opts.rows]    参数摘要 [[标签, 值], ...]
 * @param {string}  [opts.message] 提示正文
 * @param {string}  [opts.okText]  确认按钮文案
 * @param {boolean} [opts.danger]  确认按钮是否用危险样式
 * @returns {Promise<boolean>} 用户是否点击了确认
 */
function askConfirm(opts) {
    opts = opts || {};

    const modal = document.getElementById('confirmModal');
    const titleEl = document.getElementById('confirmTitle');
    const listEl = document.getElementById('confirmList');
    const msgEl = document.getElementById('confirmMessage');
    const okBtn = document.getElementById('confirmOk');
    const cancelBtn = document.getElementById('confirmCancel');

    // 兜底：对话框节点缺失时退回原生 confirm
    if (!modal || !okBtn || !cancelBtn) {
        return Promise.resolve(window.confirm(opts.message || opts.title || '确认继续？'));
    }

    titleEl.textContent = opts.title || '确认操作';

    listEl.innerHTML = '';
    const rows = opts.rows || [];
    rows.forEach(function (row) {
        const dt = document.createElement('dt');
        dt.textContent = row[0];
        const dd = document.createElement('dd');
        dd.textContent = row[1];
        listEl.appendChild(dt);
        listEl.appendChild(dd);
    });
    listEl.classList.toggle('is-hidden', rows.length === 0);

    msgEl.textContent = opts.message || '';
    msgEl.classList.toggle('is-hidden', !opts.message);

    okBtn.textContent = opts.okText || '确认';
    okBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');

    modal.classList.add('is-open');

    return new Promise(function (resolve) {
        function finish(result) {
            modal.classList.remove('is-open');
            modal.onclick = null;
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            document.removeEventListener('keydown', onKey);
            resolve(result);
        }
        function onOk() { finish(true); }
        function onCancel() { finish(false); }
        function onKey(e) { if (e.key === 'Escape') finish(false); }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        document.addEventListener('keydown', onKey);
        // 点击遮罩也视为取消
        modal.onclick = function (e) { if (e.target === modal) finish(false); };

        okBtn.focus();
    });
}

// 确认返回首页的函数
function confirmBack() {
    askConfirm({
        title: '返回首页',
        message: '返回首页后当前页面的配置将会清空。',
        okText: '确定'
    }).then(function (ok) {
        if (ok) {
            history.back();
        }
    });
}

// 显示题目
function displayQuestions(questions) {
    const container = document.getElementById('analysisContent');
    
    // 清空容器，只保留加载中内容
    container.innerHTML = '';
    
    questions.forEach(question => {
        // 转换JSON数据格式为前端所需格式
        const questionData = {
            number: question.题号,
            type: question.题型,
            content: question.题目内容,
            options: question.选项
        };
        
        const questionDiv = document.createElement('div');
        questionDiv.className = 'question-container';
        questionDiv.dataset.questionId = questionData.number;
        
        // 题目头部
        const headerDiv = document.createElement('div');
        headerDiv.className = 'question-header';
        
        const numberSpan = document.createElement('span');
        numberSpan.className = 'question-number';
        numberSpan.textContent = questionData.number;
        
        const typeSpan = document.createElement('span');
        typeSpan.className = 'question-type';
        typeSpan.textContent = questionData.type;
        
        headerDiv.appendChild(numberSpan);
        headerDiv.appendChild(typeSpan);
        
        // 题目内容
        const contentDiv = document.createElement('div');
        contentDiv.className = 'question-content';
        contentDiv.textContent = questionData.content;
        
        // 选项容器
        const optionsDiv = document.createElement('div');
        optionsDiv.className = 'options-container';
        
        // 根据题型显示不同的选项
        switch(questionData.type) {
            case '单选题':
                displaySingleChoiceOptions(questionData, optionsDiv);
                break;
            case '多选题':
                displayMultipleChoiceOptions(questionData, optionsDiv);
                break;
            case '填空题':
                displayFillBlankOptions(questionData, optionsDiv);
                break;
            case '矩阵题':
                displayMatrixOptions(questionData, optionsDiv);
                break;
            case '量表题':
                displayScaleOptions(questionData, optionsDiv);
                break;
            default:
                displayDefaultOptions(questionData, optionsDiv);
        }
        
        questionDiv.appendChild(headerDiv);
        questionDiv.appendChild(contentDiv);
        questionDiv.appendChild(optionsDiv);
        
        container.appendChild(questionDiv);
    });
}

// 显示单选题选项
function displaySingleChoiceOptions(question, container) {
    question.options.forEach((option, index) => {
        const optionDiv = document.createElement('div');
        optionDiv.className = 'option-item';
        
        const optionText = document.createElement('div');
        optionText.className = 'option-text';
        optionText.textContent = option;
        
        const inputDiv = document.createElement('div');
        inputDiv.style.display = 'flex';
        inputDiv.style.alignItems = 'center';
        
        // 检查是否是带文本输入的选项（单选题也可能有"其他"选项）
                if (option.includes('【填空题输入框】')) {
                    const configBtn = document.createElement('button');
                    configBtn.type = 'button';
                    configBtn.className = 'btn btn-sm btn-primary';
                    configBtn.textContent = '点击编辑文本答案';
                    configBtn.onclick = () => openTextAnswerModal(question.number, index);
                    configBtn.style.marginRight = '5px';
                    
                    inputDiv.appendChild(configBtn);
                }
        
        const probabilityInput = document.createElement('input');
        probabilityInput.type = 'number';
        probabilityInput.className = 'probability-input form-control';
        probabilityInput.placeholder = '概率';
        probabilityInput.min = '0';
        probabilityInput.max = '100';
        probabilityInput.dataset.questionId = question.number;
        probabilityInput.dataset.optionIndex = index;
        probabilityInput.style.marginRight = '5px';
        
        const percentageSpan = document.createElement('span');
        percentageSpan.textContent = '%';
        percentageSpan.style.marginRight = '10px';
        
        inputDiv.appendChild(probabilityInput);
        inputDiv.appendChild(percentageSpan);
        
        optionDiv.appendChild(optionText);
        optionDiv.appendChild(inputDiv);
        
        container.appendChild(optionDiv);
    });
}

// 显示多选题选项
function displayMultipleChoiceOptions(question, container) {
    question.options.forEach((option, index) => {
        const optionDiv = document.createElement('div');
        optionDiv.className = 'option-item';
        
        const optionText = document.createElement('div');
        optionText.className = 'option-text';
        optionText.textContent = option;
        
        const inputDiv = document.createElement('div');
        inputDiv.style.display = 'flex';
        inputDiv.style.alignItems = 'center';
        
        // 检查是否是带文本输入的选项
                if (option.includes('【填空题输入框】')) {
                    const configBtn = document.createElement('button');
                    configBtn.type = 'button';
                    configBtn.className = 'btn btn-sm btn-primary';
                    configBtn.textContent = '点击编辑文本答案';
                    configBtn.onclick = () => openTextAnswerModal(question.number, index);
                    configBtn.style.marginRight = '5px';
                    
                    inputDiv.appendChild(configBtn);
                }
        
        const probabilityInput = document.createElement('input');
        probabilityInput.type = 'number';
        probabilityInput.className = 'probability-input form-control';
        probabilityInput.placeholder = '概率';
        probabilityInput.min = '0';
        probabilityInput.max = '100';
        probabilityInput.dataset.questionId = question.number;
        probabilityInput.dataset.optionIndex = index;
        probabilityInput.style.marginRight = '5px';
        
        const percentageSpan = document.createElement('span');
        percentageSpan.textContent = '%';
        percentageSpan.style.marginRight = '10px';
        
        inputDiv.appendChild(probabilityInput);
        inputDiv.appendChild(percentageSpan);
        
        optionDiv.appendChild(optionText);
        optionDiv.appendChild(inputDiv);
        
        container.appendChild(optionDiv);
    });
}

// 显示填空题选项
function displayFillBlankOptions(question, container) {
    const fillBlankDiv = document.createElement('div');
    fillBlankDiv.className = 'fill-blank-container';
    
    const inputDiv = document.createElement('div');
    
    const configBtn = document.createElement('button');
            configBtn.type = 'button';
            configBtn.className = 'btn btn-sm btn-primary';
            configBtn.textContent = '点击编辑文本答案';
            configBtn.onclick = () => openTextAnswerModal(question.number, 0);
    
    inputDiv.appendChild(configBtn);
    
    fillBlankDiv.appendChild(inputDiv);
    container.appendChild(fillBlankDiv);
}

// 显示矩阵题选项
function displayMatrixOptions(question, container) {
    // 解析可选答案和行选项
    let optionalAnswers = [];
    const rowOptions = [];
    
    question.options.forEach(option => {
        if (option.startsWith('可选答案:')) {
            optionalAnswers = option.replace('可选答案:', '').split('|').map(ans => ans.trim());
        } else if (option.match(/^\d+\./)) {
            rowOptions.push(option);
        }
    });
    
    if (optionalAnswers.length === 0 || rowOptions.length === 0) {
        displayDefaultOptions(question, container);
        return;
    }
    
    const tableDiv = document.createElement('div');
    tableDiv.className = 'matrix-container';
    
    const table = document.createElement('table');
    table.className = 'matrix-table';
    
    // 创建表头
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    
    const emptyCell = document.createElement('th');
    emptyCell.style.textAlign = 'center';
    emptyCell.style.verticalAlign = 'middle';
    emptyCell.style.padding = '5px';
    headerRow.appendChild(emptyCell);
    
    optionalAnswers.forEach(answer => {
        const th = document.createElement('th');
        th.textContent = answer;
        th.style.textAlign = 'center';
        th.style.verticalAlign = 'middle';
        th.style.padding = '5px';
        headerRow.appendChild(th);
    });
    
    thead.appendChild(headerRow);
    table.appendChild(thead);
    
    // 创建表格内容
    const tbody = document.createElement('tbody');
    
    rowOptions.forEach((rowOption, rowIndex) => {
        const row = document.createElement('tr');
        
        const rowLabelCell = document.createElement('td');
        rowLabelCell.textContent = rowOption;
        rowLabelCell.style.textAlign = 'left';
        rowLabelCell.style.verticalAlign = 'middle';
        rowLabelCell.style.padding = '5px';
        rowLabelCell.style.fontWeight = 'normal';
        row.appendChild(rowLabelCell);
        
        optionalAnswers.forEach((answer, colIndex) => {
            const cell = document.createElement('td');
            cell.style.textAlign = 'center';
            cell.style.verticalAlign = 'middle';
            
            const inputDiv = document.createElement('div');
            inputDiv.style.display = 'flex';
            inputDiv.style.alignItems = 'center';
            inputDiv.style.justifyContent = 'center';
            
            const probabilityInput = document.createElement('input');
            probabilityInput.type = 'number';
            probabilityInput.className = 'probability-input form-control';
            probabilityInput.placeholder = '概率';
            probabilityInput.min = '1';
            probabilityInput.max = '100';
            probabilityInput.dataset.questionId = question.number;
            probabilityInput.dataset.rowIndex = rowIndex;
            probabilityInput.dataset.colIndex = colIndex;
            probabilityInput.style.marginRight = '5px';
            
            const percentageSpan = document.createElement('span');
            percentageSpan.textContent = '%';
            
            inputDiv.appendChild(probabilityInput);
            inputDiv.appendChild(percentageSpan);
            
            cell.appendChild(inputDiv);
            row.appendChild(cell);
        });
        
        tbody.appendChild(row);
    });
    
    table.appendChild(tbody);
    tableDiv.appendChild(table);
    container.appendChild(tableDiv);
}

// 显示量表题选项
function displayScaleOptions(question, container) {
    // 解析量表选项
    let scaleOptions = [];
    
    question.options.forEach(option => {
        if (option.startsWith('量表选项:')) {
            scaleOptions = option.replace('量表选项:', '').split('|').map(opt => opt.trim());
        }
    });
    
    if (scaleOptions.length === 0) {
        displayDefaultOptions(question, container);
        return;
    }
    
    // 创建表格容器以确保对齐
    const tableDiv = document.createElement('div');
    tableDiv.style.overflowX = 'auto';
    
    const table = document.createElement('table');
    table.style.width = '100%';
    table.style.borderCollapse = 'collapse';
    
    // 创建表头
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    
    // 空表头单元格
    const emptyTh = document.createElement('th');
    emptyTh.style.padding = '5px';
    headerRow.appendChild(emptyTh);
    
    // 添加量表选项作为表头
    scaleOptions.forEach(option => {
        const th = document.createElement('th');
        th.textContent = option;
        th.style.padding = '5px';
        th.style.textAlign = 'center';
        headerRow.appendChild(th);
    });
    
    thead.appendChild(headerRow);
    table.appendChild(thead);
    
    // 创建表格内容行
    const tbody = document.createElement('tbody');
    const contentRow = document.createElement('tr');
    
    // 第一列显示"概率"
    const labelTd = document.createElement('td');
    labelTd.textContent = '概率';
    labelTd.style.padding = '5px';
    labelTd.style.fontWeight = 'bold';
    contentRow.appendChild(labelTd);
    
    // 为每个量表选项添加概率输入框
    scaleOptions.forEach((option, index) => {
        const td = document.createElement('td');
        td.style.padding = '5px';
        td.style.textAlign = 'center';
        
        const inputDiv = document.createElement('div');
        inputDiv.style.display = 'flex';
        inputDiv.style.alignItems = 'center';
        inputDiv.style.justifyContent = 'center';
        
        const probabilityInput = document.createElement('input');
        probabilityInput.type = 'number';
        probabilityInput.className = 'probability-input form-control';
        probabilityInput.placeholder = '概率';
        probabilityInput.min = '0';
        probabilityInput.max = '100';
        probabilityInput.dataset.questionId = question.number;
        probabilityInput.dataset.optionIndex = index;
        probabilityInput.style.marginRight = '5px';
        
        const percentageSpan = document.createElement('span');
        percentageSpan.textContent = '%';
        
        inputDiv.appendChild(probabilityInput);
        inputDiv.appendChild(percentageSpan);
        
        td.appendChild(inputDiv);
        contentRow.appendChild(td);
    });
    
    tbody.appendChild(contentRow);
    table.appendChild(tbody);
    
    tableDiv.appendChild(table);
    container.appendChild(tableDiv);
}

// 显示默认选项
function displayDefaultOptions(question, container) {
    question.options.forEach((option, index) => {
        const optionDiv = document.createElement('div');
        optionDiv.className = 'option-item';
        
        const optionText = document.createElement('div');
        optionText.className = 'option-text';
        optionText.textContent = option;
        
        optionDiv.appendChild(optionText);
        container.appendChild(optionDiv);
    });
}

// 打开文本答案配置弹窗
function openTextAnswerModal(questionId, optionIndex) {
    currentQuestionId = questionId;
    currentOptionIndex = optionIndex;
    
    // 清空现有表单
    const answerList = document.getElementById('answerList');
    answerList.innerHTML = '';
    
    // 检查是否已有配置
    const configKey = `${questionId}_${optionIndex}`;
    const existingAnswers = textAnswersConfig[configKey];
    
    if (existingAnswers && existingAnswers.length > 0) {
        // 显示已有配置
        existingAnswers.forEach((answer, index) => {
            const answerItem = document.createElement('div');
            answerItem.className = 'answer-item';
            answerItem.style.display = 'flex';
            answerItem.style.alignItems = 'center';
            answerItem.style.marginBottom = '10px';
            
            const textInput = document.createElement('input');
            textInput.type = 'text';
            textInput.id = 'answerText' + index;
            textInput.className = 'answer-text form-control';
            textInput.placeholder = '输入文本答案';
            textInput.value = answer.text;
            textInput.style.flex = '2';
            textInput.style.marginRight = '10px';
            
            const probabilityContainer = document.createElement('div');
            probabilityContainer.style.display = 'flex';
            probabilityContainer.style.alignItems = 'center';
            
            const probabilityInput = document.createElement('input');
            probabilityInput.type = 'number';
            probabilityInput.id = 'answerProb' + index;
            probabilityInput.className = 'probability-input form-control';
            probabilityInput.placeholder = '概率';
            probabilityInput.min = '0';
            probabilityInput.max = '100';
            probabilityInput.value = answer.probability;
            probabilityInput.style.width = '80px';
            probabilityInput.style.marginRight = '5px';
            
            const percentageSpan = document.createElement('span');
            percentageSpan.textContent = '%';
            percentageSpan.style.marginRight = '10px';
            
            probabilityContainer.appendChild(probabilityInput);
            probabilityContainer.appendChild(percentageSpan);
            
            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'btn btn-sm btn-danger';
            deleteBtn.textContent = '删除';
            deleteBtn.onclick = () => removeAnswerItem(deleteBtn);
            deleteBtn.style.minWidth = '60px';
            
            answerItem.appendChild(textInput);
            answerItem.appendChild(probabilityContainer);
            answerItem.appendChild(deleteBtn);
            
            answerList.appendChild(answerItem);
        });
    } else {
        // 添加一个空的答案项
        addAnswerItem();
    }
    
    const modal = document.getElementById('textAnswerModal');
    modal.classList.add('is-open');
}

// 验证单个文本答案输入
function validateSingleAnswerItem(textInput, probabilityInput) {
    // 先移除之前的错误样式
    textInput.classList.remove('is-invalid');
    probabilityInput.classList.remove('is-invalid');
    
    const text = textInput.value.trim();
    const probabilityValue = probabilityInput.value.trim();
    
    // 检查文本内容
    if (text === '') {
        textInput.classList.add('is-invalid');
        return false;
    }
    
    // 检查概率
    if (probabilityValue === '') {
        probabilityInput.classList.add('is-invalid');
        return false;
    } else {
        const probability = parseInt(probabilityValue);
        if (isNaN(probability)) {
            probabilityInput.classList.add('is-invalid');
            return false;
        } else if (probability < 0 || probability > 100) {
            probabilityInput.classList.add('is-invalid');
            return false;
        }
    }
    
    return true;
}

// 添加答案项
function addAnswerItem() {
    const container = document.getElementById('answerList');
    const itemCount = container.children.length;
    
    const answerItem = document.createElement('div');
    answerItem.className = 'answer-item';
    answerItem.style.display = 'flex';
    answerItem.style.alignItems = 'center';
    answerItem.style.marginBottom = '10px';
    
    const textInput = document.createElement('input');
    textInput.type = 'text';
    textInput.id = 'answerText' + itemCount;
    textInput.className = 'answer-text form-control';
    textInput.placeholder = '输入文本答案';
    textInput.style.flex = '2';
    textInput.style.marginRight = '10px';
    
    const probabilityContainer = document.createElement('div');
    probabilityContainer.style.display = 'flex';
    probabilityContainer.style.alignItems = 'center';
    
    const probabilityInput = document.createElement('input');
    probabilityInput.type = 'number';
    probabilityInput.id = 'answerProb' + itemCount;
    probabilityInput.className = 'probability-input form-control';
    probabilityInput.placeholder = '概率';
    probabilityInput.min = '0';
    probabilityInput.max = '100';
    probabilityInput.style.width = '80px';
    probabilityInput.style.marginRight = '5px';
    
    const percentageSpan = document.createElement('span');
    percentageSpan.textContent = '%';
    percentageSpan.style.marginRight = '10px';
    
    // 添加blur事件监听器
    textInput.addEventListener('blur', function() {
        validateSingleAnswerItem(textInput, probabilityInput);
    });
    
    probabilityInput.addEventListener('blur', function() {
        validateSingleAnswerItem(textInput, probabilityInput);
    });
    
    probabilityContainer.appendChild(probabilityInput);
    probabilityContainer.appendChild(percentageSpan);
    
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'btn btn-sm btn-danger';
    deleteBtn.textContent = '删除';
    deleteBtn.onclick = () => removeAnswerItem(deleteBtn);
    deleteBtn.style.minWidth = '60px';
    
    answerItem.appendChild(textInput);
    answerItem.appendChild(probabilityContainer);
    answerItem.appendChild(deleteBtn);
    
    container.appendChild(answerItem);
}

// 移除答案项
function removeAnswerItem(btn) {
    const answerList = document.getElementById('answerList');
    const answerItems = answerList.getElementsByClassName('answer-item');
    
    if (answerItems.length > 1) {
        btn.parentElement.remove();
    } else {
        showAlert('至少需要保留一个文本答案', 'warning');
    }
}

// 保存文本答案
function saveTextAnswers() {
    const answerList = document.getElementById('answerList');
    const answerItems = answerList.getElementsByClassName('answer-item');
    
    const answers = [];
    const errors = [];
    
    // 验证每个文本答案项
    for (let i = 0; i < answerItems.length; i++) {
        const item = answerItems[i];
        const textInput = item.getElementsByClassName('answer-text')[0];
        const probabilityInput = item.getElementsByClassName('probability-input')[0];
        
        // 先移除之前的错误样式
        textInput.classList.remove('is-invalid');
        probabilityInput.classList.remove('is-invalid');
        
        const text = textInput.value.trim();
        const probabilityValue = probabilityInput.value.trim();
        
        // 检查文本内容
        if (text === '') {
            errors.push(`第${i + 1}个文本答案不能为空`);
            textInput.classList.add('is-invalid');
        }
        
        // 检查概率
        if (probabilityValue === '') {
            errors.push(`第${i + 1}个文本答案的概率不能为空`);
            probabilityInput.classList.add('is-invalid');
        } else {
            const probability = parseInt(probabilityValue);
            if (isNaN(probability)) {
                errors.push(`第${i + 1}个文本答案的概率不是有效数字`);
                probabilityInput.classList.add('is-invalid');
            } else if (probability < 0 || probability > 100) {
                errors.push(`第${i + 1}个文本答案的概率必须在0-100之间`);
                probabilityInput.classList.add('is-invalid');
            }
        }
        
        // 如果当前项验证通过，添加到答案列表
        if (text !== '' && probabilityValue !== '' && !isNaN(parseInt(probabilityValue))) {
            answers.push({
                text: text,
                probability: parseInt(probabilityValue)
            });
        }
    }
    
    // 检查是否至少有一个有效答案
    if (answers.length === 0) {
        errors.push('请至少配置一个有效的文本答案');
    }
    
    // 检查概率总和是否为100%
    if (answers.length > 0) {
        const totalProbability = answers.reduce((sum, answer) => sum + answer.probability, 0);
        if (totalProbability !== 100) {
            errors.push(`文本答案的概率总和必须为100%，当前总和为${totalProbability}%`);
            // 为所有概率输入框添加错误样式
            for (let i = 0; i < answerItems.length; i++) {
                const probabilityInput = answerItems[i].getElementsByClassName('probability-input')[0];
                probabilityInput.classList.add('is-invalid');
            }
        }
    }
    
    // 如果有错误，显示错误提示
    if (errors.length > 0) {
        if (window.ProbabilityValidator) {
            // 如果有概率验证器，使用其错误显示功能
            window.ProbabilityValidator.showErrors(errors);
        } else {
            // 否则使用alert.js显示错误
            showAlert(errors.join('\n'), 'error');
        }
        return;
    }
    
    // 保存答案到全局变量（只保存在内存中，刷新后消失）
    const configKey = `${currentQuestionId}_${currentOptionIndex}`;
    textAnswersConfig[configKey] = answers;
    
    // 同时更新全局变量，确保概率验证器能够读取到最新配置
    window.textAnswersConfig = textAnswersConfig;
    
    console.log('保存文本答案:', {
        questionId: currentQuestionId,
        optionIndex: currentOptionIndex,
        answers: answers
    });
    console.log('当前所有文本答案配置:', textAnswersConfig);
    
    // 保存成功提示
    showAlert('文本答案配置成功！', 'success');
    
    // 关闭弹窗
    const modal = document.getElementById('textAnswerModal');
    modal.classList.remove('is-open');
    
    // 清空表单
    answerList.innerHTML = '';
    addAnswerItem();
    
    // 验证当前题目，确保错误提示被移除
    if (window.ProbabilityValidator) {
        const questionContainer = document.querySelector(`[data-question-id="${currentQuestionId}"]`);
        const questionType = questionContainer.querySelector('.question-type').textContent;
        
        switch(questionType) {
            case '单选题':
                window.ProbabilityValidator.validateSingleChoice(currentQuestionId);
                break;
            case '多选题':
                window.ProbabilityValidator.validateMultipleChoice(currentQuestionId);
                break;
            case '填空题':
                window.ProbabilityValidator.validateFillBlank(currentQuestionId);
                break;
            default:
                // 其他题型也尝试验证
                window.ProbabilityValidator.validateAll();
                break;
        }
    }
    
    // 额外保险：直接移除当前题目的所有错误提示
    const questionContainer = document.querySelector(`[data-question-id="${currentQuestionId}"]`);
    if (questionContainer) {
        // 移除题目级别的错误提示
        const errorId = questionContainer.dataset.errorId;
        if (errorId) {
            const floatError = document.getElementById(errorId);
            if (floatError) {
                floatError.remove();
            }
            questionContainer.removeAttribute('data-error-id');
        }
        
        // 移除可能存在的浮动错误提示
        const floatErrors = document.querySelectorAll('.question-error-float');
        floatErrors.forEach(error => {
            const errorText = error.textContent;
            if (errorText.includes(`第${currentQuestionId}题`)) {
                error.remove();
            }
        });
    }
}

// 收集所有题目配置
function collectQuestionsConfig() {
    const config = [];
    const questionContainers = document.querySelectorAll('.question-container');
    
    questionContainers.forEach(container => {
        const questionType = container.querySelector('.question-type').textContent;
        const questionId = container.dataset.questionId;
        const questionConfig = {
            id: questionId,
            type: questionType,
            options: []
        };
        
        // 根据题型收集不同的配置
        switch(questionType) {
            case '单选题':
            case '多选题':
                const options = container.querySelectorAll('.option-item');
                options.forEach((option, index) => {
                    const probInput = option.querySelector('.probability-input');
                    if (probInput) {
                        questionConfig.options.push({
                            index: index,
                            probability: parseInt(probInput.value) || 0
                        });
                    }
                    
                    // 检查是否有文本答案配置（针对带文本输入的选项）
                    const configKey = `${questionId}_${index}`;
                    if (textAnswersConfig[configKey]) {
                        // 为每个选项单独存储文本答案配置
                        if (!questionConfig.optionTextAnswers) {
                            questionConfig.optionTextAnswers = {};
                        }
                        questionConfig.optionTextAnswers[index] = textAnswersConfig[configKey];
                    }
                });
                break;
            case '填空题':
                // 填空题直接使用文本答案配置
                const fillKey = `${questionId}_0`;
                if (textAnswersConfig[fillKey]) {
                    questionConfig.textAnswers = textAnswersConfig[fillKey];
                }
                break;
            case '矩阵题':
                const rows = container.querySelectorAll('.matrix-table tbody tr');
                rows.forEach((row, rowIndex) => {
                    const rowConfig = {
                        rowIndex: rowIndex,
                        options: []
                    };
                    const cells = row.querySelectorAll('td:not(:first-child)');
                    cells.forEach((cell, colIndex) => {
                        const probInput = cell.querySelector('.probability-input');
                        if (probInput) {
                            rowConfig.options.push({
                                colIndex: colIndex,
                                probability: parseInt(probInput.value) || 0
                            });
                        }
                    });
                    questionConfig.options.push(rowConfig);
                });
                break;
            case '量表题':
                const scaleInputs = container.querySelectorAll('.probability-input');
                scaleInputs.forEach((input, index) => {
                    questionConfig.options.push({
                        index: index,
                        probability: parseInt(input.value) || 0
                    });
                });
                break;
        }
        
        config.push(questionConfig);
    });
    
    return config;
}

// 显示程序运行完成界面
function showCompletedInterface(data) {
    const runningInterface = document.getElementById('runningInterface');
    if (runningInterface) {
        runningInterface.remove();
    }
    
    const analysisContent = document.getElementById('analysisContent');
    setVisible(analysisContent, true);

    // 创建完成界面
    const completedInterface = document.createElement('div');
    completedInterface.id = 'completedInterface';
    completedInterface.className = 'result-wrap';

    let successCount = 0;
    let failedCount = 0;

    // 提取成功和失败次数
    if (data.data && data.data.successCount !== undefined) {
        successCount = data.data.successCount;
    }
    if (data.data && data.data.failedCount !== undefined) {
        failedCount = data.data.failedCount;
    }

    // 构建统计信息（不显示失败份数）
    let statsHtml = '';
    if (successCount > 0 || failedCount > 0) {
        // 目标份数使用用户输入的值（输入框），避免与成功/失败份数混淆
        const targetInput = document.getElementById('targetCount');
        const userTarget = targetInput ? parseInt(targetInput.value) : 0;
        statsHtml = `
            <div class="result-card">
                <div class="result-stats">
                    <div>
                        <div class="result-stat-label">成功份数</div>
                        <div class="result-stat-value is-ok">${successCount}</div>
                    </div>
                    <div class="result-divider"></div>
                    <div>
                        <div class="result-stat-label">目标份数</div>
                        <div class="result-stat-value">${userTarget || (successCount + failedCount)}</div>
                    </div>
                </div>
            </div>
        `;
    }
    
    completedInterface.innerHTML = `
        <div class="result-heading">
            <i class="fa fa-check-circle" aria-hidden="true"></i> 运行结束
        </div>
        ${statsHtml}
    `;
    
    // 清空分析内容并添加完成界面
    analysisContent.innerHTML = '';
    analysisContent.appendChild(completedInterface);
}

/**
 * 追加一条历史运行记录。
 * 记录里的 taskId 由调用方传入后端返回的真实任务号，前端不再自己造一个假的。
 * @param {Object} record 记录内容
 */
function addHistoryRecord(record) {
    let history = JSON.parse(localStorage.getItem('wjxHistory') || '[]');
    history.unshift(record);
    // 限制历史记录数量为10条
    if (history.length > 10) {
        history = history.slice(0, 10);
    }
    localStorage.setItem('wjxHistory', JSON.stringify(history));
    updateHistoryModal();
}

// 更新历史记录弹窗内容
function updateHistoryModal() {
    const historyContent = document.getElementById('historyContent');
    const history = JSON.parse(localStorage.getItem('wjxHistory') || '[]');
    
    // 添加清空按钮
    let html = '<div class="history-actions"><button type="button" class="btn btn-sm btn-danger" onclick="clearHistory()">清空历史记录</button></div>';

    if (history.length === 0) {
        html += '<div class="state-msg">暂无历史运行记录</div>';
    } else {
        history.forEach((record, index) => {
            html += `
                <div class="history-item">
                    <div class="history-item-title">运行记录 ${index + 1}</div>
                    <p><strong>时间：</strong>${record.timestamp}</p>
                    <p><strong>问卷链接：</strong>${record.url}</p>
                    <p><strong>目标份数：</strong>${record.targetCount}份</p>
                </div>
            `;
        });
    }
    
    historyContent.innerHTML = html;
}

// 清空历史记录
function clearHistory() {
    askConfirm({
        title: '清空历史记录',
        message: '所有历史运行记录将被删除，此操作不可恢复。',
        okText: '清空',
        danger: true
    }).then(function (ok) {
        if (!ok) return;
        // 从localStorage中删除历史记录
        localStorage.removeItem('wjxHistory');
        // 更新历史记录弹窗内容
        updateHistoryModal();
        // 显示成功提示
        showAlert('历史记录已清空！', 'success');
    });
}

// 生成随机概率
function generateRandomProbabilities() {
    // 获取所有题目容器
    const questionContainers = document.querySelectorAll('.question-container');
    
    questionContainers.forEach(container => {
        const questionType = container.querySelector('.question-type').textContent;
        const questionId = container.dataset.questionId;
        
        switch(questionType) {
            case '单选题':
                generateSingleChoiceRandomProbs(container);
                break;
            case '多选题':
                generateMultipleChoiceRandomProbs(container);
                break;
            case '矩阵题':
                generateMatrixRandomProbs(container);
                break;
            case '量表题':
                generateScaleRandomProbs(container);
                break;
            default:
                // 其他题型暂时不处理
                break;
        }
    });
    
    // 执行整体验证，确保所有错误提示都被清除
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateAll();
    }
    
    showAlert('随机概率生成完成！', 'success');
}

// 生成单选题随机概率
function generateSingleChoiceRandomProbs(container) {
    const inputs = container.querySelectorAll('.probability-input');
    const count = inputs.length;
    
    if (count === 0) return;
    
    // 生成随机概率，确保总和为100，并提高信效度
    const probs = generateBalancedRandomProbs(count, 'single');
    
    inputs.forEach((input, index) => {
        input.value = probs[index];
        // 移除可能存在的错误样式
        input.classList.remove('is-invalid');
    });
    
    // 清除当前题目的所有浮动错误提示
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateSingleChoice(container.dataset.questionId);
    }
}

// 生成多选题随机概率
function generateMultipleChoiceRandomProbs(container) {
    const inputs = container.querySelectorAll('.probability-input');
    const count = inputs.length;
    
    if (count === 0) return;
    
    // 多选题概率生成，考虑信效度：避免极端分布
    const probs = generateBalancedRandomProbs(count, 'multiple');
    
    inputs.forEach((input, index) => {
        input.value = probs[index];
        // 移除可能存在的错误样式
        input.classList.remove('is-invalid');
    });
    
    // 清除当前题目的所有浮动错误提示
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateMultipleChoice(container.dataset.questionId);
    }
}

// 生成矩阵题随机概率
function generateMatrixRandomProbs(container) {
    const table = container.querySelector('.matrix-table');
    if (!table) return;
    
    const rows = table.querySelectorAll('tbody tr');
    
    rows.forEach(row => {
        const inputs = row.querySelectorAll('.probability-input');
        const count = inputs.length;
        
        if (count === 0) return;
        
        // 每行的概率总和为100，并提高信效度
        const probs = generateBalancedRandomProbs(count, 'matrix');
        
        inputs.forEach((input, index) => {
            input.value = probs[index];
            // 移除可能存在的错误样式
            input.classList.remove('is-invalid');
        });
    });
    
    // 清除当前题目的所有浮动错误提示
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateMatrix(container.dataset.questionId);
    }
}

// 生成量表题随机概率
function generateScaleRandomProbs(container) {
    const inputs = container.querySelectorAll('.probability-input');
    const count = inputs.length;
    
    if (count === 0) return;
    
    // 量表题概率总和为100，考虑正态分布特征提高信效度
    const probs = generateBalancedRandomProbs(count, 'scale');
    
    inputs.forEach((input, index) => {
        input.value = probs[index];
        // 移除可能存在的错误样式
        input.classList.remove('is-invalid');
    });
    
    // 清除当前题目的所有浮动错误提示
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateScale(container.dataset.questionId);
    }
}

// 生成总和为100的随机整数数组（考虑信效度的优化版本）
function generateBalancedRandomProbs(count, type) {
    if (count === 1) {
        return [100];
    }
    
    let probs = [];
    
    // 根据题型采用不同的概率生成策略
    switch(type) {
        case 'single':
            // 单选题：避免极端分布，确保每个选项都有一定概率
            probs = generateAvoidExtremeDistribution(count);
            break;
        case 'multiple':
            // 多选题：相对均衡，但允许一定差异
            probs = generateBalancedDistribution(count);
            break;
        case 'matrix':
            // 矩阵题：每行内部均衡，避免极端值
            probs = generateAvoidExtremeDistribution(count);
            break;
        case 'scale':
            // 量表题：模拟正态分布，中间选项概率较高
            probs = generateNormalDistribution(count);
            break;
        default:
            // 默认：使用基础随机算法
            probs = generateRandomSumTo100(count);
            break;
    }
    
    return probs;
}

// 避免极端分布的随机概率生成（用于单选题、矩阵题）
function generateAvoidExtremeDistribution(count) {
    if (count === 1) return [100];
    
    // 设定最小概率，避免极端的0或100，提高信效度
    const minProb = 8; // 每个选项至少8%的概率，提高信效度
    const totalMin = minProb * count;
    const remaining = 100 - totalMin;
    
    if (remaining < 0) {
        // 如果选项太多，降低最小概率，但确保至少5%
        const adjustedMin = Math.max(5, Math.floor(100 / count));
        const adjustedRemaining = 100 - adjustedMin * count;
        return generateWithMinProb(count, adjustedMin, adjustedRemaining);
    }
    
    return generateWithMinProb(count, minProb, remaining);
}

// 基于最小概率的随机分布
function generateWithMinProb(count, minProb, remaining) {
    const probs = [];
    
    // 先分配最小概率
    for (let i = 0; i < count; i++) {
        probs.push(minProb);
    }
    
    // 将剩余概率随机分配
    for (let i = 0; i < count - 1; i++) {
        const max = remaining - (count - i - 1);
        const additional = Math.floor(Math.random() * (max + 1));
        probs[i] += additional;
        remaining -= additional;
    }
    probs[count - 1] += remaining;
    
    // 随机打乱数组
    for (let i = probs.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [probs[i], probs[j]] = [probs[j], probs[i]];
    }
    
    return probs;
}

// 均衡分布的随机概率生成（用于多选题）
function generateBalancedDistribution(count) {
    if (count === 1) return [100];
    
    // 多选题：允许一定差异，但避免极端，提高信效度
    const minProb = 12; // 每个选项至少12%的概率
    const maxProb = 60; // 每个选项最多60%的概率
    const probs = [];
    let remaining = 100 - minProb * count;
    
    // 先分配最小概率
    for (let i = 0; i < count; i++) {
        probs.push(minProb);
    }
    
    // 随机分配剩余概率
    for (let i = 0; i < count - 1 && remaining > 0; i++) {
        const maxAdd = Math.min(remaining, maxProb - probs[i]);
        if (maxAdd > 0) {
            const add = Math.floor(Math.random() * (maxAdd + 1));
            probs[i] += add;
            remaining -= add;
        }
    }
    
    // 将剩余概率分配给最后一个选项
    if (remaining > 0) {
        probs[count - 1] += remaining;
        // 确保不超过最大概率
        if (probs[count - 1] > maxProb) {
            let excess = probs[count - 1] - maxProb;
            probs[count - 1] = maxProb;
            // 重新分配超出部分
            for (let i = 0; i < count - 1 && excess > 0; i++) {
                const add = Math.min(excess, maxProb - probs[i]);
                probs[i] += add;
                excess -= add;
            }
        }
    }
    
    // 随机打乱数组
    for (let i = probs.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [probs[i], probs[j]] = [probs[j], probs[i]];
    }
    
    return probs;
}

// 正态分布的随机概率生成（用于量表题）
function generateNormalDistribution(count) {
    if (count === 1) return [100];
    
    // 使用改进的正态分布算法，确保边缘选项也有合理概率
    const minProb = 8; // 每个选项至少8%的概率
    const probs = [];
    let total = 0;
    
    // 生成基于正态分布的权重
    for (let i = 0; i < count; i++) {
        // 计算每个选项到中心的距离
        const center = (count - 1) / 2;
        const distance = Math.abs(i - center);
        
        // 使用正态分布公式计算权重，标准差调整为count/4以获得更集中的分布
        const stdDev = count / 4;
        const weight = Math.exp(-Math.pow(distance, 2) / (2 * Math.pow(stdDev, 2)));
        probs.push(weight);
        total += weight;
    }
    
    // 归一化并确保最小概率
    let sumMin = minProb * count;
    let sumWeights = probs.reduce((a, b) => a + b, 0);
    let normalized = probs.map(p => {
        // 计算基于权重的概率
        let prob = Math.round(p * (100 - sumMin) / sumWeights) + minProb;
        return prob;
    });
    
    // 调整总和为100
    let sum = normalized.reduce((a, b) => a + b, 0);
    let diff = 100 - sum;
    
    // 智能分配差值，优先调整中间选项
    if (diff !== 0) {
        // 计算每个选项到中心的距离，用于确定调整优先级
        const center = (count - 1) / 2;
        const distances = normalized.map((p, i) => Math.abs(i - center));
        
        // 按距离排序，距离越小优先级越高
        const indicesByPriority = distances.map((d, i) => i)
            .sort((a, b) => distances[a] - distances[b]);
        
        // 分配差值
        for (let i = 0; i < indicesByPriority.length && diff !== 0; i++) {
            const index = indicesByPriority[i];
            if (diff > 0) {
                normalized[index]++;
                diff--;
            } else {
                // 确保不低于最小概率
                if (normalized[index] > minProb) {
                    normalized[index]--;
                    diff++;
                }
            }
        }
    }
    
    return normalized;
}

// 生成总和为100的随机整数数组（原始版本，作为后备）
function generateRandomSumTo100(count) {
    if (count === 1) {
        return [100];
    }
    
    const probs = [];
    let remaining = 100;
    
    // 生成前count-1个随机数
    for (let i = 0; i < count - 1; i++) {
        // 每个数的范围为0到remaining之间
        const max = remaining - (count - i - 1);
        const min = 0;
        const prob = Math.floor(Math.random() * (max - min + 1)) + min;
        
        probs.push(prob);
        remaining -= prob;
    }
    
    // 最后一个数是剩余的值
    probs.push(remaining);

    // 随机打乱数组
    for (let i = probs.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [probs[i], probs[j]] = [probs[j], probs[i]];
    }

    return probs;
}

// ===== 进度管理逻辑 =====
let sse = null;
let currentTaskId = null;

function startProgressMonitoring(taskId) {
    currentTaskId = taskId;
    const analysisContent = document.getElementById('analysisContent');
    const brushBtn = document.getElementById('brushBtn');
    const progressPanel = document.getElementById('progressPanel');

    // 隐藏分析内容和刷问卷按钮，进度面板独占页面（类似之前文字展示的效果）
    setVisible(analysisContent, false);
    setVisible(brushBtn, false);
    setVisible(progressPanel, true);

    // 从系统配置获取目标份数（输入框的值）
    const targetCount = parseInt(document.getElementById('targetCount').value);
    document.getElementById('progressTargetCount').textContent = targetCount;

    // 创建SSE连接
    sse = new EventSource(`/api/brush/progress/${taskId}`);

    // 后端发送的是命名事件 "progress"，必须用 addEventListener 接收
    sse.addEventListener('progress', function(event) {
        try {
            const data = JSON.parse(event.data);
            // 更新进度条宽度
            document.getElementById('progressBar').style.width = data.percent + '%';
            // 更新已完成数量
            document.getElementById('completedCount').textContent = data.completed;
            // 更新百分比显示
            document.getElementById('percentDisplay').textContent = data.percent;
            // 更新目标份数（若后端有值则用后端值）
            if (data.total && data.total > 0) {
                document.getElementById('progressTargetCount').textContent = data.total;
            }

            if (data.status === 'COMPLETED') {
                sse.close();
                // 进度条填满
                document.getElementById('progressBar').style.width = '100%';
                document.getElementById('percentDisplay').textContent = '100';
                // 隐藏进度面板
                setVisible('progressPanel', false);
                // 将进度数据映射为完成界面期望的格式
                showCompletedInterface({
                    data: {
                        successCount: data.completed,
                        failedCount: data.failed
                    }
                });
            } else if (data.status === 'FAILED' || data.status === 'STOPPED') {
                sse.close();
                // 保留进度面板显示结束状态，不突然消失
                const title = document.getElementById('progressTitle');
                const bar = document.getElementById('progressBar');
                const stopBtn = document.getElementById('stopButton');
                if (data.status === 'FAILED') {
                    title.innerHTML = '<i class="fa fa-times-circle" aria-hidden="true"></i> 任务失败';
                    title.classList.add('is-failed');
                    bar.classList.remove('is-done');
                    bar.classList.add('is-failed');
                } else {
                    title.innerHTML = '<i class="fa fa-stop-circle" aria-hidden="true"></i> 任务已停止';
                    title.classList.add('is-stopped');
                }
                // 按钮变为"返回配置"
                stopBtn.className = 'btn btn-primary';
                stopBtn.innerHTML = '<i class="fa fa-arrow-left" aria-hidden="true"></i> 返回配置';
                stopBtn.onclick = restoreAfterTaskEnd;
            }
        } catch (e) {
            console.error('解析进度数据失败:', e);
        }
    });

    sse.onerror = function() {
        console.error('SSE连接出错，尝试重新连接...');
    };
}

function stopTask() {
    if (currentTaskId && sse) {
        sse.close();
        fetch(`/api/brush/stop/${currentTaskId}`, {
            method: 'POST'
        }).then(() => {
            // 恢复完整界面：隐藏进度面板，重新显示解析内容和开刷按钮
            setVisible('progressPanel', false);
            setVisible('analysisContent', true);
            setVisible('brushBtn', true);
            showAlert('任务已停止', 'info');
        });
    }
}

// 任务结束（失败/停止）后点击"返回配置"恢复界面
function restoreAfterTaskEnd() {
    setVisible('progressPanel', false);
    setVisible('analysisContent', true);
    setVisible('brushBtn', true);
    // 重置进度面板状态，方便下次任务
    const title = document.getElementById('progressTitle');
    const bar = document.getElementById('progressBar');
    const stopBtn = document.getElementById('stopButton');
    title.innerHTML = '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i> 正在刷问卷';
    title.classList.remove('is-failed', 'is-stopped');
    bar.classList.remove('is-failed', 'is-done');
    bar.style.width = '0%';
    document.getElementById('completedCount').textContent = '0';
    document.getElementById('percentDisplay').textContent = '0';
    stopBtn.className = 'btn btn-danger';
    stopBtn.innerHTML = '<i class="fa fa-stop" aria-hidden="true"></i> 停止任务';
    stopBtn.onclick = stopTask;
}

// 初始化停止按钮
// 统一用 onclick 绑定：任务结束/返回配置时会重新赋值 onclick，
// 若此处改用 addEventListener，两次绑定会叠加，导致 stopTask 被触发两次
const stopButtonEl = document.getElementById('stopButton');
if (stopButtonEl) {
    stopButtonEl.onclick = stopTask;
}