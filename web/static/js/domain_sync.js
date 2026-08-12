'use strict';

(function initDomainSync(global) {
    const TERMINAL = new Set(['completed', 'completed_with_warnings', 'failed', 'error', 'cancelled']);
    const ACTIVE = new Set(['queued', 'running', 'cancelling']);
    const controls = new Map();
    const PERMISSION_PATTERNS = [
        /没有接口.+访问权限/i,
        /权限.+访问/i,
        /permission.+denied/i,
        /insufficient.+permission/i,
    ];

    async function requestJson(url, options = {}) {
        const method = String(options.method || 'GET').toUpperCase();
        const headers = { ...(options.headers || {}) };
        const controller = new AbortController();
        const timeoutId = global.setTimeout(() => controller.abort(), 30000);
        const externalSignal = options.signal;
        const abortFromExternal = () => controller.abort();
        externalSignal?.addEventListener?.('abort', abortFromExternal, { once: true });
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            headers['X-Quant-Session'] = document.querySelector('meta[name="quant-session-token"]')?.content || '';
        }
        try {
            const response = await fetch(url, { ...options, headers, signal: controller.signal });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(payload.error || `请求失败 (${response.status})`);
                error.code = payload.code || 'REQUEST_FAILED';
                throw error;
            }
            return payload;
        } finally {
            global.clearTimeout(timeoutId);
            externalSignal?.removeEventListener?.('abort', abortFromExternal);
        }
    }

    function ensureDetails(control) {
        let details = control.root.querySelector('.domain-sync-details');
        if (!details) {
            details = document.createElement('span');
            details.className = 'domain-sync-details';
            details.setAttribute('role', 'status');
            details.setAttribute('aria-live', 'polite');
            control.root.append(details);
        }
        return details;
    }

    function detailItem(label, value) {
        if (value === undefined || value === null || value === '') return null;
        const item = document.createElement('span');
        item.className = 'domain-sync-detail';
        item.textContent = `${label} ${value}`;
        return item;
    }

    function renderDetails(control, detail = {}) {
        const details = ensureDetails(control);
        const page = detail.planCursor || (detail.offset !== undefined ? `OFFSET ${detail.offset}` : '');
        const items = [
            detailItem('JOB', detail.jobId),
            detailItem('DATASET', detail.dataset),
            detailItem('PHASE', detail.phase),
            detailItem('PAGE', page),
            detailItem('WRITTEN', detail.rowsWritten),
            detailItem('WARNING', detail.warning),
            detailItem('ERROR CODE', detail.errorCode),
            detailItem('ERROR', detail.errorText),
            detailItem('MESSAGE', detail.message),
        ].filter(Boolean);
        if (detail.jobId) {
            const diagnostic = document.createElement('button');
            diagnostic.type = 'button';
            diagnostic.className = 'text-action domain-sync-diagnostic';
            diagnostic.dataset.syncDiagnostic = detail.jobId;
            diagnostic.textContent = 'DIAGNOSTIC';
            items.push(diagnostic);
        }
        const content = items.flatMap((item, index) => (
            index ? [document.createTextNode(' · '), item] : [item]
        ));
        details.replaceChildren(...content);
    }

    function jobDetail(control, job, latestEvent, message) {
        const result = job.result || {};
        const warnings = Array.isArray(result.warnings) ? result.warnings.filter(Boolean) : [];
        return {
            jobId: job.job_id || control.jobId,
            dataset: latestEvent.dataset || latestEvent.dataset_id || result.dataset,
            phase: latestEvent.phase,
            planCursor: latestEvent.plan_cursor,
            offset: latestEvent.offset,
            rowsWritten: result.rows_written ?? latestEvent.rows_written ?? latestEvent.row_count,
            warning: latestEvent.warning || warnings.join('；') || (latestEvent.warning_count ? `${latestEvent.warning_count}` : ''),
            errorCode: latestEvent.error_code || result.error_code,
            retryable: latestEvent.retryable ?? result.retryable,
            errorText: result.error || latestEvent.error,
            message,
        };
    }

    function isPermissionDenied(...values) {
        const text = values.filter(Boolean).join(' ');
        return PERMISSION_PATTERNS.some(pattern => pattern.test(text));
    }

    function permissionMessage(detail = {}) {
        const dataset = detail.dataset ? `数据集 ${detail.dataset}` : '该数据集';
        return `${dataset} 不在当前 Tushare 权限内；保留并刷新本地缓存，不再提供无意义的重试。`;
    }

    function setState(control, status, message = '', detail = {}) {
        control.status = status;
        control.state.textContent = String(status || 'UNKNOWN').toUpperCase();
        control.root.dataset.syncStatus = status || 'unknown';
        control.root.title = message || '';
        renderDetails(control, { ...detail, message: detail.message || message });
        const active = ACTIVE.has(status);
        const failed = ['failed', 'error', 'cancelled', 'token_missing'].includes(status);
        const permissionDenied = status === 'permission_denied';
        const canRetry = failed && (status === 'cancelled' || detail.retryable !== false);
        control.start.hidden = active || permissionDenied;
        control.start.disabled = active;
        control.cancel.hidden = !active;
        control.retry.hidden = !canRetry || permissionDenied;
    }

    function stopPolling(control) {
        if (control.pollTimer) global.clearTimeout(control.pollTimer);
        control.pollTimer = null;
    }

    function defaultRequest(domain) {
        if (domain === 'futures') {
            return { datasets: ['fut_basic'], scope: 'metadata' };
        }
        return {};
    }

    function refreshWorkspace(domain) {
        const buttonId = {
            futures: 'futures-refresh',
            macro: 'macro-refresh',
            industry: 'industry-refresh',
        }[domain];
        document.getElementById(buttonId)?.click();
        global.dispatchEvent(new CustomEvent('quant:domain-sync-complete', { detail: { domain } }));
    }

    async function poll(control) {
        if (!control.jobId) return;
        try {
            const job = await requestJson(`/api/sync/status/${encodeURIComponent(control.jobId)}`);
            const result = job.result || {};
            const latestEvent = (job.events || []).at(-1) || {};
            const rawMessage = result.error || latestEvent.message || latestEvent.phase || '';
            const detail = jobDetail(control, job, latestEvent, rawMessage);
            const permissionDenied = ['failed', 'error'].includes(job.status)
                && isPermissionDenied(
                    result.error_code,
                    latestEvent.error_code,
                    result.error,
                    latestEvent.error,
                    latestEvent.message,
                    latestEvent.warning,
                );
            const displayStatus = permissionDenied ? 'permission_denied' : (job.status || 'running');
            const message = permissionDenied ? permissionMessage(detail) : rawMessage;
            control.networkFailures = 0;
            setState(control, displayStatus, message, { ...detail, message });
            if (TERMINAL.has(job.status)) {
                stopPolling(control);
                if (job.status !== 'cancelled') {
                    refreshWorkspace(control.domain);
                }
                return;
            }
        } catch (error) {
            control.networkFailures = (control.networkFailures || 0) + 1;
            if (control.networkFailures <= 3) {
                const retryDelay = 500 * (2 ** (control.networkFailures - 1));
                setState(control, 'running', `状态读取暂时失败，${retryDelay}ms 后重试`, {
                    jobId: control.jobId,
                    errorCode: error.code,
                    errorText: error.message,
                });
                control.pollTimer = global.setTimeout(() => poll(control), retryDelay);
                return;
            }
            setState(control, 'error', error.message, {
                jobId: control.jobId,
                errorCode: error.code,
                errorText: error.message,
            });
            stopPolling(control);
            return;
        }
        control.pollTimer = global.setTimeout(() => poll(control), 1000);
    }

    async function start(control, options = {}) {
        if (!control || control.starting || (control.jobId && ACTIVE.has(control.status))) return;
        control.starting = true;
        stopPolling(control);
        setState(control, 'queued', '正在创建本地同步任务');
        const request = {
            ...defaultRequest(control.domain),
            ...(options || {}),
            domain: control.domain,
        };
        control.lastRequest = request;
        try {
            const payload = await requestJson('/api/sync/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request),
            });
            control.jobId = payload.job_id;
            setState(control, payload.status || 'queued', '', { jobId: control.jobId, phase: 'queued' });
            await poll(control);
        } catch (error) {
            const missingToken = error.code === 'TOKEN_MISSING';
            setState(
                control,
                missingToken ? 'token_missing' : 'error',
                missingToken
                    ? '请设置 TUSHARE_TOKEN 或在 config/config_local.yaml 填写 tushare.token；Token 不会写入任务或安装包。'
                    : error.message,
                { errorCode: error.code, errorText: error.message },
            );
        } finally {
            control.starting = false;
        }
    }

    async function cancel(control) {
        if (!control.jobId) return;
        try {
            await requestJson(`/api/sync/cancel/${encodeURIComponent(control.jobId)}`, { method: 'POST' });
            setState(control, 'cancelling', '正在安全停止；已写入的数据会保留', {
                jobId: control.jobId,
                phase: 'cancelling',
            });
        } catch (error) {
            setState(control, 'error', error.message, {
                jobId: control.jobId,
                errorCode: error.code,
                errorText: error.message,
            });
        }
    }

    async function inspect(control) {
        try {
            const status = await requestJson(`/api/domain-status/${encodeURIComponent(control.domain)}`);
            setState(control, status.state || 'empty', `本地仓库 ${status.storage_bytes || 0} bytes`, {
                message: `LOCAL ${status.storage_bytes || 0} BYTES`,
            });
        } catch (error) {
            setState(control, 'error', error.message, {
                errorCode: error.code,
                errorText: error.message,
            });
        }
    }

    function openDiagnostics(event) {
        const trigger = event.target.closest('[data-sync-diagnostic]');
        if (!trigger) return;
        const jobId = trigger.dataset.syncDiagnostic;
        if (global.quantSystemWorkspace) global.quantSystemWorkspace.openJob(jobId);
        global.location.hash = '#/system';
    }

    function updateEquityVisibility() {
        const control = controls.get('hong_kong');
        if (!control) return;
        control.root.hidden = global.quantMarketContext?.currentMarket?.() !== 'hong_kong';
    }

    function mount() {
        document.querySelectorAll('[data-sync-domain]').forEach(root => {
            const domain = root.dataset.syncDomain;
            const control = {
                domain,
                root,
                state: root.querySelector('.domain-sync-state'),
                start: root.querySelector('.domain-sync-start'),
                cancel: root.querySelector('.domain-sync-cancel'),
                retry: root.querySelector('.domain-sync-retry'),
                jobId: null,
                pollTimer: null,
                status: 'empty',
                starting: false,
            };
            controls.set(domain, control);
            control.start?.addEventListener('click', () => start(control));
            control.retry?.addEventListener('click', () => start(control, {
                ...(control.lastRequest || {}),
                force: true,
            }));
            control.cancel?.addEventListener('click', () => cancel(control));
            control.root.addEventListener('click', openDiagnostics);
            inspect(control);
        });
        updateEquityVisibility();
        global.addEventListener('quant:market-change', updateEquityVisibility);
        global.addEventListener('quant:market-render', updateEquityVisibility);
        global.addEventListener('beforeunload', () => controls.forEach(stopPolling));
    }

    document.addEventListener('DOMContentLoaded', mount);
    global.quantDomainSync = Object.freeze({
        start: (domain, options = {}) => start(controls.get(domain), options),
    });
})(window);
