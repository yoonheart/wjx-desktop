// 右上角消息提示：实心语义色底 + 白色文字 + 图标 + 关闭按钮，自动消失
// 视觉全部由 app.css 的 .notice-stack / .notice 定义，这里只管挂载与生命周期
// 注意：类名不用 toast —— Bootstrap 的 .toast:not(.show) 会把元素整个 display:none

const ALERT_ICONS = {
    success: 'fa-check-circle',
    error: 'fa-times-circle',
    info: 'fa-info-circle',
    warning: 'fa-exclamation-triangle'
};

// 自动消失时长（毫秒）
const ALERT_DURATION = 4500;
// 同时最多保留的提示条数，超出时挤掉最旧的一条
const ALERT_MAX = 4;
// 退场动画时长，需与 .toast 的 transition-duration 对齐
const ALERT_LEAVE_MS = 240;

function getToastStack() {
    let stack = document.getElementById('noticeStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'noticeStack';
        stack.className = 'notice-stack';
        document.body.appendChild(stack);
    }
    return stack;
}

function showAlert(message, type = 'error') {
    const stack = getToastStack();
    const kind = ALERT_ICONS[type] ? type : 'info';
    const text = String(message == null ? '' : message);

    // 允许同一条消息重复出现：不做去重，直接往下追加

    // 超出上限时挤掉最旧的一条（最上面那条）
    while (stack.children.length >= ALERT_MAX) {
        stack.firstElementChild.remove();
    }

    const el = document.createElement('div');
    el.className = 'notice is-' + kind;
    el.dataset.message = text;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');

    const icon = document.createElement('i');
    icon.className = 'fa ' + ALERT_ICONS[kind] + ' notice-icon';
    icon.setAttribute('aria-hidden', 'true');

    const msg = document.createElement('div');
    msg.className = 'notice-msg';
    // 用 textContent 而非 innerHTML：消息里可能含换行，交给 CSS 的 pre-line 处理
    msg.textContent = text;

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'notice-close';
    closeBtn.setAttribute('aria-label', '关闭提示');
    closeBtn.innerHTML = '&times;';

    el.appendChild(icon);
    el.appendChild(msg);
    el.appendChild(closeBtn);

    let timer = null;
    let dismissed = false;

    function dismiss() {
        if (dismissed) return;
        dismissed = true;
        if (timer) clearTimeout(timer);
        el.classList.remove('is-visible');
        el.classList.add('is-leaving');
        setTimeout(() => el.remove(), ALERT_LEAVE_MS);
    }

    closeBtn.addEventListener('click', dismiss);

    // 按时间顺序往下排：新的追加在最下面
    stack.appendChild(el);
    // 下一帧再加可见类，保证进场过渡能触发
    requestAnimationFrame(() => el.classList.add('is-visible'));

    timer = setTimeout(dismiss, ALERT_DURATION);
}

// 导出函数（如果需要模块使用）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { showAlert };
}
