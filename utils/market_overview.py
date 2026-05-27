"""
市场云图缓存与聚合工具
"""
from __future__ import annotations

import json
import os
import socket
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Callable, Dict, List, Optional

import pandas as pd

from utils.data_provider import MAX_REASONABLE_MARKET_CAP_YUAN, get_config_value, normalize_market_cap_yuan
from utils.local_config import load_config_file
from utils.price_adjustment import repair_adjustment_gaps


INDEX_TARGETS = [
    {"symbol": "sh000001", "code": "000001", "name": "上证指数"},
    {"symbol": "sz399001", "code": "399001", "name": "深证成指"},
    {"symbol": "sz399006", "code": "399006", "name": "创业板指"},
    {"symbol": "sh000688", "code": "000688", "name": "科创50"},
    {"symbol": "sh000300", "code": "000300", "name": "沪深300"},
]

CNINFO_STANDARD_PRIORITY = [
    "申银万国行业分类标准",
    "申银万国行业分类标准(旧)",
    "巨潮行业分类标准",
    "巨潮行业分类标准(旧)",
    "中证行业分类标准",
    "中证行业分类标准(旧)",
    "新财富行业分类标准",
    "证监会行业分类标准（2012）",
    "证监会行业分类标准（2001）",
]

INDUSTRY_FETCH_MAX_WORKERS = 12
INDUSTRY_CACHE_REUSE_MIN_RATIO = 0.95
INDUSTRY_READY_MAX_UNMAPPED = 100
INDUSTRY_READY_MAX_UNMAPPED_RATIO = 0.02
INDUSTRY_FETCH_TIMEOUT_SECONDS = 12
INDUSTRY_FETCH_MAX_SECONDS = 120
HEATMAP_SCOPES = ("all", "main", "chinext", "star")
HEATMAP_METRICS = ("daily", "weekly", "monthly", "five_day")

HIDDEN_MARKET_STOCK_CODES = {"300391"}
HIDDEN_MARKET_STOCK_NAMES = {"长药退"}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def _classify_board(stock_code: str) -> str:
    code = str(stock_code or "").strip()
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def _safe_float(value) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(numeric):
        return None
    return numeric


def _safe_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def is_hidden_market_stock(stock_code: str, stock_name: str = "") -> bool:
    code = str(stock_code or "").strip()
    name = _safe_text(stock_name)
    return code in HIDDEN_MARKET_STOCK_CODES or name in HIDDEN_MARKET_STOCK_NAMES


def _pct_change(latest: Optional[float], base: Optional[float]) -> Optional[float]:
    if latest is None or base is None or base == 0:
        return None
    return round(((latest / base) - 1) * 100, 4)


def _metric_base_close(df: pd.DataFrame, metric: str) -> Optional[float]:
    dates = pd.to_datetime(df["date"], errors="coerce")
    closes = pd.to_numeric(df["close"], errors="coerce")
    valid_df = pd.DataFrame({"date": dates, "close": closes}).dropna()
    if valid_df.empty:
        return None

    latest_date = valid_df.iloc[0]["date"]
    if metric == "daily":
        if len(valid_df) < 2:
            return None
        return _safe_float(valid_df.iloc[1]["close"])

    if metric == "five_day":
        if len(valid_df) < 5:
            return None
        return _safe_float(valid_df.iloc[4]["close"])

    if metric == "weekly":
        iso = latest_date.isocalendar()
        scoped = valid_df[
            (valid_df["date"].dt.isocalendar().year == iso.year)
            & (valid_df["date"].dt.isocalendar().week == iso.week)
        ]
        if scoped.empty:
            return None
        return _safe_float(scoped.iloc[-1]["close"])

    if metric == "monthly":
        scoped = valid_df[
            (valid_df["date"].dt.year == latest_date.year)
            & (valid_df["date"].dt.month == latest_date.month)
        ]
        if scoped.empty:
            return None
        return _safe_float(scoped.iloc[-1]["close"])

    return None


