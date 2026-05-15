'use strict';

const PAGE_TITLES = {
    dashboard: '系统概览',
    heatmap: '市场云图',
    stocks: '股票列表',
    selection: '执行选股',
    strategies: '策略配置',
    watchlist: '自选股票',
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
    stockSearchTimer: null,
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
    indexKlineChart: null,
    currentIndexSymbol: 'sh000001',
    localProgressTimer: null,
    jobStartTime: null,
    serverElapsedBase: 0,
    heatmapMetaLoaded: false,
    heatmapMarkets: [],
    heatmapScope: 'all',
    heatmapMetric: 'daily',
    heatmapGroups: [],
    heatmapLoading: false,
    heatmapPayloadCache: new Map(),
    heatmapHealth: null,
    heatmapAppFullscreenActive: false,
    updateModalStep: 'provider',
    updateProvider: null,
    updateHasTushareToken: false,
    updateDefaultProvider: 'akshare',
    globalTickerText: '',
    currentStockDetail: null,
    pendingExportStock: null,
    watchlistLoaded: false,
    watchlistCache: [],
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function flashElement(element) {
    if (!element) {
        return;
    }
    element.classList.remove('tick-flash');
    void element.offsetWidth;
    element.classList.add('tick-flash');
    window.setTimeout(() => {
        element.classList.remove('tick-flash');
    }, 120);
}

function updateTextWithFlash(target, nextValue) {
    const element = typeof target === 'string' ? document.getElementById(target) : target;
    if (!element) {
        return;
    }

    const nextText = String(nextValue ?? '--');
    if (element.textContent === nextText) {
        return;
    }

    element.textContent = nextText;
    flashElement(element);
}

function setCommandOutput(message, tone = '') {
    const output = document.getElementById('terminal-command-output');
    if (!output) {
        return;
    }

    output.textContent = message;
    output.className = `command-output${tone ? ` ${tone}` : ''}`;
    flashElement(output);
}

function updateGlobalTicker(text) {
    const track = document.getElementById('global-ticker-track');
    if (!track) {
        return;
    }

    const normalized = String(text || '').trim();
    if (!normalized) {
        return;
    }

    const repeated = `${normalized}   •   ${normalized}`;
    if (state.globalTickerText === repeated) {
        return;
    }

    state.globalTickerText = repeated;
    track.textContent = repeated;
    flashElement(track);
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

function buildDefaultTickerText(payload = {}) {
    const boardCounts = payload.board_counts || state.boardCounts || {};
    const statusLabel = {
        ready: 'READY',
        running: 'RUNNING',
        halted: 'HALTED',
        error: 'ERROR',
    }[state.status] || 'READY';

    return [
        `A-SHARE ${formatNumber(payload.total_stocks || 0)}`,
        `MAIN ${formatNumber(boardCounts.main || 0)}`,
        `CHINEXT ${formatNumber(boardCounts.chinext || 0)}`,
        `STAR ${formatNumber(boardCounts.star || 0)}`,
        `STRATS ${formatNumber(payload.strategies || state.strategies.length || 0)}`,
        `DATE ${payload.latest_date || '--'}`,
        `SESSION ${statusLabel}`,
        'SYS CONNECTED',
    ].join('   ');
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
        return '#1a1a1a';
    }
    const clamped = Math.max(-4, Math.min(4, value));
    if (clamped === 0) {
        return '#1a1a1a';
    }
    if (clamped > 0) {
        const ratio = clamped / 4;
        const red = Math.round(13 + (0 - 13) * ratio);
        const green = Math.round(26 + (255 - 26) * ratio);
        const blue = Math.round(13 + (65 - 13) * ratio);
        return `rgb(${red}, ${green}, ${blue})`;
    }
    const ratio = Math.abs(clamped) / 4;
    const red = Math.round(26 + (255 - 26) * ratio);
    const green = Math.round(10 + (49 - 10) * ratio);
    const blue = Math.round(10 + (49 - 10) * ratio);
    return `rgb(${red}, ${green}, ${blue})`;
}

function heatmapGroupPalette(index) {
    const palette = [
        { base: '#111111', hover: '#1a1a1a' },
        { base: '#141414', hover: '#1d1d1d' },
        { base: '#171717', hover: '#202020' },
        { base: '#101010', hover: '#191919' },
        { base: '#121212', hover: '#1b1b1b' },
        { base: '#151515', hover: '#1e1e1e' },
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
        updateTextWithFlash(text, label);
    }
    if (sidebar) {
        updateTextWithFlash(sidebar, label);
    }
    if (hero) {
        updateTextWithFlash(hero, label);
    }
    if (stat) {
        updateTextWithFlash(stat, label);
    }
    setCommandOutput(`SYS ${label}`, mode === 'error' || mode === 'halted' ? 'error' : '');
    updateGlobalTicker(buildDefaultTickerText());
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

function findCachedStock(code) {
    return state.allStocksCache.find(stock => String(stock.code) === String(code));
}

function formatStockTitle(code, name) {
    return `${code || '--'} ${name || ''}`.trim();
}

function setStockExportStatus(message = '', tone = '') {
    const status = document.getElementById('stock-export-status');
    if (!status) {
        return;
    }
    status.textContent = message;
    status.className = `stock-export-status${message ? ' active' : ''}${tone ? ` ${tone}` : ''}`;
}

function openExportConfirm(payload) {
    state.pendingExportStock = payload || state.currentStockDetail;
    const message = document.getElementById('export-confirm-message');
    if (message) {
        message.textContent = payload?.message || '本地 CSV 不是最新，请选择导出方式。';
    }
    document.getElementById('export-confirm').classList.add('active');
}

function closeExportConfirm() {
    document.getElementById('export-confirm').classList.remove('active');
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
        const method = String(fetchOptions.method || 'GET').toUpperCase();
        const headers = {
            ...(fetchOptions.headers || {}),
        };
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            const token = document.querySelector('meta[name="quant-session-token"]')?.content || '';
            headers['X-Quant-Session'] = token;
        }

        const response = await fetch(url, {
            ...fetchOptions,
            headers,
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
    setCommandOutput(`FUNC ${String(page).toUpperCase()}<GO>`, 'info');
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
    if (page === 'watchlist') {
        loadWatchlist();
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

        updateTextWithFlash('stat-stocks', formatNumber(data.total_stocks));
        updateTextWithFlash('stat-date', data.latest_date || '--');
        updateTextWithFlash('stat-strategies', formatNumber(data.strategies));
        updateTextWithFlash('hero-strategy-count', formatNumber(data.strategies));
        updateTextWithFlash('hero-latest-date', data.latest_date || '--');
        updateTextWithFlash('stocks-total-label', `当前本地股票池 ${formatNumber(data.total_stocks)} 只`);

        updateTextWithFlash('board-count-main', formatNumber(boardCounts.main || 0));
        updateTextWithFlash('board-count-chinext', formatNumber(boardCounts.chinext || 0));
        updateTextWithFlash('board-count-star', formatNumber(boardCounts.star || 0));

        updateTextWithFlash('hero-universe', 'ALL BOARDS');
        updateTextWithFlash('sidebar-universe-text', 'ALL BOARDS');
        loadDashboardIndexKline(state.currentIndexSymbol);
        loadDashboardPulse();
        updateGlobalTicker(buildDefaultTickerText(data));
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
        const anomalyCount = Number(data.cache_status?.market_cap_anomaly_count || 0);
        document.getElementById('heatmap-latest-date').textContent = data.latest_date || '--';
        document.getElementById('heatmap-cache-note').textContent = Object.keys(cacheErrors).length
            ? `缓存已降级使用旧数据: ${Object.keys(cacheErrors).join(' / ')}`
            : (refreshPending
                ? '云图缓存需要刷新。点击“刷新云图”重建缓存，或先执行 UPDATE 更新数据'
                : (anomalyCount > 0
                ? `发现 ${formatNumber(anomalyCount)} 条异常市值，建议刷新云图缓存`
                : (unmappedCount > 0
                ? `行业缓存仍有 ${formatNumber(unmappedCount)} 只股票未归类，系统会继续尝试补全`
                : '行业分类缓存已完整匹配，板块可直接点开查看全部股票')));
        renderHeatmapFilters();
    } catch (error) {
        document.getElementById('heatmap-cache-note').textContent = `元数据加载失败: ${error.message}`;
    }
}

async function loadHeatmapHealth(forceReload = false) {
    if (state.heatmapHealth && !forceReload) {
        return state.heatmapHealth;
    }
    const result = await apiFetch('/api/heatmap/health');
    if (!result.success) {
        throw new Error(result.error || '市场云图状态检查失败');
    }
    state.heatmapHealth = result.data || {};
    return state.heatmapHealth;
}

function heatmapCacheKey(health) {
    return [
        state.heatmapScope,
        state.heatmapMetric,
        health?.snapshot_latest_date || '--',
        health?.snapshot_updated_at || '--',
        health?.local_latest_date || '--',
    ].join('|');
}

function clearHeatmapPayloadCache() {
    state.heatmapPayloadCache.clear();
    state.heatmapHealth = null;
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
    return [
        `上涨家数 ${formatNumber(stats?.up_count ?? 0)}`,
        `下跌家数 ${formatNumber(stats?.down_count ?? 0)}`,
        `平盘家数 ${formatNumber(stats?.flat_count ?? 0)}`,
        `中位涨幅 ${medianText}`,
        `最新交易日 ${latestDate || '--'}`,
    ];
}

function buildTickerLoopText(stats, latestDate) {
    const ticker = buildTickerText(stats, latestDate).join('   •   ');
    return `${ticker}   •   ${ticker}`;
}

function formatPulseGroupList(groups) {
    if (!Array.isArray(groups) || !groups.length) {
        return '--';
    }
    return groups.map(item => {
        const pct = Number.isFinite(Number(item.change_pct)) ? formatPercent(item.change_pct) : '--';
        return `${item.name || '--'} ${pct}`;
    }).join(' / ');
}

function renderDashboardPulse(payload) {
    const container = document.getElementById('dashboard-market-pulse');
    if (!container) {
        return;
    }

    const stats = payload?.ticker_stats || {};
    const median = stats.median_change_pct;
    container.innerHTML = `
        <div class="pulse-card">
            <div class="pulse-label">ADVANCERS</div>
            <div class="pulse-value up">${formatNumber(stats.up_count || 0)}</div>
            <div class="pulse-sub">上涨家数</div>
        </div>
        <div class="pulse-card">
            <div class="pulse-label">DECLINERS</div>
            <div class="pulse-value down">${formatNumber(stats.down_count || 0)}</div>
            <div class="pulse-sub">下跌家数</div>
        </div>
        <div class="pulse-card">
            <div class="pulse-label">UNCHANGED</div>
            <div class="pulse-value">${formatNumber(stats.flat_count || 0)}</div>
            <div class="pulse-sub">平盘家数</div>
        </div>
        <div class="pulse-card">
            <div class="pulse-label">MEDIAN MOVE</div>
            <div class="pulse-value ${signedClass(median)}">${formatPercent(median)}</div>
            <div class="pulse-sub">全市场中位涨幅</div>
        </div>
        <div class="pulse-card pulse-card-wide">
            <div class="pulse-label">STRONG GROUPS</div>
            <div class="pulse-value up">${escapeHtml(formatPulseGroupList(payload?.leaders))}</div>
            <div class="pulse-sub">行业涨幅前三</div>
        </div>
        <div class="pulse-card pulse-card-wide">
            <div class="pulse-label">WEAK GROUPS</div>
            <div class="pulse-value down">${escapeHtml(formatPulseGroupList(payload?.laggards))}</div>
            <div class="pulse-sub">行业跌幅前三</div>
        </div>
        <div class="pulse-card pulse-card-wide">
            <div class="pulse-label">INDEX WATCH</div>
            <div class="pulse-value">${escapeHtml(formatPulseGroupList(payload?.header_indices))}</div>
            <div class="pulse-sub">主要指数快照</div>
        </div>
        <div class="pulse-card pulse-card-wide">
            <div class="pulse-label">COVERAGE</div>
            <div class="pulse-value">${formatNumber(payload?.stock_count || 0)} / ${formatNumber(payload?.group_count || 0)}</div>
            <div class="pulse-sub">股票 / 行业分组 · ${escapeHtml(payload?.latest_date || '--')}</div>
        </div>
    `;
}

function renderDashboardHealth(payload) {
    const container = document.getElementById('dashboard-market-health');
    if (!container) {
        return;
    }

    const health = payload?.cache_health || {};
    const refreshPending = Boolean(health.refresh_pending);
    const anomalyCount = Number(health.market_cap_anomaly_count || 0);
    const cacheState = refreshPending ? 'REFRESH' : 'READY';
    const cacheClass = refreshPending ? 'down' : 'up';
    const capClass = anomalyCount > 0 ? 'down' : 'up';

    container.innerHTML = `
        <div class="health-card">
            <div class="pulse-label">LOCAL DATE</div>
            <div class="pulse-value">${escapeHtml(health.local_latest_date || '--')}</div>
            <div class="pulse-sub">${formatNumber(health.local_stock_count || 0)} 本地股票</div>
        </div>
        <div class="health-card">
            <div class="pulse-label">MAP CACHE</div>
            <div class="pulse-value ${cacheClass}">${cacheState}</div>
            <div class="pulse-sub">${escapeHtml(health.snapshot_updated_at || '--')}</div>
        </div>
        <div class="health-card">
            <div class="pulse-label">CAP CHECK</div>
            <div class="pulse-value ${capClass}">${formatNumber(anomalyCount)}</div>
            <div class="pulse-sub">异常市值记录</div>
        </div>
        <div class="health-card">
            <div class="pulse-label">INDUSTRY MAP</div>
            <div class="pulse-value">${formatNumber(health.industry_mapped_count || 0)}</div>
            <div class="pulse-sub">未归类 ${formatNumber(health.industry_unmapped_count || 0)}</div>
        </div>
        <div class="health-card">
            <div class="pulse-label">ACTION</div>
            <div class="pulse-value ${cacheClass}">${refreshPending ? 'UPDATE' : 'STAND BY'}</div>
            <div class="pulse-sub">${refreshPending ? 'F2 刷新云图或 UPDATE' : '可直接查看 F2 云图'}</div>
        </div>
    `;
}

async function loadDashboardPulse() {
    if (state.systemHalted) {
        return;
    }
    const container = document.getElementById('dashboard-market-pulse');
    try {
        const result = await apiFetch('/api/dashboard-pulse');
        if (!result.success) {
            throw new Error(result.error || '市场强弱数据加载失败');
        }
        renderDashboardPulse(result.data || {});
        renderDashboardHealth(result.data || {});
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        if (container) {
            container.innerHTML = `<div class="state-empty">MARKET PULSE LOAD FAILED: ${escapeHtml(error.message)}</div>`;
        }
        const healthContainer = document.getElementById('dashboard-market-health');
        if (healthContainer) {
            healthContainer.innerHTML = `<div class="state-empty">DATA WATCH LOAD FAILED: ${escapeHtml(error.message)}</div>`;
        }
    }
}

function formatCompactAmount(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return '--';
    }
    if (numeric >= 100000000) {
        return `${(numeric / 100000000).toFixed(1)}亿`;
    }
    if (numeric >= 10000) {
        return `${(numeric / 10000).toFixed(1)}万`;
    }
    return numeric.toFixed(0);
}

function setDashboardIndexButtons(symbol) {
    document.querySelectorAll('#dashboard-index-selector .index-switch-btn').forEach(button => {
        button.classList.toggle('active', button.dataset.symbol === symbol);
    });
}

function resizeDashboardIndexChart(delay = 0) {
    window.setTimeout(() => {
        if (state.indexKlineChart) {
            state.indexKlineChart.resize();
        }
    }, delay);
}

function renderDashboardIndexKline(payload) {
    const container = document.getElementById('dashboard-sparkline');
    if (!container) {
        return;
    }

    const candles = Array.isArray(payload?.candles)
        ? payload.candles.filter(item =>
            Number.isFinite(Number(item.open)) &&
            Number.isFinite(Number(item.close)) &&
            Number.isFinite(Number(item.low)) &&
            Number.isFinite(Number(item.high))
        )
        : [];

    if (!candles.length) {
        if (state.indexKlineChart) {
            state.indexKlineChart.dispose();
            state.indexKlineChart = null;
        }
        container.innerHTML = '<div class="state-empty">NO KLINE DATA</div>';
        return;
    }

    if (!window.echarts) {
        container.innerHTML = '<div class="state-empty">CHART ENGINE MISSING</div>';
        return;
    }

    if (!state.indexKlineChart) {
        state.indexKlineChart = window.echarts.init(container);
    }

    const dates = candles.map(item => item.date.slice(5));
    const values = candles.map(item => [
        Number(item.open),
        Number(item.close),
        Number(item.low),
        Number(item.high),
    ]);
    const latest = candles[candles.length - 1];
    const previous = candles[candles.length - 2];
    const changePct = previous && Number(previous.close)
        ? (((Number(latest.close) / Number(previous.close)) - 1) * 100)
        : null;
    const meta = document.getElementById('dashboard-index-meta');
    if (meta) {
        const changeText = Number.isFinite(changePct) ? `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%` : '--';
        meta.textContent = `${payload.name || '--'} ${latest.date} ${changeText}`;
        meta.className = `quote-chart-meta ${Number(changePct) >= 0 ? 'positive' : 'negative'}`;
    }

    state.indexKlineChart.setOption({
        animation: false,
        backgroundColor: '#000000',
        grid: {
            left: 44,
            right: 12,
            top: 14,
            bottom: 26,
        },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            backgroundColor: '#050505',
            borderColor: '#333333',
            borderWidth: 1,
            textStyle: {
                color: '#f0f0f0',
                fontFamily: 'IBM Plex Mono, Menlo, monospace',
                fontSize: 10,
            },
            formatter(params) {
                const point = params && params[0];
                if (!point) {
                    return '';
                }
                const item = candles[point.dataIndex];
                return [
                    `${payload.name || ''} ${item.date}`,
                    `O ${item.open}  H ${item.high}`,
                    `L ${item.low}  C ${item.close}`,
                    `VOL ${formatCompactAmount(item.volume)}`,
                ].join('<br>');
            },
        },
        xAxis: {
            type: 'category',
            data: dates,
            boundaryGap: true,
            axisLine: { lineStyle: { color: '#333333' } },
            axisTick: { show: false },
            axisLabel: {
                color: '#777777',
                fontSize: 9,
                interval: 4,
            },
            splitLine: { show: false },
        },
        yAxis: {
            scale: true,
            position: 'right',
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: {
                color: '#777777',
                fontSize: 9,
            },
            splitLine: {
                lineStyle: {
                    color: '#1f1f1f',
                    type: 'dashed',
                },
            },
        },
        series: [{
            name: payload.name || 'INDEX',
            type: 'candlestick',
            data: values,
            barWidth: '56%',
            itemStyle: {
                color: '#ff3131',
                color0: '#00c853',
                borderColor: '#ff3131',
                borderColor0: '#00c853',
            },
        }],
    }, true);
    resizeDashboardIndexChart(30);
}

async function loadDashboardIndexKline(symbol = 'sh000001') {
    if (state.systemHalted) {
        return;
    }
    state.currentIndexSymbol = symbol;
    setDashboardIndexButtons(symbol);

    const container = document.getElementById('dashboard-sparkline');
    if (container && !state.indexKlineChart) {
        container.innerHTML = '<div class="state-loading">LOADING INDEX KLINE...</div>';
    }

    try {
        const result = await apiFetch(`/api/index-kline?symbol=${encodeURIComponent(symbol)}&limit=30`);
        if (!result.success) {
            throw new Error(result.error || '指数K线加载失败');
        }
        renderDashboardIndexKline(result.data || {});
    } catch (error) {
        if (error.name === 'AbortError' && state.systemHalted) {
            return;
        }
        if (state.indexKlineChart) {
            state.indexKlineChart.dispose();
            state.indexKlineChart = null;
        }
        if (container) {
            container.innerHTML = `<div class="state-empty">KLINE LOAD FAILED: ${escapeHtml(error.message)}</div>`;
        }
    }
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

function hasPywebviewFullscreenBridge() {
    return Boolean(window.pywebview?.api?.toggle_heatmap_fullscreen);
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
    const isFullscreen = Boolean(shell && getFullscreenElement() === shell) || state.heatmapAppFullscreenActive;

    document.body.classList.toggle('heatmap-fullscreen-active', isFullscreen);
    document.body.classList.toggle('heatmap-app-fullscreen-active', state.heatmapAppFullscreenActive);
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
    if (state.heatmapAppFullscreenActive && hasPywebviewFullscreenBridge()) {
        await window.pywebview.api.toggle_heatmap_fullscreen();
        state.heatmapAppFullscreenActive = false;
        syncHeatmapFullscreenState();
    }
}

async function toggleHeatmapFullscreen() {
    if (state.systemHalted) {
        return;
    }

    const shell = getHeatmapFullscreenShell();
    if (!shell) {
        toast('当前浏览器不支持全屏模式', 'error');
        return;
    }

    try {
        if (!fullscreenEnabled() && hasPywebviewFullscreenBridge()) {
            await window.pywebview.api.toggle_heatmap_fullscreen();
            state.heatmapAppFullscreenActive = !state.heatmapAppFullscreenActive;
            syncHeatmapFullscreenState();
            return;
        }
        if (!fullscreenEnabled()) {
            toast('当前浏览器不支持全屏模式', 'error');
            return;
        }
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
                    borderColor: '#ff6600',
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
        backgroundColor: '#000000',
        tooltip: {
            confine: true,
            backgroundColor: '#000000',
            borderColor: '#ff6600',
            borderWidth: 1,
            textStyle: {
                color: '#e0e0e0',
                fontFamily: 'IBM Plex Mono',
                fontSize: 10,
            },
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
                color: '#e0e0e0',
                fontSize: 10,
                fontFamily: 'IBM Plex Mono',
            },
            upperLabel: {
                show: true,
                color: '#ff6600',
                height: 20,
                fontSize: 10,
                fontFamily: 'IBM Plex Mono',
            },
            itemStyle: {
                borderColor: '#1a1a1a',
                borderWidth: 1,
                gapWidth: 1,
            },
            emphasis: {
                upperLabel: {
                    color: '#ff6600',
                },
            },
            levels: [
                {
                    itemStyle: {
                        borderColor: '#ff6600',
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
                        borderColor: '#1a1a1a',
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

function renderHeatmapPayload(data) {
    document.getElementById('heatmap-latest-date').textContent = data.latest_date || '--';
    document.getElementById('heatmap-subtitle').textContent = `按行业聚合，面积映射总市值，颜色映射${heatmapMetricLabel(state.heatmapMetric)}涨跌幅`;
    document.getElementById('heatmap-scope-label').textContent = heatmapScopeLabel(state.heatmapScope);
    document.getElementById('heatmap-metric-label').textContent = heatmapMetricLabel(state.heatmapMetric);
    document.getElementById('heatmap-stock-count').textContent = `${formatNumber(data.stock_count || 0)} 只股票`;
    const tickerText = buildTickerText(data.ticker_stats, data.latest_date).join('   •   ');
    document.getElementById('heatmap-ticker-track').textContent = buildTickerLoopText(data.ticker_stats, data.latest_date);
    updateGlobalTicker(`${heatmapScopeLabel(state.heatmapScope)}   ${tickerText}`);
    renderHeatmapIndices(data.header_indices || []);
    renderHeatmapChart(data.groups || []);
}

async function loadHeatmap(forceReload = false) {
    if (state.systemHalted) {
        return;
    }
    setHeatmapLoading(true, forceReload ? '正在刷新市场云图...' : '正在生成市场云图...');

    try {
        const health = await loadHeatmapHealth(forceReload);
        const key = heatmapCacheKey(health);
        if (!forceReload && state.heatmapPayloadCache.has(key)) {
            renderHeatmapPayload(state.heatmapPayloadCache.get(key));
            resizeHeatmapChart(40);
            return;
        }

        if (!forceReload && health.refresh_pending) {
            throw new Error('市场云图缓存不是最新，请点击“刷新云图”重建缓存，或先执行 UPDATE 更新数据。');
        }

        const result = await apiFetch(`/api/heatmap?scope=${encodeURIComponent(state.heatmapScope)}&metric=${encodeURIComponent(state.heatmapMetric)}${forceReload ? '&refresh=1' : ''}`);
        if (!result.success) {
            throw new Error(result.error || '市场云图加载失败');
        }
        const data = result.data || {};
        if (forceReload) {
            state.heatmapHealth = null;
            state.heatmapPayloadCache.clear();
        }
        const cacheHealth = forceReload ? await loadHeatmapHealth(true) : health;
        state.heatmapPayloadCache.set(heatmapCacheKey(cacheHealth), data);
        renderHeatmapPayload(data);
    } catch (error) {
        const container = document.getElementById('heatmap-chart');
        if (container) {
            if (state.heatmapChart) {
                state.heatmapChart.dispose();
                state.heatmapChart = null;
            }
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

async function searchStocks(keyword, limit = 20) {
    const result = await apiFetch(`/api/stocks/search?q=${encodeURIComponent(keyword)}&limit=${limit}`);
    if (!result.success) {
        throw new Error(result.error || '股票搜索失败');
    }
    return result.data || [];
}

async function applyStockSearch(keyword) {
    const normalized = String(keyword || '').trim().toLowerCase();
    if (!normalized) {
        renderStocks(state.allStocksCache);
        return;
    }

    let filtered = state.allStocksCache.filter(stock =>
        String(stock.code).toLowerCase().includes(normalized) ||
        String(stock.name || '').toLowerCase().includes(normalized)
    );

    try {
        const searchResults = await searchStocks(normalized, 80);
        const orderedCodes = searchResults.map(item => String(item.code));
        const fromCache = orderedCodes
            .map(code => findCachedStock(code))
            .filter(Boolean);
        const cacheCodes = new Set(filtered.map(item => String(item.code)));
        fromCache.forEach(item => {
            if (!cacheCodes.has(String(item.code))) {
                filtered.push(item);
            }
        });
        filtered = filtered.sort((a, b) => {
            const aIndex = orderedCodes.indexOf(String(a.code));
            const bIndex = orderedCodes.indexOf(String(b.code));
            if (aIndex === -1 && bIndex === -1) {
                return String(a.code).localeCompare(String(b.code));
            }
            if (aIndex === -1) return 1;
            if (bIndex === -1) return -1;
            return aIndex - bIndex;
        });
    } catch (error) {
        console.warn('stock search failed:', error);
    }

    renderStocks(filtered);
}

async function openStockByQuery(query) {
    const keyword = String(query || '').trim();
    if (!keyword) {
        setCommandOutput('INPUT REQUIRED', 'error');
        return;
    }
    try {
        let match = null;
        if (/^\d{6}$/.test(keyword)) {
            match = findCachedStock(keyword) || { code: keyword, name: '' };
        } else {
            const results = await searchStocks(keyword, 1);
            match = results[0] || null;
        }
        if (!match) {
            throw new Error(`未找到匹配股票: ${keyword}`);
        }
        setCommandOutput(`LOADING ${match.code}`, 'info');
        viewStockDetail(match.code, match.name || '');
    } catch (error) {
        setCommandOutput(error.message, 'error');
        toast(error.message, 'error');
    }
}

async function viewStockDetail(code, name) {
    if (state.systemHalted) {
        return;
    }

    state.currentStockDetail = { code, name: name || '' };
    document.getElementById('modal-title').textContent = formatStockTitle(code, name);
    setStockExportStatus('');
    document.getElementById('stock-info').innerHTML = '<div class="state-loading">加载个股详情...</div>';
    document.getElementById('stock-modal').classList.add('active');

    try {
        const result = await apiFetch(`/api/stock/${code}`);
        if (!result.success) {
            throw new Error(result.error || '个股详情加载失败');
        }
        const resolvedName = result.name || name || findCachedStock(code)?.name || '';
        state.currentStockDetail = { code: result.code || code, name: resolvedName };
        document.getElementById('modal-title').textContent = formatStockTitle(result.code || code, resolvedName);
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
    const dates = reversed.map(item => item.date);
    const candleValues = reversed.map(item => [
        Number(item.open),
        Number(item.close),
        Number(item.low),
        Number(item.high),
    ]);
    const volumeValues = reversed.map((item, index) => ({
        value: Number(item.volume) || 0,
        itemStyle: {
            color: Number(item.close) >= Number(item.open) ? '#ff3131' : '#00c853',
        },
        rawIndex: index,
    }));
    const kValues = reversed.map(item => Number(item.K));
    const dValues = reversed.map(item => Number(item.D));
    const jValues = reversed.map(item => Number(item.J));
    const minJValues = reversed.map(item => Number(item.MIN_J));
    const zxShortValues = reversed.map(item => Number(item.ZX_SHORT));
    const zxLongValues = reversed.map(item => Number(item.ZX_LONG));
    const buildSequenceMarks = (field, yField, minValue, maxValue) => reversed
        .map((item, index) => {
            const value = Number(item[field]);
            return {
                value,
                data: [dates[index], Number(item[yField]), value],
            };
        })
        .filter(item =>
            Number.isFinite(item.value) &&
            item.value >= minValue &&
            item.value <= maxValue &&
            Number.isFinite(item.data[1])
        );
    const upSeqEarlyMarks = buildSequenceMarks('UP_SEQ', 'UP_SEQ_Y', 1, 8);
    const upSeqLateMarks = buildSequenceMarks('UP_SEQ', 'UP_SEQ_Y', 9, 13);
    const downSeqEarlyMarks = buildSequenceMarks('DOWN_SEQ', 'DOWN_SEQ_Y', 1, 8);
    const downSeqLateMarks = buildSequenceMarks('DOWN_SEQ', 'DOWN_SEQ_Y', 9, 13);
    const violentKMarks = reversed
        .map((item, index) => ({
            value: item.VIOLENT_K ? 1 : 0,
            data: [dates[index], Number(item.VIOLENT_K_Y), '★'],
        }))
        .filter(item => item.value && Number.isFinite(item.data[1]));

    const chartEl = document.getElementById('stock-chart');
    if (!chartEl) {
        return;
    }

    if (state.chartInstance) {
        if (typeof state.chartInstance.dispose === 'function') {
            state.chartInstance.dispose();
        } else if (typeof state.chartInstance.destroy === 'function') {
            state.chartInstance.destroy();
        }
        state.chartInstance = null;
    }

    if (!window.echarts) {
        document.getElementById('stock-info').innerHTML = '<div class="state-empty">ECharts 加载失败，无法渲染个股走势。</div>';
        return;
    }

    state.chartInstance = window.echarts.init(chartEl);
    state.chartInstance.setOption({
        animation: false,
        backgroundColor: '#0a0a0a',
        legend: {
            top: 6,
            right: 12,
            data: ['K线', '知行短期趋势线', '知行多空线', '大暴力K', '成交量', 'K', 'D', 'J', 'MIN_J'],
            itemWidth: 12,
            itemHeight: 8,
            textStyle: {
                color: '#e0e0e0',
                fontFamily: 'IBM Plex Mono, Menlo, monospace',
                fontSize: 10,
            },
        },
        axisPointer: {
            link: [{ xAxisIndex: 'all' }],
            label: {
                backgroundColor: '#222222',
            },
        },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            backgroundColor: '#050505',
            borderColor: '#333333',
            borderWidth: 1,
            textStyle: {
                color: '#f0f0f0',
                fontFamily: 'IBM Plex Mono, Menlo, monospace',
                fontSize: 10,
            },
            formatter(params) {
                const point = params && params[0];
                if (!point) {
                    return '';
                }
                const item = reversed[point.dataIndex];
                return [
                    `${item.date}`,
                    `O ${item.open}  H ${item.high}  L ${item.low}  C ${item.close}`,
                    `知行短期 ${item.ZX_SHORT ?? '--'}  多空 ${item.ZX_LONG ?? '--'}`,
                    `VOL ${formatCompactAmount(item.volume)}`,
                    `K ${item.K}  D ${item.D}  J ${item.J}`,
                    `MIN_J ${item.MIN_J}`,
                ].join('<br>');
            },
        },
        grid: [
            { left: 52, right: 48, top: 34, height: 250 },
            { left: 52, right: 48, top: 306, height: 62 },
            { left: 52, right: 48, top: 390, height: 92 },
        ],
        xAxis: [
            {
                type: 'category',
                data: dates,
                boundaryGap: true,
                axisLine: { lineStyle: { color: '#333333' } },
                axisTick: { show: false },
                axisLabel: { show: false },
                splitLine: { show: false },
            },
            {
                type: 'category',
                gridIndex: 1,
                data: dates,
                boundaryGap: true,
                axisLine: { lineStyle: { color: '#333333' } },
                axisTick: { show: false },
                axisLabel: { show: false },
                splitLine: { show: false },
            },
            {
                type: 'category',
                gridIndex: 2,
                data: dates,
                boundaryGap: true,
                axisLine: { lineStyle: { color: '#333333' } },
                axisTick: { show: false },
                axisLabel: {
                    color: '#888888',
                    fontSize: 10,
                    interval: 12,
                },
                splitLine: { show: false },
            },
        ],
        yAxis: [
            {
                scale: true,
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: '#e0e0e0', fontSize: 10 },
                splitLine: {
                    lineStyle: { color: '#1f1f1f' },
                },
            },
            {
                gridIndex: 1,
                scale: true,
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: {
                    color: '#777777',
                    fontSize: 9,
                    formatter: value => formatCompactAmount(value),
                },
                splitLine: {
                    lineStyle: { color: '#1f1f1f' },
                },
            },
            {
                gridIndex: 2,
                min: value => Math.min(0, Math.floor(value.min / 10) * 10),
                max: value => Math.max(100, Math.ceil(value.max / 10) * 10),
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: '#888888', fontSize: 9 },
                splitLine: {
                    lineStyle: { color: '#1f1f1f', type: 'dashed' },
                },
            },
        ],
        dataZoom: [
            { type: 'inside', xAxisIndex: [0, 1, 2], start: 0, end: 100 },
            {
                type: 'slider',
                xAxisIndex: [0, 1, 2],
                bottom: 4,
                height: 18,
                borderColor: '#333333',
                backgroundColor: '#0d0d0d',
                fillerColor: 'rgba(255, 102, 0, 0.12)',
                handleStyle: { color: '#ff6600' },
                textStyle: { color: '#777777', fontSize: 9 },
            },
        ],
        series: [
            {
                name: 'K线',
                type: 'candlestick',
                data: candleValues,
                barWidth: '56%',
                itemStyle: {
                    color: '#ff3131',
                    color0: '#00c853',
                    borderColor: '#ff3131',
                    borderColor0: '#00c853',
                },
            },
            {
                name: '知行短期趋势线',
                type: 'line',
                data: zxShortValues,
                showSymbol: false,
                smooth: true,
                lineStyle: { color: '#ffffff', width: 1.1 },
            },
            {
                name: '知行多空线',
                type: 'line',
                data: zxLongValues,
                showSymbol: false,
                smooth: true,
                lineStyle: { color: '#d7df3f', width: 1.1 },
            },
            {
                name: '上涨序列1-8',
                type: 'scatter',
                data: upSeqEarlyMarks.map(item => item.data),
                symbolSize: 0,
                tooltip: { show: false },
                label: {
                    show: true,
                    formatter: params => String(params.data[2]),
                    color: '#ff4dff',
                    fontSize: 13,
                    fontWeight: 700,
                    position: 'top',
                },
            },
            {
                name: '上涨序列9-13',
                type: 'scatter',
                data: upSeqLateMarks.map(item => item.data),
                symbolSize: 0,
                tooltip: { show: false },
                label: {
                    show: true,
                    formatter: params => String(params.data[2]),
                    color: '#00ff41',
                    fontSize: 13,
                    fontWeight: 700,
                    position: 'top',
                },
            },
            {
                name: '下跌序列1-8',
                type: 'scatter',
                data: downSeqEarlyMarks.map(item => item.data),
                symbolSize: 0,
                tooltip: { show: false },
                label: {
                    show: true,
                    formatter: params => String(params.data[2]),
                    color: '#00ff41',
                    fontSize: 13,
                    fontWeight: 700,
                    position: 'bottom',
                },
            },
            {
                name: '下跌序列9-13',
                type: 'scatter',
                data: downSeqLateMarks.map(item => item.data),
                symbolSize: 0,
                tooltip: { show: false },
                label: {
                    show: true,
                    formatter: params => String(params.data[2]),
                    color: '#ff4dff',
                    fontSize: 13,
                    fontWeight: 700,
                    position: 'bottom',
                },
            },
            {
                name: '大暴力K',
                type: 'scatter',
                data: violentKMarks.map(item => item.data),
                symbolSize: 0,
                tooltip: { show: false },
                label: {
                    show: true,
                    formatter: '★',
                    color: '#ffffff',
                    fontSize: 12,
                    fontWeight: 700,
                    textBorderColor: '#000000',
                    textBorderWidth: 2,
                    position: 'top',
                },
            },
            {
                name: '成交量',
                type: 'bar',
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: volumeValues,
                barWidth: '58%',
            },
            {
                name: 'K',
                type: 'line',
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: kValues,
                showSymbol: false,
                lineStyle: { color: '#ffd700', width: 1.1 },
            },
            {
                name: 'D',
                type: 'line',
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: dValues,
                showSymbol: false,
                lineStyle: { color: '#00ff41', width: 1.1 },
            },
            {
                name: 'J',
                type: 'line',
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: jValues,
                showSymbol: false,
                lineStyle: { color: '#ff4dff', width: 1.1 },
            },
            {
                name: 'MIN_J',
                type: 'line',
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: minJValues,
                showSymbol: false,
                lineStyle: { color: '#ff3131', width: 1.3 },
            },
        ],
    });
    window.setTimeout(() => {
        if (state.chartInstance && typeof state.chartInstance.resize === 'function') {
            state.chartInstance.resize();
        }
    }, 30);

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
            <div class="kv-item">
                <div class="kv-label">MIN_J</div>
                <div class="kv-value down">${escapeHtml(latest.MIN_J)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">知行短期</div>
                <div class="kv-value">${escapeHtml(latest.ZX_SHORT)}</div>
            </div>
            <div class="kv-item">
                <div class="kv-label">知行多空</div>
                <div class="kv-value">${escapeHtml(latest.ZX_LONG)}</div>
            </div>
        </div>
    `;
}

async function exportCurrentStock(mode = 'check') {
    if (state.systemHalted) {
        toast('系统已急停，无法导出 CSV', 'error');
        return;
    }
    const stock = state.currentStockDetail || state.pendingExportStock;
    if (!stock || !stock.code) {
        toast('当前没有打开的股票', 'error');
        return;
    }

    const exportButton = document.getElementById('stock-export-btn');
    const updateButton = document.getElementById('export-update-first-btn');
    const forceButton = document.getElementById('export-force-btn');
    [exportButton, updateButton, forceButton].forEach(button => {
        if (button) {
            button.disabled = true;
        }
    });
    setStockExportStatus(mode === 'update' ? '正在用 Tushare 更新并导出...' : '正在检查并导出 CSV...', 'info');

    try {
        const result = await apiFetch(`/api/stock/${encodeURIComponent(stock.code)}/export`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ mode }),
        });

        if (result.needs_update) {
            setStockExportStatus(result.message || '本地数据不是最新，请选择导出方式。', 'warning');
            openExportConfirm({
                ...stock,
                message: result.message,
                freshness: result.freshness,
            });
            return;
        }

        closeExportConfirm();
        setStockExportStatus(result.message || 'CSV 已导出', 'success');
        toast(result.message || 'CSV 已导出', 'success', 4200);
    } catch (error) {
        setStockExportStatus(`导出失败: ${error.message}`, 'error');
        toast(`导出失败: ${error.message}`, 'error');
    } finally {
        [exportButton, updateButton, forceButton].forEach(button => {
            if (button) {
                button.disabled = false;
            }
        });
    }
}

function closeModal() {
    document.getElementById('stock-modal').classList.remove('active');
    setStockExportStatus('');
    closeExportConfirm();
    if (state.chartInstance) {
        if (typeof state.chartInstance.dispose === 'function') {
            state.chartInstance.dispose();
        } else if (typeof state.chartInstance.destroy === 'function') {
            state.chartInstance.destroy();
        }
        state.chartInstance = null;
    }
}

function closeIndustryModal() {
    document.getElementById('industry-modal').classList.remove('active');
}

function formatWatchlistDate(value) {
    if (!value) {
        return '--';
    }
    return String(value).replace(' ', '<br>');
}

function renderWatchlist(items) {
    const tbody = document.getElementById('watchlist-tbody');
    const totalLabel = document.getElementById('watchlist-total-label');
    if (totalLabel) {
        totalLabel.textContent = `已添加 ${formatNumber(items.length)} 只自选股票`;
    }
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="state-empty">暂无自选股，可输入代码、名称或拼音首字母添加。</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(stock => `
        <tr>
            <td class="code-cell">${escapeHtml(stock.code)}</td>
            <td>${escapeHtml(stock.name || '未知')}</td>
            <td>${boardBadge(stock.board || stock.code)}</td>
            <td class="mono">${escapeHtml(stock.latest_price ?? '--')}</td>
            <td class="mono">${escapeHtml(stock.latest_date ?? '--')}</td>
            <td class="mono">${escapeHtml(stock.market_cap ?? '--')}</td>
            <td>${escapeHtml(stock.note || '--')}</td>
            <td class="mono">${formatWatchlistDate(escapeHtml(stock.created_at || '--'))}</td>
            <td>
                <div class="watchlist-row-actions">
                    <button class="btn btn-ghost view-detail-btn" type="button"
                        data-code="${escapeHtml(stock.code)}"
                        data-name="${escapeHtml(stock.name || '')}">
                        K线
                    </button>
                    <button class="btn btn-secondary export-watchlist-btn" type="button"
                        data-code="${escapeHtml(stock.code)}"
                        data-name="${escapeHtml(stock.name || '')}">
                        导出
                    </button>
                    <button class="btn btn-danger remove-watchlist-btn" type="button"
                        data-code="${escapeHtml(stock.code)}">
                        删除
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

function filterWatchlist(keyword) {
    const normalized = String(keyword || '').trim().toLowerCase();
    if (!normalized) {
        renderWatchlist(state.watchlistCache);
        return;
    }
    renderWatchlist(state.watchlistCache.filter(stock =>
        String(stock.code).toLowerCase().includes(normalized) ||
        String(stock.name || '').toLowerCase().includes(normalized) ||
        String(stock.note || '').toLowerCase().includes(normalized)
    ));
}

async function loadWatchlist(forceReload = false) {
    if (state.systemHalted) {
        return;
    }
    if (state.watchlistLoaded && !forceReload) {
        filterWatchlist(document.getElementById('watchlist-query')?.value || '');
        return;
    }

    const tbody = document.getElementById('watchlist-tbody');
    tbody.innerHTML = '<tr><td colspan="9" class="state-loading">正在加载自选股...</td></tr>';
    try {
        const result = await apiFetch('/api/watchlist');
        if (!result.success) {
            throw new Error(result.error || '自选股加载失败');
        }
        state.watchlistCache = result.data || [];
        state.watchlistLoaded = true;
        filterWatchlist(document.getElementById('watchlist-query')?.value || '');
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="9" class="state-empty">加载失败: ${escapeHtml(error.message)}</td></tr>`;
    }
}

async function addWatchlistItem() {
    if (state.systemHalted) {
        toast('系统已急停，无法添加自选股', 'error');
        return;
    }
    const queryInput = document.getElementById('watchlist-query');
    const noteInput = document.getElementById('watchlist-note');
    const query = queryInput.value.trim();
    const note = noteInput.value.trim();
    if (!query) {
        toast('请输入股票代码、名称或拼音首字母', 'error');
        return;
    }

    try {
        const result = await apiFetch('/api/watchlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ query, note }),
        });
        if (!result.success) {
            throw new Error(result.error || '添加失败');
        }
        queryInput.value = '';
        noteInput.value = '';
        state.watchlistLoaded = false;
        await loadWatchlist(true);
        toast(`已加入自选: ${result.data.code} ${result.data.name || ''}`.trim(), 'success');
    } catch (error) {
        toast(`添加失败: ${error.message}`, 'error');
    }
}

async function removeWatchlistItem(code) {
    try {
        const result = await apiFetch(`/api/watchlist/${encodeURIComponent(code)}`, {
            method: 'DELETE',
        });
        if (!result.success) {
            throw new Error(result.error || '删除失败');
        }
        state.watchlistLoaded = false;
        await loadWatchlist(true);
        toast(`已移除自选: ${code}`, 'success');
    } catch (error) {
        toast(`删除失败: ${error.message}`, 'error');
    }
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
        title.textContent = '选择 Tushare Token';
    } else {
        title.textContent = '更新任务执行中';
    }
}

function renderUpdateTokenPrompt() {
    const note = document.getElementById('update-token-note');
    const defaultButton = document.getElementById('update-token-default-btn');
    const input = document.getElementById('update-tushare-token');
    const confirmButton = document.getElementById('update-token-confirm-btn');
    if (!note || !defaultButton || !input || !confirmButton) {
        return;
    }

    if (state.updateHasTushareToken) {
        note.textContent = '选择了 Tushare。可直接使用本机默认 Token，也可在下方手动输入临时 Token。';
        defaultButton.style.display = '';
        input.placeholder = 'MANUAL TOKEN OPTIONAL';
        confirmButton.textContent = '使用输入 Token 更新';
    } else {
        note.textContent = '选择了 Tushare。当前未检测到本机默认 Token，请手动输入。';
        defaultButton.style.display = 'none';
        input.placeholder = 'INPUT TUSHARE TOKEN';
        confirmButton.textContent = '开始更新';
    }
}

async function loadUpdateOptions() {
    try {
        const result = await apiFetch('/api/update/options');
        if (!result.success) {
            return;
        }
        const data = result.data || {};
        state.updateHasTushareToken = Boolean(data.has_tushare_token);
        state.updateDefaultProvider = data.default_provider || 'akshare';
        renderUpdateTokenPrompt();
    } catch (error) {
        console.error('loadUpdateOptions failed:', error);
    }
}

function openUpdateModal() {
    if (state.systemHalted) {
        toast('系统已急停，无法继续更新数据', 'error');
        return;
    }
    state.updateProvider = null;
    document.getElementById('update-tushare-token').value = '';
    renderUpdateTokenPrompt();
    setUpdateModalStep('provider');
    document.getElementById('update-modal').classList.add('active');
    loadUpdateOptions();
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
            clearHeatmapPayloadCache();
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
        button.textContent = isLoading
            ? '运行中'
            : (button.id === 'execute-selection-btn' ? '立即执行' : 'RUN');
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
    const reportPath = meta.selection_report_path || '';

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
    if (reportPath) {
        html += `<div class="state-empty">选股记录已保存: ${escapeHtml(reportPath)}</div>`;
    }

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
                selection_report_path: job.selection_report_path || '',
            });
            toast(job.selection_report_path ? '选股执行完成，Markdown 已保存' : '选股执行完成', 'success');
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

function applyHaltState(message = '全部 Web 功能已停止。重启服务器后方可恢复。', mode = 'halt') {
    if (state.systemHalted) {
        const sub = document.getElementById('halt-overlay-sub');
        if (sub) {
            sub.textContent = message;
        }
        const mark = document.getElementById('halt-overlay-mark');
        const title = document.getElementById('halt-overlay-title');
        if (mark && title && mode === 'shutdown') {
            mark.textContent = 'EXIT';
            title.textContent = 'SYSTEM EXITING';
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
    const mark = document.getElementById('halt-overlay-mark');
    const title = document.getElementById('halt-overlay-title');
    if (mark) {
        mark.textContent = mode === 'shutdown' ? 'EXIT' : 'HALTED';
    }
    if (title) {
        title.textContent = mode === 'shutdown' ? 'SYSTEM EXITING' : 'SYSTEM HALTED';
    }
    if (sub) {
        sub.textContent = message;
    }

    document.querySelectorAll('button, input, select, textarea').forEach(element => {
        element.disabled = true;
    });

    const haltBtn = document.getElementById('halt-btn');
    const updateBtn = document.getElementById('update-data-btn');
    const shutdownBtn = document.getElementById('shutdown-btn');
    if (haltBtn) {
        haltBtn.disabled = true;
        haltBtn.innerHTML = '<span class="halt-btn-dot"></span><span>HALTED</span>';
    }
    if (updateBtn) {
        updateBtn.disabled = true;
    }
    if (shutdownBtn) {
        shutdownBtn.disabled = true;
        shutdownBtn.textContent = mode === 'shutdown' ? 'EXITING' : 'EXIT';
    }

    document.getElementById('halt-confirm').classList.remove('active');
    document.getElementById('shutdown-confirm').classList.remove('active');
    document.getElementById('update-modal').classList.remove('active');
    document.getElementById('halt-overlay').classList.add('active');
    document.getElementById('selection-results-headline').textContent = mode === 'shutdown' ? '系统正在退出' : '系统已急停';
    document.getElementById('selection-results-meta').textContent = mode === 'shutdown' ? 'EXITING' : 'HALTED';
    setCommandOutput(mode === 'shutdown' ? 'SYS EXITING' : 'SYS HALTED', 'error');
    updateGlobalTicker(
        mode === 'shutdown'
            ? 'SYS EXITING   SERVER PROCESS WILL STOP   RESTART WEB TO USE AGAIN'
            : 'SYS HALTED   ALL FUNCTIONS DISABLED   RESTART SERVICE REQUIRED'
    );
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

function openShutdownConfirm() {
    document.getElementById('shutdown-confirm').classList.add('active');
}

function closeShutdownConfirm() {
    document.getElementById('shutdown-confirm').classList.remove('active');
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

async function confirmShutdown() {
    try {
        await apiFetch('/api/system_shutdown', { method: 'POST' }, {
            allowWhenHalted: true,
            interpretHalt: false,
        });
    } catch (error) {
        console.warn('system shutdown request failed:', error);
    } finally {
        applyHaltState('系统正在退出。后端服务进程即将关闭，关闭后刷新页面会显示无法连接。', 'shutdown');
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

function runTerminalCommand() {
    const input = document.getElementById('terminal-command-input');
    if (!input) {
        return;
    }

    const raw = input.value.trim();
    if (!raw) {
        setCommandOutput('INPUT REQUIRED', 'error');
        return;
    }

    const command = raw.toUpperCase();
    input.value = '';

    if (command === 'F1' || command === 'DASH' || command === 'HOME') {
        switchPage('dashboard');
        return;
    }
    if (command === 'F2' || command === 'HEAT' || command === 'MAP') {
        switchPage('heatmap');
        return;
    }
    if (command === 'F3' || command === 'STOCK' || command === 'STOCKS' || command === 'POOL') {
        switchPage('stocks');
        return;
    }
    if (command === 'F4' || command === 'RUN' || command === 'SELECT') {
        runSelection();
        return;
    }
    if (command === 'F5' || command === 'CFG' || command === 'STRAT') {
        switchPage('strategies');
        return;
    }
    if (command === 'F6' || command === 'WATCH' || command === 'WATCHLIST' || command === 'ZX') {
        switchPage('watchlist');
        return;
    }
    if (command === 'UPDATE') {
        openUpdateModal();
        return;
    }
    if (command === 'HALT') {
        openHaltConfirm();
        return;
    }
    if (command === 'EXIT' || command === 'QUIT') {
        openShutdownConfirm();
        return;
    }
    openStockByQuery(raw);
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
        const keyword = event.target.value.trim();
        window.clearTimeout(state.stockSearchTimer);
        state.stockSearchTimer = window.setTimeout(() => {
            applyStockSearch(keyword);
        }, 160);
    });
    document.getElementById('stock-search').addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            openStockByQuery(event.target.value);
        }
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

    document.getElementById('watchlist-tbody').addEventListener('click', event => {
        const detailButton = event.target.closest('.view-detail-btn');
        if (detailButton) {
            viewStockDetail(detailButton.dataset.code, detailButton.dataset.name);
            return;
        }
        const exportButton = event.target.closest('.export-watchlist-btn');
        if (exportButton) {
            state.currentStockDetail = {
                code: exportButton.dataset.code,
                name: exportButton.dataset.name || '',
            };
            exportCurrentStock('check');
            return;
        }
        const removeButton = event.target.closest('.remove-watchlist-btn');
        if (removeButton) {
            removeWatchlistItem(removeButton.dataset.code);
        }
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
    document.getElementById('dashboard-index-selector').addEventListener('click', event => {
        const button = event.target.closest('[data-symbol]');
        if (!button || button.classList.contains('active')) {
            return;
        }
        loadDashboardIndexKline(button.dataset.symbol);
    });
    document.getElementById('refresh-selection-options-btn').addEventListener('click', () => loadSelectionOptions(true));
    document.getElementById('watchlist-add-btn').addEventListener('click', addWatchlistItem);
    document.getElementById('watchlist-refresh-btn').addEventListener('click', () => loadWatchlist(true));
    document.getElementById('watchlist-query').addEventListener('input', event => filterWatchlist(event.target.value));
    document.getElementById('watchlist-query').addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addWatchlistItem();
        }
    });
    document.getElementById('watchlist-note').addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addWatchlistItem();
        }
    });
    document.getElementById('save-config-btn').addEventListener('click', saveConfig);
    document.getElementById('refresh-heatmap-btn').addEventListener('click', async () => {
        clearHeatmapPayloadCache();
        await loadHeatmap(true);
        await loadHeatmapMeta(true);
    });
    document.getElementById('heatmap-fullscreen-btn').addEventListener('click', toggleHeatmapFullscreen);
    document.getElementById('update-data-btn').addEventListener('click', openUpdateModal);
    document.getElementById('shutdown-btn').addEventListener('click', openShutdownConfirm);

    document.getElementById('halt-btn').addEventListener('click', openHaltConfirm);
    document.getElementById('cancel-halt-btn').addEventListener('click', closeHaltConfirm);
    document.getElementById('confirm-halt-btn').addEventListener('click', confirmHalt);
    document.getElementById('cancel-shutdown-btn').addEventListener('click', closeShutdownConfirm);
    document.getElementById('confirm-shutdown-btn').addEventListener('click', confirmShutdown);

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
    document.getElementById('update-token-default-btn').addEventListener('click', async () => {
        await startUpdateJob('tushare', '');
    });
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
    document.getElementById('stock-export-btn').addEventListener('click', () => exportCurrentStock('check'));
    document.getElementById('export-confirm-close-btn').addEventListener('click', closeExportConfirm);
    document.getElementById('export-update-first-btn').addEventListener('click', () => exportCurrentStock('update'));
    document.getElementById('export-force-btn').addEventListener('click', () => exportCurrentStock('force'));
    document.getElementById('export-confirm').addEventListener('click', event => {
        if (event.target.id === 'export-confirm') {
            closeExportConfirm();
        }
    });

    document.getElementById('industry-modal').addEventListener('click', event => {
        if (event.target.id === 'industry-modal') {
            closeIndustryModal();
        }
    });
    document.getElementById('industry-modal-close-btn').addEventListener('click', closeIndustryModal);
    document.addEventListener('fullscreenchange', syncHeatmapFullscreenState);
    document.addEventListener('webkitfullscreenchange', syncHeatmapFullscreenState);

    document.getElementById('terminal-command-go').addEventListener('click', runTerminalCommand);
    document.getElementById('terminal-command-input').addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            runTerminalCommand();
        }
    });
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
    window.addEventListener('resize', () => resizeDashboardIndexChart());
    window.addEventListener('resize', () => {
        if (state.chartInstance && typeof state.chartInstance.resize === 'function') {
            state.chartInstance.resize();
        }
    });

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
            if (typeof state.chartInstance.dispose === 'function') {
                state.chartInstance.dispose();
            } else if (typeof state.chartInstance.destroy === 'function') {
                state.chartInstance.destroy();
            }
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
