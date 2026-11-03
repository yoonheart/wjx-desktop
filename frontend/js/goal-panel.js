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
            outcome: ($('goalOutcome') || {}).value || '',
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

        // 结局题只能是单选题或量表题（多选/矩阵/排序/填空没有单一取值）
        const outcomeItems = list.filter(function (q) {
            return q.type.indexOf('单选题') >= 0 || q.type.indexOf('量表') >= 0;
        });
        fillSelect($('goalOutcome'), outcomeItems.map(function (q) {
            return { value: q.id, label: '第' + q.id + '题 ' + q.title };
        }), '请选择题目');

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
                const picked = list.filter(function (q) { return q.id === outcome.value; })[0];
                const previous = ($('goalPositive') || {}).value || '';
                fillSelect($('goalPositive'), (picked ? picked.options : []).map(function (text, index) {
                    return { value: index + 1, label: (index + 1) + '.' + (text || '选项') };
                }), '请选择选项');
                const positive = $('goalPositive');
                if (positive && previous) {
                    positive.value = previous;
                }
                sync();
            };
        }

        ['goalEnabled', 'goalAlpha', 'goalKmo', 'goalSig'].forEach(function (id) {
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
        toggleBody();
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
        const outcomeQ = ($('goalOutcome') || {}).value || '';
        const positiveIdx = parseInt(($('goalPositive') || {}).value, 10) || 1;

        const scaleIds = [];
        questions().forEach(function (q) {
            if (q.isMatrix) {
                q.rows.forEach(function (row) { scaleIds.push(Number(row.key)); });
            } else if (q.type.indexOf('量表') >= 0) {
                scaleIds.push(parseInt(q.id, 10));
            }
        });
        const dimensions = scaleIds.length ? [scaleIds] : [];

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
        shim('validitySwitch', enabled);
        shim('validityTarget', alpha.toFixed(2));
        shim('validityDimensions', dimensions.map(function (g) { return g.join('-'); }).join(';'));
        shim('validityReverse', '');
        shim('synthSwitch', enabled && outcomeQ && predictors.length > 0);
        shim('synthOutcomeQ', outcomeQ);
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
            summary.textContent = enabled
                ? 'α ' + alpha.toFixed(2) + ' · KMO ' + kmo.toFixed(2)
                    + (outcomeQ ? ' · 结局题第' + outcomeQ + '题' : ' · 未选结局题')
                    + ' · 因素 ' + predictors.length + ' 个'
                : '';
        }

        const hint = $('goalHint');
        if (hint) {
            if (!enabled) {
                hint.textContent = '未开启：按普通随机方式填写。';
            } else if (!outcomeQ) {
                hint.textContent = '已开启：请选择结局题（不选则只保证信度，不构造结论）。';
            } else if (!predictors.length) {
                hint.textContent = '已开启：请勾选至少一个影响因素。';
            } else {
                hint.textContent = '已开启：量表按 α=' + alpha.toFixed(2) + ' 生成，'
                    + predictors.length + ' 个影响因素按设定方向构造，至少 ' + sig + ' 项显著。';
            }
        }
    }

    /** 给后端的新结构。 */
    function payload() {
        const enabled = Boolean(($('goalEnabled') || {}).checked);
        if (!enabled) {
            return null;
        }
        const list = questions();
        const scorable = [];
        list.forEach(function (q) {
            if (q.isMatrix) {
                q.rows.forEach(function (row) { scorable.push(Number(row.key)); });
            } else if (q.type.indexOf('量表') >= 0) {
                scorable.push(Number(q.id));
            }
        });

        const dimensions = scorable.length ? [scorable] : [];

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

        return {
            enabled: true,
            alpha_target: parseFloat(($('goalAlpha') || {}).value) || 0.85,
            dimensions: dimensions,
            reverse: [],
            dimension_correlation: 0.45,
            kmo_target: parseFloat(($('goalKmo') || {}).value) || 0.70,
            outcome_question: parseInt(($('goalOutcome') || {}).value, 10) || 0,
            positive_options: (parseInt(($('goalPositive') || {}).value, 10) || 1)
                ? [parseInt(($('goalPositive') || {}).value, 10) || 1] : [],
            predictors: predictors,
            directions: directions,
            significant_required: parseInt(($('goalSig') || {}).value, 10) || 0,
            event_rate: 0.5,
            attempts: 12
        };
    }

    /** 结果页的体检报告。 */
    async function showGoalReport(taskId) {
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