def _read_stock_snapshot(csv_path: Path, stock_names: Dict[str, str]) -> Optional[dict]:
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    if df.empty:
        return None
    if {"date", "open", "high", "low", "close"}.issubset(df.columns):
        df, _ = repair_adjustment_gaps(df)

    latest_date = pd.to_datetime(df.iloc[0]["date"], errors="coerce")
    latest_close = _safe_float(df.iloc[0]["close"])
    market_cap = normalize_market_cap_yuan(df.iloc[0].get("market_cap"), source_unit="yuan") or 0
    if pd.isna(latest_date) or latest_close is None:
        return None

    metrics = {}
    for metric in ["daily", "weekly", "monthly", "five_day"]:
        metrics[metric] = _pct_change(latest_close, _metric_base_close(df, metric))

    code = csv_path.stem
    name = stock_names.get(code, "未知")
    if is_hidden_market_stock(code, name):
        return None

    return {
        "code": code,
        "name": name,
        "board": _classify_board(code),
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "latest_price": round(latest_close, 2),
        "market_cap": round(market_cap or 0.0, 2),
        "data_count": len(df),
        "metrics": metrics,
    }


def snapshot_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "heatmap_snapshot.json"


def industry_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "industry_map.json"


def index_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "index_snapshot.json"


def heatmap_payload_cache_dir(data_dir: str = "data") -> Path:
    return Path(data_dir) / "heatmap_payloads"


def heatmap_payload_cache_path(data_dir: str = "data", scope: str = "all", metric: str = "daily") -> Path:
    scope = scope if scope in HEATMAP_SCOPES else "all"
    metric = metric if metric in HEATMAP_METRICS else "daily"
    return heatmap_payload_cache_dir(data_dir) / f"{scope}-{metric}.json"


def load_snapshot_cache(data_dir: str = "data") -> dict:
    return _load_json(snapshot_cache_path(data_dir), {})


def load_industry_cache(data_dir: str = "data") -> dict:
    return _load_json(industry_cache_path(data_dir), {})


def _is_tushare_provider_dir(data_path: Path) -> bool:
    parts = data_path.resolve().parts
    return len(parts) >= 2 and parts[-2] == "providers" and parts[-1] == "tushare"


def _load_tushare_token() -> str:
    token = os.getenv("TUSHARE_TOKEN") or ""
    if token.strip():
        return token.strip()

    value = get_config_value(load_config_file(), "data_source", "tushare", "token")
    if value:
        return str(value).strip()
    return ""


def _load_tushare_metadata_industries(data_path: Path, csv_codes: set[str]) -> tuple[Dict[str, str], Dict[str, str], str]:
    meta_path = data_path / "tushare_stock_map.json"
    mapping: Dict[str, str] = {}
    source_map: Dict[str, str] = {}
    metadata = _load_json(meta_path, {})

    if isinstance(metadata, dict):
        for code, item in metadata.items():
            if code not in csv_codes or not isinstance(item, dict):
                continue
            industry = _safe_text(item.get("industry"))
            if industry:
                mapping[code] = industry
                source_map[code] = "tushare_stock_map"

    if mapping or not _is_tushare_provider_dir(data_path):
        return mapping, source_map, ""

    token = _load_tushare_token()
    if not token:
        return mapping, source_map, "missing_tushare_token"

    try:
        import tushare as ts

        ts.set_token(token)
        pro = ts.pro_api(token)
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,exchange,list_date",
        )
    except Exception as exc:
        return mapping, source_map, str(exc)

    if df is None or df.empty or "symbol" not in df.columns:
        return mapping, source_map, "empty_stock_basic"

    df = df.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df = df[df["symbol"].isin(csv_codes)]
    updated_metadata = dict(metadata) if isinstance(metadata, dict) else {}

    for _, row in df.iterrows():
        code = str(row.get("symbol", "")).zfill(6)
        industry = _safe_text(row.get("industry"))
        if industry:
            mapping[code] = industry
            source_map[code] = "tushare_stock_basic"
        updated_metadata[code] = {
            "ts_code": _safe_text(row.get("ts_code")),
            "name": _safe_text(row.get("name")),
            "area": _safe_text(row.get("area")),
            "industry": industry,
            "exchange": _safe_text(row.get("exchange")),
            "market": _safe_text(row.get("market")),
            "list_date": _safe_text(row.get("list_date")),
        }

    if updated_metadata:
        _write_json(meta_path, updated_metadata)
    return mapping, source_map, ""


