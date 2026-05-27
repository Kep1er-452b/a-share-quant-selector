"""Tencent qfq HTTP data provider."""

from __future__ import annotations

import pandas as pd

from utils.akshare_fetcher import AKShareFetcher, DEFAULT_STOCK_LIST


class TencentFetcher(AKShareFetcher):
    """Explicit Tencent provider using fqkline HTTP price data."""

    provider_name = "tencent"

    def get_all_stock_codes(self, max_retries=3):
        print("正在通过腾讯接口获取A股股票列表...")
        for attempt in range(max_retries):
            try:
                stocks = self._fetch_stock_list_http()
                if stocks:
                    filtered = {}
                    code_pattern = r'^(00|30|60|68|88)\d{4}$'
                    exclude_keywords = ['债', '基', 'ETF', 'LOF', '基金', '理财', '信托', 'B股', '指数', '国债', '企债', '转债', '回购', 'R-', 'GC']
                    for code, name in stocks.items():
                        if not pd.Series([code]).str.match(code_pattern).iloc[0]:
                            continue
                        if any(keyword in name for keyword in exclude_keywords):
                            continue
                        filtered[code] = name
                    if filtered:
                        print(f"✓ 腾讯接口获取成功: {len(filtered)} 只A股股票")
                        self._save_stock_names(filtered)
                        return filtered
            except Exception as exc:
                print(f"  腾讯股票列表失败 (第{attempt + 1}/{max_retries}次): {exc}")

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
