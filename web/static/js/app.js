'use strict';

const PAGE_TITLES = {
    dashboard: '系统概览',
    heatmap: '市场云图',
    stocks: '股票列表',
    selection: '执行选股',
    strategies: '策略配置',
};

const BOARD_LABELS = {
    main: '主板',
    chinext: '创业板',
    star: '科创板',
};

const state = {
    currentPage: 'dashboard',
    chartInstance: null,
    heatmapChart: null,
    allStocksCache: [],
    stocksLoaded: false,
    stocksLoadingPromise: null,
    strategies: [],
    boardOptions: [],
    boardCounts: {},
    selectionOptionsLoaded: false,
    systemHalted: false,
    status: 'ready',
    toastTimer: null,
    activeControllers: new Set(),
    selectionPollTimer: null,
    currentSelectionJobId: null,
    updatePollTimer: null,
    currentUpdateJobId: null,
    localProgressTimer: null,
    jobStartTime: null,
    serverElapsedBase: 0,
    heatmapMetaLoaded: false,
    heatmapMarkets: [],
    heatmapScope: 'all',
    heatmapMetric: 'daily',
    heatmapGroups: [],
    heatmapLoading: false,
    updateModalStep: 'provider',
    updateProvider: null,
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatNumber(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return '--';
    }
    return numeric.toLocaleString('zh-CN');
}

function formatDateTime(value) {
    if (!value) {
        return '--';
    }

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    return new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    }).format(date);
}

function formatPercent(value, digits = 2) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return '--';
    }
    const fixed = numeric.toFixed(digits);
    return `${numeric > 0 ? '+' : ''}${fixed}%`;
}

function heatmapMetricLabel(metric) {
    const mapping = {
        daily: '日线',
        weekly: '本周以来',
        monthly: '本月以来',
        five_day: '最近五个交易日',
    };
    return mapping[metric] || metric;
}

function heatmapScopeLabel(scope) {
    const mapping = {
        all: 'A股全图',
        main: '上证A股',
        chinext: '创业板',
        star: '科创板',
        bse: '北交所A股',
    };
    return mapping[scope] || scope;
}

function signedClass(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric === 0) {
        return '';
    }
    return numeric > 0 ? 'up' : 'down';
}

function heatmapColor(changePct) {
    const value = Number(changePct);
    if (!Number.isFinite(value)) {
        return '#45515d';
    }
    const clamped = Math.max(-4, Math.min(4, value));
    if (clamped >= 0) {
        const ratio = clamped / 4;
        const red = Math.round(79 + (201 - 79) * ratio);
        const green = Math.round(90 - (90 - 65) * ratio);
        const blue = Math.round(102 - (102 - 65) * ratio);
        return `rgb(${red}, ${green}, ${blue})`;
    }
    const ratio = Math.abs(clamped) / 4;
    const red = Math.round(69 - (69 - 18) * ratio);
    const green = Math.round(81 + (156 - 81) * ratio);
    const blue = Math.round(93 - (93 - 85) * ratio);
    return `rgb(${red}, ${green}, ${blue})`;
}

function heatmapGroupPalette(index) {
    const palette = [
        { base: 'rgba(125, 139, 156, 0.20)', hover: 'rgba(125, 139, 156, 0.06)' },
        { base: 'rgba(111, 126, 145, 0.18)', hover: 'rgba(111, 126, 145, 0.05)' },
        { base: 'rgba(140, 130, 150, 0.18)', hover: 'rgba(140, 130, 150, 0.05)' },
        { base: 'rgba(138, 125, 118, 0.18)', hover: 'rgba(138, 125, 118, 0.05)' },
        { base: 'rgba(111, 140, 136, 0.18)', hover: 'rgba(111, 140, 136, 0.05)' },
        { base: 'rgba(118, 128, 109, 0.18)', hover: 'rgba(118, 128, 109, 0.05)' },
    ];
    return palette[index % palette.length];
}

function toast(message, type = 'info', duration = 2600) {
    const el = document.getElementById('toast');
    if (!el) {
        return;
    }

    window.clearTimeout(state.toastTimer);
    el.textContent = message;
    el.className = `toast ${type} show`;
    state.toastTimer = window.setTimeout(() => {
        el.className = 'toast';
    }, duration);
}

function setStatus(mode) {
    state.status = mode;
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const sidebar = document.getElementById('sidebar-status-text');
    const hero = document.getElementById('hero-session-state');
    const stat = document.getElementById('stat-session');
    const map = {
        ready: 'READY',
        running: 'RUNNING',
        halted: 'HALTED',
        error: 'ERROR',
    };
    const label = map[mode] || map.ready;

    if (dot) {
        dot.className = `status-dot ${mode}`;
    }
    if (text) {
        text.textContent = label;
    }
    if (sidebar) {
        sidebar.textContent = label;
    }
    if (hero) {
        hero.textContent = label;
    }
    if (stat) {
        stat.textContent = label;
    }
}

function classifyBoard(code) {
    const stockCode = String(code || '').trim();
    if (stockCode.startsWith('688') || stockCode.startsWith('689')) {
        return 'star';
    }
    if (stockCode.startsWith('300') || stockCode.startsWith('301')) {
        return 'chinext';
    }
    return 'main';
}

function boardBadge(boardOrCode) {
    const board = BOARD_LABELS[boardOrCode] ? boardOrCode : classifyBoard(boardOrCode);
    return `<span class="board-badge ${board}">${BOARD_LABELS[board] || board}</span>`;
}

function abortActiveRequests() {
    state.activeControllers.forEach(controller => controller.abort());
    state.activeControllers.clear();
}

function stopSelectionPolling() {
    if (state.selectionPollTimer) {
        window.clearInterval(state.selectionPollTimer);
        state.selectionPollTimer = null;
    }
    stopLocalProgressTimer();
}

function stopUpdatePolling() {
    if (state.updatePollTimer) {
        window.clearInterval(state.updatePollTimer);
        state.updatePollTimer = null;
    }
}

function scrollResultsToTop() {
    const container = document.querySelector('.results-panel-body');
    if (container) {
        container.scrollTop = 0;
    }
}

function scrollStocksToTop() {
    const container = document.querySelector('#stocks-page .table-wrap');
    if (container) {
        container.scrollTop = 0;
        container.scrollLeft = 0;
    }
    window.scrollTo(0, 0);
}

function queueStocksScrollReset() {
    scrollStocksToTop();
    window.requestAnimationFrame(() => {
        scrollStocksToTop();
        window.requestAnimationFrame(scrollStocksToTop);
    });
    window.setTimeout(scrollStocksToTop, 0);
}

function startLocalProgressTimer(serverElapsed) {
    stopLocalProgressTimer();
    state.jobStartTime = Date.now();
    state.serverElapsedBase = Number(serverElapsed) || 0;
    state.localProgressTimer = window.setInterval(updateLocalElapsedTime, 200);
}

function stopLocalProgressTimer() {
    if (state.localProgressTimer) {
        window.clearInterval(state.localProgressTimer);
        state.localProgressTimer = null;
    }
    state.jobStartTime = null;
    state.serverElapsedBase = 0;
}

function updateLocalElapsedTime() {
    if (!state.jobStartTime) return;

    const localElapsed = Math.floor((Date.now() - state.jobStartTime) / 1000);
    const totalElapsed = state.serverElapsedBase + localElapsed;

    const elapsedElements = document.querySelectorAll('.selection-progress-time, .selection-summary-card:nth-child(1) .value');
    elapsedElements.forEach(el => {
        if (el && el.classList.contains('selection-progress-time')) {
            el.textContent = formatDateTime(new Date());
        }
    });

    const elapsedCard = document.querySelector('.selection-progress-grid .selection-summary-card:first-child .value');
    if (elapsedCard) {
        elapsedCard.textContent = formatElapsed(totalElapsed);
    }

    const progressSub = document.querySelector('.selection-progress-sub');
    if (progressSub) {
        const currentStockMatch = progressSub.textContent.match(/已执行\s+\d+:\d+/);
        if (currentStockMatch) {
            progressSub.textContent = progressSub.textContent.replace(
                /已执行\s+\d+:\d+/,
                `已执行 ${formatElapsed(totalElapsed)}`
            );
        }
    }
}

async function apiFetch(url, fetchOptions = {}, config = {}) {
    const { allowWhenHalted = false, interpretHalt = true } = config;

    if (state.systemHalted && !allowWhenHalted) {
        throw new Error('系统已急停，当前操作不可执行');
    }

    const controller = new AbortController();
    state.activeControllers.add(controller);

    try {
        const response = await fetch(url, {
            ...fetchOptions,
            signal: controller.signal,
        });

        const contentType = response.headers.get('content-type') || '';
        const data = contentType.includes('application/json')
            ? await response.json()
            : await response.text();

        if (interpretHalt && data && typeof data === 'object' && data.halted) {
            applyHaltState(data.error || '系统已急停，重启服务器后方可恢复');
            throw new Error(data.error || '系统已急停');
        }

        if (!response.ok) {
            const message = data && typeof data === 'object'
                ? (data.error || data.message || `请求失败 (${response.status})`)
                : `请求失败 (${response.status})`;
            throw new Error(message);
        }

        return data;
    } finally {
        state.activeControllers.delete(controller);
    }
}