def load_index_cache(data_dir: str = "data") -> dict:
    return _load_json(index_cache_path(data_dir), {})


def load_heatmap_payload_cache(data_dir: str = "data", scope: str = "all", metric: str = "daily") -> dict:
    return _load_json(heatmap_payload_cache_path(data_dir, scope, metric), {})


def _stock_csv_files(data_dir: str = "data") -> List[Path]:
    return sorted(Path(data_dir).glob("[0-9][0-9]/*.csv"))


def _local_stock_data_status(data_dir: str = "data") -> dict:
    data_path = Path(data_dir)
    stock_names = _load_json(data_path / "stock_names.json", {})
    latest_date = None
    readable_count = 0
    for csv_path in _stock_csv_files(data_dir):
        code = csv_path.stem
        if is_hidden_market_stock(code, stock_names.get(code, "")):
            continue
        try:
            df = pd.read_csv(csv_path, usecols=["date"], nrows=1)
        except Exception:
            continue
        if df.empty:
            continue
        date_value = pd.to_datetime(df.iloc[0]["date"], errors="coerce")
        if pd.isna(date_value):
            continue
        readable_count += 1
        date_text = date_value.strftime("%Y-%m-%d")
        if latest_date is None or date_text > latest_date:
            latest_date = date_text
    return {
        "latest_date": latest_date,
        "stock_count": readable_count,
    }


def snapshot_cache_needs_refresh(data_dir: str = "data") -> bool:
    snapshot = load_snapshot_cache(data_dir)
    if not snapshot:
        return True
    local_status = _local_stock_data_status(data_dir)
    local_latest = local_status.get("latest_date")
    snapshot_latest = snapshot.get("latest_date")
    if local_latest and (not snapshot_latest or local_latest > snapshot_latest):
        return True
    local_count = local_status.get("stock_count") or 0
    snapshot_count = int(snapshot.get("stock_count") or 0)
    if local_count and local_count != snapshot_count:
        return True
    return False


def build_snapshot_cache(data_dir: str = "data", progress_callback: Optional[Callable] = None) -> dict:
    data_path = Path(data_dir)
    stock_names = _load_json(data_path / "stock_names.json", {})
    csv_files = _stock_csv_files(data_dir)
    records = []
    total_files = len(csv_files)
    max_workers = min(max((os.cpu_count() or 4) * 2, 4), 32, max(total_files, 1))
    processed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending = {
            executor.submit(_read_stock_snapshot, csv_path, stock_names): csv_path
            for csv_path in csv_files
        }
        while pending:
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                csv_path = pending.pop(future)
                processed += 1
                try:
                    record = future.result()
                except Exception:
                    record = None
                if record:
                    records.append(record)
                if progress_callback and (processed == 1 or processed == total_files or processed % 150 == 0):
                    progress_callback(
                        stage="snapshot",
                        current_step="并行构建本地云图快照",
                        processed_count=processed,
                        total_count=total_files,
                        progress_pct=int((processed / max(total_files, 1)) * 100),
                        current_stock={"code": csv_path.stem, "name": stock_names.get(csv_path.stem, "未知")},
                    )

    latest_date = max((item["latest_date"] for item in records), default=None)
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_date": latest_date,
        "stock_count": len(records),
        "stocks": records,
    }
    _write_json(snapshot_cache_path(data_dir), payload)
    return payload


def _emit_industry_progress(
    progress_callback: Optional[Callable],
    current_step: str,
    processed_count: int,
    total_count: int,
    code: str = "",
) -> None:
    if not progress_callback:
        return
    progress_callback(
        stage="industry",
        current_step=current_step,
        processed_count=processed_count,
        total_count=total_count,
        progress_pct=int((processed_count / max(total_count, 1)) * 100),
        current_stock={"code": code, "name": ""},
    )


