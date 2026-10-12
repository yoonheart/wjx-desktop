/**
 * 网址输入框历史记录下拉。
 *
 * <p>点击输入框向下弹出历史记录（<b>无记录时不弹出</b>），支持删除单条 / 清空全部；
 * 点击某条记录会直接填入输入框并自动开始解析。输入框有内容时，右侧显示
 * 圆形清除按钮，点击可一键清空输入框。记录保存在浏览器本地 localStorage
 * （key: wjxUrlHistory），最多保留 MAX_ITEMS 条，重复网址自动去重置顶。</p>
 *
 * <p>依赖页面元素：#wjxUrl（输入框）、#urlHistory（下拉容器）、
 * #urlHistoryList（列表）、#urlHistoryClear（清空按钮）、
 * #urlClear（输入框清除按钮）、#analyzeBtn（解析按钮）。</p>
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'wjxUrlHistory';
    var MAX_ITEMS = 10;

    var input = document.getElementById('wjxUrl');
    var box = document.getElementById('urlHistory');
    var listEl = document.getElementById('urlHistoryList');
    var clearBtn = document.getElementById('urlHistoryClear');
    var inputClearBtn = document.getElementById('urlClear');
    var analyzeBtn = document.getElementById('analyzeBtn');

    // 页面缺少必要节点时静默退出（例如解析页没有网址输入框）
    if (!input || !box || !listEl) {
        return;
    }

    /** 读取历史（过滤非法项） */
    function load() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            var arr = raw ? JSON.parse(raw) : [];
            if (!Array.isArray(arr)) {
                return [];
            }
            return arr.filter(function (u) {
                return typeof u === 'string' && u.trim() !== '';
            });
        } catch (e) {
            return [];
        }
    }

    /** 写回历史 */
    function save(arr) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(arr));
        } catch (e) {
            // localStorage 不可用（隐私模式/配额满）时忽略
        }
    }

    /** 新增一条（去重后置顶）。仅在链接合法时记录；超过 MAX_ITEMS 时丢弃最早的 */
    function add(url) {
        url = (url || '').trim();
        if (!url) {
            return;
        }
        if (typeof window.isWjxUrl === 'function' && !window.isWjxUrl(url)) {
            return;
        }
        var arr = load().filter(function (u) { return u !== url; });
        arr.unshift(url);
        if (arr.length > MAX_ITEMS) {
            arr = arr.slice(0, MAX_ITEMS);
        }
        save(arr);
    }

    /** 删除一条 */
    function remove(url) {
        save(load().filter(function (u) { return u !== url; }));
    }

    /** 清空全部 */
    function clearAll() {
        save([]);
    }

    /** 同步输入框清除按钮的显隐：有内容才显示 */
    function syncClearBtn() {
        if (!inputClearBtn) {
            return;
        }
        if (input.value && input.value.trim() !== '') {
            inputClearBtn.classList.remove('is-hidden');
        } else {
            inputClearBtn.classList.add('is-hidden');
        }
    }

    /** 渲染列表 */
    function render() {
        var arr = load();
        listEl.innerHTML = '';
        arr.forEach(function (url) {
            var li = document.createElement('li');
            li.className = 'url-history-item';

            var text = document.createElement('span');
            text.className = 'url-history-url';
            text.textContent = url;
            text.title = url;

            var del = document.createElement('button');
            del.type = 'button';
            del.className = 'url-history-del';
            del.setAttribute('aria-label', '删除该记录');
            del.innerHTML = '&times;';

            // 点击记录：填入并自动开始解析
            text.addEventListener('click', function () {
                input.value = url;
                syncClearBtn();
                hide();
                if (analyzeBtn && !analyzeBtn.disabled) {
                    analyzeBtn.click();
                }
            });
            // 删除单条（阻止冒泡，避免触发「填入解析」）
            del.addEventListener('click', function (e) {
                e.stopPropagation();
                remove(url);
                render();
            });

            li.appendChild(text);
            li.appendChild(del);
            listEl.appendChild(li);
        });
    }

    /** 展开下拉：无历史记录时不弹出 */
    function show() {
        if (load().length === 0) {
            hide();
            return;
        }
        render();
        box.classList.remove('is-hidden');
    }

    function hide() {
        box.classList.add('is-hidden');
    }

    // 点击/聚焦输入框即弹出（无记录则保持收起）
    input.addEventListener('click', show);
    input.addEventListener('focus', show);

    // 输入框内容变化时同步清除按钮
    input.addEventListener('input', syncClearBtn);

    // 输入框内的圆形清除按钮：清空内容并聚焦
    if (inputClearBtn) {
        inputClearBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            input.value = '';
            syncClearBtn();
            hide();
            input.focus();
        });
    }

    // 清空全部：清完没有记录，直接收起
    if (clearBtn) {
        clearBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            clearAll();
            render();
            hide();
        });
    }

    // 回车 / Esc 收起
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === 'Escape') {
            hide();
        }
    });

    // 点击其它区域收起
    document.addEventListener('click', function (e) {
        if (e.target !== input && !box.contains(e.target)) {
            hide();
        }
    });

    // 每次发起解析时记录当前链接（点击按钮或回车都会走到这里）
    if (analyzeBtn) {
        analyzeBtn.addEventListener('click', function () {
            add(input.value);
        });
    }

    // 初始化一次，确保刷新后输入框有值时也显示清除按钮
    syncClearBtn();
})();
