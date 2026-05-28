"""Tencent qfq HTTP data provider."""

from __future__ import annotations

from utils.akshare_fetcher import AKShareFetcher, DEFAULT_STOCK_LIST


class TencentFetcher(AKShareFetcher):
    """Explicit Tencent provider using fqkline HTTP price data."""

    provider_name = "tencent"

    def get_market_caps(self, stock_codes):
        """Fetch market caps from Tencent quote data for the Tencent warehouse."""
        return self._fetch_market_cap_tencent(stock_codes)

    def _get_realtime_market_cap(self, stock_code):
        market_caps = self._fetch_market_cap_tencent([stock_code])
        return market_caps.get(stock_code)

    def get_all_stock_codes(self, max_retries=3):
        print("正在通过腾讯接口获取A股股票列表...")
        local_stocks = self._load_local_stock_names()
        if len(local_stocks) >= 3000:
            print(f"✓ 从腾讯仓本地缓存加载: {len(local_stocks)} 只股票")
            return local_stocks

        neutral_path = self.storage_root_dir / "stock_names.json"
        neutral_stocks = self._read_stock_names_file(neutral_path)
        if len(neutral_stocks) >= 3000:
            self._save_stock_names(neutral_stocks)
            print(f"✓ 从本地中性股票池缓存加载: {len(neutral_stocks)} 只股票 ({neutral_path})")
            return neutral_stocks

        for attempt in range(max_retries):
            try:
                stocks = self._fetch_stock_list_http()
                if stocks:
                    filtered = self._filter_a_share_stock_dict(stocks)
                    if len(filtered) >= 3000:
                        print(f"✓ 腾讯接口获取成功: {len(filtered)} 只A股股票")
                        self._save_stock_names(filtered)
                        return filtered
                    print(f"  腾讯股票池结果不完整: {len(filtered)} 只")
            except Exception as exc:
                print(f"  腾讯股票列表失败 (第{attempt + 1}/{max_retries}次): {exc}")

        bootstrap_stocks, bootstrap_path = self._load_shared_stock_names()
        if bootstrap_stocks:
            if self.stock_names_file != bootstrap_path:
                self._save_stock_names(bootstrap_stocks)
            print(f"✓ 腾讯原生股票池不可用，临时从其他本地股票池缓存加载: {len(bootstrap_stocks)} 只股票 ({bootstrap_path})")
            return bootstrap_stocks

        local_stocks = self._load_local_stock_names()
        if local_stocks:
            print(f"✓ 从腾讯仓本地缓存加载: {len(local_stocks)} 只股票")
            return local_stocks
        return DEFAULT_STOCK_LIST.copy()

    def fetch_stock_history(self, stock_code, years=6):
        df = self._fetch_stock_history_http(stock_code, years=years)
        if df is None or df.empty:
            return None
        return self._mark_data_source(df, 'tencent:fqkline')

    def fetch_stock_update(self, stock_code, days=10):
        df = self._fetch_stock_update_http(stock_code, days=days)
        if df is None or df.empty:
            return None
        return self._mark_data_source(df, 'tencent:fqkline:update')
