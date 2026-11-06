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
