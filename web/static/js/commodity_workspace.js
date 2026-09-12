'use strict';

(function initCommodityWorkspace(global) {
    const lifecycle = {
        active: false,
        mounted: false,
        controller: null,
        listeners: [],
        chart: null,
        generation: 0,
    };
    const state = {
        mode: 'series',
        catalog: { items: [], ratios: [] },
        selectedSeries: 'gold.sina_cfd',
        selectedRatio: 'gold_brent',
    };

    const byId = id => document.getElementById(id);
    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[char]);

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
        const timeoutId = global.setTimeout(() => controller.abort(), 30000);
        try {
            const response = await fetch(url, { signal: controller.signal });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(payload.error || `请求失败 (${response.status})`);
                error.code = payload.code || 'REQUEST_FAILED';
                throw error;
            }
            return payload;
        } finally {
            global.clearTimeout(timeoutId);
            if (lifecycle.controller === controller) lifecycle.controller = null;
        }
    }

    function setStatus(message, tone = '') {
        const element = byId('commodity-status');
        if (!element) return;
        element.textContent = message;
        element.dataset.tone = tone;
    }

    function formatValue(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return '--';
        return number.toLocaleString('en-US', { maximumFractionDigits: 6 });
    }

    function renderChartMessage(message) {
        if (!global.echarts || !byId('commodity-chart')) return;
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('commodity-chart'));
        lifecycle.chart.clear();
        lifecycle.chart.setOption({
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

    function renderCards(items) {
        const root = byId('commodity-cards');
        if (!root) return;
        if (!items.length) {
            root.innerHTML = '<div class="state-empty">本地仓库暂无商品观测数据；点击 SYNC 获取来源历史</div>';
            return;
        }
        root.innerHTML = items.slice(0, 4).map(item => {
            const latest = item.latest || {};
            const value = latest.normalized_price ?? latest.close;
            const date = latest.session_date || '--';
            const displayDate = /^\d{8}$/.test(String(date))
                ? `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)}`
                : date;
            const quality = latest.quality_status && latest.quality_status !== 'ok'
                ? latest.quality_status.toUpperCase()
                : (latest.finality || 'FINAL').toUpperCase();
            return `
                <button class="commodity-card" type="button" data-commodity-series="${escapeHtml(item.series_id)}">
                    <span class="commodity-card-label">${escapeHtml(item.label || item.asset)}</span>
                    <strong>${escapeHtml(formatValue(value))}</strong>
                    <span>${escapeHtml(item.normalized_unit || '--')} / ${escapeHtml(displayDate)}</span>
                    <small>${escapeHtml(item.instrument_type || 'observation').toUpperCase()} · ${escapeHtml(quality)}</small>
                </button>
            `;
        }).join('');
    }

    function fillSelect(select, items, current, valueKey, labelKey) {
        if (!select) return current;
        const options = items.map(item => {
            const option = document.createElement('option');
            option.value = item[valueKey];
            option.textContent = item[labelKey] || item[valueKey];
            return option;
        });
        select.replaceChildren(...options);
        const chosen = items.some(item => item[valueKey] === current)
            ? current
            : (items[0]?.[valueKey] || '');
        select.value = chosen;
        return chosen;
    }

    function renderControls(payload) {
        const items = Array.isArray(payload.items) ? payload.items : [];
        const ratios = Array.isArray(payload.ratios) ? payload.ratios : [];
        state.catalog = { ...payload, items, ratios };
        state.selectedSeries = fillSelect(
            byId('commodity-series-select'), items, state.selectedSeries, 'series_id', 'label',
        );
        state.selectedRatio = fillSelect(
            byId('commodity-ratio-select'), ratios, state.selectedRatio, 'ratio_id', 'label',
        );
        const sourceMeta = byId('commodity-source-meta');
        if (sourceMeta) sourceMeta.textContent = `${payload.source || 'SOURCE'} / CLOSE / DAILY`;
        updateModeControls();
    }

    function updateModeControls() {
        const isRatio = state.mode === 'ratio';
        const seriesField = byId('commodity-series-select')?.closest('.domain-field');
        const ratioField = byId('commodity-ratio-select')?.closest('.domain-field');
        if (seriesField) seriesField.hidden = isRatio;
        if (ratioField) ratioField.hidden = !isRatio;
    }

    function renderQuality(text, tone = '') {
        const element = byId('commodity-quality');
        if (!element) return;
        element.textContent = text;
        element.dataset.tone = tone;
    }

    function chartBase(xValues, unit, tooltipFormatter) {
        return {
            animation: false,
            backgroundColor: '#000000',
            grid: { left: 62, right: 28, top: 28, bottom: 40 },
            tooltip: {
                trigger: 'axis',
                backgroundColor: '#080808',
                borderColor: '#ff6900',
                textStyle: { color: '#f2f2f2' },
                formatter: tooltipFormatter,
            },
            xAxis: {
                type: 'category',
                data: xValues,
                axisLine: { lineStyle: { color: '#303030' } },
                axisLabel: { color: '#8c8c8c' },
            },
            yAxis: {
                type: 'value',
                scale: true,
                name: unit || '',
                nameTextStyle: { color: '#8c8c8c' },
                splitLine: { lineStyle: { color: '#181818' } },
                axisLabel: { color: '#8c8c8c' },
            },
        };
    }

    function renderSeries(payload) {
        const points = Array.isArray(payload.points) ? payload.points : [];
        const spec = payload.series || {};
        const unit = spec.normalized_unit || '--';
        byId('commodity-chart-title').textContent = spec.label || spec.series_id || 'SERIES';
        byId('commodity-chart-meta').textContent = `${payload.total || points.length} POINTS / CFD / CLOSE`;
        byId('commodity-chart-unit').textContent = `UNIT ${unit}`;
        renderQuality(
            `QUALITY ${payload.quality?.warning_count || 0} WARNINGS · ${payload.quality?.provisional_count || 0} PROVISIONAL`,
            payload.quality?.warning_count ? 'warning' : '',
        );
        if (!points.length) {
            renderChartMessage('该商品暂无本地历史数据');
            return false;
        }
        if (!global.echarts) return true;
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('commodity-chart'));
        const dates = points.map(point => point.date || point.session_date || '--');
        lifecycle.chart.setOption({
            ...chartBase(dates, unit, params => {
                const param = Array.isArray(params) ? params[0] : params;
                const point = points[param?.dataIndex] || {};
                return `${point.date || '--'}<br/>${escapeHtml(spec.label || spec.series_id || 'SERIES')}: ${formatValue(point.value ?? point.normalized_price)} ${escapeHtml(unit)}`;
            }),
            series: [{
                name: spec.label || spec.series_id,
                type: 'line',
                data: points.map(point => point.normalized_price ?? point.close ?? null),
                showSymbol: false,
                connectNulls: false,
                lineStyle: { color: '#ff6900', width: 1.5 },
                itemStyle: { color: '#ff6900' },
            }],
        }, true);
        return true;
    }

    function renderRatio(payload) {
        const points = Array.isArray(payload.points) ? payload.points : [];
        const spec = payload.ratio || {};
        const unit = spec.result_unit || 'ratio';
        byId('commodity-chart-title').textContent = spec.label || spec.ratio_id || 'RATIO';
        byId('commodity-chart-meta').textContent = `${payload.alignment || 'intersection'} / ${payload.quality?.valid_count || 0} VALID / ${payload.quality?.null_count || 0} NULL`;
        byId('commodity-chart-unit').textContent = `UNIT ${unit}`;
        renderQuality(
            `${spec.formula || 'RATIO'} · NULL 点保留原因，不前向填充`,
            payload.quality?.null_count ? 'warning' : '',
        );
        if (!points.length) {
            renderChartMessage('两腿没有共同的本地交易日');
            return false;
        }
        if (!global.echarts) return true;
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('commodity-chart'));
        const dates = points.map(point => point.date || '--');
        lifecycle.chart.setOption({
            ...chartBase(dates, unit, params => {
                const param = Array.isArray(params) ? params[0] : params;
                const point = points[param?.dataIndex] || {};
                const reason = point.reason ? `<br/>状态: ${escapeHtml(point.reason)}` : '';
                return `${point.date || '--'}<br/>比值: ${formatValue(point.value)} ${escapeHtml(unit)}${reason}`;
            }),
            series: [{
                name: spec.label || spec.ratio_id,
                type: 'line',
                data: points.map(point => point.value),
                showSymbol: false,
                connectNulls: false,
                lineStyle: { color: '#00ff41', width: 1.5 },
                itemStyle: { color: '#00ff41' },
            }],
        }, true);
        return true;
    }

    const workspace = {
        mount() {
            if (lifecycle.mounted) return;
            listen(byId('commodity-refresh'), 'click', () => workspace.refresh());
            listen(byId('commodity-load'), 'click', () => workspace.loadChart());
            listen(byId('commodity-series-select'), 'change', event => {
                state.selectedSeries = event.target.value;
                if (state.mode === 'series') workspace.loadChart();
            });
            listen(byId('commodity-ratio-select'), 'change', event => {
                state.selectedRatio = event.target.value;
                if (state.mode === 'ratio') workspace.loadChart();
            });
            listen(byId('commodity-limit-select'), 'change', () => workspace.loadChart());
            listen(byId('commodity-cards'), 'click', event => {
                const card = event.target.closest('[data-commodity-series]');
                if (!card) return;
                state.selectedSeries = card.dataset.commoditySeries;
                state.mode = 'series';
                updateModeControls();
                document.querySelector('[data-futures-mode="global"]')?.click();
                workspace.loadChart();
            });
            listen(global, 'quant:domain-sync-complete', event => {
                if (event.detail?.domain !== 'global_commodities' || !lifecycle.active) return;
                global.setTimeout(() => workspace.refresh(), 300);
            });
            listen(global, 'resize', () => lifecycle.chart?.resize());
            lifecycle.mounted = true;
        },

        setMode(mode) {
            state.mode = mode === 'ratio' ? 'ratio' : 'series';
            updateModeControls();
            if (lifecycle.active) workspace.loadChart();
        },

        async activate(mode = 'series') {
            const nextMode = mode === 'ratio' ? 'ratio' : 'series';
            const changed = state.mode !== nextMode;
            state.mode = nextMode;
            workspace.mount();
            updateModeControls();
            if (lifecycle.active) {
                lifecycle.chart?.resize();
                if (changed) await workspace.loadChart();
                return;
            }
            lifecycle.active = true;
            await workspace.refresh();
        },

        deactivate() {
            lifecycle.active = false;
            lifecycle.generation += 1;
            abortRequest();
            lifecycle.listeners.forEach(([element, type, handler]) => element.removeEventListener(type, handler));
            lifecycle.listeners = [];
            lifecycle.mounted = false;
            lifecycle.chart?.dispose();
            lifecycle.chart = null;
        },

        async refresh() {
            if (!lifecycle.active) return;
            const generation = ++lifecycle.generation;
            setStatus('LOADING CATALOG', 'warning');
            try {
                const payload = await requestJson('/api/commodities/catalog');
                if (!lifecycle.active || generation !== lifecycle.generation) return;
                renderCards(Array.isArray(payload.items) ? payload.items : []);
                renderControls(payload);
                const itemCount = Array.isArray(payload.items) ? payload.items.length : 0;
                setStatus(itemCount ? `${itemCount} SERIES / LOCAL` : 'NO LOCAL COMMODITIES', itemCount ? 'ready' : 'warning');
                await workspace.loadChart();
            } catch (error) {
                if (error.name !== 'AbortError') {
                    renderCards([]);
                    renderChartMessage(`商品目录读取失败\n${error.message}`);
                    setStatus(error.message, 'error');
                }
            }
        },

        async loadChart() {
            if (!lifecycle.active) return;
            const generation = ++lifecycle.generation;
            const limit = byId('commodity-limit-select')?.value || '520';
            const isRatio = state.mode === 'ratio';
            const identifier = isRatio ? state.selectedRatio : state.selectedSeries;
            if (!identifier) {
                renderChartMessage(isRatio ? '请选择一个比值定义' : '请选择一个商品序列');
                return;
            }
            byId('commodity-chart-meta').textContent = 'LOADING LOCAL OBSERVATION';
            renderChartMessage('正在读取本地商品数据');
            try {
                const base = isRatio ? '/api/commodities/ratios/' : '/api/commodities/series/';
                const alignment = isRatio ? '&alignment=intersection' : '';
                const payload = await requestJson(`${base}${encodeURIComponent(identifier)}?limit=${encodeURIComponent(limit)}${alignment}`);
                if (!lifecycle.active || generation !== lifecycle.generation) return;
                const hasPoints = isRatio ? renderRatio(payload) : renderSeries(payload);
                setStatus(hasPoints ? 'OBSERVATION READY' : 'NO LOCAL OBSERVATION', hasPoints ? 'ready' : 'warning');
            } catch (error) {
                if (error.name !== 'AbortError') {
                    renderChartMessage(`商品数据读取失败\n${error.message}`);
                    setStatus(error.message, 'error');
                }
            }
        },
    };

    global.quantCommodityWorkspace = Object.freeze(workspace);
})(window);
