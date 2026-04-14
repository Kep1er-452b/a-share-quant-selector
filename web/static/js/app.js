'use strict';

const PAGE_TITLES = {
    dashboard: '系统概览',
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
    localProgressTimer: null,
    jobStartTime: null,
    serverElapsedBase: 0,
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
    setStatus('halted');

    const sub = document.getElementById('halt-overlay-sub');
    if (sub) {
        sub.textContent = message;
    }

    document.querySelectorAll('button, input, select, textarea').forEach(element => {
        element.disabled = true;
    });

    const haltBtn = document.getElementById('halt-btn');
    if (haltBtn) {
        haltBtn.disabled = true;
        haltBtn.innerHTML = '<span class="halt-btn-dot"></span><span>HALTED</span>';
    }

    document.getElementById('halt-confirm').classList.remove('active');
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

    document.getElementById('halt-btn').addEventListener('click', openHaltConfirm);
    document.getElementById('cancel-halt-btn').addEventListener('click', closeHaltConfirm);
    document.getElementById('confirm-halt-btn').addEventListener('click', confirmHalt);

    document.getElementById('stock-modal').addEventListener('click', event => {
        if (event.target.id === 'stock-modal') {
            closeModal();
        }
    });
    document.getElementById('modal-close-btn').addEventListener('click', closeModal);
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
