/**
 * mock 环境：验证「换问卷后陈旧 synth 参数自动重置」的各条路径。
 * 只测纯逻辑，不起浏览器。
 *
 * 注意：analysis.js 顶层有 let/const（如 synthExtras），直接 eval 后它们
 * 不会泄漏成 global。这里用「eval 源码 + 对象字面量捕获引用」的方式拿到句柄，
 * 之后再通过 getter/setter 操作 eval 作用域里的 synthExtras。
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'src', 'main', 'resources', 'static', 'js', 'analysis.js');
const code = fs.readFileSync(SRC, 'utf8');

// ---- 极简 DOM mock ----
const elements = {};
function el(id) {
    if (!elements[id]) {
        elements[id] = {
            id, value: '', checked: false, textContent: '', className: '', innerHTML: '',
            dataset: {}, style: {}, children: [],
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
            appendChild(c) { this.children.push(c); return c; },
            removeChild() {}, remove() {},
            querySelector() { return null; }, querySelectorAll() { return []; },
            closest() { return null; }
        };
    }
    return elements[id];
}
global.document = {
    getElementById: el,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    createElement() { return el('tmp'); }
};
global.window = {
    __wjxQuestions: [],
    location: { search: '' },
    dispatchEvent() {},
    addEventListener() {},
    URLSearchParams: URLSearchParams
};
global.localStorage = {
    _d: {},
    getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; }
};
const alerts = [];
global.showAlert = (msg, type, dur) => alerts.push({ msg, type, dur });
global.setVisible = () => {};
global.askConfirm = async () => true;
global.Event = class { constructor(t) { this.type = t; } };

const api = eval(code + '\n;({'
    + 'resetStaleSynthPanel, detectStaleSynthPanel, synthMissingRefs, filterSynthConstraints,'
    + 'buildAiPrompt, buildConfigGuide, buildSynthSpec,'
    + 'get synthExtras() { return synthExtras; },'
    + 'set synthExtras(v) { synthExtras = v; }'
    + '});');

// ---- 工具 ----
function setQuestions(qs) { global.window.__wjxQuestions = qs; }
function resetPanel() {
    ['synthOutcomeQ', 'synthPositive', 'synthPredictors', 'synthEffects',
        'validityDimensions', 'validitySingleQuestions', 'validityReverse'].forEach(id => { el(id).value = ''; });
    el('synthSwitch').checked = false;
    el('validitySwitch').checked = false;
    el('synthSigLo').value = '2';
    el('synthSigHi').value = '4';
    api.synthExtras = { constraints: [], sigItems: [], seedSearch: 80, alphaMin: 0.75, kmoMin: 0.60 };
    alerts.length = 0;
}

let pass = 0, fail = 0;
function check(name, cond, extra) {
    if (cond) { pass++; console.log('  OK ' + name); }
    else { fail++; console.log('  FAIL ' + name + (extra ? '  -> ' + extra : '')); }
}

// ========== 用例 1：PmxGvvu 事件复现 ==========
console.log('\n[用例1] 7 题问卷 + 面板残留 Q4wUhCD 参数 -> 必须重置');
resetPanel();
setQuestions([
    { id: '1', type: '单选题', title: '1、传世武器', options: [] },
    { id: '2', type: '多选题', title: '2、段位', options: [] },
    { id: '3', type: '下拉框', title: '3、请做出你的选择', options: [] },
    { id: '4', type: '量表题', title: '4、给软件打几分', options: [] },
    { id: '5', type: '矩阵题', title: '5、给网页打几分', options: [] },
    { id: '6', type: '排序题', title: '6、网页与软件排序', options: [] },
    { id: '7', type: '填空题', title: '7、好的网页不比软件差', options: [] }
]);
el('synthSwitch').checked = true;
el('synthOutcomeQ').value = '21';
el('synthPositive').value = '0,1';
el('synthPredictors').value = '6,9,11,13,14,23,25,26';
el('synthEffects').value = '11:0.15,23:0.3,25:0.58,26:0.7';
el('validitySwitch').checked = true;
el('validityDimensions').value = '22+23+24+26,25+27+28';
el('validitySingleQuestions').value = '22-28';
api.synthExtras = { constraints: [{ when: { '2': [0] }, then: { '8': [1] } }], sigItems: [23, 25, 26], seedSearch: 80, alphaMin: 0.75, kmoMin: 0.60 };

const r1 = api.resetStaleSynthPanel(global.window.__wjxQuestions);
check('检测到陈旧并返回原因', typeof r1 === 'string' && r1.indexOf('Q21') >= 0, r1);
check('synth 开关被关闭', el('synthSwitch').checked === false);
check('因变量输入框被清空', el('synthOutcomeQ').value === '');
check('自变量输入框被清空', el('synthPredictors').value === '');
check('效应输入框被清空', el('synthEffects').value === '');
check('维度分组被清空', el('validityDimensions').value === '');
check('synthExtras 被重置', api.synthExtras.constraints.length === 0 && api.synthExtras.sigItems.length === 0 && api.synthExtras.seedSearch === 80);
check('弹出 warning 提示', alerts.some(a => a.type === 'warning' && a.msg.indexOf('另一份问卷') >= 0));
check('localStorage 已写回（打破闭环）', typeof localStorage.getItem('wjxSettings') === 'string');

// ========== 用例 2：同一份问卷重新解析不误伤 ==========
console.log('\n[用例2] 同一份问卷（28 题）重新解析 -> 不能误清');
resetPanel();
setQuestions(Array.from({ length: 28 }, (_, i) => ({ id: String(i + 1), type: '单选题', title: (i + 1) + '、题', options: [] })));
el('synthSwitch').checked = true;
el('synthOutcomeQ').value = '21';
el('synthPredictors').value = '6,9,11,13,14,23,25,26';
el('synthEffects').value = '23:0.3,25:0.58,26:0.7';
el('validitySwitch').checked = true;
el('validityDimensions').value = '22+23+24+26,25+27+28';
api.synthExtras = { constraints: [], sigItems: [23, 25, 26], seedSearch: 80, alphaMin: 0.75, kmoMin: 0.60 };
const r2 = api.resetStaleSynthPanel(global.window.__wjxQuestions);
check('不判定陈旧', r2 === null, r2);
check('因变量保留', el('synthOutcomeQ').value === '21');
check('没有弹提示', alerts.length === 0);

// ========== 用例 3：全新问卷（无残留）不误伤 ==========
console.log('\n[用例3] 全新用户、面板全默认 -> 不动');
resetPanel();
setQuestions([
    { id: '1', type: '单选题', title: '1、性别', options: [] },
    { id: '2', type: '量表题', title: '2、满意度', options: [] }
]);
const r3 = api.resetStaleSynthPanel(global.window.__wjxQuestions);
check('不判定陈旧', r3 === null, r3);

// ========== 用例 4：synthMissingRefs / filterSynthConstraints ==========
console.log('\n[用例4] synthMissingRefs 识别幽灵题号');
setQuestions([
    { id: '5', type: '矩阵题', title: '5、矩阵', options: [{ rowIndex: 0, rowText: '第一行' }, { rowIndex: 1, rowText: '第二行' }] },
    { id: '6', type: '单选题', title: '6、生源地', options: [] }
]);
const miss = api.synthMissingRefs({
    outcome: { question: 21 },
    predictors: [6, 9, 11, 23],
    dimensions: [{ name: 'd', items: [501, 502] }],
    effects: { '23': 0.3, '26': 0.7 },
    targets: { sigItems: [25] },
    constraints: [{ when: { '16': [1] }, then: { '18': [4] } }]
}, global.window.__wjxQuestions);
check('矩阵伪键 501/502 不算缺失', miss.indexOf(501) < 0 && miss.indexOf(502) < 0, JSON.stringify(miss));
check('Q21/9/11/23/26/25 都被识别', [21, 9, 11, 23, 26, 25].every(v => miss.indexOf(v) >= 0), JSON.stringify(miss));
const kept = api.filterSynthConstraints({
    constraints: [
        { when: { '2': [0] }, then: { '8': [1] } },
        { when: { '6': [0] }, then: { '501': [1] } }
    ]
}, global.window.__wjxQuestions);
check('constraints 只保留合法的', kept.length === 1 && kept[0].when['6'] !== undefined, JSON.stringify(kept));

// ========== 用例 5：buildAiPrompt 不渲染幽灵题号 ==========
console.log('\n[用例5] buildAiPrompt 过滤幽灵题号');
setQuestions([
    { id: '1', type: '单选题', title: '1、传世武器', options: [] },
    { id: '2', type: '多选题', title: '2、段位', options: [] },
    { id: '3', type: '下拉框', title: '3、请做出你的选择', options: [] },
    { id: '4', type: '量表题', title: '4、给软件打几分', options: [] },
    { id: '5', type: '矩阵题', title: '5、给网页打几分', options: [] },
    { id: '6', type: '排序题', title: '6、网页与软件排序', options: [] },
    { id: '7', type: '填空题', title: '7、好的网页不比软件差', options: [] }
]);
const prompt = api.buildAiPrompt(global.window.__wjxQuestions, {
    enabled: true,
    outcome: { question: 21, positiveOption: [0, 1], label: 'Q21' },
    predictors: [6, 9, 11, 13, 14, 23, 25, 26],
    dimensions: [{ name: '维度1', items: [22, 23, 24, 26] }, { name: '维度2', items: [25, 27, 28] }],
    dimCorr: [0.42],
    targets: { alphaMin: 0.8, kmoMin: 0.65, sigTotal: [3, 4], sigItems: [23, 25, 26] },
    effects: { '23': 0.3, '25': 0.58 },
    constraints: [],
    seedSearch: 80
}, 1);
const wantedText = (prompt['本次要达成的验收指标（软件自测）'] || []).join('\n');
const pendingText = (prompt['需要用户拍板的事项（先读这段）'] || []).join('\n');
check('验收指标里没有 Q21', wantedText.indexOf('Q21') < 0, wantedText.slice(0, 200));
check('验收指标走「还没指定」分支', wantedText.indexOf('因变量：还没指定') >= 0);
check('维度分组段不出现（全幽灵）', wantedText.indexOf('维度分组') < 0);
check('targetCount=1 有提醒', pendingText.indexOf('targetCount 现在是 1') >= 0);
check('effects 被清后提示从零给出', (prompt['当前状态'] || []).join('').indexOf('当前 effects 是空的') >= 0);
check('合法题号清单仍是 7 题', Object.keys(prompt).some(k => k.indexOf('共 7 题') >= 0));

// ========== 用例 6：_guide 精简后结构完整 ==========
console.log('\n[用例6] _guide 结构与体积');
const guide = api.buildConfigGuide();
const guideKeys = Object.keys(guide);
console.log('  _guide 键: ' + guideKeys.join(' / '));
const guideSize = JSON.stringify(guide).length;
console.log('  _guide 体积: ' + guideSize + ' 字节（原 7359）');
check('保留 synth 字段速查', guideKeys.indexOf('synth 字段速查') >= 0);
check('保留题型对照', guideKeys.indexOf('题型与字段对照') >= 0);
check('保留最小示例', guideKeys.indexOf('最小示例') >= 0);
check('删掉了与 _prompt 重复的长段', guideKeys.indexOf('合成数据怎么配（交给 AI 时的必读段）') < 0 && guideKeys.indexOf('概率和填空题也要填（交给 AI 时的必读段）') < 0);
check('体积降到 5KB 以下', guideSize < 5000, String(guideSize));

// ========== 用例 7：buildSynthSpec 可用性判断 ==========
console.log('\n[用例7] buildSynthSpec 对「撑不起统计」的问卷写 _不可用原因');
// 7 题游戏问卷：1 量表 + 1 矩阵（1 行）= 2 个量表项 < 3
el('synthSwitch').checked = true;
el('validitySwitch').checked = true;
el('validityDimensions').value = '';
el('validitySingleQuestions').value = '';
el('validityTarget').value = '0.85';
const spec7 = api.buildSynthSpec([
    { id: '1', type: '单选题', title: '传世武器', options: [{ index: 0 }, { index: 1 }] },
    { id: '2', type: '多选题', title: '段位', options: [] },
    { id: '3', type: '下拉框', title: '请做出你的选择', options: [{ index: 0 }, { index: 1 }] },
    { id: '4', type: '量表题', title: '给软件打几分', options: [] },
    { id: '5', type: '矩阵题', title: '给网页打几分', options: [{ rowIndex: 0, rowText: '网页', options: [] }] },
    { id: '6', type: '排序题', title: '网页与软件排序', options: [] },
    { id: '7', type: '填空题', title: '好的网页不比软件差', options: [] }
]);
check('写出 _不可用原因', typeof spec7._不可用原因 === 'string' && spec7._不可用原因.length > 0, spec7._不可用原因);
check('enabled 强制为 false', spec7.enabled === false);
check('_toFill 不含 effects/outcome', !spec7._toFill.some(function (x) { return x.indexOf('effects') >= 0 || x.indexOf('outcome') >= 0; }));

// 28 题问卷：量表项充足，不写 _不可用原因
const spec28 = api.buildSynthSpec(Array.from({ length: 28 }, function (_, i) {
    return { id: String(i + 1), type: '单选题', title: (i + 1) + '、题', options: [{ index: 0 }, { index: 1 }] };
}));
// 28 题全是单选题但没点「单选量表题号」→ 量表项为 0，也应不可用；这里验证判据走的是量表项，而不是误判
check('28 题纯单选但未点名量表 → 量表项不足，写 _不可用原因', typeof spec28._不可用原因 === 'string');

// 带充足量表题的问卷：不写 _不可用原因，enabled 跟随 synthSwitch
el('validitySingleQuestions').value = '22-28';
const specScale = api.buildSynthSpec(Array.from({ length: 28 }, function (_, i) {
    const isScale = i >= 21;
    return { id: String(i + 1), type: isScale ? '量表题' : '单选题', title: (i + 1) + '、题', options: [{ index: 0 }, { index: 1 }] };
}));
check('量表题充足 → 不写 _不可用原因', specScale._不可用原因 === undefined, specScale._不可用原因);
check('enabled 跟随 synthSwitch(true)', specScale.enabled === true);

// enabled 跟随 synthSwitch(false)：关掉合成开关再导出
el('synthSwitch').checked = false;
const specOff = api.buildSynthSpec(Array.from({ length: 28 }, function (_, i) {
    return { id: String(i + 1), type: i >= 21 ? '量表题' : '单选题', title: (i + 1) + '、题', options: [{ index: 0 }, { index: 1 }] };
}));
check('enabled 跟随 synthSwitch(false)', specOff.enabled === false);

// 不可用问卷的 _prompt 应降级为「只补概率」
el('synthSwitch').checked = true;
const unusablePrompt = api.buildAiPrompt([
    { id: '1', type: '单选题', title: '传世武器', options: [] }
], spec7, 1);
check('unusable 提示词降级（不要求显著）',
    (unusablePrompt['本次要达成的验收指标（软件自测）'] || []).join('').indexOf('不做信效度分析和回归') >= 0);

console.log('\n========== 结果: ' + pass + ' 通过, ' + fail + ' 失败 ==========');
process.exit(fail ? 1 : 0);
