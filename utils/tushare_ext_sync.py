"""Tushare extension dataset synchronization helpers."""

from __future__ import annotations

import time
import random
from collections import deque
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Callable, Iterable

import pandas as pd

from utils.tushare_ext_store import TushareExtStore


DEFAULT_INDEX_SYMBOLS = ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000300.SH")
INDEX_CACHE_HISTORY_DAYS = 2200
INDEX_CACHE_MIN_ROWS = 1300
BASIC_ENDPOINTS = {
    "stock_basic": {
        "method": "stock_basic",
        "key_fields": ("ts_code",),
        "params": {
            "exchange": "",
            "list_status": "L",
            "fields": "ts_code,symbol,name,area,industry,market,exchange,list_date",
        },
    },
    "namechange": {
        "method": "namechange",
        "key_fields": ("ts_code", "name", "start_date"),
        "params": {"fields": "ts_code,name,start_date,end_date,change_reason"},
        "trade_date_field": "start_date",
    },
}
VALUATION_ENDPOINTS = {
    "daily_basic": {
        "method": "daily_basic",
        "key_fields": ("ts_code", "trade_date"),
        "params": {
            "fields": (
                "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
                "pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,"
                "free_share,total_mv,circ_mv"
            )
        },
    },
}
PRICE_ENDPOINTS = {
    "daily": {"method": "daily", "key_fields": ("ts_code", "trade_date")},
    "weekly": {"method": "weekly", "key_fields": ("ts_code", "trade_date")},
    "monthly": {"method": "monthly", "key_fields": ("ts_code", "trade_date")},
    "adj_factor": {"method": "adj_factor", "key_fields": ("ts_code", "trade_date")},
}
FINANCIAL_ENDPOINTS = {
    "income": {"method": "income", "key_fields": ("ts_code", "end_date", "ann_date", "f_ann_date")},
    "balancesheet": {"method": "balancesheet", "key_fields": ("ts_code", "end_date", "ann_date", "f_ann_date")},
    "cashflow": {"method": "cashflow", "key_fields": ("ts_code", "end_date", "ann_date", "f_ann_date")},
    "fina_indicator": {"method": "fina_indicator", "key_fields": ("ts_code", "end_date", "ann_date")},
    "express": {"method": "express", "key_fields": ("ts_code", "end_date", "ann_date")},
    "forecast": {"method": "forecast", "key_fields": ("ts_code", "end_date", "ann_date", "type")},
    "dividend": {"method": "dividend", "key_fields": ("ts_code", "end_date", "ann_date", "div_proc")},
    "stk_holdernumber": {"method": "stk_holdernumber", "key_fields": ("ts_code", "end_date", "ann_date")},
}
TRADING_ENDPOINTS = {
    "top_list": {"method": "top_list", "key_fields": ("trade_date", "ts_code")},
    "top_inst": {"method": "top_inst", "key_fields": ("trade_date", "ts_code", "exalter", "side", "reason")},
    "block_trade": {"method": "block_trade", "key_fields": ("trade_date", "ts_code", "price", "buyer", "seller")},
    "moneyflow": {"method": "moneyflow", "key_fields": ("trade_date", "ts_code")},
    "margin": {"method": "margin", "key_fields": ("trade_date", "exchange_id")},
    "margin_detail": {"method": "margin_detail", "key_fields": ("trade_date", "ts_code")},
    "moneyflow_hsgt": {"method": "moneyflow_hsgt", "key_fields": ("trade_date",)},
    "hk_hold": {"method": "hk_hold", "key_fields": ("trade_date", "ts_code", "exchange")},
}


