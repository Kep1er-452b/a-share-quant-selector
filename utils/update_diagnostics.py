"""Tushare update failure diagnostics and atomic error-report enrichment."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd

from utils.csv_manager import CSVManager
from utils.data_provider import create_data_provider, get_config_value
from utils.error_logging import ERROR_DIR, sanitize_for_log
from utils.local_config import load_config_file
from utils.provider_router import load_active_provider, provider_data_dir, warehouse_summary
from utils.tushare_fetcher import TushareProviderError, classify_tushare_error


DIAGNOSTIC_SCHEMA_VERSION = 1
DIAGNOSTIC_MODES = {
    "standard": {"api_limit": 10, "timeout_seconds": 90, "local_limit": 200, "sample_count": 3},
    "extended": {"api_limit": 30, "timeout_seconds": 600, "local_limit": None, "sample_count": 20},
}
_REPORT_LOCK = Lock()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def resolve_update_error_report(reference, error_dir=None) -> Path:
    root = Path(error_dir or ERROR_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    text = str(reference or "latest").strip()
    if text == "latest":
        candidates = sorted(root.glob("*-update-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError("未找到更新错误报告")
        return candidates[0]

    candidate = Path(text)
    if not candidate.is_absolute():
        direct = root / candidate
        matches = list(root.glob(f"*-update-{text}.json")) if direct.suffix != ".json" else []
        candidate = matches[0] if len(matches) == 1 else direct
    resolved = candidate.resolve()
    if resolved.parent != root or resolved.suffix != ".json":
        raise ValueError("错误报告路径必须位于 logs/errors 且为 JSON 文件")
    if not resolved.exists():
        raise FileNotFoundError(f"错误报告不存在: {resolved.name}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"错误报告 JSON 损坏: {resolved.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError("错误报告根节点必须是 JSON 对象")
    return resolved


def _atomic_update_report(path: Path, mutate):
    path = resolve_update_error_report(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _REPORT_LOCK:
        lock_path.touch(exist_ok=True)
        lock_file = lock_path.open("r+")
        try:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("错误报告根节点必须是 JSON 对象")
            diagnostics = payload.setdefault("diagnostics", {})
            diagnostics.setdefault("schema_version", DIAGNOSTIC_SCHEMA_VERSION)
            diagnostics.setdefault("auto_snapshot", {})
            diagnostics.setdefault("runs", [])
            mutate(payload)
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(sanitize_for_log(payload), file, ensure_ascii=False, indent=2, default=str)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(tmp_name, path)
            except Exception:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
                raise
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            lock_file.close()
    return path


def append_diagnostic_run(report_path, run):
    return _atomic_update_report(
        Path(report_path),
        lambda payload: payload["diagnostics"]["runs"].append(sanitize_for_log(run)),
    )


def _read_json_health(path):
    path = Path(path)
    if not path.exists():
        return {"exists": False, "readable": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {"exists": True, "readable": isinstance(payload, dict), "keys": sorted(payload)[:20] if isinstance(payload, dict) else []}
    except Exception as exc:
        return {"exists": True, "readable": False, "error": str(exc)[:500]}


def _metadata_freshness(path, max_age_hours=24):
    health = _read_json_health(path)
    if not health.get("readable"):
        return {**health, "fresh": False}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        updated_at = pd.to_datetime(payload.get("updated_at"), errors="coerce")
        age_hours = (pd.Timestamp.now() - updated_at).total_seconds() / 3600
        return {**health, "age_hours": round(age_hours, 3), "fresh": 0 <= age_hours <= max_age_hours}
    except Exception as exc:
        return {**health, "fresh": False, "error": str(exc)[:500]}


def _diagnosis_from_error(error_message, context):
    samples = (((context or {}).get("provider_context") or {}).get("runtime_diagnostics") or {}).get("error_samples") or []
    if samples:
        code = samples[0].get("code") or "UNKNOWN"
        message = samples[0].get("message") or error_message
    else:
        classified = classify_tushare_error(RuntimeError(error_message or ""))
        code = classified["code"]
        message = error_message
    advice = {
        "TOKEN_MISSING": "配置 TUSHARE_TOKEN 或 config/config_local.yaml 后重试。",
        "TOKEN_INVALID": "在 Tushare 控制台确认 Token，并重新输入，不要把 Token 写入日志。",
        "PERMISSION_DENIED": "检查账号积分和目标接口权限。",
        "RATE_LIMITED": "等待当前频率窗口结束，并降低调用上限。",
        "PROXY_FAILURE": "检查系统代理；可运行标准自检比较环境路线和直连路线。",
        "NETWORK_UNREACHABLE": "检查 DNS、TLS、网络出口及 Tushare 服务可达性。",
        "NETWORK_CIRCUIT_OPEN": "网络连续失败已熔断；修复网络后重新运行标准自检。",
        "SCHEMA_MISMATCH": "检查 Tushare SDK/接口字段变化并升级适配。",
        "EMPTY_RESPONSE": "检查交易日期、权限及接口是否返回空集。",
    }.get(code, "运行标准自检；若仍无法分类，再运行扩展自检。")
    return {"code": code, "summary": str(message or "未知更新错误")[:500], "confidence": "medium", "remediation": advice}


def attach_auto_snapshot(report_path, *, config=None, project_root=None):
    path = resolve_update_error_report(report_path)
    project_root = Path(project_root or Path(__file__).resolve().parent.parent)
    config = config or load_config_file(project_root / "config" / "config.yaml")
    data_root = project_root / str(get_config_value(config, "data_dir", default="data"))
    tushare_dir = provider_data_dir(data_root, "tushare")
    original = json.loads(path.read_text(encoding="utf-8"))
    context = original.get("context") or {}
    token_source = "environment" if os.getenv("TUSHARE_TOKEN") else (
        "local_config" if get_config_value(config, "data_source", "tushare", "token") else "missing"
    )
    try:
        sdk_version = importlib.metadata.version("tushare")
    except importlib.metadata.PackageNotFoundError:
        sdk_version = None
    proxies = urllib.request.getproxies()
    disk = shutil.disk_usage(data_root if data_root.exists() else project_root)
    snapshot = {
        "created_at": _now(),
        "network_calls": 0,
        "original_stage": context.get("stage"),
        "original_provider": context.get("provider"),
        "token": {"present": token_source != "missing", "source": token_source},
        "environment": {
            "python_version": sys.version.split()[0],
            "tushare_version": sdk_version,
            "proxy_configured": any(proxies.get(key) for key in ("http", "https", "all")),
            "proxy_keys": sorted(key for key in ("http", "https", "all") if proxies.get(key)),
        },
        "provider": {
            "active": load_active_provider(data_root),
            "tushare": warehouse_summary(data_root, "tushare"),
        },
        "filesystem": {
            "warehouse_exists": tushare_dir.exists(),
            "warehouse_readable": os.access(tushare_dir, os.R_OK) if tushare_dir.exists() else False,
            "warehouse_writable": os.access(tushare_dir, os.W_OK) if tushare_dir.exists() else False,
            "free_bytes": disk.free,
            "provider_state": _read_json_health(tushare_dir / "provider_state.json"),
            "stock_metadata": _read_json_health(tushare_dir / "tushare_stock_map.json"),
            "stock_metadata_state": _metadata_freshness(tushare_dir / "tushare_stock_map_state.json"),
            "trade_calendar": _read_json_health(tushare_dir / "trade_calendar_cache.json"),
            "heatmap_cache": _read_json_health(tushare_dir / "heatmap_snapshot.json"),
            "index_cache": _read_json_health(tushare_dir / "index_snapshot.json"),
        },
        "primary_diagnosis": _diagnosis_from_error(original.get("error_message"), context),
    }
    _atomic_update_report(path, lambda payload: payload["diagnostics"].__setitem__("auto_snapshot", sanitize_for_log(snapshot)))
    return snapshot


class UpdateDiagnosticRunner:
    def __init__(self, report_path, mode="standard", token=None, *, project_root=None, config_path=None, progress_callback=None):
        if mode not in DIAGNOSTIC_MODES:
            raise ValueError("自检模式必须是 standard 或 extended")
        self.report_path = resolve_update_error_report(report_path)
        self.mode = mode
        self.settings = DIAGNOSTIC_MODES[mode]
        self.project_root = Path(project_root or Path(__file__).resolve().parent.parent)
        self.config = load_config_file(config_path or (self.project_root / "config" / "config.yaml"))
        configured_token = get_config_value(self.config, "data_source", "tushare", "token")
        self.token_source = "temporary" if token else ("environment" if os.getenv("TUSHARE_TOKEN") else ("config" if configured_token else "missing"))
        self.token = (token or os.getenv("TUSHARE_TOKEN") or configured_token or "").strip()
        self.progress_callback = progress_callback
        self.checks = []
        self.started_monotonic = None
        self.provider = None

    def _progress(self, step, current, total):
        if self.progress_callback:
            self.progress_callback({"current_step": step, "processed_count": current, "total_count": total})

    def _check(self, check_id, category, status, summary, evidence=None, remediation=None):
        item = {
            "id": check_id,
            "category": category,
            "status": status,
            "summary": summary,
            "evidence": evidence or {},
            "remediation": remediation,
        }
        self.checks.append(item)
        return item

    def _api_calls(self):
        if self.provider is None:
            return 0
        return sum(value for key, value in self.provider.get_runtime_stats().items() if str(key).endswith(".calls"))

    def _assert_budget(self):
        if time.monotonic() - self.started_monotonic > self.settings["timeout_seconds"]:
            raise TimeoutError(f"{self.mode} 自检超过 {self.settings['timeout_seconds']} 秒预算")
        if self._api_calls() >= self.settings["api_limit"]:
            raise RuntimeError(f"{self.mode} 自检达到 {self.settings['api_limit']} 次API调用上限")

    @staticmethod
    def _sample_codes(stock_map, mode):
        groups = {
            "main_sh": sorted(code for code, info in stock_map.items() if code.startswith("60")),
            "main_sz": sorted(code for code, info in stock_map.items() if code.startswith("00")),
            "chinext": sorted(code for code, info in stock_map.items() if code.startswith("30")),
            "star": sorted(code for code, info in stock_map.items() if code.startswith("68")),
        }
        missing_groups = [key for key, codes in groups.items() if not codes]
        if missing_groups:
            raise TushareProviderError(
                f"本地股票元数据缺少板块样本: {', '.join(missing_groups)}",
                code="LOCAL_REPOSITORY_CORRUPT",
                endpoint="stock_basic",
            )
        if mode == "standard":
            return [groups["main_sh"][0], groups["chinext"][0], groups["star"][0]]
        return [code for key in ("main_sh", "main_sz", "chinext", "star") for code in groups[key][:5]]

    @staticmethod
    def _validate_price_frame(frame):
        required = {"trade_date", "open", "high", "low", "close", "vol", "amount", "adj_factor"}
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return "empty"
        missing = sorted(required - set(frame.columns))
        if missing:
            return f"missing:{','.join(missing)}"
        numeric = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        invalid = (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)) | (
            numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)
        )
        return "invalid_ohlc" if invalid.any() else "ok"

    def _scan_local_csv(self):
        data_root = self.project_root / str(get_config_value(self.config, "data_dir", default="data"))
        data_dir = provider_data_dir(data_root, "tushare")
        files = sorted(data_dir.glob("[0-9][0-9]/*.csv"))
        limit = self.settings["local_limit"]
        targets = files if limit is None else files[:limit]
        failures = []
        manager = CSVManager(data_dir)
        for index, path in enumerate(targets, 1):
            if index % 50 == 0 and time.monotonic() - self.started_monotonic > self.settings["timeout_seconds"]:
                raise TimeoutError(f"{self.mode} 自检超过 {self.settings['timeout_seconds']} 秒预算")
            try:
                frame = manager.read_stock(path.stem)
                manager._validate_stock_dataframe(frame)
                dates = pd.to_datetime(frame["date"], errors="coerce")
                if dates.isna().any() or not dates.is_monotonic_decreasing or dates.duplicated().any():
                    raise ValueError("日期无效、非降序或重复")
                numeric = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
                if (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)).any():
                    raise ValueError("high 小于 OHLC 其他值")
                if (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)).any():
                    raise ValueError("low 大于 OHLC 其他值")
                required_numeric = frame[["open", "high", "low", "close", "volume", "amount", "turnover", "market_cap"]]
                if required_numeric.isna().any().any():
                    raise ValueError("关键数值字段存在缺失值")
                market_cap = pd.to_numeric(frame["market_cap"], errors="coerce")
                if (market_cap < 0).any() or (market_cap.dropna().median() if not market_cap.dropna().empty else 0) < 1_000_000:
                    raise ValueError("market_cap 疑似不是元单位或存在负值")
            except Exception as exc:
                if len(failures) < 20:
                    failures.append({"stock_code": path.stem, "error": str(exc)[:500]})
            if index % 200 == 0:
                self._progress("扫描本地 Tushare CSV", index, len(targets))
        status = "failed" if failures else "passed"
        self._check(
            "local_csv_contract",
            "local_data",
            status,
            f"检查 {len(targets)} 份CSV，发现 {len(failures)} 份异常",
            {"checked": len(targets), "total": len(files), "failures": failures},
            "根据异常股票代码修复或重新全量抓取。" if failures else None,
        )

    def run(self, trigger="cli"):
        diagnostic_id = uuid.uuid4().hex[:12]
        started_at = _now()
        self.started_monotonic = time.monotonic()
        run = {
            "diagnostic_id": diagnostic_id,
            "mode": self.mode,
            "trigger": trigger,
            "started_at": started_at,
            "status": "running",
            "api_budget": {"limit": self.settings["api_limit"], "actual": 0},
            "checks": self.checks,
        }
        try:
            if not self.token:
                raise TushareProviderError("未找到 Tushare Token", code="TOKEN_MISSING", endpoint="config")
            self.provider = create_data_provider(
                "tushare",
                data_dir=str(self.project_root / str(get_config_value(self.config, "data_dir", default="data"))),
                config=self.config,
                token=self.token,
            )
            self.provider.token_source = self.token_source
            self._progress("执行 Tushare 核心接口预检", 0, self.settings["sample_count"] + 2)
            preflight = self.provider.run_preflight()
            self._check("tushare_preflight", "remote_api", "passed", "Tushare 核心接口预检通过", preflight)
            if self.provider._preflight_stock_basic_df.empty:
                stock_map = self.provider._load_stock_metadata()
            else:
                stock_map = self.provider._stock_basic_maps(self.provider._preflight_stock_basic_df)[1]
            sample_codes = self._sample_codes(stock_map, self.mode)
            remote_frames = {}
            today = datetime.now().date()
            for index, code in enumerate(sample_codes, 1):
                self._assert_budget()
                info = stock_map[code]
                frame = self.provider._call_pro_bar(
                    ts_code=info.get("ts_code") or self.provider._to_ts_code(code),
                    asset="E",
                    freq="D",
                    adj="qfq",
                    adjfactor=True,
                    start_date=(today - timedelta(days=45)).strftime("%Y%m%d"),
                    end_date=today.strftime("%Y%m%d"),
                )
                result = self._validate_price_frame(frame)
                status = "passed" if result == "ok" else "failed"
                self._check(
                    f"pro_bar_{code}",
                    "remote_data",
                    status,
                    f"{code} pro_bar {result}",
                    {"rows": len(frame) if isinstance(frame, pd.DataFrame) else 0},
                    "检查接口权限、字段变化或个股状态。" if status == "failed" else None,
                )
                if isinstance(frame, pd.DataFrame):
                    remote_frames[code] = frame
                self._progress("验证跨板块行情样本", index, len(sample_codes))

            data_root = self.project_root / str(get_config_value(self.config, "data_dir", default="data"))
            local_manager = CSVManager(provider_data_dir(data_root, "tushare"))
            overlap_evidence = []
            for code, remote_frame in remote_frames.items():
                local_frame = local_manager.read_stock(code)
                local_dates = set(pd.to_datetime(local_frame.get("date"), errors="coerce").dropna().dt.strftime("%Y%m%d"))
                remote_dates = set(pd.to_datetime(remote_frame.get("trade_date"), errors="coerce").dropna().dt.strftime("%Y%m%d"))
                overlap = sorted(local_dates & remote_dates)
                overlap_evidence.append({"stock_code": code, "overlap_days": len(overlap), "latest_overlap": overlap[-1] if overlap else None})
            missing_overlap = [item for item in overlap_evidence if item["overlap_days"] == 0]
            self._check(
                "remote_local_date_overlap",
                "data_consistency",
                "failed" if missing_overlap else "passed",
                f"{len(overlap_evidence) - len(missing_overlap)}/{len(overlap_evidence)} 个样本存在远程/本地日期重叠",
                {"samples": overlap_evidence},
                "检查本地仓库日期漂移或重新抓取对应股票。" if missing_overlap else None,
            )
            self._scan_local_csv()

            if self.mode == "extended":
                self._assert_budget()
                universe = [
                    {
                        "code": code,
                        "name": info.get("name", ""),
                        "board": self.provider.classify_board(code, info),
                        "list_date": info.get("list_date"),
                    }
                    for code, info in sorted(stock_map.items())
                ]
                assessment = self.provider.assess_target_data(universe)
                summary = assessment.get("summary") or {}
                suspicious = summary.get("full_refresh", 0) > max(len(universe) * 0.1, 100)
                self._check(
                    "update_plan_dry_run",
                    "planner",
                    "warning" if suspicious else "passed",
                    f"只读更新规划: {summary}",
                    {"latest_trade_date": assessment.get("latest_trade_date"), "summary": summary},
                    "大量全量重抓时先检查本地CSV契约和复权锚点。" if suspicious else None,
                )

            failed = [check for check in self.checks if check["status"] == "failed"]
            warnings = [check for check in self.checks if check["status"] == "warning"]
            status = "failed" if failed else ("warning" if warnings else "passed")
            primary = failed[0] if failed else (warnings[0] if warnings else self.checks[0])
            run.update({
                "status": status,
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "api_budget": {"limit": self.settings["api_limit"], "actual": self._api_calls()},
                "primary_diagnosis": {
                    "code": primary["id"],
                    "summary": primary["summary"],
                    "confidence": "high" if failed else "medium",
                    "remediation": primary.get("remediation"),
                },
                "provider_diagnostics": self.provider.get_runtime_diagnostics(),
            })
        except Exception as exc:
            classification = classify_tushare_error(exc)
            code = getattr(exc, "code", None) or classification["code"]
            self._check(
                "diagnostic_runtime",
                classification["category"],
                "failed",
                str(exc),
                {"error_type": type(exc).__name__, "code": code},
                _diagnosis_from_error(str(exc), {}).get("remediation"),
            )
            run.update({
                "status": "failed",
                "finished_at": _now(),
                "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "api_budget": {"limit": self.settings["api_limit"], "actual": self._api_calls()},
                "primary_diagnosis": {
                    "code": code,
                    "summary": str(exc)[:500],
                    "confidence": "high" if code != "UNKNOWN" else "low",
                    "remediation": _diagnosis_from_error(str(exc), {}).get("remediation"),
                },
            })
        append_diagnostic_run(self.report_path, run)
        return run


def run_update_diagnostics(report_reference, mode="standard", token=None, *, project_root=None, config_path=None, progress_callback=None, trigger="cli"):
    runner = UpdateDiagnosticRunner(
        resolve_update_error_report(report_reference),
        mode=mode,
        token=token,
        project_root=project_root,
        config_path=config_path,
        progress_callback=progress_callback,
    )
    return runner.run(trigger=trigger)