function switchPage(page) {
    if (state.systemHalted) {
        return;
    }

    if (page !== 'heatmap') {
        exitHeatmapFullscreenIfNeeded().catch(() => {});
    }

    state.currentPage = page;

    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
    });

    document.querySelectorAll('.page').forEach(pageEl => {
        pageEl.classList.toggle('active', pageEl.id === `${page}-page`);
    });

    document.getElementById('page-title').textContent = PAGE_TITLES[page] || page;
    window.scrollTo(0, 0);

    if (page === 'dashboard') {
        loadStats();
    }
    if (page === 'heatmap') {
        loadHeatmapMeta();
        loadHeatmap();
    }
    if (page === 'stocks') {
        loadStocks();
        queueStocksScrollReset();
    }
    if (page === 'selection') {
        loadSelectionOptions();
    }
    if (page === 'strategies') {
        loadStrategies();
    }
}

async function loadStats() {
    if (state.systemHalted) {
        return;
    }

    try {
        const result = await apiFetch('/api/stats');
        if (!result.success) {
            return;
        }

        const data = result.data;
        const boardCounts = data.board_counts || {};

        document.getElementById('stat-stocks').textContent = formatNumber(data.total_stocks);
        document.getElementById('stat-date').textContent = escapeHtml(data.latest_date || '--');
        document.getElementById('stat-strategies').textContent = formatNumber(data.strategies);
        document.getElementById('hero-strategy-count').textContent = formatNumber(data.strategies);
        document.getElementById('hero-latest-date').textContent = escapeHtml(data.latest_date || '--');
        document.getElementById('stocks-total-label').textContent = `当前本地股票池 ${formatNumber(data.total_stocks)} 只`;

        document.getElementById('board-count-main').textContent = formatNumber(boardCounts.main || 0);
        document.getElementById('board-count-chinext').textContent = formatNumber(boardCounts.chinext || 0);
        document.getElementById('board-count-star').textContent = formatNumber(boardCounts.star || 0);

        document.getElementById('hero-universe').textContent = 'ALL BOARDS';
        document.getElementById('sidebar-universe-text').textContent = 'ALL BOARDS';
        if (data.halted) {
            setStatus('halted');
        } else if (state.status !== 'running') {
            setStatus('ready');
        }
    } catch (error) {
        console.error('loadStats failed:', error);
    }
}

async function loadHeatmapMeta(forceReload = false) {
    if (state.systemHalted) {
        return;
    }
    if (state.heatmapMetaLoaded && !forceReload) {
        renderHeatmapFilters();
        return;
    }

    try {
        const result = await apiFetch('/api/heatmap/meta');
        if (!result.success) {
            throw new Error(result.error || '市场云图元数据加载失败');
        }
        const data = result.data || {};
        state.heatmapMetaLoaded = true;
        state.heatmapMarkets = data.markets || [];
        const cacheErrors = data.cache_status?.errors || {};
        const unmappedCount = Number(data.cache_status?.industry_unmapped_count || 0);
        const refreshPending = Boolean(data.cache_status?.refresh_pending);
        document.getElementById('heatmap-latest-date').textContent = data.latest_date || '--';
        document.getElementById('heatmap-cache-note').textContent = Object.keys(cacheErrors).length
            ? `缓存已降级使用旧数据: ${Object.keys(cacheErrors).join(' / ')}`
            : (refreshPending
                ? '行业分类缓存正在后台补全，未分类会逐步减少'
                : (unmappedCount > 0
                ? `行业缓存仍有 ${formatNumber(unmappedCount)} 只股票未归类，系统会继续尝试补全`
                : '行业分类缓存已完整匹配，板块可直接点开查看全部股票'));
        renderHeatmapFilters();
    } catch (error) {
        document.getElementById('heatmap-cache-note').textContent = `元数据加载失败: ${error.message}`;
    }
}

function renderHeatmapFilters() {
    const marketContainer = document.getElementById('heatmap-market-filter');
    if (marketContainer && state.heatmapMarkets.length) {
        marketContainer.innerHTML = state.heatmapMarkets.map(item => `
            <button
                class="heatmap-filter-btn ${state.heatmapScope === item.key ? 'active' : ''} ${item.enabled ? '' : 'disabled'}"
                data-scope="${escapeHtml(item.key)}"
                type="button"
                ${item.enabled ? '' : 'disabled'}
                title="${escapeHtml(item.hint || '')}">
                ${escapeHtml(item.label)}
            </button>
        `).join('');
    }

    const metricContainer = document.getElementById('heatmap-metric-filter');
    if (metricContainer) {
        metricContainer.querySelectorAll('[data-metric]').forEach(button => {
            button.classList.toggle('active', button.dataset.metric === state.heatmapMetric);
        });
    }
}

function setHeatmapLoading(isLoading, message = '正在生成市场云图...') {
    state.heatmapLoading = isLoading;
    const wrap = document.querySelector('.heatmap-chart-wrap');
    const overlay = document.getElementById('heatmap-loading-overlay');
    if (!wrap || !overlay) {
        return;
    }
    wrap.classList.toggle('loading', isLoading);
    overlay.textContent = message;
}

function buildTickerText(stats, latestDate) {
    const medianText = Number.isFinite(Number(stats?.median_change_pct))
        ? `${Number(stats.median_change_pct).toFixed(2)}%`
        : '--';
    const parts = [
        `上涨家数 ${formatNumber(stats?.up_count ?? 0)}`,
        `下跌家数 ${formatNumber(stats?.down_count ?? 0)}`,
        `平盘家数 ${formatNumber(stats?.flat_count ?? 0)}`,
        `中位涨幅 ${medianText}`,
        `最新交易日 ${latestDate || '--'}`,
    ];
    return `${parts.join('   •   ')}   •   ${parts.join('   •   ')}`;
}

function renderHeatmapIndices(indices) {
    const container = document.getElementById('heatmap-index-grid');
    if (!container) {
        return;
    }
    if (!indices || !indices.length) {
        container.innerHTML = '<div class="state-empty">暂无指数摘要缓存</div>';
        return;
    }
    container.innerHTML = indices.map(item => `
        <div class="heatmap-index-card">
            <div class="heatmap-index-name">${escapeHtml(item.name)}</div>
            <span class="heatmap-index-price">${escapeHtml(formatNumber(item.latest_price))}</span>
            <span class="heatmap-index-change ${signedClass(item.change_pct)}">${escapeHtml(formatPercent(item.change_pct))}</span>
        </div>
    `).join('');
}

function getHeatmapFullscreenShell() {
    return document.getElementById('heatmap-fullscreen-shell');
}

function getFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function fullscreenEnabled() {
    return Boolean(document.fullscreenEnabled || document.webkitFullscreenEnabled);
}

function resizeHeatmapChart(delay = 0) {
    window.setTimeout(() => {
        if (state.heatmapChart) {
            state.heatmapChart.resize();
        }
    }, delay);
}

function syncHeatmapFullscreenState() {
    const shell = getHeatmapFullscreenShell();
    const button = document.getElementById('heatmap-fullscreen-btn');
    const label = button ? button.querySelector('.heatmap-fullscreen-label') : null;
    const isFullscreen = Boolean(shell && getFullscreenElement() === shell);

    document.body.classList.toggle('heatmap-fullscreen-active', isFullscreen);
    if (button) {
        button.classList.toggle('active', isFullscreen);
        button.setAttribute('aria-pressed', String(isFullscreen));
    }
    if (label) {
        label.textContent = isFullscreen ? '退出全屏' : '全屏显示';
    }

    resizeHeatmapChart(40);
    resizeHeatmapChart(220);
}

async function exitHeatmapFullscreenIfNeeded() {
    const shell = getHeatmapFullscreenShell();
    if (shell && getFullscreenElement() === shell) {
        if (document.exitFullscreen) {
            await document.exitFullscreen();
        } else if (document.webkitExitFullscreen) {
            document.webkitExitFullscreen();
        }
    }
}

async function toggleHeatmapFullscreen() {
    if (state.systemHalted) {
        return;
    }

    const shell = getHeatmapFullscreenShell();
    if (!shell || !fullscreenEnabled()) {
        toast('当前浏览器不支持全屏模式', 'error');
        return;
    }

    try {
        if (getFullscreenElement() === shell) {
            await exitHeatmapFullscreenIfNeeded();
            return;
        }
        if (getFullscreenElement()) {
            if (document.exitFullscreen) {
                await document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            }
        }
        if (shell.requestFullscreen) {
            await shell.requestFullscreen();
        } else if (shell.webkitRequestFullscreen) {
            shell.webkitRequestFullscreen();
        } else {
            throw new Error('浏览器未提供可用的全屏接口');
        }
    } catch (error) {
        toast(`全屏切换失败: ${error.message}`, 'error');
    }
}

