/* 全量加载前端脚本：专门抓「调用了但没定义」这类 ReferenceError。
 *
 * 之前 syncProxyControls 只写了调用、没写定义，解析问卷时整页报错；
 * 只加载三个脚本的冒烟测试抓不到，必须把 analysis.js 也真跑一遍。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
let html = fs.readFileSync(path.join(root, 'frontend', 'analysis.html'), 'utf8');
html = html.replace(/<script src="\/static\/js\/[^"]+"><\/script>/g, '');

const dom = new JSDOM(html, {
    url: 'http://127.0.0.1/analysis?url=https%3A%2F%2Fv.wjx.cn%2Fvm%2Fec5KcaY.aspx',
    runScripts: 'outside-only'
});
const win = dom.window;
win.fetch = () => new Promise(() => {});
win.alert = () => {};
win.showAlert = () => {};

const errors = [];
win.addEventListener('error', (event) => errors.push(String(event.message || event)));

const files = [
    'request.js',
    'api.js',
    'alert.js',
    'probability-validator.js',
    'answers-csv.js',
    'goal-panel.js',
    'analysis.js',
    'confirm.js'
];

for (const name of files) {
    const code = fs.readFileSync(path.join(root, 'frontend', 'js', name), 'utf8');
    try {
        win.eval(code);
    } catch (error) {
        console.error('加载失败 ' + name + '：' + error.message);
        process.exit(1);
    }
}


// 进度面板复位：上一轮结束留下的「返回配置/红色标题」必须被清掉
if (typeof win.resetProgressPanel !== 'function') {
    console.error('resetProgressPanel 未定义');
    process.exit(1);
}
const title = win.document.getElementById('progressTitle');
const bar = win.document.getElementById('progressBar');
const btn = win.document.getElementById('stopButton');
title.className = 'progress-head is-failed';
title.textContent = '任务失败';
bar.className = 'progress-fill is-failed';
bar.style.width = '24%';
btn.className = 'btn btn-primary';
btn.textContent = '返回配置';
win.document.getElementById('completedCount').textContent = '12';
win.document.getElementById('percentDisplay').textContent = '24';

win.resetProgressPanel();

[
    [title.className === 'progress-head', '标题还带着 is-failed：' + title.className],
    [bar.className === 'progress-fill', '进度条还带着 is-failed：' + bar.className],
    [bar.style.width === '0%', '进度条没归零：' + bar.style.width],
    [btn.className === 'btn btn-danger', '停止按钮样式没复位：' + btn.className],
    [btn.textContent.indexOf('停止任务') >= 0, '停止按钮文案没复位：' + btn.textContent],
    [typeof btn.onclick === 'function', '停止按钮没绑回停止动作'],
    [win.document.getElementById('completedCount').textContent === '0', '已完成计数没清零'],
    [win.document.getElementById('percentDisplay').textContent === '0', '百分比没清零']
].forEach(function (item) {
    if (!item[0]) {
        console.error(item[1]);
        process.exit(1);
    }
});

if (errors.length) {
    console.error('加载期间捕获到错误：' + errors.slice(0, 3).join(' | '));
    process.exit(1);
}
if (typeof win.syncProxyControls !== 'function') {
    console.error('syncProxyControls 未定义');
    process.exit(1);
}

console.log('OK: 全部前端脚本加载通过');
win.close();
process.exit(0);
