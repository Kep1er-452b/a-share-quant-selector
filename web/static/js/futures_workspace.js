'use strict';

(function initFuturesWorkspace(global) {
    const lifecycle = {
        active: false,
        mounted: false,
        controller: null,
        searchTimer: null,
        listeners: [],
        chart: null,
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
        if (lifecycle.controller) lifecycle.controller.abort();
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
        const element = byId('futures-status');
        if (!element) return;
        element.textContent = message;
        element.dataset.tone = tone;
    }

    function renderContracts(payload) {
        const body = byId('futures-contracts-body');
        const items = Array.isArray(payload.items) ? payload.items.slice(0, 300) : [];
        if (!items.length) {
            body.innerHTML = '<tr><td colspan="4" class="state-empty">本地仓库暂无匹配合约</td></tr>';
            return;
        }
        body.innerHTML = items.map(item => `
            <tr class="domain-click-row" data-futures-symbol="${escapeHtml(item.symbol)}">
                <td>${escapeHtml(item.symbol)}</td>
                <td>${escapeHtml(item.product || '--')}</td>
                <td>${escapeHtml(item.exchange || '--')}</td>
                <td>${escapeHtml(item.active_to || item.delist_date || '--')}</td>
            </tr>
        `).join('');
    }

    function renderKline(payload) {
        const candles = Array.isArray(payload.candles) ? payload.candles : [];
        byId('futures-chart-title').textContent = payload.symbol || 'SELECT CONTRACT';
        byId('futures-chart-meta').textContent = candles.length
            ? `${candles.length} BARS / SETTLE ${candles.at(-1).settle ?? '--'} / OI ${candles.at(-1).oi ?? '--'}`
            : 'NO LOCAL DAILY DATA';
        if (!global.echarts) return;
        if (!lifecycle.chart) lifecycle.chart = global.echarts.init(byId('futures-kline'));
        lifecycle.chart.setOption({
            animation: false,
            backgroundColor: '#000000',
            grid: { left: 58, right: 22, top: 24, bottom: 36 },
            tooltip: { trigger: 'axis', backgroundColor: '#080808', borderColor: '#ff6900', textStyle: { color: '#f2f2f2' } },
            xAxis: { type: 'category', data: candles.map(row => row.trade_date), axisLine: { lineStyle: { color: '#303030' } }, axisLabel: { color: '#8c8c8c' } },
            yAxis: { scale: true, splitLine: { lineStyle: { color: '#181818' } }, axisLabel: { color: '#8c8c8c' } },
            series: [{
                type: 'candlestick',
                data: candles.map(row => [row.open, row.close, row.low, row.high]),
                itemStyle: { color: '#ff3131', color0: '#00ff41', borderColor: '#ff3131', borderColor0: '#00ff41' },
            }],
        }, true);
    }

    const workspace = {
        mount() {
            if (lifecycle.mounted) return;
            listen(byId('futures-refresh'), 'click', () => workspace.refresh());
            listen(byId('futures-search'), 'input', () => {
                window.clearTimeout(lifecycle.searchTimer);
                lifecycle.searchTimer = window.setTimeout(() => workspace.refresh(), 180);
            });
            listen(byId('futures-exchange'), 'change', () => workspace.refresh());
            listen(byId('futures-product'), 'change', () => workspace.refresh());
            listen(byId('futures-contracts-body'), 'click', event => {
                const row = event.target.closest('[data-futures-symbol]');
                if (row) workspace.loadKline(row.dataset.futuresSymbol);
            });
            lifecycle.mounted = true;
        },

        async activate() {
            workspace.mount();
            if (lifecycle.active) {
                lifecycle.chart?.resize();
                return;
            }
            lifecycle.active = true;
            await workspace.refresh();
        },

        deactivate() {
            lifecycle.active = false;
            abortRequest();
            window.clearTimeout(lifecycle.searchTimer);
            lifecycle.searchTimer = null;
            lifecycle.listeners.forEach(([element, type, handler]) => element.removeEventListener(type, handler));
            lifecycle.listeners = [];
            lifecycle.mounted = false;
            lifecycle.chart?.dispose();
            lifecycle.chart = null;
        },

        async refresh() {
            if (!lifecycle.active) return;
            const params = new URLSearchParams({ limit: '300' });
            const query = byId('futures-search')?.value.trim();
            const exchange = byId('futures-exchange')?.value;
            const product = byId('futures-product')?.value.trim();
            if (query) params.set('q', query);
            if (exchange) params.set('exchange', exchange);
            if (product) params.set('product', product);
            setStatus('LOADING', 'warning');
            try {
                const payload = await requestJson(`/api/futures/contracts?${params}`);
                if (!lifecycle.active) return;
                renderContracts(payload);
                setStatus(`${payload.total || 0} CONTRACTS`, 'ready');
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message, 'error');
            }
        },

        async loadKline(symbol) {
            if (!lifecycle.active || !symbol) return;
            setStatus('LOADING KLINE', 'warning');
            try {
                const payload = await requestJson(`/api/futures/kline/${encodeURIComponent(symbol)}?limit=520`);
                if (!lifecycle.active) return;
                renderKline(payload);
                setStatus('KLINE READY', 'ready');
            } catch (error) {
                if (error.name !== 'AbortError') setStatus(error.message, 'error');
            }
        },
    };

    global.quantFuturesWorkspace = Object.freeze(workspace);
})(window);
