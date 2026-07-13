'use strict';

(function initMacroWorkspace(global) {
    const lifecycle = { active: false, mounted: false, controller: null, listeners: [], chart: null };
    const state = { catalog: [], family: 'growth', selected: new Set() };
    const families = [
        ['growth', '增长'], ['inflation', '通胀'], ['money', '货币'], ['cycle', '景气'], ['rates', '利率'],
    ];
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
        const response = await fetch(url, { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
        if (lifecycle.controller === controller) lifecycle.controller = null;
        return payload;
    }
    function setStatus(message, tone = '') {
        const element = byId('macro-status');
        element.textContent = message;
        element.dataset.tone = tone;
    }
    function renderFamilies() {
        byId('macro-family-nav').innerHTML = families.map(([family, label]) => `
            <button class="domain-tab ${family === state.family ? 'active' : ''}" data-macro-family="${family}" type="button">${label}</button>
        `).join('');
    }
    function renderCatalog() {
        const items = state.catalog.filter(item => item.family === state.family);
        if (!items.some(item => state.selected.has(item.series_id))) {
            state.selected.clear();
            if (items[0]) state.selected.add(items[0].series_id);
        }
        byId('macro-series-list').innerHTML = items.map(item => `
            <label class="domain-series-option">
                <input type="checkbox" value="${escapeHtml(item.series_id)}" ${state.selected.has(item.series_id) ? 'checked' : ''}>
                <span><b>${escapeHtml(item.label)}</b><small>${escapeHtml(item.frequency)} / ${escapeHtml(item.unit)} / ${escapeHtml(item.field)}</small></span>
            </label>
        `).join('') || '<div class="state-empty">该类别暂无本地序列定义</div>';
    }
    function renderExact(payload) {
        const series = Array.isArray(payload.series) ? payload.series : [];
        const periods = [...new Set(series.flatMap(item => (item.points || []).map(point => point.period)))].sort();
        const values = new Map(series.map(item => [item.series_id, new Map((item.points || []).map(point => [point.period, point.value]))]));
        byId('macro-exact-head').innerHTML = `<tr><th>期间</th>${series.map(item => `<th>${escapeHtml(item.label)} (${escapeHtml(item.unit)})</th>`).join('')}</tr>`;
        byId('macro-exact-body').innerHTML = periods.slice(-500).map(period => `<tr><td>${escapeHtml(period)}</td>${series.map(item => `<td>${escapeHtml(values.get(item.series_id)?.get(period) ?? '--')}</td>`).join('')}</tr>`).join('') || '<tr><td class="state-empty">当前选择暂无本地数据</td></tr>';
        byId('macro-unit-legend').innerHTML = series.map(item => `<span>${escapeHtml(item.label)}: ${escapeHtml(item.unit)} / ${escapeHtml(item.frequency)}</span>`).join('');
    }
    function renderChart(payload) {
        const series = Array.isArray(payload.series) ? payload.series : [];
        const periods = [...new Set(series.flatMap(item => (item.points || []).map(point => point.period)))].sort();
        const units = payload.axis_mode === 'shared'
            ? [series[0]?.unit || 'value']
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
        if (!global.echarts) return;
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('macro-chart'));
        lifecycle.chart.setOption({
            animation: false,
            backgroundColor: '#000000',
            color: ['#ff6900', '#ff3131', '#00ff41', '#ffd700'],
            tooltip: { trigger: 'axis', backgroundColor: '#080808', borderColor: '#ff6900', textStyle: { color: '#f2f2f2' } },
            legend: { top: 2, textStyle: { color: '#b8b8b8' } },
            grid: { left: 58 + Math.max(0, Math.ceil(units.length / 2) - 1) * 52, right: 28 + Math.max(0, Math.floor(units.length / 2) - 1) * 52, top: 34, bottom: 36 },
            xAxis: { type: 'category', data: periods, axisLine: { lineStyle: { color: '#303030' } }, axisLabel: { color: '#8c8c8c' } },
            yAxis: yAxes,
            series: series.map(item => {
                const values = new Map((item.points || []).map(point => [point.period, point.value]));
                return { name: `${item.label} (${item.unit})`, type: 'line', yAxisIndex: axisForUnit.get(item.unit || 'value') || 0, showSymbol: false, data: periods.map(period => values.get(period) ?? null) };
            }),
        }, true);
    }

    const workspace = {
        mount() {
            if (lifecycle.mounted) return;
            listen(byId('macro-refresh'), 'click', () => workspace.refresh());
            listen(byId('macro-compare'), 'click', () => workspace.loadSeries());
            listen(byId('macro-family-nav'), 'click', event => {
                const button = event.target.closest('[data-macro-family]');
                if (!button) return;
                state.family = button.dataset.macroFamily;
                renderFamilies();
                renderCatalog();
                workspace.loadSeries();
            });
            listen(byId('macro-series-list'), 'change', event => {
                if (!event.target.matches('input[type="checkbox"]')) return;
                if (event.target.checked) {
                    if (state.selected.size >= 4) event.target.checked = false;
                    else state.selected.add(event.target.value);
                } else state.selected.delete(event.target.value);
            });
            lifecycle.mounted = true;
        },
        async activate() {
            workspace.mount();
            if (lifecycle.active) { lifecycle.chart?.resize(); return; }
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
            lifecycle.chart = null;
        },
        async refresh() {
            if (!lifecycle.active) return;
            setStatus('LOADING CATALOG', 'warning');
            try {
                const payload = await requestJson('/api/macro/catalog');
                if (!lifecycle.active) return;
                state.catalog = Array.isArray(payload.items) ? payload.items : [];
                renderFamilies();
                renderCatalog();
                setStatus(`${state.catalog.length} SERIES`, 'ready');
                await workspace.loadSeries();
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message, 'error');
            }
        },
        async loadSeries() {
            if (!lifecycle.active || !state.selected.size) return;
            const params = new URLSearchParams({ max_points: '2000' });
            [...state.selected].forEach(seriesId => params.append('series_id', seriesId));
            setStatus('LOADING SERIES', 'warning');
            try {
                const payload = await requestJson(`/api/macro/series?${params}`);
                if (!lifecycle.active) return;
                renderChart(payload);
                renderExact(payload);
                setStatus('SERIES READY', 'ready');
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message, 'error');
            }
        },
    };
    global.quantMacroWorkspace = Object.freeze(workspace);
})(window);
