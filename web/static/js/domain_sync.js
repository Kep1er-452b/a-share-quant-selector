'use strict';

(function initDomainSync(global) {
    const TERMINAL = new Set(['completed', 'completed_with_warnings', 'failed', 'error', 'cancelled']);
    const ACTIVE = new Set(['queued', 'running', 'cancelling']);
    const controls = new Map();

    async function requestJson(url, options = {}) {
        const method = String(options.method || 'GET').toUpperCase();
        const headers = { ...(options.headers || {}) };
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            headers['X-Quant-Session'] = document.querySelector('meta[name="quant-session-token"]')?.content || '';
        }
        const response = await fetch(url, { ...options, headers });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.error || `请求失败 (${response.status})`);
            error.code = payload.code || 'REQUEST_FAILED';
            throw error;
        }
        return payload;
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
            errorText: result.error || latestEvent.error,
            message,
        };
    }

    function setState(control, status, message = '', detail = {}) {
        control.status = status;
        control.state.textContent = String(status || 'UNKNOWN').toUpperCase();
        control.root.dataset.syncStatus = status || 'unknown';
        control.root.title = message || '';
        renderDetails(control, { ...detail, message: detail.message || message });
        const active = ACTIVE.has(status);
        const failed = ['failed', 'error', 'cancelled', 'token_missing'].includes(status);
        control.start.hidden = active;
        control.start.disabled = active;
        control.cancel.hidden = !active;
        control.retry.hidden = !failed;
    }

    function stopPolling(control) {
        if (control.pollTimer) global.clearTimeout(control.pollTimer);
        control.pollTimer = null;
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
            const message = result.error || latestEvent.message || latestEvent.phase || '';
            setState(control, job.status || 'running', message, jobDetail(control, job, latestEvent, message));
            if (TERMINAL.has(job.status)) {
                stopPolling(control);
                if (job.status === 'completed' || job.status === 'completed_with_warnings') {
                    refreshWorkspace(control.domain);
                }
                return;
            }
        } catch (error) {
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

    async function start(control, force = false) {
        stopPolling(control);
        setState(control, 'queued', '正在创建本地同步任务');
        try {
            const payload = await requestJson('/api/sync/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ domain: control.domain, force }),
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
            };
            controls.set(domain, control);
            control.start?.addEventListener('click', () => start(control, false));
            control.retry?.addEventListener('click', () => start(control, true));
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
    global.quantDomainSync = Object.freeze({ start: domain => start(controls.get(domain), false) });
})(window);
