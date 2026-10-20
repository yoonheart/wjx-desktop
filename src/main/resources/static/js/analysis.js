// 分析页面主逻辑

// 当前编辑的题目ID
let currentQuestionId = null;
let currentOptionIndex = null;

// 存储已配置的文本答案（只保存在内存中，刷新后消失）
let textAnswersConfig = {};

// 文本答案的取用模式：configKey -> 'unique'（答案唯一，每份一条不重复）| 'random'（按概率随机）
let textAnswersMode = {};

// 当前弹窗是否处于「答案唯一」模式
let answerUniqueOn = false;

// 将textAnswersConfig暴露到全局作用域，供概率验证器使用
window.textAnswersConfig = textAnswersConfig;
// 模式也要暴露：概率验证器在开刷前要按模式校验条数与份数
window.textAnswersMode = textAnswersMode;

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

// ===== 功能设置记忆（localStorage） =====
const SETTINGS_KEY = 'wjxSettings';

/**
 * 从 localStorage 恢复功能详细设置。
 * 保存的字段：proxySwitch、apiUrl、guiMode、timeControlSwitch、minFillTime、maxFillTime。
 */
function restoreSettings() {
    let settings = null;
    try {
        settings = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
    } catch (e) {
        settings = null;
    }
    if (!settings) return;

    const proxySwitch = document.getElementById('proxySwitch');
    const apiUrl = document.getElementById('apiUrl');
    const guiMode = document.getElementById('guiMode');
    const timeControlSwitch = document.getElementById('timeControlSwitch');
    const minFillTime = document.getElementById('minFillTime');
    const maxFillTime = document.getElementById('maxFillTime');

    if (proxySwitch && typeof settings.proxySwitch === 'boolean') {
        proxySwitch.checked = settings.proxySwitch;
        setVisible(document.getElementById('apiUrlGroup'), proxySwitch.checked);
    }
    if (apiUrl && typeof settings.apiUrl === 'string') {
        apiUrl.value = settings.apiUrl;
    }
    if (guiMode && typeof settings.guiMode === 'boolean') {
        guiMode.checked = settings.guiMode;
    }
    if (timeControlSwitch && typeof settings.timeControlSwitch === 'boolean') {
        timeControlSwitch.checked = settings.timeControlSwitch;
        setVisible(document.getElementById('timeControlGroup'), timeControlSwitch.checked);
    }
    if (minFillTime && settings.minFillTime !== undefined) {
        minFillTime.value = settings.minFillTime;
    }
    if (maxFillTime && settings.maxFillTime !== undefined) {
        maxFillTime.value = settings.maxFillTime;
    }

    // 倍速
    if (settings.speedMultiplier !== undefined) {
        setSpeedMultiplier(settings.speedMultiplier);
    }

    // 提交来源比例（先填值，再按开关决定显隐）
    if (settings.sourceRatio && typeof settings.sourceRatio.mobile === 'number') {
        applySourceValues([
            settings.sourceRatio.mobile,
            settings.sourceRatio.link,
            settings.sourceRatio.wechat
        ]);
    }
    const sourceRatioSwitch = document.getElementById('sourceRatioSwitch');
    if (sourceRatioSwitch && typeof settings.sourceRatioSwitch === 'boolean') {
        sourceRatioSwitch.checked = settings.sourceRatioSwitch;
        setVisible('sourceRatioGroup', sourceRatioSwitch.checked);
    }

    // 信效度优化
    const validityTarget = document.getElementById('validityTarget');
    const validityDimensions = document.getElementById('validityDimensions');
    if (validityTarget && settings.validityTarget !== undefined) {
        validityTarget.value = settings.validityTarget;
    }
    if (validityDimensions && typeof settings.validityDimensions === 'string') {
        validityDimensions.value = settings.validityDimensions;
    }
    const validitySwitch = document.getElementById('validitySwitch');
    if (validitySwitch && typeof settings.validitySwitch === 'boolean') {
        validitySwitch.checked = settings.validitySwitch;
        setVisible('validityGroup', validitySwitch.checked);
        setVisible('validityDimGroup', validitySwitch.checked);
        setVisible('validitySingleGroup', validitySwitch.checked);
        setVisible('validityCorrGroup', validitySwitch.checked);
        setVisible('validityReverseGroup', validitySwitch.checked);
    }
    const validitySingleQuestions = document.getElementById('validitySingleQuestions');
    if (validitySingleQuestions && typeof settings.validitySingleQuestions === 'string') {
        validitySingleQuestions.value = settings.validitySingleQuestions;
    }
    const validityCorrelation = document.getElementById('validityCorrelation');
    if (validityCorrelation && typeof settings.validityCorrelation === 'string') {
        validityCorrelation.value = settings.validityCorrelation;
    }
    const validityReverse = document.getElementById('validityReverse');
    if (validityReverse && typeof settings.validityReverse === 'string') {
        validityReverse.value = settings.validityReverse;
    }

    // 合成答卷：开关 + 各输入框
    const synthSwitch = document.getElementById('synthSwitch');
    if (synthSwitch && typeof settings.synthSwitch === 'boolean') {
        synthSwitch.checked = settings.synthSwitch;
    }
    ['synthOutcomeQ', 'synthPositive', 'synthPredictors', 'synthEffects', 'synthSigLo', 'synthSigHi']
        .forEach(function (id) {
            const el = document.getElementById(id);
            if (el && settings[id] !== undefined) el.value = settings[id];
        });
    applySynthExtras(settings);
    updateSynthUI();
}

// ===== 倍速（同时开几个窗口填）/ 提交来源比例 / 信效度优化的读写 =====

/** 当前选择的倍速（即线程数），默认二倍速 */
function getSpeedMultiplier() {
    const active = document.querySelector('#speedGroup .seg-btn.is-active');
    const value = active ? parseInt(active.getAttribute('data-speed'), 10) : 2;
    return [2, 4, 6].indexOf(value) >= 0 ? value : 2;
}

function setSpeedMultiplier(value) {
    const target = String(value || 2);
    document.querySelectorAll('#speedGroup .seg-btn').forEach(function (btn) {
        btn.classList.toggle('is-active', btn.getAttribute('data-speed') === target);
    });
}

function speedText(value) {
    return { 2: '二倍速', 4: '四倍速', 6: '六倍速' }[value] || '二倍速';
}

// 手机 / 链接 / 微信三项的来源比例控件
const SOURCE_FIELDS = [
    { key: 'mobile', input: 'srcMobile', range: 'srcMobileRange' },
    { key: 'link', input: 'srcLink', range: 'srcLinkRange' },
    { key: 'wechat', input: 'srcWechat', range: 'srcWechatRange' }
];

function getSourceValues() {
    return SOURCE_FIELDS.map(function (field) {
        const el = document.getElementById(field.input);
        let value = el ? parseInt(el.value, 10) : 0;
        if (isNaN(value)) value = 0;
        return Math.min(100, Math.max(0, value));
    });
}

function applySourceValues(values) {
    SOURCE_FIELDS.forEach(function (field, i) {
        const input = document.getElementById(field.input);
        const range = document.getElementById(field.range);
        if (input) input.value = values[i];
        if (range) range.value = values[i];
    });
    updateSourceTotal();
}

function updateSourceTotal() {
    const total = getSourceValues().reduce(function (a, b) { return a + b; }, 0);
    const label = document.getElementById('sourceRatioTotal');
    if (label) {
        label.textContent = '合计 ' + total + '%';
        label.classList.toggle('is-error', total !== 100);
    }
}

/**
 * 改动某一项后，把剩余额度按比例分给另外两项，保证三项合计恒为 100。
 * 这样用户拖动任一滑块都不会把总和拖偏。
 */
function rebalanceSource(changedIndex, newValue) {
    const values = getSourceValues();
    values[changedIndex] = newValue;
    const others = [0, 1, 2].filter(function (i) { return i !== changedIndex; });
    const rest = Math.max(0, 100 - newValue);
    const sumOthers = values[others[0]] + values[others[1]];
    let first = sumOthers <= 0
        ? Math.floor(rest / 2)
        : Math.round(rest * values[others[0]] / sumOthers);
    first = Math.min(rest, Math.max(0, first));
    values[others[0]] = first;
    values[others[1]] = rest - first;
    applySourceValues(values);
}

function getSourceRatio() {
    const values = getSourceValues();
    return { mobile: values[0], link: values[1], wechat: values[2] };
}

/** 信效度优化的参数：目标信度 + 维度分组 + 单选量表题号 + 维度间相关 + 反向题号 */
function getValidityConfig() {
    const targetEl = document.getElementById('validityTarget');
    const dimEl = document.getElementById('validityDimensions');
    const singleEl = document.getElementById('validitySingleQuestions');
    const corrEl = document.getElementById('validityCorrelation');
    const reverseEl = document.getElementById('validityReverse');
    const target = targetEl ? parseFloat(targetEl.value) : 0.8;
    return {
        target: isNaN(target) ? 0.8 : target,
        dimensions: dimEl ? dimEl.value.trim() : '',
        singleQuestions: singleEl ? singleEl.value.trim() : '',
        correlation: corrEl ? corrEl.value.trim() : '',
        reverse: reverseEl ? reverseEl.value.trim() : ''
    };
}

// ===== 信效度：维度分组实时校验 =====
// 目的：用户配置时就能看出「哪些题能分组、有没有填错、每组够不够题」，
// 而不是等刷完问卷、导进 SPSS 才发现效度不达标。

/** 能参与信效度优化的题型（不含手动指定的单选题量表，后者单独处理） */
const VALIDITY_TYPES = ['量表题', '矩阵题'];

/** 解析「量表题号」输入框里的题号集合（支持 22-28、22+23+28、逗号/空格分隔） */
function parseQuestionNumbers(spec) {
    const result = {};
    const text = String(spec || '').replace(/\s*\+\s*/g, ',');
    const groups = text.split(/[,，;；\s]+/).filter(function (g) { return g.trim(); });
    groups.forEach(function (group) {
        const range = group.match(/^(\d+)\s*[-~－]\s*(\d+)$/);
        if (range) {
            let from = parseInt(range[1], 10);
            let to = parseInt(range[2], 10);
            if (from > to) { const tmp = from; from = to; to = tmp; }
            for (let q = from; q <= to; q++) result[q] = true;
        } else if (/^\d+$/.test(group)) {
            result[parseInt(group, 10)] = true;
        }
    });
    return result;
}

/** 问卷里所有能参与信效度优化的题；items = 该题贡献的题项数（矩阵题按子行数算）
 * 额外把「量表题号」里手动指定的单选题也纳入，它们按 1 个题项计。 */
function getValidityCandidates() {
    const list = [];
    const singleSpec = document.getElementById('validitySingleQuestions');
    const manualSingles = parseQuestionNumbers(singleSpec ? singleSpec.value : '');
    (window.__wjxQuestions || []).forEach(function (question) {
        const type = question.题型 || question.type || '';
        const number = Number(question.题号);
        const isManualSingle = (type === '单选题') && manualSingles[number];
        if (VALIDITY_TYPES.indexOf(type) === -1 && !isManualSingle) return;
        let items = 1;
        if (type === '矩阵题') {
            const rows = (question.选项 || []).filter(function (opt) {
                return /^\d+\./.test(String(opt).trim());
            }).length;
            items = rows > 0 ? rows : 1;
        }
        list.push({ number: number, type: type, items: items, manualSingle: isManualSingle });
    });
    return list;
}

/** 解析维度分组文本，规则与后端 _parse_dim_spec 保持一致 */
function parseDimensionSpec(spec) {
    const map = {};
    const text = String(spec || '');
    // 维度之间用逗号/分号分隔；如果一个分隔符都没有但有空格，就把空格当分隔符
    // （兼容老的「22-28 30-32」写法，同时不破坏「22-24+26」这种混写）
    let groups = text.split(/[,，;；]+/).map(function (g) { return g.trim(); })
        .filter(function (g) { return g; });
    if (groups.length === 1 && /\s/.test(groups[0])) {
        groups = groups[0].split(/\s+/).filter(function (g) { return g; });
    }
    groups.forEach(function (group, dimIndex) {
        // 组内用 + 连接若干片段，每段可以是单个题号，也可以是范围（22-24）
        group.split(/[+＋]+/).map(function (t) { return t.trim(); })
            .filter(function (t) { return t; })
            .forEach(function (part) {
                const range = part.match(/^(\d+)\s*[-~－]\s*(\d+)$/);
                if (range) {
                    let from = parseInt(range[1], 10);
                    let to = parseInt(range[2], 10);
                    if (from > to) { const tmp = from; from = to; to = tmp; }
                    for (let q = from; q <= to; q++) map[q] = dimIndex;
                    return;
                }
                if (/^\d+$/.test(part)) map[parseInt(part, 10)] = dimIndex;
            });
    });
    return map;
}

function setValidityHint(el, level, lines) {
    el.className = 'validity-hint is-' + level;
    el.textContent = '';
    lines.forEach(function (line) {
        const row = document.createElement('div');
        row.textContent = line;
        el.appendChild(row);
    });
}

function updateValidityHint() {
    const hintEl = document.getElementById('validityHint');
    const inputEl = document.getElementById('validityDimensions');
    const switchEl = document.getElementById('validitySwitch');
    if (!hintEl || !inputEl) return;

    if (switchEl && !switchEl.checked) {
        hintEl.className = 'validity-hint';
        hintEl.textContent = '';
        return;
    }

    const candidates = getValidityCandidates();
    if (!candidates.length) {
        // 没有量表题/矩阵题时，看看有没有「单选题做的 1~5 打分量表」可以救回来
        const hasSingle = (window.__wjxQuestions || []).some(function (question) {
            return (question.题型 || question.type) === '单选题';
        });
        if (hasSingle) {
            setValidityHint(hintEl, 'warn', [
                '这份问卷没有量表题或矩阵题。如果有些单选题其实是「1~5 打分」这类量表（选项是「非常不同意~非常同意」或纯数字 1~5），',
                '在下方「单选量表题号」里填上它们的题号（例：22-28），就能参与信效度优化。'
            ]);
        } else {
            setValidityHint(hintEl, 'warn', ['这份问卷里没有量表题、矩阵题，也没有单选题，信效度优化不会产生任何效果。']);
        }
        return;
    }

    const byNumber = {};
    candidates.forEach(function (item) { byNumber[item.number] = item; });
    const totalItems = candidates.reduce(function (sum, item) { return sum + item.items; }, 0);
    const usableText = candidates.map(function (item) {
        const label = item.manualSingle ? '单选题（已指定为量表）' : item.type;
        return '第' + item.number + '题（' + label + (item.items > 1 ? '，' + item.items + ' 个子项' : '') + '）';
    }).join('、');

    const spec = inputEl.value.trim();
    if (!spec) {
        setValidityHint(hintEl, 'ok', [
            '自动模式：' + usableText + '。',
            '共 ' + totalItems + ' 个题项会一起优化内部一致性。想分成多个维度，再往上面填分组。'
        ]);
        return;
    }

    const map = parseDimensionSpec(spec);
    const wrongType = [];
    const unknown = [];
    Object.keys(map).forEach(function (key) {
        const number = parseInt(key, 10);
        if (byNumber[number]) return;
        let found = null;
        (window.__wjxQuestions || []).forEach(function (question) {
            if (Number(question.题号) === number) found = question.题型 || question.type || '未知题型';
        });
        if (found) wrongType.push({ number: number, type: found });
        else unknown.push(number);
    });

    const dimItems = {};
    Object.keys(map).forEach(function (key) {
        const item = byNumber[parseInt(key, 10)];
        if (!item) return;
        dimItems[map[key]] = (dimItems[map[key]] || 0) + item.items;
    });
    const dimKeys = Object.keys(dimItems).sort(function (a, b) { return a - b; });
    const missing = candidates.filter(function (item) { return map[item.number] === undefined; });

    const lines = [];
    let level = 'ok';

    if (wrongType.length) {
        level = 'warn';
        lines.push('下面这些题号不是量表题或矩阵题，填了不会生效，请删掉：'
            + wrongType.map(function (w) { return '第' + w.number + '题（' + w.type + '）'; }).join('、') + '。');
    }
    if (unknown.length) {
        level = 'warn';
        lines.push('题号 ' + unknown.join('、') + ' 在这份问卷里不存在，请检查。');
    }

    if (dimKeys.length) {
        const detail = dimKeys.map(function (d) {
            return '第 ' + (Number(d) + 1) + ' 组 ' + dimItems[d] + ' 个题项';
        }).join(' / ');
        lines.push('已分成 ' + dimKeys.length + ' 组：' + detail + '。');
        const tooSmall = dimKeys.filter(function (d) { return dimItems[d] < 3; });
        if (tooSmall.length) {
            level = 'warn';
            lines.push('有 ' + tooSmall.length + ' 个组不足 3 个题项，效度指标（KMO、累积解释率）很难达标，建议每组至少 3~4 个题项。');
        }
    }

    if (missing.length) {
        if (level === 'ok') level = 'warn';
        lines.push('下面这些题没被任何一组覆盖，它们不会参与优化、会按你设的概率单独填：'
            + missing.map(function (m) { return '第' + m.number + '题（' + m.type + '）'; }).join('、') + '。');
    }

    if (!lines.length) lines.push('配置正常。');
    setValidityHint(hintEl, level, lines);
}

/** 「量表题号」输入框的实时校验：提醒用户哪些题号填错了 */
function updateValiditySingleHint() {
    const hintEl = document.getElementById('validitySingleHint');
    const inputEl = document.getElementById('validitySingleQuestions');
    const switchEl = document.getElementById('validitySwitch');
    if (!hintEl || !inputEl) return;

    if (switchEl && !switchEl.checked) {
        hintEl.className = 'validity-hint';
        hintEl.textContent = '';
        return;
    }

    const spec = inputEl.value.trim();
    if (!spec) {
        hintEl.className = 'validity-hint';
        hintEl.textContent = '';
        return;
    }

    const manual = parseQuestionNumbers(spec);
    const lines = [];
    let level = 'ok';
    const nonSingle = [];
    const unknown = [];
    const valid = [];

    Object.keys(manual).forEach(function (key) {
        const number = parseInt(key, 10);
        let found = null;
        (window.__wjxQuestions || []).forEach(function (question) {
            if (Number(question.题号) === number) found = question.题型 || question.type;
        });
        if (!found) { unknown.push(number); return; }
        if (found === '单选题') { valid.push(number); }
        else { nonSingle.push({ number: number, type: found }); }
    });

    if (valid.length) {
        lines.push('已指定 ' + valid.length + ' 道单选题作为量表题，它们会参与信效度优化。');
    }
    if (nonSingle.length) {
        level = 'warn';
        lines.push('这些题号不是单选题，不需要在这里填，请删掉：'
            + nonSingle.map(function (w) { return '第' + w.number + '题（' + w.type + '）'; }).join('、') + '。');
    }
    if (unknown.length) {
        level = 'warn';
        lines.push('题号 ' + unknown.join('、') + ' 在这份问卷里不存在，请检查。');
    }
    if (!lines.length) {
        level = 'warn';
        lines.push('没识别到有效的单选题题号，请检查格式（例：22-28）。');
    }
    setValidityHint(hintEl, level, lines);
}