def _run_industry_fetch_pool(fetch_func, code_list: List[str], max_workers: int):
    mapping: Dict[str, str] = {}
    source_map: Dict[str, str] = {}
    pending = set()
    executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(code_list) or 1)))
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(INDUSTRY_FETCH_TIMEOUT_SECONDS)
    deadline = time.monotonic() + INDUSTRY_FETCH_MAX_SECONDS
    try:
        pending = {executor.submit(fetch_func, code): code for code in code_list}
        while pending and time.monotonic() < deadline:
            done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    code, industry_name, source = future.result(timeout=0)
                except Exception:
                    continue
                if industry_name:
                    mapping[code] = industry_name
                    source_map[code] = source
        for future in pending:
            future.cancel()
    finally:
        socket.setdefaulttimeout(previous_timeout)
        executor.shutdown(wait=False, cancel_futures=True)
    return mapping, source_map, len(pending)


def build_industry_cache(data_dir: str = "data", progress_callback: Optional[Callable] = None) -> dict:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 akshare，无法构建行业缓存") from exc

    data_path = Path(data_dir)
    previous_cache = load_industry_cache(data_dir)
    previous_items = previous_cache.get("items", {})
    csv_codes = {path.stem for path in data_path.rglob("*.csv")}
    previous_mapping = {
        code: _safe_text(previous_items.get(code))
        for code in csv_codes
        if _safe_text(previous_items.get(code))
    }
    previous_ratio = len(previous_mapping) / max(len(csv_codes), 1)

    if previous_mapping and previous_ratio >= INDUSTRY_CACHE_REUSE_MIN_RATIO:
        _emit_industry_progress(
            progress_callback,
            "复用本地行业映射",
            len(previous_mapping),
            len(csv_codes),
        )
        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "previous_cache",
            "mapped_count": len(previous_mapping),
            "eastmoney_count": 0,
            "cninfo_count": 0,
            "reused_count": len(previous_mapping),
            "unmapped_count": len(csv_codes - set(previous_mapping)),
            "items": previous_mapping,
        }
        _write_json(industry_cache_path(data_dir), payload)
        return payload

    mapping, source_map, provider_error = _load_tushare_metadata_industries(data_path, csv_codes)
    provider_count = len(mapping)

    for code, label in previous_mapping.items():
        if code not in mapping:
            mapping[code] = label
            source_map[code] = "previous_cache"

    if mapping:
        _emit_industry_progress(
            progress_callback,
            "复用数据源行业映射",
            len(mapping),
            len(csv_codes),
        )

    def fetch_eastmoney_industry(code: str) -> tuple[str, str, str]:
        try:
            info_df = ak.stock_individual_info_em(symbol=code)
        except Exception:
            return code, "", "eastmoney"
        if info_df is None or info_df.empty or not {"item", "value"}.issubset(info_df.columns):
            return code, "", "eastmoney"
        rows = info_df[info_df["item"].astype(str).str.strip() == "行业"]
        if rows.empty:
            return code, "", "eastmoney"
        return code, _safe_text(rows.iloc[0].get("value")), "eastmoney"

    code_list = sorted(csv_codes - set(mapping))
    eastmoney_mapping, eastmoney_source_map, eastmoney_pending = _run_industry_fetch_pool(
        fetch_eastmoney_industry,
        code_list,
        INDUSTRY_FETCH_MAX_WORKERS,
    )
    mapping.update(eastmoney_mapping)
    source_map.update(eastmoney_source_map)
    _emit_industry_progress(
        progress_callback,
        "按交易软件口径刷新行业映射",
        len(csv_codes) - eastmoney_pending,
        len(csv_codes),
    )

    missing_codes = sorted(csv_codes - set(mapping))

    def choose_cninfo_industry(rows: pd.DataFrame) -> str:
        if rows is None or rows.empty:
            return ""

        normalized = rows.copy()
        normalized["分类标准"] = normalized["分类标准"].astype(str)
        normalized["变更日期"] = pd.to_datetime(normalized["变更日期"], errors="coerce")

        for standard in CNINFO_STANDARD_PRIORITY:
            scoped = normalized[normalized["分类标准"] == standard]
            if scoped.empty:
                continue
            scoped = scoped.sort_values("变更日期", ascending=False)
            for _, row in scoped.iterrows():
                for field in ["行业次类", "行业大类", "行业中类", "行业门类"]:
                    value = _safe_text(row.get(field))
                    if value:
                        return value

        normalized = normalized.sort_values("变更日期", ascending=False)
        for _, row in normalized.iterrows():
            for field in ["行业次类", "行业大类", "行业中类", "行业门类"]:
                value = _safe_text(row.get(field))
                if value:
                    return value
        return ""

    def fetch_cninfo_industry(code: str) -> tuple[str, str, str]:
        try:
            detail_df = ak.stock_industry_change_cninfo(
                symbol=code,
                start_date="20000101",
                end_date="20300101",
            )
        except Exception:
            detail_df = pd.DataFrame()
        return code, choose_cninfo_industry(detail_df), "cninfo"

    cninfo_total = len(missing_codes)
    if missing_codes:
        cninfo_mapping, cninfo_source_map, cninfo_pending = _run_industry_fetch_pool(
            fetch_cninfo_industry,
            missing_codes,
            INDUSTRY_FETCH_MAX_WORKERS,
        )
        mapping.update(cninfo_mapping)
        source_map.update(cninfo_source_map)
        _emit_industry_progress(
            progress_callback,
            "补充分散的未分类股票行业",
            cninfo_total - cninfo_pending,
            cninfo_total,
        )

    missing_codes = sorted(csv_codes - set(mapping))
    reused_count = sum(1 for source in source_map.values() if source == "previous_cache")
    for code in list(missing_codes):
        cached_label = _safe_text(previous_items.get(code))
        if cached_label:
            mapping[code] = cached_label
            source_map[code] = "previous_cache"
            reused_count += 1

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "provider_metadata + akshare:stock_individual_info_em + akshare:stock_industry_change_cninfo",
        "mapped_count": len(mapping),
        "provider_count": provider_count,
        "eastmoney_count": sum(1 for source in source_map.values() if source == "eastmoney"),
        "cninfo_count": sum(1 for source in source_map.values() if source == "cninfo"),
        "reused_count": reused_count,
        "unmapped_count": len(csv_codes - set(mapping)),
        "items": mapping,
    }
    if provider_error:
        payload["provider_error"] = provider_error
    _write_json(industry_cache_path(data_dir), payload)
    return payload


