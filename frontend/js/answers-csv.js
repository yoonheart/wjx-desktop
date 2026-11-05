/**
 * 答题配置的表格（CSV）导入导出。
 *
 * 覆盖全部题型：
 * - 单选 / 多选 / 下拉 / 量表：一行一个选项，选项序号 1..n
 * - 矩阵：一行一个格子，选项序号写成 行.列（如 1.3）
 * - 填空：一行一个候选文本，选项序号写 -
 *
 * 定位一律靠输入框自己的 data-* 属性，不再依赖页面结构，
 * 这样量表、矩阵也能被导出和导入。
 */

(function () {
    'use strict';

    const HEADER = [
        '# ===== 数据目标：改数字即可（方向写 + / - / 0）=====',
        '# ===== 答题权重：只改「权重」这一列，不需要凑满 100，导入时自动按比例归一化 =====',
        '# 只保留你要改的题也行，没出现的题保持原样；改动前请勿增删列。',
        '# 文本框：填空题用序号 -；某个选项里的填空用 o加序号（如 o4 = 第4个选项的填空）。',
        '题号,题目,选项序号,选项文字,权重,题型'
    ].join('\n');

    function csvCell(value) {
        const text = value == null ? '' : String(value);
        return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }

    function questionMeta() {
        const meta = {};
        document.querySelectorAll('.question-container').forEach(function (container) {
            const id = container.dataset.questionId || '';
            const typeEl = container.querySelector('.question-type');
            const titleEl = container.querySelector('.question-content');
            meta[id] = {
                type: typeEl ? typeEl.textContent.trim() : '',
                title: titleEl ? titleEl.textContent.trim() : ''
            };
        });
        return meta;
    }

    /** 从一个输入框往上找它对应的文字标签。 */
    function labelFor(input) {
        const cell = input.closest('td');
        if (cell) {
            const row = cell.parentElement;
            const headers = row && row.closest('table')
                ? Array.from(row.closest('table').querySelectorAll('thead th, tr:first-child th'))
                : [];
            const colIndex = parseInt(input.dataset.colIndex || '0', 10);
            const headText = headers[colIndex + 1] ? headers[colIndex + 1].textContent.trim() : '';
            const rowText = row ? (row.querySelector('td') || {}).textContent || '' : '';
            return (rowText.trim() + ' ' + headText).trim() || ('列' + (colIndex + 1));
        }
        const holder = input.closest('.option-item, .scale-item, div');
        if (holder) {
            const textEl = holder.querySelector('.option-text, .scale-text');
            if (textEl) {
                return textEl.textContent.trim();
            }
            return (holder.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30);
        }
        return '';
    }

    function buildGoalLines() {
        const goal = window.GoalPanel && window.GoalPanel.payload ? window.GoalPanel.payload() : null;
        if (!goal) {
            return ['# 启用,否'];
        }
        const dirs = Object.keys(goal.directions || {}).map(function (qid) {
            return qid + ':' + goal.directions[qid];
        }).join(',');
        const items = (goal.outcome_items || []).slice();
        const aggregate = goal.outcome_aggregate || (items.length > 1 ? 'mean' : 'single');
        const dims = (goal.dimensions || []).map(function (group) {
            return group.join(',');
        }).join('|');
        const autoBox = document.getElementById('goalAutoDim');
        return [
            '# 启用,是',
            '# 信度α,' + goal.alpha_target,
            '# 效度KMO,' + goal.kmo_target,
            '# 至少显著,' + goal.significant_required,
            '# 自动分维,' + (autoBox && !autoBox.checked ? '否' : '是'),
            '# 维度,' + dims,
            '# 结局题,' + (goal.outcome_question || ''),
            '# 结局项,' + items.join(','),
            '# 结局合成,' + aggregate,
            '# 算作正面,' + ((goal.positive_options && goal.positive_options[0]) || ''),
            '# 影响因素,' + dirs
        ];
    }

    /** 生成 CSV：遍历所有概率输入框，量表/矩阵都覆盖。 */
    function buildCsv() {
        const meta = questionMeta();
        const rows = [];

        document.querySelectorAll('.probability-input').forEach(function (input) {
            const qid = input.dataset.questionId || '';
            if (!qid) {
                return;
            }
            let index;
            if (input.dataset.rowIndex !== undefined) {
                index = (parseInt(input.dataset.rowIndex, 10) + 1) + '.' + (parseInt(input.dataset.colIndex, 10) + 1);
            } else if (input.dataset.optionIndex !== undefined) {
                index = String(parseInt(input.dataset.optionIndex, 10) + 1);
            } else {
                return;
            }
            const info = meta[qid] || { type: '', title: '' };
            rows.push([
                qid, csvCell(info.title), index, csvCell(labelFor(input)),
                parseInt(input.value, 10) || 0, csvCell(info.type)
            ].join(','));
        });

        // 文本框：填空题整题一个池；选项里的填空按 o+序号 一个池
        document.querySelectorAll('.question-container').forEach(function (container) {
            const qid = container.dataset.questionId || '';
            const info = meta[qid] || { type: '', title: '' };
            const push = function (index, value) {
                rows.push([qid, csvCell(info.title), index, csvCell(value), 1, csvCell(info.type)].join(','));
            };

            if (info.type.indexOf('填空') >= 0) {
                const cfg = (window.WjxTextAnswers ? window.WjxTextAnswers.get(qid + '_0') : null) || [];
                if (cfg.length) {
                    cfg.forEach(function (item) {
                        push('-', item.text || '');
                    });
                } else {
                    push('-', '');
                }
                return;
            }

            container.querySelectorAll('.option-item').forEach(function (optionEl, index) {
                const textEl = optionEl.querySelector('.option-text');
                const label = textEl ? textEl.textContent : '';
                if (label.indexOf('【填空题输入框】') < 0) {
                    return;
                }
                const cfg = (window.WjxTextAnswers ? window.WjxTextAnswers.get(qid + '_' + index) : null) || [];
                if (cfg.length) {
                    cfg.forEach(function (item) {
                        push('o' + (index + 1), item.text || '');
                    });
                } else {
                    push('o' + (index + 1), '');
                }
            });
        });

        return {
            text: buildGoalLines().concat([HEADER]).concat(rows).join('\n') + '\n',
            rows: rows.length
        };
    }

    function parseCsvLine(line) {
        const cells = [];
        let current = '';
        let quoted = false;
        for (let i = 0; i < line.length; i += 1) {
            const ch = line[i];
            if (quoted) {
                if (ch === '"' && line[i + 1] === '"') {
                    current += '"';
                    i += 1;
                } else if (ch === '"') {
                    quoted = false;
                } else {
                    current += ch;
                }
            } else if (ch === '"') {
                quoted = true;
            } else if (ch === ',') {
                cells.push(current);
                current = '';
            } else {
                current += ch;
            }
        }
        cells.push(current);
        return cells;
    }

    function findInput(qid, index) {
        if (index.indexOf('.') > 0) {
            const pair = index.split('.');
            const row = parseInt(pair[0], 10) - 1;
            const col = parseInt(pair[1], 10) - 1;
            return document.querySelector(
                '.probability-input[data-question-id="' + qid + '"]'
                + '[data-row-index="' + row + '"][data-col-index="' + col + '"]'
            );
        }
        const idx = parseInt(index, 10) - 1;
        return document.querySelector(
            '.probability-input[data-question-id="' + qid + '"][data-option-index="' + idx + '"]'
        );
    }

    /** 矩阵按行归一化，其它按题归一化。 */
    function groupKey(qid, index) {
        return index.indexOf('.') > 0 ? qid + '#' + index.split('.')[0] : qid;
    }

    function applyCsv(text) {
        const groups = new Map();
        const texts = new Map();
        const settings = {};
        const errors = [];
        const lines = String(text || '').split(/\r?\n/);

        lines.forEach(function (raw, lineNo) {
            const line = raw.trim();
            if (!line) {
                return;
            }
            if (line.startsWith('#')) {
                const body = line.replace(/^#\s*/, '');
                const parts = body.split(',');
                if (parts.length >= 2) {
                    settings[parts[0].trim()] = parts.slice(1).join(',').trim();
                }
                return;
            }
            const cells = parseCsvLine(line);
            if (cells.length < 5 || !/^\d+$/.test(cells[0].trim())) {
                return;
            }
            const qid = cells[0].trim();
            const index = cells[2].trim();
            const qtype = (cells[5] || '').trim();
            // 排序题没有概率可配，整行跳过（旧表里有这类行也不要报错）
            if (qtype.indexOf('排序') >= 0) {
                return;
            }
            // 文本行：- 或 o4
            if (index === '-' || /^o\d+$/i.test(index)) {
                const text = (cells[3] || '').trim();
                if (text) {
                    const key = index === '-' ? qid + '|blank' : qid + '|opt' + index.slice(1);
                    if (!texts.has(key)) {
                        texts.set(key, []);
                    }
                    texts.get(key).push(text);
                }
                return;
            }
            const weight = parseFloat(cells[4]);
            if (!Number.isFinite(weight) || weight < 0) {
                errors.push('第 ' + (lineNo + 1) + ' 行：权重「' + cells[4] + '」不是非负数字');
                return;
            }
            const key = groupKey(qid, index);
            if (!groups.has(key)) {
                groups.set(key, []);
            }
            groups.get(key).push({ qid: qid, index: index, weight: weight });
        });

        let updated = 0;
        let skipped = 0;

        groups.forEach(function (rows) {
            const total = rows.reduce(function (sum, item) { return sum + item.weight; }, 0);
            if (total <= 0) {
                return;
            }
            const scaled = rows.map(function (item) {
                return { item: item, value: Math.max(0, Math.round(item.weight / total * 100)) };
            });
            const diff = 100 - scaled.reduce(function (sum, it) { return sum + it.value; }, 0);
            if (diff !== 0) {
                scaled[scaled.length - 1].value += diff;
            }
            scaled.forEach(function (it) {
                const input = findInput(it.item.qid, it.item.index);
                if (!input) {
                    // 排序题、以及个别没有概率控件的题型，静默跳过
                    return;
                }
                input.value = String(Math.max(0, Math.min(100, it.value)));
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            });
            updated += 1;
        });

        const applied = applyTexts(texts, errors);
        applyGoalSettings(settings, errors);
        return { updated: updated + applied, skipped: skipped, errors: errors };
    }

    /** 把文本框内容写回页面（填空题 / 选项内填空）。 */
    function applyTexts(texts, errors) {
        if (!texts.size) {
            return 0;
        }
        const api = window.WjxTextAnswers;
        let applied = 0;

        texts.forEach(function (values, key) {
            const parts = key.split('|');
            const qid = parts[0];
            const target = parts[1];
            const answers = values.map(function (text) {
                return { text: text, probability: Math.round(100 / values.length) };
            });
            // 概率和补齐到 100
            const diff = 100 - answers.reduce(function (sum, it) { return sum + it.probability; }, 0);
            if (answers.length) {
                answers[0].probability += diff;
            }

            const storeKey = target === 'blank'
                ? qid + '_0'
                : qid + '_' + (parseInt(target.slice(3), 10) - 1);
            if (api && typeof api.set === 'function') {
                api.set(storeKey, answers);
            } else {
                console.warn('没有找到文本答案存储接口，跳过', storeKey);
                return;
            }
            applied += 1;
        });

        if (api && typeof api.refresh === 'function') {
            try {
                api.refresh();
            } catch (error) {
                console.warn('刷新文本答案按钮失败:', error);
            }
        }
        return applied;
    }

    function applyGoalSettings(settings, errors) {
        if (!settings || !Object.keys(settings).length) {
            return;
        }
        const enabled = (settings['启用'] || '') === '是';
        const setValue = function (id, value) {
            const el = document.getElementById(id);
            if (el) {
                el.value = value;
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        };

        const toggle = document.getElementById('goalEnabled');
        if (toggle) {
            toggle.checked = enabled;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
        }
        if (!enabled) {
            return;
        }
        if (settings['信度α']) setValue('goalAlpha', settings['信度α']);
        if (settings['效度KMO']) setValue('goalKmo', settings['效度KMO']);
        if (settings['至少显著']) setValue('goalSig', settings['至少显著']);

        // 自动分维：表里有就按表里的来（没写则保持界面现状）
        if (settings['自动分维'] !== undefined) {
            const autoBox = document.getElementById('goalAutoDim');
            if (autoBox) {
                autoBox.checked = settings['自动分维'] !== '否';
                autoBox.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }

        // 结局：优先读「结局项」（可含矩阵行、可多个），退回旧的「结局题」
        const itemText = (settings['结局项'] || settings['结局题'] || '').trim();
        if (itemText) {
            const refs = itemText.split(/[,;\s]+/).filter(function (x) { return /^\d+$/.test(x); });
            const outcome = document.getElementById('goalOutcome');
            if (outcome && refs.length) {
                const exists = Array.prototype.some.call(outcome.options, function (opt) {
                    return opt.value === refs[0];
                });
                if (!exists) {
                    errors.push('数据目标：结局项 ' + refs[0] + ' 不在问卷里');
                } else {
                    outcome.value = refs[0];
                    outcome.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
            const meanBox = document.getElementById('goalOutcomeMean');
            if (meanBox) {
                const aggregate = (settings['结局合成'] || '').trim();
                meanBox.checked = aggregate === 'mean' || refs.length > 1;
                meanBox.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
        if (settings['算作正面']) setValue('goalPositive', settings['算作正面']);

        const dirs = (settings['影响因素'] || '').split(',').map(function (item) {
            return item.trim();
        }).filter(Boolean);
        // 结局已经用掉的项目不能再当自变量：这里先算出来，遇到就跳过并提示，
        // 免得后端因为「结局与自变量重复」整单拒绝
        const usedAsOutcome = {};
        (function () {
            const outcomeEl = document.getElementById('goalOutcome');
            if (!outcomeEl || !outcomeEl.value) {
                return;
            }
            usedAsOutcome[outcomeEl.value] = true;
            const meanBox = document.getElementById('goalOutcomeMean');
            if (meanBox && meanBox.checked) {
                const host = Math.floor(Number(outcomeEl.value) / 100);
                document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
                    if (Math.floor(Number(box.dataset.qid) / 100) === host) {
                        usedAsOutcome[box.dataset.qid] = true;
                    }
                });
            }
        })();
        // 先全部取消：否则上一次勾过、这次表里没写的因素会残留，
        // 看起来"导入成功"，实际多带了一批自变量
        document.querySelectorAll('#goalFactors input[data-qid]').forEach(function (box) {
            box.checked = false;
            box.dispatchEvent(new Event('change', { bubbles: true }));
        });
        dirs.forEach(function (item) {
            const pair = item.split(':');
            const qid = (pair[0] || '').trim();
            const dir = (pair[1] || '+').trim();
            if (usedAsOutcome[qid]) {
                errors.push('第 ' + qid + ' 项已作为结局，已从影响因素里忽略');
                return;
            }
            const box = document.querySelector('#goalFactors input[data-qid="' + qid + '"]');
            const sel = document.querySelector('#goalFactors select[data-dir="' + qid + '"]');
            if (!box) {
                errors.push('数据目标：题号 ' + qid + ' 不在问卷里');
                return;
            }
            box.checked = true;
            box.dispatchEvent(new Event('change', { bubbles: true }));
            if (sel) {
                sel.value = dir;
                sel.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        if (window.GoalPanel && window.GoalPanel.sync) {
            window.GoalPanel.sync();
        }
    }

    function download(filename, text) {
        const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    function pickFile(callback) {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.csv,text/csv';
        input.onchange = function () {
            const file = input.files && input.files[0];
            if (!file) {
                return;
            }
            const reader = new FileReader();
            reader.onload = function () { callback(String(reader.result || '')); };
            reader.readAsText(file, 'utf-8');
        };
        input.click();
    }

    function toast(message, kind) {
        if (typeof showAlert === 'function') {
            showAlert(message, kind || 'info');
        } else {
            window.alert(message);
        }
    }

    async function exportCsv() {
        const result = buildCsv();
        if (!result.rows) {
            toast('还没有解析出题目，无法导出答题表。', 'error');
            return;
        }
        const filename = '答题表.csv';
        try {
            const response = await fetch('/api/config/save-text', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: filename, content: result.text })
            });
            const body = await response.json();
            const data = (body && body.data) || {};
            if (data.saved && data.path) {
                toast('已导出到：' + data.path);
                return;
            }
            if (data.saved === false && !data.path) {
                toast('已取消导出');
                return;
            }
        } catch (error) {
            console.warn('另存为不可用，改为浏览器下载:', error);
        }
        download(filename, result.text);
        toast('已导出答题表（共 ' + result.rows + ' 行）。用 Excel 打开，只改「权重」列即可。');
    }

    function importCsv() {
        pickFile(function (text) {
            const result = applyCsv(text);
            if (result.updated === 0) {
                toast('导入失败：' + (result.errors[0] || '没有可应用的行'), 'error');
                return;
            }
            let message = '已应用 ' + result.updated + ' 组权重';
            if (result.errors.length) {
                message += '\n\n需要注意：\n' + result.errors.slice(0, 5).join('\n');
            }
            toast(message, result.errors.length ? 'warning' : 'info');
        });
    }


    // 快速上手里的示例提示词：用数组拼，避免与 HTML/模板字符串冲突
    const AI_PROMPT_LINES = [
        '这是一份问卷星答题配置表（CSV）。请按要求直接修改 csv：',
        '保留所有 # 注释行、表头、题号、列顺序和行数，不要增删行列，不要解释，只输出表格内容。',
        '',
        '【我的研究目标】',
        '- 量表信度：每个维度 Cronbach α ≈ 0.85（同一维度的题要聚在同一个因子上）',
        '- 效度：KMO ≈ 0.70 以上',
        '- 回归模型：至少 2 个自变量显著（p < 0.05）且有明确方向',
        '',
        '【请这样改】',
        '1. 目标参数（只改值，不改键名）：',
        '   # 启用 → 是',
        '   # 信度α → 0.85',
        '   # 效度KMO → 0.70',
        '   # 至少显著 → 2（不要超过你实际勾选的影响因素个数）',
        '   # 自动分维 → 是（每道矩阵题算一个维度；改成「否」会把所有题压成一个维度，'
            + '题项间相关被摊薄，因子分析会碎成多个弱因子，不建议）',
        '   # 维度 → 只读信息，由程序按题型自动生成，不用改',
        '',
        '2. 结局（因变量）：',
        '   可写进 # 结局项，格式是项目编号，英文逗号分隔：',
        '   - 单选题 / 量表题：直接写题号，如 8',
        '   - 矩阵题的某一行：题号×100＋行号，如 503 表示第5题第3行',
        '   - 多维构念（矩阵多行取均值）：如 501,502,503，同时把 # 结局合成 写成 mean',
        '   # 结局合成 只取 single 或 mean；single 表示只用第一个项目。',
        '   只有【单选题】结局需要 # 算作正面（哪个选项算“满意/正面”）；',
        '   量表与矩阵行是连续分，不用填 # 算作正面。',
        '   # 结局题 是旧字段（只写题号），保留兼容，填了也不影响。',
        '',
        '3. 影响因素（自变量）：',
        '   可用的项目＝【量表题】与【矩阵题的每一行】。',
        '   矩阵行用「题号×100＋行号」表示：第5题第1行 = 501，第5题第2行 = 502。',
        '   写入 # 影响因素，格式 题号:方向（+ 正向 / - 负向 / 0 故意不显著），',
        '   英文逗号分隔。示例：4:+,501:+,502:-',
        '   至少要选 2 个，且方向不要写成同一个极端，否则回归里会互相抵消。',
        '   注意：已经用作结局的项目不要再写进影响因素（自己解释自己没有意义）。',
        '',
        '4. 权重列：',
        '   每行填 0~100 的整数，同一题内要有主次（如 60/30/10）；',
        '   不需要刚好等于 100，导入时按比例归一化；量表题权重随意填即可。',
        '',
        '5. 文本框（选项序号为 - 或 o4 的行）：',
        '   必须逐行填不同内容，不要所有行写同一句。',
        '   填空题请给出 8~15 条互不相同的自然中文短句（同一道题多行就是候选池）；',
        '   o4 表示第 4 个选项里的填空，同样要多样化。',
        '',
        '6. 多选题（如段位）：',
        '   权重只决定被选中的概率，同一题内保持有主次即可。',
        '',
        '【硬性约束】',
        '- 不要修改任何题号，不要新增/删除行，不要改动列顺序和第 6 列「题型」；',
        '- 矩阵题的选项序号保持 行.列 格式（如 2.3），不要改；',
        '- 文本行不要留空、不要重复；',
        '- 只输出 CSV 文本，不要用代码块包裹。'
    ];

    function setupPrompt() {
        const box = document.getElementById('aiPromptText');
        if (!box || box.textContent.trim()) {
            return;
        }
        box.textContent = AI_PROMPT_LINES.join('\n');

        const btn = document.getElementById('copyPromptBtn');
        if (!btn) {
            return;
        }
        btn.addEventListener('click', function () {
            const text = AI_PROMPT_LINES.join('\n');
            const done = function () {
                btn.innerHTML = '<i class="fa fa-check" aria-hidden="true"></i>';
                btn.style.borderColor = '#2f9e44';
                btn.style.color = '#2f9e44';
                if (typeof showAlert === 'function') {
                    showAlert('提示词已复制');
                }
                setTimeout(function () {
                    btn.innerHTML = '<i class="fa fa-copy" aria-hidden="true"></i>';
                    btn.style.borderColor = '#ddd';
                    btn.style.color = '';
                }, 1600);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(function () {
                    fallbackCopy(text, done);
                });
            } else {
                fallbackCopy(text, done);
            }
        });
    }

    function fallbackCopy(text, done) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        try {
            document.execCommand('copy');
            done();
        } catch (error) {
            console.warn('复制失败，请手动选择文本:', error);
            if (typeof showAlert === 'function') {
                showAlert('复制失败，请手动选中文本复制', 'error');
            }
        }
        document.body.removeChild(area);
    }

    function mount() {
        setupPrompt();
        if (document.getElementById('exportCsvBtn')) {
            return;
        }

        const openModalBtn = document.getElementById('quickStartBtn');
        const modalExport = document.getElementById('quickExportCsv');
        const modalImport = document.getElementById('quickImportCsv');

        // 弹窗里的两个按钮
        if (modalExport) {
            modalExport.addEventListener('click', exportCsv);
        }
        if (modalImport) {
            modalImport.addEventListener('click', importCsv);
        }

        // 兜底：如果页面没有弹窗按钮（旧页面），就在工具栏末尾补两个
        if (!modalExport || !modalImport) {
            const bar = document.getElementById('quickStartBtn') || document.querySelector('.toolbar');
            if (!bar || !bar.parentElement) {
                return;
            }
            const fallbackExport = document.createElement('button');
            fallbackExport.id = 'exportCsvBtn';
            fallbackExport.type = 'button';
            fallbackExport.className = 'btn btn-sm';
            fallbackExport.textContent = '导出答题表';
            fallbackExport.addEventListener('click', exportCsv);

            const fallbackImport = document.createElement('button');
            fallbackImport.id = 'importCsvBtn';
            fallbackImport.type = 'button';
            fallbackImport.className = 'btn btn-sm';
            fallbackImport.textContent = '导入答题表';
            fallbackImport.addEventListener('click', importCsv);

            bar.insertAdjacentElement('afterend', fallbackImport);
            bar.insertAdjacentElement('afterend', fallbackExport);
        }

        // 旧的 json 导入导出按钮不再使用
        const legacyExport = document.getElementById('exportConfigBtn');
        const legacyImport = document.getElementById('importConfigBtn');
        if (legacyExport && legacyExport.parentElement) {
            legacyExport.parentElement.removeChild(legacyExport);
        }
        if (legacyImport && legacyImport.parentElement) {
            legacyImport.parentElement.removeChild(legacyImport);
        }
        void openModalBtn;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', mount);
    } else {
        mount();
    }

    window.AnswersCsv = { buildCsv: buildCsv, applyCsv: applyCsv, exportCsv: exportCsv, importCsv: importCsv };
})();