/** 维度间相关的实时校验：维度数决定要填几个数，填错会直接标红 */
function updateValidityCorrHint() {
    const hintEl = document.getElementById('validityCorrHint');
    const inputEl = document.getElementById('validityCorrelation');
    const dimEl = document.getElementById('validityDimensions');
    const switchEl = document.getElementById('validitySwitch');
    if (!hintEl || !inputEl) return;

    if (switchEl && !switchEl.checked) {
        hintEl.className = 'validity-hint';
        hintEl.textContent = '';
        return;
    }

    const dimMap = parseDimensionSpec(dimEl ? dimEl.value : '');
    const dims = Object.keys(dimMap).reduce(function (set, key) {
        return set.add(dimMap[key]);
    }, new Set()).size;

    if (dims < 2) {
        setValidityHint(hintEl, 'info', ['只有 1 个维度，不需要填（留空即可）']);
        return;
    }

    const need = dims * (dims - 1) / 2;
    const raw = inputEl.value.trim();
    if (!raw) {
        setValidityHint(hintEl, 'info', ['当前 ' + dims + ' 个维度：留空 = 各维度互不相关']);
        return;
    }

    const values = raw.split(';').reduce(function (acc, row) {
        return acc.concat(row.split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s; }));
    }, []);
    const bad = values.filter(function (v) {
        const n = Number(v);
        return !isFinite(n) || Math.abs(n) >= 0.95;
    });
    if (bad.length) {
        setValidityHint(hintEl, 'error', ['只能填数字，且绝对值要小于 0.95（现在有问题的是：' + bad.join('、') + '）']);
        return;
    }
    if (values.length !== need) {
        setValidityHint(hintEl, 'error', ['当前 ' + dims + ' 个维度需要 ' + need + ' 个数值，你填了 ' + values.length + ' 个']);
        return;
    }
    setValidityHint(hintEl, 'ok', [dims + ' 个维度共 ' + need + ' 组相关，填写正确']);
}

/**
 * 收集当前「功能详细设置」面板的全部配置（导出配置 / 写 localStorage 共用）。
 */
function buildFeatureSettings() {
    return {
        proxySwitch: document.getElementById('proxySwitch') ? document.getElementById('proxySwitch').checked : true,
        apiUrl: document.getElementById('apiUrl') ? document.getElementById('apiUrl').value : '',
        guiMode: document.getElementById('guiMode') ? document.getElementById('guiMode').checked : false,
        timeControlSwitch: document.getElementById('timeControlSwitch') ? document.getElementById('timeControlSwitch').checked : false,
        minFillTime: document.getElementById('minFillTime') ? document.getElementById('minFillTime').value : 30,
        maxFillTime: document.getElementById('maxFillTime') ? document.getElementById('maxFillTime').value : 150,
        speedMultiplier: getSpeedMultiplier(),
        sourceRatioSwitch: document.getElementById('sourceRatioSwitch') ? document.getElementById('sourceRatioSwitch').checked : false,
        sourceRatio: getSourceRatio(),
        validitySwitch: document.getElementById('validitySwitch') ? document.getElementById('validitySwitch').checked : false,
        validityTarget: (document.getElementById('validityTarget') || {}).value || '0.80',
        validityDimensions: document.getElementById('validityDimensions') ? document.getElementById('validityDimensions').value : '',
        validitySingleQuestions: document.getElementById('validitySingleQuestions') ? document.getElementById('validitySingleQuestions').value : '',
        validityCorrelation: document.getElementById('validityCorrelation') ? document.getElementById('validityCorrelation').value.trim() : '',
        validityReverse: document.getElementById('validityReverse') ? document.getElementById('validityReverse').value.trim() : '',
        synthSwitch: document.getElementById('synthSwitch') ? document.getElementById('synthSwitch').checked : false,
        synthOutcomeQ: (document.getElementById('synthOutcomeQ') || {}).value || '',
        synthPositive: (document.getElementById('synthPositive') || {}).value || '',
        synthPredictors: (document.getElementById('synthPredictors') || {}).value || '',
        synthEffects: (document.getElementById('synthEffects') || {}).value || '',
        synthSigLo: (document.getElementById('synthSigLo') || {}).value || '2',
        synthSigHi: (document.getElementById('synthSigHi') || {}).value || '4',
        // 这三项界面上没有输入框，只存在内存里；不写进 localStorage 的话，
        // 关掉软件再打开，导入过的 sigItems / kmoMin / constraints 就没了，
        // 此时再导出会给出一份「参数被悄悄降级」的配置。
        synthSigItems: (synthExtras.sigItems || []).join(','),
        synthKmoMin: String(synthExtras.kmoMin || 0.60),
        synthConstraints: JSON.stringify(synthExtras.constraints || []),
        synthSeedSearch: String(synthExtras.seedSearch || 80)
    };
}

/**
 * 把当前功能详细设置写入 localStorage。
 */
function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(buildFeatureSettings()));
}

