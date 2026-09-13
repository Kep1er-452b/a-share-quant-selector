/* Read-only server results. Never calls selection or provider-update endpoints. */
(() => {
    'use strict';
    const el = id => document.getElementById(`server-result-${id}`);
    const esc = value => String(value ?? '--').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    const number = value => value === null || value === undefined ? '--' : esc(value);
    let configReady = false, initialized = false, offset = 0, total = 0;
    let generation = 0, controller = null, timer = null, lastAttempt = 0, manifest = null;
    let shownRelease = '', urls = [], checking = false, nextStatusAt = 0;
    const statusLabels = {matched: '命中', not_matched: '未命中', insufficient_history: '历史不足', excluded_invalid_name: '名称规则排除', error: '计算异常', failed: '失败', valid: '有效', unavailable: '数据不可用', strength_continued: '强势延续', pullback_observation: '回调观察', weak_observation: '弱势观察', reversal_confirmed: '反转确认', holding: '持有观察', stopped_key_low: '跌破关键低点停止', stopped_bbi: '跌破黄线停止', stopped_white_line: '跌破白线停止'};
    const statusLabel = value => statusLabels[value] || value || '--';
    const localTime = value => { const d = new Date(value); return Number.isNaN(d.getTime()) ? '--' : new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium'}).format(d); };
    const preference = 'quantSelectionResultSource';
    const source = () => document.getElementById('selection-result-source');
    const aShare = () => !window.quantMarketContext || window.quantMarketContext.currentMarket() === 'a_share';
    const active = () => document.getElementById('selection-page').classList.contains('active') && aShare() && source().value === 'server';
    async function request(path, options = {}) {
        const headers = {'X-Quant-Session': document.querySelector('meta[name="quant-session-token"]')?.content || '', ...options.headers};
        const response = await fetch(`/api/server-results${path}`, {...options, headers, signal: options.signal || AbortSignal.timeout(15000)});
        if (!response.ok) {
            const message = await response.json().catch(() => ({}));
            throw new Error(message.error || `请求失败 (${response.status})`);
        }
        return response;
    }
    function releaseURLs() { urls.forEach(url => URL.revokeObjectURL(url)); urls = []; }
    function stopView() { generation++; controller?.abort(); releaseURLs(); }
    function mode() {
        const server = aShare() && source().value === 'server';
        el('source').hidden = !aShare();
        el('panel').hidden = !server;
        document.getElementById('local-selection-layout').hidden = server;
        if (!active()) stopView();
        else { refresh().catch(showError); if (shownRelease) load(); }
    }
    function showError(error) {
        if (error.name !== 'AbortError') el('status').textContent = error.message;
    }
    async function refresh() {
        if (checking) return;
        checking = true;
        try {
            const status = await (await request('/status')).json();
            configReady = status.configured;
            if (!initialized) {
                let saved;
                try { saved = localStorage.getItem(preference); } catch (_) { /* optional */ }
                source().value = saved === 'local' ? 'local' : (configReady ? 'server' : 'local');
                initialized = true;
                mode();
            }
            const job = status.job;
            const running = job?.status === 'running';
            nextStatusAt = Date.now() + (running ? 3000 : 30000);
            el('sync').disabled = !configReady || running;
            el('cancel').disabled = !running;
            el('connection').textContent = configReady ? '已配置 · 服务器结果独立于本机行情仓库' : (status.config_error || '未配置连接；请按接入说明设置本机连接文件');
            const current = status.current;
            const currentText = current ? `最近发布 ${current.trade_date} · 修订 ${current.revision} · 上次同步 ${localTime(current.synced_at)}` : '尚无已验证结果';
            el('status').textContent = `${currentText}${job ? ` · ${job.current_step}${running ? ` ${job.progress_pct}%` : ''}` : ''}${job?.error ? `：${job.error}` : ''}${job?.warning ? ` · ${job.warning}` : ''}`;
            const authURL = /^https:\/\/login\.tailscale\.com\/a\/[A-Za-z0-9]+$/.test(job?.auth_url || '') ? job.auth_url : null;
            el('auth').hidden = !authURL;
            if (authURL) el('auth').href = authURL; else el('auth').removeAttribute('href');
            if (active() && current && shownRelease !== current.release_id) {
                const releases = await (await request('/releases')).json();
                if (!active()) return;
                el('release').innerHTML = releases.items.map(r => `<option value="${esc(r.release_id)}">${esc(r.trade_date)} / 修订 ${r.revision}</option>`).join('');
                el('release').value = current.release_id;
                shownRelease = current.release_id;
                offset = 0;
                await load();
            }
            const hour = Number(new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23'}).format(new Date()));
            if (configReady && !running && job?.error_code !== 'SSH_AUTH_REQUIRED' && source().value === 'server' && !document.hidden && (!lastAttempt || (hour >= 17 && Date.now() - lastAttempt >= 300000))) {
                await sync();
            }
        } finally { checking = false; }
    }
    async function sync() {
        lastAttempt = Date.now();
        await request('/sync', {method: 'POST'});
        el('status').textContent = '正在读取服务器发布索引…';
        el('sync').disabled = true;
        el('cancel').disabled = false;
        nextStatusAt = 0;
    }
    const label = id => manifest?.strategy_labels?.[id] || id || '--';
    function table(headings, cells) {
        return `<div class="results-table-wrap"><table class="data-table"><thead><tr>${headings.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${cells.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    async function load() {
        stopView();
        if (!active() || !el('release').value) return;
        const currentGeneration = generation;
        controller = new AbortController();
        const signal = controller.signal;
        const release = encodeURIComponent(el('release').value);
        const pinned = `release_id=${release}`;
        el('content').innerHTML = '<div class="state-loading">读取已验证缓存…</div>';
        try {
            const loadedManifest = await (await request(`/summary?${pinned}`, {signal})).json();
            if (currentGeneration !== generation) return;
            manifest = loadedManifest;
            const oldStrategy = el('strategy').value;
            el('strategy').innerHTML = '<option value="">全部策略</option>' + manifest.coverage.strategies.map(s => `<option value="${esc(s)}">${esc(label(s))}</option>`).join('');
            el('strategy').value = oldStrategy;
            const view = el('view').value;
            el('strategy').hidden = view !== 'selection-items';
            const states = Object.entries(manifest.coverage.selection_evaluation_status_counts || {}).map(([k, v]) => `${esc(statusLabel(k))} ${number(v)}`).join(' · ');
            el('summary').innerHTML = `<p>交易日 <strong>${esc(manifest.trade_date)}</strong> · ${number(manifest.coverage.selected_records)} 条信号 · ${number(manifest.coverage.tracking_members)} 只追踪股票 · ${manifest.quality.core_status === 'completed_with_warnings' ? '完成，含运行告警（见日报）' : '完成'}</p><p>${states}</p><p>选股信号直接来自服务器。通用指标历史${manifest.coverage.indicator_values?.status === 'not_computed' ? '尚未提供' : '以本次发布为准'}；交互 K 线仍使用本机行情，日期可能落后。</p><details><summary>结果版本与口径</summary><p>服务器版本 ${esc(manifest.producer.code_version)} · 策略配置 ${esc(manifest.producer.strategy_config_hash?.slice(0, 12))}</p></details>`;
            el('pagination').hidden = ['report', 'charts'].includes(view);
            if (view === 'report') {
                const text = await (await request(`/artifact/daily-report?${pinned}`, {signal})).text();
                if (currentGeneration !== generation) return;
                el('content').innerHTML = `<button class="btn btn-ghost" id="server-report-download" type="button">保存完整日报</button><pre class="server-result-report">${esc(text)}</pre>`;
                document.getElementById('server-report-download').onclick = () => {
                    const url = URL.createObjectURL(new Blob([text], {type: 'text/markdown;charset=utf-8'}));
                    urls.push(url);
                    const a = document.createElement('a'); a.href = url; a.download = `${manifest.trade_date}-daily-report.md`; a.click();
                };
                return;
            }
            if (view === 'charts') {
                el('content').replaceChildren();
                const charts = manifest.artifacts.filter(a => a.media_type === 'image/png');
                if (!charts.length) el('content').textContent = '本次发布未包含图表。';
                for (const chart of charts) {
                    try {
                        const blob = await (await request(`/artifact/${encodeURIComponent(chart.artifact_id)}?${pinned}`, {signal})).blob();
                        if (currentGeneration !== generation) return;
                        const image = document.createElement('img');
                        image.src = URL.createObjectURL(blob); urls.push(image.src);
                        image.alt = chart.artifact_id; image.className = 'server-result-chart';
                        el('content').append(image);
                    } catch (error) {
                        if (error.name === 'AbortError') throw error;
                        if (currentGeneration !== generation) return;
                        const note = document.createElement('p'); note.textContent = `${chart.artifact_id}：图表暂不可用`; el('content').append(note);
                    }
                }
                return;
            }
            const filter = view === 'selection-items' ? `&strategy=${encodeURIComponent(el('strategy').value)}` : '';
            const page = await (await request(`/data/${view}?${pinned}&offset=${offset}&limit=100${filter}`, {signal})).json();
            if (currentGeneration !== generation) return;
            total = page.total;
            el('page').textContent = total ? `${offset + 1}–${Math.min(total, offset + 100)} / ${total}` : '0 条记录';
            el('prev').disabled = offset === 0; el('next').disabled = offset + 100 >= total;
            if (!page.items.length) { el('content').innerHTML = '<div class="state-empty">本次发布此项没有记录。未命中、跳过及运行异常请参见上方评估统计和日报。</div>'; return; }
            if (view === 'selection-items') {
                el('content').innerHTML = table(['代码', '名称', '策略', '收盘价', 'J 值', '市值（亿）', '触发原因'], page.items.map(item => {
                    const s = item.trigger?.signals?.[0] || {};
                    return [esc(item.code), esc(item.name), esc(label(item.strategy_id)), number(s.close), number(s.J), number(s.market_cap), esc((s.reasons || []).join('；'))];
                }));
            } else if (view === 'tracking-daily') {
                el('content').innerHTML = table(['代码', '名称', '涨跌幅（%）', '成交量（手）', '较昨日量变（%）', '前五日量比', '数据状态 / 原因'], page.items.map(i => [esc(i.code), esc(i.name), number(i.pct_chg), number(i.volume), number(i.volume_change_pct), number(i.volume_ratio_5d), esc([statusLabel(i.data_status), ...(i.reasons || [])].join(' · '))]));
            } else if (view === 'selection-tracking') {
                el('content').innerHTML = table(['代码', '名称', '入场日期', '当前阶段', '累计涨跌（%）', '停止原因'], page.items.map(i => [esc(i.code), esc(i.name), esc(i.entry_trade_date), esc(statusLabel(i.state)), number(i.cumulative_change_pct), esc(i.stop_reason || '--')]));
            } else {
                el('content').innerHTML = table(['代码', '名称', '服务器自选信息（只读）'], page.items.map(i => [esc(i.code), esc(i.name), esc(i.note || i.status || '--')]));
            }
        } catch (error) { if (currentGeneration === generation && error.name !== 'AbortError') { showError(error); el('content').textContent = error.message; } }
    }
    function setup() {
        window.quantServerResults = Object.freeze({sync: () => sync().then(() => refresh()).catch(showError)});
        source().addEventListener('change', () => { try { localStorage.setItem(preference, source().value); } catch (_) { /* optional */ } mode(); });
        el('sync').addEventListener('click', () => sync().then(() => refresh()).catch(showError));
        el('cancel').addEventListener('click', () => request('/cancel', {method: 'POST'}).then(() => refresh()).catch(showError));
        ['release', 'view', 'strategy'].forEach(id => el(id).addEventListener('change', () => { offset = 0; load(); }));
        el('prev').addEventListener('click', () => { offset = Math.max(0, offset - 100); load(); });
        el('next').addEventListener('click', () => { offset += 100; load(); });
        window.addEventListener('quant:market-change', mode);
        window.addEventListener('quant:page-change', () => { mode(); if (active() && !el('content').children.length) load(); });
        document.addEventListener('visibilitychange', () => { if (!document.hidden) { lastAttempt = 0; refresh().catch(showError); } });
        window.addEventListener('online', () => { lastAttempt = 0; refresh().catch(showError); });
        timer = setInterval(() => { if (!document.hidden && Date.now() >= nextStatusAt) refresh().catch(showError); }, 3000);
        window.addEventListener('pagehide', () => { clearInterval(timer); stopView(); });
        refresh().catch(showError);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup); else setup();
})();
