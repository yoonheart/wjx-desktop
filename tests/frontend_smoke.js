/* 前端冒烟测试：把面板脚本真正跑一遍，抓 TDZ / 渲染异常 / 导入不生效。
 * 用 jsdom 造出最小题目 DOM，再执行 analysis.html 引用的三个脚本。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
let html = fs.readFileSync(path.join(root, 'frontend', 'analysis.html'), 'utf8');
html = html.replace(/<script src="\/static\/js\/[^"]+"><\/script>/g, '');

const dom = new JSDOM(html, {
    url: 'http://127.0.0.1/analysis?vm=TEST',
    runScripts: 'outside-only'
});
const win = dom.window;
const doc = win.document;
const errors = [];

win.fetch = () => new Promise(() => {});
win.showAlert = () => {};
win.alert = () => {};
win.toast = () => {};
const textStore = new Map();
win.WjxTextAnswers = {
    get: (k) => textStore.get(k) || [],
    set: (k, v) => textStore.set(k, v),
    refresh: () => {}
};
win.addEventListener('error', (e) => errors.push(String(e.message || e)));
win.addEventListener('unhandledrejection', (e) => errors.push(String(e.reason || e)));

function el(tag, cls, attrs, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, String(v)));
    if (text !== undefined) node.textContent = text;
    return node;
}

function addQuestions() {
    const host = doc.createElement('div');
    host.id = 'questionHost';
    doc.body.appendChild(host);

    // 第 4 题：量表题（可当结局）
    const q4 = el('div', 'question-container', { 'data-question-id': '4' });
    q4.appendChild(el('div', 'question-type', {}, '量表题'));
    q4.appendChild(el('div', 'question-content', {}, '总体满意度'));
    for (let i = 1; i <= 5; i += 1) {
        const opt = el('div', 'option-item');
        opt.appendChild(el('span', 'option-text', {}, 'O' + i));
        q4.appendChild(opt);
        const input = el('input', 'probability-input', {
            'data-question-id': '4', 'data-option-index': i - 1, value: '20'
        });
        q4.appendChild(input);
    }
    host.appendChild(q4);

    // 第 5 题：矩阵题（5 行 × 5 列），影响因素用行号 501..505
    const q5 = el('div', 'question-container', { 'data-question-id': '5' });
    q5.appendChild(el('div', 'question-type', {}, '矩阵题'));
    q5.appendChild(el('div', 'question-content', {}, '满意度'));
    const table = el('table', 'matrix-table');
    const tbody = doc.createElement('tbody');
    for (let r = 1; r <= 5; r += 1) {
        const tr = doc.createElement('tr');
        tr.appendChild(el('td', '', {}, '行' + r));
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    q5.appendChild(table);
    for (let r = 0; r < 5; r += 1) {
        for (let c = 0; c < 5; c += 1) {
            q5.appendChild(el('input', 'probability-input', {
                'data-question-id': '5', 'data-row-index': r,
                'data-col-index': c, value: '20'
            }));
        }
    }
    host.appendChild(q5);
}

addQuestions();

const scripts = ['probability-validator.js', 'answers-csv.js', 'goal-panel.js'];
scripts.forEach((name) => {
    const code = fs.readFileSync(path.join(root, 'frontend', 'js', name), 'utf8');
    try {
        win.eval(code);
    } catch (error) {
        errors.push(name + ': ' + error.message);
    }
});

function assert(condition, message) {
    if (!condition) {
        throw new Error(message);
    }
}

assert(typeof win.GoalPanel === 'object' && win.GoalPanel, 'GoalPanel 未暴露');
assert(typeof win.AnswersCsv === 'object' && win.AnswersCsv, 'AnswersCsv 未暴露');

// 关键：render 不能抛异常（上一轮的 TDZ 错误就是死在这里）
win.GoalPanel.render(true);
win.GoalPanel.render(false);
const status = doc.getElementById('goalStatus');
assert(!status || !/渲染出错/.test(status.textContent),
    'render 报错：' + (status && status.textContent));
assert(errors.length === 0, '脚本异常：' + errors.join(' | '));

const csv = [
    '# 启用,是',
    '# 信度α,0.85',
    '# 效度KMO,0.70',
    '# 至少显著,2',
    '# 自动分维,是',
    '# 维度,501,502,503,504,505|4',
    '# 构念,0=[501,502,503,504,505];1=[4]',
    '# 相关,0-1:+0.60',
    '# 反向,',
    '# 效应量,0.45',
    '# 有序题,',
    '# 调节,',
    '# 结局题,4',
    '# 结局项,4',
    '# 结局合成,single',
    '# 算作正面,',
    '# 影响因素,501:+,502:+,503:+,504:+,505:+',
    '题号,题目,选项序号,选项文字,权重,题型',
    '4,总体满意度,1,O1,30,量表题',
    '4,总体满意度,2,O2,40,量表题',
    '4,总体满意度,3,O3,30,量表题',
    '5,满意度,1.1,O1,30,矩阵题',
    '5,满意度,1.2,O2,40,矩阵题',
    '5,满意度,1.3,O3,30,矩阵题',
    '7,建议,-,界面还可以再简洁,1,填空题',
    '7,建议,-,希望增加深色模式,1,填空题',
    '1,使用时长,o2,其他请说明,1,单选题',
    ''
].join('\n');

const result = win.AnswersCsv.applyCsv(csv);
assert(result.updated > 0, '没有任何权重被应用');
assert(doc.getElementById('goalOutcome').value === '4', '结局项没有写入');
assert(doc.querySelector('#goalFactors input[data-qid="501"]').checked,
    '影响因素 501 没有被勾上');

// 文本框：整题填空 '-' 与选项内填空 'oN' 都要写回文本答案池
const blankPool = textStore.get('7_0') || [];
assert(blankPool.length === 2 && blankPool[0].text === '界面还可以再简洁',
    '填空题文本池没有写入');
const optPool = textStore.get('1_1') || [];
assert(optPool.length === 1 && optPool[0].text === '其他请说明',
    '选项内填空没有写入');

// 再渲染一次：勾选不能被定时重渲染冲掉
win.GoalPanel.render(false);
assert(doc.getElementById('goalOutcome').value === '4', '重渲染后结局丢失');
assert(doc.querySelector('#goalFactors input[data-qid="501"]').checked,
    '重渲染后因素勾选丢失');
assert(errors.length === 0, '导入后产生脚本异常：' + errors.join(' | '));

console.log('OK: render 无异常、导入生效、重渲染不丢勾选');
win.close();
