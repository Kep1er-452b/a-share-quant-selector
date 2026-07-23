'use strict';

(function initEquityRouter(global) {
    const SOURCE_STATE_KEY = 'quantEquityRouteSource';

    function routeFor(market, symbol) {
        return `#/equities/${encodeURIComponent(market)}/instrument/${encodeURIComponent(symbol)}`;
    }

    function marketRouteFor(market) {
        return `#/equities/${encodeURIComponent(market)}`;
    }

    function parseMarketRoute(hash = global.location.hash) {
        const match = String(hash || '').match(/^#\/equities\/(a_share|hong_kong)\/?$/);
        return match ? match[1] : null;
    }

    function parseInstrumentRoute(hash = global.location.hash) {
        const match = String(hash || '').match(/^#\/equities\/(a_share|hong_kong)\/instrument\/([^/?#]+)$/);
        if (!match) return null;
        try {
            return { market: match[1], symbol: decodeURIComponent(match[2]).toUpperCase() };
        } catch (_error) {
            return null;
        }
    }

    function openInstrument(market, symbol, sourceState = null) {
        const cleanSymbol = String(symbol || '').trim().toUpperCase();
        if (!cleanSymbol) throw new Error('Instrument symbol is required');
        if (sourceState) {
            global.sessionStorage.setItem(SOURCE_STATE_KEY, JSON.stringify(sourceState));
        }
        const routeState = { market, symbol: cleanSymbol, sourceState: sourceState || null };
        global.history.pushState(routeState, '', routeFor(market, cleanSymbol));
        global.dispatchEvent(new CustomEvent('quant:open-instrument', {
            detail: {
                market,
                symbol: market === 'a_share' ? cleanSymbol.split('.', 1)[0] : cleanSymbol,
                canonicalSymbol: cleanSymbol,
            },
        }));
    }

    function restoreSourceState() {
        try {
            const value = JSON.parse(global.sessionStorage.getItem(SOURCE_STATE_KEY) || 'null');
            global.sessionStorage.removeItem(SOURCE_STATE_KEY);
            return value;
        } catch (_error) {
            return null;
        }
    }

    global.quantEquityRouter = Object.freeze({
        openInstrument,
        restoreSourceState,
        routeFor,
        marketRouteFor,
        parseMarketRoute,
        parseInstrumentRoute,
    });
})(window);
