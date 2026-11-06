/**
 * 数据目标：只做「绑定 + 取值」，不再往页面里插 DOM。
 *
 * 面板 HTML 直接写在 analysis.html 里（#goalEnabled 那一组），这里负责：
 * 1. 用解析出来的题目填充下拉框和影响因素列表；
 * 2. 把值同步到老代码仍在读的隐藏字段（保持兼容，避免动 analysis.js）；
 * 3. 对外提供 payload()（新的 goal 结构）和体检报告渲染。
 */

(function () {
    'use strict';

    function $(id) {
        return document.getElementById(id);
    }

    /** 老代码还在读这些 id，缺了就补一批隐藏字段，保证不报错。 */
    function ensureShims() {
        if ($('validitySwitch')) {
            return;
        }
        const holder = document.createElement('div');
        holder.style.display = 'none';
        holder.innerHTML = ''
            + '<input type="checkbox" id="validitySwitch">'
            + '<input id="validityTarget">'
            + '<input id="validityDimensions">'
            + '<input id="validitySingleQuestions">'
            + '<input id="validityCorrelation">'
            + '<input id="validityReverse">'
            + '<input type="checkbox" id="synthSwitch">'
            + '<input id="synthOutcomeQ">'
            + '<input id="synthPositive">'
            + '<input id="synthPredictors">'
            + '<input id="synthEffects">'
            + '<input id="synthSigLo">'
            + '<input id="synthSigHi">'
            + '<input id="goalEffect">'
            + '<input id="goalDefaultCorr">'
            + '<input id="goalReverseValue">';
        document.body.appendChild(holder);
    }

    /** 开关打开才显示下面几块配置。 */

    // ---- 按问卷隔离的持久化 ----------------------------------------------
    // 存 SQLite（/api/state），不同问卷用不同 key，互不干扰；
    // 后端不可用时退回 localStorage，至少本次会话不丢。

    function surveyToken() {
        const raw = String(location.search || '') + String(location.hash || '');
        let decoded = raw;
        try {
            decoded = decodeURIComponent(raw);
        } catch (error) {
            decoded = raw;
        }
        const match = decoded.match(/vm\/([A-Za-z0-9]+)/)
            || decoded.match(/shortid=([A-Za-z0-9]+)/);
        return match ? match[1] : 'unknown';
    }

    /** 老版本把配置都存进了 goal:unknown；读不到本问卷的桶时回退读它一次。 */
    function legacyKey() {
        return storageKey() === 'goal:unknown' ? '' : 'goal:unknown';
    }

    function storageKey() {
        return 'goal:' + surveyToken();
    }

    let saveTimer = null;

    function scheduleSave() {
        if (saveTimer) {
            clearTimeout(saveTimer);
        }
        saveTimer = setTimeout(saveNow, 600);
    }

    function snapshot() {
        const data = payload();
        const factors = [];
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            if (box.checked) {
                const qid = box.dataset.qid;
                const dir = (document.querySelector('#goalFactors select[data-dir="' + qid + '"]') || {}).value || '+';
                factors.push(qid + ':' + dir);
            }
        });
        return {
            enabled: Boolean(($('goalEnabled') || {}).checked),
            alpha: ($('goalAlpha') || {}).value || '0.86',
            kmo: ($('goalKmo') || {}).value || '0.75',
            sig: ($('goalSig') || {}).value || '0',
            autoDim: autoDimEnabled(),
            mode: ($('goalMode') || {}).value || 'full',
            outcome: ($('goalOutcome') || {}).value || '',
            mean: Boolean(($('goalOutcomeMean') || {}).checked),
            positive: ($('goalPositive') || {}).value || '',
            factors: factors,
            goal: data || null
        };
    }

    function saveNow() {
        const text = JSON.stringify(snapshot());
        try {
            localStorage.setItem(storageKey(), text);
        } catch (error) {
            console.warn('localStorage 写入失败:', error);
        }
        fetch('/api/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: { [storageKey()]: text } })
        }).catch(function () { /* 后端不可用时保持 localStorage 即可 */ });
    }

    function restore(state) {
        if (!state) {
            return;
        }
        const toggle = $('goalEnabled');
        if (toggle) {
            toggle.checked = Boolean(state.enabled);
        }
        const set = function (id, value) {
            const el = $(id);
            if (el && value !== undefined && value !== null && value !== '') {
                el.value = value;
            }
        };
        set('goalAlpha', state.alpha);
        set('goalKmo', state.kmo);
        set('goalSig', state.sig);
        const autoBox = $('goalAutoDim');
        if (autoBox && state.autoDim !== undefined) {
            autoBox.checked = Boolean(state.autoDim);
        }
        if (state.mode) {
            const modeBox = $('goalMode');
            if (modeBox) {
                modeBox.value = state.mode;
            }
        }
        // 先还原「合成」勾选，再触发结局变更：onchange 里会重新应用可见性
        const meanBox = $('goalOutcomeMean');
        if (meanBox) {
            meanBox.checked = Boolean(state.mean);
        }
        if (state.outcome) {
            const outcome = $('goalOutcome');
            if (outcome) {
                outcome.value = state.outcome;
                if (typeof outcome.onchange === 'function') {
                    outcome.onchange();
                }
            }
        }
        if (state.positive) {
            set('goalPositive', state.positive);
        }
        (state.factors || []).forEach(function (item) {
            const pair = String(item).split(':');
            const qid = pair[0];
            const dir = pair[1] || '+';
            const box = document.querySelector('#goalFactors input[data-qid="' + qid + '"]');
            const sel = document.querySelector('#goalFactors select[data-dir="' + qid + '"]');
            if (box) {
                box.checked = true;
            }
            if (sel) {
                sel.value = dir;
            }
        });
        toggleBody();
        setTimeout(loadSaved, 0);
    }

    function loadSaved() {
        let local = null;
        const keys = [storageKey(), legacyKey()].filter(Boolean);
        for (let i = 0; i < keys.length && !local; i += 1) {
            try {
                const raw = localStorage.getItem(keys[i]);
                local = raw ? JSON.parse(raw) : null;
            } catch (error) {
                local = null;
            }
        }
        if (local) {
            restore(local);
        }
        fetch('/api/state').then(function (r) { return r.json(); }).then(function (body) {
            const items = (body && body.data) || {};
            let raw = items[storageKey()];
            if (!raw && legacyKey()) {
                raw = items[legacyKey()];
            }
            if (!raw) {
                return;
            }
            const fromDb = JSON.parse(raw);
            // 数据库里的版本更新（例如上次是 Ctrl+C 强退），以它为准
            if (!local || JSON.stringify(fromDb) !== JSON.stringify(local)) {
                restore(fromDb);
            }
        }).catch(function () { /* 忽略 */ });
    }

    function setHidden(id, hidden) {
        const el = $(id);
        if (el) {
            el.classList.toggle('is-hidden', hidden);
        }
    }

    /** 按「分析类型」只露出该用的那几块，避免一屏全是配置。 */
    function applyMode() {
        const on = Boolean(($('goalEnabled') || {}).checked);
        const mode = ($('goalMode') || {}).value || 'full';
        setHidden('goalBodyGroup', !on);
        setHidden('goalModeGroup', !on);
        setHidden('goalTargetGroup', !(on && mode !== 'basic'));
        setHidden('goalRelationGroup', !(on && mode === 'full'));
        setHidden('goalFactorGroup', !(on && mode !== 'basic'));
    }

    function toggleBody() {
        applyMode();
        sync();
    }

    /** 读取题目，矩阵题展开成每一行（行编号 = 题号*100+行号）。 */
    function questions() {
        const list = [];
        document.querySelectorAll('.question-container').forEach(function (container) {
            const id = container.dataset.questionId || '';
            if (!/^\d+$/.test(id)) {
                return;
            }
            const titleEl = container.querySelector('.question-content');
            const typeEl = container.querySelector('.question-type');
            const type = typeEl ? typeEl.textContent.trim() : '';
            const title = titleEl ? titleEl.textContent.trim().slice(0, 24) : '';
            const options = [];
            container.querySelectorAll('.option-item .option-text').forEach(function (el) {
                options.push(el.textContent.trim());
            });

            const isMatrix = type.indexOf('矩阵') >= 0;
            const rows = [];
            if (isMatrix) {
                container.querySelectorAll('.matrix-table tbody tr').forEach(function (tr, index) {
                    const labelEl = tr.querySelector('td');
                    const label = labelEl ? labelEl.textContent.trim() : ('行' + (index + 1));
                    rows.push({ key: String(Number(id) * 100 + (index + 1)), label: label });
                });
            }

            list.push({
                id: id,
                title: title,
                type: type,
                options: options,
                isMatrix: isMatrix,
                rows: rows
            });
        });
        return list;
    }

    let lastSignature = '';

    function signature(list) {
        return list.map(function (q) { return q.id; }).join(',');
    }

    /** 当前选中的结局：可能是整题，也可能是矩阵的某一行。 */
    function selectedOutcome() {
        const value = ($('goalOutcome') || {}).value || '';
        if (!value) {
            return null;
        }
        const list = questions();
        for (let i = 0; i < list.length; i += 1) {
            const q = list[i];
            if (q.isMatrix) {
                const row = q.rows.filter(function (r) { return r.key === value; })[0];
                if (row) {
                    // 矩阵行与量表都是连续取值；只有普通单选题才做二分类
                    return { question: q, row: row, value: value, continuous: true };
                }
            } else if (q.id === value) {
                return {
                    question: q,
                    row: null,
                    value: value,
                    continuous: q.type.indexOf('量表') >= 0
                };
            }
        }
        return null;
    }

    /** 结局项目编号列表：勾了「合并整题各行」就把该矩阵的每一行都算进来。 */
    function outcomeItems() {
        const picked = selectedOutcome();
        if (!picked) {
            return [];
        }
        const mean = Boolean(($('goalOutcomeMean') || {}).checked);
        if (picked.row && mean) {
            return picked.question.rows.map(function (r) { return Number(r.key); });
        }
        return [Number(picked.value)];
    }

    function outcomeAggregate() {
        return outcomeItems().length > 1 ? 'mean' : 'single';
    }

    /** "4,10" → [4, 10] */
    function parseRefList(text) {
        return String(text || '').split(/[,\s、;]+/)
            .map(function (x) { return parseInt(x, 10); })
            .filter(function (n) { return n > 0; });
    }

    /** "501*4:0.30" → {focal:501, moderator:4, effect:0.3} */
    function parseModeration(text) {
        const part = String(text || '').trim();
        if (!part) {
            return {};
        }
        const cut = part.lastIndexOf(':');
        if (cut <= 0) {
            return {};
        }
        const pair = part.slice(0, cut).split('*');
        const effect = parseFloat(part.slice(cut + 1));
        if (pair.length !== 2) {
            return {};
        }
        const focal = parseInt(pair[0], 10);
        const moderator = parseInt(pair[1], 10);
        if (!focal || !moderator || !isFinite(effect)) {
            return {};
        }
        return { focal: focal, moderator: moderator, effect: effect };
    }

    /** 自动分维：每道矩阵题一维、每道量表题一维（结局项已排除）。 */
    function autoDimensionGroups(exclude) {
        const groups = [];
        questions().forEach(function (q) {
            if (q.isMatrix) {
                const rows = q.rows
                    .map(function (r) { return Number(r.key); })
                    .filter(function (n) { return !exclude[n]; });
                if (rows.length) {
                    groups.push(rows);
                }
            } else if (q.type.indexOf('量表') >= 0 && !exclude[Number(q.id)]) {
                groups.push([Number(q.id)]);
            }
        });
        // 有序题（如使用时长）单独成维，否则界面维度数会和后端对不上
        parseRefList(($('goalOrdinal') || {}).value || '').forEach(function (n) {
            if (!exclude[n]) {
                groups.push([n]);
            }
        });
        return groups;
    }

    function autoDimEnabled() {
        const box = $('goalAutoDim');
        return box ? Boolean(box.checked) : true;
    }

    /** "0-1:0.45;2-4:-0.3" → {"0-1": 0.45, "2-4": -0.3} */
    function parseCorrelations(text) {
        const out = {};
        String(text || '').split(/[;；]/).forEach(function (chunk) {
            const part = chunk.trim();
            if (!part) {
                return;
            }
            const cut = part.lastIndexOf(':');
            if (cut <= 0) {
                return;
            }
            const key = part.slice(0, cut).trim().replace(/[,_，]/g, '-');
            const value = parseFloat(part.slice(cut + 1));
            if (!isFinite(value)) {
                return;
            }
            out[key] = Math.max(-0.98, Math.min(0.98, value));
        });
        return out;
    }

    function fillSelect(select, items, placeholder) {
        if (!select) {
            return;
        }
        const previous = select.value;
        select.innerHTML = '';
        const first = document.createElement('option');
        first.value = '';
        first.textContent = placeholder;
        select.appendChild(first);
        items.forEach(function (item) {
            const option = document.createElement('option');
            option.value = String(item.value);
            option.textContent = item.label;
            select.appendChild(option);
        });
        if (previous && items.some(function (item) { return String(item.value) === previous; })) {
            select.value = previous;
        }
    }

    function render(force) {
        try {
            renderInner(force);
        } catch (error) {
            const status = $('goalStatus');
            if (status) {
                status.textContent = '配置面板渲染出错：' + (error && error.message ? error.message : error);
                status.style.color = '#b02a37';
            }
            if (window.console) {
                console.error('[goal-panel] render 失败', error);
            }
        }
    }

    function renderInner(force) {
        const list = questions();
        const sig = signature(list) + '|' + list.map(function (q) { return q.rows.length; }).join(',');
        if (!force && sig === lastSignature) {
            return;
        }
        lastSignature = sig;

        // 重建前先记住当前选择：面板每 1.5 秒会重渲染一次，
        // 不还原的话，导入或手选的因素会被下一次重渲染全部冲掉。
        const keepOutcome = ($('goalOutcome') || {}).value || '';
        const keepPositive = ($('goalPositive') || {}).value || '';
        const keepFactors = [];
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            if (box.checked) {
                const sel = document.querySelector('#goalFactors select[data-dir="' + box.dataset.qid + '"]');
                keepFactors.push([box.dataset.qid, (sel && sel.value) || '+']);
            }
        });

        // 结局可以是：单选题、量表题，或矩阵题的任意一行
        // （整道矩阵不行——一题多行=多个变量，结局必须收成一个数）
        const outcomeItems = [];
        list.forEach(function (q) {
            if (q.isMatrix) {
                q.rows.forEach(function (row) {
                    outcomeItems.push({
                        value: row.key,
                        label: '第' + q.id + '题(' + row.label + ')'
                    });
                });
                return;
            }
            if (q.type.indexOf('单选题') >= 0 || q.type.indexOf('量表') >= 0) {
                outcomeItems.push({ value: q.id, label: '第' + q.id + '题 ' + q.title });
            }
        });
        fillSelect($('goalOutcome'), outcomeItems, '请选择题目');

        // 结局题没选时，正面选项也要有占位符
        fillSelect($('goalPositive'), [], '请选择选项');

        const factors = $('goalFactors');
        if (factors) {
            factors.innerHTML = '';
            list.forEach(function (q) {
                if (q.isMatrix) {
                    // 矩阵：每一行都能单独选方向
                    q.rows.forEach(function (row) {
                        factors.appendChild(factorRow(row.key, '第' + q.id + '题(' + row.label + ') '));
                    });
                    return;
                }
                if (q.type.indexOf('量表') >= 0) {
                    factors.appendChild(factorRow(q.id, '第' + q.id + '题 ' + q.title));
                }
            });
            factors.querySelectorAll('input[data-qid]').forEach(function (box) {
                box.addEventListener('change', sync);
            });
            factors.querySelectorAll('select[data-dir]').forEach(function (sel) {
                sel.addEventListener('change', sync);
            });
        }

        // 还原结局与因素（outcome 必须先声明，否则 TDZ 报错）
        const outcome = $('goalOutcome');
        if (outcome && keepOutcome) {
            const exists = Array.prototype.some.call(outcome.options, function (opt) {
                return opt.value === keepOutcome;
            });
            if (exists) {
                outcome.value = keepOutcome;
                if (typeof outcome.onchange === 'function') {
                    outcome.onchange();
                }
                if (keepPositive) {
                    const positive = $('goalPositive');
                    if (positive) {
                        positive.value = keepPositive;
                    }
                }
            }
        }
        keepFactors.forEach(function (pair) {
            const box = document.querySelector('#goalFactors input[data-qid="' + pair[0] + '"]');
            const sel = document.querySelector('#goalFactors select[data-dir="' + pair[0] + '"]');
            if (box) {
                box.checked = true;
            }
            if (sel) {
                sel.value = pair[1];
            }
        });

        if (outcome) {
            outcome.onchange = function () {
                const picked = selectedOutcome();
                const previous = ($('goalPositive') || {}).value || '';
                const options = picked && !picked.row ? picked.question.options : [];
                fillSelect($('goalPositive'), options.map(function (text, index) {
                    return { value: index + 1, label: (index + 1) + '.' + (text || '选项') };
                }), '请选择选项');
                const positive = $('goalPositive');
                if (positive && previous) {
                    positive.value = previous;
                }
                applyOutcomeMode();
                sync();
            };
        }

        const meanBox = $('goalOutcomeMean');
        if (meanBox) {
            meanBox.addEventListener('change', function () {
                applyOutcomeMode();
                sync();
            });
        }

        ['goalEnabled', 'goalAlpha', 'goalKmo', 'goalSig', 'goalAutoDim',
            'goalCorrelations', 'goalModeration', 'goalOrdinal', 'goalEffect',
            'goalReverse', 'goalMode'].forEach(function (id) {
            const el = $(id);
            if (el) {
                el.addEventListener('change', sync);
                el.addEventListener('input', sync);
            }
        });

        const toggle = $('goalEnabled');
        if (toggle) {
            toggle.addEventListener('change', toggleBody);
        }
        const modeSelect = $('goalMode');
        if (modeSelect) {
            modeSelect.addEventListener('change', function () {
                applyMode();
                sync();
            });
        }
        applyOutcomeMode();
        toggleBody();
    }

    /** 按结局类型切换可见控件：连续结局不需要「算作正面」，矩阵行才给「合并各行」。 */
    function applyOutcomeMode() {
        const picked = selectedOutcome();
        const positiveGroup = $('goalPositiveGroup');
        const meanLabel = $('goalOutcomeMeanLabel');
        const meanBox = $('goalOutcomeMean');
        const continuous = Boolean(picked && picked.continuous);
        if (positiveGroup) {
            positiveGroup.classList.toggle('is-hidden', continuous);
        }
        if (meanLabel) {
            meanLabel.classList.toggle('is-hidden', !(picked && picked.row));
        }
        if (continuous && meanBox) {
            // 单选题没有「行」的概念，避免留下一个勾着的孤儿状态
            if (!picked.row) {
                meanBox.checked = false;
            }
        }

        // 结局项不能同时当自变量（自己解释自己没有意义），直接禁用该行
        const used = {};
        outcomeItems().forEach(function (n) { used[String(n)] = true; });
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            const blocked = Boolean(used[box.dataset.qid]);
            if (blocked) {
                box.checked = false;
            }
            box.disabled = blocked;
            const row = box.closest('label');
            if (row) {
                row.style.opacity = blocked ? '0.45' : '';
            }
        });
    }

    /** 一行因素：勾选框 + 标签 + 方向。 */
    function factorRow(key, label) {
        const row = document.createElement('label');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:13px;'
            + 'white-space:nowrap;flex:0 0 auto;min-width:0';
        row.innerHTML = ''
            + '<input type="checkbox" data-qid="' + key + '">'
            + '<span style="min-width:190px">' + label + '</span>'
            + '<select data-dir="' + key + '" style="padding:3px 6px;border:1px solid #ddd;border-radius:5px">'
            + '<option value="+">正向</option><option value="-">负向</option><option value="0">不显著</option>'
            + '</select>';
        return row;
    }

    /** 采集当前配置，兼容老字段。 */
    /** 有序题只能是单选题：填了矩阵/量表题号会覆盖那道题的答案。 */
    function checkOrdinalInput() {
        const warn = $('goalOrdinalWarn');
        const input = $('goalOrdinal');
        if (!warn || !input) {
            return;
        }
        const values = parseRefList(input.value || '');
        if (!values.length) {
            warn.textContent = '';
            return;
        }
        const list = questions();
        const bad = values.filter(function (number) {
            const hit = list.filter(function (q) { return String(q.id) === String(number); })[0];
            return !hit || hit.type.indexOf('单选') < 0;
        });
        warn.textContent = bad.length
            ? '这些不是单选题，填了会打乱结构：' + bad.join('、')
            : '';
    }

    function sync() {
        scheduleSave();
        const enabled = Boolean(($('goalEnabled') || {}).checked);
        const alpha = parseFloat(($('goalAlpha') || {}).value) || 0.85;
        const kmo = parseFloat(($('goalKmo') || {}).value) || 0.70;
        const sig = parseInt(($('goalSig') || {}).value, 10) || 0;
        const items = outcomeItems();
        const picked = selectedOutcome();
        const positiveIdx = parseInt(($('goalPositive') || {}).value, 10) || 1;

        // 结局项不进维度：因变量不能算进自变量的 α
        const exclude = {};
        items.forEach(function (n) { exclude[n] = true; });
        const scaleIds = [];
        questions().forEach(function (q) {
            if (q.isMatrix) {
                q.rows.forEach(function (row) {
                    if (!exclude[Number(row.key)]) {
                        scaleIds.push(Number(row.key));
                    }
                });
            } else if (q.type.indexOf('量表') >= 0 && !exclude[Number(q.id)]) {
                scaleIds.push(parseInt(q.id, 10));
            }
        });
        const dimensions = autoDimEnabled()
            ? autoDimensionGroups(exclude)
            : (scaleIds.length ? [scaleIds] : []);

        const predictors = [];
        const directions = {};
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            if (!box.checked) {
                return;
            }
            const qid = box.dataset.qid;
            const dir = (document.querySelector('#goalFactors select[data-dir="' + qid + '"]') || {}).value || '+';
            predictors.push(parseInt(qid, 10));
            directions[qid] = dir;
        });

        // 同步给老代码读的隐藏字段
        const shim = function (id, value) {
            const el = $(id);
            if (el) {
                if (el.type === 'checkbox') {
                    el.checked = Boolean(value);
                } else {
                    el.value = String(value);
                }
            }
        };
        // 整题编号（如 4）直接用；矩阵行编号（如 901）才需要 /100 反推题号
        const legacyQuestion = items.length
            ? (items[0] >= 100 ? Math.floor(items[0] / 100) : items[0])
            : 0;
        shim('validitySwitch', enabled);
        shim('validityTarget', alpha.toFixed(2));
        shim('validityDimensions', dimensions.map(function (g) { return g.join('-'); }).join(';'));
        shim('validityReverse', '');
        shim('synthSwitch', enabled && items.length > 0 && predictors.length > 0);
        shim('synthOutcomeQ', legacyQuestion);
        shim('synthPositive', positiveIdx - 1);
        shim('synthPredictors', predictors.join(','));
        shim('synthEffects', predictors.map(function (q) {
            const dir = directions[String(q)];
            const weight = dir === '-' ? -0.5 : (dir === '0' ? 0 : 0.5);
            return q + ':' + weight;
        }).join(','));
        shim('synthSigLo', Math.max(0, sig));
        shim('synthSigHi', Math.max(sig, predictors.length));

        const summary = $('goalSummary');
        if (summary) {
            let outcomeText = ' · 未选结局题';
            if (picked) {
                outcomeText = ' · 结局第' + picked.question.id + '题'
                    + (picked.row ? '(' + picked.row.label + ')' : '')
                    + (items.length > 1 ? '（' + items.length + ' 行均值）' : '');
            }
            summary.textContent = enabled
                ? 'α ' + alpha.toFixed(2) + ' · KMO ' + kmo.toFixed(2)
                    + ' · ' + dimensions.length + ' 个维度'
                    + outcomeText
                    + ' · 因素 ' + predictors.length + ' 个'
                : '';
        }

        checkOrdinalInput();

        const constructsHint = $('goalConstructs');
        if (constructsHint) {
            constructsHint.textContent = dimensions.length
                ? '构念编号：' + dimensions.map(function (group, index) {
                    return index + '=[' + group.join(',') + ']';
                }).join('　')
                : '';
        }

        const hint = $('goalHint');
        if (hint) {
            if (!enabled) {
                hint.textContent = '未开启：按普通随机方式填写。';
            } else if (!items.length) {
                hint.textContent = '已开启：请选择结局题（不选则只保证信度，不构造结论）。';
            } else if (!predictors.length) {
                hint.textContent = '已开启：请勾选至少一个影响因素。';
            } else {
                const kind = picked && picked.continuous ? '连续（保序构造）' : '二分类（按占比切分）';
                hint.textContent = '已开启：'
                    + dimensions.length + ' 个维度各按 α=' + alpha.toFixed(2) + ' 生成，结局按'
                    + kind + '，' + predictors.length + ' 个影响因素按设定方向构造，至少 '
                    + sig + ' 项显著。' + (autoDimEnabled() ? '' : '（自动分维已关闭：所有题当一维）');
            }
        }
    }

    /** 给后端的新结构。 */
    function payload() {
        const enabled = Boolean(($('goalEnabled') || {}).checked);
        if (!enabled) {
            return null;
        }
        const items = outcomeItems();
        const exclude = {};
        items.forEach(function (n) { exclude[n] = true; });

        const scorable = [];
        questions().forEach(function (q) {
            if (q.isMatrix) {
                q.rows.forEach(function (row) {
                    if (!exclude[Number(row.key)]) {
                        scorable.push(Number(row.key));
                    }
                });
            } else if (q.type.indexOf('量表') >= 0 && !exclude[Number(q.id)]) {
                scorable.push(Number(q.id));
            }
        });

        const dimensions = autoDimEnabled()
            ? autoDimensionGroups(exclude)
            : (scorable.length ? [scorable] : []);

        const predictors = [];
        const directions = {};
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            if (!box.checked) {
                return;
            }
            const qid = box.dataset.qid;
            predictors.push(parseInt(qid, 10));
            directions[qid] = (document.querySelector('#goalFactors select[data-dir="' + qid + '"]') || {}).value || '+';
        });

        const picked = selectedOutcome();
        const positiveRaw = parseInt(($('goalPositive') || {}).value, 10) || 0;
        // 「只要信效度」模式：不下发结局与自变量，只做测量层
        const mode = ($('goalMode') || {}).value || 'full';
        const onlyReliability = mode === 'basic';
        const sentItems = onlyReliability ? [] : items;
        const sentPredictors = onlyReliability ? [] : predictors;
        const sentDirections = onlyReliability ? {} : directions;
        return {
            enabled: true,
            alpha_target: parseFloat(($('goalAlpha') || {}).value) || 0.85,
            dimensions: dimensions,
            reverse: parseRefList(($('goalReverse') || {}).value || ''),
            dimension_correlation: parseFloat(($('goalDefaultCorr') || {}).value) || 0.30,
            // 构念间相关（带符号）：键为 "0-1" 这种构念索引对，来自答题表
            dimension_correlations: parseCorrelations(
                ($('goalCorrelations') || {}).value || ''
            ),
            target_effect: parseFloat(($('goalEffect') || {}).value) || 0.45,
            ordinal_items: parseRefList(($('goalOrdinal') || {}).value || ''),
            moderation: parseModeration(($('goalModeration') || {}).value || ''),
            kmo_target: parseFloat(($('goalKmo') || {}).value) || 0.70,
            // 新字段：结局项目（可含矩阵行、可多项目合成）
            outcome_items: sentItems,
            outcome_aggregate: outcomeAggregate(),
            // 旧字段保留：其它模块只想知道「结局是哪道题」
            outcome_question: picked ? parseInt(picked.question.id, 10) : 0,
            positive_options: positiveRaw ? [positiveRaw] : [],
            predictors: sentPredictors,
            directions: sentDirections,
            significant_required: parseInt(($('goalSig') || {}).value, 10) || 0,
            event_rate: 0.5,
            attempts: 12
        };
    }

    /** 结果页的体检报告。 */
    /** 已按需求关闭：停止后不再展示信效度/体检信息。 */
    async function showGoalReport(taskId) {
        showGoalReportDisabled(taskId);
    }

    async function showGoalReportDisabled(taskId) {
        return;
        try {
            const response = await fetch('/api/tasks/' + encodeURIComponent(taskId));
            const body = await response.json();
            const goal = body && body.data && body.data.goal;
            if (!goal) {
                return;
            }
            let host = document.getElementById('goalReportBox');
            if (!host) {
                host = document.createElement('div');
                host.id = 'goalReportBox';
                host.style.cssText = 'margin-top:14px;padding:12px 16px;border-radius:10px;background:#f7f9ff;border:1px solid #e2e8ff;font-size:13px;line-height:1.8';
                const anchor = document.querySelector('.stop-reason') || document.getElementById('progressPanel');
                if (!anchor || !anchor.parentElement) {
                    return;
                }
                anchor.parentElement.appendChild(host);
            }
            const mark = function (ok) { return ok ? '✅' : '⚠️'; };
            const parts = ['<div style="font-weight:600;margin-bottom:6px">数据目标体检</div>'];
            parts.push('<div>' + mark(goal.alpha >= 0.83) + ' 信度 α = ' + goal.alpha
                + '　' + mark(goal.kmo >= 0.67) + ' KMO = ' + goal.kmo
                + '　' + mark(goal.achieved) + (goal.achieved ? ' 已达标' : ' 未完全达标') + '</div>');
            if (goal.predictors && goal.predictors.length) {
                parts.push('<div style="margin-top:4px">影响因素（显著 ' + goal.significant + '/' + goal.predictors.length + '）</div>');
                goal.predictors.forEach(function (item) {
                    parts.push('<div style="margin-left:12px">' + mark(item.ok) + ' 第' + item.question
                        + '题：r=' + item.r + '，p=' + item.p + '，方向 ' + item.direction
                        + '（期望 ' + item.expected + '）</div>');
                });
            }
            if (goal.notes && goal.notes.length) {
                parts.push('<div style="margin-top:6px;color:#a15c00">差距：' + goal.notes.join('；') + '</div>');
            }
            host.innerHTML = parts.join('');
        } catch (error) {
            console.warn('读取数据目标报告失败:', error);
        }
    }

    function boot() {
        ensureShims();
        const build = document.querySelector('meta[name="wjx-build"]');
        const buildEl = $('goalBuild');
        if (build && buildEl) {
            buildEl.textContent = 'build ' + build.getAttribute('content');
        }
        if (!document.getElementById('goalEnabled')) {
            return;
        }
        render();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
    // 解析完成后题目才会出现：按签名轮询，不监听点击（否则会打断用户操作）
    setInterval(function () {
        if (document.getElementById('goalEnabled')) {
            render(false);
        }
    }, 1500);

    window.GoalPanel = { sync: sync, payload: payload, render: render };
    window.showGoalReport = showGoalReport;
})();
