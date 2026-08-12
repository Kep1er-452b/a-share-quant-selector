'use strict';

(function initSystemWorkspace(global) {
    const EVENT_FILTER_FIELDS = [
        'since', 'until', 'severity', 'domain', 'market', 'module',
        'dataset', 'symbol', 'job_id', 'error_code',
    ];
    const ACTIVE_TASK_STATES = new Set(['queued', 'running', 'cancelling']);
    const SUPPORTED_CANCEL_TYPES = new Set([
        'selection', 'update', 'diagnostic', 'wyckoff', 'domain_sync',
    ]);

    class SystemWorkspace {
        constructor() {
            this.mounted = false;
            this.active = false;
            this.controller = null;
            this.timer = null;
            this.view = 'ops-tasks';
            this.pendingJobId = '';
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
            const toolbar = this.root?.querySelector('#ops-events .domain-toolbar');
            if (!toolbar) return;
            const severity = document.getElementById('ops-event-severity');
            if (severity) severity.dataset.opsFilter = 'severity';
            const refresh = document.getElementById('ops-events-refresh');
            EVENT_FILTER_FIELDS.filter(field => field !== 'severity').forEach(field => {
                if (toolbar.querySelector(`[data-ops-filter="${field}"]`)) return;
                const input = document.createElement('input');
                input.className = 'search-input ops-event-filter';
                input.dataset.opsFilter = field;
                input.type = ['since', 'until'].includes(field) ? 'datetime-local' : 'search';
                input.placeholder = field.toUpperCase();
                input.setAttribute('aria-label', `事件筛选 ${field}`);
                toolbar.insertBefore(input, refresh || null);
            });
            if (this.pendingJobId) {
                const jobFilter = toolbar.querySelector('[data-ops-filter="job_id"]');
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
                this.fetchInto('/api/ops/health', 'ops-health', this.renderObject.bind(this)),
                this.fetchInto('/api/ops/performance', 'ops-performance', this.renderObject.bind(this)),
            ]);
        }

        async refreshTasks() {
            return this.fetchInto('/api/ops/tasks?limit=100', 'ops-tasks', this.renderTasks.bind(this));
        }

        async refreshEvents() {
            const query = new URLSearchParams({ limit: '100' });
            this.root?.querySelectorAll('[data-ops-filter]').forEach(control => {
                const field = control.dataset.opsFilter;
                let value = String(control.value || '').trim();
                if (!field || !value) return;
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
                if (target) target.innerHTML = renderer(payload);
            } catch (error) {
                if (error.name === 'AbortError') return;
                const target = document.getElementById(targetId);
                if (target) target.innerHTML = `<div class="state-error">${this.escape(error.message)}</div>`;
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
            const items = payload.items || [];
            if (!items.length) return '<div class="state-empty">没有匹配事件</div>';
            return `<div class="ops-grid">${items.map(item => {
                const context = [item.domain, item.dataset, item.module, item.error_code, item.job_id].filter(Boolean).join(' · ');
                const symbol = item.symbol
                    ? `<button class="text-action" data-ops-symbol="${this.escape(item.symbol)}" data-market="${this.escape(item.market || 'a_share')}">${this.escape(item.symbol)}</button>`
                    : '';
                return `<div class="ops-row"><b>${this.escape(item.severity)}</b><span>${this.escape(item.message)}</span><span>${this.escape(context || '--')} ${symbol}</span></div>`;
            }).join('')}</div>`;
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

        renderObject(payload) {
            return `<pre class="ops-json">${this.escape(JSON.stringify(payload, null, 2))}</pre>`;
        }

        escape(value) {
            return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
        }
    }

    global.quantSystemWorkspace = new SystemWorkspace();
})(window);