// 页面加载完成后执行
    document.addEventListener('DOMContentLoaded', function() {
        // 清空文本答案配置，确保每次刷新都是新的开始
        textAnswersConfig = {};
        textAnswersMode = {};
        // 重新赋值后必须同步全局引用：概率验证器读的是 window.textAnswersConfig，
        // 只改局部变量会让它一直看着那个已经被丢弃的空对象（导入后开刷会误报「请配置文本答案」）。
        window.textAnswersConfig = textAnswersConfig;
        window.textAnswersMode = textAnswersMode;

        // ===== 功能设置记忆功能：从 localStorage 恢复上次的设置 =====
        restoreSettings();

        // 代理开关：控制API输入框显隐（默认开启显示）
        const proxySwitch = document.getElementById('proxySwitch');
        const apiUrlGroup = document.getElementById('apiUrlGroup');
        if (proxySwitch && apiUrlGroup) {
            proxySwitch.addEventListener('change', function() {
                setVisible(apiUrlGroup, proxySwitch.checked);
                saveSettings();
            });
        }

        // 时间控制开关：控制填写区间设置显隐（默认关闭）
        const timeControlSwitch = document.getElementById('timeControlSwitch');
        const timeControlGroup = document.getElementById('timeControlGroup');
        if (timeControlSwitch && timeControlGroup) {
            timeControlSwitch.addEventListener('change', function() {
                setVisible(timeControlGroup, timeControlSwitch.checked);
                saveSettings();
            });
        }

        // 倍速：点击切换档位
        const speedGroup = document.getElementById('speedGroup');
        if (speedGroup) {
            speedGroup.addEventListener('click', function(e) {
                const btn = e.target.closest('.seg-btn');
                if (!btn) return;
                setSpeedMultiplier(btn.getAttribute('data-speed'));
                saveSettings();
            });
        }

        // 提交来源比例：开关控制显隐；滑块与数字框双向联动，且合计恒为 100
        const sourceRatioSwitch = document.getElementById('sourceRatioSwitch');
        if (sourceRatioSwitch) {
            sourceRatioSwitch.addEventListener('change', function() {
                setVisible('sourceRatioGroup', sourceRatioSwitch.checked);
                saveSettings();
            });
        }
        SOURCE_FIELDS.forEach(function(field, index) {
            const input = document.getElementById(field.input);
            const range = document.getElementById(field.range);
            if (input) {
                input.addEventListener('change', function() {
                    let value = parseInt(input.value, 10);
                    if (isNaN(value)) value = 0;
                    rebalanceSource(index, Math.min(100, Math.max(0, value)));
                    saveSettings();
                });
            }
            if (range) {
                range.addEventListener('input', function() {
                    rebalanceSource(index, parseInt(range.value, 10) || 0);
                });
                range.addEventListener('change', saveSettings);
            }
        });

        // 信效度优化：开关控制参数区显隐
        const validitySwitch = document.getElementById('validitySwitch');
        if (validitySwitch) {
            validitySwitch.addEventListener('change', function() {
                setVisible('validityGroup', validitySwitch.checked);
                setVisible('validityDimGroup', validitySwitch.checked);
                setVisible('validitySingleGroup', validitySwitch.checked);
                setVisible('validityCorrGroup', validitySwitch.checked);
                setVisible('validityReverseGroup', validitySwitch.checked);
                updateValidityHint();
                updateValiditySingleHint();
                updateValidityCorrHint();
                saveSettings();
            });
        }
        ['validityTarget', 'validityDimensions'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', saveSettings);
                el.addEventListener('input', function() {
                    updateValidityHint();
                    // 维度数变了，维度间相关需要填的个数也跟着变
                    updateValidityCorrHint();
                });
            }
        });
        const validityCorrInput = document.getElementById('validityCorrelation');
        if (validityCorrInput) {
            validityCorrInput.addEventListener('change', saveSettings);
            validityCorrInput.addEventListener('input', updateValidityCorrHint);
        }
        const validityReverseInput = document.getElementById('validityReverse');
        if (validityReverseInput) {
            validityReverseInput.addEventListener('change', saveSettings);
            validityReverseInput.addEventListener('input', saveSettings);
        }
        const validitySingleInput = document.getElementById('validitySingleQuestions');
        if (validitySingleInput) {
            validitySingleInput.addEventListener('change', saveSettings);
            validitySingleInput.addEventListener('input', function() {
                updateValiditySingleHint();
                updateValidityHint();
            });
        }
        updateValidityHint();
        updateValiditySingleHint();
        updateValidityCorrHint();

        // 代理链接、界面模式、时间区间输入框改动时记忆
        ['apiUrl', 'guiMode', 'minFillTime', 'maxFillTime'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', saveSettings);
            }
        });

        // 代理链接一旦输入内容，清除错误高亮
        const apiUrlInput = document.getElementById('apiUrl');
        if (apiUrlInput) {
            apiUrlInput.addEventListener('input', function() {
                if (this.value.trim()) {
                    this.classList.remove('is-error');
                    setVisible('proxyErrorBox', false);
                }
            });
        }

        // 代理引导弹窗：点击"如何获取ip代理"按钮打开
        const proxyHelpBtn = document.getElementById('proxyHelpBtn');
        if (proxyHelpBtn) {
            proxyHelpBtn.addEventListener('click', function() {
                document.getElementById('proxyHelpModal').classList.add('is-open');
            });
        }

        // 详细设置按钮：打开弹窗
        const detailSettingsBtn = document.getElementById('detailSettingsBtn');
        if (detailSettingsBtn) {
            detailSettingsBtn.addEventListener('click', function() {
                document.getElementById('detailSettingsModal').classList.add('is-open');
            });
        }

        // 右下角赞助组件：悬停头像即可展开（CSS 负责），这里补点击切换与关闭
        const donate = document.getElementById('donate');
        if (donate) {
            const donateFab = document.getElementById('donateFab');
            const donateClose = document.getElementById('donateClose');

            // 记录「此刻是不是靠悬停展示的」。桌面端鼠标停在头像上时，
            // 光靠 :hover 就已经是展开态，只看 is-open 判断不出用户想关掉。
            let donateHovering = false;

            function syncDonateAria() {
                const shown = donate.classList.contains('is-open') || donateHovering;
                donateFab.setAttribute('aria-expanded', String(shown));
            }

            // 收起：is-dismissed 用来压住「鼠标还停在圆上」时的悬停展开规则，
            // 否则关完立刻又被 :hover 顶开，看起来像没反应
            function hideDonate() {
                donate.classList.remove('is-open');
                donate.classList.add('is-dismissed');
                donateHovering = false;
                syncDonateAria();
            }

            // 点击头像：已展开（悬停或点开）→ 收起；否则展开
            donateFab.addEventListener('click', function() {
                if (donate.classList.contains('is-open') || donateHovering) {
                    hideDonate();
                } else {
                    donate.classList.remove('is-dismissed');
                    donate.classList.add('is-open');
                    syncDonateAria();
                }
            });

            // 点「×」：收起并记住，之后悬停不再自动弹，只能再点头像打开
            donateClose.addEventListener('click', hideDonate);

            // 点击组件以外区域收起
            document.addEventListener('click', function(e) {
                if (!donate.contains(e.target)) {
                    donate.classList.remove('is-open');
                    donateHovering = false;
                    syncDonateAria();
                }
            });

            // 鼠标进出头像：同步悬停标记与 aria
            donate.addEventListener('mouseenter', function() {
                if (donate.classList.contains('is-dismissed')) {
                    return;
                }
                donateHovering = true;
                syncDonateAria();
            });
            donate.addEventListener('mouseleave', function() {
                donateHovering = false;
                // 离开即解除「本次不再自动展开」，下次悬停恢复正常
                donate.classList.remove('is-dismissed');
                syncDonateAria();
            });
        }

        // 填写区间输入框验证（10 秒 ~ 360 秒，超出取边界值并提醒）
        const minFillTimeInput = document.getElementById('minFillTime');
        const maxFillTimeInput = document.getElementById('maxFillTime');
        const FILL_TIME_MIN = 10;    // 下限：10 秒
        const FILL_TIME_MAX = 360;   // 上限：6 分钟

        // 单个输入框按 10~360 秒区间纠正自身取值
        function clampFillTime(input, fallback) {
            let value = parseInt(input.value);
            if (isNaN(value)) {
                showAlert('请输入有效的数字，填写时间应在 10~360 秒之间（最短 10 秒，最长 6 分钟）', 'error');
                value = fallback;
            } else if (value < FILL_TIME_MIN) {
                showAlert('填写时间最短 10 秒，已自动调整为 10 秒', 'error');
                value = FILL_TIME_MIN;
            } else if (value > FILL_TIME_MAX) {
                showAlert('填写时间最长 360 秒（6 分钟），已自动调整为 360 秒', 'error');
                value = FILL_TIME_MAX;
            }
            input.value = value;
            return value;
        }

        [minFillTimeInput, maxFillTimeInput].forEach(input => {
            if (input) {
                input.addEventListener('change', function() {
                    const isMin = this === minFillTimeInput;
                    // 1. 先保证自身在 10~360 秒之内
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

                // 维度分组的实时校验需要知道每题题型：先把解析结果挂到全局
                window.__wjxQuestions = Array.isArray(questions) ? questions : [];
                // 导入/导出需要知道当前问卷链接（校验配置是否属于同一份问卷）
                window.__wjxSurveyUrl = wjxUrl;
                // 题目结构可能变了：面板里的 synth/validity 参数可能是上一份问卷的
                // （localStorage 会自动恢复上次的值）。先做陈旧检测并重置，
                // 再跑题号校验提示 —— 只出红字不重置的话，导出时陈旧参数会被原样带走。
                resetStaleSynthPanel(questions);
                updateValidityHint();
                // 合成答卷的题号校验同样依赖解析结果，必须在这里重跑一次，
                // 否则会停留在「问卷还没解析完」的旧提示上
                updateSynthHint();
                updateValiditySingleHint();

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
                const supportedTypes = ['单选题', '多选题', '填空题', '矩阵题', '量表题', '排序题', '下拉框'];
                const unsupported = questions.filter(question => {
                    const type = question.题型 || question.type;
                    return !supportedTypes.includes(type);
                });

                if (unsupported.length > 0) {
                    // 存在不支持的题型，显示错误信息
                    const detail = unsupported
                        .map(question => '第' + (question.题号 || '?') + '题（' + (question.题型 || question.type || '未知') + '）')
                        .join('、');
                    analysisContent.innerHTML = '<div class="state-msg is-error">目前系统只支持单选，多选，填空，矩阵，量表，排序，下拉框这七种题型。<br>无法处理的题目：'
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
                // 右下角赞助组件只在「解析之后」出现（含后续的开刷/完成界面）
                setVisible('donate', true);

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

    // 导入 / 导出配置按钮
    const exportConfigBtn = document.getElementById('exportConfigBtn');
    if (exportConfigBtn) {
        exportConfigBtn.addEventListener('click', exportQuestionConfig);
    }
    const importConfigBtn = document.getElementById('importConfigBtn');
    if (importConfigBtn) {
        importConfigBtn.addEventListener('click', importQuestionConfig);
    }
    
    // 开始刷问卷按钮点击事件（添加概率检测）
    const brushBtn = document.getElementById('brushBtn');
    if (brushBtn) {
        brushBtn.addEventListener('click', function() {
            // 执行概率检测
            const result = ProbabilityValidator.validateAll();
            
            
            if (result.isValid) {
                // 概率配置合法，可以继续执行刷问卷操作
                // 获取当前设置的参数
            const targetCount = document.getElementById('targetCount').value;
            const urlParams = new URLSearchParams(window.location.search);
            const url = urlParams.get('url');
            const speedMultiplier = getSpeedMultiplier(); // 倍速 = 同时开几个窗口，默认二倍速
            // 从页面上的设置项直接读取（仅本次使用）
            // 界面模式开关：勾选=弹出浏览器窗口；默认关闭=无界面（后台静默运行）
            const guiMode = document.getElementById('guiMode').checked;
            const headless = !guiMode;
            const useProxy = document.getElementById('proxySwitch').checked; // 默认开启代理
            const ipApiUrl = document.getElementById('apiUrl').value.trim();

            // ===== 代理校验：开启代理但未填链接时，自动打开详细设置并红框提示 =====
            if (useProxy && !ipApiUrl) {
                // 自动打开功能详细设置弹窗
                document.getElementById('detailSettingsModal').classList.add('is-open');
                // 红框高亮输入框
                const apiUrlInput = document.getElementById('apiUrl');
                apiUrlInput.classList.add('is-error');
                // 显示红色提示文字 + 如何获取按钮
                setVisible('proxyErrorBox', true);
                // 滚动/聚焦输入框
                apiUrlInput.focus();
                return;
            }
            // 校验通过后清除错误提示
            document.getElementById('apiUrl').classList.remove('is-error');
            setVisible('proxyErrorBox', false);

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

            // 提交来源比例（默认关闭）：开启时三项必须加起来正好 100%
            const sourceEnabled = document.getElementById('sourceRatioSwitch').checked;
            const sourceRatio = getSourceRatio();
            if (sourceEnabled) {
                const srcTotal = sourceRatio.mobile + sourceRatio.link + sourceRatio.wechat;
                if (srcTotal !== 100) {
                    showAlert('提交来源比例三项之和必须为 100%（当前 ' + srcTotal + '%）', 'warning');
                    document.getElementById('detailSettingsModal').classList.add('is-open');
                    return;
                }
            }

            // 信效度优化（默认关闭）
            const validityEnabled = document.getElementById('validitySwitch').checked;
            const validity = getValidityConfig();
            // 合成答卷：规格直接从面板收集（导入配置只是把它回填到面板）
            const synthSpec = collectSynthSpec();
            if (synthSpec) {
                if (!synthSpec.outcome.question) {
                    showAlert('合成答卷已开启，但没填因变量题号。请在「因变量」里填题号（如 21）再开始。', 'error');
                    return;
                }
                if (!synthSpec.predictors.length) {
                    showAlert('合成答卷已开启，但没有可用的自变量。请填写「自变量」题号，或先解析问卷。', 'error');
                    return;
                }
                if (!Object.keys(synthSpec.effects).length) {
                    showAlert('合成答卷已开启，但「效应强度」是空的，回归会是零显著。至少写一个，如 23:0.30。', 'error');
                    return;
                }
                // 份数下限：α / KMO / 显著项都按 200 份标定过，几十份时会剧烈波动，
                // 与其跑完一轮才发现，不如现在就拦住。
                if (parseInt(targetCount, 10) < 100) {
                    showAlert('目标份数只有 ' + targetCount + ' 份，太少。\n'
                        + '合成答卷的统计目标（α≥0.8、3~4 个显著）是按 200 份标定的，'
                        + '份数不足时结果会大幅波动。\n请把「目标份数」改成 200 再开刷。', 'error');
                    return;
                }
                // 填空题没有答案池 = 每份都提交失败（问卷里文本框基本全是必答），
                // 这个必须在开刷前拦下，不然代理和时间全白耗。
                const fillIds = (window.__wjxQuestions || [])
                    .filter(function (q) { return q.type === '填空题'; })
                    .map(function (q) { return parseInt(q.id, 10); })
                    .filter(function (id) { return Number.isFinite(id); });
                const missingFill = fillIds.filter(function (id) {
                    const cfg = textAnswersConfig[id + '_0'];
                    return !Array.isArray(cfg) || !cfg.length;
                });
                if (missingFill.length) {
                    showAlert('这些填空题还没有答案池：'
                        + missingFill.map(function (q) { return 'Q' + q; }).join('、')
                        + '。\n它们基本都是必答题，不填会让每一份答卷提交失败。\n'
                        + '请点题目旁的「设置答案」配一批，或者导入一份带 textAnswers 的配置。',
                        'error');
                    return;
                }
            }

            // 自定义确认对话框（替代阻塞式 confirm），确认后再真正启动任务
            askConfirm({
                title: '确认开始任务',
                rows: [
                    ['问卷链接', url],
                    ['目标份数', targetCount + ' 份'],
                    ['ip代理', useProxy ? '开启' : '关闭'],
                    ['界面模式', guiMode ? '开启' : '关闭'],
                    ['倍速', speedText(speedMultiplier)],
                    ['时间控制', timeControl ? `${minFillTime} ~ ${maxFillTime} 秒（一个 IP 只填一份）` : '关闭'],
                    ['提交来源', sourceEnabled
                        ? `手机 ${sourceRatio.mobile}% / 链接 ${sourceRatio.link}% / 微信 ${sourceRatio.wechat}%`
                        : '关闭'],
                    ['信效度优化', validityEnabled
                        ? `开启（目标信度 ${validity.target.toFixed(2)}${validity.dimensions ? '，维度 ' + validity.dimensions : '，单维度'}${validity.singleQuestions ? '，量表题号 ' + validity.singleQuestions : ''}）`
                        : '关闭'],
                    ['合成答卷', synthSpec
                        ? `开启（Y=${synthSpec.outcome.label} / Q${synthSpec.outcome.question}，`
                          + `${synthSpec.predictors.length} 个自变量，目标 ${synthSpec.targets.sigTotal[0]}-${synthSpec.targets.sigTotal[1]} 个显著，`
                          + `α≥${synthSpec.targets.alphaMin.toFixed(2)}，KMO≥${synthSpec.targets.kmoMin.toFixed(2)}）`
                        : '关闭']
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
                    maxFillTime: maxFillTime,
                    sourceEnabled: sourceEnabled,
                    sourceRatio: sourceRatio,
                    validityEnabled: validityEnabled,
                    validityTarget: validity.target,
                    validityDimensions: validity.dimensions,
                    validitySingleQuestions: validity.singleQuestions,
                    validityCorrelation: validity.correlation,
                    validityReverse: validity.reverse,
                    // 合成答卷规格（null = 走普通概率模式）
                    synth: synthSpec
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
                // 显示错误信息
                ProbabilityValidator.showErrors(result.errors);
                
                // 定位到第一个有问题的题目或选项
                if (result.errors.length > 0) {
                    // 从第一个错误信息中提取题目ID和可能的选项索引
                    const firstError = result.errors[0];
                    const questionIdMatch = firstError.match(/第(\d+)题/);
                    const optionIndexMatch = firstError.match(/选项(\d+)/);
                    
                    if (questionIdMatch) {
                        const questionId = questionIdMatch[1];
                        const questionElement = document.querySelector(`[data-question-id="${questionId}"]`);
                        
                        if (questionElement) {
                            let targetElement = questionElement;
                            
                            // 如果错误信息中包含选项索引，尝试定位到具体选项
                            if (optionIndexMatch) {
                                const optionIndex = parseInt(optionIndexMatch[1]) - 1; // 转换为0-based索引
                                const optionElements = questionElement.querySelectorAll('.option-item');
                                
                                if (optionElements.length > optionIndex) {
                                    targetElement = optionElements[optionIndex];
                                }
                            }
                            
                            // 滚动到目标元素位置
                            targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            
                            // 添加视觉提示，高亮显示目标元素
                            targetElement.classList.add('is-invalid');
                            
                            // 3秒后移除视觉提示
                            setTimeout(() => {
                                targetElement.classList.remove('is-invalid');
                            }, 3000);
                        }
                    }
                }
            }
        });
    }
});

// 确认返回首页的函数
function confirmBack() {
    // 运行结束后点「返回首页」：直接回到开刷前的配置界面（等价于「返回配置」），
    // 概率、文本答案、功能设置全都还在，不用重新解析问卷。
    const completedInterface = document.getElementById('completedInterface');
    if (completedInterface) {
        completedInterface.remove();
        setVisible('analysisContent', true);
        setVisible('brushBtn', true);
        // 按钮文字恢复成「返回首页」，否则会一直停在「返回配置」上
        const backBtn = document.getElementById('backBtn');
        if (backBtn) {
            backBtn.innerHTML = '<i class="fa fa-arrow-left" aria-hidden="true"></i> 返回首页';
        }
        return;
    }
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
            case '下拉框':
                displayDropdownOptions(questionData, optionsDiv);
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
                    configBtn.dataset.optionQuestionId = question.number;
                    configBtn.dataset.optionIndex = index;
                    configBtn.textContent = optionTextButtonText(question.number, index);
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
                    configBtn.dataset.optionQuestionId = question.number;
                    configBtn.dataset.optionIndex = index;
                    configBtn.textContent = optionTextButtonText(question.number, index);
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

// 显示下拉框题选项
// 问卷星的下拉框是 select2 组件，列表第一项固定是占位项「请选择」（本身不可作为答案），
// 其余才是真正的选项。后端 wjx2.py 的 droplist() 会丢弃首项，只对真正的选项按概率抽取，
// 因此这里同样跳过首项，且把它隐藏、不显示占位提示；显示序号从 1 重新顺延
// （选项1 显示为「1.」），保证前端提交的概率数组与脚本落点时一一对应。
function displayDropdownOptions(question, container) {
    const options = question.options || [];

    // 真正的可选项：从第二项开始（跳过首项占位项）
    options.slice(1).forEach((option, index) => {
        const optionDiv = document.createElement('div');
        optionDiv.className = 'option-item';

        const optionText = document.createElement('div');
        optionText.className = 'option-text';
        // 去掉扫描结果自带的原始序号，按显示顺序从 1 重新编号
        const plainText = String(option).replace(/^\d+\.\s*/, '');
        optionText.textContent = (index + 1) + '. ' + plainText;

        const inputDiv = document.createElement('div');
        inputDiv.style.display = 'flex';
        inputDiv.style.alignItems = 'center';

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
            // 记下题号，导入配置后能批量刷新文案（见 refreshFillBlankButtons）
            configBtn.dataset.fillQuestionId = question.number;
            configBtn.textContent = fillBlankButtonText(question.number);
            configBtn.onclick = () => openTextAnswerModal(question.number, 0);
    
    inputDiv.appendChild(configBtn);
    
    fillBlankDiv.appendChild(inputDiv);
    container.appendChild(fillBlankDiv);
}

/**
 * 填空题按钮文案：配过答案就报条数，没配就明说「未配置」。
 *
 * 原来文案固定是「点击编辑文本答案」，配没配看起来都一样，
 * 导入了一份带答案的配置也不知道到底读进去了几条。
 */
function fillBlankButtonText(questionId) {
    const cfg = textAnswersConfig[questionId + '_0'];
    const n = Array.isArray(cfg) ? cfg.length : 0;
    return n > 0 ? ('编辑文本答案（' + n + ' 条）') : '未配置文本答案';
}

/** 导入配置后刷新所有「答案池」按钮的文案（答案已回填，但按钮还是旧文案）。 */
function refreshFillBlankButtons() {
    refreshAnswerButtons();
}

function refreshAnswerButtons() {
    document.querySelectorAll('[data-fill-question-id]').forEach(function (btn) {
        btn.textContent = fillBlankButtonText(btn.dataset.fillQuestionId);
    });
    document.querySelectorAll('[data-option-question-id]').forEach(function (btn) {
        btn.textContent = optionTextButtonText(btn.dataset.optionQuestionId, btn.dataset.optionIndex);
    });
}

/**
 * 「其他___」选项上的按钮文案。
 *
 * 和独立填空题是两套东西：这里配的是「选了其他之后补充填写」的内容，
 * 存在 optionTextAnswers 里；独立填空题是 textAnswers。两者都容易漏。
 */
function optionTextButtonText(questionId, optionIndex) {
    const cfg = textAnswersConfig[questionId + '_' + optionIndex];
    const n = Array.isArray(cfg) ? cfg.length : 0;
    return n > 0 ? ('文本答案（' + n + ' 条）') : '未配置文本答案';
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
    
    // 恢复该空上次保存的取用模式（默认“按概率随机”）
    answerUniqueOn = textAnswersMode[configKey] === 'unique';

    // 绑定「答案唯一」开关（只绑一次）
    const uniqueSwitch = document.getElementById('answerUniqueSwitch');
    if (uniqueSwitch && !uniqueSwitch.dataset.bound) {
        uniqueSwitch.dataset.bound = '1';
        uniqueSwitch.addEventListener('change', onAnswerUniqueChange);
    }
    
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
            probabilityContainer.className = 'answer-prob-group';
            
            const probabilityInput = document.createElement('input');
            probabilityInput.type = 'number';
            probabilityInput.id = 'answerProb' + index;
            probabilityInput.className = 'probability-input form-control';
            probabilityInput.placeholder = '概率';
            probabilityInput.min = '0';
            probabilityInput.max = '100';
            probabilityInput.value = answer.probability;
            
            const percentageSpan = document.createElement('span');
            percentageSpan.textContent = '%';
            percentageSpan.className = 'mono-label';
            
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
    
    applyAnswerUniqueUI();

    const modal = document.getElementById('textAnswerModal');
    modal.classList.add('is-open');
}

/**
 * 切换「答案唯一」开关（默认关闭）。
 * 开启后：每份问卷只取其中一条答案、互不重复，因此不再需要设置概率——
 * 概率输入框与百分号整组隐藏，顶部说明隐藏（说明已放进问号提示里）。
 */
function onAnswerUniqueChange(event) {
    const el = event && event.target ? event.target : document.getElementById('answerUniqueSwitch');
    answerUniqueOn = !!(el && el.checked);
    applyAnswerUniqueUI();
}

// 把当前模式反映到界面：开关状态、概率框显隐、顶部说明
function applyAnswerUniqueUI() {
    const modal = document.getElementById('textAnswerModal');
    const el = document.getElementById('answerUniqueSwitch');
    const note = document.getElementById('textAnswerNote');

    if (el) {
        el.checked = answerUniqueOn;
    }
    if (modal) {
        modal.classList.toggle('is-unique', answerUniqueOn);
    }
    if (note) {
        // 唯一模式下概率已无意义（输入框整组隐藏），顶部说明一并收起；
        // 相关解释只保留在问号提示里，不在外面重复展示。
        note.classList.toggle('is-hidden', answerUniqueOn);
        if (!answerUniqueOn) {
            note.textContent = '总概率相加须得 100';
        }
    }
    if (answerUniqueOn) {
        fillUniqueProbabilities();
    }
}

// 答案唯一模式下把概率均分为整数且总和恰为 100（下游仍按整数概率解析，
// 脚本在该模式下不按概率抽，这里只是让数据结构保持一致）
function fillUniqueProbabilities() {
    const items = document.querySelectorAll('#answerList .answer-item');
    const n = items.length;
    if (n === 0) return;
    const base = Math.floor(100 / n);
    const remainder = 100 - base * n;
    items.forEach((item, index) => {
        const probInput = item.querySelector('.probability-input');
        if (probInput) {
            probInput.value = String(base + (index < remainder ? 1 : 0));
        }
    });
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
    probabilityContainer.className = 'answer-prob-group';
    
    const probabilityInput = document.createElement('input');
    probabilityInput.type = 'number';
    probabilityInput.id = 'answerProb' + itemCount;
    probabilityInput.className = 'probability-input form-control';
    probabilityInput.placeholder = '概率';
    probabilityInput.min = '0';
    probabilityInput.max = '100';
    
    const percentageSpan = document.createElement('span');
    percentageSpan.textContent = '%';
    percentageSpan.className = 'mono-label';
    
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

    // 答案唯一模式下新增一行：重新均分概率，保证总和恒为 100
    if (answerUniqueOn) {
        fillUniqueProbabilities();
    }
}

// 移除答案项
function removeAnswerItem(btn) {
    const answerList = document.getElementById('answerList');
    const answerItems = answerList.getElementsByClassName('answer-item');
    
    if (answerItems.length > 1) {
        btn.parentElement.remove();
        if (answerUniqueOn) {
            fillUniqueProbabilities();
        }
    } else {
        showAlert('至少需要保留一个文本答案', 'warning');
    }
}

// 保存文本答案
function saveTextAnswers() {
    const answerList = document.getElementById('answerList');
    const answerItems = answerList.getElementsByClassName('answer-item');
    
    // 「答案唯一」模式：每份问卷用一条、互不重复，因此不校验概率（概率由程序均分），
    // 改为校验「已填条数 === 目标份数」——多了不行，少了也不行。
    if (answerUniqueOn) {
        saveUniqueTextAnswers(answerItems);
        return;
    }
    
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
            const probability = ProbabilityValidator.parseProbability(probabilityValue);
            if (probability === null) {
                errors.push(`第${i + 1}个文本答案的概率不是有效数字`);
                probabilityInput.classList.add('is-invalid');
            } else if (probability < 0 || probability > 100) {
                errors.push(`第${i + 1}个文本答案的概率必须在0-100之间`);
                probabilityInput.classList.add('is-invalid');
            }
        }
        
        // 如果当前项验证通过，添加到答案列表
        const parsedProbability = ProbabilityValidator.parseProbability(probabilityValue);
        if (text !== '' && probabilityValue !== '' && parsedProbability !== null) {
            answers.push({
                text: text,
                probability: parsedProbability
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
    textAnswersMode[configKey] = 'random';
    
    // 同时更新全局变量，确保概率验证器能够读取到最新配置
    window.textAnswersConfig = textAnswersConfig;
    window.textAnswersMode = textAnswersMode;
    
    
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

/**
 * 「答案唯一」模式的保存：不校验概率，校验条数与目标份数一致。
 * 概率由程序均分为整数且总和恰为 100（脚本在该模式下不按概率抽，仅保持数据结构一致）。
 */
function saveUniqueTextAnswers(answerItems) {
    const texts = [];
    const errors = [];

    for (let i = 0; i < answerItems.length; i++) {
        const textInput = answerItems[i].getElementsByClassName('answer-text')[0];
        textInput.classList.remove('is-invalid');
        const text = textInput.value.trim();
        if (text === '') {
            errors.push(`第${i + 1}条答案不能为空`);
            textInput.classList.add('is-invalid');
        }
        texts.push(text);
    }

    // 目标份数取当前界面上的设置
    const targetInput = document.getElementById('targetCount');
    const targetCount = targetInput ? (parseInt(targetInput.value, 10) || 0) : 0;
    const filledCount = texts.filter(t => t !== '').length;

    if (filledCount !== targetCount) {
        errors.push(`答案唯一：已填 ${filledCount} 条答案，目标份数 ${targetCount} 份，两者必须一致（多了不行，少了也不行）`);
    }

    if (errors.length > 0) {
        if (window.ProbabilityValidator) {
            window.ProbabilityValidator.showErrors(errors);
        } else {
            showAlert(errors.join('\n'), 'error');
        }
        return;
    }

    // 概率均分：先取整，余数分给前几条，保证总和恰为 100
    const n = texts.length;
    const base = Math.floor(100 / n);
    const remainder = 100 - base * n;
    const answers = texts.map((text, index) => ({
        text: text,
        probability: base + (index < remainder ? 1 : 0)
    }));

    const configKey = `${currentQuestionId}_${currentOptionIndex}`;
    textAnswersConfig[configKey] = answers;
    textAnswersMode[configKey] = 'unique';

    window.textAnswersConfig = textAnswersConfig;
    window.textAnswersMode = textAnswersMode;

    showAlert(`已保存 ${n} 条答案（答案唯一）`, 'success');

    closeAndResetTextAnswerModal();
}

// 保存后关闭弹窗并复位表单；同时清掉该题的题目级错误提示
function closeAndResetTextAnswerModal() {
    const modal = document.getElementById('textAnswerModal');
    const answerList = document.getElementById('answerList');

    if (modal) {
        modal.classList.remove('is-open');
    }
    if (answerList) {
        answerList.innerHTML = '';
    }

    answerUniqueOn = false;
    applyAnswerUniqueUI();
    addAnswerItem();

    const questionContainer = document.querySelector(`[data-question-id="${currentQuestionId}"]`);
    if (questionContainer) {
        const errorId = questionContainer.dataset.errorId;
        if (errorId) {
            const floatError = document.getElementById(errorId);
            if (floatError) {
                floatError.remove();
            }
            questionContainer.removeAttribute('data-error-id');
        }
    }
}

// 收集所有题目配置
function collectQuestionsConfig() {
    const config = [];
    const questionContainers = document.querySelectorAll('.question-container');
    
    questionContainers.forEach(container => {
        const questionType = container.querySelector('.question-type').textContent;
        const questionId = container.dataset.questionId;
        // 题干一起导出：文件交给别人（或 AI）填概率时，得知道这道题问的是什么
        const titleEl = container.querySelector('.question-content');
        const questionConfig = {
            id: questionId,
            type: questionType,
            title: titleEl ? titleEl.textContent.trim() : '',
            options: []
        };

        // 根据题型收集不同的配置
        switch(questionType) {
            case '单选题':
            case '多选题':
            case '下拉框':
                const options = container.querySelectorAll('.option-item');
                options.forEach((option, index) => {
                    const probInput = option.querySelector('.probability-input');
                    const textEl = option.querySelector('.option-text');
                    if (probInput) {
                        questionConfig.options.push({
                            index: index,
                            text: textEl ? textEl.textContent.trim() : '',
                            probability: parseInt(probInput.value) || 0
                        });
                    }
                    
                    // 检查是否有文本答案配置（针对带文本输入的选项）
                    const configKey = `${questionId}_${index}`;
                    if (textAnswersConfig[configKey]) {
                        // 为每个选项单独存储文本答案配置
                        if (!questionConfig.optionTextAnswers) {
                            questionConfig.optionTextAnswers = {};
                            // 每个选项各自的取用模式：unique（答案唯一）/ random（按概率随机）
                            questionConfig.optionTextAnswersMode = {};
                        }
                        questionConfig.optionTextAnswers[index] = textAnswersConfig[configKey];
                        questionConfig.optionTextAnswersMode[index] =
                            textAnswersMode[configKey] || 'random';
                    }
                });
                break;
            case '填空题':
                // 填空题直接使用文本答案配置
                const fillKey = `${questionId}_0`;
                if (textAnswersConfig[fillKey]) {
                    questionConfig.textAnswers = textAnswersConfig[fillKey];
                    questionConfig.textAnswersMode = textAnswersMode[fillKey] || 'random';
                }
                break;
            case '矩阵题':
                const rows = container.querySelectorAll('.matrix-table tbody tr');
                rows.forEach((row, rowIndex) => {
                    const labelCell = row.querySelector('td');
                    const rowConfig = {
                        rowIndex: rowIndex,
                        rowText: labelCell ? labelCell.textContent.trim() : '',
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
                        text: String(index + 1),
                        probability: parseInt(input.value) || 0
                    });
                });
                break;
        }
        
        config.push(questionConfig);
    });

    return config;
}

/** 合成答卷面板的事件绑定（开关联动、实时提示、随改随存） */
function initSynthControls() {
    const switchEl = document.getElementById('synthSwitch');
    if (!switchEl) return;

    switchEl.addEventListener('change', function () {
        updateSynthUI();
        updateSynthHint();
        saveSettings();
    });

    ['synthOutcomeQ', 'synthPositive', 'synthPredictors', 'synthEffects', 'synthSigLo', 'synthSigHi']
        .forEach(function (id) {
            const el = document.getElementById(id);
            if (!el) return;
            el.addEventListener('input', function () { updateSynthHint(); });
            el.addEventListener('change', function () { saveSettings(); });
        });

    updateSynthUI();
    updateSynthHint();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSynthControls);
} else {
    initSynthControls();
}

// ===== 问卷配置导入 / 导出 =====
// 导出：每道题的概率/文本答案配置 + 功能详细设置 → json 文件（文件名默认为问卷码）。
// 导入：选一个 json 文件，回填到界面。文件对话框由后端（桌面模式）弹出。

/** 从问卷链接提取问卷码：https://v.wjx.cn/vm/YJc8nO1.aspx → YJc8nO1 */
function extractSurveyCode(url) {
    const matched = String(url || '').match(/\/vm\/([A-Za-z0-9]+)/);
    return matched ? matched[1] : null;
}

/** 当前正在解析的问卷链接（解析入口写入，供导出校验与命名） */
function getCurrentSurveyUrl() {
    return window.__wjxSurveyUrl || '';
}

/** 导出配置：收集数据 → 后端弹「另存为」→ 写盘 */
async function exportQuestionConfig() {
    const questions = collectQuestionsConfig();
    if (!questions.length) {
        showAlert('还没有解析出题目，无法导出配置。请先解析问卷。', 'error');
        return;
    }
    const url = getCurrentSurveyUrl();
    const code = extractSurveyCode(url);
    const targetInput = document.getElementById('targetCount');
    // 合成答卷规格：面板上已经配好的直接带走（发给 AI 微调即可），
    // 没配过就生成草案，把需要判断的字段登记到 _toFill
    const collected = collectSynthSpec();
    let synthForExport = collected || buildSynthSpec(questions);
    // 兜底：解析时的重置没覆盖到（用户在解析后又改了面板、或回调出了异常），
    // 导出前再查一次。核心参数引用的题号不存在 → 整份按陈旧处理，退回干净草案；
    // 只有 constraints 带幽灵题号 → 保留其余，只丢掉那几条规则。
    let staleNote = '';
    if (collected) {
        const missing = synthMissingRefs(collected, questions);
        if (missing.length) {
            synthForExport = buildSynthSpec(questions);
            staleNote = '\n\n注意：面板里的合成参数引用了当前问卷不存在的题号（'
                + missing.map(function (q) { return 'Q' + q; }).join('、')
                + '），已按空白草案导出，避免把上一份问卷的参数带进这份文件。';
        } else {
            const kept = filterSynthConstraints(collected, questions);
            if (kept.length !== (collected.constraints || []).length) {
                synthForExport.constraints = kept;
            }
        }
    }

    // 不管走哪条分支，都把「哪些题还没填答案」写进文件。
    // 走 collectSynthSpec 时它不知道这些信息，AI 就看不到缺口清单。
    if (!synthForExport._emptyAnswers) {
        const emptyScan = scanEmptyAnswers(questions);
        synthForExport._emptyAnswers = {
            probabilityAllZero: emptyScan.noProb,
            textAnswersEmpty: emptyScan.noText,
            '_说明': '第一项列出概率全为 0 的题（会用默认分布兜底，描述统计会失真）；'
                + '第二项列出没有答案池的填空题（提交时会被必答拦住）。空数组表示都已配好。'
        };
    }

    const targetCountForExport = targetInput
        ? Math.min(1000, Math.max(1, parseInt(targetInput.value, 10) || 1))
        : 1;

    const payload = {
        version: 2,
        // 用途标记：这个文件产出的是模拟答卷，不是调研数据。
        // 写在最外层而不是只写在说明里，是为了让任何读文件的人/AI 一眼看见。
        synthetic: true,
        dataKind: 'simulated',
        _prompt: buildAiPrompt(questions, synthForExport, targetCountForExport),
        _guide: buildConfigGuide(),
        url: url,
        targetCount: targetCountForExport,
        exportedAt: new Date().toISOString(),
        settings: buildFeatureSettings(),
        synth: synthForExport,
        questions: questions
    };
    try {
        const result = await api.config.exportConfig({
            suggestedName: (code || 'wjx-config') + '.json',
            data: payload
        });
        const path = (result && result.data && result.data.path) || '';
        // 合成规格里需要人/AI 判断的字段，导出时明确列出来，免得对方只改了概率
        const pending = payload.synth && Array.isArray(payload.synth._toFill) ? payload.synth._toFill : [];
        let message = '配置已导出：' + path;
        if (pending.length) {
            message += '\n\n文件里的 synth 段还需要填：\n'
                + pending.map(function (x) { return '· ' + x; }).join('\n')
                + '\n\n把这个文件交给 AI，说明目标（例如「3 个显著、α≥0.8」），让它填完再导入。';
        }
        message += staleNote;
        showAlert(message, 'success', 30000);
    } catch (error) {
        // request.js 对业务失败（code !== 1）会 reject，完整响应挂在 error.data
        const message = (error && error.data && error.data.msg) || (error && error.message) || '导出失败';
        if (!/取消/.test(message)) showAlert(message, 'error');
    }
}

/** 把导出文件里的「功能详细设置」回填到界面 */
function applyFeatureSettings(settings) {
    if (!settings || typeof settings !== 'object') return;
    const fakeEventSettings = { ...settings };
    // 直接复用 restoreSettings 的字段回填逻辑：把 settings 对象交给它
    const proxySwitch = document.getElementById('proxySwitch');
    if (proxySwitch && typeof fakeEventSettings.proxySwitch === 'boolean') {
        proxySwitch.checked = fakeEventSettings.proxySwitch;
        setVisible(document.getElementById('apiUrlGroup'), proxySwitch.checked);
    }
    const apiUrl = document.getElementById('apiUrl');
    if (apiUrl && typeof fakeEventSettings.apiUrl === 'string') {
        apiUrl.value = fakeEventSettings.apiUrl;
    }
    const guiMode = document.getElementById('guiMode');
    if (guiMode && typeof fakeEventSettings.guiMode === 'boolean') {
        guiMode.checked = fakeEventSettings.guiMode;
    }
    const timeControlSwitch = document.getElementById('timeControlSwitch');
    if (timeControlSwitch && typeof fakeEventSettings.timeControlSwitch === 'boolean') {
        timeControlSwitch.checked = fakeEventSettings.timeControlSwitch;
        setVisible(document.getElementById('timeControlGroup'), timeControlSwitch.checked);
    }
    const minFillTime = document.getElementById('minFillTime');
    if (minFillTime && fakeEventSettings.minFillTime !== undefined) {
        minFillTime.value = fakeEventSettings.minFillTime;
    }
    const maxFillTime = document.getElementById('maxFillTime');
    if (maxFillTime && fakeEventSettings.maxFillTime !== undefined) {
        maxFillTime.value = fakeEventSettings.maxFillTime;
    }
    if (fakeEventSettings.speedMultiplier !== undefined) {
        setSpeedMultiplier(fakeEventSettings.speedMultiplier);
    }
    if (fakeEventSettings.sourceRatio && typeof fakeEventSettings.sourceRatio.mobile === 'number') {
        applySourceValues([
            fakeEventSettings.sourceRatio.mobile,
            fakeEventSettings.sourceRatio.link,
            fakeEventSettings.sourceRatio.wechat
        ]);
    }
    const sourceRatioSwitch = document.getElementById('sourceRatioSwitch');
    if (sourceRatioSwitch && typeof fakeEventSettings.sourceRatioSwitch === 'boolean') {
        sourceRatioSwitch.checked = fakeEventSettings.sourceRatioSwitch;
        setVisible('sourceRatioGroup', sourceRatioSwitch.checked);
    }
    const validitySwitch = document.getElementById('validitySwitch');
    if (validitySwitch && typeof fakeEventSettings.validitySwitch === 'boolean') {
        validitySwitch.checked = fakeEventSettings.validitySwitch;
        setVisible('validityGroup', validitySwitch.checked);
        setVisible('validityDimGroup', validitySwitch.checked);
        setVisible('validitySingleGroup', validitySwitch.checked);
        setVisible('validityCorrGroup', validitySwitch.checked);
        setVisible('validityReverseGroup', validitySwitch.checked);
    }
    const validityTarget = document.getElementById('validityTarget');
    if (validityTarget && fakeEventSettings.validityTarget !== undefined) {
        validityTarget.value = fakeEventSettings.validityTarget;
    }
    const validityDimensions = document.getElementById('validityDimensions');
    if (validityDimensions && typeof fakeEventSettings.validityDimensions === 'string') {
        validityDimensions.value = fakeEventSettings.validityDimensions;
    }
    const validitySingleQuestions = document.getElementById('validitySingleQuestions');
    if (validitySingleQuestions && typeof fakeEventSettings.validitySingleQuestions === 'string') {
        validitySingleQuestions.value = fakeEventSettings.validitySingleQuestions;
    }
    const validityCorrelation = document.getElementById('validityCorrelation');
    if (validityCorrelation && typeof fakeEventSettings.validityCorrelation === 'string') {
        validityCorrelation.value = fakeEventSettings.validityCorrelation;
    }
    const validityReverse = document.getElementById('validityReverse');
    if (validityReverse && typeof fakeEventSettings.validityReverse === 'string') {
        validityReverse.value = fakeEventSettings.validityReverse;
    }
    // 合成答卷：导入的 settings 里若带这些字段也一并回填
    // （随后 applySynthSpec 会用 synth 段覆盖一次，以 synth 段为准）
    const synthSwitchEl = document.getElementById('synthSwitch');
    if (synthSwitchEl && typeof fakeEventSettings.synthSwitch === 'boolean') {
        synthSwitchEl.checked = fakeEventSettings.synthSwitch;
    }
    ['synthOutcomeQ', 'synthPositive', 'synthPredictors', 'synthEffects', 'synthSigLo', 'synthSigHi']
        .forEach(function (id) {
            const el = document.getElementById(id);
            if (el && fakeEventSettings[id] !== undefined) el.value = fakeEventSettings[id];
        });
    applySynthExtras(fakeEventSettings);
    updateSynthUI();

    updateValidityHint();
    updateValiditySingleHint();
    saveSettings();
}

/** 把导出文件里的「每道题配置」回填到界面（按题号匹配） */
function applyQuestionsConfig(questions) {
    if (!Array.isArray(questions) || !questions.length) return;
    let applied = 0;
    const containers = document.querySelectorAll('.question-container');
    containers.forEach(container => {
        const questionId = container.dataset.questionId;
        const saved = questions.find(q => String(q.id) === String(questionId));
        if (!saved) return;

        // 单选 / 多选 / 下拉框：按选项顺序填概率，并恢复文本答案配置
        if (['单选题', '多选题', '下拉框'].indexOf(saved.type) !== -1) {
            const options = container.querySelectorAll('.option-item');
            (saved.options || []).forEach(opt => {
                const optionEl = options[opt.index];
                const input = optionEl ? optionEl.querySelector('.probability-input') : null;
                if (input && typeof opt.probability === 'number') {
                    input.value = opt.probability;
                }
            });
            if (saved.optionTextAnswers) {
                Object.keys(saved.optionTextAnswers).forEach(index => {
                    // 归一化成 [{text, probability}]：纯字符串数组也能导入
                    const normalized = normalizeTextAnswers(saved.optionTextAnswers[index]);
                    if (!normalized) return;
                    textAnswersConfig[`${questionId}_${index}`] = normalized;
                    textAnswersMode[`${questionId}_${index}`] =
                        (saved.optionTextAnswersMode || {})[index] || 'random';
                });
            }
            applied++;
            return;
        }
        // 填空题：恢复文本答案
        if (saved.type === '填空题' && saved.textAnswers) {
            const normalized = normalizeTextAnswers(saved.textAnswers);
            if (normalized) {
                textAnswersConfig[`${questionId}_0`] = normalized;
                textAnswersMode[`${questionId}_0`] = saved.textAnswersMode || 'random';
                applied++;
            }
            return;
        }
        // 矩阵题：按行恢复每格概率
        if (saved.type === '矩阵题') {
            const rows = container.querySelectorAll('.matrix-table tbody tr');
            (saved.options || []).forEach(rowConfig => {
                const row = rows[rowConfig.rowIndex];
                if (!row) return;
                const cells = row.querySelectorAll('td:not(:first-child)');
                (rowConfig.options || []).forEach(cellConfig => {
                    const cell = cells[cellConfig.colIndex];
                    const input = cell ? cell.querySelector('.probability-input') : null;
                    if (input && typeof cellConfig.probability === 'number') {
                        input.value = cellConfig.probability;
                    }
                });
            });
            applied++;
            return;
        }
        // 量表题：按顺序恢复概率
        if (saved.type === '量表题') {
            const inputs = container.querySelectorAll('.probability-input');
            (saved.options || []).forEach(opt => {
                const input = inputs[opt.index];
                if (input && typeof opt.probability === 'number') {
                    input.value = opt.probability;
                }
            });
            applied++;
        }
    });
    // 导入是直接写 textAnswersConfig 的，概率验证器读的是 window 上的引用，
    // 这里必须同步一次，否则开刷前会误报「第X题（填空题）请配置文本答案」。
    window.textAnswersConfig = textAnswersConfig;
    window.textAnswersMode = textAnswersMode;
    return applied;
}

/** 导入配置：后端弹「打开文件」→ 读出 JSON → 问卷一致才回填 */
/**
 * 校验并装载「合成答卷规格」。
 *
 * 只做结构校验和归一化，不做统计计算（那是 Python 侧的事）。
 * 校验不通过时不阻断题目配置的导入，只把 synth 关掉并给出可读的原因 ——
 * 用户可能只是想把这份文件当成普通概率配置来用。
 *
 * @param {Object} spec 导出文件里的 synth 段
 * @returns {{ok: boolean, enabled: boolean, warnings: string[], summary: string}}
 */
function applySynthSpec(spec) {
    window.__wjxSynthSpec = null;
    const result = { ok: false, enabled: false, warnings: [], summary: '' };
    if (!spec || typeof spec !== 'object') return result;

    const toNum = function (v) {
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    };

    const outcome = spec.outcome || {};
    const qid = toNum(outcome.question);
    // positiveOption 允许数组：三档因变量可以把前两档合并成 1（如 Q21 的 [0,1]）
    const rawPos = outcome.positiveOption;
    const posList = (Array.isArray(rawPos) ? rawPos : [rawPos])
        .map(toNum).filter(function (v) { return v !== null; });
    const predictors = Array.isArray(spec.predictors)
        ? spec.predictors.map(toNum).filter(function (v) { return v !== null; })
        : [];

    // ---- 题号白名单：不存在的题号必须报出来，不能静默吞掉 ----
    // 引擎对查不到的题号是「跳过」，日志里只说「自变量 N 个」，
    // 于是「说好 8 个变量」会悄悄变成 4 个，跑出来的显著项自然对不上。
    // 这里从已渲染的题目容器取合法题号；问卷还没解析时跳过（避免误报）。
    const idNodes = document.querySelectorAll('.question-container[data-question-id]');
    const validIds = new Set();
    idNodes.forEach(function (n) {
        const v = toNum(n.dataset.questionId);
        if (v !== null) validIds.add(v);
    });
    // 注意：dimensions 的校验放在下面 dims 声明之后（变量顺序不能倒）
    const checkIds = function (list, label) {
        if (!validIds.size) return;           // 没解析问卷，无从校验
        const bad = list.filter(function (v) {
            if (validIds.has(v)) return false;
            // 矩阵行伪键：题号*100+行号，宿主题必须存在
            if (v >= 100 && validIds.has(Math.floor(v / 100))) return false;
            return true;
        });
        if (bad.length) {
            result.warnings.push(label + ' 里的 ' + bad.join('、')
                + ' 在问卷里不存在，这些变量会被引擎忽略'
                + (bad.some(function (v) { return v >= 100; })
                    ? '（大于 100 的编号是「矩阵行」写法，只有当对应题确实是矩阵题时才有效）' : ''));
        }
    };
    checkIds(predictors, 'predictors');

    // effects：键统一成题号字符串，值必须是有限数字
    const effects = {};
    const rawEffects = (spec.effects && typeof spec.effects === 'object') ? spec.effects : {};
    Object.keys(rawEffects).forEach(function (key) {
        const k = toNum(key);
        const v = toNum(rawEffects[key]);
        if (k === null) {
            result.warnings.push('effects 里的键「' + key + '」不是题号，已忽略');
            return;
        }
        if (v === null) {
            result.warnings.push('effects["' + key + '"] 不是数字，已忽略');
            return;
        }
        if (predictors.length && predictors.indexOf(k) < 0) {
            result.warnings.push('effects 里的第 ' + k + ' 题不在 predictors 中，已忽略');
            return;
        }
        effects[String(k)] = v;
    });

    // 维度允许两种写法：[{name, items:[22,23]}] 或简写 [[22,23],[25,27]]。
    // 以前只认前者，AI 写成简写时 items 取不到 → 整组被静默过滤掉，α 就没了。
    const dims = Array.isArray(spec.dimensions)
        ? spec.dimensions.map(function (d, i) {
            let items = [];
            if (Array.isArray(d)) {
                items = d.map(toNum).filter(function (v) { return v !== null; });
            } else if (d && Array.isArray(d.items)) {
                items = d.items.map(toNum).filter(function (v) { return v !== null; });
            }
            return { name: (d && !Array.isArray(d) && d.name) || ('维度' + (i + 1)), items: items };
        }).filter(function (d) { return d.items.length >= 2; })
        : [];
    checkIds(dims.reduce(function (acc, d) {
        return acc.concat(d.items);
    }, []), 'dimensions');

    const targets = spec.targets || {};
    let sigLo = toNum(Array.isArray(targets.sigTotal) ? targets.sigTotal[0] : null);
    let sigHi = toNum(Array.isArray(targets.sigTotal) ? targets.sigTotal[1] : null);
    if (sigLo === null) sigLo = 2;
    if (sigHi === null) sigHi = Math.max(sigLo, 4);
    if (sigLo > sigHi) { const t = sigLo; sigLo = sigHi; sigHi = t; }

    const alphaMin = toNum(targets.alphaMin);
    const kmoMin = toNum(targets.kmoMin);

    // ---- 硬性校验：缺了这些没法生成 ----
    if (!spec.enabled) {
        result.warnings.push('synth.enabled = false，本次按普通概率模式刷题');
        return result;
    }
    if (qid === null) {
        result.warnings.push('synth.outcome.question 没填：不知道哪道题当因变量，合成模式无法启用');
        return result;
    }
    if (!posList.length) {
        result.warnings.push('synth.outcome.positiveOption 没填：无法确定哪个选项记为 1');
        return result;
    }
    if (predictors.length && predictors.indexOf(qid) >= 0) {
        result.warnings.push('因变量 Q' + qid + ' 同时出现在 predictors 里，已从自变量中移除');
        const idx = predictors.indexOf(qid);
        predictors.splice(idx, 1);
    }
    if (!predictors.length) {
        result.warnings.push('predictors 是空的，没有自变量可进回归');
        return result;
    }
    if (!Object.keys(effects).length) {
        result.warnings.push('synth.effects 是空的：所有变量都会被当作无效应，回归大概率零显著');
    }
    if (sigHi > Math.floor(predictors.length * 0.6)) {
        result.warnings.push('targets.sigTotal 上限 ' + sigHi + ' 相对自变量个数偏多，可能搜不到解');
    }

    const normalized = {
        enabled: true,
        outcome: {
            question: qid,
            positiveOption: posList,
            label: String(outcome.label || ('Q' + qid))
        },
        predictors: predictors,
        dimensions: dims,
        dimCorr: Array.isArray(spec.dimCorr)
            ? spec.dimCorr.map(toNum).filter(function (v) { return v !== null; })
            : [],
        targets: {
            alphaMin: alphaMin === null ? 0.75 : alphaMin,
            kmoMin: kmoMin === null ? 0.60 : kmoMin,
            sigTotal: [sigLo, sigHi],
            sigItems: Array.isArray(targets.sigItems)
                ? targets.sigItems.map(toNum).filter(function (v) { return v !== null; })
                    .filter(function (v) { return !validIds.size || validIds.has(v)
                        || (v >= 100 && validIds.has(Math.floor(v / 100))); })
                : []
        },
        effects: effects,
        constraints: Array.isArray(spec.constraints) ? spec.constraints : [],
        seedSearch: Math.min(300, Math.max(10, toNum(spec.seedSearch) || 80))
    };

    window.__wjxSynthSpec = normalized;
    result.ok = true;
    result.enabled = true;
    result.summary = 'Y=' + normalized.outcome.label + '（Q' + qid + '，选项 '
        + posList.map(function (v) { return v + 1; }).join('/') + ' 记为 1）'
        + '，自变量 ' + predictors.length + ' 个，'
        + '目标 ' + sigLo + '-' + sigHi + ' 个显著，'
        + 'α≥' + normalized.targets.alphaMin + '，KMO≥' + normalized.targets.kmoMin;
    // 回填到界面：用户可以直接在面板上继续改，不用再碰 JSON
    applySynthToUI(normalized);
    return result;
}

// ===== 合成答卷：界面配置 ↔ 生成规格 =====
// 界面能表达的（因变量、自变量、效应、目标显著数）走输入框；
// 界面表达不了的（一致性规则 constraints、指定显著项）在导入时存进 synthExtras，
// 用户继续在界面上编辑时原样保留，不会被覆盖掉。

let synthExtras = { constraints: [], sigItems: [], seedSearch: 80, alphaMin: 0.75, kmoMin: 0.60 };

/** 解析 "0,1,2" 这种索引列表（允许中文逗号、空格、分号） */
function parseIndexList(text) {
    return String(text || '').split(/[,，;；\s]+/).filter(Boolean)
        .map(function (s) {
            const n = Number(s);
            return Number.isFinite(n) ? n : null;
        })
        .filter(function (v) { return v !== null && v >= 0; });
}

/** 解析 "23:0.30, 25:0.58" -> {"23": 0.30, "25": 0.58} */
function parseEffectList(text) {
    const out = {};
    String(text || '').split(/[,，;；\s]+/).filter(Boolean).forEach(function (seg) {
        const matched = seg.match(/^(\d+)\s*[:：=]\s*(-?\d*\.?\d+)$/);
        if (!matched) return;
        out[String(parseInt(matched[1], 10))] = parseFloat(matched[2]);
    });
    return out;
}

function formatEffectList(effects) {
    return Object.keys(effects || {})
        .sort(function (a, b) { return Number(a) - Number(b); })
        .map(function (k) { return k + ':' + effects[k]; })
        .join(', ');
}

/** 自动推断候选自变量：全部量表题 + 单选题 + 下拉框 */
function autoSynthPredictors() {
    const out = [];
    document.querySelectorAll('.question-container').forEach(function (c) {
        const typeEl = c.querySelector('.question-type');
        const type = typeEl ? typeEl.textContent.trim() : '';
        if (type === '量表题' || type === '单选题' || type === '下拉框') {
            const id = parseInt(c.dataset.questionId, 10);
            if (Number.isFinite(id) && out.indexOf(id) < 0) out.push(id);
        }
    });
    return out;
}

/** 从界面收集合成规格；开关没开返回 null */
function collectSynthSpec() {
    const sw = document.getElementById('synthSwitch');
    if (!sw || !sw.checked) return null;

    const qEl = document.getElementById('synthOutcomeQ');
    const qid = qEl ? parseInt(qEl.value, 10) : NaN;

    let pos = parseIndexList((document.getElementById('synthPositive') || {}).value);
    if (!pos.length) pos = [0];

    // 维度沿用上方信效度面板的分组；没填就按「单选量表题号」整体当一维
    let groups = parseDimensionGroups((document.getElementById('validityDimensions') || {}).value || '');
    if (!groups.length) {
        const singles = parseQuestionNumbers((document.getElementById('validitySingleQuestions') || {}).value || '');
        if (singles.length >= 2) groups = [singles];
    }
    const dimensions = groups
        .filter(function (g) { return g.length >= 2; })
        .map(function (g, i) { return { name: '维度' + (i + 1), items: g }; });

    let predictors = parseIndexList((document.getElementById('synthPredictors') || {}).value);
    if (!predictors.length) predictors = autoSynthPredictors();

    const lo = parseInt((document.getElementById('synthSigLo') || {}).value, 10);
    const hi = parseInt((document.getElementById('synthSigHi') || {}).value, 10);

    // 因变量题干（只取前 24 字，用于日志里认人）
    let label = 'Q' + (Number.isFinite(qid) ? qid : '?');
    if (Number.isFinite(qid) && qid > 0) {
        const titleEl = document.querySelector('.question-container[data-question-id="' + qid + '"] .question-content');
        if (titleEl) {
            // DOM 里的题干带着「21、」「【单选题】」这些前缀，存进配置前先清掉，
            // 否则日志和 JSON 里全是噪音
            label = titleEl.textContent.trim()
                .replace(/^\s*\d+\s*[、.．]\s*/, '')
                .replace(/【[^】]*】/g, '')
                .replace(/[（(][^）)]*[）)]/g, '')
                .replace(/\s+/g, ' ')
                .trim()
                .slice(0, 24);
        }
    }

    const alphaTarget = parseFloat((document.getElementById('validityTarget') || {}).value) || 0.80;

    return {
        enabled: true,
        outcome: {
            question: Number.isFinite(qid) && qid > 0 ? qid : null,
            positiveOption: pos,
            label: label
        },
        predictors: predictors,
        dimensions: dimensions,
        dimCorr: parseIndexList((document.getElementById('validityCorrelation') || {}).value)
            .map(function (v) { return v; }),
        targets: {
            // 界面上的「目标信度」是想要的结果，验收下限留一点余量
            alphaMin: Math.max(0.60, Math.round((alphaTarget - 0.05) * 100) / 100),
            kmoMin: synthExtras.kmoMin || 0.60,
            sigTotal: [
                Number.isFinite(lo) ? lo : 2,
                Number.isFinite(hi) ? hi : 4
            ],
            sigItems: synthExtras.sigItems || []
        },
        effects: parseEffectList((document.getElementById('synthEffects') || {}).value),
        constraints: synthExtras.constraints || [],
        seedSearch: synthExtras.seedSearch || 80
    };
}

/** 把规格回填到界面（导入配置时调用） */
/**
 * 还原「界面上没有输入框」的合成答卷参数。
 *
 * sigItems / kmoMin / constraints / seedSearch 没有对应的输入框，
 * 只能靠导入或 localStorage 带回来。不还原的话，重新导出会得到一份
 * 「参数被悄悄降级」的配置（alphaMin 0.80 → 0.75、sigItems 清空之类）。
 */
function applySynthExtras(s) {
    if (!s || typeof s !== 'object') return;

    const raw = s.synthSigItems;
    if (Array.isArray(raw)) {
        synthExtras.sigItems = raw.map(Number).filter(function (v) { return Number.isFinite(v); });
    } else if (typeof raw === 'string' && raw.trim()) {
        synthExtras.sigItems = raw.split(/[,，;；\s]+/)
            .map(function (v) { return parseInt(v, 10); })
            .filter(function (v) { return Number.isFinite(v); });
    }

    const k = parseFloat(s.synthKmoMin);
    if (Number.isFinite(k)) synthExtras.kmoMin = Math.min(0.95, Math.max(0.30, k));

    const seed = parseInt(s.synthSeedSearch, 10);
    if (Number.isFinite(seed)) synthExtras.seedSearch = seed;

    if (typeof s.synthConstraints === 'string' && s.synthConstraints.trim()) {
        try {
            const c = JSON.parse(s.synthConstraints);
            if (Array.isArray(c)) synthExtras.constraints = c;
        } catch (e) {
            // 存坏了就保持原值，不因为一行脏数据把整份配置搞挂
        }
    }
}

function applySynthToUI(spec) {
    const sw = document.getElementById('synthSwitch');
    if (!sw) return;
    sw.checked = !!spec.enabled;

    const setValue = function (id, val) {
        const el = document.getElementById(id);
        if (el) el.value = val == null ? '' : val;
    };

    const outcome = spec.outcome || {};
    setValue('synthOutcomeQ', outcome.question);
    const pos = outcome.positiveOption;
    setValue('synthPositive', Array.isArray(pos) ? pos.join(',') : pos);

    setValue('synthPredictors', (spec.predictors || []).join(','));
    setValue('synthEffects', formatEffectList(spec.effects));

    const targets = spec.targets || {};
    if (Array.isArray(targets.sigTotal)) {
        setValue('synthSigLo', targets.sigTotal[0]);
        setValue('synthSigHi', targets.sigTotal[1]);
    }

    // 界面表达不了的部分存起来，后续编辑不丢
    synthExtras.constraints = Array.isArray(spec.constraints) ? spec.constraints : [];
    synthExtras.sigItems = Array.isArray(targets.sigItems) ? targets.sigItems : [];
    synthExtras.seedSearch = spec.seedSearch || 80;
    synthExtras.kmoMin = targets.kmoMin || 0.60;
    synthExtras.alphaMin = targets.alphaMin || 0.75;

    // 反向同步到「目标信度」输入框。
    // collectSynthSpec 是从这个框推 alphaMin 的（alphaTarget - 0.05），
    // 不回填的话「导入 → 再导出」会把 alphaMin 从 0.80 悄悄掉回 0.75。
    if (Number.isFinite(targets.alphaMin)) {
        const t = Math.min(0.90, Math.max(0.60, Math.round((targets.alphaMin + 0.05) * 100) / 100));
        setValue('validityTarget', t.toFixed(2));
    }

    updateSynthUI();
    updateSynthHint();
}

/** 开关联动：显示/隐藏下面的输入项 */
function updateSynthUI() {
    const sw = document.getElementById('synthSwitch');
    const on = !!(sw && sw.checked);
    ['synthGroup', 'synthOutcomeGroup', 'synthPredictorGroup', 'synthEffectGroup']
        .forEach(function (id) { setVisible(id, on); });
}

/**
 * 查一道题：先查已解析的题目列表，查不到再退回 DOM。
 *
 * 只用 DOM 查询会误报 —— 若分析页尚未渲染完、或题目被分页渲染，
 * 明明存在的题会被判成「找不到第 N 题」。window.__wjxQuestions 是解析结果的源头，
 * 用它判断更可靠。
 */
function lookupParsedQuestion(qid) {
    const target = parseInt(String(qid), 10);
    if (!Number.isFinite(target)) return null;

    // 解析结果里的字段可能叫 题号 / id / number，逐个兼容
    const list = window.__wjxQuestions || [];
    for (let i = 0; i < list.length; i++) {
        const q = list[i] || {};
        const raw = q.题号 !== undefined ? q.题号
            : (q.id !== undefined ? q.id : q.number);
        if (parseInt(String(raw), 10) !== target) continue;
        const opts = q.选项 || q.options || [];
        const type = String(q.题型 || q.type || '').trim();
        return { type: type, optionCount: Array.isArray(opts) ? opts.length : 0 };
    }

    // DOM 兜底：不靠属性选择器，逐个解析属性值，容忍前后缀和空格
    const nodes = document.querySelectorAll('.question-container[data-question-id]');
    for (let i = 0; i < nodes.length; i++) {
        if (parseInt(String(nodes[i].dataset.questionId), 10) !== target) continue;
        const typeEl = nodes[i].querySelector('.question-type');
        return {
            type: typeEl ? typeEl.textContent.trim() : '',
            optionCount: nodes[i].querySelectorAll('.option-item').length
        };
    }
    return null;
}

/**
 * 题目索引是否ready。
 *
 * 「一道题都找不到」和「这一题不存在」是两件事：前者说明问卷还没解析完，
 * 后者才是题号写错了。不区分的话，用户会看到一片「这些题号不存在」的误报。
 */
function questionIndexReady() {
    if ((window.__wjxQuestions || []).length) return true;
    return document.querySelectorAll('.question-container').length > 0;
}

/** 题号是否存在（矩阵的「题号×100+行号」也算存在，只要宿主题在） */
function isKnownQuestionId(qid) {
    const id = Number(qid);
    if (!Number.isFinite(id)) return false;
    if (lookupParsedQuestion(id)) return true;
    return id >= 100 && !!lookupParsedQuestion(Math.floor(id / 100));
}

/** 实时提示：因变量题号对不对、自变量几个、效应写了几个 */
function updateSynthHint() {
    const sw = document.getElementById('synthSwitch');
    if (!sw || !sw.checked) return;

    const qid = parseInt((document.getElementById('synthOutcomeQ') || {}).value, 10);
    const outcomeHint = document.getElementById('synthOutcomeHint');
    if (outcomeHint) {
        if (!Number.isFinite(qid) || qid <= 0) {
            outcomeHint.textContent = '还没填因变量题号';
            outcomeHint.className = 'validity-hint is-warn';
        } else {
            const found = lookupParsedQuestion(qid);
            if (!questionIndexReady()) {
                outcomeHint.textContent = '问卷还没解析完，题号暂时无法校验';
                outcomeHint.className = 'validity-hint';
            } else if (!found) {
                outcomeHint.textContent = '找不到第 ' + qid + ' 题';
                outcomeHint.className = 'validity-hint is-warn';
            } else {
                const type = found.type;
                const n = found.optionCount;
                if (type !== '单选题' && type !== '量表题' && type !== '下拉框') {
                    outcomeHint.textContent = '因变量必须是单选题，第 ' + qid + ' 题是' + type;
                    outcomeHint.className = 'validity-hint is-warn';
                } else {
                    outcomeHint.textContent = '共 ' + n + ' 个选项';
                    outcomeHint.className = 'validity-hint';
                }
            }
        }
    }

    const preds = parseIndexList((document.getElementById('synthPredictors') || {}).value);
    const predHint = document.getElementById('synthPredictorHint');
    if (predHint) {
        const count = preds.length || autoSynthPredictors().length;
        // 题号存在性：填了别的问卷的题号时，引擎会静默忽略，
        // 「8 个变量」会悄悄变少，所以界面上必须先点出来。
        const ready = questionIndexReady();
        const unknownPreds = ready
            ? preds.filter(function (v) { return !isKnownQuestionId(v); })
            : [];
        if (!ready && preds.length) {
            predHint.textContent = '共 ' + preds.length + ' 个自变量（问卷未解析，暂不校验题号）';
            predHint.className = 'validity-hint';
        } else if (unknownPreds.length) {
            predHint.textContent = '这些题号在这份问卷里不存在：'
                + unknownPreds.map(function (v) { return 'Q' + v; }).join('、')
                + '（会被忽略，等于少几个自变量）';
            predHint.className = 'validity-hint is-warn';
        } else if (count > 15) {
            predHint.textContent = '共 ' + count + ' 个（超过 15 个不容易收敛，建议精简）';
            predHint.className = 'validity-hint is-warn';
        } else {
            predHint.textContent = '共 ' + count + ' 个自变量';
            predHint.className = 'validity-hint';
        }
    }

    const effects = parseEffectList((document.getElementById('synthEffects') || {}).value);
    const effectHint = document.getElementById('synthEffectHint');
    if (effectHint) {
        const keys = Object.keys(effects);
        if (!keys.length) {
            effectHint.textContent = '没写效应 = 所有变量都按无关处理，回归大概率零显著';
            effectHint.className = 'validity-hint is-warn';
        } else {
            effectHint.textContent = '已给 ' + keys.length + ' 个变量设定效应';
            effectHint.className = 'validity-hint';
        }
    }
}

/**
 * 检测面板里的合成/信效度参数是不是「另一份问卷」留下的。
 *
 * 背景：synth / validity 面板的值会存进 localStorage，打开软件时自动恢复。
 * 用户换一份问卷时（比如从 28 题的就业问卷换成 7 题的游戏问卷），页面重新加载、
 * 面板原样恢复上一份的参数 —— 因变量 21、自变量 9/11/23、维度 22-28，
 * 在新问卷里一个都不存在。此时直接导出，这些幽灵题号会被写进 json，
 * 连 _prompt 里的「验收指标」都会被带偏，AI 收到必然出错。
 * （2026-10-20：PmxGvvu.json 实测整个 synth 段是 Q4wUhCD.json 的复制。）
 *
 * 判据（满足任一即判定陈旧）：
 *   1. 合成开关开着，因变量题号填了，但当前问卷没有这一题；
 *   2. 合成开关开着，自变量填了，但当前问卷里一道都不存在；
 *   3. 信效度开关开着，维度分组填了，但里面所有题号都不存在。
 * 同一份问卷重新解析 / 刷新页面时题号都还在，不会误伤；
 * 只填错个别题号的情况交给 updateSynthHint 的红字和导出时的过滤，不整体重置。
 */
function detectStaleSynthPanel() {
    if (!questionIndexReady()) return null;

    const synthSw = document.getElementById('synthSwitch');
    const validitySw = document.getElementById('validitySwitch');

    const outcomeQ = parseInt((document.getElementById('synthOutcomeQ') || {}).value, 10);
    if (synthSw && synthSw.checked && Number.isFinite(outcomeQ) && outcomeQ > 0
            && !isKnownQuestionId(outcomeQ)) {
        return '因变量 Q' + outcomeQ + ' 在当前问卷里不存在';
    }

    const preds = parseIndexList((document.getElementById('synthPredictors') || {}).value);
    if (synthSw && synthSw.checked && preds.length
            && preds.every(function (v) { return !isKnownQuestionId(v); })) {
        return '自变量 ' + preds.join('、') + ' 在当前问卷里都不存在';
    }

    const dimText = (document.getElementById('validityDimensions') || {}).value || '';
    const dimIds = parseDimensionGroups(dimText)
        .reduce(function (acc, g) { return acc.concat(g); }, []);
    if (validitySw && validitySw.checked && dimIds.length
            && dimIds.every(function (v) { return !isKnownQuestionId(v); })) {
        return '维度分组 ' + dimIds.join('、') + ' 在当前问卷里都不存在';
    }
    return null;
}

/**
 * 陈旧就重置：清空 synth 面板、synthExtras 和维度分组，
 * 并把干净状态写回 localStorage —— 不写回的话，下次打开软件
 * restoreSettings 又会把陈旧值恢复出来，形成死循环。
 * validityTarget / validityCorrelation 是通用偏好，保留。
 */
function resetStaleSynthPanel(questions) {
    const reason = detectStaleSynthPanel();
    if (!reason) return null;

    const sw = document.getElementById('synthSwitch');
    if (sw) sw.checked = false;
    ['synthOutcomeQ', 'synthPositive', 'synthPredictors', 'synthEffects']
        .forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
    const lo = document.getElementById('synthSigLo');
    const hi = document.getElementById('synthSigHi');
    if (lo) lo.value = '2';
    if (hi) hi.value = '4';
    // 界面上没有输入框的三项也要重置，否则导出时又会跟着面板一起出去
    synthExtras.constraints = [];
    synthExtras.sigItems = [];
    synthExtras.seedSearch = 80;
    synthExtras.alphaMin = 0.75;
    synthExtras.kmoMin = 0.60;
    // 维度分组 / 单选量表题号同样可能属于上一份问卷
    ['validityDimensions', 'validitySingleQuestions', 'validityReverse'].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });

    updateSynthUI();
    saveSettings();
    updateSynthHint();
    updateValidityHint();
    updateValiditySingleHint();

    const total = Array.isArray(questions) ? questions.length : 0;
    showAlert('检测到面板里的合成参数属于另一份问卷（' + reason + '），已自动清空。'
        + '\n当前问卷共 ' + total + ' 题。现在可以重新配置，'
        + '或直接导出空白草案发给 AI 填写。', 'warning', 15000);
    return reason;
}

/**
 * 列出 synth 规格里「当前问卷中不存在」的题号（矩阵行伪键只要宿主题在就算存在）。
 * 导出前的最后一道闸：面板在解析后被手动改坏、或重置没跑到的边角情况，在这里兜底。
 */
function synthMissingRefs(spec, questions) {
    const list = Array.isArray(questions) ? questions : (window.__wjxQuestions || []);
    const known = {};
    list.forEach(function (q) {
        const raw = q && (q.id !== undefined ? q.id : (q.题号 !== undefined ? q.题号 : q.number));
        const n = parseInt(String(raw), 10);
        if (Number.isFinite(n)) known[n] = true;
    });
    const missing = [];
    const push = function (v) {
        const n = Number(v);
        if (!Number.isFinite(n) || n <= 0 || missing.indexOf(n) >= 0) return;
        if (known[n]) return;
        if (n >= 100 && known[Math.floor(n / 100)]) return;
        missing.push(n);
    };
    const s = spec || {};
    push((s.outcome || {}).question);
    (s.predictors || []).forEach(push);
    (s.dimensions || []).forEach(function (d) {
        const items = Array.isArray(d) ? d : (d && d.items) || [];
        items.forEach(push);
    });
    Object.keys(s.effects || {}).forEach(push);
    ((s.targets || {}).sigItems || []).forEach(push);
    return missing;
}

/** 丢掉引用了不存在题号的 constraints（核心参数陈旧时整份都不用，见导出处的处理） */
function filterSynthConstraints(spec, questions) {
    const list = Array.isArray(questions) ? questions : (window.__wjxQuestions || []);
    const known = {};
    list.forEach(function (q) {
        const raw = q && (q.id !== undefined ? q.id : (q.题号 !== undefined ? q.题号 : q.number));
        const n = parseInt(String(raw), 10);
        if (Number.isFinite(n)) known[n] = true;
    });
    const ok = function (v) {
        const n = Number(v);
        if (!Number.isFinite(n) || n <= 0) return false;
        if (known[n]) return true;
        return n >= 100 && known[Math.floor(n / 100)];
    };
    return (spec.constraints || []).filter(function (c) {
        const when = Object.keys((c && c.when) || {});
        const then = Object.keys((c && c.then) || {});
        return when.concat(then).every(ok);
    });
}

async function importQuestionConfig() {
    let data;
    try {
        const result = await api.config.importConfig();
        data = result && result.data;
    } catch (error) {
        const message = (error && error.data && error.data.msg) || (error && error.message) || '导入失败';
        if (!/取消/.test(message)) showAlert(message, 'error');
        return;
    }
    if (!data || !Array.isArray(data.questions)) {
        showAlert('文件格式不对：这不是本工具导出的配置文件。', 'error');
        return;
    }

    // 整段回填放在 try 里：任何一步抛异常，原来会静默中断 ——
    // 既没有提示、也没有任何反馈，用户只能看到「点了打开但什么都没发生」。
    try {
    const savedUrl = String(data.url || '');
    const currentUrl = getCurrentSurveyUrl();
    const sameSurvey = savedUrl && currentUrl
        && extractSurveyCode(savedUrl) === extractSurveyCode(currentUrl);
    if (!sameSurvey) {
        const ok = await askConfirm({
            title: '问卷不一致',
            rows: [
                ['配置来自', savedUrl || '未知问卷'],
                ['当前问卷', currentUrl || '未知问卷']
            ],
            message: '这份配置是给另一份问卷用的，题号对不上可能会填出错误配置。确定仍然导入吗？',
            okText: '仍然导入'
        });
        if (!ok) return;
    }

    // 目标份数最先回填：它排在任何可能抛异常的步骤之前，
    // 否则中途一出错就会停在界面的默认值 1 份 —— 1 份跑什么都不可能有统计结果。
    const targetInput = document.getElementById('targetCount');
    let restoredCount = 0;
    if (targetInput && Number.isFinite(Number(data.targetCount)) && Number(data.targetCount) > 0) {
        restoredCount = Math.min(1000, Math.max(1, parseInt(data.targetCount, 10)));
        targetInput.value = restoredCount;
        targetInput.dispatchEvent(new Event('change'));
    }

    applyFeatureSettings(data.settings);
    const applied = applyQuestionsConfig(data.questions);
    // 合成答卷规格：校验通过就挂到全局，开刷时随请求发给后端
    const synthResult = applySynthSpec(data.synth);

    // 导入成功只报「导入成功」，细节不再铺开；
    // 但有一类情况必须拦下来说：问卷有填空题却一条答案池都没恢复，
    // 用户会以为「文本答案没导入」，实际是配置里缺 textAnswers。
    refreshAnswerButtons();
    const importWarnings = [];
    const hasFill = (data.questions || []).some(function (q) { return q.type === '填空题'; });
    const fillRestored = (data.questions || []).filter(function (q) {
        const cfg = textAnswersConfig[q.id + '_0'];
        return q.type === '填空题' && Array.isArray(cfg) && cfg.length > 0;
    });
    if (hasFill && !fillRestored.length) {
        importWarnings.push('问卷里有填空题，但没有恢复任何答案池——'
            + '可能是配置文件里没有 textAnswers，也可能是问卷还没解析出来（界面未就绪时无法回填）。'
            + '请确认已解析问卷后重新导入。');
    }

    if (synthResult.enabled) {
        // 导入时再扫一遍空答案：AI 有可能只改了 synth、把概率和填空题漏了，
        // 这种情况不拦住（合成模式能跑），但必须让用户知道描述统计会失真。
        const empty = scanEmptyAnswers(data.questions);
        if (empty.noProb.length) {
            importWarnings.push('有 ' + empty.noProb.length + ' 题概率全为 0（'
                + empty.noProb.map(function (q) { return 'Q' + q; }).join('、')
                + '），将用「中间高两边低」的默认分布兜底：α/KMO/显著性不受影响，'
                + '但这些题在 SPSS 里的分布会失真（如学历变成硕士最多）。要让数据像真人填的，请补全。');
        }
        if (empty.noText.length) {
            importWarnings.push('有 ' + empty.noText.length + ' 道填空题没有答案池（'
                + empty.noText.map(function (q) { return 'Q' + q; }).join('、')
                + '），提交时会被问卷必答题拦住，务必补全 textAnswers。');
        }
    }
    importWarnings.push.apply(importWarnings, synthResult.warnings);

    // 正常导入只报「导入成功」，几秒自动消失（鼠标放上去会暂停倒计时）；
    // 有警告（概率缺失/答案池缺失等）才升级为 warning 并多留一会儿让人看清。
    if (importWarnings.length) {
        showAlert('导入成功，但有几点注意：\n'
            + importWarnings.map(function (w) { return '· ' + w; }).join('\n'),
            'warning', 15000);
    } else {
        showAlert('导入成功', 'success', 4000);
    }
    } catch (error) {
        console.error('导入配置失败:', error);
        showAlert('导入时出错：' + ((error && error.message) || error)
            + '\n配置可能只回填了一部分，请先别开刷，把这条提示发出来排查。', 'error', 30000);
    }
}

/**
 * 归一化一组文本答案。
 * 配置文件（尤其是手工改过的 JSON）里可能写成 ["同意","有道理"] 这种纯字符串，
 * 而弹窗/脚本要的是 [{text:"同意", probability:25}, ...] 且概率合计恰好 100。
 * 这里统一成对象形态：缺概率的按比例补齐，合计不等于 100 的按最大余数法缩放到 100。
 * @param {Array} raw 原始数组（字符串或对象混合都接受）
 * @returns {Array|null} 归一化后的数组；没有有效答案时返回 null
 */
function normalizeTextAnswers(raw) {
    if (!Array.isArray(raw) || !raw.length) return null;

    const items = raw.map(function (a) {
        if (a && typeof a === 'object') {
            const text = a.text != null ? a.text
                : (a.value != null ? a.value
                : (a.content != null ? a.content : ''));
            const p = Number(a.probability);
            return { text: String(text), probability: Number.isFinite(p) && p > 0 ? p : 0 };
        }
        return { text: String(a == null ? '' : a), probability: 0 };
    }).filter(function (a) { return a.text !== ''; });

    if (!items.length) return null;

    const total = items.reduce(function (s, a) { return s + a.probability; }, 0);
    if (total <= 0) {
        // 全都没写概率：平均分配，并把余数补在第一项上，保证整数且合计 100
        const base = Math.floor(100 / items.length);
        items.forEach(function (a) { a.probability = base; });
        items[0].probability += 100 - base * items.length;
        return items;
    }
    if (total === 100) return items;

    // 有概率但合计不是 100：按比例缩放，最大余数法保证结果为整数且合计恰为 100
    const scaled = items.map(function (a) { return a.probability * 100 / total; });
    const floored = scaled.map(Math.floor);
    let rest = 100 - floored.reduce(function (s, n) { return s + n; }, 0);
    const order = scaled.map(function (v, i) { return { i: i, frac: v - Math.floor(v) }; })
        .sort(function (x, y) { return y.frac - x.frac; });
    for (let k = 0; k < order.length && rest > 0; k++, rest--) {
        floored[order[k].i] += 1;
    }
    items.forEach(function (a, i) { a.probability = floored[i]; });
    return items;
}

/**
 * 把「维度分组」文本框解析成二维数组： ["22-24,25+27,26-28"] -> [[22,23,24],[25,27],[26,28]]
 */
function parseDimensionGroups(spec) {
    const groups = [];
    String(spec || '').split(/[,，;；\s]+/).filter(Boolean).forEach(function (seg) {
        const range = seg.match(/^(\d+)\s*[-~－]\s*(\d+)$/);
        if (range) {
            let start = parseInt(range[1], 10), end = parseInt(range[2], 10);
            if (start > end) { const t = start; start = end; end = t; }
            const list = [];
            for (let q = start; q <= end; q++) list.push(q);
            if (list.length) groups.push(list);
            return;
        }
        if (/^\d+(\s*\+\s*\d+)+$/.test(seg)) {
            groups.push(seg.split(/\s*\+\s*/).map(function (n) { return parseInt(n, 10); }));
            return;
        }
        if (/^\d+$/.test(seg)) groups.push([parseInt(seg, 10)]);
    });
    return groups;
}

/**
 * 扫出「还没填答案」的题：概率全 0 的选择题、没有答案池的填空题。
 *
 * 这两类不补齐，导入时会被概率校验拦住、或者提交时被问卷必答拦住，
 * 但界面上不容易一眼看出来。导出时写进 json 让 AI 知道该填哪些，
 * 导入时再扫一遍，防止 AI 只改了 synth 忘了改概率。
 */
function scanEmptyAnswers(questions) {
    const noProb = [];
    const noText = [];
    (questions || []).forEach(function (q) {
        const qid = parseInt(q.id, 10);
        if (!Number.isFinite(qid)) return;
        if (q.type === '填空题') {
            const ta = q.textAnswers || [];
            const sum = ta.reduce(function (s, x) { return s + (Number(x.probability) || 0); }, 0);
            if (!ta.length || sum <= 0) noText.push(qid);
            return;
        }
        const opts = q.options || [];
        if (!opts.length) return;   // 排序题等本来就不需要概率
        let ok = false;
        if (q.type === '矩阵题') {
            // 矩阵按行算：任意一行合计 <= 0 就算没填
            const badRow = opts.some(function (row) {
                return (row.options || []).reduce(function (s, o) {
                    return s + (Number(o.probability) || 0);
                }, 0) <= 0;
            });
            ok = !badRow;
        } else {
            ok = opts.reduce(function (s, o) { return s + (Number(o.probability) || 0); }, 0) > 0;
        }
        if (!ok) noProb.push(qid);
    });
    return { noProb: noProb, noText: noText };
}

/**
 * 生成「合成答卷」草案（synth 段）。
 *
 * 软件只负责能自动推断的部分（哪些题是量表、维度怎么分、候选自变量），
 * 需要判断的字段一律留 null 并登记到 _toFill，交给 AI 或用户填。
 */
function buildSynthSpec(questions) {
    const synthSwitch = document.getElementById('synthSwitch');
    const validitySwitch = document.getElementById('validitySwitch');
    const dimInput = document.getElementById('validityDimensions');
    const singleInput = document.getElementById('validitySingleQuestions');
    const targetInput = document.getElementById('validityTarget');

    // 合成答卷的开关是 synthSwitch，不是 validitySwitch。
    // 早先把 enabled 绑到了 validityOn，导致信效度开着、合成关着时导出的
    // 文件里 synth.enabled 却是 true —— 状态自相矛盾（2026-10-20 定位）。
    const synthOn = !!(synthSwitch && synthSwitch.checked);
    const validityOn = !!(validitySwitch && validitySwitch.checked);
    const singleSet = new Set(Array.from(parseQuestionNumbers(singleInput ? singleInput.value : '')));
    const groups = parseDimensionGroups(dimInput ? dimInput.value : '');

    const idOf = function (q) { return parseInt(q.id, 10); };

    // 能参与信效度的题：量表题、矩阵题的每一行，或被点名「用单选做题的 1-5 打分量表」
    const scaleIds = questions
        .filter(function (q) { return q.type === '量表题' || (q.type === '单选题' && singleSet.has(idOf(q))); })
        .map(idOf);
    // 矩阵题每一行算一道独立的量表题
    const matrixRows = questions
        .filter(function (q) { return q.type === '矩阵题'; })
        .reduce(function (acc, q) {
            (q.options || []).forEach(function (row, i) {
                acc.push(idOf(q) * 100 + i);
            });
            return acc;
        }, []);
    const allScaleItems = scaleIds.concat(matrixRows);

    const dimensions = (groups.length ? groups : [allScaleItems.slice()])
        .filter(function (items) { return items.length >= 2; })
        .map(function (items) { return { name: '维度', items: items }; });

    // 候选自变量：量表题 + 单选/下拉（填空和多选不进回归）
    const predictors = questions
        .filter(function (q) {
            return q.type === '量表题' || q.type === '单选题' || q.type === '下拉框';
        })
        .map(idOf);

    const alphaTarget = targetInput ? parseFloat(targetInput.value) : 0.80;

    // 找出「没填答案」的题，直接列进文件，让 AI/用户知道该补哪些
    const empty = scanEmptyAnswers(questions);
    const noProb = empty.noProb;
    const noText = empty.noText;

    // ---- 问卷是否撑得起统计分析 ----
    // 判据：能算信度的题项（量表题 + 矩阵行）不足 3 个，或连 2 个候选自变量都没有，
    // 或者根本挑不出一个二分类因变量（单选题）→ 都不该让 AI 去硬凑 effects/targets。
    // 这种情况写 _不可用原因，_prompt 会据此把任务降级成「只补概率和答案池」。
    const binaryCandidates = questions.filter(function (q) {
        return q.type === '单选题' && (q.options || []).length >= 2;
    }).length;
    let unusableReason = null;
    if (allScaleItems.length < 3) {
        unusableReason = '量表题（含矩阵行）不足 3 个，算不出可信的 Cronbach α 与 KMO';
    } else if (predictors.length < 2) {
        unusableReason = '候选自变量不足 2 个，做不了回归';
    } else if (binaryCandidates === 0) {
        unusableReason = '没有可作为二分类因变量的单选题';
    }

    const toFill = unusableReason
        ? []
        : [
            "outcome.question（因变量题号）",
            "outcome.positiveOption（哪个选项记为 1）",
            "effects（每个自变量对 Y 的真实效应）",
            "constraints（逻辑一致性规则）"
        ];
    if (noProb.length) {
        toFill.push("questions[].options[].probability（这些题概率全为 0，必须补全且合计 100："
            + noProb.map(function (q) { return 'Q' + q; }).join('、') + "）");
    }
    if (noText.length) {
        toFill.push("questions[].textAnswers（这些填空题没有答案池，必须补全："
            + noText.map(function (q) { return 'Q' + q; }).join('、') + "）");
    }

    const spec = {
        "_说明": "合成答卷规格。软件按这里的参数生成每一份答卷并整批校验统计性质。填法见 _guide 的「synth 字段速查」。",
        "_toFill": toFill,
        "_emptyAnswers": {
            "probabilityAllZero": noProb,
            "textAnswersEmpty": noText,
            "_说明": "这两项列出的是「还没填答案」的题号。非空就说明那几题必须补，"
                + "否则导入会被概率校验拦住，或提交时被问卷必答拦住。补法见 _guide 的「概率和答案池怎么填」。"
        },
        "enabled": synthOn,
        "outcome": {
            "question": null,
            "positiveOption": null,
            "label": ""
        },
        "predictors": predictors,
        "dimensions": dimensions,
        "dimCorr": [],
        "targets": {
            "alphaMin": Math.max(0.60, Math.round((alphaTarget - 0.05) * 100) / 100),
            "kmoMin": 0.60,
            "sigTotal": [2, 4],
            "sigItems": []
        },
        "effects": {},
        "constraints": [],
        "seedSearch": 80
    };
    if (unusableReason) {
        spec._不可用原因 = unusableReason;
        spec.enabled = false;
    }
    return spec;
}

/**
 * 生成「给 AI 的任务说明」（_prompt 段）。
 *
 * _guide 讲的是「每个字段是什么」，_prompt 讲的是「你要做什么、改哪里、怎么输出」。
 * 两者分工不同：只有 _guide 时，AI 经常回一句「已修改」或只贴一个片段，
 * 拿回来根本没法导入，所以这里把输出格式要求写死。
 */
function buildAiPrompt(questions, spec, targetCount) {
    const s = spec || {};
    const outcome = s.outcome || {};
    const targets = s.targets || {};
    const nCount = Number(targetCount);
    // 问卷本身撑不起统计分析时，synth 里会带一个 _不可用原因。
    // 这种情况要拦住「硬凑参数」，不能再按常规流程要 effects。
    const unusable = s['_不可用原因'];

    // ---- 题号过滤：提示词里只允许出现「当前问卷真实存在」的题号 ----
    // 面板可能留着另一份问卷的参数（localStorage 恢复 + 换问卷没重置）。
    // titleOf 对不存在的题号返回空串，直接渲染会出现「Q9、Q11、Q26」
    // 这种光杆题号 —— 和上面的「合法题号清单」自相矛盾，AI 要么照抄要么困惑。
    // 实测踩过：PmxGvvu.json（7 题）的验收指标段写着「因变量 Q21、自变量 8 个」。
    // 注意：必须查传入的 questions 参数（和 titleOf 同源），不能查
    // window.__wjxQuestions —— 两者不是同一个数组，查全局会把合法配置误判成幽灵。
    const qidOf = function (q) {
        const raw = q && (q.id !== undefined ? q.id : (q.题号 !== undefined ? q.题号 : q.number));
        return parseInt(String(raw), 10);
    };
    const exists = function (id) {
        const n = Number(id);
        if (!Number.isFinite(n) || n <= 0) return false;
        const list = questions || [];
        if (list.some(function (q) { return qidOf(q) === n; })) return true;
        // 矩阵行伪键：宿主持存在就算存在
        return n >= 100 && list.some(function (q) { return qidOf(q) === Math.floor(n / 100); });
    };
    const outcomeQ = Number(outcome.question);
    const outcomeOk = Number.isFinite(outcomeQ) && outcomeQ > 0 && exists(outcomeQ);
    const preds = (Array.isArray(s.predictors) ? s.predictors : []).filter(exists);
    const dims = (Array.isArray(s.dimensions) ? s.dimensions : [])
        .map(function (d) {
            const items = (Array.isArray(d) ? d : (d && d.items) || []).filter(exists);
            return { name: (!Array.isArray(d) && d && d.name) || '', items: items };
        })
        .filter(function (d) { return d.items.length >= 2; });
    const effectsAll = s.effects || {};
    const effects = {};
    Object.keys(effectsAll).forEach(function (k) {
        if (exists(k)) effects[k] = effectsAll[k];
    });
    const sigItemsAll = Array.isArray(targets.sigItems) ? targets.sigItems : [];
    const sigItems = sigItemsAll.filter(exists);

    const titleOf = function (id) {
        const n = Number(id);
        // 矩阵行的伪键：题号*100+行号，比如 700 = 第 7 题第 1 行。
        // 这种题号在 questions 里查不到，得回到矩阵的 rowText 去取名字。
        if (Number.isFinite(n) && n >= 100) {
            const qid = Math.floor(n / 100), row = n % 100;
            const q = (questions || []).find(function (x) { return String(x.id) === String(qid); });
            const r = q && (q.options || [])[row];
            const t = r && r.rowText ? String(r.rowText) : '';
            return t.replace(/^\s*\d+\s*[、.．]\s*/, '').replace(/【[^】]*】/g, '').trim();
        }
        const q = (questions || []).find(function (x) { return String(x.id) === String(id); });
        if (!q) return '';
        // 题干里混着「6、」「【单选题】」「（1~5 分）」这些，对判断题意没帮助，
        // 清掉既好读也省 token
        return String(q.title || '')
            .replace(/^\s*\d+\s*[、.．]\s*/, '')
            .replace(/【[^】]*】/g, '')
            .replace(/[（(][^）)]*[）)]/g, '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 30);
    };
    const qLabel = function (id) {
        const n = Number(id);
        // 矩阵行要标出「第几行」，否则 AI 看到 Q700 完全不知道指什么
        if (Number.isFinite(n) && n >= 100) {
            const t = titleOf(id);
            return 'Q' + Math.floor(n / 100) + '第' + (n % 100 + 1) + '行' + (t ? '（' + t + '）' : '');
        }
        const t = titleOf(id);
        return 'Q' + id + (t ? '（' + t + '）' : '');
    };
    const num = function (v, d) {
        const n = Number(v);
        return Number.isFinite(n) ? n : d;
    };

    // ---- 合法题号清单：这是防「凭空编题号」最有效的一招 ----
    // 只给字段说明的话，模型会自己发明题号（实测把矩阵行的 700/1200 塞进预测变量，
    // 或者从别的问卷抄一套），而引擎对不存在的题号是静默忽略的，日志里看不出来。
    // 所以直接把闭集摆在它面前，并明说「清单外一律无效」。
    const allIds = (questions || []).map(function (q) { return String(q.id); });
    const hasMatrix = (questions || []).some(function (q) { return q.type === '矩阵题'; });
    const whitelist = [];
    (questions || []).forEach(function (q) {
        const t = titleOf(q.id);
        whitelist.push('Q' + q.id + ' ' + String(q.type || '').replace('题', '') + (t ? '「' + t + '」' : ''));
    });
    // 每题之间用顿号连成一行，太长就按 5 题折行，便于模型逐条核对
    const whitelistLines = [];
    for (let i = 0; i < whitelist.length; i += 5) {
        whitelistLines.push(whitelist.slice(i, i + 5).join('、'));
    }

    // ---- 我要什么（从当前草案里读，换问卷时自动跟着变）----
    const wanted = [];
    if (outcomeOk) {
        const raw = outcome.positiveOption;
        const posText = (Array.isArray(raw) ? raw : [raw])
            .map(Number).filter(function (v) { return Number.isFinite(v); })
            .map(function (v) { return '选项' + (v + 1); }).join(' + ');
        const name = outcome.label || titleOf(outcomeQ);
        wanted.push('因变量：Q' + outcomeQ + (name ? '「' + name + '」' : '')
            + (posText ? '，' + posText + ' 合并记为 1，其余记为 0' : '') + '。');
    } else {
        wanted.push('因变量：还没指定。请挑一个二分类（或能合并成两类）的单选题，填进 synth.outcome。');
    }
    if (preds.length) {
        wanted.push('进回归的自变量 ' + preds.length + ' 个：'
            + preds.map(function (q) { return qLabel(q); }).join('、') + '。');
    }
    if (dims.length) {
        wanted.push('维度分组：' + dims.map(function (d, i) {
            return (d.name || ('维度' + (i + 1))) + ' ['
                + (d.items || []).map(function (q) { return qLabel(q); }).join('、') + ']';
        }).join('；') + '。');
    }
    const sigArr = Array.isArray(targets.sigTotal) ? targets.sigTotal : [];
    const sigLo = num(sigArr[0], 2);
    const sigHi = num(sigArr[1], 4);
    wanted.push('回归模块的验收断言：应稳定输出 ' + sigLo + (sigHi > sigLo ? '~' + sigHi : '') + ' 个显著项'
        + (sigItems.length ? '，其中 ' + sigItems.map(function (q) { return 'Q' + q; }).join('、') + ' 必须显著' : '')
        + '（用来检查 IRLS 求解与 Wald 检验的实现是否正确）。');
    wanted.push('信度模块的验收断言：Cronbach α ≥ ' + num(targets.alphaMin, 0.8).toFixed(2)
        + ' —— 分维度算、以及把所有量表题合并算，两种口径都要过。');
    wanted.push('效度模块的验收断言：KMO ≥ ' + num(targets.kmoMin, 0.6).toFixed(2)
        + '，Bartlett 球形检验 p < 0.001。');
    wanted.push('数据形态要求：必须保留真实作答的噪声特征（不允许所有变量都显著、不允许每题分布都均匀），'
        + '否则测不出软件在「非理想数据」下的表现。');

    // ---- 当前状态：说清「哪些能直接信、哪些必须你来定」----
    const effectText = Object.keys(effects).length
        ? '当前 effects = ' + JSON.stringify(effects) + '（键=题号，值=该题对 Y 的 logit 系数，正数=提高 Y=1 的概率）。'
        : '当前 effects 是空的，需要你从零给出。';
    // 不适合做统计时，别再把「目标 α / 显著项」当成任务，否则 AI 会硬凑
    const wantedOut = unusable
        ? ['这份问卷不做信效度分析和回归（原因见 synth._不可用原因）。'
           + '本次任务只有一项：把还没填的 probability 和 textAnswers 补全，让程序能正常作答提交。']
        : wanted;

    const state = unusable
        ? ['这份问卷题目结构不足，不需要 effects / targets / dimCorr 这类参数，也不该去造。',
           '概率和答案池已经补齐，导入后直接按普通概率模式作答即可。']
        : [
            effectText,
            '上面这些目标（显著个数、α、KMO）是软件生成时必须命中的验收条件；命中不了它会自动换随机种子整批重来。',
            '但 α、KMO、Wald p 都是整批样本的统计量 —— 你不用也不能替它算，只要把 effects / targets / dimCorr 这些参数给对。',
            Object.keys(effects).length
                ? '如果你判断当前参数已经能满足目标，就不要改，直接回答「当前配置已满足要求，无需修改」并说明依据。'
                : '当前还没有任何效应参数，需要你补完 effects 之后这套配置才能生成。'
        ];

    // ---- 哪些是「软件默认值」而不是「用户的要求」----
    // 新问卷刚解析就导出时，这里几乎全是默认：因变量空、自变量把所有题都塞进来、
    // α 是 0.75、显著是 2~4。不点明的话，AI 会把这些默认值当成用户的需求去做。
    const empty = scanEmptyAnswers(questions);
    const pending = [];

    if (unusable) {
        // 问卷本身撑不起统计分析（量表题不够 / 没有二分类因变量）。
        // 这时最该防的是 AI 硬凑参数、给出一个「看起来能跑」的配置。
        pending.push('这份问卷**当前不启用合成答卷**，原因见 synth._不可用原因：'
            + '题目结构不足（量表题太少或没有可用的二分类因变量）。'
            + '不要强行填 effects、也不要把 enabled 改成 true —— 题目数量不够时，'
            + '生成的数据算不出可信的 α / KMO / 显著项，只会误导。'
            + '用户说「补数据」时，指的是补全 probability 和 textAnswers，让程序能正常作答提交。');
    } else {
    if (!s.enabled) {
        pending.push('synth.enabled 现在是 false（合成答卷没开）。要让这套参数真正生效，必须改成 true。');
    }
    if (!outcomeOk) {
        pending.push('因变量（synth.outcome.question）：**未指定**。程序猜不出来，'
            + '必须由用户明确「哪一题、哪几个选项算 1」。用户没说就反问，不要自己挑。');
        pending.push('挑因变量的硬规则：**不能是人口学/背景题**（性别、年级、高校、学历、专业、'
            + '生源地、户口、父母学历、家庭收入、实习次数、技能自评、成绩排名、offer 数这类）。'
            + '它们只能做自变量。因变量必须是被解释的**结果题**——问意愿、状态或评价的那种，'
            + '题干里通常带「是否」「打算」「愿意」「会不会」「倾向」。'
            + '拿背景题当因变量，回归照样能跑出显著，但研究问题整个换了，等于白做。');
    }
    if (!sigItems.length) {
        pending.push('必须显著的题（synth.targets.sigItems）：未指定。可以留空（只约束总数），'
            + '也可以按用户要求点名，比如 [23, 25, 26]。');
    }
    if (preds.length > 12) {
        pending.push('自变量：当前是软件自动纳入的**全部 ' + preds.length + ' 道题**，'
            + '不是用户挑选的子集。一般的回归建模只要 5~10 个，按用户的要求筛。');
    }
    if (!Object.keys(effects).length) {
        pending.push('synth.effects：**空**，需要你从零给出，否则回归会是零显著。');
    }
    if (num(targets.alphaMin, 0) <= 0.75) {
        pending.push('targets.alphaMin 现在是软件默认的 ' + num(targets.alphaMin, 0.75)
            + '；用户如果说「α≥0.80」，就改成 0.80。');
    }
    if (num(targets.kmoMin, 0) <= 0.60) {
        pending.push('targets.kmoMin 现在是默认的 ' + num(targets.kmoMin, 0.60)
            + '；用户常说「要保证效度」，一般指 KMO ≥ 0.65 且 Bartlett 显著。');
    }
    if (dims.length === 1 && (dims[0].items || []).length > 4) {
        pending.push('维度分组：当前是「所有量表题算一个维度」（软件默认）。'
            + '如果这些题明显测的不是同一个构念（比如既有薪资又有离家近），'
            + '应先拆成 2~3 组再算信度，否则合并 α 在方法上站不住。');
    }

    }

    // 答案缺口的检测：概率全 0、填空没答案池、「其他___」没补充答案。
    // 这三种短板跟「用不用合成答卷」无关，两种情况都要报，所以放在 if/else 外面。
    if (empty.noProb.length) {
        pending.push('这 ' + empty.noProb.length + ' 道题的 probability 全是 0（'
            + empty.noProb.map(function (q) { return 'Q' + q; }).join('、')
            + '）：软件会用「中间高两边低」的默认分布兜底，α/KMO/显著性不受影响，'
            + '但描述统计会失真（比如学历题变成硕士最多）。要具备真实作答的形态就必须补，合计各 100。');
    }
    if (empty.noText.length) {
        pending.push('这 ' + empty.noText.length + ' 道填空题没有答案池（'
            + empty.noText.map(function (q) { return 'Q' + q; }).join('、')
            + '）：它们基本都是必答题，不填会让每一份都提交失败，必须补 textAnswers（合计 100）。');
    }
    const emptyOther = [];
    (questions || []).forEach(function (q) {
        const ota = q.optionTextAnswers || {};
        (q.options || []).forEach(function (o) {
            if (o && typeof o.text === 'string' && o.text.indexOf('【填空题输入框】') >= 0) {
                const cfg = ota[String(o.index)];
                if (!Array.isArray(cfg) || !cfg.length) {
                    emptyOther.push('Q' + q.id + '·选项' + (o.index + 1));
                }
            }
        });
    });
    if (emptyOther.length) {
        pending.push('这些「其他___」选项没有补充答案（' + emptyOther.join('、')
            + '）：选了「其他」的人会被填成「其他情况」，' + emptyOther.length + ' 处全一样，重复度过高。'
            + '请补 optionTextAnswers（合计 100）。');
    }
    // 份数是导出的默认值 1，用户经常忘了改。1 份数据算不出任何统计量，
    // 不点明的话 AI 会拿着一份 targetCount=1 的配置去凑「3 个显著」。
    if (!(Number.isFinite(nCount) && nCount > 1)) {
        pending.push('targetCount 现在是 1（软件默认值，不是用户的要求）：'
            + '只生成 1 份答卷，α/KMO/显著性一个都算不出来。'
            + '用户没说份数就先问；通常这类自测要 200 份左右。');
    }

    pending.push('以上是「文件现状」，不是「用户需求」。用户明确说过的以用户为准；'
        + '用户没说的，按上面提示处理或反问。');

    return {
        '_用途与边界（先读这段，避免误判）': [
            '这是「合成数据（synthetic data）生成配置」，用途是**软件自测与功能演示**，不是学术调研数据。',
            '背景：本项目是一个问卷结构解析 / 批量作答的桌面工具，内部有一条统计管线'
                + '（概率建模 → 批量生成 → α 与 KMO 计算 → 二元 logistic 回归 → 导出 CSV）。'
                + '为验证这条管线算得对不对、导出格式对不对，需要一批「统计结构已知」的模拟数据当测试夹具'
                + '（test fixture）—— 这和用假用户数据测电商系统的下单流程是同一件事。',
            '所以下面的 α ≥ 0.80、3 个显著，本质是**软件自测的验收断言**，'
                + '等价于单测里写 assert result ≈ expected，用来检查代码实现，不是为了让某个研究结论显著。',
            '生成的全部是模拟值：不含任何真实受访者信息，也不代表任何真实调研结论；'
                + '导出时会带 "synthetic": true 标记，界面也会标注「模拟数据」。',
            '不用于、也不应被用于学术论文、期刊投稿、学位论文或任何对外发布的调研结论。',
            '如果你的策略是不协助「生成可能被误用的数据」：**可以只做参数填写这一件事** —— '
                + '只改 JSON 里的建模参数（effects / targets / dimCorr / probability），'
                + '不生成任何答卷。真实采样与统计校验由本地程序完成，这也是本文件期望你做的事。'
        ],
        '_说明': '本文件自带全部说明，把整个文件发给 AI 即可，不需要再解释格式。AI 按下面执行。',
        '这是一个什么文件': '问卷配置 + 合成数据规格（UTF-8 JSON）。questions 是问卷结构快照，'
            + 'synth 是生成参数。软件读取后用统计模型生成 N 份模拟答卷，用于自测统计管线与功能演示。',
        ['本问卷的合法题号（只能用这些，共 ' + allIds.length + ' 题）']: whitelistLines.concat([
            '↑ 这就是本题库的全部题号。synth.outcome / predictors / dimensions / effects / sigItems 里'
                + '出现的每一个题号，都必须能在上面查到；查不到的一律无效，引擎会静默忽略（不报错，'
                + '日志也不会提），结果就是「说好 8 个变量，实际只跑 4 个」。',
            hasMatrix
                ? '本问卷含矩阵题。矩阵的每一行可以当作一道独立量表题，编号规则是「题号×100+行号」'
                    + '（第 7 题第 1 行 = 701，从 1 开始数）；只有矩阵题才允许这种编号。'
                : '**本问卷没有矩阵题**，所以不存在任何「题号×100+行号」形式的编号。'
                    + '如果看到 700、1200 这类大于 100 的数字，那是从别的问卷带过来的残留，必须换成真实题号。'
        ]),
        '本次要达成的验收指标（软件自测）': wantedOut,
        '需要用户拍板的事项（先读这段）': pending,
        '当前状态': state,
        '要改就改这几个字段': [
            'synth.effects —— 核心参数。键=题号字符串，值=该题对 Y 的设定 logit 系数（工程参数，不是某个真实总体的估计值）。参考量级'
                + '（n=200、8 个自变量）：5 点量表 0.55~0.70 → 约 p<0.01；3~5 级人口学 0.35~0.40 → 约 p<0.01；'
                + '二分类变量约 0.70；<0.15 基本不显著，可以留着做「预期不显著但方向合理」的对照。',
            'synth.targets.sigTotal —— [下界, 上界]。要「恰好 N 个」必须写 [N,N]；写 [N,N+1] 只代表'
                + '「不超过 N+1」，因为软件命中区间就停，不会偏向区间下界。',
            'synth.targets.sigItems —— 哪几题必须显著（题号数组）。',
            'synth.dimCorr —— 维度间相关，按上三角顺序，影响 KMO；0.40~0.50 比较合适。',
            'questions[].options[].probability —— 只决定单题的分布形状（描述统计好不好看），不影响显著性。'
                + '不要让某一项超过 85%，方差被压塌会让 α 掉下来。',
            'questions[].textAnswers / optionTextAnswers —— 填空题和「其他___」选项的答案池，合计都要 = 100。'
        ],
        '输出要求（最关键，请严格遵守）': [
            '方案 A（推荐，如果你能直接写文件）：就地修改，只改需要改的数值，其余内容一个字节都不要动。'
                + '改完告诉用户：改了哪几个字段、从多少改成多少、预期会得到几个显著。',
            '方案 B（只能输出文本时）：输出**完整** JSON，从第一行的 { 到最后一行的 }，保存成 .json 后能直接导入。',
            '绝对不要写「其余保持不变」「...」「（此处省略）」「以下是修改部分」—— 这样的输出没法用。',
            '绝对不要删题、改题号、改题干、减少选项数量。questions 数组必须逐题原样保留。',
            '如果内容太长输出不完整：只输出 synth 段（一个完整 JSON 对象），并说明「用这个对象整体替换原文件的 synth 段」；'
                + '不要输出一个残缺的完整文件。'
        ],
        '改完自检（逐条过一遍）': [
            '每道单选题 / 多选题 / 下拉框 / 量表题：options 里所有 probability 合计 = 100。',
            '每道矩阵题：每一行的 probability 合计 = 100（不是整题合计）。',
            '每道填空题：textAnswers 合计 = 100。',
            '每个带「其他___」的选项：optionTextAnswers 合计 = 100。',
            '**逐个核对题号**：outcome.question、predictors、sigItems、dimensions 里的每个数字，'
                + '都要能在「本问卷的合法题号」清单里查到。这一条最容易漏，也最容易出事。',
            'predictors 的数量要和用户要求的变量数一致（说要 8 个就是 8 个，不是 6 个也不是 26 个）。',
            'synth.effects 里的每个键，都必须出现在 synth.predictors 里。',
            'synth.outcome.question 必须是单选题，且能二值化（选项数 ≥ 2、正向选项不等于全部选项）。',
            '整份 JSON 能被标准解析器解析：没有多余逗号、没有 // 注释、没有尾随字符。'
        ],
        '常见错误（别踩）': [
            '× 把人口学变量当因变量（性别、学历、年级、生源地这类 Q1~Q20）。'
                + '它们是背景变量，只能当自变量；因变量必须是被解释的「结果题」，'
                + '通常问的是意愿/状态/评价（如「是否考虑留在某地就业」）。'
                + '搞反了统计上照样能跑，但研究问题整个变了。',
            '× 从别的问卷复制题号。矩阵行写法是「题号×100+行号」（700 = 第 7 题第 1 行），'
                + '只有当第 7 题确实是矩阵题时才成立。本问卷若没有矩阵题，'
                + '700/1200 这类题号指向的东西不存在，引擎会忽略它们，'
                + '「8 个自变量」会悄悄变成 4 个。改完必须逐个核对题号在 questions 里存在。',
            '× dimensions 写成不认的格式。支持两种：'
                + '[{"name":"经济","items":[22,23]}] 或简写 [[22,23],[25,27]]；'
                + '不要写成 {"22":[...]} 这种对象或纯字符串。',
            '× 想通过改 probability 去影响显著性 —— 无效。显著性只由 effects 决定，probability 只负责单题的分布形状。',
            '× 把 effects 全部往大堆 —— 同维度共线会把系数放大 1.5~2 倍，OR 会大到超出合理区间，测试用的分布也会失真。',
            '× sigTotal 写得比自变量个数还大 —— 软件搜不到解，只能退回最接近的一批。',
            '× 只改 synth、把概率和填空题留空 —— 描述统计会失真（比如学历变成「硕士最多」），填空题还会被必答拦住。'
        ]
    };
}

/**
 * 导出文件里自带的「字段速查」（_guide 段）。
 *
 * 分工：_prompt 是给 AI 的任务书（做什么、怎么输出、哪些要用户拍板），
 * _guide 是字段字典（每个字段是什么、合法值是什么）。
 * 早先两者各写一套长篇说明，同一件事讲两遍，文件凭空多出 3KB，
 * AI 还要判断该听哪套 —— 现在 _guide 只留速查，判断类的话全部收口到 _prompt。
 */
function buildConfigGuide() {
    return {
        "这个文件是什么": "问卷星助手的问卷配置：questions=问卷结构快照，synth=模拟数据生成参数，settings=功能设置。"
            + "生成的是模拟数据（synthetic: true），用于软件自测与功能演示，不是学术调研数据。"
            + "改完点「导入配置」即可生效，不需要重新解析问卷。",
        "怎么填": [
            "1) 选择题：改 options[].probability（0~100 整数），同题合计必须 = 100；矩阵题按行合计 = 100；",
            "2) 填空题：改 textAnswers（[{text, probability}]，合计 100）。textAnswersMode 两种取值："
                + "\"random\"（默认，按权重随机抽，答案可重复）/ \"unique\"（按份数一一发放，条数必须等于 targetCount）；",
            "3) 「其他___」选项：补 optionTextAnswers（合计 100），否则选其他的人会被填成同一句「其他情况」；",
            "4) 合成答卷参数填 synth 段，字段速查见下文；",
            "5) id / type / title / text / index / rowIndex / colIndex 是定位用的，不要改、不要删题；",
            "6) settings 不想动就保持原样；",
            "7) 存成 UTF-8 的 .json，回到软件点「导入配置」。synth._emptyAnswers 列出还没填答案的题，补齐即可清空。"
        ],
        "概率和答案池怎么填": [
            "先看 synth._emptyAnswers：probabilityAllZero / textAnswersEmpty 里列出的题号就是还没填答案的题。",
            "概率全 0 的题软件会用「中间高、两边低」兜底，统计结论不受影响，但描述统计会失真；",
            "填空题没有答案池会被问卷的必答校验拦住，直接刷不下去。",
            "× 不要写均匀分布（5 个选项就 20/20/20/20/20）：真实作答几乎不会均匀；",
            "× 不要有单项超过 85%：方差被压塌，α 和 KMO 会一起掉；",
            "√ 按调查群体的真实比例给，允许有偏。参考量级（按需调整，不要照抄）：",
            "  性别 48/52；学历（本科/硕士/专科）72/20/8；",
            "  家庭收入（1万以下/1-3万/3-6万/6-10万/10万以上）14/28/30/18/10；",
            "  5 点量表 10~16 / 18~24 / 28~32 / 20~26 / 10~16（中间高两端低，别完全对称）；",
            "√ 因变量那题的分布最关键：它决定 Y=1 占比，软件据此标定截距；"
                + "正向比例低于 20% 或高于 80% 会被限制到 20%~80% 并告警；",
            "√ 填空题答案池：用真实存在的城市/单位/专业名，按人口或经济权重给概率（省会排前面），"
                + "不要凑一堆概率相同的答案——生源地/就业地的分布本身就是高度偏斜的；",
            "分工：probability 只决定单题分布形状；显著性和信效度由 synth.effects 决定，别在概率上做文章。"
        ],
        "synth 字段速查": {
            "outcome.question": "因变量题号。必须是单选题、且能二值化（选项数≥2）。不能拿人口学/背景题（性别、学历、收入、生源地这类）当因变量——它们只能做自变量。",
            "outcome.positiveOption": "哪些选项记为 1（0 基索引，第一个选项是 0），如 [0,1]。",
            "outcome.label": "一句话说明，如「是否考虑留在安徽」。",
            "predictors": "进回归的自变量题号数组，5~10 个为宜。题号必须能在 questions 里查到——查不到的会被引擎静默忽略，「8 个变量」会悄悄变少。",
            "dimensions": "维度分组：测同一构念的题放一组，每组≥2 题、3 题更稳。两种写法都行：[{\"name\":\"经济\",\"items\":[22,23]}] 或简写 [[22,23],[25,27]]。只有矩阵题才有「题号×100+行号」的行编号（第 7 题第 1 行=701），本问卷没有矩阵题时不要用这类数字。",
            "dimCorr": "维度间相关，按上三角顺序给，如 3 维写 [0.40,0.34,0.30]；留空=各维度独立（区别效度会难看）。",
            "effects": "核心字段：键=题号字符串，值=该题对 Y 的 logit 系数，正数=提高 Y=1 的概率；没列的变量按 0 处理。同维度共线会把系数放大 1.5~2 倍，不要往大堆；同一维度只给一道题设效应，其余会被共线自动压到不显著——这正是真实数据的形态。",
            "constraints": "逻辑一致性规则，让作答像真人：[{\"when\":{\"2\":[0]},\"then\":{\"8\":[1]}}] 表示第 2 题选第 0 个选项时，第 8 题只能选第 1 个。when 可用 \"*\" 兜底。常用规则：高校↔所在城市、生源地类型↔户口性质、目标城市↔留皖意愿、毕业打算↔offer 数。",
            "targets.sigTotal": "显著项个数区间 [下界,上界]。要「恰好 N 个」必须写 [N,N]；写 [N,N+1] 只代表不超过 N+1（软件命中区间就停）。",
            "targets.sigItems": "必须显著的题号数组，可留空（只约束总数）。",
            "targets.alphaMin": "Cronbach α 下限，默认 0.75；用户说 α≥0.80 就填 0.80。",
            "targets.kmoMin": "KMO 下限，默认 0.60；用户说「保证效度」一般指 ≥0.65 且 Bartlett 显著。",
            "seedSearch": "最多重试多少批，默认 80，够用。"
        },
        "settings 字段说明": {
            "targetCount": "目标份数（整数，1~1000）",
            "speedMultiplier": "倍速，只能填 2 / 4 / 6",
            "timeControlSwitch": "是否控制单份填写时长（true/false）",
            "minFillTime": "填写时长下限，单位秒，字符串，10~360",
            "maxFillTime": "填写时长上限，单位秒，字符串，10~360",
            "proxySwitch": "是否使用代理（true/false）",
            "apiUrl": "提取代理 IP 的接口地址，不需要代理就留空字符串",
            "sourceRatioSwitch": "是否按比例伪装提交来源（手机/链接/微信）",
            "sourceRatio": "三个比例，mobile + link + wechat 必须 = 100",
            "validitySwitch": "是否开启信效度优化（量表题/矩阵行按潜在因子生成，题间一致性由 validityTarget 决定，和 probability 无关）",
            "validityTarget": "目标 Cronbach α，字符串，0.60~0.90，常用 \"0.80\"（0.85~0.88 达标率更高）",
            "validityDimensions": "维度分组，题号区间，如 \"4-5\" 或 \"1-3,4-6\"；每组至少 3 个题项（矩阵的一行算一题）",
            "validitySingleQuestions": "把「用单选题做的 1~5 打分量表」的题号写这里，如 \"22-28\"；没有就填空字符串",
            "validityCorrelation": "维度间相关（可选），建议 0.3~0.5：按上三角顺序填，分号分行。3 维写 \"0.45,0.30;0.35\"，4 维写 \"0.45,0.30,0.35;0.40,0.25;0.50\"；留空 = 各维度互不相关",
            "validityReverse": "反向计分题号（可选），如 \"25+30\"；程序按 k+1-r 自动翻转，留空 = 全部正向"
        },
        "题型与字段对照": {
            "单选题 / 多选题 / 下拉框": "options[{index, text, probability}]，整题合计 100",
            "量表题": "options[{index, text(第几个刻度), probability}]，整题合计 100",
            "矩阵题": "options[{rowIndex, rowText, options:[{colIndex, probability}]}]，每行合计 100",
            "排序题": "options 为空数组即可",
            "填空题": "options 为空数组；用 textAnswers[{text, probability}]，合计 100；textAnswersMode 可选 \"random\"/\"unique\""
        },
        "最小示例": {
            "questions": [
                {
                    "id": "1", "type": "单选题", "title": "你的性别",
                    "options": [
                        { "index": 0, "text": "男", "probability": 50 },
                        { "index": 1, "text": "女", "probability": 50 }
                    ]
                },
                {
                    "id": "2", "type": "矩阵题", "title": "请给下列方面打分",
                    "options": [
                        {
                            "rowIndex": 0, "rowText": "颜值",
                            "options": [
                                { "colIndex": 0, "probability": 20 },
                                { "colIndex": 1, "probability": 30 },
                                { "colIndex": 2, "probability": 30 },
                                { "colIndex": 3, "probability": 20 }
                            ]
                        }
                    ]
                },
                {
                    "id": "3", "type": "填空题", "title": "还有什么建议",
                    "options": [],
                    "textAnswers": [
                        { "text": "没有", "probability": 50 },
                        { "text": "挺好的", "probability": 50 }
                    ],
                    "textAnswersMode": "random"
                }
            ]
        }
    };
}

// 显示程序运行完成界面
function showCompletedInterface(data) {
    const runningInterface = document.getElementById('runningInterface');
    if (runningInterface) {
        runningInterface.remove();
    }
    
    const analysisContent = document.getElementById('analysisContent');

    // 创建完成界面
    // 关键：这里**不能**清空 analysisContent —— 一清空，用户已经配好的每道题概率、
    // 文本答案、维度分组就全没了，点「返回首页」只能退回首页重来。
    // 改成：把配置界面整体隐藏，完成界面作为它的兄弟节点插进去，回来时再反过来显示。
    let completedInterface = document.getElementById('completedInterface');
    if (!completedInterface) {
        completedInterface = document.createElement('div');
        completedInterface.id = 'completedInterface';
        completedInterface.className = 'result-wrap';
        analysisContent.parentNode.insertBefore(completedInterface, analysisContent.nextSibling);
    }
    setVisible(analysisContent, false);

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
    
    setVisible(completedInterface, true);

    // 顶部按钮改叫「返回配置」：此时点它是回到刚才那套已配置好的界面，而不是真的回首页
    const backBtn = document.getElementById('backBtn');
    if (backBtn) {
        backBtn.innerHTML = '<i class="fa fa-arrow-left" aria-hidden="true"></i> 返回配置';
    }
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

    // 骨架可以用 innerHTML，但问卷链接来自 URL 参数，必须用 textContent 写入：
    // 直接拼 innerHTML 的话，/analysis?url=<img onerror=...> 这种会被当标记执行。
    historyContent.innerHTML = '';

    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.className = 'btn btn-sm btn-danger';
    clearBtn.textContent = '清空历史记录';
    clearBtn.addEventListener('click', clearHistory);
    actions.appendChild(clearBtn);
    historyContent.appendChild(actions);

    if (history.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'state-msg';
        empty.textContent = '暂无历史运行记录';
        historyContent.appendChild(empty);
        return;
    }

    history.forEach((record, index) => {
        const item = document.createElement('div');
        item.className = 'history-item';

        const title = document.createElement('div');
        title.className = 'history-item-title';
        title.textContent = '运行记录 ' + (index + 1);
        item.appendChild(title);

        [
            ['时间：', record.timestamp],
            ['问卷链接：', record.url],
            ['目标份数：', (record.targetCount === undefined || record.targetCount === null ? 0 : record.targetCount) + '份']
        ].forEach(pair => {
            const p = document.createElement('p');
            const label = document.createElement('strong');
            label.textContent = pair[0];
            p.appendChild(label);
            p.appendChild(document.createTextNode(pair[1] === undefined || pair[1] === null ? '' : String(pair[1])));
            item.appendChild(p);
        });

        historyContent.appendChild(item);
    });
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
            case '下拉框':
                generateDropdownRandomProbs(container);
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

// 生成下拉框题随机概率（同单选题：避免极端分布，真实选项概率之和为100）
function generateDropdownRandomProbs(container) {
    // 占位项没有概率输入框，这里只会选到真正的选项
    const inputs = container.querySelectorAll('.probability-input');
    const count = inputs.length;

    if (count === 0) return;

    const probs = generateBalancedRandomProbs(count, 'single');

    inputs.forEach((input, index) => {
        input.value = probs[index];
        // 移除可能存在的错误样式
        input.classList.remove('is-invalid');
    });

    // 清除当前题目的所有浮动错误提示
    if (window.ProbabilityValidator) {
        const result = window.ProbabilityValidator.validateDropdown(container.dataset.questionId);
    }
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

    // 选项数过多（> 100）时无法保证每项都至少 1%：把 100 个点随机撒到各选项上
    if (count > 100) {
        const probs = new Array(count).fill(0);
        for (let k = 0; k < 100; k++) {
            probs[Math.floor(Math.random() * count)]++;
        }
        return probs;
    }
    
    // 多选题：允许一定差异，但避免极端，提高信效度
    // 每个选项的最小概率：默认 12%，选项过多时自动下调，保证 minProb * count <= 100
    const minProb = Math.max(1, Math.min(12, Math.floor(100 / count)));
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

    // 兜底：确保总和恰好为 100（选项数很多时上面的分配仍可能有偏差）
    let diff = 100 - probs.reduce((a, b) => a + b, 0);
    let guard = 0;
    while (diff !== 0 && guard++ < 10000) {
        const idx = Math.floor(Math.random() * count);
        if (diff > 0) {
            probs[idx]++;
            diff--;
        } else if (probs[idx] > 1) {
            probs[idx]--;
            diff++;
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

// ===== 问号说明：点击显示；鼠标移开问号、或再次点击问号，提示消失 =====
// 说明文字写在元素的 data-tip 属性上（不用 title，避免与原生悬停提示叠加）。
(function initHelpTips() {
    let tipEl = null;
    let activeIcon = null;

    function ensureTip() {
        if (!tipEl) {
            tipEl = document.createElement('div');
            tipEl.className = 'help-tip';
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }

    function hideHelpTip() {
        if (tipEl) {
            tipEl.classList.remove('is-open');
        }
        if (activeIcon) {
            activeIcon.classList.remove('is-active');
        }
        activeIcon = null;
    }

    function showHelpTip(icon) {
        // data-tip 里允许换行缩进，这里折叠成单空格，避免多余空白
        const text = (icon.getAttribute('data-tip') || '').replace(/\s+/g, ' ').trim();
        if (!text) return;

        const el = ensureTip();
        el.textContent = text;
        el.classList.add('is-open');
        activeIcon = icon;
        icon.classList.add('is-active');

        // 定位到问号正下方；下方放不下翻到上方；左右按视口收敛，避免出界
        const rect = icon.getBoundingClientRect();
        const gap = 6;
        const margin = 8;
        el.style.left = '0px';
        el.style.top = '0px';
        const tipWidth = el.offsetWidth;
        const tipHeight = el.offsetHeight;

        let left = rect.left + rect.width / 2 - tipWidth / 2;
        left = Math.max(margin, Math.min(left, window.innerWidth - tipWidth - margin));

        let top = rect.bottom + gap;
        if (top + tipHeight > window.innerHeight - margin) {
            const above = rect.top - tipHeight - gap;
            top = above >= margin
                ? above
                : Math.max(margin, window.innerHeight - tipHeight - margin);
        }

        el.style.left = left + 'px';
        el.style.top = top + 'px';
    }

    document.addEventListener('click', function (e) {
        const icon = e.target && e.target.closest
            ? e.target.closest('.help-icon[data-tip]')
            : null;
        if (icon) {
            if (activeIcon === icon) {
                hideHelpTip();
            } else {
                hideHelpTip();
                showHelpTip(icon);
            }
            return;
        }
        hideHelpTip();
    });

    // 鼠标移开问号即隐藏；移入提示框本身不算移开（方便阅读与选中文字）
    document.addEventListener('mouseout', function (e) {
        if (!activeIcon) return;
        const to = e.relatedTarget;
        if (to && (activeIcon.contains(to) || (tipEl && tipEl.contains(to)))) return;
        const from = e.target && e.target.closest
            ? e.target.closest('.help-icon[data-tip]')
            : null;
        if (from === activeIcon) {
            hideHelpTip();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            hideHelpTip();
        }
    });

    // 滚动 / 改变窗口大小后位置会失真，直接收起
    window.addEventListener('scroll', hideHelpTip, true);
    window.addEventListener('resize', hideHelpTip);
})();