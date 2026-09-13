'use strict';

(function initSystemWorkspace(global) {
    const EVENT_FILTER_FIELDS = [
        'since', 'until', 'severity', 'domain', 'market', 'module',
        'dataset', 'symbol', 'job_id', 'error_code',
    ];
    const ACTIVE_TASK_STATES = new Set(['queued', 'running', 'cancelling']);
    const SUPPORTED_CANCEL_TYPES = new Set([
        'selection', 'update', 'diagnostic', 'wyckoff', 'domain_sync', 'server_results',
    ]);
    const STATUS_META = {
        ready: ['正常', 'ok'], available: ['可用', 'ok'], completed: ['已完成', 'ok'],
        closed: ['正常', 'ok'], fresh: ['新鲜', 'ok'], running: ['运行中', 'active'],
        queued: ['排队中', 'active'], cancelling: ['停止中', 'warning'],
        warning: ['需关注', 'warning'], completed_with_warnings: ['有警告', 'warning'],
        stale: ['已过期', 'warning'], open: ['已断路', 'error'], critical: ['严重', 'critical'],
        error: ['错误', 'error'], failed: ['失败', 'error'], halted: ['已中止', 'error'],
        unavailable: ['不可用', 'muted'], empty: ['未同步', 'muted'], unknown: ['未知', 'muted'],
        not_checked: ['未深检', 'muted'], cancelled: ['已取消', 'muted'],
    };
    const EVENT_SEVERITY_META = {
        critical: { label: '严重', code: 'CRITICAL', tone: 'critical', rank: 4 },
        error: { label: '错误', code: 'ERROR', tone: 'error', rank: 3 },
        warning: { label: '警告', code: 'WARNING', tone: 'warning', rank: 2 },
        info: { label: '信息', code: 'INFO', tone: 'info', rank: 1 },
        debug: { label: '调试', code: 'DEBUG', tone: 'muted', rank: 0 },
    };
    const PERFORMANCE_META = {
        api_call_count: ['API 调用', '次'],
        api_latency_ms: ['API 响应延迟', 'ms'],
        db_duration_ms: ['数据库查询耗时', 'ms'],
        domain_store_query_ms: ['领域仓库查询耗时', 'ms'],
        response_payload_bytes: ['响应数据量', 'bytes'],
        cache_hit_rate: ['缓存命中率', '%'],
        retained_task_count: ['保留任务', '项'],
        retained_event_count: ['保留事件', '条'],
        domain_sync_rows_written: ['同步写入行数', '行'],
        task_elapsed_seconds: ['任务耗时', 's'],
        task_progress_pct: ['任务进度', '%'],
    };
    const DOMAIN_LABELS = {
        hong_kong: '港股', futures: '期货', global_commodities: '全球商品',
        macro: '宏观', industry: '行业', a_share: 'A 股',
    };

    class SystemWorkspace {
        constructor() {
            this.mounted = false;
            this.active = false;
            this.controller = null;
            this.timer = null;
            this.view = 'ops-tasks';
            this.pendingJobId = '';
            this.eventSeverity = '';
            this.onTabs = this.onTabs.bind(this);
            this.onContent = this.onContent.bind(this);
        }

        mount() {
            if (this.mounted) return;
            this.root = document.getElementById('system-workspace-root');
            if (!this.root) return;
            this.root.addEventListener('click', this.onTabs);
            this.root.addEventListener('click', this.onContent);
            this.root.addEventListener('change', this.onTabs);
            this.ensureEventFilters();
            this.mounted = true;
        }

        ensureEventFilters() {
            EVENT_FILTER_FIELDS.forEach(field => {
                const control = this.root?.querySelector(`[data-ops-filter="${field}"]`);
                if (control) control.dataset.opsFilter = field;
            });
            if (this.pendingJobId) {
                const jobFilter = this.root?.querySelector('[data-ops-filter="job_id"]');
                if (jobFilter) jobFilter.value = this.pendingJobId;
            }
        }

        async activate() {
            if (this.active) return;
            this.mount();
            this.active = true;
            await this.refresh();
            this.timer = global.setInterval(() => {
                if (this.active && this.view === 'ops-tasks') this.refreshTasks();
            }, 5000);
        }

        deactivate() {
            this.active = false;
            this.controller?.abort();
            this.controller = null;
            if (this.timer) global.clearInterval(this.timer);
            this.timer = null;
            if (this.mounted && this.root) {
                this.root.removeEventListener('click', this.onTabs);
                this.root.removeEventListener('click', this.onContent);
                this.root.removeEventListener('change', this.onTabs);
                this.mounted = false;
            }
        }

        async refresh() {
            this.controller?.abort();
            this.controller = new AbortController();
            await Promise.allSettled([
                this.refreshTasks(),
                this.refreshEvents(),
                this.fetchInto('/api/ops/health', 'ops-health', this.renderHealth.bind(this)),
                this.fetchInto('/api/ops/performance', 'ops-performance', this.renderPerformance.bind(this)),
            ]);
        }

        async refreshTasks() {
            return this.fetchInto('/api/ops/tasks?limit=100', 'ops-tasks', this.renderTasks.bind(this));
        }

        async refreshEvents() {
            const query = new URLSearchParams({ limit: '500' });
            this.root?.querySelectorAll('[data-ops-filter]').forEach(control => {
                const field = control.dataset.opsFilter;
                let value = String(control.value || '').trim();
                if (!field || field === 'severity' || !value) return;
                if (['since', 'until'].includes(field)) {
                    const timestamp = new Date(value);
                    if (Number.isNaN(timestamp.getTime())) return;
                    value = timestamp.toISOString();
                }
                query.set(field, value);
            });
            return this.fetchInto(`/api/ops/events?${query}`, 'ops-events-content', this.renderEvents.bind(this));
        }

        async cancelTask(taskType, jobId) {
            if (!SUPPORTED_CANCEL_TYPES.has(taskType) || !/^[A-Za-z0-9_-]{1,64}$/.test(jobId)) return;
            const token = document.querySelector('meta[name="quant-session-token"]')?.content || '';
            const status = document.getElementById('ops-export-status');
            if (status) status.textContent = 'CANCELLING';
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 30000);
            try {
                const response = await fetch(
                    `/api/ops/tasks/${encodeURIComponent(taskType)}/${encodeURIComponent(jobId)}/cancel`,
                    { method: 'POST', headers: { 'X-Quant-Session': token }, signal: controller.signal },
                );
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
                if (status) status.textContent = 'CANCEL REQUESTED';
                await this.refreshTasks();
            } catch (error) {
                if (status) {
                    status.textContent = 'CANCEL ERROR';
                    status.title = error.message;
                }
            } finally {
                clearTimeout(timeoutId);
            }
        }

        async exportDiagnostics() {
            const status = document.getElementById('ops-export-status');
            if (status) status.textContent = 'EXPORTING';
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 30000);
            try {
                const token = document.querySelector('meta[name="quant-session-token"]')?.content || '';
                const response = await fetch('/api/ops/diagnostics/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Quant-Session': token },
                    body: JSON.stringify({ limit: 200 }),
                    signal: controller.signal,
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
                if (status) {
                    status.textContent = payload.filename || 'EXPORTED';
                    status.title = '诊断包已写入本机日志目录；响应不暴露绝对路径。';
                }
            } catch (error) {
                if (status) {
                    status.textContent = 'EXPORT ERROR';
                    status.title = error.message;
                }
            } finally {
                clearTimeout(timeoutId);
            }
        }

        async fetchInto(url, targetId, renderer) {
            if (!this.active) return;
            const controller = new AbortController();
            const parentSignal = this.controller?.signal;
            const abortFromParent = () => controller.abort();
            parentSignal?.addEventListener('abort', abortFromParent, { once: true });
            const timeoutId = setTimeout(() => controller.abort(), 30000);
            try {
                const token = document.querySelector('meta[name="quant-session-token"]')?.content || '';
                const response = await fetch(url, {
                    headers: { 'X-Quant-Session': token },
                    signal: controller.signal,
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json();
                const target = document.getElementById(targetId);
                if (target) {
                    target.classList.remove('state-loading', 'state-empty', 'state-error');
                    target.innerHTML = renderer(payload);
                }
            } catch (error) {
                if (error.name === 'AbortError') return;
                const target = document.getElementById(targetId);
                if (target) {
                    target.classList.remove('state-loading', 'state-empty');
                    target.innerHTML = `<div class="state-error">${this.escape(error.message)}</div>`;
                }
            } finally {
                clearTimeout(timeoutId);
                parentSignal?.removeEventListener('abort', abortFromParent);
            }
        }

        onTabs(event) {
            if (event.target.matches?.('[data-ops-filter]')) {
                this.refreshEvents();
                return;
            }
            const severity = event.target.closest?.('[data-ops-severity]');
            if (severity) {
                this.eventSeverity = severity.dataset.opsSeverity || '';
                const control = document.getElementById('ops-event-severity');
                if (control) control.value = this.eventSeverity;
                this.updateSeverityButtons();
                this.refreshEvents();
                return;
            }
            if (event.target.closest?.('#ops-events-clear')) {
                this.root?.querySelectorAll('[data-ops-filter]').forEach(control => { control.value = ''; });
                this.eventSeverity = '';
                this.updateSeverityButtons();
                this.refreshEvents();
                return;
            }
            if (event.target.closest('#ops-diagnostics-export')) {
                this.exportDiagnostics();
                return;
            }
            const tab = event.target.closest('[data-ops-view]');
            if (!tab) {
                if (event.target.id === 'ops-events-refresh') this.refreshEvents();
                return;
            }
            this.activateView(tab.dataset.opsView);
        }

        onContent(event) {
            const cancellation = event.target.closest('[data-ops-cancel]');
            if (cancellation) {
                this.cancelTask(cancellation.dataset.taskType, cancellation.dataset.jobId);
                return;
            }
            const symbol = event.target.closest('[data-ops-symbol]');
            if (symbol) {
                global.quantEquityRouter.openInstrument(symbol.dataset.market || 'a_share', symbol.dataset.opsSymbol, { page: 'system', view: this.view });
            }
        }

        renderTasks(payload) {
            const items = payload.items || [];
            if (!items.length) return '<div class="state-empty">没有活动或保留任务</div>';
            return `<div class="ops-grid">${items.map(item => {
                const taskType = this.escape(item.task_type);
                const jobId = this.escape(item.job_id);
                const canCancel = ACTIVE_TASK_STATES.has(item.status) && SUPPORTED_CANCEL_TYPES.has(item.task_type);
                const action = canCancel
                    ? `<button class="text-action" type="button" data-ops-cancel data-task-type="${taskType}" data-job-id="${jobId}">CANCEL</button>`
                    : '';
                return `<div class="ops-row"><b>${taskType}</b><span>${this.escape(item.status)} · ${jobId}</span><span>${this.escape(item.current_step || item.dataset || '--')} ${action}</span></div>`;
            }).join('')}</div>`;
        }

        renderEvents(payload) {
            const sourceItems = payload.items || [];
            const normalized = sourceItems.map(item => ({ item, severity: this.normalizeEventSeverity(item) }));
            const filtered = this.eventSeverity
                ? normalized.filter(entry => entry.severity === this.eventSeverity)
                : normalized;
            const visible = filtered.slice(0, 100);
            const counts = { critical: 0, error: 0, warning: 0, info: 0 };
            normalized.forEach(({ severity }) => {
                const key = severity === 'debug' ? 'info' : severity;
                counts[key] = (counts[key] || 0) + 1;
            });
            const bounded = Number(payload.total || 0) > sourceItems.length;
            const totalText = bounded
                ? `最近 ${this.formatNumber(sourceItems.length)} / 共 ${this.formatNumber(payload.total)} 条`
                : `筛选结果 ${this.formatNumber(filtered.length)} 条`;
            const overview = `<div class="ops-event-overview" aria-label="当前结果事件级别统计">
                ${['critical', 'error', 'warning', 'info'].map(level => {
                    const meta = EVENT_SEVERITY_META[level];
                    return `<div class="ops-event-count" data-tone="${meta.tone}"><span>${meta.label}</span><strong>${this.formatNumber(counts[level])}</strong></div>`;
                }).join('')}
                <div class="ops-event-total"><span>当前显示</span><strong>${this.formatNumber(visible.length)}</strong><span>${this.escape(totalText)}</span></div>
            </div>`;
            if (!visible.length) return `${overview}<div class="state-empty">当前筛选条件下没有事件</div>`;
            return `${overview}<div class="ops-event-list">${visible.map(({ item, severity }) => this.renderEventItem(item, severity)).join('')}</div>`;
        }

        renderEventItem(item, severity) {
            const meta = EVENT_SEVERITY_META[severity] || EVENT_SEVERITY_META.info;
            const rawSeverity = String(item.severity || 'info').toLowerCase();
            const contexts = [
                ['类型', this.eventTypeLabel(item.event_type)],
                ['域', DOMAIN_LABELS[item.domain] || item.domain],
                ['市场', DOMAIN_LABELS[item.market] || item.market],
                ['模块', item.module], ['数据集', item.dataset], ['任务', item.job_id],
                ['错误码', item.error_code],
            ].filter(([, value]) => value);
            const symbol = item.symbol
                ? `<button class="text-action ops-event-symbol" type="button" data-ops-symbol="${this.escape(item.symbol)}" data-market="${this.escape(item.market || 'a_share')}">${this.escape(item.symbol)}</button>`
                : '';
            const audit = {
                event_id: item.event_id,
                raw_severity: rawSeverity,
                event_type: item.event_type,
                details: item.details || {},
            };
            return `<article class="ops-event-item" data-tone="${meta.tone}">
                <header class="ops-event-head">
                    <span class="ops-event-badge" data-tone="${meta.tone}"><b>${meta.label}</b><span>${meta.code}</span></span>
                    <time datetime="${this.escape(item.timestamp || '')}">${this.escape(this.formatDateTime(item.timestamp))}</time>
                    ${rawSeverity !== severity ? `<span class="ops-event-reclassified">原始级别 ${this.escape(rawSeverity)}</span>` : ''}
                </header>
                <p class="ops-event-message">${this.escape(item.message || '未提供事件说明')}</p>
                ${contexts.length || symbol ? `<div class="ops-event-context">${contexts.map(([label, value]) => `<span><small>${label}</small>${this.escape(value)}</span>`).join('')}${symbol}</div>` : ''}
                <details class="ops-disclosure"><summary>完整上下文</summary><pre>${this.escape(JSON.stringify(audit, null, 2))}</pre></details>
            </article>`;
        }

        renderHealth(payload) {
            const overall = this.statusMeta(payload.status);
            const stores = Object.entries(payload.stores || {});
            const datasets = Object.entries(payload.datasets || {});
            const readyStores = stores.filter(([, item]) => this.storeStatus(item) === 'ready').length;
            const disk = payload.disk || {};
            const tasks = payload.tasks || {};
            const cache = payload.a_share_cache || {};
            const freePercent = this.clampPercent(Number(disk.free_ratio || 0) * 100);
            const headline = overall.tone === 'ok' ? '系统运行正常' : (overall.tone === 'warning' ? '有项目需要关注' : '系统存在异常');
            return `<div class="ops-visual ops-health-visual">
                <header class="ops-summary-head" data-tone="${overall.tone}">
                    <div><span class="ops-kicker">SYSTEM HEALTH</span><strong>${headline}</strong><small>轻量检查，不读取完整仓库内容</small></div>
                    ${this.statusBadge(payload.status)}
                </header>
                <div class="ops-key-grid">
                    ${this.keyMetric('磁盘可用', this.formatBytes(disk.free_bytes), `${this.formatNumber(freePercent, 1)}% 剩余`, disk.status)}
                    ${this.keyMetric('活动任务', this.formatNumber(tasks.active_count || 0), `队列 ${this.formatNumber(tasks.queue_depth || 0)}`, tasks.status)}
                    ${this.keyMetric('A 股快照', this.formatNumber(cache.snapshot_stock_count || 0), cache.snapshot_latest_date || '暂无日期', cache.status)}
                    ${this.keyMetric('领域仓库', `${readyStores}/${stores.length}`, `占用 ${this.formatBytes(payload.storage_bytes)}`, readyStores === stores.length ? 'ready' : 'warning')}
                </div>
                <div class="ops-health-layout">
                    <section class="ops-data-section ops-health-primary">
                        <div class="ops-section-title"><h3>A 股数据与任务</h3><span>${this.escape(cache.active_provider || '未设置数据源')}</span></div>
                        <div class="ops-fact-grid">
                            ${this.fact('本机股票', this.formatNumber(cache.local_stock_count || 0), cache.local_latest_date || '暂无日期')}
                            ${this.fact('行情快照', this.formatNumber(cache.snapshot_stock_count || 0), cache.snapshot_latest_date || '暂无日期')}
                            ${this.fact('指数缓存', this.booleanLabel(cache.indices_ready), cache.indices_ready ? '可读取' : '待生成', cache.indices_ready ? 'ready' : 'warning')}
                            ${this.fact('行业缓存', this.booleanLabel(cache.industry_ready), cache.industry_ready ? '可读取' : '待生成', cache.industry_ready ? 'ready' : 'warning')}
                            ${this.fact('热力图', this.booleanLabel(cache.heatmap_payloads_ready), cache.heatmap_payloads_ready ? '可读取' : '待生成', cache.heatmap_payloads_ready ? 'ready' : 'warning')}
                            ${this.fact('市值异常', this.formatNumber(cache.market_cap_anomaly_count || 0), cache.refresh_pending ? '缓存刷新中' : '无待刷新', (cache.market_cap_anomaly_count || cache.refresh_pending) ? 'warning' : 'ready')}
                        </div>
                        <div class="ops-meter-row"><span>磁盘使用</span><div class="ops-meter" role="meter" aria-label="磁盘使用率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${this.formatNumber(100 - freePercent, 1)}"><i style="width:${this.clampPercent(100 - freePercent)}%"></i></div><b>${this.formatNumber(100 - freePercent, 1)}%</b></div>
                        <div class="ops-inline-statuses">
                            ${this.inlineStatus('快照时效', cache.snapshot_stale ? 'stale' : 'fresh')}
                            ${this.inlineStatus('刷新任务', cache.refresh_pending ? 'running' : 'ready')}
                            ${this.inlineStatus('任务冲突', tasks.conflict_detected ? 'warning' : 'ready')}
                            ${this.inlineStatus('来源错误', Number(tasks.source_error_count || 0) ? 'error' : 'ready')}
                        </div>
                    </section>
                    <section class="ops-data-section">
                        <div class="ops-section-title"><h3>数据源断路器</h3><span>网络失败保护</span></div>
                        ${this.renderCircuits(payload.api_circuits)}
                    </section>
                    <section class="ops-data-section ops-health-wide">
                        <div class="ops-section-title"><h3>独立领域仓库</h3><span>${stores.length} 个仓库</span></div>
                        <div class="ops-status-table ops-store-table">${stores.map(([name, item]) => {
                            const status = this.storeStatus(item);
                            return `<div class="ops-status-row"><b>${this.escape(DOMAIN_LABELS[name] || name)}</b>${this.statusBadge(status)}<span>Schema ${this.escape(item.schema_version ?? '--')}</span><span>${this.escape(String(item.journal_mode || '--').toUpperCase())}</span><span>${this.formatBytes(item.db_size_bytes)}</span></div>`;
                        }).join('') || '<div class="state-empty">没有领域仓库</div>'}</div>
                    </section>
                    <section class="ops-data-section ops-health-wide">
                        <div class="ops-section-title"><h3>数据新鲜度</h3><span>${datasets.length} 个数据集</span></div>
                        <div class="ops-status-table ops-dataset-table">${datasets.map(([name, item]) => {
                            const status = item.freshness === 'stale' ? 'stale' : (item.status || item.freshness || 'unknown');
                            const age = Number.isFinite(Number(item.age_days)) ? `${this.formatNumber(item.age_days, 1)} 天` : '未知';
                            return `<div class="ops-status-row"><b>${this.escape(name)}</b>${this.statusBadge(status)}<span>${this.escape(this.frequencyLabel(item.frequency))}</span><span>距更新 ${age}</span><span>${this.escape(this.formatDateTime(item.updated_at, true))}</span></div>`;
                        }).join('') || '<div class="state-empty">没有数据集状态</div>'}</div>
                    </section>
                </div>
                ${this.rawDisclosure('原始健康数据', payload)}
            </div>`;
        }

        renderPerformance(payload) {
            const signals = payload.signals || {};
            const entries = Object.entries(signals).sort(([left], [right]) => this.performanceOrder(left) - this.performanceOrder(right));
            const latency = signals.api_latency_ms || {};
            const database = signals.db_duration_ms || signals.domain_store_query_ms || {};
            const responseSize = signals.response_payload_bytes || {};
            const samplePercent = this.clampPercent((Number(payload.retained_samples || 0) / Math.max(Number(payload.capacity || 0), 1)) * 100);
            return `<div class="ops-visual ops-performance-visual">
                <header class="ops-summary-head" data-tone="active">
                    <div><span class="ops-kicker">PERFORMANCE WINDOW</span><strong>本机性能观察</strong><small>指标来自当前进程内的有界采样</small></div>
                    <span class="ops-window-count">${this.formatNumber(payload.retained_samples || 0)} / ${this.formatNumber(payload.capacity || 0)} SAMPLES</span>
                </header>
                <div class="ops-key-grid">
                    ${this.keyMetric('API 平均延迟', this.formatMetric(latency.avg, 'ms'), `最近 ${this.formatMetric(latency.latest, 'ms')}`, latency.status || (latency.count ? 'available' : 'unavailable'))}
                    ${this.keyMetric('数据库平均耗时', this.formatMetric(database.avg, 'ms'), `最近 ${this.formatMetric(database.latest, 'ms')}`, database.status || (database.count ? 'available' : 'unavailable'))}
                    ${this.keyMetric('平均响应数据量', this.formatMetric(responseSize.avg, 'bytes'), `最大 ${this.formatMetric(responseSize.max, 'bytes')}`, responseSize.status || (responseSize.count ? 'available' : 'unavailable'))}
                    ${this.keyMetric('采样窗口', `${this.formatNumber(samplePercent, 1)}%`, `${this.formatNumber(payload.retained_samples || 0)} 条已保留`, 'available')}
                </div>
                <section class="ops-data-section">
                    <div class="ops-section-title"><h3>性能信号</h3><span>观察值不等同于告警阈值</span></div>
                    <div class="ops-performance-list">${entries.map(([name, signal]) => this.renderPerformanceSignal(name, signal)).join('') || '<div class="state-empty">当前进程还没有性能采样</div>'}</div>
                </section>
                <p class="ops-footnote">分布刻度按当前观察窗口的最小值和最大值归一化，仅用于比较最近值与平均值。</p>
                ${this.rawDisclosure('原始性能数据', payload)}
            </div>`;
        }

        renderPerformanceSignal(name, signal) {
            const [label, unit] = PERFORMANCE_META[name] || [this.humanizeMetric(name), ''];
            const available = signal.status === 'available';
            if (!available) {
                return `<div class="ops-performance-row is-unavailable"><div><b>${this.escape(label)}</b><code>${this.escape(name)}</code></div>${this.statusBadge('unavailable')}<p>${this.escape(this.unavailableReason(signal.reason))}</p></div>`;
            }
            const primary = Number.isFinite(Number(signal.value)) ? signal.value : signal.latest;
            const min = Number(signal.min);
            const max = Number(signal.max);
            const span = Number.isFinite(min) && Number.isFinite(max) ? max - min : 0;
            const latestPosition = span > 0 ? this.clampPercent(((Number(signal.latest) - min) / span) * 100) : 100;
            const averagePosition = span > 0 ? this.clampPercent(((Number(signal.avg) - min) / span) * 100) : 100;
            const hasDistribution = Number.isFinite(Number(signal.count));
            return `<div class="ops-performance-row">
                <div class="ops-performance-name"><b>${this.escape(label)}</b><code>${this.escape(name)}</code></div>
                <strong class="ops-performance-value">${this.formatMetric(primary, unit)}</strong>
                <div class="ops-performance-range">
                    ${hasDistribution ? `<div class="ops-range-scale" aria-label="${this.escape(label)}观察窗口分布"><i style="width:${latestPosition}%"></i><em style="left:${averagePosition}%"></em></div><div class="ops-range-labels"><span>MIN ${this.formatMetric(signal.min, unit)}</span><span>AVG ${this.formatMetric(signal.avg, unit)}</span><span>MAX ${this.formatMetric(signal.max, unit)}</span></div>` : '<span class="ops-no-distribution">实时计数值</span>'}
                </div>
                <span class="ops-sample-count">${hasDistribution ? `${this.formatNumber(signal.count)} SAMPLES` : 'GAUGE'}</span>
            </div>`;
        }

        renderCircuits(circuits = {}) {
            const providers = Object.entries(circuits?.providers || {});
            if (!providers.length) return '<div class="state-empty">没有数据源断路器状态</div>';
            return `<div class="ops-circuit-list">${providers.map(([name, item]) => `<div class="ops-circuit-row"><b>${this.escape(name)}</b>${this.statusBadge(item.state || 'unknown')}<span>连续失败 ${this.formatNumber(item.consecutive_failures ?? 0)}</span><time>${this.escape(this.formatDateTime(item.updated_at, true))}</time></div>`).join('')}</div>`;
        }

        normalizeEventSeverity(item) {
            const raw = String(item?.severity || 'info').toLowerCase();
            const status = String(item?.details?.status || '').toLowerCase();
            const message = String(item?.message || '').toLowerCase();
            const cancellation = status === 'cancelled'
                || /\b(cancelled|cancellation requested)\b/.test(message)
                || /用户.{0,6}(停止|取消)|任务已安全结束/.test(message);
            const failure = raw === 'error' || ['failed', 'error', 'halted'].includes(status)
                || /\b(error|failed|failure)\b/.test(message)
                || /(执行|任务|同步|更新).{0,8}(失败|异常)|发生.{0,4}(错误|异常)/.test(message);
            if ((cancellation && !failure) || raw === 'debug') return cancellation ? 'info' : 'debug';
            if (['critical', 'fatal', 'emergency', 'alert'].includes(raw)) return 'critical';
            if (raw === 'error') return 'error';
            if (['warning', 'warn'].includes(raw)) return 'warning';
            return 'info';
        }

        updateSeverityButtons() {
            const value = this.eventSeverity;
            this.root?.querySelectorAll('[data-ops-severity]').forEach(button => {
                const selected = (button.dataset.opsSeverity || '') === value;
                button.classList.toggle('active', selected);
                button.setAttribute('aria-pressed', String(selected));
            });
        }

        statusMeta(status) {
            const key = String(status || 'unknown').toLowerCase();
            const [label, tone] = STATUS_META[key] || [key || '未知', 'muted'];
            return { key, label, tone };
        }

        statusBadge(status) {
            const meta = this.statusMeta(status);
            return `<span class="ops-status-badge" data-tone="${meta.tone}" title="${this.escape(meta.key)}">${this.escape(meta.label)}</span>`;
        }

        storeStatus(item = {}) {
            if (item.status) return String(item.status).toLowerCase();
            if (item.error || !['ok', 'not_checked', undefined, null].includes(item.integrity)) return 'error';
            return 'ready';
        }

        keyMetric(label, value, detail, status) {
            const meta = this.statusMeta(status);
            return `<div class="ops-key-metric" data-tone="${meta.tone}"><span>${this.escape(label)}</span><strong>${this.escape(value)}</strong><small>${this.escape(detail)}</small></div>`;
        }

        fact(label, value, detail, status = 'ready') {
            return `<div class="ops-fact"><span>${this.escape(label)}</span><strong>${this.escape(value)}</strong><small data-tone="${this.statusMeta(status).tone}">${this.escape(detail)}</small></div>`;
        }

        inlineStatus(label, status) {
            return `<span class="ops-inline-status"><b>${this.escape(label)}</b>${this.statusBadge(status)}</span>`;
        }

        rawDisclosure(label, payload) {
            return `<details class="ops-disclosure ops-raw"><summary>${this.escape(label)}</summary><pre>${this.escape(JSON.stringify(payload, null, 2))}</pre></details>`;
        }

        formatMetric(value, unit = '') {
            const number = Number(value);
            if (!Number.isFinite(number)) return '--';
            if (unit === 'bytes') return this.formatBytes(number);
            const formatted = this.formatNumber(number, Math.abs(number) < 10 && !Number.isInteger(number) ? 2 : 1);
            return unit ? `${formatted} ${unit}` : formatted;
        }

        formatBytes(value) {
            const number = Number(value);
            if (!Number.isFinite(number) || number < 0) return '--';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let scaled = number;
            let index = 0;
            while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; }
            return `${this.formatNumber(scaled, index === 0 ? 0 : 1)} ${units[index]}`;
        }

        formatNumber(value, digits = 0) {
            const number = Number(value);
            if (!Number.isFinite(number)) return '--';
            return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(number);
        }

        formatDateTime(value, compact = false) {
            if (!value) return '暂无记录';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return String(value);
            return new Intl.DateTimeFormat('zh-CN', {
                year: compact ? undefined : 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', second: compact ? undefined : '2-digit', hour12: false,
            }).format(date).replace(/\//g, '-');
        }

        clampPercent(value) {
            const number = Number(value);
            return Math.max(0, Math.min(100, Number.isFinite(number) ? number : 0));
        }

        booleanLabel(value) { return value ? 'READY' : 'WAIT'; }

        frequencyLabel(value) {
            return ({ daily: '每日', weekly: '每周', monthly: '每月', quarterly: '每季', annual: '每年', irregular: '不定期' })[value] || value || '未知频率';
        }

        eventTypeLabel(value) {
            return ({ task: '任务', domain_sync: '领域同步', security: '安全', data_quality: '数据质量' })[value] || value || '系统';
        }

        performanceOrder(name) {
            const order = Object.keys(PERFORMANCE_META).indexOf(name);
            return order === -1 ? 999 : order;
        }

        humanizeMetric(name) { return String(name || '').replace(/_/g, ' ').toUpperCase(); }

        unavailableReason(reason) {
            const text = String(reason || '当前窗口没有采样');
            if (text === 'no generic cache observer') return '尚未接入通用缓存命中率观测';
            if (text === 'no samples observed') return '当前窗口没有采样';
            return text;
        }

        activateView(view) {
            this.view = view || 'ops-tasks';
            this.root?.querySelectorAll('[data-ops-view]').forEach(item => item.classList.toggle('active', item.dataset.opsView === this.view));
            this.root?.querySelectorAll('.ops-view').forEach(item => item.classList.toggle('active', item.id === this.view));
        }

        openJob(jobId) {
            const normalized = String(jobId || '').trim();
            if (!/^[A-Za-z0-9_-]{1,64}$/.test(normalized)) return;
            this.pendingJobId = normalized;
            this.mount();
            this.ensureEventFilters();
            const jobFilter = this.root?.querySelector('[data-ops-filter="job_id"]');
            if (jobFilter) jobFilter.value = normalized;
            this.activateView('ops-events');
            if (this.active) this.refreshEvents();
        }

        escape(value) {
            return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
        }
    }

    global.quantSystemWorkspace = new SystemWorkspace();
})(window);
