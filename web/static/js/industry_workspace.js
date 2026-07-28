'use strict';

(function initIndustryWorkspace(global) {
    const lifecycle = { active: false, mounted: false, controller: null, listeners: [], chart: null, cycleChart: null };
    const state = { mode: 'market', industryId: null };
    const byId = id => document.getElementById(id);
    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
    function listen(element, type, handler) {
        if (!element) return;
        element.addEventListener(type, handler);
        lifecycle.listeners.push([element, type, handler]);
    }
    function abortRequest() {
        lifecycle.controller?.abort();
        lifecycle.controller = null;
    }
    async function requestJson(url) {
        abortRequest();
        const controller = new AbortController();
        lifecycle.controller = controller;
        const timeoutId = setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, { signal: controller.signal });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
            return payload;
        } finally {
            clearTimeout(timeoutId);
            if (lifecycle.controller === controller) lifecycle.controller = null;
        }
    }
    function setStatus(message, tone = '') {
        const element = byId('industry-status');
        if (!element) return;
        element.textContent = message;
        element.dataset.tone = tone;
    }
    function renderClassifications(payload) {
        const items = Array.isArray(payload.items) ? payload.items.slice(0, 500) : [];
        byId('industry-classifications-body').innerHTML = items.map(item => `
            <tr class="domain-click-row" data-industry-id="${escapeHtml(item.industry_id)}"><td>${escapeHtml(item.industry_id)}</td><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.level)}</td></tr>
        `).join('') || '<tr><td colspan="3" class="state-empty">本地仓库暂无行业分类</td></tr>';
        const coverage = payload.coverage || {};
        byId('industry-coverage').textContent = `COVERAGE ${coverage.mapped ?? 0} / ${coverage.total ?? 0}`;
        return items.length;
    }
    function renderChartMessage(chartKey, elementId, message) {
        if (!global.echarts) return;
        if (!lifecycle[chartKey]) lifecycle[chartKey] = global.echarts.init(byId(elementId));
        lifecycle[chartKey].clear();
        lifecycle[chartKey].setOption({
            animation: false,
            backgroundColor: '#000000',
            graphic: [{
                type: 'text',
                left: 'center',
                top: 'middle',
                style: {
                    text: message,
                    fill: '#8c8c8c',
                    font: '11px monospace',
                    textAlign: 'center',
                },
            }],
        }, true);
    }
    function clearIndustryDetail(message, title = 'SELECT INDUSTRY') {
        byId('industry-detail-title').textContent = title;
        byId('industry-members-body').innerHTML = `<tr><td colspan="4" class="state-empty">${escapeHtml(message)}</td></tr>`;
        renderChartMessage('chart', 'industry-kline', message);
    }
    function renderIndustryDetail(payload) {
        const industry = payload.industry || {};
        const members = Array.isArray(payload.members) ? payload.members.slice(0, 1000) : [];
        const candles = Array.isArray(payload.index_candles) ? payload.index_candles : [];
        const candleState = payload.index_candles_state || {};
        byId('industry-detail-title').textContent = `${industry.industry_id || '--'} ${industry.name || ''}`;
        byId('industry-members-body').innerHTML = members.map(item => `
            <tr><td>${escapeHtml(item.symbol)}</td><td>${escapeHtml(item.name || '--')}</td><td>${escapeHtml(item.in_date || '--')}</td><td><button class="text-action" data-industry-symbol="${escapeHtml(item.symbol)}" data-industry-name="${escapeHtml(item.name || '')}" type="button">OPEN KLINE</button></td></tr>
        `).join('') || '<tr><td colspan="4" class="state-empty">该行业暂无本地成分数据</td></tr>';
        if (!candles.length) {
            renderChartMessage(
                'chart',
                'industry-kline',
                candleState.message || '该行业暂无本地指数 K 线',
            );
            return { hasMembers: Boolean(members.length), hasCandles: false };
        }
        if (!global.echarts) return { hasMembers: Boolean(members.length), hasCandles: true };
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('industry-kline'));
        lifecycle.chart.setOption({
            animation: false, backgroundColor: '#000000',
            grid: { left: 56, right: 20, top: 20, bottom: 32 },
            tooltip: { trigger: 'axis', backgroundColor: '#080808', borderColor: '#ff6900', textStyle: { color: '#f2f2f2' } },
            xAxis: { type: 'category', data: candles.map(row => row.trade_date), axisLine: { lineStyle: { color: '#303030' } }, axisLabel: { color: '#8c8c8c' } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: '#181818' } }, axisLabel: { color: '#8c8c8c' } },
            series: [{ type: 'candlestick', data: candles.map(row => [row.open, row.close, row.low, row.high]), itemStyle: { color: '#ff3131', color0: '#00ff41', borderColor: '#ff3131', borderColor0: '#00ff41' } }],
        }, true);
        return { hasMembers: Boolean(members.length), hasCandles: true };
    }
    function switchMode(mode) {
        state.mode = mode;
        document.querySelectorAll('[data-industry-mode]').forEach(button => button.classList.toggle('active', button.dataset.industryMode === mode));
        byId('industry-market-view').classList.toggle('active', mode === 'market');
        byId('industry-cycle-view').classList.toggle('active', mode === 'cycle');
        if (mode === 'cycle') workspace.loadCycle();
        else lifecycle.chart?.resize();
    }
    function renderCycle(payload) {
        const series = Array.isArray(payload.series) ? payload.series : [];
        const hasPoints = series.some(item => Array.isArray(item.points) && item.points.length);
        if (!hasPoints) {
            renderChartMessage('cycleChart', 'industry-cycle-chart', '当前选择暂无本地产业景气数据');
            return false;
        }
        const units = payload.axis_mode === 'normalized'
            ? ['z-score']
            : [...new Set(series.map(item => item.unit || 'value'))];
        const axisForUnit = new Map(units.map((unit, index) => [unit, index]));
        const yAxes = units.map((unit, index) => ({
            type: 'value',
            scale: true,
            name: unit,
            position: index % 2 ? 'right' : 'left',
            offset: Math.floor(index / 2) * 52,
            splitLine: { show: index === 0, lineStyle: { color: '#181818' } },
            axisLine: { show: true, lineStyle: { color: '#303030' } },
            axisLabel: { color: '#8c8c8c' },
            nameTextStyle: { color: '#b8b8b8' },
        }));
        if (!global.echarts) return true;
        if (!lifecycle.cycleChart) lifecycle.cycleChart = global.echarts.init(byId('industry-cycle-chart'));
        lifecycle.cycleChart.setOption({
            animation: false, backgroundColor: '#000000', color: ['#ff6900', '#00ff41'],
            tooltip: { trigger: 'axis', backgroundColor: '#080808', borderColor: '#ff6900', textStyle: { color: '#f2f2f2' } },
            legend: { top: 2, textStyle: { color: '#b8b8b8' } },
            grid: { left: 58 + Math.max(0, Math.ceil(units.length / 2) - 1) * 52, right: 28 + Math.max(0, Math.floor(units.length / 2) - 1) * 52, top: 34, bottom: 36 },
            xAxis: { type: 'category', axisLine: { lineStyle: { color: '#303030' } }, axisLabel: { color: '#8c8c8c' } },
            yAxis: yAxes,
            series: series.map(item => ({ name: `${item.name} (${item.unit})`, type: 'line', yAxisIndex: axisForUnit.get(item.unit || 'value') || 0, showSymbol: false, data: item.points || [] })),
        }, true);
        return true;
    }

    const workspace = {
        mount() {
            if (lifecycle.mounted) return;
            listen(byId('industry-refresh'), 'click', () => workspace.refresh());
            listen(byId('industry-level'), 'change', () => {
                state.industryId = null;
                clearIndustryDetail('请选择当前层级中的行业');
                workspace.refresh();
            });
            listen(byId('industry-workspace-root'), 'click', event => {
                const modeButton = event.target.closest('[data-industry-mode]');
                if (modeButton) { switchMode(modeButton.dataset.industryMode); return; }
                const industryRow = event.target.closest('[data-industry-id]');
                if (industryRow) { workspace.loadDetail(industryRow.dataset.industryId); return; }
                const instrument = event.target.closest('[data-industry-symbol]');
                if (instrument) {
                    global.quantEquityRouter.openInstrument('a_share', instrument.dataset.industrySymbol, {
                        page: 'industry',
                        industryId: state.industryId,
                        mode: state.mode,
                        level: byId('industry-level')?.value || 'L1',
                        scrollY: global.scrollY,
                    });
                }
            });
            listen(byId('industry-cycle-series'), 'change', () => workspace.loadCycle());
            listen(byId('industry-cycle-normalize'), 'change', () => workspace.loadCycle());
            listen(global, 'quant:return-to-source', async event => {
                const source = event.detail || {};
                if (source.page !== 'industry') return;
                if (source.level) byId('industry-level').value = source.level;
                switchMode(source.mode === 'cycle' ? 'cycle' : 'market');
                if (source.industryId && source.mode !== 'cycle') {
                    await workspace.loadDetail(source.industryId);
                }
                global.requestAnimationFrame(() => global.scrollTo(0, Number(source.scrollY) || 0));
            });
            lifecycle.mounted = true;
        },
        async activate() {
            workspace.mount();
            if (lifecycle.active) { lifecycle.chart?.resize(); lifecycle.cycleChart?.resize(); return; }
            lifecycle.active = true;
            await workspace.refresh();
        },
        deactivate() {
            lifecycle.active = false;
            abortRequest();
            lifecycle.listeners.forEach(([element, type, handler]) => element.removeEventListener(type, handler));
            lifecycle.listeners = [];
            lifecycle.mounted = false;
            lifecycle.chart?.dispose();
            lifecycle.cycleChart?.dispose();
            lifecycle.chart = null;
            lifecycle.cycleChart = null;
        },
        async refresh() {
            if (!lifecycle.active) return;
            if (state.mode === 'cycle') return workspace.loadCycle();
            const level = byId('industry-level')?.value || 'L1';
            setStatus('LOADING INDUSTRIES', 'warning');
            try {
                const payload = await requestJson(`/api/industry/classifications?level=${encodeURIComponent(level)}`);
                if (!lifecycle.active) return;
                const itemCount = renderClassifications(payload);
                setStatus(
                    itemCount ? `${itemCount} INDUSTRIES` : 'NO LOCAL INDUSTRIES',
                    itemCount ? 'ready' : 'warning',
                );
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message, 'error');
            }
        },
        async loadDetail(industryId) {
            if (!lifecycle.active || !industryId) return;
            state.industryId = industryId;
            clearIndustryDetail('正在读取本地行业详情', `${industryId} LOADING`);
            setStatus('LOADING DETAIL', 'warning');
            try {
                const payload = await requestJson(`/api/industry/detail/${encodeURIComponent(industryId)}?limit=520`);
                if (!lifecycle.active) return;
                const result = renderIndustryDetail(payload) || {};
                const complete = result.hasMembers && result.hasCandles;
                const partial = result.hasMembers || result.hasCandles;
                setStatus(
                    complete ? 'DETAIL READY' : (partial ? 'PARTIAL LOCAL DETAIL' : 'NO LOCAL DETAIL'),
                    complete ? 'ready' : 'warning',
                );
            } catch (error) {
                if (error.name !== 'AbortError') {
                    clearIndustryDetail(`行业详情读取失败：${error.message}`, industryId);
                    setStatus(error.message, 'error');
                }
            }
        },
        async loadCycle() {
            if (!lifecycle.active) return;
            const params = new URLSearchParams();
            document.querySelectorAll('#industry-cycle-series input:checked').forEach(input => params.append('series_id', input.value));
            if (byId('industry-cycle-normalize')?.checked) params.set('normalize', '1');
            if (!params.has('series_id')) {
                renderChartMessage('cycleChart', 'industry-cycle-chart', '请选择至少一个产业景气序列');
                setStatus('NO CYCLE SERIES SELECTED', 'warning');
                return;
            }
            renderChartMessage('cycleChart', 'industry-cycle-chart', '正在读取本地产业景气数据');
            setStatus('LOADING CYCLE', 'warning');
            try {
                const payload = await requestJson(`/api/industry/cycle-series?${params}`);
                if (!lifecycle.active) return;
                const hasPoints = renderCycle(payload);
                setStatus(hasPoints ? 'CYCLE READY' : 'NO LOCAL CYCLE DATA', hasPoints ? 'ready' : 'warning');
            } catch (error) {
                if (error.name !== 'AbortError') {
                    renderChartMessage('cycleChart', 'industry-cycle-chart', `产业景气读取失败\n${error.message}`);
                    setStatus(error.message, 'error');
                }
            }
        },
    };
    global.quantIndustryWorkspace = Object.freeze(workspace);
})(window);
