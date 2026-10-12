/**
 * 统一确认对话框（软件自带样式）+ 桌面端「关闭窗口」确认钩子。
 *
 * <p>页面内原本各有一份 askConfirm（analysis.js）。这里收敛为唯一实现（后加载覆盖），
 * 并额外提供桌面端关窗钩子 window.__wjxConfirmCloseSoftware —— 由桌面启动器
 * DesktopLauncher 在用户点击窗口关闭按钮（×）且正在刷问卷时，通过
 * executeJavaScript 调用；用户点「关闭」后再通知后端强杀正在刷的任务并退出软件。</p>
 */
(function () {
    'use strict';

    /**
     * 自定义确认对话框，替代阻塞式 window.confirm。
     * @param {Object}  opts
     * @param {string}  [opts.title]      标题
     * @param {Array}   [opts.rows]       参数摘要 [[标签, 值], ...]
     * @param {string}  [opts.message]    提示正文
     * @param {string}  [opts.okText]     确认按钮文案
     * @param {string}  [opts.cancelText] 取消按钮文案
     * @param {boolean} [opts.danger]     确认按钮是否用危险样式
     * @param {boolean} [opts.reverse]    是否把确认按钮放到左侧（与取消按钮对调）
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
        cancelBtn.textContent = opts.cancelText || '取消';

        const footer = modal.querySelector('.modal-footer');
        if (footer) footer.classList.toggle('is-reverse', !!opts.reverse);

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

    // 暴露到全局：页面其它脚本按 askConfirm(...) 调用
    window.askConfirm = askConfirm;

    /**
     * 桌面端关窗钩子：由 DesktopLauncher 在用户点 × 且正在刷问卷时调用。
     * 弹出软件自带的确认框；点「关闭」后再通知后端强杀正在刷的任务并退出软件。
     */
    window.__wjxConfirmCloseSoftware = function () {
        askConfirm({
            title: '关闭软件',
            message: '当前正在刷问卷，关闭软件将自动停止刷问卷。确定要关闭吗？',
            okText: '关闭',
            danger: true,
            reverse: true
        }).then(function (ok) {
            if (!ok) {
                return;
            }
            // 请求发出后软件随即退出，无需处理响应
            fetch('/api/desktop/close', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            }).catch(function () { /* 忽略 */ });
        });
    };
})();
