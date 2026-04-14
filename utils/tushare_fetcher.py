"""
A股数据抓取模块 - 使用 tushare
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from utils.data_provider import BaseDataProvider, DataProviderError


class TushareFetcher(BaseDataProvider):
    """Tushare 数据抓取器"""

    provider_name = "tushare"

    def __init__(self, data_dir="data", token=None):
        super().__init__(data_dir)
        self.token = (token or "").strip()
        if not self.token:
            raise DataProviderError(
                "未找到 Tushare Token。请先设置环境变量 TUSHARE_TOKEN，或在 config/config.yaml 中配置 "
                "data_source.tushare.token，或在交互模式下输入 token。"
            )

        try:
            import tushare as ts
        except ImportError as exc:
            raise DataProviderError("未安装 tushare，请先执行 `pip install -r requirements.txt`。") from exc

        self.ts = ts
        self.ts.set_token(self.token)
        self.pro = self.ts.pro_api(self.token)
        self.stock_meta_file = Path(data_dir) / "tushare_stock_map.json"
        self.daily_basic_calls = deque()
        self.daily_basic_limit_per_minute = 180
        self.daily_basic_rate_limit_wait = 62

    def _load_stock_metadata(self):
        if self.stock_meta_file.exists():
            try:
                with open(self.stock_meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_stock_metadata(self, stock_map):
        try:
            with open(self.stock_meta_file, "w", encoding="utf-8") as f:
                json.dump(stock_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存 Tushare 股票映射失败: {e}")

    def get_stock_universe(self, max_retries=3):
        metadata = self._load_stock_metadata()
        if not metadata:
            self.get_all_stock_codes(max_retries=max_retries)
            metadata = self._load_stock_metadata()

        if metadata:
            universe = []
            for code, info in sorted(metadata.items()):
                universe.append({
                    "code": str(code).zfill(6),
                    "name": info.get("name", ""),
                    "board": self.classify_board(code, info),
                    "market": info.get("market"),
                    "exchange": info.get("exchange"),
                    "ts_code": info.get("ts_code"),
                })
            return universe

        return super().get_stock_universe(max_retries=max_retries)

    def _to_ts_code(self, stock_code: str) -> str:
        stock_code = str(stock_code).zfill(6)
        metadata = self._load_stock_metadata()
        if stock_code in metadata and metadata[stock_code].get("ts_code"):
            return metadata[stock_code]["ts_code"]

        if stock_code.startswith(("60", "68", "88")):
            return f"{stock_code}.SH"
        return f"{stock_code}.SZ"

    def _get_latest_trade_date(self, max_lookback_days=10):
        """获取最近一个可用交易日"""
        for delta in range(max_lookback_days):
            trade_date = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                df = self._call_daily_basic(
                    trade_date=trade_date,
                    fields="ts_code,trade_date,total_mv"
                )
                if df is not None and not df.empty:
                    return trade_date, df
            except Exception:
                continue
        return None, pd.DataFrame()

    def get_latest_trade_date(self):
        trade_date, _ = self._get_latest_trade_date()
        if trade_date:
            return pd.to_datetime(trade_date).date()
        return super().get_latest_trade_date()

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        message = str(error)
        rate_limit_keywords = [
            "每分钟最多访问该接口",
            "抱歉，您每分钟最多访问该接口",
            "rate limit",
            "too many requests",
        ]
        return any(keyword in message.lower() if keyword.isascii() else keyword in message for keyword in rate_limit_keywords)

    def _throttle_daily_basic(self):
        now = time.time()
        while self.daily_basic_calls and now - self.daily_basic_calls[0] >= 60:
            self.daily_basic_calls.popleft()

        if len(self.daily_basic_calls) >= self.daily_basic_limit_per_minute:
            wait_seconds = 60 - (now - self.daily_basic_calls[0]) + 0.5
            wait_seconds = max(wait_seconds, 0.5)
            print(f"  daily_basic 接口接近限流，等待 {wait_seconds:.1f} 秒后继续...")
            time.sleep(wait_seconds)
            now = time.time()
            while self.daily_basic_calls and now - self.daily_basic_calls[0] >= 60:
                self.daily_basic_calls.popleft()

        self.daily_basic_calls.append(time.time())

    def _call_daily_basic(self, **kwargs):
        self._throttle_daily_basic()
        return self.pro.daily_basic(**kwargs)

    def _fetch_daily_basic_range(self, ts_code: str, start_date: str, end_date: str):
        last_error = None
        for attempt in range(4):
            try:
                df = self._call_daily_basic(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                    fields="ts_code,trade_date,turnover_rate,total_mv"
                )
                if isinstance(df, pd.DataFrame):
                    return df
                return pd.DataFrame()
            except Exception as e:
                last_error = e
                if self._is_rate_limit_error(e) and attempt < 3:
                    wait_seconds = self.daily_basic_rate_limit_wait
                    print(f"  daily_basic 命中限流，等待 {wait_seconds} 秒后重试...")
                    time.sleep(wait_seconds)
                    self.daily_basic_calls.clear()
                    continue
                if attempt < 3:
                    time.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            print(f"  获取 daily_basic 失败: {last_error}")
        return pd.DataFrame()

    @staticmethod
    def _numeric_column(frame: pd.DataFrame, column_name: str, default=0):
        if column_name in frame.columns:
            return pd.to_numeric(frame[column_name], errors="coerce").fillna(default)
        return pd.Series([default] * len(frame), index=frame.index)

    def _prepare_price_dataframe(self, price_df: pd.DataFrame):
        if price_df is None or not isinstance(price_df, pd.DataFrame) or price_df.empty:
            return None

        result = price_df.copy()
        required_columns = {"trade_date", "open", "high", "low", "close"}
        missing_columns = sorted(required_columns - set(result.columns))
        if missing_columns:
            print(f"  行情数据缺少字段: {', '.join(missing_columns)}，跳过该股票")
            return None

        result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
        result = result.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
        if result.empty:
            return None

        if "vol" not in result.columns and "volume" not in result.columns:
            result["vol"] = 0
        if "amount" not in result.columns:
            result["amount"] = 0

        return result

    def _prepare_basic_dataframe(self, basic_df: pd.DataFrame):
        if basic_df is None or not isinstance(basic_df, pd.DataFrame) or basic_df.empty:
            return pd.DataFrame(columns=["trade_date", "turnover_rate", "total_mv"])

        result = basic_df.copy()
        if "trade_date" not in result.columns:
            print("  daily_basic 缺少 trade_date，已跳过换手率/市值合并")
            return pd.DataFrame(columns=["trade_date", "turnover_rate", "total_mv"])

        for column in ("turnover_rate", "total_mv"):
            if column not in result.columns:
                result[column] = pd.NA

        result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
        result = result.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
        return result[["trade_date", "turnover_rate", "total_mv"]]

    def _normalize_history_dataframe(self, price_df: pd.DataFrame, basic_df: pd.DataFrame):
        result = self._prepare_price_dataframe(price_df)
        if result is None or result.empty:
            return None

        basic_df = self._prepare_basic_dataframe(basic_df)
        if not basic_df.empty:
            result = result.merge(
                basic_df,
                on="trade_date",
                how="left"
            )
        else:
            result["turnover_rate"] = np.nan
            result["total_mv"] = np.nan

        result = result.rename(
            columns={
                "trade_date": "date",
                "turnover_rate": "turnover",
                "total_mv": "market_cap",
            }
        )

        # Tushare amount 单位为千元，统一转换为元；total_mv 单位为万元，统一转换为元
        result["amount"] = self._numeric_column(result, "amount") * 1000
        result["market_cap"] = self._numeric_column(result, "market_cap", default=np.nan) * 10000
        result["turnover"] = self._numeric_column(result, "turnover", default=np.nan)
        result["volume"] = self._numeric_column(result, "vol") if "vol" in result.columns else self._numeric_column(result, "volume")

        keep_columns = ["date", "open", "high", "low", "close", "volume", "amount", "turnover", "market_cap"]
        result = result[keep_columns]
        result["date"] = pd.to_datetime(result["date"])
        result = result.sort_values("date", ascending=False)
        return result

    def get_all_stock_codes(self, max_retries=3):
        """获取所有上市 A 股股票代码"""
        print("正在通过 Tushare 获取A股股票列表...")

        exclude_keywords = ["债", "ETF", "LOF", "基金", "理财", "信托", "B股", "指数", "转债"]
        code_pattern = re.compile(r"^(00|30|60|68)\d{4}$")

        for attempt in range(max_retries):
            try:
                df = self.pro.stock_basic(
                    exchange="",
                    list_status="L",
                    fields="ts_code,symbol,name,market,exchange,list_date"
                )

                if df is None or df.empty:
                    raise DataProviderError("Tushare 返回空股票列表")

                df["symbol"] = df["symbol"].astype(str).str.zfill(6)
                df = df[df["exchange"].isin(["SSE", "SZSE"])]
                df = df[df["symbol"].str.match(code_pattern)]
                for keyword in exclude_keywords:
                    df = df[~df["name"].str.contains(keyword, na=False)]

                stock_dict = dict(zip(df["symbol"], df["name"]))
                stock_map = {
                    row["symbol"]: {
                        "ts_code": row["ts_code"],
                        "name": row["name"],
                        "exchange": row["exchange"],
                        "market": row["market"],
                        "list_date": row["list_date"],
                    }
                    for _, row in df.iterrows()
                }

                self._save_stock_names(stock_dict)
                self._save_stock_metadata(stock_map)
                print(f"✓ Tushare 获取成功: {len(stock_dict)} 只A股股票")
                return stock_dict
            except Exception as e:
                print(f"  Tushare 获取股票列表失败 (第{attempt + 1}/{max_retries}次): {e}")

        local_stocks = self._load_local_stock_names()
        if local_stocks:
            print(f"✓ 从本地缓存加载: {len(local_stocks)} 只股票")
            return local_stocks

        raise DataProviderError("Tushare 无法获取股票列表，且本地缓存不存在。")

    def fetch_stock_history(self, stock_code, years=6):
        """
        抓取单只股票历史数据
        前复权，按日期倒序排列
        """
        ts_code = self._to_ts_code(stock_code)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365 * years)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        try:
            price_df = self.ts.pro_bar(
                ts_code=ts_code,
                asset="E",
                freq="D",
                adj="qfq",
                start_date=start_str,
                end_date=end_str
            )
            if price_df is None or price_df.empty:
                return None

            basic_df = self._fetch_daily_basic_range(ts_code, start_str, end_str)
            return self._normalize_history_dataframe(price_df, basic_df)
        except Exception as e:
            print(f"  Tushare 获取历史数据失败: {e}")
            return None

    def fetch_stock_update(self, stock_code, days=10):
        """
        抓取近期数据用于增量更新
        """
        ts_code = self._to_ts_code(stock_code)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(days + 5, 10))
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        try:
            price_df = self.ts.pro_bar(
                ts_code=ts_code,
                asset="E",
                freq="D",
                adj="qfq",
                start_date=start_str,
                end_date=end_str
            )
            if price_df is None or price_df.empty:
                return None

            basic_df = self._fetch_daily_basic_range(ts_code, start_str, end_str)
            return self._normalize_history_dataframe(price_df, basic_df)
        except Exception as e:
            print(f"  Tushare 获取增量数据失败: {e}")
            return None

    def get_market_caps(self, stock_codes):
        """
        批量获取最新总市值
        """
        trade_date, daily_basic_df = self._get_latest_trade_date()
        if not trade_date or daily_basic_df.empty:
            return {}

        if "total_mv" not in daily_basic_df.columns:
            return {}

        symbols = {str(code).zfill(6) for code in stock_codes}
        market_caps = {}
        for _, row in daily_basic_df.iterrows():
            ts_code = str(row["ts_code"])
            symbol = ts_code.split(".")[0]
            if symbol not in symbols:
                continue
            total_mv = pd.to_numeric(row.get("total_mv", 0), errors="coerce")
            if pd.notna(total_mv) and total_mv > 0:
                market_caps[symbol] = int(float(total_mv) * 10000)
        return market_caps
