"""
市场云图缓存与聚合工具
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Callable, Dict, List, Optional

import pandas as pd


INDEX_TARGETS = [
    {"symbol": "sh000001", "code": "000001", "name": "上证指数"},
    {"symbol": "sz399001", "code": "399001", "name": "深证成指"},
    {"symbol": "sz399006", "code": "399006", "name": "创业板指"},
    {"symbol": "sh000688", "code": "000688", "name": "科创50"},
    {"symbol": "sh000300", "code": "000300", "name": "沪深300"},
]

CNINFO_STANDARD_PRIORITY = [
    "证监会行业分类标准（2012）",
    "申银万国行业分类标准",
    "申银万国行业分类标准(旧)",
    "新财富行业分类标准",
    "巨潮行业分类标准",
    "巨潮行业分类标准(旧)",
    "证监会行业分类标准（2001）",
]

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
        df = pd.read_csv(csv_path, usecols=["date", "close", "market_cap"])
    except Exception:
        return None

    if df.empty:
        return None

    latest_date = pd.to_datetime(df.iloc[0]["date"], errors="coerce")
    latest_close = _safe_float(df.iloc[0]["close"])
    market_cap = _safe_float(df.iloc[0].get("market_cap"))
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
        "metrics": metrics,
    }


def snapshot_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "heatmap_snapshot.json"


def industry_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "industry_map.json"


def index_cache_path(data_dir: str = "data") -> Path:
    return Path(data_dir) / "index_snapshot.json"


def load_snapshot_cache(data_dir: str = "data") -> dict:
    return _load_json(snapshot_cache_path(data_dir), {})


def load_industry_cache(data_dir: str = "data") -> dict:
    return _load_json(industry_cache_path(data_dir), {})


def load_index_cache(data_dir: str = "data") -> dict:
    return _load_json(index_cache_path(data_dir), {})


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

    for index, csv_path in enumerate(csv_files, start=1):
        record = _read_stock_snapshot(csv_path, stock_names)
        if record:
            records.append(record)
        if progress_callback and (index == 1 or index == total_files or index % 150 == 0):
            progress_callback(
                stage="snapshot",
                current_step="构建本地云图快照",
                processed_count=index,
                total_count=total_files,
                progress_pct=int((index / max(total_files, 1)) * 100),
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


def build_industry_cache(data_dir: str = "data", progress_callback: Optional[Callable] = None) -> dict:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 akshare，无法构建行业缓存") from exc

    data_path = Path(data_dir)
    previous_cache = load_industry_cache(data_dir)
    previous_items = previous_cache.get("items", {})
    csv_codes = {path.stem for path in data_path.rglob("*.csv")}
    sectors_df = ak.stock_sector_spot(indicator="行业")
    mapping: Dict[str, str] = {}
    sector_total = len(sectors_df)

    for index, row in enumerate(sectors_df.to_dict("records"), start=1):
        label = str(row.get("label", ""))
        sector_name = str(row.get("板块", "")).strip()
        if not label or not sector_name:
            continue
        try:
            detail_df = ak.stock_sector_detail(sector=label)
        except Exception:
            continue

        for detail in detail_df.itertuples(index=False):
            code = str(getattr(detail, "code", "")).zfill(6)
            if code and code in csv_codes and code not in mapping:
                mapping[code] = sector_name

        if progress_callback and (index == 1 or index == sector_total or index % 6 == 0):
            progress_callback(
                stage="industry",
                current_step="刷新行业映射缓存",
                processed_count=index,
                total_count=sector_total,
                progress_pct=int((index / max(sector_total, 1)) * 100),
                current_stock=None,
            )

    missing_codes = sorted(csv_codes - set(mapping))
    reused_count = 0
    for code in list(missing_codes):
        cached_label = _safe_text(previous_items.get(code))
        if cached_label:
            mapping[code] = cached_label
            reused_count += 1

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
                for field in ["行业大类", "行业中类", "行业次类", "行业门类"]:
                    value = _safe_text(row.get(field))
                    if value:
                        return value

        normalized = normalized.sort_values("变更日期", ascending=False)
        for _, row in normalized.iterrows():
            for field in ["行业大类", "行业中类", "行业次类", "行业门类"]:
                value = _safe_text(row.get(field))
                if value:
                    return value
        return ""

    def fetch_cninfo_industry(code: str) -> tuple[str, str]:
        try:
            detail_df = ak.stock_industry_change_cninfo(
                symbol=code,
                start_date="20000101",
                end_date="20300101",
            )
        except Exception:
            detail_df = pd.DataFrame()
        return code, choose_cninfo_industry(detail_df)

    cninfo_total = len(missing_codes)
    for processed, code in enumerate(missing_codes, start=1):
        resolved_code, industry_name = fetch_cninfo_industry(code)
        if industry_name:
            mapping[resolved_code] = industry_name
        if progress_callback and cninfo_total and (processed == 1 or processed == cninfo_total or processed % 25 == 0):
            progress_callback(
                stage="industry",
                current_step="补充分散的未分类股票行业",
                processed_count=processed,
                total_count=cninfo_total,
                progress_pct=int((processed / max(cninfo_total, 1)) * 100),
                current_stock={"code": resolved_code, "name": ""},
            )

    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "akshare:stock_sector_spot/stock_sector_detail + akshare:stock_industry_change_cninfo",
        "mapped_count": len(mapping),
        "reused_count": reused_count,
        "unmapped_count": len(csv_codes - set(mapping)),
        "items": mapping,
    }
    _write_json(industry_cache_path(data_dir), payload)
    return payload


def build_index_cache(data_dir: str = "data") -> dict:
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("未安装 akshare，无法构建指数缓存") from exc

    index_df = ak.stock_zh_index_spot_sina()
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
    return not (
        "unmapped_count" in industry
        and "mapped_count" in industry
        and bool(industry.get("source"))
    )


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


def build_heatmap_payload(data_dir: str = "data", scope: str = "all", metric: str = "daily") -> dict:
    caches = ensure_market_caches(data_dir=data_dir)
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
