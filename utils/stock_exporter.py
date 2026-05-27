"""
Stock lookup, single-stock freshness checks, and CSV export helpers.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from utils.csv_manager import CSVManager
from utils.data_provider import create_data_provider, get_config_value
from utils.local_config import load_config_file
from utils.market_overview import is_hidden_market_stock
from utils.provider_router import get_active_provider_name, provider_data_dir
from utils.strategy_labels import fallback_stock_name


DOWNLOADS_DIR = Path("/Users/chenxingyu/Downloads")


try:
    from pypinyin import Style, lazy_pinyin
except ImportError:  # pragma: no cover - dependency is declared, fallback keeps app usable.
    Style = None
    lazy_pinyin = None


def load_stock_names(data_dir: str = "data") -> Dict[str, str]:
    result = {}
    names_file = Path(data_dir) / "stock_names.json"
    if names_file.exists():
        try:
            with open(names_file, "r", encoding="utf-8") as file:
                payload = json.load(file) or {}
            for code, name in payload.items():
                normalized_code = str(code or "").strip().zfill(6)
                if CSVManager.STOCK_CODE_PATTERN.match(normalized_code):
                    result[normalized_code] = str(name or "").strip()
        except Exception:
            result = {}

    meta_file = Path(data_dir) / "tushare_stock_map.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as file:
                payload = json.load(file) or {}
            for code, meta in payload.items():
                normalized_code = str(code or "").strip().zfill(6)
                name = meta.get("name") if isinstance(meta, dict) else None
                if CSVManager.STOCK_CODE_PATTERN.match(normalized_code) and name:
                    result[normalized_code] = str(name).strip()
        except Exception:
            pass
    return result


def default_tushare_token(config: Optional[dict] = None):
    token = os.getenv("TUSHARE_TOKEN")
    if token:
        return token.strip(), "环境变量 TUSHARE_TOKEN"

    config = config or load_config_file("config/config.yaml")
    token = get_config_value(config, "data_source", "tushare", "token")
    if token:
        return str(token).strip(), "本机配置"

    return None, None


def _normalize_query(value) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _pinyin_parts(name: str):
    if not name or lazy_pinyin is None:
        return "", ""
    full_parts = lazy_pinyin(name, errors="ignore")
    first_parts = lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore")
    return "".join(full_parts).lower(), "".join(first_parts).lower()


def _safe_export_name(name: str) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "").strip())
    return safe_name.strip("_") or "未知"


@dataclass
class StockMatch:
    code: str
    name: str
    board: str
    pinyin: str
    initials: str
    matched_by: str = ""

    def to_dict(self):
        return {
            "code": self.code,
            "name": self.name,
            "board": self.board,
            "pinyin": self.pinyin,
            "initials": self.initials,
            "matched_by": self.matched_by,
        }


def classify_board(stock_code: str) -> str:
    code = str(stock_code or "").strip()
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def build_stock_search_index(data_dir: str = "data", include_hidden: bool = False) -> List[StockMatch]:
    csv_manager = CSVManager(data_dir)
    stock_names = load_stock_names(data_dir)
    codes = sorted(set(csv_manager.list_all_stocks()) | set(stock_names.keys()))
    matches = []

    for code in codes:
        name = stock_names.get(code) or fallback_stock_name(code)
        if not include_hidden and is_hidden_market_stock(code, name):
            continue
        pinyin, initials = _pinyin_parts(name)
        matches.append(StockMatch(
            code=code,
            name=name,
            board=classify_board(code),
            pinyin=pinyin,
            initials=initials,
        ))

    return matches


def search_stocks(query: str, data_dir: str = "data", limit: int = 20) -> List[dict]:
    keyword = _normalize_query(query)
    if not keyword:
        return []

    results = []
    for item in build_stock_search_index(data_dir=data_dir):
        haystacks = {
            "code": item.code.lower(),
            "name": item.name.lower(),
            "pinyin": item.pinyin,
            "initials": item.initials,
        }

        rank = None
        matched_by = ""
        for key, value in haystacks.items():
            if keyword == value:
                rank = 0
                matched_by = key
                break
        if rank is None:
            for key, value in haystacks.items():
                if value.startswith(keyword):
                    rank = 1 if key in {"code", "initials"} else 2
                    matched_by = key
                    break
        if rank is None:
            for key, value in haystacks.items():
                if keyword in value:
                    rank = 3
                    matched_by = key
                    break

        if rank is not None:
            row = item.to_dict()
            row["matched_by"] = matched_by
            results.append((rank, item.code, row))

    results.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in results[:limit]]


def resolve_stock_query(query: str, data_dir: str = "data") -> Optional[dict]:
    keyword = _normalize_query(query)
    if not keyword:
        return None

    if CSVManager.STOCK_CODE_PATTERN.match(keyword):
        stock_names = load_stock_names(data_dir)
        code = CSVManager.validate_stock_code(keyword)
        name = stock_names.get(code) or fallback_stock_name(code)
        pinyin, initials = _pinyin_parts(name)
        return StockMatch(
            code=code,
            name=name,
            board=classify_board(code),
            pinyin=pinyin,
            initials=initials,
            matched_by="code",
        ).to_dict()

    matches = search_stocks(keyword, data_dir=data_dir, limit=1)
    return matches[0] if matches else None


class StockExportService:
    def __init__(
        self,
        data_dir: str = "data",
        config: Optional[dict] = None,
        tushare_token: Optional[str] = None,
        downloads_dir: Path = DOWNLOADS_DIR,
        provider_name: Optional[str] = None,
    ):
        self.data_dir = data_dir
        self.config = config or load_config_file("config/config.yaml")
        self.provider_name = (provider_name or get_active_provider_name(data_dir)).strip().lower()
        self.storage_dir = provider_data_dir(data_dir, self.provider_name)
        if not self.storage_dir.exists():
            self.storage_dir = Path(data_dir)
        self.csv_manager = CSVManager(self.storage_dir)
        self.downloads_dir = Path(downloads_dir)
        self.tushare_token = (tushare_token or "").strip()

    def resolve(self, query: str) -> dict:
        match = resolve_stock_query(query, data_dir=str(self.storage_dir))
        if not match:
            raise ValueError(f"未找到匹配股票: {query}")
        return match

    def _provider(self):
        token = self.tushare_token
        if self.provider_name == "tushare" and not token:
            token, _ = default_tushare_token(self.config)
        if self.provider_name == "tushare" and not token:
            raise ValueError("未找到默认 Tushare Token，无法校验或更新数据")
        return create_data_provider(
            provider_name=self.provider_name,
            data_dir=self.data_dir,
            config=self.config,
            token=token,
        )

    def assess_freshness(self, code: str, name: str = "") -> dict:
        code = CSVManager.validate_stock_code(code)
        provider = self._provider()
        target = [{"code": code, "name": name or fallback_stock_name(code)}]
        freshness = provider.assess_target_data(target)
        info = freshness.get("status_map", {}).get(code, {})
        return {
            "is_fresh": bool(freshness.get("is_fresh")),
            "latest_trade_date": freshness.get("latest_trade_date"),
            "local_latest_date": info.get("latest_date"),
            "row_count": info.get("row_count", 0),
            "status": info.get("status", "missing"),
            "reason": info.get("reason", ""),
        }

    def update_single_stock(self, code: str, name: str = "") -> dict:
        code = CSVManager.validate_stock_code(code)
        provider = self._provider()
        target = [{"code": code, "name": name or fallback_stock_name(code)}]
        provider.sync_target_data(target, board="all", max_stocks=None, purpose="export")
        self.storage_dir = Path(provider.full_data_dir)
        self.csv_manager = CSVManager(self.storage_dir)
        return self.assess_freshness(code, name=name)

    def _copy_csv(self, code: str, name: str) -> Path:
        source = self.csv_manager.get_stock_path(code, create_dirs=False)
        if not source.exists() or source.stat().st_size == 0:
            raise FileNotFoundError(f"{code} 本地 CSV 不存在，无法导出")

        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        target = self.downloads_dir / f"{code}_{_safe_export_name(name)}.csv"
        shutil.copy2(source, target)
        return target

    def export_stock(self, query: str, update_first: bool = False, force_export: bool = False) -> dict:
        match = self.resolve(query)
        code = match["code"]
        name = match["name"]
        freshness = None

        if update_first:
            freshness = self.update_single_stock(code, name=name)
        elif not force_export:
            freshness = self.assess_freshness(code, name=name)
            if not freshness.get("is_fresh"):
                return {
                    "success": False,
                    "needs_update": True,
                    "code": code,
                    "name": name,
                    "freshness": freshness,
                    "message": (
                        f"{code} {name} 本地数据不是最新。"
                        f"本地最新 {freshness.get('local_latest_date') or '无'}，"
                        f"目标交易日 {freshness.get('latest_trade_date') or '未知'}。"
                    ),
                }

        target = self._copy_csv(code, name)
        return {
            "success": True,
            "needs_update": False,
            "code": code,
            "name": name,
            "path": str(target),
            "freshness": freshness,
            "message": f"已导出到 {target}",
        }
