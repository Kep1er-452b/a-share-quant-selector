"""
数据源抽象与公共工具
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from utils.csv_manager import CSVManager
from utils.progress import ProgressTracker


BOARD_LABELS = {
    "all": "全市场",
    "main": "主板",
    "chinext": "创业板",
    "star": "科创板",
}


class DataProviderError(RuntimeError):
    """数据源相关错误"""


class BaseDataProvider:
    """数据源基类，封装通用的股票池、续抓和状态管理逻辑"""

    provider_name = "base"
    required_columns = {"date", "open", "high", "low", "close", "volume", "amount", "turnover", "market_cap"}

    def __init__(self, data_dir: str = "data"):
        self.csv_manager = CSVManager(data_dir)
        self.full_data_dir = Path(data_dir)
        self.stock_names_file = self.full_data_dir / "stock_names.json"
        self.fetch_state_file = self.full_data_dir / "fetch_state.json"

    def _load_local_stock_names(self) -> Dict[str, str]:
        """从本地文件加载股票名称"""
        if self.stock_names_file.exists():
            try:
                with open(self.stock_names_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_stock_names(self, stock_dict: Dict[str, str]) -> None:
        """保存股票名称到本地"""
        try:
            with open(self.stock_names_file, "w", encoding="utf-8") as f:
                json.dump(stock_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存股票名称失败: {e}")

    def _load_fetch_state(self) -> dict:
        if self.fetch_state_file.exists():
            try:
                with open(self.fetch_state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"profiles": {}}

    def _save_fetch_state(self, state: dict) -> None:
        try:
            with open(self.fetch_state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存抓取状态失败: {e}")

    def _profile_key(self, board: str, max_stocks=None) -> str:
        limit = "all" if not max_stocks else str(max_stocks)
        return f"{self.provider_name}:{board}:{limit}"

    def get_all_stock_codes(self, max_retries: int = 3) -> Dict[str, str]:
        raise NotImplementedError

    def fetch_stock_history(self, stock_code: str, years: int = 6) -> Optional[pd.DataFrame]:
        raise NotImplementedError

    def fetch_stock_update(self, stock_code: str, days: int = 10) -> Optional[pd.DataFrame]:
        raise NotImplementedError

    def get_market_caps(self, stock_codes) -> Dict[str, int]:
        """批量获取最新总市值，默认返回空"""
        return {}

    def get_trade_calendar_status(self) -> dict:
        """
        返回交易日历缓存状态。基类默认只提供占位信息。
        """
        return {
            "provider": self.provider_name,
            "cache_available": False,
            "latest_cached_date": None,
            "years": [],
            "source": "fallback",
        }

    def update_trade_calendar_cache(self, years=None) -> dict:
        """
        更新本地交易日历缓存。默认数据源不支持。
        """
        raise DataProviderError(f"{self.provider_name} 数据源暂不支持交易日历缓存更新")

    def classify_board(self, stock_code: str, metadata: Optional[dict] = None) -> str:
        """按元数据优先、代码前缀回退的方式划分板块"""
        metadata = metadata or {}
        market = str(metadata.get("market", "")).strip()
        if market in {"科创板"}:
            return "star"
        if market in {"创业板"}:
            return "chinext"
        if market in {"主板", "中小板"}:
            return "main"

        code = str(stock_code).zfill(6)
        if code.startswith("68"):
            return "star"
        if code.startswith("30"):
            return "chinext"
        return "main"

    def get_stock_universe(self, max_retries: int = 3) -> List[dict]:
        """
        返回包含板块信息的股票池。
        默认用股票列表构造，provider 可以覆写以提供更完整元数据。
        """
        stock_dict = self.get_all_stock_codes(max_retries=max_retries)
        universe = []
        for code, name in sorted(stock_dict.items()):
            universe.append({
                "code": str(code).zfill(6),
                "name": name,
                "board": self.classify_board(code),
                "market": None,
            })
        return universe

    def get_target_universe(self, board: str = "all", max_stocks=None, max_retries: int = 3) -> List[dict]:
        universe = self.get_stock_universe(max_retries=max_retries)
        if board != "all":
            universe = [item for item in universe if item.get("board") == board]
        universe = sorted(universe, key=lambda item: item["code"])
        if max_stocks:
            universe = universe[:max_stocks]
        return universe

    def get_latest_trade_date(self):
        """
        获取最近一个应视为“已完成收盘”的交易日。
        基类使用工作日+15:00 的保守估算，provider 可覆写为真实交易日。
        """
        now = datetime.now()
        latest = now.date()
        if now.time() < datetime.strptime("15:00", "%H:%M").time():
            latest -= timedelta(days=1)
        while latest.weekday() >= 5:
            latest -= timedelta(days=1)
        return latest

    def get_trade_dates_between(self, start_date, end_date) -> List:
        """
        获取给定区间内的交易日列表（含起止边界）。
        基类使用工作日近似，provider 可覆写为真实交易所日历。
        """
        start = pd.to_datetime(start_date).date() if start_date else None
        end = pd.to_datetime(end_date).date() if end_date else None
        if not start or not end or start > end:
            return []
        return list(pd.bdate_range(start=start, end=end).date)

    def get_missing_trade_dates(self, latest_local_date, latest_trade_date) -> List:
        """
        获取本地最新日期之后，到目标最新交易日之间仍需补齐的交易日。
        """
        start = pd.to_datetime(latest_local_date).date() if latest_local_date else None
        end = pd.to_datetime(latest_trade_date).date() if latest_trade_date else None
        if not start or not end or start >= end:
            return []
        next_day = start + timedelta(days=1)
        return self.get_trade_dates_between(next_day, end)

    def _quick_row_count(self, path: Path) -> int:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return max(sum(1 for _ in f) - 1, 0)
        except Exception:
            return 0

    def _inspect_local_stock(self, stock_code: str, latest_trade_date) -> dict:
        path = self.csv_manager.get_stock_path(stock_code)
        latest_trade_date = pd.to_datetime(latest_trade_date).date() if latest_trade_date else None
        result = {
            "code": stock_code,
            "path": str(path),
            "exists": path.exists() and path.stat().st_size > 0 if path.exists() else False,
            "status": "missing",
            "latest_date": None,
            "row_count": 0,
            "reason": "",
        }

        if not path.exists():
            result["reason"] = "csv_missing"
            return result

        if path.stat().st_size == 0:
            result["status"] = "full_refresh"
            result["reason"] = "csv_empty"
            return result

        result["row_count"] = self._quick_row_count(path)
        try:
            df_quick = pd.read_csv(path, nrows=3)
        except Exception:
            result["status"] = "full_refresh"
            result["reason"] = "csv_unreadable"
            return result

        if df_quick.empty:
            result["status"] = "full_refresh"
            result["reason"] = "csv_empty_rows"
            return result

        missing_cols = self.required_columns - set(df_quick.columns)
        if missing_cols:
            result["status"] = "full_refresh"
            result["reason"] = f"missing_columns:{','.join(sorted(missing_cols))}"
            return result

        try:
            latest_date = pd.to_datetime(df_quick.iloc[0]["date"]).date()
            result["latest_date"] = latest_date.isoformat()
        except Exception:
            result["status"] = "full_refresh"
            result["reason"] = "invalid_date"
            return result

        if result["row_count"] < 60:
            result["status"] = "full_refresh"
            result["reason"] = f"too_short:{result['row_count']}"
            return result

        if latest_trade_date and latest_date >= latest_trade_date:
            result["status"] = "up_to_date"
            result["reason"] = "latest"
        else:
            result["status"] = "stale"
            result["reason"] = "needs_incremental"
        return result

    def _apply_market_cap_override(self, stock_code: str, df: Optional[pd.DataFrame], market_cap_map: Dict[str, int]):
        if df is not None and not df.empty and stock_code in market_cap_map:
            df["market_cap"] = market_cap_map[stock_code]
        return df

    def _write_profile_state(
        self,
        board: str,
        max_stocks,
        target_universe: List[dict],
        latest_trade_date,
        status_map: Dict[str, dict],
    ):
        state = self._load_fetch_state()
        profiles = state.setdefault("profiles", {})
        profile_key = self._profile_key(board, max_stocks=max_stocks)

        summary = {}
        for info in status_map.values():
            summary[info["status"]] = summary.get(info["status"], 0) + 1

        profiles[profile_key] = {
            "provider": self.provider_name,
            "board": board,
            "board_label": BOARD_LABELS.get(board, board),
            "max_stocks": max_stocks,
            "latest_trade_date": pd.to_datetime(latest_trade_date).date().isoformat() if latest_trade_date else None,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "target_count": len(target_universe),
            "target_codes": [item["code"] for item in target_universe],
            "status_summary": summary,
            "code_status": {
                code: {
                    "status": info.get("status"),
                    "latest_date": info.get("latest_date"),
                    "row_count": info.get("row_count", 0),
                    "reason": info.get("reason", ""),
                }
                for code, info in status_map.items()
            },
        }
        self._save_fetch_state(state)

    def assess_target_data(self, target_universe: List[dict]) -> dict:
        """
        非破坏性检查目标股票池本地数据状态。
        """
        latest_trade_date = self.get_latest_trade_date()
        latest_trade_date = pd.to_datetime(latest_trade_date).date() if latest_trade_date else None

        status_map = {}
        summary = {
            "up_to_date": 0,
            "stale": 0,
            "full_refresh": 0,
        }
        for item in target_universe:
            code = item["code"]
            info = self._inspect_local_stock(code, latest_trade_date)
            status_map[code] = info
            if info["status"] in summary:
                summary[info["status"]] += 1

        return {
            "latest_trade_date": latest_trade_date.isoformat() if latest_trade_date else None,
            "status_map": status_map,
            "summary": summary,
            "is_fresh": summary["stale"] == 0 and summary["full_refresh"] == 0,
        }

    def sync_target_data(
        self,
        target_universe: List[dict],
        board: str = "all",
        max_stocks=None,
        purpose: str = "init",
        progress_callback=None,
        halt_checker=None,
    ):
        """
        智能续抓目标股票池：
        - 缺失/损坏/历史过短 -> 全量抓取
        - 已有但过期 -> 优先增量补齐
        - 已是最新 -> 跳过
        """
        def emit_progress(**payload):
            if progress_callback:
                progress_callback(payload)

        def ensure_not_halted():
            if halt_checker and halt_checker():
                raise InterruptedError("系统已急停")

        if not target_universe:
            print("✗ 目标股票池为空")
            return

        ensure_not_halted()
        latest_trade_date = self.get_latest_trade_date()
        latest_trade_date = pd.to_datetime(latest_trade_date).date() if latest_trade_date else None
        latest_trade_date_str = latest_trade_date.isoformat() if latest_trade_date else "未知"

        status_map = {}
        up_to_date = []
        incremental = []
        full_refresh = []

        for item in target_universe:
            code = item["code"]
            info = self._inspect_local_stock(code, latest_trade_date)
            status_map[code] = info
            if info["status"] == "up_to_date":
                up_to_date.append(item)
            elif info["status"] == "stale":
                incremental.append(item)
            else:
                full_refresh.append(item)

        print("\n📋 目标股票池概览")
        print(f"  板块: {BOARD_LABELS.get(board, board)}")
        print(f"  目标数量: {len(target_universe)} 只")
        print(f"  最新交易日: {latest_trade_date_str}")
        print(f"  已最新: {len(up_to_date)} 只")
        print(f"  需增量补齐: {len(incremental)} 只")
        print(f"  需全量重抓: {len(full_refresh)} 只")
        emit_progress(
            stage="assessment",
            current_step="检查本地数据状态",
            latest_trade_date=latest_trade_date_str,
            target_count=len(target_universe),
            up_to_date_count=len(up_to_date),
            incremental_count=len(incremental),
            full_refresh_count=len(full_refresh),
            processed_count=0,
            total_count=len(incremental) + len(full_refresh),
            progress_pct=0,
            current_stock=None,
        )

        if not incremental and not full_refresh:
            print("✓ 目标股票池已完整且为最新，无需继续抓取")
            self._write_profile_state(board, max_stocks, target_universe, latest_trade_date, status_map)
            emit_progress(
                stage="completed",
                current_step="本地数据已是最新",
                processed_count=0,
                total_count=0,
                progress_pct=100,
                current_stock=None,
            )
            return

        market_cap_targets = [item["code"] for item in incremental + full_refresh]
        market_cap_map = self.get_market_caps(market_cap_targets) if market_cap_targets else {}

        processed = 0
        total_work = len(incremental) + len(full_refresh)
        tracker = ProgressTracker(total_work or 1, label="同步进度")
        success_count = 0
        failed_count = 0
        print("\n开始同步目标股票池数据...")
        print("=" * 60)
        emit_progress(
            stage="sync",
            current_step="开始同步目标股票池数据",
            processed_count=processed,
            total_count=total_work,
            progress_pct=0,
            current_stock=None,
            success_count=success_count,
            failed_count=failed_count,
        )

        for item in incremental:
            ensure_not_halted()
            code = item["code"]
            name = item.get("name", "")
            latest_local = status_map[code].get("latest_date")
            latest_local_date = pd.to_datetime(latest_local).date() if latest_local else None
            missing_trade_dates = self.get_missing_trade_dates(latest_local_date, latest_trade_date)
            trading_days_needed = max(len(missing_trade_dates), 1) if latest_trade_date and latest_local_date else 10

            processed += 1
            current_stock = {"code": code, "name": name}
            progress_prefix = tracker.line(
                processed,
                extra=f"成功 {success_count} | 失败 {failed_count}"
            )
            emit_progress(
                stage="sync",
                current_step="增量补齐",
                processed_count=processed,
                total_count=total_work,
                progress_pct=int((processed / max(total_work, 1)) * 100),
                current_stock=current_stock,
                success_count=success_count,
                failed_count=failed_count,
            )
            print(f"{progress_prefix}\n  -> 增量补齐 {code} {name} (缺 {trading_days_needed} 个交易日)...", end=" ")
            df = self.fetch_stock_update(code, days=min(trading_days_needed, 1000))
            df = self._apply_market_cap_override(code, df, market_cap_map)
            if df is not None and not df.empty:
                self.csv_manager.update_stock(code, df)
                refreshed = self._inspect_local_stock(code, latest_trade_date)
                refreshed["status"] = "incremental_updated"
                refreshed["reason"] = "incremental_ok"
                status_map[code] = refreshed
                success_count += 1
                print("✓")
            else:
                print("↺ 增量失败，转全量重抓")
                full_refresh.append(item)
                total_work += 1
                tracker.total = max(total_work, 1)
                status_map[code]["status"] = "full_refresh"
                status_map[code]["reason"] = "incremental_failed"
            emit_progress(
                stage="sync",
                current_step="增量补齐完成",
                processed_count=processed,
                total_count=total_work,
                progress_pct=int((processed / max(total_work, 1)) * 100),
                current_stock=current_stock,
                success_count=success_count,
                failed_count=failed_count,
            )

            if processed % 10 == 0:
                time.sleep(0.1)

        refresh_queue = []
        seen_codes = set()
        for item in full_refresh:
            if item["code"] not in seen_codes:
                refresh_queue.append(item)
                seen_codes.add(item["code"])

        for item in refresh_queue:
            ensure_not_halted()
            code = item["code"]
            name = item.get("name", "")
            processed += 1
            current_stock = {"code": code, "name": name}
            progress_prefix = tracker.line(
                processed,
                extra=f"成功 {success_count} | 失败 {failed_count}"
            )
            emit_progress(
                stage="sync",
                current_step="全量重抓",
                processed_count=processed,
                total_count=total_work,
                progress_pct=int((processed / max(total_work, 1)) * 100),
                current_stock=current_stock,
                success_count=success_count,
                failed_count=failed_count,
            )
            print(f"{progress_prefix}\n  -> 全量重抓 {code} {name} ...", end=" ")
            df = self.fetch_stock_history(code, years=6)
            df = self._apply_market_cap_override(code, df, market_cap_map)
            if df is not None and not df.empty:
                self.csv_manager.write_stock(code, df)
                refreshed = self._inspect_local_stock(code, latest_trade_date)
                refreshed["status"] = "full_refreshed"
                refreshed["reason"] = "full_refresh_ok"
                status_map[code] = refreshed
                success_count += 1
                print(f"✓ ({len(df)}条)")
            else:
                failed = status_map.get(code, {})
                failed["status"] = "failed"
                failed["reason"] = "fetch_failed"
                status_map[code] = failed
                failed_count += 1
                print("✗")
            emit_progress(
                stage="sync",
                current_step="全量重抓完成",
                processed_count=processed,
                total_count=total_work,
                progress_pct=int((processed / max(total_work, 1)) * 100),
                current_stock=current_stock,
                success_count=success_count,
                failed_count=failed_count,
            )

            if processed % 10 == 0:
                time.sleep(0.1)

        print("=" * 60)
        print(tracker.line(total_work, extra=f"成功 {success_count} | 失败 {failed_count}"))
        final_summary = {}
        for info in status_map.values():
            final_summary[info["status"]] = final_summary.get(info["status"], 0) + 1
        summary_text = " | ".join(f"{k}: {v}" for k, v in sorted(final_summary.items()))
        print(f"同步完成: {summary_text} | 总耗时 {tracker.elapsed_text()}")
        emit_progress(
            stage="completed",
            current_step="目标股票池同步完成",
            processed_count=processed,
            total_count=total_work,
            progress_pct=100,
            current_stock=None,
            success_count=success_count,
            failed_count=failed_count,
            summary=final_summary,
        )

        self._write_profile_state(board, max_stocks, target_universe, latest_trade_date, status_map)

    def init_full_data(self, max_stocks=None, skip_failed: bool = True):
        """
        兼容旧接口：默认按全市场目标股票池执行智能续抓。
        """
        target_universe = self.get_target_universe(board="all", max_stocks=max_stocks)
        self.sync_target_data(target_universe, board="all", max_stocks=max_stocks, purpose="init")

    def daily_update(self, max_stocks=None):
        """
        兼容旧接口：默认按全市场目标股票池执行智能续抓。
        """
        target_universe = self.get_target_universe(board="all", max_stocks=max_stocks)
        self.sync_target_data(target_universe, board="all", max_stocks=max_stocks, purpose="run")


def get_config_value(config: Optional[dict], *keys, default=None):
    """安全读取嵌套配置"""
    current = config or {}
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def create_data_provider(provider_name: str, data_dir: str = "data", config: Optional[dict] = None, token: Optional[str] = None):
    """根据名称创建数据源实例"""
    normalized = (provider_name or "akshare").strip().lower()

    if normalized == "akshare":
        from utils.akshare_fetcher import AKShareFetcher

        return AKShareFetcher(data_dir)

    if normalized == "tushare":
        from utils.tushare_fetcher import TushareFetcher

        resolved_token = (
            token
            or os.getenv("TUSHARE_TOKEN")
            or get_config_value(config, "data_source", "tushare", "token")
        )
        return TushareFetcher(data_dir=data_dir, token=resolved_token)

    raise DataProviderError(f"不支持的数据源: {provider_name}")