function renderHeatmapChart(groups) {
    const container = document.getElementById('heatmap-chart');
    if (!container) {
        return;
    }
    if (!window.echarts) {
        container.innerHTML = '<div class="state-empty">ECharts 加载失败，无法渲染云图。</div>';
        return;
    }
    if (!state.heatmapChart) {
        state.heatmapChart = window.echarts.init(container);
        window.addEventListener('resize', () => {
            if (state.heatmapChart) {
                state.heatmapChart.resize();
            }
        });
    }

    state.heatmapGroups = Array.isArray(groups) ? groups : [];

    const treeData = state.heatmapGroups.map((group, index) => {
        const palette = heatmapGroupPalette(index);
        return {
            name: `${group.name}`,
            group_name: group.name,
            value: Number(group.market_cap || 0) / 1e8,
            stock_count: group.stock_count,
            change_pct: group.change_pct,
            itemStyle: {
                color: palette.base,
            },
            emphasis: {
                itemStyle: {
                    color: palette.hover,
                    borderColor: 'rgba(255,255,255,0.26)',
                    borderWidth: 3,
                },
            },
            children: (group.children || []).map(item => ({
                name: `${item.name}\n${Number.isFinite(Number(item.change_pct)) ? formatPercent(item.change_pct) : '--'}`,
                value: item.value,
                code: item.code,
                industry: item.industry,
                board: item.board,
                latest_price: item.latest_price,
                change_pct: item.change_pct,
                market_cap: item.market_cap,
                itemStyle: {
                    color: heatmapColor(item.change_pct),
                },
            })),
        };
    });

    state.heatmapChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            confine: true,
            formatter(params) {
                const data = params.data || {};
                if (!data.code) {
                    const groupChange = Number.isFinite(Number(data.change_pct))
                        ? formatPercent(data.change_pct)
                        : '--';
                    return [
                        `<strong>${escapeHtml(data.group_name || params.name)}</strong>`,
                        `股票数: ${formatNumber(data.stock_count || data.children?.length || 0)}`,
                        `平均涨跌幅: ${escapeHtml(groupChange)}`,
                        '点击板块边框可查看全部个股明细',
                    ].join('<br>');
                }
                const changeText = Number.isFinite(Number(data.change_pct))
                    ? formatPercent(data.change_pct)
                    : '--';
                return [
                    `<strong>${escapeHtml(data.name.split('\n')[0])}</strong>`,
                    `代码: ${escapeHtml(data.code)}`,
                    `行业: ${escapeHtml(data.industry || '未分类')}`,
                    `最新价: ${escapeHtml(data.latest_price)}`,
                    `涨跌幅: ${escapeHtml(changeText)}`,
                    `总市值: ${escapeHtml(formatNumber(((Number(data.market_cap) || 0) / 1e8).toFixed(2)))} 亿`,
                ].join('<br>');
            },
        },
        series: [{
            type: 'treemap',
            roam: false,
            nodeClick: false,
            breadcrumb: { show: false },
            visibleMin: 1,
            squareRatio: 1.25,
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            label: {
                show: true,
                formatter(params) {
                    return params.name;
                },
                color: '#f3f6fa',
                fontSize: 12,
            },
            upperLabel: {
                show: true,
                color: '#f3f6fa',
                height: 22,
                fontSize: 16,
            },
            itemStyle: {
                borderColor: 'rgba(255,255,255,0.08)',
                borderWidth: 1,
                gapWidth: 1,
            },
            emphasis: {
                upperLabel: {
                    color: '#ffffff',
                },
            },
            levels: [
                {
                    itemStyle: {
                        borderColor: 'rgba(255,255,255,0.12)',
                        borderWidth: 2,
                        gapWidth: 2,
                    },
                    upperLabel: {
                        show: true,
                    },
                },
                {
                    itemStyle: {
                        gapWidth: 1,
                        borderColorSaturation: 0.5,
                    },
                },
            ],
            data: treeData,
        }],
    });

    state.heatmapChart.off('click');
    state.heatmapChart.on('click', params => {
        const data = params?.data || {};
        if (data.code) {
            viewStockDetail(data.code, data.name ? String(data.name).split('\n')[0] : '');
            return;
        }
        const groupName = data.group_name || params?.name;
        const group = state.heatmapGroups.find(item => item.name === groupName);
        if (group) {
            openIndustryModal(group);
        }
    });
}

async function loadHeatmap(forceReload = false) {
    if (state.systemHalted) {
        return;
    }
    setHeatmapLoading(true, forceReload ? '正在刷新市场云图...' : '正在生成市场云图...');

    try {
        const result = await apiFetch(`/api/heatmap?scope=${encodeURIComponent(state.heatmapScope)}&metric=${encodeURIComponent(state.heatmapMetric)}`);
        if (!result.success) {
            throw new Error(result.error || '市场云图加载失败');
        }
        const data = result.data || {};
        document.getElementById('heatmap-latest-date').textContent = data.latest_date || '--';
        document.getElementById('heatmap-subtitle').textContent = `按行业聚合，面积映射总市值，颜色映射${heatmapMetricLabel(state.heatmapMetric)}涨跌幅`;
        document.getElementById('heatmap-scope-label').textContent = heatmapScopeLabel(state.heatmapScope);
        document.getElementById('heatmap-metric-label').textContent = heatmapMetricLabel(state.heatmapMetric);
        document.getElementById('heatmap-stock-count').textContent = `${formatNumber(data.stock_count || 0)} 只股票`;
        document.getElementById('heatmap-ticker-track').textContent = buildTickerText(data.ticker_stats, data.latest_date);
        renderHeatmapIndices(data.header_indices || []);
        renderHeatmapChart(data.groups || []);
    } catch (error) {
        const container = document.getElementById('heatmap-chart');
        if (container) {
            container.innerHTML = `<div class="state-empty">市场云图加载失败: ${escapeHtml(error.message)}</div>`;
        }
    } finally {
        setHeatmapLoading(false);
    }
}

async function loadStocks(forceReload = false) {
    if (state.systemHalted) {
        return;
    }

    const tbody = document.getElementById('stocks-tbody');

    if (state.stocksLoaded && state.allStocksCache.length && !forceReload) {
        renderStocks(state.allStocksCache);
        return;
    }

    if (state.stocksLoadingPromise && !forceReload) {
        await state.stocksLoadingPromise;
        renderStocks(state.allStocksCache);
        return;
    }

    tbody.innerHTML = '<tr><td colspan="8" class="state-loading state-table-message">正在载入股票列表...</td></tr>';

    state.stocksLoadingPromise = (async () => {
        let page = 1;
        let totalPages = 1;
        let allStocks = [];

        do {
            const result = await apiFetch(`/api/stocks?page=${page}&per_page=500`);
            if (!result.success) {
                throw new Error(result.error || '股票列表加载失败');
            }

            allStocks = allStocks.concat(result.data);
            totalPages = result.total_pages;
            tbody.innerHTML = `<tr><td colspan="8" class="state-loading state-table-message">已加载 ${formatNumber(allStocks.length)} / ${formatNumber(result.total)} 只股票...</td></tr>`;
            page += 1;
        } while (page <= totalPages);

        state.allStocksCache = allStocks;
        state.stocksLoaded = true;
    })();

    try {
        await state.stocksLoadingPromise;
        renderStocks(state.allStocksCache);
    } catch (error) {
        state.stocksLoaded = false;
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        tbody.innerHTML = `<tr><td colspan="8" class="state-empty state-table-message">加载失败: ${escapeHtml(error.message)}</td></tr>`;
    } finally {
        state.stocksLoadingPromise = null;
    }
}