class TushareExtSync:
    """Synchronize optional Tushare datasets into the extension store."""

    _shared_call_times = deque()
    _shared_call_lock = Lock()

    def __init__(
        self,
        store: TushareExtStore,
        pro,
        pro_bar=None,
        *,
        calls_per_minute: int = 180,
        rate_limit_wait_seconds: float = 62,
    ):
        self.store = store
        self.pro = pro
        self.pro_bar = pro_bar
        self.calls_per_minute = max(1, int(calls_per_minute))
        self.rate_limit_wait_seconds = max(0.0, float(rate_limit_wait_seconds))
        self._call_times = self.__class__._shared_call_times
        self._call_lock = self.__class__._shared_call_lock

    @staticmethod
    def _date_text(value) -> str:
        return pd.to_datetime(value).strftime("%Y%m%d")

    @staticmethod
    def _rows(frame) -> list[dict]:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return []
        return frame.where(pd.notna(frame), None).to_dict("records")

    @staticmethod
    def _is_permission_error(error: Exception) -> bool:
        text = str(error).lower()
        markers = ("没有访问", "没有权限", "权限", "permission", "not have access", "积分不足")
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        text = str(error).lower()
        return any(
            marker in text
            for marker in ("每分钟", "频率", "rate limit", "too many requests", "429")
        )

    def _acquire_call_slot(self) -> None:
        while True:
            wait_seconds = 0.0
            with self._call_lock:
                now = time.monotonic()
                while self._call_times and now - self._call_times[0] >= 60:
                    self._call_times.popleft()
                if len(self._call_times) < self.calls_per_minute:
                    self._call_times.append(now)
                    return
                wait_seconds = max(0.01, 60 - (now - self._call_times[0]))
            time.sleep(wait_seconds)

    def _invoke_provider(self, method, **params):
        last_error = None
        for attempt in range(4):
            self._acquire_call_slot()
            try:
                return method(**params)
            except Exception as exc:
                last_error = exc
                if not self._is_rate_limit_error(exc) or attempt >= 3:
                    raise
                time.sleep(self.rate_limit_wait_seconds + random.uniform(0.05, 0.5))

    def _call_dataset(
        self,
        dataset: str,
        method_name: str,
        *,
        key_fields: tuple[str, ...],
        scope: str,
        params: dict,
        ts_code_field: str = "ts_code",
        trade_date_field: str = "trade_date",
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        try:
            method = getattr(self.pro, method_name)
            frame = self._invoke_provider(method, **params)
            rows = self._rows(frame)
            written = self.store.upsert_rows(
                dataset,
                rows,
                key_fields=key_fields,
                ts_code_field=ts_code_field,
                trade_date_field=trade_date_field,
            )
            self.store.set_sync_state(
                dataset,
                scope=scope,
                status="completed",
                start_date=params.get("start_date") or params.get("trade_date"),
                end_date=params.get("end_date") or params.get("trade_date"),
                row_count=written,
            )
            result = {"status": "completed", "rows": written}
        except Exception as exc:
            if not self._is_permission_error(exc):
                raise
            warning = f"Tushare {dataset} permission warning: {exc}"
            self.store.set_sync_state(
                dataset,
                scope=scope,
                status="warning",
                start_date=params.get("start_date") or params.get("trade_date"),
                end_date=params.get("end_date") or params.get("trade_date"),
                warning=warning,
                row_count=0,
            )
            result = {"status": "warning", "rows": 0, "warning": warning}
        if progress_callback:
            progress_callback({"dataset": dataset, **result})
        return result

    def ensure_index_cache(
        self,
        *,
        symbols: Iterable[str] = DEFAULT_INDEX_SYMBOLS,
        today: date | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        today = today or datetime.now().date()
        end_date = self._date_text(today)
        fetched_rows = 0
        datasets = {}
        for symbol in symbols:
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            latest = self.store.latest_trade_date("index_daily", ts_code=symbol)
            target_start = self._date_text(today - timedelta(days=INDEX_CACHE_HISTORY_DAYS))
            cached_rows = self.store.query_rows(
                "index_daily",
                ts_code=symbol,
                limit=1,
                descending=False,
            )
            earliest = self._date_text(cached_rows[0].get("trade_date")) if cached_rows else None
            if earliest and earliest <= target_start and latest:
                start_date = self._date_text(pd.to_datetime(latest) + pd.Timedelta(days=1))
            else:
                start_date = target_start
            result = self._call_dataset(
                "index_daily",
                "index_daily",
                key_fields=("ts_code", "trade_date"),
                scope=symbol,
                params={"ts_code": symbol, "start_date": start_date, "end_date": end_date},
                progress_callback=progress_callback,
            )
            fetched_rows += int(result.get("rows") or 0)
            datasets[symbol] = result
        return {"status": "completed", "fetched_rows": fetched_rows, "datasets": datasets}

    def sync_basics(
        self,
        *,
        today: date | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        today = today or datetime.now().date()
        year_start = f"{today.year}0101"
        year_end = f"{today.year}1231"
        results = {}
        for dataset, spec in BASIC_ENDPOINTS.items():
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            result = self._call_dataset(
                dataset,
                spec["method"],
                key_fields=spec["key_fields"],
                scope="all",
                params=dict(spec.get("params") or {}),
                trade_date_field=spec.get("trade_date_field", "trade_date"),
                progress_callback=progress_callback,
            )
            results[dataset] = result

        for hs_type in ("SH", "SZ"):
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            result = self._call_dataset(
                "hs_const",
                "hs_const",
                key_fields=("ts_code", "hs_type", "in_date"),
                scope=hs_type,
                params={"hs_type": hs_type, "is_new": "1"},
                trade_date_field="in_date",
                progress_callback=progress_callback,
            )
            results[f"hs_const_{hs_type}"] = result

        if halt_checker and halt_checker():
            raise InterruptedError("用户已停止此次更新")
        trade_cal = self._call_dataset(
            "trade_cal",
            "trade_cal",
            key_fields=("exchange", "cal_date"),
            scope=str(today.year),
            params={
                "exchange": "",
                "start_date": year_start,
                "end_date": year_end,
                "fields": "exchange,cal_date,is_open,pretrade_date",
            },
            trade_date_field="cal_date",
            progress_callback=progress_callback,
        )
        results["trade_cal"] = trade_cal
        return {"status": "completed", "datasets": results}

    def sync_valuation_snapshot(
        self,
        trade_dates: Iterable[str],
        *,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        results = {}
        for trade_date in trade_dates:
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            date_text = self._date_text(trade_date)
            for dataset, spec in VALUATION_ENDPOINTS.items():
                if halt_checker and halt_checker():
                    raise InterruptedError("用户已停止此次更新")
                params = dict(spec.get("params") or {})
                params["trade_date"] = date_text
                result = self._call_dataset(
                    dataset,
                    spec["method"],
                    key_fields=spec["key_fields"],
                    scope=date_text,
                    params=params,
                    progress_callback=progress_callback,
                )
                results[dataset] = result
        return {"status": "completed", "datasets": results}

    def sync_price_tracks(
        self,
        target_universe: Iterable[dict],
        *,
        start_date: str,
        end_date: str,
        datasets: Iterable[str] | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        selected = list(datasets or list(PRICE_ENDPOINTS.keys()) + (["daily_qfq"] if self.pro_bar else []))
        results = {dataset: {"status": "completed", "rows": 0} for dataset in selected}
        for stock in target_universe:
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            ts_code = self._ts_code_for_stock(stock)
            for dataset in selected:
                if dataset == "daily_qfq":
                    result = self._call_qfq_price_track(
                        ts_code,
                        start_date=self._date_text(start_date),
                        end_date=self._date_text(end_date),
                        progress_callback=progress_callback,
                    )
                    results[dataset]["rows"] += int(result.get("rows") or 0)
                    if result.get("status") == "warning":
                        results[dataset] = result
                    continue
                spec = PRICE_ENDPOINTS[dataset]
                result = self._call_dataset(
                    dataset,
                    spec["method"],
                    key_fields=spec["key_fields"],
                    scope=ts_code,
                    params={
                        "ts_code": ts_code,
                        "start_date": self._date_text(start_date),
                        "end_date": self._date_text(end_date),
                    },
                    progress_callback=progress_callback,
                )
                results[dataset]["rows"] += int(result.get("rows") or 0)
                if result.get("status") == "warning":
                    results[dataset] = result
        return {"status": "completed", "datasets": results}

    def _call_qfq_price_track(
        self,
        ts_code: str,
        *,
        start_date: str,
        end_date: str,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        if self.pro_bar is None:
            result = {"status": "warning", "rows": 0, "warning": "Tushare pro_bar 不可用，已跳过 daily_qfq"}
            self.store.set_sync_state(
                "daily_qfq",
                scope=ts_code,
                status="warning",
                start_date=start_date,
                end_date=end_date,
                warning=result["warning"],
            )
            if progress_callback:
                progress_callback({"dataset": "daily_qfq", **result})
            return result
        try:
            frame = self._invoke_provider(
                self.pro_bar,
                api=self.pro,
                ts_code=ts_code,
                asset="E",
                freq="D",
                adj="qfq",
                adjfactor=True,
                start_date=start_date,
                end_date=end_date,
            )
            rows = self._rows(frame)
            written = self.store.upsert_rows("daily_qfq", rows, key_fields=("ts_code", "trade_date"))
            self.store.set_sync_state(
                "daily_qfq",
                scope=ts_code,
                status="completed",
                start_date=start_date,
                end_date=end_date,
                row_count=written,
            )
            result = {"status": "completed", "rows": written}
        except Exception as exc:
            if not self._is_permission_error(exc):
                raise
            warning = f"Tushare daily_qfq permission warning: {exc}"
            self.store.set_sync_state(
                "daily_qfq",
                scope=ts_code,
                status="warning",
                start_date=start_date,
                end_date=end_date,
                warning=warning,
            )
            result = {"status": "warning", "rows": 0, "warning": warning}
        if progress_callback:
            progress_callback({"dataset": "daily_qfq", **result})
        return result

    @staticmethod
    def _ts_code_for_stock(stock: dict) -> str:
        ts_code = str(stock.get("ts_code") or "").strip().upper()
        if ts_code:
            return ts_code
        code = str(stock.get("code") or stock.get("symbol") or "").zfill(6)
        suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        return f"{code}.{suffix}"

    def sync_financials_for_universe(
        self,
        target_universe: Iterable[dict],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        datasets: Iterable[str] | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        selected = list(datasets or FINANCIAL_ENDPOINTS.keys())
        results = {dataset: {"status": "completed", "rows": 0} for dataset in selected}
        for stock in target_universe:
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            ts_code = self._ts_code_for_stock(stock)
            for dataset in selected:
                spec = FINANCIAL_ENDPOINTS[dataset]
                params = {"ts_code": ts_code}
                if start_date:
                    params["start_date"] = self._date_text(start_date)
                if end_date:
                    params["end_date"] = self._date_text(end_date)
                result = self._call_dataset(
                    dataset,
                    spec["method"],
                    key_fields=spec["key_fields"],
                    scope=ts_code,
                    params=params,
                    trade_date_field=spec.get("trade_date_field", "end_date"),
                    progress_callback=progress_callback,
                )
                results[dataset]["rows"] += int(result.get("rows") or 0)
                if result.get("status") == "warning":
                    results[dataset] = result
        return {"status": "completed", "datasets": results}

    def sync_trading_snapshot(
        self,
        trade_dates: Iterable[str],
        *,
        datasets: Iterable[str] | None = None,
        progress_callback: Callable[[dict], None] | None = None,
        halt_checker: Callable[[], bool] | None = None,
    ) -> dict:
        selected = list(datasets or TRADING_ENDPOINTS.keys())
        results = {}
        for trade_date in trade_dates:
            if halt_checker and halt_checker():
                raise InterruptedError("用户已停止此次更新")
            date_text = self._date_text(trade_date)
            for dataset in selected:
                if halt_checker and halt_checker():
                    raise InterruptedError("用户已停止此次更新")
                spec = TRADING_ENDPOINTS[dataset]
                result = self._call_dataset(
                    dataset,
                    spec["method"],
                    key_fields=spec["key_fields"],
                    scope=date_text,
                    params={"trade_date": date_text},
                    progress_callback=progress_callback,
                )
                results[dataset] = result
        return {"status": "completed", "datasets": results}
