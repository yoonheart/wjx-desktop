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
            + '<input id="synthSigHi">';
        document.body.appendChild(holder);
    }

    /** 开关打开才显示下面几块配置。 */

    // ---- 按问卷隔离的持久化 ----------------------------------------------
    // 存 SQLite（/api/state），不同问卷用不同 key，互不干扰；
    // 后端不可用时退回 localStorage，至少本次会话不丢。

    function surveyToken() {
        const match = String(location.search || '').match(/vm\/([A-Za-z0-9]+)/)
            || String(location.search || '').match(/shortid=([A-Za-z0-9]+)/);
        return match ? match[1] : 'unknown';
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
        try {
            const raw = localStorage.getItem(storageKey());
            local = raw ? JSON.parse(raw) : null;
        } catch (error) {
            local = null;
        }
        if (local) {
            restore(local);
        }
        fetch('/api/state').then(function (r) { return r.json(); }).then(function (body) {
            const items = (body && body.data) || {};
            const raw = items[storageKey()];
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

    function toggleBody() {
        const on = Boolean(($('goalEnabled') || {}).checked);
        ['goalBodyGroup', 'goalTargetGroup', 'goalFactorGroup'].forEach(function (id) {
            const el = $(id);
            if (el) {
                el.classList.toggle('is-hidden', !on);
            }
        });
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
        return groups;
    }

    function autoDimEnabled() {
        const box = $('goalAutoDim');
        return box ? Boolean(box.checked) : true;
    }

    function fillSelect(select, items, placeholder) {
        if (!select) {
            return;
        }
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
    }

    function render(force) {
        const list = questions();
        const sig = signature(list) + '|' + list.map(function (q) { return q.rows.length; }).join(',');
        if (!force && sig === lastSignature) {
            return;
        }
        lastSignature = sig;

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

        const outcome = $('goalOutcome');
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

        ['goalEnabled', 'goalAlpha', 'goalKmo', 'goalSig', 'goalAutoDim'].forEach(function (id) {
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
        const legacyQuestion = items.length ? Math.floor(items[0] / 100) : 0;
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
        return {
            enabled: true,
            alpha_target: parseFloat(($('goalAlpha') || {}).value) || 0.85,
            dimensions: dimensions,
            reverse: [],
            dimension_correlation: 0.45,
            kmo_target: parseFloat(($('goalKmo') || {}).value) || 0.70,
            // 新字段：结局项目（可含矩阵行、可多项目合成）
            outcome_items: items,
            outcome_aggregate: outcomeAggregate(),
            // 旧字段保留：其它模块只想知道「结局是哪道题」
            outcome_question: picked ? parseInt(picked.question.id, 10) : 0,
            positive_options: positiveRaw ? [positiveRaw] : [],
            predictors: predictors,
            directions: directions,
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