def build_index_cache(data_dir: str = "data") -> dict:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 akshare，无法构建指数缓存") from exc

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(INDUSTRY_FETCH_TIMEOUT_SECONDS)
    try:
        index_df = ak.stock_zh_index_spot_sina()
    finally:
        socket.setdefaulttimeout(previous_timeout)
    index_df["代码"] = index_df["代码"].astype(str)
    items = []
    for target in INDEX_TARGETS:
        matched = index_df[index_df["代码"] == target["symbol"]]
        if matched.empty:
            continue
        row = matched.iloc[0]
        items.append(
            {
                "symbol": target["symbol"],
                "code": target["code"],
                "name": target["name"],
                "latest_price": round(_safe_float(row["最新价"]) or 0.0, 2),
                "change_pct": round(_safe_float(row["涨跌幅"]) or 0.0, 2),
                "change_value": round(_safe_float(row["涨跌额"]) or 0.0, 2),
            }
        )

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "akshare:stock_zh_index_spot_sina",
        "items": items,
    }
    _write_json(index_cache_path(data_dir), payload)
    return payload


def rebuild_market_caches(
    data_dir: str = "data",
    progress_callback: Optional[Callable] = None,
    preserve_existing: bool = True,
) -> dict:
    previous_snapshot = load_snapshot_cache(data_dir)
    previous_industry = load_industry_cache(data_dir)
    previous_index = load_index_cache(data_dir)

    result = {
        "snapshot": previous_snapshot,
        "industry": previous_industry,
        "indices": previous_index,
        "errors": {},
    }

    try:
        result["snapshot"] = build_snapshot_cache(data_dir=data_dir, progress_callback=progress_callback)
    except Exception as exc:
        if not preserve_existing:
            raise
        result["errors"]["snapshot"] = str(exc)

    try:
        result["industry"] = build_industry_cache(data_dir=data_dir, progress_callback=progress_callback)
    except Exception as exc:
        if not preserve_existing:
            raise
        result["errors"]["industry"] = str(exc)

    try:
        result["indices"] = build_index_cache(data_dir=data_dir)
    except Exception as exc:
        if not preserve_existing:
            raise
        result["errors"]["indices"] = str(exc)

    try:
        result["heatmap_payloads"] = build_heatmap_payload_cache(
            data_dir=data_dir,
            caches=result,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        if not preserve_existing:
            raise
        result["errors"]["heatmap_payloads"] = str(exc)

    return result


def ensure_market_caches(data_dir: str = "data") -> dict:
    snapshot = load_snapshot_cache(data_dir)
    industry = load_industry_cache(data_dir)
    indices = load_index_cache(data_dir)
    errors = {}

    if snapshot_cache_needs_refresh(data_dir):
        try:
            snapshot = build_snapshot_cache(data_dir=data_dir)
        except Exception as exc:
            errors["snapshot"] = str(exc)

    if not industry:
        try:
            industry = build_industry_cache(data_dir=data_dir)
        except Exception as exc:
            errors["industry"] = str(exc)

    if not indices:
        try:
            indices = build_index_cache(data_dir=data_dir)
        except Exception as exc:
            errors["indices"] = str(exc)

    if snapshot or industry or indices:
        return {
            "snapshot": snapshot,
            "industry": industry,
            "indices": indices,
            "errors": errors,
        }
    return rebuild_market_caches(data_dir=data_dir, preserve_existing=True)


def market_cache_needs_refresh(data_dir: str = "data") -> bool:
    if snapshot_cache_needs_refresh(data_dir):
        return True
    industry = load_industry_cache(data_dir)
    if not industry:
        return True
    local_status = _local_stock_data_status(data_dir)
    local_count = int(local_status.get("stock_count") or 0)
    unmapped_count = int(industry.get("unmapped_count") or 0)
    allowed_unmapped = max(
        INDUSTRY_READY_MAX_UNMAPPED,
        int(local_count * INDUSTRY_READY_MAX_UNMAPPED_RATIO),
    )
    return not (
        "unmapped_count" in industry
        and "mapped_count" in industry
        and bool(industry.get("source"))
        and unmapped_count <= allowed_unmapped
        and heatmap_payload_cache_ready(data_dir)
    )


def load_market_caches(data_dir: str = "data") -> dict:
    return {
        "snapshot": load_snapshot_cache(data_dir),
        "industry": load_industry_cache(data_dir),
        "indices": load_index_cache(data_dir),
        "errors": {},
    }


def count_market_cap_anomalies(data_dir: str = "data") -> int:
    snapshot = load_snapshot_cache(data_dir)
    count = 0
    for stock in snapshot.get("stocks", []) or []:
        market_cap = _safe_float(stock.get("market_cap"))
        if market_cap and market_cap > MAX_REASONABLE_MARKET_CAP_YUAN:
            count += 1
    return count


def market_cache_health(data_dir: str = "data") -> dict:
    local_status = _local_stock_data_status(data_dir)
    snapshot = load_snapshot_cache(data_dir)
    industry = load_industry_cache(data_dir)
    indices = load_index_cache(data_dir)

    local_latest = local_status.get("latest_date")
    snapshot_latest = snapshot.get("latest_date")
    local_count = local_status.get("stock_count") or 0
    snapshot_count = int(snapshot.get("stock_count") or 0)
    snapshot_stale = bool(
        not snapshot
        or (local_latest and (not snapshot_latest or local_latest > snapshot_latest))
        or (local_count and local_count != snapshot_count)
    )
    industry_ready = bool(
        industry
        and "unmapped_count" in industry
        and "mapped_count" in industry
        and industry.get("source")
    )
    unmapped_count = int(industry.get("unmapped_count") or 0)
    allowed_unmapped = max(
        INDUSTRY_READY_MAX_UNMAPPED,
        int((local_count or snapshot_count or 0) * INDUSTRY_READY_MAX_UNMAPPED_RATIO),
    )
    if industry_ready and unmapped_count > allowed_unmapped:
        industry_ready = False
    indices_ready = bool(indices and indices.get("items"))
    payloads_ready = heatmap_payload_cache_ready(data_dir)
    refresh_pending = snapshot_stale or not industry_ready or not indices_ready or not payloads_ready

    return {
        "local_latest_date": local_latest,
        "local_stock_count": local_count,
        "snapshot_latest_date": snapshot_latest,
        "snapshot_stock_count": snapshot_count,
        "snapshot_updated_at": snapshot.get("updated_at"),
        "industry_updated_at": industry.get("updated_at"),
        "industry_mapped_count": int(industry.get("mapped_count") or 0),
        "industry_unmapped_count": unmapped_count,
        "industry_allowed_unmapped_count": allowed_unmapped,
        "indices_updated_at": indices.get("updated_at"),
        "snapshot_stale": snapshot_stale,
        "industry_ready": industry_ready,
        "indices_ready": indices_ready,
        "heatmap_payloads_ready": payloads_ready,
        "refresh_pending": refresh_pending,
        "market_cap_anomaly_count": count_market_cap_anomalies(data_dir),
    }


def _group_stocks_by_industry(stocks: List[dict], industry_items: Dict[str, str], metric: str) -> List[dict]:
    grouped: Dict[str, dict] = {}
    for stock in stocks:
        industry = industry_items.get(stock["code"], "未分类")
        group = grouped.setdefault(
            industry,
            {
                "name": industry,
                "stock_count": 0,
                "market_cap": 0.0,
                "children": [],
                "change_sum": 0.0,
                "change_count": 0,
            },
        )
        metric_value = stock.get("metrics", {}).get(metric)
        child = {
            "code": stock["code"],
            "name": stock["name"],
            "industry": industry,
            "board": stock["board"],
            "latest_price": stock["latest_price"],
            "change_pct": metric_value,
            "market_cap": stock["market_cap"],
            "value": max(round((stock["market_cap"] or 0.0) / 1e8, 4), 0.0001),
        }
        group["children"].append(child)
        group["stock_count"] += 1
        group["market_cap"] += stock["market_cap"] or 0.0
        if metric_value is not None:
            group["change_sum"] += float(metric_value)
            group["change_count"] += 1

    result = list(grouped.values())
    result.sort(key=lambda item: item["market_cap"], reverse=True)
    for item in result:
        item["children"].sort(key=lambda child: child["market_cap"], reverse=True)
        item["change_pct"] = round(item["change_sum"] / item["change_count"], 4) if item["change_count"] else None
        item.pop("change_sum", None)
        item.pop("change_count", None)
    return result


def _build_market_stats(stocks: List[dict], metric: str) -> dict:
    values = [stock.get("metrics", {}).get(metric) for stock in stocks]
    values = [value for value in values if value is not None]
    up_count = len([value for value in values if value > 0])
    down_count = len([value for value in values if value < 0])
    flat_count = len(values) - up_count - down_count
    return {
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "median_change_pct": round(float(median(values)), 2) if values else None,
    }


def _heatmap_payload_signature(snapshot: dict, industry_cache: dict, index_cache: dict) -> dict:
    return {
        "snapshot_updated_at": snapshot.get("updated_at"),
        "snapshot_latest_date": snapshot.get("latest_date"),
        "industry_updated_at": industry_cache.get("updated_at"),
        "industry_mapped_count": industry_cache.get("mapped_count"),
        "industry_unmapped_count": industry_cache.get("unmapped_count"),
        "index_updated_at": index_cache.get("updated_at"),
    }


def _heatmap_payload_cache_is_current(payload: dict, snapshot: dict, industry_cache: dict, index_cache: dict) -> bool:
    return bool(payload and payload.get("signature") == _heatmap_payload_signature(snapshot, industry_cache, index_cache))


def _build_heatmap_payload_from_caches(caches: dict, scope: str = "all", metric: str = "daily") -> dict:
    snapshot = caches.get("snapshot") or {}
    industry_cache = caches.get("industry") or {}
    index_cache = caches.get("indices") or {}

    stocks = [
        stock for stock in snapshot.get("stocks", [])
        if not is_hidden_market_stock(stock.get("code"), stock.get("name"))
    ]
    if scope in {"main", "chinext", "star"}:
        stocks = [stock for stock in stocks if stock.get("board") == scope]

    groups = _group_stocks_by_industry(stocks, industry_cache.get("items", {}), metric)
    stats = _build_market_stats(stocks, metric)

    return {
        "latest_date": snapshot.get("latest_date"),
        "updated_at": snapshot.get("updated_at"),
        "scope": scope,
        "metric": metric,
        "groups": groups,
        "group_count": len(groups),
        "stock_count": len(stocks),
        "header_indices": index_cache.get("items", []),
        "ticker_stats": stats,
        "cache_status": {
            "snapshot_updated_at": snapshot.get("updated_at"),
            "industry_updated_at": industry_cache.get("updated_at"),
            "index_updated_at": index_cache.get("updated_at"),
            "errors": caches.get("errors", {}),
        },
    }


def build_heatmap_payload_cache(
    data_dir: str = "data",
    scopes: tuple[str, ...] = HEATMAP_SCOPES,
    metrics: tuple[str, ...] = HEATMAP_METRICS,
    caches: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
) -> dict:
    caches = caches or load_market_caches(data_dir=data_dir)
    snapshot = caches.get("snapshot") or {}
    industry_cache = caches.get("industry") or {}
    index_cache = caches.get("indices") or {}
    signature = _heatmap_payload_signature(snapshot, industry_cache, index_cache)
    total = max(len(scopes) * len(metrics), 1)
    processed = 0
    paths = {}

    for scope in scopes:
        for metric in metrics:
            processed += 1
            payload = _build_heatmap_payload_from_caches(caches, scope=scope, metric=metric)
            wrapper = {
                "success": True,
                "data": payload,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "scope": scope,
                "metric": metric,
                "signature": signature,
            }
            path = heatmap_payload_cache_path(data_dir, scope, metric)
            _write_json(path, wrapper)
            paths[f"{scope}:{metric}"] = str(path)
            if progress_callback:
                progress_callback(
                    stage="heatmap_payload",
                    current_step="预生成云图视图缓存",
                    processed_count=processed,
                    total_count=total,
                    progress_pct=int((processed / total) * 100),
                    current_stock={"code": scope, "name": metric},
                )

    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(paths),
        "paths": paths,
        "signature": signature,
    }


def heatmap_payload_cache_ready(data_dir: str = "data") -> bool:
    caches = load_market_caches(data_dir=data_dir)
    snapshot = caches.get("snapshot") or {}
    industry_cache = caches.get("industry") or {}
    index_cache = caches.get("indices") or {}
    payload = load_heatmap_payload_cache(data_dir, "all", "daily")
    return _heatmap_payload_cache_is_current(payload, snapshot, industry_cache, index_cache)


def build_heatmap_payload(data_dir: str = "data", scope: str = "all", metric: str = "daily", refresh: bool = True) -> dict:
    caches = ensure_market_caches(data_dir=data_dir) if refresh else load_market_caches(data_dir=data_dir)
    if not refresh:
        cached = load_heatmap_payload_cache(data_dir, scope, metric)
        if _heatmap_payload_cache_is_current(
            cached,
            caches.get("snapshot") or {},
            caches.get("industry") or {},
            caches.get("indices") or {},
        ):
            return cached.get("data") or cached.get("payload") or {}
    return _build_heatmap_payload_from_caches(caches, scope=scope, metric=metric)
