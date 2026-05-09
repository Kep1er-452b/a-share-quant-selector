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
                "未找到 Tushare Token。请先设置环境变量 TUSHARE_TOKEN，或在 config/config_local.yaml / "
                "config/config.yaml 中配置 data_source.tushare.token，或在交互模式下输入 token。"
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
        self.daily_basic_by_date_cache = {}
        self.daily_basic_by_date_failures = set()
        self.daily_basic_date_cache_max_days = 40
        self.daily_basic_api_calls = 0
        self.daily_basic_cache_hits = 0
        self._latest_trade_date_cache = None
        self._latest_trade_date_daily_basic_cache = pd.DataFrame()
        self.trade_calendar_cache_file = Path(data_dir) / "trade_calendar_cache.json"
        self.trade_calendar_seed_file = Path(__file__).resolve().parent.parent / "config" / "trade_calendar_seed_2026.json"
        self.trade_calendar_cache = {}
        self.trade_calendar_range_cache = {}
        self._trade_calendar_warning_emitted = False
        self._load_trade_calendar_cache()

    def _load_trade_calendar_cache(self):
        merged_cache = {"years": {}, "updated_at": None}

        for path in [self.trade_calendar_seed_file, self.trade_calendar_cache_file]:
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f) or {}
                years = payload.get("years", {})
                for year, info in years.items():
                    merged_cache["years"][str(year)] = info
                updated_at = payload.get("updated_at")
                if updated_at:
                    merged_cache["updated_at"] = updated_at
            except Exception:
                continue

        self.trade_calendar_cache = merged_cache

    def _save_trade_calendar_cache(self):
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "years": self.trade_calendar_cache.get("years", {}),
        }
        try:
            with open(self.trade_calendar_cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存交易日历缓存失败: {e}")

    def _calendar_latest_cached_date(self):
        latest_date = None
        for info in self.trade_calendar_cache.get("years", {}).values():
            open_dates = info.get("open_dates", [])
            if not open_dates:
                continue
            year_latest = max(open_dates)
            if latest_date is None or year_latest > latest_date:
                latest_date = year_latest
        return latest_date

    def _get_cached_trade_dates_between(self, start_date, end_date):
        start = pd.to_datetime(start_date).date() if start_date else None
        end = pd.to_datetime(end_date).date() if end_date else None
        if not start or not end or start > end:
            return []

        result = []
        for year in range(start.year, end.year + 1):
            info = self.trade_calendar_cache.get("years", {}).get(str(year), {})
            for date_text in info.get("open_dates", []):
                date_value = pd.to_datetime(date_text).date()
                if start <= date_value <= end:
                    result.append(date_value)
        return sorted(set(result))

    def _warn_trade_calendar_fallback(self, reason, used_cache=True):
        if self._trade_calendar_warning_emitted:
            return
        self._trade_calendar_warning_emitted = True
        latest_cached_date = self._calendar_latest_cached_date() or "无缓存"
        fallback_mode = "本地交易日历缓存" if used_cache else "本地工作日近似判断"
        print(
            f"  trade_cal 无响应，将使用{fallback_mode}。"
            f"请确保本地交易日历缓存已为最新。"
            f"当前日历缓存截至: {latest_cached_date}。"
            f"原因: {reason}"
        )
        print("  可执行 `python3 main.py calendar --provider tushare --update --years 2026` 更新日历缓存。")

    def _fetch_trade_calendar_year(self, year: int):
        df = self.pro.trade_cal(
            exchange="",
            start_date=f"{year}0101",
            end_date=f"{year}1231",
            fields="cal_date,is_open,pretrade_date",
        )
        if df is None or df.empty or not {"cal_date", "is_open"}.issubset(df.columns):
            raise DataProviderError(f"Tushare trade_cal 返回空数据: {year}")

        open_days = (
            df[df["is_open"].astype(int) == 1]["cal_date"]
            .astype(str)
            .sort_values()
            .tolist()
        )
        if not open_days:
            raise DataProviderError(f"Tushare trade_cal 未返回开市日: {year}")

        return {
            "year": str(year),
            "source": "tushare",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "open_dates": open_days,
        }

    def get_trade_calendar_status(self) -> dict:
        years = sorted(self.trade_calendar_cache.get("years", {}).keys())
        return {
            "provider": self.provider_name,
            "cache_available": bool(years),
            "latest_cached_date": self._calendar_latest_cached_date(),
            "years": years,
            "source": "cache" if years else "fallback",
        }

    def update_trade_calendar_cache(self, years=None) -> dict:
        if not years:
            current_year = datetime.now().year
            years = [current_year]

        normalized_years = sorted({int(year) for year in years})
        updated_years = {}
        for year in normalized_years:
            updated_years[str(year)] = self._fetch_trade_calendar_year(year)

        current_years = self.trade_calendar_cache.setdefault("years", {})
        current_years.update(updated_years)
        self.trade_calendar_cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_trade_calendar_cache()
        self._trade_calendar_warning_emitted = False
        return self.get_trade_calendar_status()

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
        if self._latest_trade_date_cache:
            return self._latest_trade_date_cache, self._latest_trade_date_daily_basic_cache.copy()

        for delta in range(max_lookback_days):
            trade_date = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                df = self._fetch_daily_basic_trade_date(trade_date)
                if df is not None and not df.empty:
                    self._latest_trade_date_cache = trade_date
                    self._latest_trade_date_daily_basic_cache = df.copy()
                    return trade_date, df
            except Exception:
                continue
        return None, pd.DataFrame()

    def get_latest_trade_date(self):
        trade_date, _ = self._get_latest_trade_date()
        if trade_date:
            return pd.to_datetime(trade_date).date()
        return super().get_latest_trade_date()

    def get_trade_dates_between(self, start_date, end_date):
        start = pd.to_datetime(start_date).date() if start_date else None
        end = pd.to_datetime(end_date).date() if end_date else None
        if not start or not end or start > end:
            return []

        cache_key = (start.isoformat(), end.isoformat())
        if cache_key in self.trade_calendar_range_cache:
            return self.trade_calendar_range_cache[cache_key]

        try:
            df = self.pro.trade_cal(
                exchange="",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="cal_date,is_open",
            )
            if df is not None and not df.empty and {"cal_date", "is_open"}.issubset(df.columns):
                open_days = df[df["is_open"].astype(int) == 1]["cal_date"]
                trade_dates = list(pd.to_datetime(open_days).dt.date)
                self.trade_calendar_range_cache[cache_key] = trade_dates
                return trade_dates
        except Exception as e:
            cached_trade_dates = self._get_cached_trade_dates_between(start, end)
            if cached_trade_dates:
                self.trade_calendar_range_cache[cache_key] = cached_trade_dates
                self._warn_trade_calendar_fallback(str(e), used_cache=True)
                return cached_trade_dates

            trade_dates = super().get_trade_dates_between(start, end)
            self.trade_calendar_range_cache[cache_key] = trade_dates
            self._warn_trade_calendar_fallback(str(e), used_cache=False)
            return trade_dates

        cached_trade_dates = self._get_cached_trade_dates_between(start, end)
        if cached_trade_dates:
            self.trade_calendar_range_cache[cache_key] = cached_trade_dates
            return cached_trade_dates

        trade_dates = super().get_trade_dates_between(start, end)
        self.trade_calendar_range_cache[cache_key] = trade_dates
        return trade_dates

    def _resolve_update_start_date(self, days: int, end_date):
        """
        按交易日回推增量抓取起点，而不是简单按自然日回退。
        """
        end = pd.to_datetime(end_date).date() if end_date else datetime.now().date()
        lookback_days = max(days * 4 + 10, 20)
        calendar_start = end - timedelta(days=lookback_days)
        trade_dates = self.get_trade_dates_between(calendar_start, end)

        if trade_dates:
            window_size = max(days + 1, 2)  # 额外带上 1 个交易日作缓冲，便于去重合并
            start_index = max(len(trade_dates) - window_size, 0)
            return trade_dates[start_index]

        return end - timedelta(days=max(days + 5, 10))

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
        self.daily_basic_api_calls += 1
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
    def _trade_date_key(trade_date) -> str:
        return pd.to_datetime(trade_date).strftime("%Y%m%d")

    def _fetch_daily_basic_trade_date(self, trade_date):
        date_key = self._trade_date_key(trade_date)
        if date_key in self.daily_basic_by_date_cache:
            self.daily_basic_cache_hits += 1
            return self.daily_basic_by_date_cache[date_key].copy()

        last_error = None
        for attempt in range(4):
            try:
                df = self._call_daily_basic(
                    trade_date=date_key,
                    fields="ts_code,trade_date,turnover_rate,total_mv"
                )
                if not isinstance(df, pd.DataFrame):
                    df = pd.DataFrame()
                self.daily_basic_by_date_cache[date_key] = df.copy()
                return df
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

        self.daily_basic_by_date_failures.add(date_key)
        if last_error is not None:
            print(f"  获取 {date_key} daily_basic 失败: {last_error}")
        return pd.DataFrame()

    def _fetch_daily_basic_from_trade_date_cache(self, ts_code: str, start_date: str, end_date: str):
        start = pd.to_datetime(start_date).date()
        end = pd.to_datetime(end_date).date()
        trade_dates = self.get_trade_dates_between(start, end)
        if not trade_dates:
            return pd.DataFrame(columns=["ts_code", "trade_date", "turnover_rate", "total_mv"])
        if len(trade_dates) > self.daily_basic_date_cache_max_days:
            return None

        frames = []
        for trade_date in trade_dates:
            date_key = self._trade_date_key(trade_date)
            date_df = self._fetch_daily_basic_trade_date(date_key)
            if date_key in self.daily_basic_by_date_failures:
                return None
            if date_df.empty or "ts_code" not in date_df.columns:
                continue
            stock_df = date_df[date_df["ts_code"].astype(str) == ts_code]
            if not stock_df.empty:
                frames.append(stock_df)

        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame(columns=["ts_code", "trade_date", "turnover_rate", "total_mv"])

    def prefetch_incremental_aux_data(self, trade_dates) -> None:
        normalized_dates = sorted({
            pd.to_datetime(trade_date).date()
            for trade_date in trade_dates
            if trade_date is not None
        })
        if not normalized_dates:
            return

        end_date = max(normalized_dates)
        start_probe = min(normalized_dates) - timedelta(days=max(len(normalized_dates) * 4 + 10, 20))
        window_dates = self.get_trade_dates_between(start_probe, end_date)
        target_count = min(max(len(normalized_dates) + 1, 2), len(window_dates))
        target_dates = window_dates[-target_count:] if window_dates else normalized_dates

        if len(target_dates) > self.daily_basic_date_cache_max_days:
            print(
                f"  Tushare daily_basic 跨度 {len(target_dates)} 个交易日，"
                "保守回退为按股票查询。"
            )
            return

        missing_dates = [
            trade_date for trade_date in target_dates
            if self._trade_date_key(trade_date) not in self.daily_basic_by_date_cache
        ]
        if not missing_dates:
            return

        print(
            f"  Tushare 优化: 预取 daily_basic {len(missing_dates)} 个交易日，"
            "避免全市场按股票重复请求。"
        )
        for trade_date in missing_dates:
            self._fetch_daily_basic_trade_date(trade_date)

    def get_runtime_stats(self) -> dict:
        return {
            "daily_basic实际请求": self.daily_basic_api_calls,
            "daily_basic缓存命中": self.daily_basic_cache_hits,
            "daily_basic缓存交易日": len(self.daily_basic_by_date_cache),
        }

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
        end_date = self.get_latest_trade_date() or datetime.now().date()
        start_date = self._resolve_update_start_date(days, end_date)
        start_str = start_date.strftime("%Y%m%d")
        end_str = pd.to_datetime(end_date).strftime("%Y%m%d")

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

            basic_df = self._fetch_daily_basic_from_trade_date_cache(ts_code, start_str, end_str)
            if basic_df is None:
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
