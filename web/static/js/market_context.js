'use strict';

(function initMarketContext(global) {
    const STORAGE_KEY = 'quantMarketContext';
    const SUPPORTED = new Set(['a_share', 'hong_kong']);
    let capabilities = null;
    let current = loadStoredMarket();

    function loadStoredMarket() {
        try {
            const value = JSON.parse(global.localStorage.getItem(STORAGE_KEY) || 'null');
            return value?.version === 1 && SUPPORTED.has(value.market) ? value.market : 'a_share';
        } catch (_error) {
            return 'a_share';
        }
    }

    function persist() {
        try {
            global.localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, market: current }));
        } catch (_error) {
            // A private/locked profile may reject storage; in-memory context still works.
        }
    }

    function render() {
        document.querySelectorAll('#equity-market-switch [data-market]').forEach(button => {
            const active = button.dataset.market === current;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
        document.documentElement.dataset.market = current;
    }

    function setMarket(marketId, options = {}) {
        if (!SUPPORTED.has(marketId) || (capabilities && !capabilities[marketId])) {
            throw new Error(`Unsupported market: ${marketId}`);
        }
        const previous = current;
        current = marketId;
        persist();
        render();
        global.dispatchEvent(new CustomEvent('quant:market-render', {
            detail: { market: current, previous },
        }));
        if (previous !== current && !options.silent) {
            global.dispatchEvent(new CustomEvent('quant:market-change', {
                detail: { market: current, previous },
            }));
        }
        return current;
    }

    async function validateCapabilities() {
        try {
            const response = await fetch('/api/markets/capabilities');
            if (!response.ok) return;
            const payload = await response.json();
            capabilities = payload.markets || null;
            if (capabilities && !capabilities[current]) {
                setMarket(payload.default_market || 'a_share', { silent: true });
            }
        } catch (_error) {
            // Offline startup keeps the last validated local market.
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        render();
        document.getElementById('equity-market-switch')?.addEventListener('click', event => {
            const button = event.target.closest('[data-market]');
            if (button) setMarket(button.dataset.market);
        });
        validateCapabilities();
    });

    global.quantMarketContext = Object.freeze({
        currentMarket: () => current,
        setMarket,
        capabilities: () => capabilities,
    });
})(window);