function renderStocks(stocks) {
    const tbody = document.getElementById('stocks-tbody');

    if (!stocks.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="state-empty">没有匹配的股票</td></tr>';
        queueStocksScrollReset();
        return;
    }

    tbody.innerHTML = stocks.map(stock => {
        const board = stock.board || classifyBoard(stock.code);
        return `
            <tr>
                <td class="code-cell">${escapeHtml(stock.code)}</td>
                <td>${escapeHtml(stock.name || '未知')}</td>
                <td>${boardBadge(board)}</td>
                <td class="mono">${escapeHtml(stock.latest_price)}</td>
                <td class="mono">${escapeHtml(stock.latest_date)}</td>
                <td class="mono">${escapeHtml(stock.market_cap)}</td>
                <td class="mono">${escapeHtml(stock.data_count)}</td>
                <td>
                    <button class="btn btn-ghost view-detail-btn" type="button"
                        data-code="${escapeHtml(stock.code)}"
                        data-name="${escapeHtml(stock.name || '')}">
                        详情
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    queueStocksScrollReset();
}

async function viewStockDetail(code, name) {
    if (state.systemHalted) {
        return;
    }

    document.getElementById('modal-title').textContent = `${code} ${name || ''}`.trim();
    document.getElementById('stock-info').innerHTML = '<div class="state-loading">加载个股详情...</div>';
    document.getElementById('stock-modal').classList.add('active');

    try {
        const result = await apiFetch(`/api/stock/${code}`);
        if (!result.success) {
            throw new Error(result.error || '个股详情加载失败');
        }
        renderStockChart(result.data || []);
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        document.getElementById('stock-info').innerHTML = `<div class="state-empty">加载失败: ${escapeHtml(error.message)}</div>`;
    }
}

function renderStockChart(data) {
    if (!data.length) {
        document.getElementById('stock-info').innerHTML = '<div class="state-empty">暂无图表数据</div>';
        return;
    }

    const reversed = [...data].reverse();
    const labels = reversed.map(item => item.date);
    const prices = reversed.map(item => item.close);
    const kValues = reversed.map(item => item.K);
    const dValues = reversed.map(item => item.D);
    const jValues = reversed.map(item => item.J);

    const canvas = document.getElementById('stock-chart');
    const ctx = canvas.getContext('2d');

    if (state.chartInstance) {
        state.chartInstance.destroy();
    }

    Chart.defaults.color = '#7d8c9a';

    state.chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: '收盘价',
                    data: prices,
                    borderColor: '#f59f0b',
                    backgroundColor: 'rgba(245, 159, 11, 0.08)',
                    fill: true,
                    tension: 0.12,
                    pointRadius: 0,
                    borderWidth: 1.6,
                    yAxisID: 'yPrice',
                },
                {
                    label: 'K',
                    data: kValues,
                    borderColor: '#4ba3ff',
                    pointRadius: 0,
                    borderWidth: 1.1,
                    tension: 0.12,
                    yAxisID: 'yKDJ',
                },
                {
                    label: 'D',
                    data: dValues,
                    borderColor: '#23c483',
                    pointRadius: 0,
                    borderWidth: 1.1,
                    tension: 0.12,
                    yAxisID: 'yKDJ',
                },
                {
                    label: 'J',
                    data: jValues,
                    borderColor: '#ff5c5c',
                    pointRadius: 0,
                    borderWidth: 1.1,
                    tension: 0.12,
                    yAxisID: 'yKDJ',
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    labels: {
                        color: '#b2bfcc',
                        font: {
                            family: '"IBM Plex Mono", monospace',
                            size: 11,
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: {
                        color: '#7d8c9a',
                        maxTicksLimit: 10,
                        font: {
                            family: '"IBM Plex Mono", monospace',
                            size: 10,
                        },
                    },
                    grid: {
                        color: 'rgba(255,255,255,0.06)',
                    },
                },
                yPrice: {
                    type: 'linear',
                    position: 'left',
                    ticks: {
                        color: '#b2bfcc',
                        font: {
                            family: '"IBM Plex Mono", monospace',
                            size: 10,
                        },
                    },
                    grid: {
                        color: 'rgba(255,255,255,0.06)',
                    },
                },
                yKDJ: {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    max: 100,
                    ticks: {
                        color: '#7d8c9a',
                        stepSize: 20,
                        font: {
                            family: '"IBM Plex Mono", monospace',
                            size: 10,
                        },
                    },
                    grid: {
                        drawOnChartArea: false,
                    },
                },
            },
        },
    });

    const latest = data[0];
    const jClass = Number(latest.J) > 80 ? 'down' : (Number(latest.J) < 20 ? 'up' : '');

    document.getElementById('stock-info').innerHTML = `
        <div class="stock-kv">
            <div class="kv-item">
                <div class="kv-label">最新价</div>
                <div class="kv-value">¥${escapeHtml(latest.close)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">最高</div>
                <div class="kv-value">¥${escapeHtml(latest.high)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">最低</div>
                <div class="kv-value">¥${escapeHtml(latest.low)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">成交量</div>
                <div class="kv-value">${formatNumber(latest.volume)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">市值 (亿)</div>
                <div class="kv-value">${escapeHtml(latest.market_cap)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">K</div>
                <div class="kv-value">${escapeHtml(latest.K)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">D</div>
                <div class="kv-value">${escapeHtml(latest.D)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">J</div>
                <div class="kv-value ${jClass}">${escapeHtml(latest.J)}</div>
            </div>
        </div>
    `;
}

function closeModal() {
    document.getElementById('stock-modal').classList.remove('active');
}

function closeIndustryModal() {
    document.getElementById('industry-modal').classList.remove('active');
}

function openIndustryModal(group) {
    if (!group || !Array.isArray(group.children)) {
        return;
    }

    document.getElementById('industry-modal-title').textContent = group.name || '板块详情';
    document.getElementById('industry-modal-subtitle').textContent =
        `${heatmapMetricLabel(state.heatmapMetric)} · ${formatNumber(group.children.length)} 只股票 · 点击板块边框打开的明细视图`;
    document.getElementById('industry-modal-change-header').textContent = `${heatmapMetricLabel(state.heatmapMetric)}涨跌幅`;

    const tbody = document.getElementById('industry-modal-tbody');
    tbody.innerHTML = group.children.map(item => `
        <tr>
            <td class="code-cell">${escapeHtml(item.code)}</td>
            <td>${escapeHtml(item.name || '未知')}</td>
            <td>${boardBadge(item.board || item.code)}</td>
            <td class="mono">${escapeHtml(item.latest_price ?? '--')}</td>
            <td class="mono metric-value ${signedClass(item.change_pct)}">${escapeHtml(formatPercent(item.change_pct))}</td>
            <td class="mono">${escapeHtml(formatNumber(((Number(item.market_cap) || 0) / 1e8).toFixed(2)))}</td>
        </tr>
    `).join('');
    document.getElementById('industry-modal').classList.add('active');
}

function setUpdateModalStep(step) {
    state.updateModalStep = step;
    document.querySelectorAll('#update-modal .update-modal-step').forEach(element => {
        element.classList.toggle('active', element.id === `update-${step}-step`);
    });

    const title = document.getElementById('update-modal-title');
    if (!title) {
        return;
    }
    if (step === 'provider') {
        title.textContent = '选择更新数据源';
    } else if (step === 'token') {
        title.textContent = '输入 Tushare Token';
    } else {
        title.textContent = '更新任务执行中';
    }
}

function openUpdateModal() {
    if (state.systemHalted) {
        toast('系统已急停，无法继续更新数据', 'error');
        return;
    }
    state.updateProvider = null;
    document.getElementById('update-tushare-token').value = '';
    setUpdateModalStep('provider');
    document.getElementById('update-modal').classList.add('active');
}

function closeUpdateModal() {
    if (state.currentUpdateJobId && state.updateModalStep === 'progress') {
        return;
    }
    document.getElementById('update-modal').classList.remove('active');
}

async function startUpdateJob(provider, token = '') {
    setUpdateModalStep('progress');
    document.getElementById('update-provider-value').textContent = provider.toUpperCase();
    document.getElementById('update-progress-headline').textContent = '正在启动更新任务...';
    document.getElementById('update-progress-logs').innerHTML = '<div class="progress-log-row"><span class="progress-log-message">正在创建任务...</span></div>';

    try {
        const result = await apiFetch('/api/update/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                provider,
                tushare_token: token,
            }),
        });
        if (!result.success) {
            throw new Error(result.error || '更新任务启动失败');
        }
        state.currentUpdateJobId = result.job_id;
        renderUpdateJob(result.data || {});
        stopUpdatePolling();
        state.updatePollTimer = window.setInterval(pollUpdateJobStatus, 1000);
        await pollUpdateJobStatus();
    } catch (error) {
        setUpdateModalStep('provider');
        toast(`更新失败: ${error.message}`, 'error');
    }
}

function renderUpdateJob(job) {
    const percent = Number(job.progress_pct || 0);
    const updateButton = document.getElementById('update-data-btn');
    if (updateButton) {
        updateButton.disabled = ['queued', 'running'].includes(job.status);
    }
    document.getElementById('update-progress-bar-fill').style.width = `${Math.min(100, Math.max(0, percent))}%`;
    document.getElementById('update-progress-pct').textContent = `${percent}%`;
    document.getElementById('update-current-step').textContent = job.current_step || '等待执行';
    document.getElementById('update-processed-count').textContent = `${formatNumber(job.processed_count || 0)} / ${formatNumber(job.total_count || 0)}`;
    document.getElementById('update-provider-value').textContent = String(job.provider || '--').toUpperCase();
    document.getElementById('update-elapsed-value').textContent = formatElapsed(job.elapsed_seconds || 0);
    document.getElementById('update-progress-status').textContent = String(job.status || 'queued').toUpperCase();
    document.getElementById('update-progress-headline').textContent = job.error
        ? `更新失败: ${job.error}`
        : (job.current_stock
            ? `当前处理: ${job.current_stock.name || '未知'} (${job.current_stock.code || '--'})`
            : (job.current_step || '更新任务进行中...'));

    const logs = Array.isArray(job.logs) ? [...job.logs].reverse() : [];
    document.getElementById('update-progress-logs').innerHTML = logs.map(item => `
        <div class="progress-log-row">
            <span class="progress-log-time">${escapeHtml(item.time)}</span>
            <span class="progress-log-message">${escapeHtml(item.message)}</span>
        </div>
    `).join('') || '<div class="progress-log-row"><span class="progress-log-message">等待任务启动...</span></div>';
}

async function pollUpdateJobStatus() {
    if (!state.currentUpdateJobId) {
        return;
    }
    try {
        const result = await apiFetch(`/api/update/status/${state.currentUpdateJobId}`, {}, {
            allowWhenHalted: true,
        });
        if (!result.success) {
            throw new Error(result.error || '更新状态同步失败');
        }
        const job = result.data || {};
        renderUpdateJob(job);

        if (job.status === 'completed') {
            stopUpdatePolling();
            state.currentUpdateJobId = null;
            const updateButton = document.getElementById('update-data-btn');
            if (updateButton) {
                updateButton.disabled = false;
            }
            toast('数据更新完成', 'success');
            await loadStats();
            if (state.currentPage === 'heatmap') {
                await Promise.all([loadHeatmapMeta(true), loadHeatmap(true)]);
            } else {
                state.heatmapMetaLoaded = false;
            }
            window.setTimeout(() => {
                document.getElementById('update-modal').classList.remove('active');
            }, 600);
            return;
        }

        if (job.status === 'error') {
            stopUpdatePolling();
            state.currentUpdateJobId = null;
            const updateButton = document.getElementById('update-data-btn');
            if (updateButton) {
                updateButton.disabled = false;
            }
            toast(`更新失败: ${job.error || '未知错误'}`, 'error');
            return;
        }

        if (job.status === 'halted') {
            stopUpdatePolling();
            state.currentUpdateJobId = null;
            applyHaltState(job.error || '系统已急停，当前更新任务已终止。');
        }
    } catch (error) {
        stopUpdatePolling();
        state.currentUpdateJobId = null;
        const updateButton = document.getElementById('update-data-btn');
        if (updateButton) {
            updateButton.disabled = false;
        }
        toast(`更新状态同步失败: ${error.message}`, 'error');
    }
}

function renderBoardOptions(boards) {
    const container = document.getElementById('board-filter');
    container.innerHTML = boards.map(board => `
        <label class="board-option">
            <input class="option-input" type="checkbox" name="board" value="${escapeHtml(board.key)}" checked>
            <span class="board-option-card">
                <span class="board-option-name">${escapeHtml(board.label)}</span>
                <span class="board-option-meta">${formatNumber(board.count)} 只股票</span>
            </span>
        </label>
    `).join('');
}

function renderStrategyOptions(strategies) {
    const container = document.getElementById('strategy-filter');

    if (!strategies.length) {
        container.innerHTML = '<div class="state-empty">未检测到可用策略</div>';
        return;
    }

    container.innerHTML = strategies.map(strategy => `
        <label class="strategy-option">
            <input class="option-input" type="checkbox" name="strategy" value="${escapeHtml(strategy.name)}" checked>
            <span class="strategy-option-card">
                <span class="strategy-option-name">${escapeHtml(strategy.name)}</span>
                <span class="strategy-option-meta">${formatNumber(strategy.param_count)} 个参数</span>
            </span>
        </label>
    `).join('');
}

async function loadSelectionOptions(forceReload = false) {
    if (state.systemHalted) {
        return;
    }

    if (state.selectionOptionsLoaded && !forceReload) {
        updateSelectionSnapshot();
        return;
    }

    try {
        const result = await apiFetch('/api/selection/options');
        if (!result.success) {
            throw new Error(result.error || '选项加载失败');
        }

        state.boardOptions = result.data.boards || [];
        state.boardCounts = Object.fromEntries(state.boardOptions.map(item => [item.key, item.count]));
        state.strategies = result.data.strategies || [];
        state.selectionOptionsLoaded = true;

        renderBoardOptions(state.boardOptions);
        renderStrategyOptions(state.strategies);
        updateSelectionSnapshot();
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }

        document.getElementById('board-filter').innerHTML = `<div class="state-empty">加载失败: ${escapeHtml(error.message)}</div>`;
        document.getElementById('strategy-filter').innerHTML = `<div class="state-empty">加载失败: ${escapeHtml(error.message)}</div>`;
    }
}

function getSelectedBoards() {
    return Array.from(document.querySelectorAll('#board-filter input[name="board"]:checked'))
        .map(input => input.value);
}

function getSelectedStrategies() {
    return Array.from(document.querySelectorAll('#strategy-filter input[name="strategy"]:checked'))
        .map(input => input.value);
}

function updateSelectionSnapshot() {
    const selectedBoards = getSelectedBoards();
    const selectedStrategies = getSelectedStrategies();

    const stockPool = selectedBoards.reduce((sum, key) => sum + (state.boardCounts[key] || 0), 0);

    document.getElementById('selected-boards-count').textContent = formatNumber(selectedBoards.length);
    document.getElementById('selected-strategies-count').textContent = formatNumber(selectedStrategies.length);
    document.getElementById('selection-stock-pool').textContent = formatNumber(stockPool);
    document.getElementById('board-selection-meta').textContent = `${selectedBoards.length} / ${state.boardOptions.length || 0} 已选`;
    document.getElementById('selection-execution-note').textContent = stockPool
        ? `预计扫描 ${formatNumber(stockPool)} 只股票`
        : '当前没有可执行股票池';

    const boardText = selectedBoards.length === Object.keys(BOARD_LABELS).length
        ? 'ALL BOARDS'
        : selectedBoards.map(key => BOARD_LABELS[key]).join(' / ') || 'NONE';
    document.getElementById('sidebar-universe-text').textContent = boardText;
}

function setRunButtonsLoading(isLoading) {
    const buttons = [
        document.getElementById('run-selection-btn'),
        document.getElementById('execute-selection-btn'),
    ];

    buttons.forEach(button => {
        if (!button) {
            return;
        }
        button.disabled = isLoading;
        button.innerHTML = isLoading
            ? `
                <svg viewBox="0 0 12 12" fill="currentColor">
                    <rect x="2" y="2" width="3" height="8"></rect>
                    <rect x="7" y="2" width="3" height="8"></rect>
                </svg>
                运行中
            `
            : `
                <svg viewBox="0 0 12 12" fill="currentColor">
                    <polygon points="2,1 10,6 2,11"></polygon>
                </svg>
                ${button.id === 'execute-selection-btn' ? '立即执行' : '执行选股'}
            `;
    });
}

function buildMetric(label, value, className = '') {
    return `
        <div class="metric">
            <div class="metric-label">${escapeHtml(label)}</div>
            <div class="metric-value ${className}">${escapeHtml(value)}</div>
        </div>
    `;
}

function buildSignalMetrics(signal) {
    const metrics = [];
    metrics.push(buildMetric('当前价', `¥${signal.close ?? '--'}`, 'highlight'));

    const jValue = Number(signal.J);
    const jClass = Number.isFinite(jValue) ? (jValue > 80 ? 'down' : (jValue < 20 ? 'up' : '')) : '';
    metrics.push(buildMetric('J 值', signal.J ?? '--', jClass));
    metrics.push(buildMetric('市值 (亿)', signal.market_cap ?? '--'));

    if (signal.volume_ratio !== undefined) {
        metrics.push(buildMetric('量比', `${signal.volume_ratio}x`));
    } else if (signal.yangyin_ratio_57 !== undefined) {
        metrics.push(buildMetric('57阳阴比', signal.yangyin_ratio_57));
    } else if (signal.yangyin_ratio_14 !== undefined) {
        metrics.push(buildMetric('14阳阴比', signal.yangyin_ratio_14));
    }

    if (signal.hm_short !== undefined && signal.hm_long !== undefined) {
        metrics.push(buildMetric('短/长线', `${signal.hm_short} / ${signal.hm_long}`));
    } else if (signal.wl !== undefined && signal.yl !== undefined) {
        metrics.push(buildMetric('WL / YL', `${signal.wl} / ${signal.yl}`));
    }

    return metrics.join('');
}

function formatElapsed(seconds) {
    const total = Number(seconds) || 0;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function renderSelectionProgress(job) {
    const total = Number(job.total_candidates || 0);
    const completed = Number(job.completed_candidates || 0);
    const selected = Number(job.selected_count || 0);
    const percent = Number(job.progress_pct || 0);
    const currentStock = job.current_stock
        ? `${job.current_stock.name || '未知'} (${job.current_stock.code || '--'})`
        : '等待第一批结果...';
    const logs = Array.isArray(job.logs) ? job.logs.slice().reverse() : [];
    const elapsed = Number(job.elapsed_seconds || 0);

    document.getElementById('selection-results-headline').textContent = '任务执行中';
    document.getElementById('selection-results-meta').textContent = `${job.backend || 'thread'} / ${percent}%`;

    document.getElementById('selection-results').innerHTML = `
        <div class="selection-progress">
            <div class="selection-progress-head">
                <div>
                    <div class="selection-progress-title">Selection In Progress</div>
                    <div class="selection-progress-sub">
                        已执行 ${formatElapsed(elapsed)}，当前处理到 ${escapeHtml(currentStock)}
                    </div>
                </div>
                <div class="selection-progress-time">${formatDateTime(new Date())}</div>
            </div>
            <div class="selection-progress-bar-wrap">
                <div class="selection-progress-bar">
                    <div class="selection-progress-bar-fill" style="width:${Math.min(100, Math.max(0, percent))}%"></div>
                </div>
                <div class="selection-progress-bar-text">${percent}%</div>
            </div>
            <div class="selection-progress-grid">
                <div class="selection-summary-card">
                    <span class="label">Elapsed</span>
                    <span class="value">${formatElapsed(elapsed)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Processed</span>
                    <span class="value">${formatNumber(completed)} / ${formatNumber(total)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Selected</span>
                    <span class="value">${formatNumber(selected)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Skipped</span>
                    <span class="value">${formatNumber(job.skipped_stock_count || 0)}</span>
                </div>
            </div>
            <div class="progress-log-panel">
                <div class="progress-log-header">
                    <span>Execution Feed</span>
                    <span>${escapeHtml(job.status || 'running').toUpperCase()}</span>
                </div>
                <div class="progress-log-list">
                    ${logs.map(item => `
                        <div class="progress-log-row">
                            <span class="progress-log-time">${escapeHtml(item.time)}</span>
                            <span class="progress-log-message">${escapeHtml(item.message)}</span>
                        </div>
                    `).join('') || '<div class="progress-log-row"><span class="progress-log-message">正在初始化任务...</span></div>'}
                </div>
            </div>
        </div>
    `;

    if (job.status === 'running' || job.status === 'queued') {
        startLocalProgressTimer(elapsed);
    }
}

function renderSelectionResults(results, time, meta = {}) {
    const strategies = meta.strategies || Object.keys(results);
    const selectedBoards = meta.boards || getSelectedBoards();
    const selectedStrategies = meta.strategies || getSelectedStrategies();
    const stockPoolSize = meta.stock_pool_size || 0;

    let totalCount = 0;
    strategies.forEach(name => {
        totalCount += (results[name] || []).length;
    });

    document.getElementById('selection-results-headline').textContent = totalCount
        ? `本次共命中 ${formatNumber(totalCount)} 条信号`
        : '本次执行未筛出符合条件的股票';
    document.getElementById('selection-results-meta').textContent = time || 'Run Complete';

    let html = `
        <div class="selection-summary">
            <div class="selection-summary-strip">
                <div>
                    <div class="selection-summary-title">Selection Completed</div>
                    <div class="selection-summary-sub">
                        板块 ${escapeHtml(selectedBoards.map(key => BOARD_LABELS[key] || key).join(' / '))}
                        · 策略 ${formatNumber(selectedStrategies.length)} 个
                    </div>
                </div>
                <div class="selection-summary-time">${escapeHtml(time || '--')}</div>
            </div>
            <div class="selection-summary-grid">
                <div class="selection-summary-card">
                    <span class="label">Signals</span>
                    <span class="value">${formatNumber(totalCount)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Strategies</span>
                    <span class="value">${formatNumber(selectedStrategies.length)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Stock Pool</span>
                    <span class="value">${formatNumber(stockPoolSize)}</span>
                </div>
                <div class="selection-summary-card">
                    <span class="label">Boards</span>
                    <span class="value">${formatNumber(selectedBoards.length)}</span>
                </div>
            </div>
        </div>
    `;

    strategies.forEach(strategyName => {
        const signals = results[strategyName] || [];

        html += `
            <section class="strategy-result-card">
                <div class="strategy-result-header">
                    <span class="strategy-result-name">${escapeHtml(strategyName)}</span>
                    <span class="strategy-result-count">${formatNumber(signals.length)} 只</span>
                </div>
        `;

        if (!signals.length) {
            html += '<div class="signal-empty">当前策略在本次筛选条件下没有命中信号。</div>';
        } else {
            html += `
                <div class="results-table-wrap">
                    <table class="data-table results-table">
                        <thead>
                            <tr>
                                <th>代码</th>
                                <th>名称</th>
                                <th>板块</th>
                                <th>现价</th>
                                <th>J值</th>
                                <th>市值(亿)</th>
                                <th>补充指标</th>
                                <th>触发条件</th>
                                <th>操作</th>
                            </tr>
                        </thead>
                        <tbody>
            `;
            html += signals.map(item => {
                const signal = Array.isArray(item.signals) ? (item.signals[0] || {}) : {};
                const reasonTags = (signal.reasons || []).map(reason => `<span class="tag">${escapeHtml(reason)}</span>`).join('');
                const jValue = Number(signal.J);
                const jClass = Number.isFinite(jValue) ? (jValue > 80 ? 'down' : (jValue < 20 ? 'up' : '')) : '';
                const extraMetric = signal.volume_ratio !== undefined
                    ? `量比 ${signal.volume_ratio}x`
                    : (signal.yangyin_ratio_57 !== undefined
                        ? `57阳阴比 ${signal.yangyin_ratio_57}`
                        : (signal.yangyin_ratio_14 !== undefined
                            ? `14阳阴比 ${signal.yangyin_ratio_14}`
                            : (signal.hm_short !== undefined && signal.hm_long !== undefined
                                ? `短/长线 ${signal.hm_short}/${signal.hm_long}`
                                : (signal.wl !== undefined && signal.yl !== undefined
                                    ? `WL/YL ${signal.wl}/${signal.yl}`
                                    : '--'))));

                return `
                    <tr>
                        <td class="code-cell">${escapeHtml(item.code)}</td>
                        <td>${escapeHtml(item.name || '未知')}</td>
                        <td>${boardBadge(item.code)}</td>
                        <td class="mono">${escapeHtml(signal.close ?? '--')}</td>
                        <td class="mono metric-value ${jClass}">${escapeHtml(signal.J ?? '--')}</td>
                        <td class="mono">${escapeHtml(signal.market_cap ?? '--')}</td>
                        <td class="mono">${escapeHtml(extraMetric)}</td>
                        <td><div class="result-tag-wrap">${reasonTags || '<span class="tag">MATCH</span>'}</div></td>
                        <td>
                            <button class="btn btn-ghost view-detail-btn" type="button"
                                data-code="${escapeHtml(item.code)}"
                                data-name="${escapeHtml(item.name || '')}">
                                K线
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
            html += `
                        </tbody>
                    </table>
                </div>
            `;
        }

        html += '</section>';
    });

    document.getElementById('selection-results').innerHTML = html;
    scrollResultsToTop();
}

async function runSelection() {
    if (state.systemHalted) {
        toast('系统已急停，无法继续执行', 'error');
        return;
    }

    // 先中止仍在后台执行的股票列表/概览请求，避免它们继续占用连接和资源。
    abortActiveRequests();

    if (state.currentPage !== 'selection') {
        switchPage('selection');
    }

    await loadSelectionOptions();

    const boards = getSelectedBoards();
    const strategies = getSelectedStrategies();

    if (!boards.length) {
        toast('请至少选择一个股票类型', 'error');
        return;
    }
    if (!strategies.length) {
        toast('请至少选择一个策略', 'error');
        return;
    }

    setRunButtonsLoading(true);
    setStatus('running');
    scrollResultsToTop();
    document.getElementById('selection-results-headline').textContent = '正在启动任务';
    document.getElementById('selection-results-meta').textContent = 'Initializing';
    document.getElementById('selection-results').innerHTML = '<div class="state-loading">正在创建选股任务...</div>';

    try {
        stopSelectionPolling();
        state.currentSelectionJobId = null;

        const result = await apiFetch('/api/select/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                boards: boards.join(','),
                strategies: strategies.join(','),
            }),
        });
        if (!result.success) {
            throw new Error(result.error || '执行失败');
        }

        state.currentSelectionJobId = result.job_id;
        renderSelectionProgress(result.data || {});
        state.selectionPollTimer = window.setInterval(pollSelectionJobStatus, 1000);
        await pollSelectionJobStatus();
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }

        document.getElementById('selection-results-headline').textContent = '执行失败';
        document.getElementById('selection-results-meta').textContent = 'Error';
        document.getElementById('selection-results').innerHTML = `<div class="state-empty">执行失败: ${escapeHtml(error.message)}</div>`;
        toast(`执行失败: ${error.message}`, 'error');
        setStatus('error');
    } finally {
        // 运行态由轮询结束时负责回收。
    }
}

async function pollSelectionJobStatus() {
    if (!state.currentSelectionJobId) {
        return;
    }

    try {
        const result = await apiFetch(`/api/select/status/${state.currentSelectionJobId}`);
        if (!result.success) {
            throw new Error(result.error || '状态同步失败');
        }

        const job = result.data || {};

        if (job.status === 'running' || job.status === 'queued') {
            state.serverElapsedBase = Number(job.elapsed_seconds) || 0;
            state.jobStartTime = Date.now();
        }

        renderSelectionProgress(job);

        if (job.status === 'completed') {
            stopSelectionPolling();
            clearSavedSelectionState();
            setRunButtonsLoading(false);
            setStatus('ready');
            renderSelectionResults(job.results || {}, job.result_time, {
                boards: job.boards || getSelectedBoards(),
                strategies: job.strategies || getSelectedStrategies(),
                stock_pool_size: job.total_candidates || 0,
            });
            toast('选股执行完成', 'success');
            return;
        }

        if (job.status === 'error') {
            stopSelectionPolling();
            clearSavedSelectionState();
            setRunButtonsLoading(false);
            setStatus('error');
            document.getElementById('selection-results-headline').textContent = '执行失败';
            document.getElementById('selection-results-meta').textContent = 'Error';
            document.getElementById('selection-results').innerHTML = `<div class="state-empty">执行失败: ${escapeHtml(job.error || '未知错误')}</div>`;
            toast(`执行失败: ${job.error || '未知错误'}`, 'error');
            return;
        }

        if (job.status === 'halted') {
            stopSelectionPolling();
            clearSavedSelectionState();
            applyHaltState(job.error || '系统已急停，当前任务已终止。');
        }
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        stopSelectionPolling();
        clearSavedSelectionState();
        setRunButtonsLoading(false);
        setStatus('error');
        document.getElementById('selection-results-headline').textContent = '状态同步失败';
        document.getElementById('selection-results-meta').textContent = 'Error';
        document.getElementById('selection-results').innerHTML = `<div class="state-empty">状态同步失败: ${escapeHtml(error.message)}</div>`;
        toast(`状态同步失败: ${error.message}`, 'error');
    }
}

function flattenConfigEntries(obj, path = []) {
    const entries = [];

    Object.entries(obj || {}).forEach(([key, value]) => {
        const nextPath = [...path, key];
        const isNested = value && typeof value === 'object' && !Array.isArray(value);

        if (isNested) {
            entries.push(...flattenConfigEntries(value, nextPath));
            return;
        }

        entries.push({
            path: nextPath.join('.'),
            label: key,
            value,
        });
    });

    return entries;
}

function renderStrategiesConfig(config) {
    const container = document.getElementById('strategies-config');
    const blocks = Object.entries(config || {}).map(([strategyName, params]) => {
        const entries = flattenConfigEntries(params);
        const rows = entries.map(entry => {
            const inputType = typeof entry.value === 'number' ? 'number' : 'text';
            const step = typeof entry.value === 'number' && !Number.isInteger(entry.value) ? 'any' : '1';
            return `
                <div class="param-row">
                    <span class="param-label">${escapeHtml(entry.label)}</span>
                    <span class="param-path">${escapeHtml(entry.path)}</span>
                    <input
                        class="param-input"
                        type="${inputType}"
                        step="${step}"
                        value="${escapeHtml(entry.value)}"
                        data-strategy="${escapeHtml(strategyName)}"
                        data-path="${escapeHtml(entry.path)}">
                </div>
            `;
        }).join('');

        return `
            <section class="strategy-config-block">
                <div class="strategy-config-header">
                    <div class="strategy-config-name">${escapeHtml(strategyName)}</div>
                </div>
                <div class="config-params-grid">${rows || '<div class="state-empty">无可编辑参数</div>'}</div>
            </section>
        `;
    }).join('');

    container.innerHTML = blocks || '<div class="state-empty">未读取到策略配置</div>';
}

async function loadStrategies() {
    if (state.systemHalted) {
        return;
    }

    const container = document.getElementById('strategies-config');
    container.innerHTML = '<div class="state-loading">正在加载策略配置...</div>';

    try {
        const result = await apiFetch('/api/config');
        if (!result.success) {
            throw new Error(result.error || '配置加载失败');
        }
        renderStrategiesConfig(result.data || {});
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        container.innerHTML = `<div class="state-empty">加载失败: ${escapeHtml(error.message)}</div>`;
    }
}

function parseInputValue(rawValue) {
    const value = String(rawValue ?? '').trim();
    if (value === 'true') {
        return true;
    }
    if (value === 'false') {
        return false;
    }
    if (/^-?\d+(\.\d+)?$/.test(value)) {
        return Number(value);
    }
    return value;
}

function assignNested(target, path, value) {
    const keys = path.split('.');
    let current = target;

    keys.forEach((key, index) => {
        if (index === keys.length - 1) {
            current[key] = value;
            return;
        }
        if (!current[key] || typeof current[key] !== 'object') {
            current[key] = {};
        }
        current = current[key];
    });
}

async function saveConfig() {
    if (state.systemHalted) {
        toast('系统已急停，无法保存配置', 'error');
        return;
    }

    const config = {};
    const inputs = document.querySelectorAll('#strategies-config .param-input');

    inputs.forEach(input => {
        const strategy = input.dataset.strategy;
        const path = input.dataset.path;
        if (!config[strategy]) {
            config[strategy] = {};
        }
        assignNested(config[strategy], path, parseInputValue(input.value));
    });

    try {
        const result = await apiFetch('/api/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(config),
        });

        if (!result.success) {
            throw new Error(result.error || '保存失败');
        }

        state.selectionOptionsLoaded = false;
        toast('配置已保存并重新加载策略', 'success');
        await loadSelectionOptions(true);
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        toast(`保存失败: ${error.message}`, 'error');
    }
}

function applyHaltState(message = '全部 Web 功能已停止。重启服务器后方可恢复。') {
    if (state.systemHalted) {
        const sub = document.getElementById('halt-overlay-sub');
        if (sub) {
            sub.textContent = message;
        }
        return;
    }

    state.systemHalted = true;
    document.body.classList.add('is-halted');
    stopSelectionPolling();
    abortActiveRequests();
    closeModal();
    closeIndustryModal();
    stopUpdatePolling();
    exitHeatmapFullscreenIfNeeded().catch(() => {});
    setStatus('halted');

    const sub = document.getElementById('halt-overlay-sub');
    if (sub) {
        sub.textContent = message;
    }

    document.querySelectorAll('button, input, select, textarea').forEach(element => {
        element.disabled = true;
    });

    const haltBtn = document.getElementById('halt-btn');
    const updateBtn = document.getElementById('update-data-btn');
    if (haltBtn) {
        haltBtn.disabled = true;
        haltBtn.innerHTML = '<span class="halt-btn-dot"></span><span>HALTED</span>';
    }
    if (updateBtn) {
        updateBtn.disabled = true;
    }

    document.getElementById('halt-confirm').classList.remove('active');
    document.getElementById('update-modal').classList.remove('active');
    document.getElementById('halt-overlay').classList.add('active');
    document.getElementById('selection-results-headline').textContent = '系统已急停';
    document.getElementById('selection-results-meta').textContent = 'HALTED';
}

function openHaltConfirm() {
    if (state.systemHalted) {
        return;
    }
    document.getElementById('halt-confirm').classList.add('active');
}

function closeHaltConfirm() {
    document.getElementById('halt-confirm').classList.remove('active');
}

async function confirmHalt() {
    if (state.systemHalted) {
        return;
    }

    try {
        await apiFetch('/api/emergency_stop', { method: 'POST' }, {
            allowWhenHalted: true,
            interpretHalt: false,
        });
    } catch (error) {
        console.warn('emergency stop request failed:', error);
    } finally {
        applyHaltState('全部 Web 功能已停止。当前运行中的请求已被中断，需重启服务器恢复。');
    }
}

function updateClock() {
    const el = document.getElementById('topbar-clock');
    if (!el) {
        return;
    }

    el.textContent = new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    }).format(new Date());
}

function bindEvents() {
    document.getElementById('sidebar-nav').addEventListener('click', event => {
        const item = event.target.closest('.nav-item');
        if (!item) {
            return;
        }
        switchPage(item.dataset.page);
    });

    document.getElementById('stock-search').addEventListener('input', event => {
        const keyword = event.target.value.trim().toLowerCase();
        if (!keyword) {
            renderStocks(state.allStocksCache);
            return;
        }

        const filtered = state.allStocksCache.filter(stock =>
            String(stock.code).toLowerCase().includes(keyword) ||
            String(stock.name || '').toLowerCase().includes(keyword)
        );
        renderStocks(filtered);
    });

    document.getElementById('stocks-tbody').addEventListener('click', event => {
        const button = event.target.closest('.view-detail-btn');
        if (!button) {
            return;
        }
        viewStockDetail(button.dataset.code, button.dataset.name);
    });

    document.getElementById('selection-results').addEventListener('click', event => {
        const button = event.target.closest('.view-detail-btn');
        if (!button) {
            return;
        }
        viewStockDetail(button.dataset.code, button.dataset.name);
    });

    document.getElementById('board-filter').addEventListener('change', updateSelectionSnapshot);
    document.getElementById('strategy-filter').addEventListener('change', updateSelectionSnapshot);

    document.getElementById('strategy-select-all-btn').addEventListener('click', () => {
        document.querySelectorAll('#strategy-filter input[name="strategy"]').forEach(input => {
            input.checked = true;
        });
        updateSelectionSnapshot();
    });

    document.getElementById('strategy-clear-all-btn').addEventListener('click', () => {
        document.querySelectorAll('#strategy-filter input[name="strategy"]').forEach(input => {
            input.checked = false;
        });
        updateSelectionSnapshot();
    });

    document.getElementById('run-selection-btn').addEventListener('click', runSelection);
    document.getElementById('execute-selection-btn').addEventListener('click', runSelection);
    document.getElementById('hero-run-btn').addEventListener('click', () => switchPage('selection'));
    document.getElementById('hero-stocks-btn').addEventListener('click', () => switchPage('stocks'));
    document.getElementById('refresh-dashboard-btn').addEventListener('click', loadStats);
    document.getElementById('refresh-selection-options-btn').addEventListener('click', () => loadSelectionOptions(true));
    document.getElementById('save-config-btn').addEventListener('click', saveConfig);
    document.getElementById('refresh-heatmap-btn').addEventListener('click', () => loadHeatmap(true));
    document.getElementById('heatmap-fullscreen-btn').addEventListener('click', toggleHeatmapFullscreen);
    document.getElementById('update-data-btn').addEventListener('click', openUpdateModal);

    document.getElementById('halt-btn').addEventListener('click', openHaltConfirm);
    document.getElementById('cancel-halt-btn').addEventListener('click', closeHaltConfirm);
    document.getElementById('confirm-halt-btn').addEventListener('click', confirmHalt);

    document.getElementById('heatmap-market-filter').addEventListener('click', event => {
        const button = event.target.closest('[data-scope]');
        if (!button || button.disabled) {
            return;
        }
        state.heatmapScope = button.dataset.scope;
        renderHeatmapFilters();
        loadHeatmap();
    });

    document.getElementById('heatmap-metric-filter').addEventListener('click', event => {
        const button = event.target.closest('[data-metric]');
        if (!button) {
            return;
        }
        state.heatmapMetric = button.dataset.metric;
        renderHeatmapFilters();
        loadHeatmap();
    });

    document.querySelectorAll('.update-provider-btn').forEach(button => {
        button.addEventListener('click', async () => {
            state.updateProvider = button.dataset.provider;
            if (state.updateProvider === 'tushare') {
                setUpdateModalStep('token');
                return;
            }
            await startUpdateJob(state.updateProvider);
        });
    });
    document.getElementById('update-token-back-btn').addEventListener('click', () => setUpdateModalStep('provider'));
    document.getElementById('update-token-confirm-btn').addEventListener('click', async () => {
        await startUpdateJob('tushare', document.getElementById('update-tushare-token').value.trim());
    });
    document.getElementById('update-modal-close-btn').addEventListener('click', closeUpdateModal);
    document.getElementById('update-modal').addEventListener('click', event => {
        if (event.target.id === 'update-modal') {
            closeUpdateModal();
        }
    });

    document.getElementById('stock-modal').addEventListener('click', event => {
        if (event.target.id === 'stock-modal') {
            closeModal();
        }
    });
    document.getElementById('modal-close-btn').addEventListener('click', closeModal);

    document.getElementById('industry-modal').addEventListener('click', event => {
        if (event.target.id === 'industry-modal') {
            closeIndustryModal();
        }
    });
    document.getElementById('industry-modal-close-btn').addEventListener('click', closeIndustryModal);
    document.addEventListener('fullscreenchange', syncHeatmapFullscreenState);
    document.addEventListener('webkitfullscreenchange', syncHeatmapFullscreenState);
}

const SELECTION_STATE_KEY = 'quant_selection_state';

function saveSelectionState() {
    if (!state.currentSelectionJobId) {
        sessionStorage.removeItem(SELECTION_STATE_KEY);
        return;
    }

    try {
        sessionStorage.setItem(SELECTION_STATE_KEY, JSON.stringify({
            jobId: state.currentSelectionJobId,
            savedAt: Date.now(),
            currentPage: state.currentPage,
        }));
    } catch (e) {
        console.warn('Failed to save selection state:', e);
    }
}

function loadSavedSelectionState() {
    try {
        const raw = sessionStorage.getItem(SELECTION_STATE_KEY);
        if (!raw) return null;

        const data = JSON.parse(raw);
        if (!data.jobId || !data.savedAt) return null;

        const ageMs = Date.now() - data.savedAt;
        if (ageMs > 30 * 60 * 1000) {
            sessionStorage.removeItem(SELECTION_STATE_KEY);
            return null;
        }

        return data;
    } catch (e) {
        console.warn('Failed to load selection state:', e);
        sessionStorage.removeItem(SELECTION_STATE_KEY);
        return null;
    }
}

function clearSavedSelectionState() {
    sessionStorage.removeItem(SELECTION_STATE_KEY);
}

async function init() {
    bindEvents();
    syncHeatmapFullscreenState();
    updateClock();
    window.setInterval(updateClock, 1000);

    window.addEventListener('beforeunload', handleBeforeUnload);

    try {
        const result = await apiFetch('/api/system_status', {}, {
            allowWhenHalted: true,
            interpretHalt: false,
        });
        if (result.halted) {
            applyHaltState('系统已经处于急停状态。请重启服务器后再恢复使用。');
            return;
        }
    } catch (error) {
        console.warn('system status check failed:', error);
    }

    await restoreSelectionStateIfNeeded();
    await Promise.all([loadStats(), loadSelectionOptions()]);
}

function handleBeforeUnload() {
    abortActiveRequests();
    stopSelectionPolling();
    stopUpdatePolling();
    stopLocalProgressTimer();

    if (state.currentSelectionJobId && (state.status === 'running')) {
        saveSelectionState();
    } else {
        clearSavedSelectionState();
    }

    if (state.chartInstance) {
        try {
            state.chartInstance.destroy();
        } catch (e) {}
        state.chartInstance = null;
    }
    if (state.heatmapChart) {
        try {
            state.heatmapChart.dispose();
        } catch (e) {}
        state.heatmapChart = null;
    }
}

async function restoreSelectionStateIfNeeded() {
    const saved = loadSavedSelectionState();
    if (!saved) return;

    try {
        const result = await apiFetch(`/api/select/status/${saved.jobId}`, {}, { allowWhenHalted: true });
        if (!result.success || !result.data) {
            clearSavedSelectionState();
            return;
        }

        const job = result.data;
        if (job.status === 'completed' || job.status === 'error' || job.status === 'halted') {
            clearSavedSelectionState();
            if (saved.currentPage === 'selection') {
                switchPage('selection');
                if (job.status === 'completed' && job.results) {
                    renderSelectionResults(job.results || {}, job.result_time, {
                        boards: job.boards || getSelectedBoards(),
                        strategies: job.strategies || getSelectedStrategies(),
                        stock_pool_size: job.total_candidates || 0,
                    });
                }
            }
            return;
        }

        state.currentSelectionJobId = saved.jobId;
        setRunButtonsLoading(true);
        setStatus('running');

        if (state.currentPage !== saved.currentPage) {
            switchPage(saved.currentPage);
        }

        document.getElementById('selection-results-headline').textContent = '检测到未完成任务，正在恢复...';
        document.getElementById('selection-results-meta').textContent = 'Restoring';
        document.getElementById('selection-results').innerHTML = '<div class="state-loading">正在恢复任务状态...</div>';

        renderSelectionProgress(job);
        state.selectionPollTimer = window.setInterval(pollSelectionJobStatus, 1000);

        toast('已恢复未完成的选股任务', 'info');
    } catch (error) {
        console.warn('Failed to restore selection state:', error);
        clearSavedSelectionState();
    }
}

document.addEventListener('DOMContentLoaded', init);
