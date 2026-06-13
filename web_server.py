"""
Web 服务器 - A股量化选股系统前端
"""
from flask import Flask, Response, render_template, jsonify, request, send_from_directory, has_request_context
import json
import sys
import socket
import os
import re
import secrets
import signal
import shutil
import subprocess
import time
import uuid
from threading import Event, Lock, Thread, Timer
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote
import yaml
import pandas as pd

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.csv_manager import CSVManager
from utils.data_provider import BOARD_LABELS, create_data_provider, get_config_value, DataProviderError
from utils.error_logging import append_system_log as shared_append_system_log, write_error_report
from utils.config_schema import atomic_write_yaml, validate_strategy_params
from utils.local_config import load_config_file
from utils.market_overview import (
    build_heatmap_payload,
    heatmap_payload_cache_path,
    rebuild_market_caches,
    ensure_market_caches,
    load_market_caches,
    market_cache_health,
    market_cache_needs_refresh,
    is_hidden_market_stock,
)
from utils.provider_router import (
    VALID_PROVIDERS,
    activate_provider,
    active_data_dir as routed_active_data_dir,
    get_active_provider_name,
    legacy_summary,
    list_provider_statuses,
    load_active_provider,
    provider_data_dir,
    warehouse_summary,
)
from utils.runtime_paths import selection_results_dir, wyckoff_results_dir
from utils.selection_worker import (
    build_worker_context,
    initialize_selection_worker,
    process_selection_chunk,
)
from utils.strategy_labels import (
    STRATEGY_GROUPS,
    fallback_stock_name,
    is_invalid_stock_name,
    strategy_ui_metadata,
)
from utils.stock_exporter import (
    StockExportService,
    load_stock_names as load_stock_names_from_dir,
    resolve_stock_query,
    search_stocks,
)
from wyckoff_ai import WyckoffPipeline, has_deepseek_config
from wyckoff_ai.naming import stock_output_folder_name
from wyckoff_ai.pipeline import WyckoffPipelineError
import strategy.strategy_registry as strategy_registry_module
from strategy.strategy_registry import StrategyRegistry
from strategy.formula_strategy import FORMULA_DISPLAY_NAME, FORMULA_STRATEGY_NAME, build_formula_params
from utils.formula_engine import FormulaError

app = Flask(__name__, 
            template_folder='web/templates',
            static_folder='web/static')

# 全局状态
halt_event = Event()
shutdown_event = Event()
WEB_SESSION_TOKEN = secrets.token_urlsafe(32)
selection_jobs = {}
selection_jobs_lock = Lock()
update_jobs = {}
update_jobs_lock = Lock()
update_cancel_events = {}
wyckoff_jobs = {}
wyckoff_jobs_lock = Lock()
watchlist_lock = Lock()

INDEX_KLINE_TARGETS = {
    'sh000001': {'symbol': 'sh000001', 'name': '上证指数'},
    'sz399001': {'symbol': 'sz399001', 'name': '深证成指'},
    'sz399006': {'symbol': 'sz399006', 'name': '创业板指'},
    'sh000688': {'symbol': 'sh000688', 'name': '科创50'},
}
INDEX_KLINE_CACHE_TTL_SECONDS = 15 * 60
LOG_DIR = project_root / "logs"
SYSTEM_LOG_FILE = LOG_DIR / "system.log"
INCIDENT_DIR = LOG_DIR / "incidents"
EMERGENCY_EXIT_DELAY_SECONDS = 1.2
UPDATE_FAILURE_MIN_COVERAGE = 0.20
UPDATE_CACHE_REFRESH_MIN_COVERAGE = 0.90
UPDATE_COVERAGE_GUARD_MIN_TARGETS = 100


def _reload_registry():
    """重新加载策略注册器，确保参数变更立即生效。"""
    global registry
    registry = StrategyRegistry("config/strategy_params.yaml")
    registry.auto_register_from_directory("strategy")
    strategy_registry_module._registry = registry
    return registry


registry = _reload_registry()


def _load_config(config_path="config/config.yaml"):
    return load_config_file(config_path)


def _data_root_dir():
    config = _load_config()
    return Path(str(_config_value(config, 'data_dir', default='data')))


def _active_data_dir():
    return routed_active_data_dir(_data_root_dir())


def _active_provider_name():
    return get_active_provider_name(_data_root_dir())


def _active_csv_manager():
    return CSVManager(_active_data_dir())


def _index_kline_cache_path(data_dir='data'):
    return Path(data_dir) / 'index_kline_cache.json'


def _load_index_kline_cache(data_dir='data'):
    cache_path = _index_kline_cache_path(data_dir)
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, 'r', encoding='utf-8') as file:
            return json.load(file) or {}
    except Exception:
        return {}


def _load_json_file(path, default=None):
    target = Path(path)
    if not target.exists():
        return {} if default is None else default
    try:
        with open(target, 'r', encoding='utf-8') as file:
            return json.load(file) or ({} if default is None else default)
    except Exception:
        return {} if default is None else default


def _save_index_kline_cache(cache, data_dir='data'):
    cache_path = _index_kline_cache_path(data_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as file:
        json.dump(cache, file, ensure_ascii=False, indent=2)


def _cached_index_kline(symbol, data_dir='data'):
    cache = _load_index_kline_cache(data_dir)
    item = cache.get(symbol) or {}
    updated_at = item.get('updated_at')
    if not updated_at:
        return None, cache
    try:
        updated_time = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None, cache
    if (datetime.now() - updated_time).total_seconds() <= INDEX_KLINE_CACHE_TTL_SECONDS:
        return item, cache
    return None, cache


def _fetch_index_kline(symbol, data_dir='data', limit=30):
    target = INDEX_KLINE_TARGETS.get(symbol) or INDEX_KLINE_TARGETS['sh000001']
    cached_item, cache = _cached_index_kline(target['symbol'], data_dir=data_dir)
    if cached_item:
        return {**cached_item, 'from_cache': True}

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError('未安装 akshare，无法获取指数K线') from exc

    source = 'akshare:stock_zh_index_daily_em'
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
    try:
        df = ak.stock_zh_index_daily_em(
            symbol=target['symbol'],
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        try:
            df = _fetch_index_kline_tencent(target['symbol'], max(limit + 5, 35))
            source = 'tencent:appstock/fqkline'
        except Exception:
            df = ak.stock_zh_index_daily(symbol=target['symbol'])
            source = 'akshare:stock_zh_index_daily'

    if df is None or df.empty:
        df = _fetch_index_kline_tencent(target['symbol'], max(limit + 5, 35))
        source = 'tencent:appstock/fqkline'
    if df is None or df.empty:
        raise RuntimeError(f"{target['name']} 暂无K线数据")

    df = df.copy()
    df['date'] = df['date'].astype(str)
    for column in ['open', 'close', 'low', 'high', 'volume', 'amount']:
        if column in df.columns:
            df[column] = df[column].astype(float)
    df = df.sort_values('date').tail(limit)
    candles = [
        {
            'date': row['date'],
            'open': round(float(row['open']), 2),
            'close': round(float(row['close']), 2),
            'low': round(float(row['low']), 2),
            'high': round(float(row['high']), 2),
            'volume': round(float(row.get('volume', 0)), 2),
            'amount': round(float(row.get('amount', 0)), 2),
        }
        for row in df.to_dict('records')
    ]
    payload = {
        'symbol': target['symbol'],
        'name': target['name'],
        'period': 'daily',
        'limit': limit,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': source,
        'candles': candles,
    }
    cache[target['symbol']] = payload
    _save_index_kline_cache(cache, data_dir=data_dir)
    return {**payload, 'from_cache': False}


def _fetch_index_kline_tencent(symbol, limit):
    import requests

    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{limit},qfq'
    response = requests.get(
        url,
        timeout=15,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://stock.finance.qq.com/',
        },
    )
    response.raise_for_status()
    payload = response.json()
    data_level = payload.get('data', {})
    klines = []

    if isinstance(data_level, dict):
        stock_data = data_level.get(symbol, {})
        if isinstance(stock_data, dict):
            klines = stock_data.get('qfqday', []) or stock_data.get('day', [])
    elif isinstance(data_level, list):
        for item in data_level:
            if isinstance(item, list) and len(item) >= 2 and item[0] == symbol and isinstance(item[1], list):
                klines = item[1]
                break

    records = []
    for item in klines:
        if isinstance(item, list) and len(item) >= 6:
            records.append({
                'date': str(item[0]),
                'open': float(item[1]),
                'close': float(item[2]),
                'high': float(item[3]),
                'low': float(item[4]),
                'volume': float(item[5]),
                'amount': 0.0,
            })
    return pd.DataFrame(records)


def _config_value(config, *keys, default=None):
    current = config or {}
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _is_port_available(host, port):
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((probe_host, port)) != 0


def _resolve_web_address(host=None, port=None, auto_port=None, config=None):
    resolved_host = host or _config_value(config, "web", "host", default="127.0.0.1")
    resolved_port = port or _config_value(config, "web", "port", default=5080)
    use_auto_port = auto_port if auto_port is not None else _config_value(config, "web", "auto_port", default=True)

    if _is_port_available(resolved_host, resolved_port):
        return resolved_host, resolved_port

    if not use_auto_port:
        raise OSError(f"端口 {resolved_port} 已被占用")

    for candidate in range(resolved_port + 1, resolved_port + 51):
        if _is_port_available(resolved_host, candidate):
            print(f"⚠️ 端口 {resolved_port} 已被占用，自动切换到 {candidate}")
            return resolved_host, candidate

    raise OSError(f"未找到可用端口，请手动指定 --port")


def _is_halted():
    return halt_event.is_set()


def _halted_response():
    return jsonify({
        'success': False,
        'halted': True,
        'shutdown_requested': shutdown_event.is_set(),
        'error': '系统已急停，Web 服务正在退出或已停止'
    }), 503


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _sanitize_for_log(value):
    sensitive_markers = ("token", "secret", "password", "passwd", "api_key", "apikey", "key")
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in sensitive_markers):
                sanitized[key_text] = "***REDACTED***"
            else:
                sanitized[key_text] = _sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_log(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _append_system_log(event, message, detail=None):
    shared_append_system_log(event, message, detail)


def _compact_job_snapshot(job, fields):
    snapshot = {}
    for field in fields:
        if field in job:
            snapshot[field] = job.get(field)
    if "started_at_monotonic" in job:
        snapshot["elapsed_seconds"] = _elapsed_seconds(job)
    if "logs" in job:
        snapshot["logs"] = list(job.get("logs") or [])[-12:]
    return _sanitize_for_log(snapshot)


def _snapshot_jobs_for_incident():
    common_fields = [
        "job_id", "status", "created_at", "updated_at", "started_at", "finished_at",
        "elapsed_seconds", "error", "progress_pct", "current_step", "current_stock",
    ]
    with update_jobs_lock:
        update_snapshot = [
            _compact_job_snapshot(
                job,
                common_fields + [
                    "provider", "processed_count", "total_count", "cache_refresh",
                    "success_count", "failed_count",
                ],
            )
            for job in update_jobs.values()
        ]
    with selection_jobs_lock:
        selection_snapshot = [
            _compact_job_snapshot(
                job,
                common_fields + [
                    "boards", "strategies", "backend", "total_candidates",
                    "completed_candidates", "valid_stock_count", "skipped_stock_count",
                    "invalid_name_count", "selected_count", "result_time",
                    "selection_report_path",
                ],
            )
            for job in selection_jobs.values()
        ]
    with wyckoff_jobs_lock:
        wyckoff_snapshot = [
            _compact_job_snapshot(
                job,
                [
                    "job_id", "query", "status", "current_step", "message",
                    "progress_pct", "created_at", "updated_at", "error",
                ],
            )
            for job in wyckoff_jobs.values()
        ]

    return {
        "update_jobs": update_snapshot,
        "selection_jobs": selection_snapshot,
        "wyckoff_jobs": wyckoff_snapshot,
    }


def _mark_jobs_emergency_halted(reason):
    timestamp = _job_timestamp()
    with update_jobs_lock:
        for job in update_jobs.values():
            if job.get("status") in {"queued", "running"}:
                job["status"] = "halted"
                job["error"] = reason
                job["finished_at"] = timestamp
                job["updated_at"] = timestamp
                job["elapsed_seconds"] = _elapsed_seconds(job)
                _append_job_log(job, "事故急停触发，系统即将退出。")
    with selection_jobs_lock:
        for job in selection_jobs.values():
            if job.get("status") in {"queued", "running"}:
                job["status"] = "halted"
                job["error"] = reason
                job["updated_at"] = timestamp
                job["elapsed_seconds"] = _elapsed_seconds(job)
                _append_job_log(job, "事故急停触发，系统即将退出。")
    with wyckoff_jobs_lock:
        for job in wyckoff_jobs.values():
            if job.get("status") in {"queued", "running"}:
                job["status"] = "halted"
                job["current_step"] = "事故急停"
                job["message"] = "事故急停触发，系统即将退出。"
                job["error"] = reason
                job["updated_at"] = timestamp


def _write_emergency_incident(reason):
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now()
    incident_id = timestamp.strftime("%Y%m%d-%H%M%S")
    request_snapshot = {}
    if has_request_context():
        request_snapshot = {
            "remote_addr": request.remote_addr,
            "path": request.path,
            "method": request.method,
            "user_agent": request.headers.get("User-Agent"),
        }
    payload = {
        "incident_id": incident_id,
        "type": "emergency_stop",
        "reason": reason,
        "created_at": timestamp.isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "shutdown_delay_seconds": EMERGENCY_EXIT_DELAY_SECONDS,
        "request": request_snapshot,
        "tasks": _snapshot_jobs_for_incident(),
    }
    incident_path = INCIDENT_DIR / f"{incident_id}-emergency-stop.json"
    with open(incident_path, "w", encoding="utf-8") as file:
        json.dump(_sanitize_for_log(payload), file, ensure_ascii=False, indent=2, default=_json_default)
    _append_system_log(
        "emergency_stop",
        "事故急停触发，任务快照已写入，Web 服务即将退出。",
        {"incident_path": str(incident_path), "pid": os.getpid()},
    )
    return incident_path


def _schedule_process_termination(delay_seconds):
    timer = Timer(delay_seconds, _terminate_current_process)
    timer.daemon = True
    timer.start()
    return timer


def _trigger_emergency_stop(reason="用户手动触发事故急停"):
    halt_event.set()
    shutdown_event.set()
    _mark_jobs_emergency_halted(reason)
    incident_path = _write_emergency_incident(reason)
    _schedule_process_termination(EMERGENCY_EXIT_DELAY_SECONDS)
    return incident_path


def _classify_board(stock_code):
    code = str(stock_code or "").strip()
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def _normalize_csv_value(raw_value):
    value = str(raw_value or "").strip().lower()
    return value


def _bounded_text(value, field_name, *, max_length=200, allow_empty=True):
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field_name} 不能为空")
    if len(text) > max_length:
        raise ValueError(f"{field_name} 过长，最多 {max_length} 个字符")
    return text


_SHORT_JOB_ID_RE = re.compile(r"^[0-9a-fA-F]{12}$")


def _validate_job_id(job_id):
    text = str(job_id or "").strip()
    if _SHORT_JOB_ID_RE.fullmatch(text):
        return text.lower()
    try:
        return str(uuid.UUID(text))
    except (TypeError, ValueError):
        raise ValueError("任务 ID 格式无效")


def _parse_requested_boards(raw_value):
    allowed = set(BOARD_LABELS.keys()) - {"all"}
    if raw_value is None or str(raw_value).strip() == "":
        return ["main", "chinext", "star"]
    values = [_normalize_csv_value(item) for item in str(raw_value or "").split(",")]
    selected = [item for item in values if item in allowed]
    if not selected:
        raise ValueError("未选择有效板块")
    return selected


def _parse_requested_strategies(raw_value, allow_empty=False):
    available = [name for name in registry.list_strategies() if name != FORMULA_STRATEGY_NAME]
    if raw_value is None:
        return available
    if str(raw_value).strip() == "":
        if allow_empty:
            return []
        return available

    requested = []
    invalid = []
    for item in str(raw_value).split(","):
        strategy_name = item.strip()
        if len(strategy_name) > 80:
            invalid.append(strategy_name[:80])
            continue
        if strategy_name and strategy_name in registry.strategies:
            requested.append(strategy_name)
        elif strategy_name:
            invalid.append(strategy_name)

    if invalid:
        raise ValueError(f"无效策略: {', '.join(invalid)}")
    if not requested:
        raise ValueError("未选择有效策略")
    return requested


def _parse_formula_spec(raw_value):
    if not isinstance(raw_value, dict) or not raw_value.get('enabled'):
        return None
    formula = _bounded_text(raw_value.get('expression') or raw_value.get('formula'), '条件公式', max_length=2000)
    label = _bounded_text(raw_value.get('name') or raw_value.get('label'), '公式名称', max_length=40) or FORMULA_DISPLAY_NAME
    try:
        return build_formula_params(formula, label)
    except FormulaError as exc:
        raise ValueError(str(exc)) from exc


def _selection_runtime_params(formula_spec):
    if not formula_spec:
        return {}
    return {FORMULA_STRATEGY_NAME: formula_spec}


def _append_formula_strategy(requested_strategies, formula_spec):
    strategies = list(requested_strategies)
    if formula_spec and FORMULA_STRATEGY_NAME not in strategies:
        strategies.append(FORMULA_STRATEGY_NAME)
    return strategies


def _load_stock_names():
    result = load_stock_names_from_dir(str(_data_root_dir()))
    active_names = load_stock_names_from_dir(str(_active_data_dir()))
    result.update(active_names)
    return result


def _markdown_escape(value):
    text = str(value if value is not None else "--")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _signal_extra_metric(signal):
    if signal.get('volume_ratio') is not None:
        return f"量比 {signal.get('volume_ratio')}x"
    if signal.get('yangyin_ratio_57') is not None:
        return f"57阳阴比 {signal.get('yangyin_ratio_57')}"
    if signal.get('yangyin_ratio_14') is not None:
        return f"14阳阴比 {signal.get('yangyin_ratio_14')}"
    if signal.get('hm_short') is not None and signal.get('hm_long') is not None:
        return f"短/长线 {signal.get('hm_short')}/{signal.get('hm_long')}"
    if signal.get('wl') is not None and signal.get('yl') is not None:
        return f"WL/YL {signal.get('wl')}/{signal.get('yl')}"
    return "--"


def _build_selection_markdown(results, time_text, meta=None):
    meta = meta or {}
    strategies = meta.get('strategies') or list(results.keys())
    boards = meta.get('boards') or []
    total_count = sum(len(results.get(strategy_name, [])) for strategy_name in strategies)
    board_text = " / ".join(BOARD_LABELS.get(board, board) for board in boards) or "全部"

    lines = [
        "# 选股内容",
        "",
        f"- 时间: {time_text or _job_timestamp()}",
        f"- 命中信号: {total_count}",
        f"- 策略数量: {len(strategies)}",
        f"- 股票池: {meta.get('stock_pool_size', 0)}",
        f"- 板块: {board_text}",
        "",
    ]

    if not total_count:
        lines.extend(["本次执行未筛出符合条件的股票。", ""])

    for strategy_name in strategies:
        signals = results.get(strategy_name, [])
        lines.extend([
            f"## {strategy_name}",
            "",
            f"命中 {len(signals)} 只",
            "",
        ])
        if not signals:
            lines.extend(["当前策略在本次筛选条件下没有命中信号。", ""])
            continue

        lines.append("| 代码 | 名称 | 板块 | 现价 | J值 | 市值(亿) | 补充指标 | 触发条件 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | --- | --- |")
        for item in signals:
            signal = (item.get('signals') or [{}])[0] if isinstance(item.get('signals'), list) else {}
            reasons = signal.get('reasons') or ["MATCH"]
            reason_text = "、".join(str(reason) for reason in reasons)
            lines.append(
                "| "
                f"{_markdown_escape(item.get('code'))} | "
                f"{_markdown_escape(item.get('name') or '未知')} | "
                f"{_markdown_escape(BOARD_LABELS.get(_classify_board(item.get('code')), _classify_board(item.get('code'))))} | "
                f"{_markdown_escape(signal.get('close'))} | "
                f"{_markdown_escape(signal.get('J'))} | "
                f"{_markdown_escape(signal.get('market_cap'))} | "
                f"{_markdown_escape(_signal_extra_metric(signal))} | "
                f"{_markdown_escape(reason_text)} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _save_selection_markdown(results, time_text, meta=None):
    output_dir = selection_results_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", f"{timestamp}-选股内容").strip("-")
    output_path = output_dir / f"{filename}.md"
    content = _build_selection_markdown(results, time_text, meta=meta)
    output_path.write_text(content, encoding="utf-8")
    return str(output_path)


def _watchlist_path():
    return _data_root_dir() / 'watchlist.json'


def _load_watchlist():
    path = _watchlist_path()
    if not path.exists():
        return {'items': {}}
    try:
        with open(path, 'r', encoding='utf-8') as file:
            payload = json.load(file) or {}
        items = payload.get('items') if isinstance(payload, dict) else {}
        if not isinstance(items, dict):
            items = {}
        return {'items': items}
    except Exception:
        return {'items': {}}


def _save_watchlist(payload):
    path = _watchlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix('.json.tmp')
    with open(temp_path, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def _stock_display_name(code, stock_names):
    return stock_names.get(code) or fallback_stock_name(code)


def _is_fallback_stock_name(code, name):
    return str(name or "").strip() == fallback_stock_name(code)


def _build_board_counts(stock_codes):
    counts = {"main": 0, "chinext": 0, "star": 0}
    for code in stock_codes:
        counts[_classify_board(code)] += 1
    return counts


def _filter_hidden_stock_codes(stock_codes, stock_names=None):
    names = stock_names or {}
    return [
        code for code in stock_codes
        if not is_hidden_market_stock(code, names.get(code, ""))
    ]


def _is_invalid_stock_name(name):
    return is_invalid_stock_name(name)


def _safe_int_arg(name, default, minimum=1, maximum=1000):
    raw_value = request.args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return min(max(value, minimum), maximum)


def _stock_table_row(code, stock_names=None):
    stock_names = stock_names or _load_stock_names()
    df = _active_csv_manager().read_stock_for_analysis(code)
    if df.empty:
        return {
            'code': code,
            'name': _stock_display_name(code, stock_names),
            'board': _classify_board(code),
            'latest_price': None,
            'latest_date': None,
            'market_cap': None,
            'data_count': 0,
        }
    latest = df.iloc[0]
    latest_date = latest['date']
    if hasattr(latest_date, 'strftime'):
        latest_date = latest_date.strftime('%Y-%m-%d')
    return {
        'code': code,
        'name': _stock_display_name(code, stock_names),
        'board': _classify_board(code),
        'latest_price': round(float(latest['close']), 2),
        'latest_date': latest_date,
        'market_cap': round(float(latest.get('market_cap', 0)) / 1e8, 2),
        'data_count': len(df),
    }


def _stock_table_row_from_snapshot(stock, stock_names=None, row_counts=None):
    stock_names = stock_names or {}
    row_counts = row_counts or {}
    code = str(stock.get('code') or '').zfill(6)
    return {
        'code': code,
        'name': stock.get('name') or _stock_display_name(code, stock_names),
        'board': stock.get('board') or _classify_board(code),
        'latest_price': stock.get('latest_price'),
        'latest_date': stock.get('latest_date'),
        'market_cap': round(float(stock.get('market_cap') or 0) / 1e8, 2),
        'data_count': stock.get('data_count') or row_counts.get(code) or '--',
    }


def _load_stock_row_counts(data_dir):
    state_path = Path(data_dir) / 'fetch_state.json'
    payload = _load_json_file(state_path, {}) or {}
    profiles = payload.get('profiles') or {}
    if not profiles:
        return {}

    preferred = None
    for profile in profiles.values():
        if not isinstance(profile, dict):
            continue
        if profile.get('updated_at') and (
            preferred is None
            or str(profile.get('updated_at')) > str(preferred.get('updated_at'))
        ):
            preferred = profile
    if preferred is None:
        preferred = next((item for item in profiles.values() if isinstance(item, dict)), {})

    local_status = preferred.get('local_status') or preferred.get('code_status') or {}
    return {
        str(code).zfill(6): int(info.get('row_count') or 0)
        for code, info in local_status.items()
        if isinstance(info, dict)
    }


def _export_service():
    config = _load_config()
    return StockExportService(
        data_dir=str(_config_value(config, 'data_dir', default='data')),
        config=config,
        provider_name=_active_provider_name(),
    )


def _require_session_token():
    token = request.headers.get("X-Quant-Session", "")
    if token != WEB_SESSION_TOKEN:
        return jsonify({
            'success': False,
            'error': '本地会话令牌无效，请刷新页面后重试',
        }), 403
    return None


def _chunk_candidates(candidates, chunk_size):
    return [candidates[i:i + chunk_size] for i in range(0, len(candidates), chunk_size)]


def _get_web_selection_settings():
    config = _load_config()

    selection_config = config.get('selection', {}) if isinstance(config, dict) else {}

    raw_mode = str(selection_config.get('mode', 'parallel')).strip().lower()
    mode = raw_mode if raw_mode in {'parallel', 'sequential'} else 'parallel'

    # Web 端默认优先线程池，避免请求内频繁拉起进程导致额外开销。
    raw_backend = str(selection_config.get('backend', 'thread')).strip().lower()
    backend = raw_backend if raw_backend in {'process', 'thread', 'sequential'} else 'thread'

    default_workers = min(max(os.cpu_count() or 4, 1), 12)
    try:
        max_workers = int(selection_config.get('max_workers', default_workers))
    except (TypeError, ValueError):
        max_workers = default_workers
    max_workers = max(1, min(max_workers, 32))

    try:
        chunk_size = int(selection_config.get('chunk_size', 50))
    except (TypeError, ValueError):
        chunk_size = 50
    chunk_size = max(10, min(chunk_size, 500))

    return {
        'mode': mode,
        'backend': backend,
        'max_workers': max_workers,
        'chunk_size': chunk_size,
    }


def _resolve_selection_backend(candidate_count, settings):
    if settings['mode'] != 'parallel' or candidate_count <= 1:
        return 'sequential'

    requested_backend = settings['backend']
    if requested_backend == 'sequential':
        return 'sequential'

    if requested_backend == 'process':
        if candidate_count < max(settings['chunk_size'] * 4, 200):
            return 'thread'
        return 'process'

    if candidate_count < max(settings['chunk_size'], 40):
        return 'sequential'
    return 'thread'


def _job_timestamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _elapsed_seconds(job):
    started_at = job.get('started_at_monotonic')
    if started_at is None:
        return 0
    return max(0, int(time.monotonic() - started_at))


def _append_job_log(job, message):
    logs = job.setdefault('logs', [])
    logs.append({
        'time': _job_timestamp(),
        'message': message,
    })
    if len(logs) > 12:
        del logs[:-12]


def _update_job(job_id, **updates):
    with selection_jobs_lock:
        job = selection_jobs.get(job_id)
        if not job:
            return None
        job.update(updates)
        job['updated_at'] = _job_timestamp()
        job['elapsed_seconds'] = _elapsed_seconds(job)
        return job


def _append_job_log_by_id(job_id, message):
    with selection_jobs_lock:
        job = selection_jobs.get(job_id)
        if not job:
            return None
        _append_job_log(job, message)
        job['updated_at'] = _job_timestamp()
        job['elapsed_seconds'] = _elapsed_seconds(job)
        return job


def _serialize_job(job):
    if not job:
        return None
    serialized = {
        key: value
        for key, value in job.items()
        if key not in {'started_at_monotonic'}
    }
    serialized['elapsed_seconds'] = _elapsed_seconds(job)
    return serialized


def _find_running_job():
    with selection_jobs_lock:
        for job in selection_jobs.values():
            if job.get('status') in {'queued', 'running'}:
                return _serialize_job(job)
    return None


def _update_update_job(job_id, **updates):
    with update_jobs_lock:
        job = update_jobs.get(job_id)
        if not job:
            return None
        job.update(updates)
        job['updated_at'] = _job_timestamp()
        job['elapsed_seconds'] = _elapsed_seconds(job)
        return job


def _append_update_job_log(job_id, message):
    with update_jobs_lock:
        job = update_jobs.get(job_id)
        if not job:
            return None
        _append_job_log(job, message)
        job['updated_at'] = _job_timestamp()
        job['elapsed_seconds'] = _elapsed_seconds(job)
        return job


def _find_running_update_job():
    with update_jobs_lock:
        for job in update_jobs.values():
            if job.get('status') in {'queued', 'running'}:
                return _serialize_job(job)
    return None


def _update_cancel_event(job_id):
    with update_jobs_lock:
        return update_cancel_events.get(job_id)


def _create_update_job(provider):
    job_id = uuid.uuid4().hex[:12]
    now = _job_timestamp()
    job = {
        'job_id': job_id,
        'status': 'queued',
        'provider': provider,
        'created_at': now,
        'updated_at': now,
        'started_at_monotonic': time.monotonic(),
        'elapsed_seconds': 0,
        'progress_pct': 0,
        'processed_count': 0,
        'total_count': 0,
        'planned_total_count': 0,
        'retry_count': 0,
        'current_step': '等待执行',
        'current_stock': None,
        'started_at': None,
        'finished_at': None,
        'error': None,
        'error_report_path': None,
        'cancel_requested': False,
        'logs': [],
        'cache_refresh': None,
    }
    _append_job_log(job, '更新任务已创建，等待执行。')
    with update_jobs_lock:
        update_jobs[job_id] = job
        update_cancel_events[job_id] = Event()
    return job_id


def _create_selection_job(requested_boards, requested_strategies, formula_spec=None):
    job_id = uuid.uuid4().hex[:12]
    now = _job_timestamp()
    job = {
        'job_id': job_id,
        'status': 'queued',
        'created_at': now,
        'updated_at': now,
        'started_at_monotonic': time.monotonic(),
        'elapsed_seconds': 0,
        'boards': requested_boards,
        'strategies': requested_strategies,
        'formula': formula_spec,
        'backend': 'thread',
        'progress_pct': 0,
        'total_candidates': 0,
        'completed_candidates': 0,
        'valid_stock_count': 0,
        'skipped_stock_count': 0,
        'invalid_name_count': 0,
        'selected_count': 0,
        'current_stock': None,
        'results': None,
        'result_time': None,
        'selection_report_path': None,
        'error': None,
        'logs': [],
    }
    _append_job_log(job, '任务已创建，等待执行。')
    with selection_jobs_lock:
        selection_jobs[job_id] = job
    return job_id


@app.before_request
def block_requests_after_halt():
    if request.path.startswith('/api/') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        token_error = _require_session_token()
        if token_error:
            return token_error

    allowed_endpoints = {
        'index',
        'static',
        'get_system_status',
        'emergency_stop',
        'system_shutdown',
        'get_selection_job_status',
        'get_update_job_status',
    }
    if request.endpoint in allowed_endpoints:
        return None

    if _is_halted() and request.path.startswith('/api/'):
        return _halted_response()

    return None


def _run_selection_job(job_id, requested_boards, requested_strategies, formula_spec=None):
    try:
        _update_job(
            job_id,
            status='running',
            result_time=None,
            error=None,
        )

        manager = _active_csv_manager()
        stock_codes = [
            code for code in manager.list_all_stocks()
            if _classify_board(code) in requested_boards
        ]
        stock_names = _load_stock_names()
        stock_codes = _filter_hidden_stock_codes(stock_codes, stock_names)

        candidates = []
        invalid_name_count = 0
        for code in stock_codes:
            name = _stock_display_name(code, stock_names)
            if _is_invalid_stock_name(name):
                invalid_name_count += 1
                continue
            candidates.append((code, name))

        data_dir = str(manager.data_dir)
        settings = _get_web_selection_settings()
        settings['backend'] = 'thread'
        backend = _resolve_selection_backend(len(candidates), settings)
        candidate_chunks = _chunk_candidates(candidates, settings['chunk_size'])
        effective_workers = min(settings['max_workers'], max(len(candidate_chunks), 1))
        runtime_strategy_params = _selection_runtime_params(formula_spec)

        _update_job(
            job_id,
            backend=backend,
            total_candidates=len(candidates),
            invalid_name_count=invalid_name_count,
            current_stock=None,
        )
        _append_job_log_by_id(
            job_id,
            f"开始执行，股票池 {len(candidates)} 只，板块 {requested_boards}，策略 {requested_strategies}。"
        )

        results = {strategy_name: [] for strategy_name in requested_strategies}
        completed_candidates = 0
        valid_total_count = 0
        skipped_count = 0
        error_counts = {strategy_name: 0 for strategy_name in requested_strategies}

        def consume_chunk(chunk_result):
            nonlocal completed_candidates, valid_total_count, skipped_count
            completed_candidates += chunk_result.get('processed_count', 0)
            valid_total_count += chunk_result.get('valid_count', 0)
            skipped_count += chunk_result.get('skipped_count', 0)

            for strategy_name in requested_strategies:
                results[strategy_name].extend(
                    chunk_result['results_by_strategy'].get(strategy_name, [])
                )
                error_counts[strategy_name] += chunk_result['error_counts'].get(strategy_name, 0)

            selected_count = sum(len(items) for items in results.values())
            current_stock = None
            if chunk_result.get('last_processed_code'):
                current_stock = {
                    'code': chunk_result.get('last_processed_code'),
                    'name': chunk_result.get('last_processed_name', '未知'),
                }

            progress_pct = int((completed_candidates / max(len(candidates), 1)) * 100)
            _update_job(
                job_id,
                completed_candidates=completed_candidates,
                valid_stock_count=valid_total_count,
                skipped_stock_count=skipped_count,
                selected_count=selected_count,
                current_stock=current_stock,
                progress_pct=progress_pct,
            )
            if current_stock:
                _append_job_log_by_id(
                    job_id,
                    f"已处理 {completed_candidates}/{len(candidates)}，当前至 {current_stock['name']}({current_stock['code']})。"
                )

        if backend == 'thread':
            worker_context = build_worker_context(
                data_dir,
                requested_strategies,
                str(registry.params_file),
                runtime_strategy_params,
            )
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(process_selection_chunk, chunk, "all", False, worker_context)
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    if _is_halted():
                        executor.shutdown(wait=False, cancel_futures=True)
                        _update_job(job_id, status='halted', error='系统已急停')
                        _append_job_log_by_id(job_id, '任务因系统急停而终止。')
                        return
                    consume_chunk(future.result())
        else:
            worker_context = build_worker_context(
                data_dir,
                requested_strategies,
                str(registry.params_file),
                runtime_strategy_params,
            )
            for chunk in candidate_chunks:
                if _is_halted():
                    _update_job(job_id, status='halted', error='系统已急停')
                    _append_job_log_by_id(job_id, '任务因系统急停而终止。')
                    return
                consume_chunk(process_selection_chunk(chunk, "all", False, worker_context))

        for strategy_name in results:
            results[strategy_name] = sorted(results[strategy_name], key=lambda item: item['code'])

        result_time = _job_timestamp()
        report_meta = {
            'boards': requested_boards,
            'strategies': requested_strategies,
            'stock_pool_size': len(candidates),
            'invalid_name_count': invalid_name_count,
            'valid_stock_count': valid_total_count,
            'skipped_stock_count': skipped_count,
            'backend': backend,
            'formula': formula_spec,
        }
        report_path = _save_selection_markdown(results, result_time, report_meta)

        _update_job(
            job_id,
            status='completed',
            progress_pct=100,
            completed_candidates=len(candidates),
            results=results,
            result_time=result_time,
            selection_report_path=report_path,
            current_stock=None,
        )
        _append_job_log_by_id(
            job_id,
            f"执行完成，共命中 {sum(len(items) for items in results.values())} 条信号。"
        )
        _append_job_log_by_id(job_id, f"选股记录已保存: {report_path}")
    except Exception as exc:
        error_report_path = write_error_report(
            'selection',
            exc,
            {'job_id': job_id, 'boards': requested_boards, 'strategies': requested_strategies},
            error_id=job_id,
        )
        _update_job(
            job_id,
            status='error',
            error=str(exc),
            error_report_path=str(error_report_path),
            current_stock=None,
        )
        _append_job_log_by_id(job_id, f"执行失败: {exc}；错误日志: {error_report_path}")


def _emit_update_progress(job_id, payload, phase_offset=0, phase_weight=100):
    total_count = int(payload.get('total_count') or 0)
    processed_count = int(payload.get('processed_count') or 0)
    planned_total_count = int(payload.get('planned_total_count') or total_count)
    retry_count = int(payload.get('retry_count') or max(total_count - planned_total_count, 0))
    raw_progress = int(payload.get('progress_pct') or 0)
    overall_progress = min(100, max(0, phase_offset + int(raw_progress * phase_weight / 100)))
    current_stock = payload.get('current_stock')
    current_step = payload.get('current_step') or '同步数据中'

    _update_update_job(
        job_id,
        progress_pct=overall_progress,
        processed_count=processed_count,
        total_count=total_count,
        planned_total_count=planned_total_count,
        retry_count=retry_count,
        current_step=current_step,
        current_stock=current_stock,
    )


def _refresh_market_caches_for_job(job_id, data_dir):
    _append_update_job_log(job_id, '开始刷新市场缓存。')
    _append_system_log(
        'update_cache_refresh_start',
        '开始刷新市场缓存。',
        {'job_id': job_id, 'data_dir': data_dir},
    )

    def cache_progress(**payload):
        stage = payload.get('stage')
        if stage == 'snapshot':
            _emit_update_progress(job_id, payload, phase_offset=82, phase_weight=12)
            if payload.get('processed_count') in {1, payload.get('total_count')}:
                _append_update_job_log(job_id, payload.get('current_step') or '构建本地云图快照')
        elif stage == 'industry':
            _emit_update_progress(job_id, payload, phase_offset=94, phase_weight=5)
            _append_update_job_log(job_id, payload.get('current_step') or '刷新行业映射')
        elif stage == 'heatmap_payload':
            _emit_update_progress(job_id, payload, phase_offset=99, phase_weight=1)

    cache_result = rebuild_market_caches(
        data_dir=data_dir,
        progress_callback=cache_progress,
        preserve_existing=True,
    )

    if cache_result.get('errors'):
        error_report_path = write_error_report(
            'market_cache',
            RuntimeError('市场缓存刷新存在降级项'),
            {'job_id': job_id, 'data_dir': data_dir, 'errors': cache_result.get('errors')},
            error_id=job_id,
        )
        _append_update_job_log(
            job_id,
            f"缓存刷新完成，但存在降级项: {cache_result['errors']}；错误日志: {error_report_path}"
        )
        _append_system_log(
            'update_cache_refresh_degraded',
            '缓存刷新完成，但存在降级项。',
            {'job_id': job_id, 'errors': cache_result.get('errors'), 'error_report_path': str(error_report_path)},
        )
    else:
        _append_update_job_log(job_id, '市场缓存刷新完成。')
        _append_system_log(
            'update_cache_refresh_completed',
            '市场缓存刷新完成。',
            {'job_id': job_id},
        )

    _update_update_job(job_id, cache_refresh=cache_result.get('errors') or {})


def _provider_update_coverage(summary):
    try:
        return float(summary.get('coverage_ratio') or 0)
    except (TypeError, ValueError):
        return 0.0


def _provider_update_target_count(summary):
    try:
        return int(summary.get('target_count') or 0)
    except (TypeError, ValueError):
        return 0


def _provider_update_stock_count(summary):
    try:
        return int(summary.get('stock_count') or 0)
    except (TypeError, ValueError):
        return 0


def _coverage_guard_active(summary):
    return _provider_update_target_count(summary) >= UPDATE_COVERAGE_GUARD_MIN_TARGETS


def _provider_switch_warnings(data_root, provider, provider_state=None):
    provider_state = provider_state or warehouse_summary(data_root, provider)
    statuses = list_provider_statuses(data_root)
    latest_dates = [
        str(status.get('latest_trade_date'))
        for status in statuses
        if status.get('stock_count') and status.get('latest_trade_date')
    ]
    freshest_date = max(latest_dates) if latest_dates else None
    target_date = provider_state.get('latest_trade_date')
    target_count = _provider_update_target_count(provider_state)
    stock_count = _provider_update_stock_count(provider_state)
    try:
        failed_count = int(provider_state.get('failed_count') or 0)
    except (TypeError, ValueError):
        failed_count = 0
    try:
        warning_count = int(provider_state.get('warning_count') or 0)
    except (TypeError, ValueError):
        warning_count = 0
    coverage_ratio = provider_state.get('coverage_ratio')
    status = str(provider_state.get('status') or '').lower()
    warnings = []

    if not target_date:
        warnings.append('目标数据源缺少最新交易日信息')
    elif freshest_date and str(target_date) < freshest_date:
        warnings.append(f'目标数据源最新交易日 {target_date} 落后于本地最新 {freshest_date}')

    if status in {'failed', 'error'}:
        warnings.append(f'目标数据源状态为 {status.upper()}')
    elif status == 'partial':
        warnings.append('目标数据源最近一次更新状态为 PARTIAL')

    if target_count > 0 and coverage_ratio is not None and _provider_update_coverage(provider_state) < 0.98:
        warnings.append(f'目标数据源覆盖率为 {round(_provider_update_coverage(provider_state) * 100, 2)}%')
    if target_count > 0 and stock_count < target_count:
        warnings.append(f'目标数据源本地股票数 {stock_count} 少于目标股票池 {target_count}')
    if failed_count > 0:
        warnings.append(f'目标数据源有 {failed_count} 条更新失败记录')
    if warning_count > 0:
        warnings.append(f'目标数据源有 {warning_count} 条更新警告记录')

    return warnings


def _run_update_job(job_id, provider_name, provider_token):
    config = _load_config()
    data_dir = str(_config_value(config, 'data_dir', default='data'))
    provider = None
    cancel_event = _update_cancel_event(job_id)

    def is_cancelled():
        return bool(cancel_event and cancel_event.is_set())

    def ensure_update_continues():
        if is_cancelled():
            raise InterruptedError('用户已停止此次更新')
        if _is_halted():
            raise InterruptedError('系统已急停')

    def error_context(stage):
        context = {'job_id': job_id, 'provider': provider_name, 'stage': stage}
        if provider is not None:
            try:
                provider_context = provider.get_error_context()
            except Exception as diagnostic_error:
                context['provider_diagnostics_error'] = str(diagnostic_error)
            else:
                if provider_context:
                    context['provider_context'] = provider_context
        return context

    try:
        ensure_update_continues()

        _update_update_job(
            job_id,
            status='running',
            started_at=_job_timestamp(),
            current_step='准备更新环境',
            error=None,
            progress_pct=1,
        )
        _append_update_job_log(job_id, f'开始执行数据更新，数据源: {provider_name}。')
        _append_system_log(
            'update_job_start',
            f'开始执行数据更新，数据源: {provider_name}。',
            {'job_id': job_id, 'provider': provider_name},
        )

        provider = create_data_provider(
            provider_name=provider_name,
            data_dir=data_dir,
            config=config,
            token=(provider_token or '').strip() or None,
        )
        _update_update_job(
            job_id,
            current_step='获取股票列表',
            progress_pct=2,
        )
        _append_update_job_log(job_id, f'正在获取 {provider_name} 目标股票池。')
        target_universe = provider.get_target_universe(board='all', max_stocks=None)
        ensure_update_continues()
        _update_update_job(
            job_id,
            total_count=len(target_universe),
            current_step='分析目标股票池',
            progress_pct=3,
        )
        _append_update_job_log(job_id, f'目标股票池 {len(target_universe)} 只，准备开始同步。')

        def progress_callback(payload):
            _emit_update_progress(job_id, payload, phase_offset=0, phase_weight=82)
            if payload.get('stage') == 'data_quality':
                stock = payload.get('current_stock') or {}
                message = (
                    f"{payload.get('current_step')}: "
                    f"{stock.get('name', '未知')}({stock.get('code', '--')})"
                )
                _append_update_job_log(job_id, message)
                _append_system_log(
                    'data_quality_adjustment_gap',
                    message,
                    {
                        'job_id': job_id,
                        'provider': provider_name,
                        'stock': stock,
                        'adjustment_gaps': payload.get('adjustment_gaps', []),
                    },
                )
            if payload.get('current_stock') and payload.get('stage') == 'sync':
                stock = payload['current_stock']
                step = payload.get('current_step') or '同步数据中'
                processed = payload.get('processed_count') or 0
                total = payload.get('total_count') or 0
                if processed == 1 or processed == total or processed % 150 == 0:
                    _append_update_job_log(
                        job_id,
                        f"{step}: {stock.get('name', '未知')}({stock.get('code', '--')}) {processed}/{max(total, 1)}"
                    )

        sync_summary = provider.sync_target_data(
            target_universe,
            board='all',
            max_stocks=None,
            purpose='run',
            progress_callback=progress_callback,
            halt_checker=lambda: _is_halted() or is_cancelled(),
        )

        ensure_update_continues()

        provider_state = warehouse_summary(data_dir, provider_name)
        coverage_ratio = _provider_update_coverage(provider_state)
        target_count = _provider_update_target_count(provider_state)
        stock_count = _provider_update_stock_count(provider_state)
        if (
            _coverage_guard_active(provider_state)
            and coverage_ratio < UPDATE_FAILURE_MIN_COVERAGE
        ):
            message = (
                f"本次更新覆盖率过低 ({stock_count}/{target_count})，"
                "疑似网络/代理或数据源连接异常；已跳过缓存刷新，保留当前 active provider。"
            )
            failure_context = error_context('low_coverage')
            failure_context['provider_state'] = provider_state
            error_report_path = write_error_report(
                'update',
                DataProviderError(message),
                failure_context,
                error_id=job_id,
            )
            _append_update_job_log(job_id, message)
            _append_update_job_log(job_id, f'错误日志: {error_report_path}')
            _append_system_log(
                'update_job_failed_low_coverage',
                message,
                {
                    'job_id': job_id,
                    'provider': provider_name,
                    'summary': provider_state,
                    'sync_summary': sync_summary,
                    'error_report_path': str(error_report_path),
                },
            )
            _update_update_job(
                job_id,
                status='failed',
                progress_pct=100,
                current_step='更新失败',
                current_stock=None,
                error=message,
                error_report_path=str(error_report_path),
                finished_at=_job_timestamp(),
                cache_refresh={'skipped': 'low_coverage'},
            )
            return

        provider_dir = str(provider.full_data_dir)
        if (
            _coverage_guard_active(provider_state)
            and coverage_ratio < UPDATE_CACHE_REFRESH_MIN_COVERAGE
        ):
            message = (
                f"本次更新覆盖率不足以刷新市场缓存 ({stock_count}/{target_count})，"
                "已保留旧缓存。"
            )
            _append_update_job_log(job_id, message)
            _append_system_log(
                'update_cache_refresh_skipped',
                message,
                {'job_id': job_id, 'provider': provider_name, 'summary': provider_state},
            )
            _update_update_job(job_id, cache_refresh={'skipped': 'low_coverage'})
        else:
            ensure_update_continues()
            _refresh_market_caches_for_job(job_id, provider_dir)
            ensure_update_continues()
            provider_state = warehouse_summary(data_dir, provider_name)
        switch_allowed = (
            provider_state.get('stock_count', 0) > 0
            and (provider_state.get('coverage_ratio') or 0) >= 0.98
            and (provider_state.get('failed_count') or 0) <= max(5, int((provider_state.get('target_count') or 0) * 0.02))
        )
        if switch_allowed:
            activate_provider(data_dir, provider_name, provider_state)
            _append_update_job_log(job_id, f"active provider 已切换为 {provider_name}。")
            _append_system_log(
                'active_provider_switched',
                f'active provider 已切换为 {provider_name}。',
                {'job_id': job_id, 'provider': provider_name, 'summary': provider_state},
            )
        else:
            _append_update_job_log(job_id, 'active provider 未切换，更新结果未达到覆盖率/失败数阈值。')
            _append_system_log(
                'active_provider_switch_skipped',
                'active provider 未切换，更新结果未达到覆盖率/失败数阈值。',
                {'job_id': job_id, 'provider': provider_name, 'summary': provider_state, 'sync_summary': sync_summary},
            )
        _update_update_job(
            job_id,
            status='completed',
            progress_pct=100,
            current_step='更新完成',
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, '数据更新与缓存重建已完成。')
        _append_system_log(
            'update_job_completed',
            '数据更新与缓存重建已完成。',
            {'job_id': job_id, 'provider': provider_name, 'provider_state': provider_state},
        )
    except InterruptedError as exc:
        if is_cancelled() and not _is_halted():
            message = '用户已停止此次更新；已写入的数据和现有日志均已保留。'
            error_report_path = write_error_report(
                'update',
                exc,
                error_context('cancelled'),
                error_id=job_id,
            )
            _update_update_job(
                job_id,
                status='cancelled',
                current_step='更新已停止',
                error=message,
                error_report_path=str(error_report_path),
                current_stock=None,
                finished_at=_job_timestamp(),
            )
            _append_update_job_log(job_id, f'{message} 错误日志: {error_report_path}')
            _append_system_log(
                'update_job_cancelled',
                message,
                {
                    'job_id': job_id,
                    'provider': provider_name,
                    'error_report_path': str(error_report_path),
                },
            )
        else:
            _update_update_job(
                job_id,
                status='halted',
                error='系统已急停',
                current_stock=None,
                finished_at=_job_timestamp(),
            )
            _append_update_job_log(job_id, '任务因系统急停而终止。')
    except DataProviderError as exc:
        error_report_path = write_error_report(
            'update',
            exc,
            error_context('data_provider'),
            error_id=job_id,
        )
        _update_update_job(
            job_id,
            status='error',
            error=str(exc),
            error_report_path=str(error_report_path),
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, f'更新失败: {exc}；错误日志: {error_report_path}')
        _append_system_log(
            'update_job_error',
            f'更新失败: {exc}',
            {'job_id': job_id, 'provider': provider_name, 'error_report_path': str(error_report_path)},
        )
    except Exception as exc:
        error_report_path = write_error_report(
            'update',
            exc,
            error_context('unexpected'),
            error_id=job_id,
        )
        _update_update_job(
            job_id,
            status='error',
            error=str(exc),
            error_report_path=str(error_report_path),
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, f'更新失败: {exc}；错误日志: {error_report_path}')
        _append_system_log(
            'update_job_error',
            f'更新失败: {exc}',
            {'job_id': job_id, 'provider': provider_name, 'error_report_path': str(error_report_path)},
        )


def _warm_market_caches_background():
    data_dir = str(_active_data_dir())
    try:
        if market_cache_needs_refresh(data_dir=data_dir):
            rebuild_market_caches(data_dir=data_dir, preserve_existing=True)
        else:
            ensure_market_caches(data_dir=data_dir)
        print("✓ 市场云图缓存已就绪")
    except Exception as exc:
        print(f"⚠️ 市场云图缓存预热失败: {exc}")


@app.route('/')
def index():
    """主页"""
    return render_template('index.html', session_token=WEB_SESSION_TOKEN)


@app.route('/api/stocks')
def get_stocks():
    """获取股票列表"""
    try:
        data_dir = str(_active_data_dir())
        stock_names = _load_stock_names()
        requested_board = _normalize_csv_value(request.args.get('board'))

        # 获取每只股票的基本信息 - 支持分页
        page = _safe_int_arg('page', 1, minimum=1, maximum=100000)
        per_page = _safe_int_arg('per_page', 1000, minimum=1, maximum=10000)

        snapshot_stocks = (load_market_caches(data_dir=data_dir).get('snapshot') or {}).get('stocks') or []
        if snapshot_stocks:
            row_counts = _load_stock_row_counts(data_dir)
            rows = [
                _stock_table_row_from_snapshot(stock, stock_names, row_counts)
                for stock in snapshot_stocks
                if not is_hidden_market_stock(stock.get('code'), stock.get('name'))
            ]
            if requested_board in {"main", "chinext", "star"}:
                rows = [row for row in rows if row.get('board') == requested_board]
            rows.sort(key=lambda row: str(row.get('code') or ''))

            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            return jsonify({
                'success': True,
                'data': rows[start_idx:end_idx],
                'total': len(rows),
                'page': page,
                'per_page': per_page,
                'total_pages': (len(rows) + per_page - 1) // per_page,
                'source': 'snapshot',
            })

        manager = _active_csv_manager()
        stocks = manager.list_all_stocks()
        stocks = _filter_hidden_stock_codes(stocks, stock_names)
        if requested_board in {"main", "chinext", "star"}:
            stocks = [code for code in stocks if _classify_board(code) == requested_board]

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_stocks = stocks[start_idx:end_idx]
        
        stock_list = []
        for code in paginated_stocks:
            row = _stock_table_row(code, stock_names)
            if row['data_count']:
                stock_list.append(row)
        
        return jsonify({
            'success': True, 
            'data': stock_list, 
            'total': len(stocks),
            'page': page,
            'per_page': per_page,
            'total_pages': (len(stocks) + per_page - 1) // per_page,
            'source': 'csv',
        })
    except Exception as e:
        error_report_path = write_error_report(
            'selection',
            e,
            {'mode': 'sync_select', 'path': request.path},
        )
        return jsonify({'success': False, 'error': str(e), 'error_report_path': str(error_report_path)})


@app.route('/api/stocks/search')
def search_stock_api():
    """按代码、名称、拼音或首字母搜索股票。"""
    try:
        query = request.args.get('q', '')
        limit = _safe_int_arg('limit', 20, minimum=1, maximum=50)
        data_dir = str(_active_data_dir())
        return jsonify({
            'success': True,
            'data': search_stocks(query, data_dir=data_dir, limit=limit),
        })
    except Exception as e:
        error_report_path = write_error_report(
            'selection',
            e,
            {'mode': 'sync_select', 'path': request.path},
        )
        return jsonify({'success': False, 'error': str(e), 'error_report_path': str(error_report_path)})


STOCK_PERIODS = {
    'daily': {'label': '日K', 'freq': None, 'limit': 160},
    'weekly': {'label': '周K', 'freq': 'W-FRI', 'limit': 160},
    'monthly': {'label': '月K', 'freq': 'ME', 'limit': 120},
}


def _resample_stock_period(df, period):
    period = period if period in STOCK_PERIODS else 'daily'
    if period == 'daily' or df.empty:
        return df.copy(), period

    ascending = df.copy()
    ascending['date'] = pd.to_datetime(ascending['date'], errors='coerce')
    ascending = ascending.dropna(subset=['date']).sort_values('date')
    if ascending.empty:
        return ascending, period

    for column in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turnover', 'market_cap']:
        if column in ascending.columns:
            ascending[column] = pd.to_numeric(ascending[column], errors='coerce')
        else:
            ascending[column] = 0

    grouped = (
        ascending
        .groupby(pd.Grouper(key='date', freq=STOCK_PERIODS[period]['freq']))
        .agg(
            date=('date', 'max'),
            open=('open', 'first'),
            high=('high', 'max'),
            low=('low', 'min'),
            close=('close', 'last'),
            volume=('volume', 'sum'),
            amount=('amount', 'sum'),
            turnover=('turnover', 'sum'),
            market_cap=('market_cap', 'last'),
        )
        .dropna(subset=['date', 'open', 'high', 'low', 'close'])
        .reset_index(drop=True)
    )
    return grouped.sort_values('date', ascending=False).reset_index(drop=True), period


@app.route('/api/stock/<code>')
def get_stock_detail(code):
    """获取单只股票详情"""
    try:
        code = CSVManager.validate_stock_code(code)
        requested_period = _normalize_csv_value(request.args.get('period')) or 'daily'
        stock_names = _load_stock_names()
        stock_name = _stock_display_name(code, stock_names)
        df = _active_csv_manager().read_stock_for_analysis(code)
        if df.empty:
            return jsonify({'success': False, 'error': '股票不存在'})
        df, period = _resample_stock_period(df, requested_period)
        if df.empty:
            return jsonify({'success': False, 'error': '股票周期数据为空'})
        
        # 计算KDJ指标与动态 Min J
        from utils.technical import KDJ, calculate_zhixing_main_overlay
        from strategy.b1_min_j_simple import calculate_min_j
        kdj_df = KDJ(df, n=9, m1=3, m2=3)
        overlay_df = calculate_zhixing_main_overlay(df)
        indicator_df = df.copy()
        indicator_df['K'] = kdj_df['K']
        indicator_df['D'] = kdj_df['D']
        indicator_df['J'] = kdj_df['J']
        min_j = calculate_min_j(indicator_df)
        
        # 转换为列表格式
        data = []
        limit = STOCK_PERIODS.get(period, STOCK_PERIODS['daily'])['limit']
        for i, (_, row) in enumerate(df.head(limit).iterrows()):
            data.append({
                'date': row['date'].strftime('%Y-%m-%d'),
                'open': round(row['open'], 2),
                'high': round(row['high'], 2),
                'low': round(row['low'], 2),
                'close': round(row['close'], 2),
                'volume': int(row['volume']),
                'amount': round(row['amount'] / 1e4, 2),  # 万元
                'turnover': round(row.get('turnover', 0), 2),
                'market_cap': round(row.get('market_cap', 0) / 1e8, 2),  # 总市值，单位：亿
                'K': round(kdj_df.iloc[i]['K'], 2),
                'D': round(kdj_df.iloc[i]['D'], 2),
                'J': round(kdj_df.iloc[i]['J'], 2),
                'MIN_J': round(min_j.iloc[i], 2),
                'ZX_SHORT': round(overlay_df.iloc[i]['ZX_SHORT'], 2),
                'ZX_LONG': round(overlay_df.iloc[i]['ZX_LONG'], 2),
                'UP_SEQ': None if pd.isna(overlay_df.iloc[i]['UP_SEQ']) else int(overlay_df.iloc[i]['UP_SEQ']),
                'UP_SEQ_Y': None if pd.isna(overlay_df.iloc[i]['UP_SEQ_Y']) else round(overlay_df.iloc[i]['UP_SEQ_Y'], 2),
                'DOWN_SEQ': None if pd.isna(overlay_df.iloc[i]['DOWN_SEQ']) else int(overlay_df.iloc[i]['DOWN_SEQ']),
                'DOWN_SEQ_Y': None if pd.isna(overlay_df.iloc[i]['DOWN_SEQ_Y']) else round(overlay_df.iloc[i]['DOWN_SEQ_Y'], 2),
                'VIOLENT_K': bool(overlay_df.iloc[i]['VIOLENT_K']),
                'VIOLENT_K_Y': round(overlay_df.iloc[i]['VIOLENT_K_Y'], 2),
            })
        
        return jsonify({
            'success': True,
            'code': code,
            'name': stock_name,
            'period': period,
            'period_label': STOCK_PERIODS.get(period, STOCK_PERIODS['daily'])['label'],
            'data': data,
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/stock/<code>/export', methods=['POST'])
def export_stock_csv(code):
    """检查/更新/导出单只股票 CSV 到 Downloads。"""
    try:
        if _is_halted():
            return _halted_response()

        code = CSVManager.validate_stock_code(code)
        payload = request.get_json(silent=True) or {}
        mode = _normalize_csv_value(payload.get('mode')) or 'check'
        if mode not in {'check', 'update', 'force'}:
            return jsonify({'success': False, 'error': '不支持的导出模式'}), 400

        service = _export_service()
        result = service.export_stock(
            code,
            update_first=(mode == 'update'),
            force_export=(mode == 'force'),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wyckoff/config')
def get_wyckoff_config():
    """Return Wyckoff AI configuration status without exposing secrets."""
    try:
        config = _load_config()
        return jsonify({
            'success': True,
            'data': {
                'configured': has_deepseek_config(config),
                'provider': 'deepseek',
                'model': 'deepseek-v4-pro',
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


def _wyckoff_outputs_root():
    return wyckoff_results_dir()


def _path_relative_to_project(path):
    target = Path(path)
    if not target.is_absolute():
        target = project_root / target
    try:
        return target.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return target.as_posix()


def _wyckoff_chart_url(chart_path):
    outputs_root = _wyckoff_outputs_root().resolve()
    target = Path(chart_path)
    if not target.is_absolute():
        target = project_root / target
    try:
        relative = target.resolve().relative_to(outputs_root).as_posix()
        return f"/outputs/wyckoff/files/{quote(relative)}"
    except ValueError:
        return f"/outputs/wyckoff/charts/{quote(target.name)}"


def _attach_wyckoff_chart_url(result):
    if isinstance(result, dict):
        chart_path = (result.get('paths') or {}).get('chart_path')
        if chart_path:
            result['chart_url'] = _wyckoff_chart_url(chart_path)
    return result


def _wyckoff_run_timestamp(value, fallback_path):
    text = str(value or '').strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y%m%d-%H%M%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(text[:19], fmt).strftime('%Y%m%d-%H%M%S')
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(Path(fallback_path).stat().st_mtime).strftime('%Y%m%d-%H%M%S')
    except OSError:
        return datetime.now().strftime('%Y%m%d-%H%M%S')


def _unique_wyckoff_run_dir(stock_dir, timestamp):
    candidate = stock_dir / timestamp
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        suffixed = stock_dir / f"{timestamp}-{index}"
        if not suffixed.exists():
            return suffixed
        index += 1


def _unique_file_path(path):
    if not path.exists():
        return path
    index = 2
    stem = path.stem
    suffix = path.suffix
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _legacy_wyckoff_stem(path, suffix):
    name = Path(path).name
    return name[:-len(suffix)] if name.endswith(suffix) else None


def organize_wyckoff_cache():
    """Move legacy flat Wyckoff artifacts into stock/run folders."""
    root = _wyckoff_outputs_root()
    legacy_dirs = {
        'analysis_path': root / 'json',
        'chart_path': root / 'charts',
        'debug_path': root / 'debug',
    }
    if not any(path.exists() for path in legacy_dirs.values()):
        return {'moved_runs': 0, 'moved_files': 0, 'orphaned_files': 0, 'skipped': 0}

    suffixes = {
        'analysis_path': '-analysis.json',
        'chart_path': '-chart.png',
        'debug_path': '-debug.txt',
    }
    grouped = {}
    for kind, directory in legacy_dirs.items():
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file():
                continue
            stem = _legacy_wyckoff_stem(path, suffixes[kind])
            if not stem:
                continue
            grouped.setdefault(stem, {})[kind] = path

    moved_runs = 0
    moved_files = 0
    orphaned_files = 0
    skipped = 0
    for stem, files in grouped.items():
        analysis_path = files.get('analysis_path')
        if not analysis_path:
            skipped += 1
            continue
        try:
            payload = json.loads(analysis_path.read_text(encoding='utf-8'))
        except Exception:
            skipped += 1
            continue

        stock = payload.get('stock') or {}
        code = str(stock.get('code') or '').strip()
        name = str(stock.get('name') or code).strip()
        if not code:
            skipped += 1
            continue

        stock_dir = root / stock_output_folder_name(name, code)
        run_dir = _unique_wyckoff_run_dir(
            stock_dir,
            _wyckoff_run_timestamp(payload.get('generated_at'), analysis_path),
        )
        new_paths = {
            'analysis_path': run_dir / 'json' / analysis_path.name,
            'chart_path': run_dir / 'charts' / files.get('chart_path', Path(f'{stem}-chart.png')).name,
            'debug_path': run_dir / 'debug' / files.get('debug_path', Path(f'{stem}-debug.txt')).name,
        }
        for target in new_paths.values():
            target.parent.mkdir(parents=True, exist_ok=True)

        for kind, source in files.items():
            target = new_paths.get(kind)
            if not target:
                continue
            shutil.move(str(source), str(target))
            moved_files += 1

        payload.setdefault('paths', {})
        for key, target in new_paths.items():
            if target.exists():
                payload['paths'][key] = _path_relative_to_project(target)
        payload['paths']['run_dir'] = _path_relative_to_project(run_dir)
        payload['paths']['stock_dir'] = _path_relative_to_project(stock_dir)
        new_paths['analysis_path'].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        moved_runs += 1

    for kind, directory in legacy_dirs.items():
        if not directory.exists():
            continue
        subdir = {
            'analysis_path': 'json',
            'chart_path': 'charts',
            'debug_path': 'debug',
        }[kind]
        for source in list(directory.iterdir()):
            if not source.is_file():
                continue
            timestamp = _wyckoff_run_timestamp('', source)
            target = _unique_file_path(root / '_orphaned' / timestamp / subdir / source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            orphaned_files += 1

    for directory in legacy_dirs.values():
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()

    return {'moved_runs': moved_runs, 'moved_files': moved_files, 'orphaned_files': orphaned_files, 'skipped': skipped}


def cleanup_wyckoff_cache_keep_latest():
    """Keep only the newest run folder for each stock folder."""
    root = _wyckoff_outputs_root()
    organize_result = organize_wyckoff_cache()
    kept_runs = 0
    deleted_runs = 0
    deleted_files = 0
    stock_dirs = [
        path for path in root.iterdir()
        if path.is_dir() and path.name not in {'charts', 'json', 'debug'} and not path.name.startswith('_')
    ] if root.exists() else []
    for stock_dir in stock_dirs:
        run_dirs = [path for path in stock_dir.iterdir() if path.is_dir()]
        if len(run_dirs) <= 1:
            kept_runs += len(run_dirs)
            continue
        run_dirs.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
        kept_runs += 1
        for old_run in run_dirs[1:]:
            deleted_files += sum(1 for item in old_run.rglob('*') if item.is_file())
            shutil.rmtree(old_run)
            deleted_runs += 1
    return {
        **organize_result,
        'kept_runs': kept_runs,
        'deleted_runs': deleted_runs,
        'deleted_files': deleted_files,
    }


@app.route('/api/wyckoff/analyze', methods=['POST'])
def analyze_wyckoff_stock():
    """Run a single-stock Wyckoff AI analysis against local CSV data."""
    try:
        if _is_halted():
            return _halted_response()

        payload = request.get_json(silent=True) or {}
        query = _normalize_csv_value(payload.get('query'))
        if not query:
            return jsonify({'success': False, 'error': '请输入股票代码、名称或拼音'}), 400

        config = _load_config()
        data_dir = str(_active_data_dir())
        pipeline = WyckoffPipeline(
            config=config,
            data_dir=data_dir,
            output_dir=_wyckoff_outputs_root(),
        )
        result = pipeline.analyze_stock(query)
        return jsonify(_attach_wyckoff_chart_url(result))
    except WyckoffPipelineError as e:
        error_report_path = write_error_report(
            'wyckoff',
            e,
            {'query': query, 'path': request.path},
        )
        return jsonify({'success': False, 'error': str(e), 'error_report_path': str(error_report_path)}), 400
    except Exception as e:
        error_report_path = write_error_report(
            'wyckoff',
            e,
            {'query': query, 'path': request.path},
        )
        return jsonify({'success': False, 'error': str(e), 'error_report_path': str(error_report_path)}), 500


def _update_wyckoff_job(job_id, **updates):
    with wyckoff_jobs_lock:
        job = wyckoff_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _run_wyckoff_job(job_id, query):
    try:
        _update_wyckoff_job(
            job_id,
            status='running',
            current_step='任务启动',
            message='威科夫任务已启动，正在准备分析环境。',
            progress_pct=1,
        )

        def progress_callback(payload):
            _update_wyckoff_job(
                job_id,
                status='running',
                current_step=payload.get('step') or 'running',
                message=payload.get('message') or '任务进行中',
                progress_pct=int(payload.get('progress_pct') or 0),
            )

        config = _load_config()
        data_dir = str(_active_data_dir())
        pipeline = WyckoffPipeline(
            config=config,
            data_dir=data_dir,
            output_dir=_wyckoff_outputs_root(),
        )
        result = pipeline.analyze_stock(query, progress_callback=progress_callback)
        _attach_wyckoff_chart_url(result)
        _update_wyckoff_job(
            job_id,
            status='done',
            current_step='完成',
            message='威科夫分析完成，图表与文件已保存。',
            progress_pct=100,
            result=result,
        )
    except WyckoffPipelineError as e:
        error_report_path = write_error_report(
            'wyckoff',
            e,
            {'job_id': job_id, 'query': query, 'stage': 'pipeline'},
            error_id=job_id,
        )
        _update_wyckoff_job(
            job_id,
            status='error',
            current_step='失败',
            message=f"{e}；错误日志: {error_report_path}",
            error=str(e),
            error_report_path=str(error_report_path),
        )
    except Exception as e:
        error_report_path = write_error_report(
            'wyckoff',
            e,
            {'job_id': job_id, 'query': query, 'stage': 'unexpected'},
            error_id=job_id,
        )
        _update_wyckoff_job(
            job_id,
            status='error',
            current_step='失败',
            message=f"{e}；错误日志: {error_report_path}",
            error=str(e),
            error_report_path=str(error_report_path),
        )


@app.route('/api/wyckoff/start', methods=['POST'])
def start_wyckoff_stock():
    """Start a single-stock Wyckoff AI analysis job."""
    try:
        if _is_halted():
            return _halted_response()

        payload = request.get_json(silent=True) or {}
        query = _normalize_csv_value(payload.get('query'))
        if not query:
            return jsonify({'success': False, 'error': '请输入股票代码、名称或拼音'}), 400

        job_id = str(uuid.uuid4())
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with wyckoff_jobs_lock:
            wyckoff_jobs[job_id] = {
                'job_id': job_id,
                'query': query,
                'status': 'queued',
                'current_step': '排队',
                'message': '威科夫任务已进入队列。',
                'progress_pct': 0,
                'created_at': now,
                'updated_at': now,
                'result': None,
                'error': None,
            }
        thread = Thread(target=_run_wyckoff_job, args=(job_id, query), daemon=True)
        thread.start()
        return jsonify({'success': True, 'job_id': job_id, 'data': wyckoff_jobs[job_id]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wyckoff/status/<job_id>')
def get_wyckoff_job_status(job_id):
    """Return current Wyckoff analysis job progress."""
    try:
        if _is_halted():
            return _halted_response()
        with wyckoff_jobs_lock:
            job = wyckoff_jobs.get(job_id)
            if not job:
                return jsonify({'success': False, 'error': '威科夫任务不存在或已过期'}), 404
        return jsonify({'success': True, 'data': dict(job)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wyckoff/cache/organize', methods=['POST'])
def organize_wyckoff_cache_endpoint():
    """Organize legacy Wyckoff cache artifacts into stock/run folders."""
    try:
        if _is_halted():
            return _halted_response()
        result = organize_wyckoff_cache()
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wyckoff/cache/cleanup', methods=['POST'])
def cleanup_wyckoff_cache_endpoint():
    """Delete old Wyckoff run folders, keeping the newest run per stock."""
    try:
        if _is_halted():
            return _halted_response()
        result = cleanup_wyckoff_cache_keep_latest()
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/wyckoff/reveal', methods=['POST'])
def reveal_wyckoff_file():
    """Reveal a generated Wyckoff artifact in Finder."""
    try:
        if _is_halted():
            return _halted_response()

        payload = request.get_json(silent=True) or {}
        raw_path = _normalize_csv_value(payload.get('path'))
        if not raw_path:
            return jsonify({'success': False, 'error': '缺少文件路径'}), 400

        outputs_root = _wyckoff_outputs_root().resolve()
        target = Path(raw_path)
        if not target.is_absolute():
            target = project_root / target
        target = target.resolve()
        if outputs_root not in target.parents and target != outputs_root:
            return jsonify({'success': False, 'error': '只能打开威科夫分析结果目录下的文件'}), 400
        if not target.exists():
            return jsonify({'success': False, 'error': f'文件不存在: {target}'}), 404

        subprocess.run(['open', '-R', str(target)], check=False)
        return jsonify({'success': True, 'path': str(target)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/outputs/wyckoff/charts/<path:filename>')
def serve_wyckoff_chart(filename):
    """Serve legacy flat Wyckoff chart PNG files."""
    charts_dir = _wyckoff_outputs_root() / 'charts'
    return send_from_directory(charts_dir, filename)


@app.route('/outputs/wyckoff/files/<path:filename>')
def serve_wyckoff_file(filename):
    """Serve generated Wyckoff files from nested output folders."""
    outputs_root = _wyckoff_outputs_root().resolve()
    target = (outputs_root / filename).resolve()
    if outputs_root not in target.parents and target != outputs_root:
        return jsonify({'success': False, 'error': '只能读取威科夫分析结果目录下的文件'}), 400
    return send_from_directory(outputs_root, filename)


@app.route('/api/dashboard-pulse')
def get_dashboard_pulse():
    """获取首页市场强弱快照。"""
    try:
        data_dir = str(_active_data_dir())
        data_root = str(_data_root_dir())
        payload = build_heatmap_payload(data_dir=data_dir, scope='all', metric='daily', refresh=False)
        health = market_cache_health(data_dir=data_dir)
        groups = payload.get('groups', []) or []
        industry_groups = [{
            'name': group.get('name'),
            'change_pct': group.get('change_pct'),
            'median_change_pct': group.get('median_change_pct'),
            'stock_count': group.get('stock_count', 0),
            'up_count': group.get('up_count', 0),
            'down_count': group.get('down_count', 0),
            'flat_count': group.get('flat_count', 0),
            'market_cap': group.get('market_cap', 0),
        } for group in groups]
        ranked_groups = [
            group for group in industry_groups
            if group.get('change_pct') is not None
        ]
        leaders = sorted(ranked_groups, key=lambda item: item.get('change_pct', 0), reverse=True)[:3]
        laggards = sorted(ranked_groups, key=lambda item: item.get('change_pct', 0))[:3]
        return jsonify({
            'success': True,
            'data': {
                'latest_date': payload.get('latest_date'),
                'stock_count': payload.get('stock_count'),
                'group_count': payload.get('group_count'),
                'ticker_stats': payload.get('ticker_stats', {}),
                'leaders': leaders,
                'laggards': laggards,
                'industry_groups': industry_groups,
                'header_indices': payload.get('header_indices', []),
                'cache_health': health,
                'active_provider': load_active_provider(data_root),
                'provider_statuses': list_provider_statuses(data_root),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/provider/activate', methods=['POST'])
def activate_data_provider():
    """手动切换当前 active provider，不触发数据下载。"""
    try:
        if _is_halted():
            return _halted_response()

        running_update = _find_running_update_job()
        if running_update:
            return jsonify({
                'success': False,
                'error': '当前有数据更新任务正在执行，请等待完成后再切换数据源',
                'job': running_update,
            }), 409

        running_selection = _find_running_job()
        if running_selection:
            return jsonify({
                'success': False,
                'error': '当前有选股任务正在执行，请等待完成后再切换数据源',
                'job': running_selection,
            }), 409

        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400

        provider = _normalize_csv_value(payload.get('provider'))
        if provider not in VALID_PROVIDERS:
            return jsonify({'success': False, 'error': '不支持的数据源'}), 400

        data_root = str(_data_root_dir())
        provider_state = warehouse_summary(data_root, provider)
        if _provider_update_stock_count(provider_state) <= 0:
            return jsonify({
                'success': False,
                'error': f'{provider.upper()} 本地数据仓为空，不能切换为当前数据源',
                'data': {
                    'provider_state': provider_state,
                    'active_provider': load_active_provider(data_root),
                    'provider_statuses': list_provider_statuses(data_root),
                },
            }), 400

        warnings = _provider_switch_warnings(data_root, provider, provider_state)
        activate_provider(data_root, provider, provider_state)
        active_state = load_active_provider(data_root)
        _append_system_log(
            'manual_provider_switch',
            f'active provider 已手动切换为 {provider}。',
            {
                'provider': provider,
                'warnings': warnings,
                'provider_state': provider_state,
            },
        )
        return jsonify({
            'success': True,
            'data': {
                'active_provider': active_state,
                'provider_statuses': list_provider_statuses(data_root),
                'provider_state': provider_state,
                'warnings': warnings,
            },
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/select', methods=['POST'])
def run_selection():
    """执行选股"""
    try:
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
        formula_spec = _parse_formula_spec(payload.get('formula'))
        requested_boards = _parse_requested_boards(payload.get('boards'))
        requested_strategies = _parse_requested_strategies(payload.get('strategies'), allow_empty=bool(formula_spec))
        requested_strategies = _append_formula_strategy(requested_strategies, formula_spec)

        manager = _active_csv_manager()
        stock_codes = [
            code for code in manager.list_all_stocks()
            if _classify_board(code) in requested_boards
        ]
        stock_names = _load_stock_names()
        stock_codes = _filter_hidden_stock_codes(stock_codes, stock_names)
        candidates = []
        invalid_name_count = 0
        for code in stock_codes:
            name = _stock_display_name(code, stock_names)
            if _is_invalid_stock_name(name):
                invalid_name_count += 1
                continue
            candidates.append((code, name))

        data_dir = str(manager.data_dir)
        settings = _get_web_selection_settings()
        backend = _resolve_selection_backend(len(candidates), settings)
        candidate_chunks = _chunk_candidates(candidates, settings['chunk_size'])
        effective_workers = min(settings['max_workers'], max(len(candidate_chunks), 1))

        runtime_strategy_params = _selection_runtime_params(formula_spec)

        print(
            f"[web] 开始执行选股: boards={requested_boards}, "
            f"strategies={requested_strategies}, "
            f"候选={len(candidates)}, backend={backend}, workers={effective_workers}, "
            f"chunk={settings['chunk_size']}"
        )

        results = {strategy_name: [] for strategy_name in requested_strategies}
        valid_total_count = 0
        skipped_count = 0
        error_counts = {strategy_name: 0 for strategy_name in requested_strategies}

        def consume_chunk(chunk_result):
            nonlocal valid_total_count, skipped_count
            valid_total_count += chunk_result.get('valid_count', 0)
            skipped_count += chunk_result.get('skipped_count', 0)

            for strategy_name in requested_strategies:
                results[strategy_name].extend(
                    chunk_result['results_by_strategy'].get(strategy_name, [])
                )
                error_counts[strategy_name] += chunk_result['error_counts'].get(strategy_name, 0)

        if backend == 'process':
            with ProcessPoolExecutor(
                max_workers=effective_workers,
                initializer=initialize_selection_worker,
                initargs=(data_dir, requested_strategies, str(registry.params_file), runtime_strategy_params),
            ) as executor:
                futures = [
                    executor.submit(process_selection_chunk, chunk, "all", False)
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    if _is_halted():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return _halted_response()
                    consume_chunk(future.result())
        elif backend == 'thread':
            worker_context = build_worker_context(
                data_dir,
                requested_strategies,
                str(registry.params_file),
                runtime_strategy_params,
            )
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(process_selection_chunk, chunk, "all", False, worker_context)
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    if _is_halted():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return _halted_response()
                    consume_chunk(future.result())
        else:
            worker_context = build_worker_context(
                data_dir,
                requested_strategies,
                str(registry.params_file),
                runtime_strategy_params,
            )
            for chunk in candidate_chunks:
                if _is_halted():
                    return _halted_response()
                consume_chunk(process_selection_chunk(chunk, "all", False, worker_context))

        for strategy_name in results:
            results[strategy_name] = sorted(results[strategy_name], key=lambda item: item['code'])

        result_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        meta = {
            'boards': requested_boards,
            'strategies': requested_strategies,
            'stock_pool_size': len(candidates),
            'invalid_name_count': invalid_name_count,
            'valid_stock_count': valid_total_count,
            'skipped_stock_count': skipped_count,
            'backend': backend,
            'formula': formula_spec,
        }
        report_path = _save_selection_markdown(results, result_time, meta)

        print(
            f"[web] 选股完成: valid={valid_total_count}, skipped={skipped_count}, "
            f"invalid_name={invalid_name_count}, "
            f"selected={sum(len(items) for items in results.values())}, "
            f"errors={error_counts}"
        )

        return jsonify({
            'success': True,
            'data': results,
            'time': result_time,
            'selection_report_path': report_path,
            'meta': meta,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/select/start', methods=['POST'])
def start_selection_job():
    """启动异步选股任务。"""
    try:
        if _is_halted():
            return _halted_response()

        running_job = _find_running_job()
        if running_job:
            return jsonify({
                'success': False,
                'error': '已有选股任务正在执行',
                'job': running_job,
            }), 409

        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
        formula_spec = _parse_formula_spec(payload.get('formula'))
        requested_boards = _parse_requested_boards(payload.get('boards'))
        requested_strategies = _parse_requested_strategies(payload.get('strategies'), allow_empty=bool(formula_spec))
        requested_strategies = _append_formula_strategy(requested_strategies, formula_spec)

        job_id = _create_selection_job(requested_boards, requested_strategies, formula_spec)
        thread = Thread(
            target=_run_selection_job,
            args=(job_id, requested_boards, requested_strategies, formula_spec),
            daemon=True,
        )
        thread.start()

        return jsonify({
            'success': True,
            'job_id': job_id,
            'data': _serialize_job(selection_jobs.get(job_id)),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/select/status/<job_id>')
def get_selection_job_status(job_id):
    """查询异步选股任务状态。"""
    try:
        job_id = _validate_job_id(job_id)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    with selection_jobs_lock:
        job = selection_jobs.get(job_id)
        if not job:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        return jsonify({
            'success': True,
            'data': _serialize_job(job),
        })


@app.route('/api/selection/options')
def get_selection_options():
    """获取选股页需要的板块和策略选项。"""
    try:
        stock_names = _load_stock_names()
        stock_codes = _filter_hidden_stock_codes(_active_csv_manager().list_all_stocks(), stock_names)
        board_counts = _build_board_counts(stock_codes)
        boards = [
            {
                'key': board_key,
                'label': BOARD_LABELS[board_key],
                'count': board_counts.get(board_key, 0),
            }
            for board_key in ['main', 'chinext', 'star']
        ]

        strategies = []
        for strategy_name, strategy in registry.strategies.items():
            if strategy_name == FORMULA_STRATEGY_NAME:
                continue
            ui = strategy_ui_metadata(strategy_name)
            strategies.append({
                'name': strategy_name,
                'display_name': ui['label'],
                'description': ui['description'],
                'group': ui['group'],
                'order': ui['order'],
                'param_count': len(strategy.params or {}),
                'params': strategy.params or {},
            })
        group_order = {item['key']: item['order'] for item in STRATEGY_GROUPS}
        strategies.sort(key=lambda item: (
            group_order.get(item['group'], 999),
            item['order'],
            item['name'],
        ))

        return jsonify({
            'success': True,
            'data': {
                'boards': boards,
                'strategy_groups': STRATEGY_GROUPS,
                'strategies': strategies,
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/strategies')
def get_strategies():
    """获取策略列表"""
    try:
        strategies = []
        for name, strategy in registry.strategies.items():
            if name == FORMULA_STRATEGY_NAME:
                continue
            strategies.append({
                'name': name,
                'params': strategy.params
            })
        return jsonify({'success': True, 'data': strategies})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/stats')
def get_stats():
    """获取系统统计信息"""
    try:
        stock_names = _load_stock_names()
        manager = _active_csv_manager()
        stocks = _filter_hidden_stock_codes(manager.list_all_stocks(), stock_names)
        board_counts = _build_board_counts(stocks)
        
        # 计算数据日期范围
        dates = []
        for code in stocks[:50]:  # 采样
            df = manager.read_stock_for_analysis(code)
            if not df.empty:
                dates.append(df.iloc[0]['date'])
        
        latest_date = max(dates).strftime('%Y-%m-%d') if dates else '-'
        
        return jsonify({
            'success': True,
            'data': {
                'total_stocks': len(stocks),
                'latest_date': latest_date,
                'strategies': len(registry.strategies),
                'board_counts': board_counts,
                'halted': _is_halted(),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/index-kline')
def get_index_kline():
    """获取首页指数日K线。"""
    try:
        data_dir = str(_active_data_dir())
        symbol = _normalize_csv_value(request.args.get('symbol')) or 'sh000001'
        if symbol not in INDEX_KLINE_TARGETS:
            symbol = 'sh000001'
        limit = int(request.args.get('limit', 30))
        limit = min(max(limit, 10), 60)
        try:
            payload = _fetch_index_kline(symbol, data_dir=data_dir, limit=limit)
        except Exception as exc:
            cache = _load_index_kline_cache(data_dir)
            cached_payload = cache.get(symbol)
            if cached_payload:
                payload = {**cached_payload, 'from_cache': True, 'stale': True, 'warning': str(exc)}
            else:
                raise
        return jsonify({'success': True, 'data': payload})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/heatmap')
def get_heatmap():
    """获取市场云图数据。"""
    try:
        data_dir = str(_active_data_dir())
        force_refresh = str(request.args.get('refresh') or '').lower() in {'1', 'true', 'yes'}
        scope = _normalize_csv_value(request.args.get('scope')) or 'all'
        if scope not in {'all', 'main', 'chinext', 'star'}:
            scope = 'all'

        metric = _normalize_csv_value(request.args.get('metric')) or 'daily'
        if metric not in {'daily', 'weekly', 'monthly', 'five_day'}:
            metric = 'daily'

        payload_path = heatmap_payload_cache_path(data_dir, scope, metric)
        cache_refs = [
            Path(data_dir) / 'heatmap_snapshot.json',
            Path(data_dir) / 'industry_map.json',
            Path(data_dir) / 'index_snapshot.json',
        ]
        if (
            not force_refresh
            and payload_path.exists()
            and payload_path.stat().st_mtime >= max(
                (path.stat().st_mtime for path in cache_refs if path.exists()),
                default=0,
            )
        ):
            return Response(payload_path.read_text(encoding='utf-8'), mimetype='application/json')

        refresh_errors = {}
        if force_refresh:
            cache_result = rebuild_market_caches(data_dir=data_dir, preserve_existing=True)
            refresh_errors = cache_result.get('errors') or {}
            health = market_cache_health(data_dir=data_dir)
            if health.get('refresh_pending'):
                return jsonify({
                    'success': False,
                    'error': '市场云图刷新后仍不是最新，旧缓存已保留，请检查刷新失败项。',
                    'reason': 'refresh_pending_after_refresh',
                    'data': {
                        'health': health,
                        'errors': refresh_errors,
                    },
                })
        else:
            health = market_cache_health(data_dir=data_dir)
            if health.get('refresh_pending'):
                return jsonify({
                    'success': False,
                    'error': '市场云图缓存不是最新，请先点击“刷新云图”或执行数据更新。',
                    'reason': 'refresh_pending',
                    'data': health,
                })

        if payload_path.exists() and not refresh_errors:
            return Response(payload_path.read_text(encoding='utf-8'), mimetype='application/json')

        payload = build_heatmap_payload(data_dir=data_dir, scope=scope, metric=metric, refresh=False)
        if refresh_errors:
            payload.setdefault('cache_status', {})['errors'] = refresh_errors
            payload.setdefault('cache_status', {})['warning'] = '部分缓存刷新失败，当前云图可能使用保留缓存。'
        return jsonify({'success': True, 'data': payload})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/heatmap/health')
def get_heatmap_health():
    """快速检查市场云图缓存健康状态，不触发重建。"""
    try:
        data_dir = str(_active_data_dir())
        return jsonify({'success': True, 'data': market_cache_health(data_dir=data_dir)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/heatmap/meta')
def get_heatmap_meta():
    """获取市场云图元信息。"""
    try:
        config = _load_config()
        data_dir = str(_active_data_dir())
        cache_bundle = load_market_caches(data_dir=data_dir)
        health = market_cache_health(data_dir=data_dir)
        snapshot_stocks = cache_bundle.get('snapshot', {}).get('stocks', []) or []
        visible_snapshot_stocks = [
            stock for stock in snapshot_stocks
            if not is_hidden_market_stock(stock.get('code'), stock.get('name'))
        ]
        industry_items = cache_bundle.get('industry', {}).get('items', {}) or {}
        industry_mapped_count = sum(
            1 for stock in visible_snapshot_stocks
            if industry_items.get(stock.get('code'))
        )
        industry_unmapped_count = max(len(visible_snapshot_stocks) - industry_mapped_count, 0)
        default_provider = get_config_value(config, 'data_source', 'default_provider', default='akshare')
        has_tushare_token = bool(
            os.getenv('TUSHARE_TOKEN')
            or get_config_value(config, 'data_source', 'tushare', 'token')
        )

        return jsonify({
            'success': True,
            'data': {
                'latest_date': cache_bundle.get('snapshot', {}).get('latest_date'),
                'default_provider': str(default_provider or 'akshare').lower(),
                'has_tushare_token': has_tushare_token,
                'markets': [
                    {'key': 'all', 'label': 'A股全图', 'enabled': True},
                    {'key': 'main', 'label': '上证A股', 'enabled': True},
                    {'key': 'chinext', 'label': '创业板', 'enabled': True},
                    {'key': 'star', 'label': '科创板', 'enabled': True},
                    {
                        'key': 'bse',
                        'label': '北交所A股',
                        'enabled': False,
                        'hint': '当前系统版本并不支持北交所，敬请期待～',
                    },
                ],
                'metrics': [
                    {'key': 'daily', 'label': '日线'},
                    {'key': 'weekly', 'label': '本周以来'},
                    {'key': 'monthly', 'label': '本月以来'},
                    {'key': 'five_day', 'label': '最近五个交易日'},
                ],
                'cache_status': {
                    'snapshot_updated_at': cache_bundle.get('snapshot', {}).get('updated_at'),
                    'industry_updated_at': cache_bundle.get('industry', {}).get('updated_at'),
                    'index_updated_at': cache_bundle.get('indices', {}).get('updated_at'),
                    'industry_mapped_count': industry_mapped_count,
                    'industry_unmapped_count': industry_unmapped_count,
                    'refresh_pending': health.get('refresh_pending'),
                    'local_latest_date': health.get('local_latest_date'),
                    'local_stock_count': health.get('local_stock_count'),
                    'snapshot_stale': health.get('snapshot_stale'),
                    'indices_ready': health.get('indices_ready'),
                    'market_cap_anomaly_count': health.get('market_cap_anomaly_count'),
                    'errors': cache_bundle.get('errors', {}),
                },
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update/options')
def get_update_options():
    """获取 Web 更新数据功能的默认选项。"""
    try:
        config = _load_config()
        default_provider = get_config_value(config, 'data_source', 'default_provider', default='akshare')
        has_tushare_token = bool(
            os.getenv('TUSHARE_TOKEN')
            or get_config_value(config, 'data_source', 'tushare', 'token')
        )
        data_root = str(_config_value(config, 'data_dir', default='data'))
        active_state = load_active_provider(data_root)
        latest_date = ensure_market_caches(
            data_dir=str(_active_data_dir())
        ).get('snapshot', {}).get('latest_date')

        return jsonify({
            'success': True,
            'data': {
                'default_provider': str(default_provider or 'akshare').lower(),
                'has_tushare_token': has_tushare_token,
                'latest_date': latest_date,
                'active_provider': active_state.get('active_provider'),
                'active_provider_state': active_state,
                'providers': list_provider_statuses(data_root),
                'legacy_provider': legacy_summary(data_root),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update/start', methods=['POST'])
def start_update_job():
    """启动异步更新任务。"""
    try:
        if _is_halted():
            return _halted_response()

        running_job = _find_running_update_job()
        if running_job:
            return jsonify({
                'success': False,
                'error': '已有更新任务正在执行',
                'job': running_job,
            }), 409

        running_selection = _find_running_job()
        if running_selection:
            return jsonify({
                'success': False,
                'error': '当前有选股任务正在执行，请等待完成后再更新数据',
                'job': running_selection,
            }), 409

        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
        provider = _normalize_csv_value(payload.get('provider')) or 'akshare'
        if provider not in {'akshare', 'tushare', 'tencent'}:
            return jsonify({'success': False, 'error': '不支持的数据源'}), 400

        tushare_token = _bounded_text(payload.get('tushare_token'), 'Tushare Token', max_length=128)
        job_id = _create_update_job(provider)
        thread = Thread(
            target=_run_update_job,
            args=(job_id, provider, tushare_token),
            daemon=True,
        )
        thread.start()

        with update_jobs_lock:
            job = update_jobs.get(job_id)
        return jsonify({
            'success': True,
            'job_id': job_id,
            'data': _serialize_job(job),
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update/status/<job_id>')
def get_update_job_status(job_id):
    """查询异步更新任务状态。"""
    try:
        job_id = _validate_job_id(job_id)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    with update_jobs_lock:
        job = update_jobs.get(job_id)
        if not job:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        return jsonify({
            'success': True,
            'data': _serialize_job(job),
        })


@app.route('/api/update/cancel/<job_id>', methods=['POST'])
def cancel_update_job(job_id):
    """停止单个更新任务，不触发全局急停并保留任务日志。"""
    try:
        job_id = _validate_job_id(job_id)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400

    with update_jobs_lock:
        job = update_jobs.get(job_id)
        if not job:
            return jsonify({'success': False, 'error': '任务不存在'}), 404

        status = str(job.get('status') or '').lower()
        if status in {'completed', 'error', 'failed', 'halted', 'cancelled'}:
            return jsonify({
                'success': True,
                'message': '任务已结束，现有日志已保留。',
                'data': _serialize_job(job),
            })

        cancel_event = update_cancel_events.get(job_id)
        if cancel_event is None:
            cancel_event = Event()
            update_cancel_events[job_id] = cancel_event
        cancel_event.set()
        job['cancel_requested'] = True
        job['current_step'] = '正在停止此次更新'
        job['updated_at'] = _job_timestamp()
        job['elapsed_seconds'] = _elapsed_seconds(job)
        _append_job_log(job, '用户请求停止此次更新；正在结束任务并保留日志。')
        serialized = _serialize_job(job)

    _append_system_log(
        'update_job_cancel_requested',
        '用户请求停止此次更新。',
        {'job_id': job_id, 'provider': job.get('provider')},
    )
    return jsonify({
        'success': True,
        'message': '停止请求已提交，已写入的数据和日志将保留。',
        'data': serialized,
    })


@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    """获取自选股列表。"""
    try:
        stock_names = _load_stock_names()
        with watchlist_lock:
            payload = _load_watchlist()
            items = payload.get('items', {})

        rows = []
        changed = False
        for code, meta in sorted(items.items(), key=lambda entry: entry[1].get('created_at', '')):
            try:
                code = CSVManager.validate_stock_code(code)
            except ValueError:
                continue
            row = _stock_table_row(code, stock_names)
            stored_name = str(meta.get('name') or '').strip()
            if row.get('name') and (not stored_name or _is_fallback_stock_name(code, stored_name)):
                meta['name'] = row['name']
                meta['updated_at'] = meta.get('updated_at') or _job_timestamp()
                changed = True
            row.update({
                'note': str(meta.get('note') or ''),
                'created_at': meta.get('created_at'),
                'updated_at': meta.get('updated_at'),
            })
            rows.append(row)
        if changed:
            _save_watchlist(payload)

        return jsonify({'success': True, 'data': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/watchlist', methods=['POST'])
def add_watchlist_item():
    """添加或更新自选股。"""
    try:
        if _is_halted():
            return _halted_response()

        payload = request.get_json(silent=True) or {}
        query = _bounded_text(payload.get('query') or payload.get('code'), '股票查询', max_length=80)
        note = _bounded_text(payload.get('note'), '备注', max_length=200)
        if not query:
            return jsonify({'success': False, 'error': '请输入股票代码、名称或拼音首字母'}), 400

        data_dir = str(_active_data_dir())
        match = resolve_stock_query(query, data_dir=data_dir)
        if not match:
            return jsonify({'success': False, 'error': f'未找到匹配股票: {query}'}), 404

        code = CSVManager.validate_stock_code(match['code'])
        now = _job_timestamp()
        with watchlist_lock:
            watchlist = _load_watchlist()
            items = watchlist.setdefault('items', {})
            existing = items.get(code, {})
            items[code] = {
                'code': code,
                'name': match.get('name') or fallback_stock_name(code),
                'note': note if note else existing.get('note', ''),
                'created_at': existing.get('created_at') or now,
                'updated_at': now,
            }
            _save_watchlist(watchlist)

        stock_names = _load_stock_names()
        row = _stock_table_row(code, stock_names)
        row.update({
            'note': items[code].get('note', ''),
            'created_at': items[code].get('created_at'),
            'updated_at': items[code].get('updated_at'),
        })
        return jsonify({'success': True, 'data': row})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/watchlist/batch', methods=['DELETE'])
def remove_watchlist_items():
    """批量移除自选股。"""
    try:
        if _is_halted():
            return _halted_response()

        payload = request.get_json(silent=True) or {}
        raw_codes = payload.get('codes') or []
        if not isinstance(raw_codes, list):
            return jsonify({'success': False, 'error': 'codes 必须是数组'}), 400

        codes = []
        for raw_code in raw_codes:
            try:
                code = CSVManager.validate_stock_code(raw_code)
            except ValueError:
                continue
            if code not in codes:
                codes.append(code)
        if not codes:
            return jsonify({'success': False, 'error': '请选择要删除的自选股'}), 400

        removed_codes = []
        with watchlist_lock:
            watchlist = _load_watchlist()
            items = watchlist.setdefault('items', {})
            for code in codes:
                if items.pop(code, None) is not None:
                    removed_codes.append(code)
            _save_watchlist(watchlist)

        return jsonify({
            'success': True,
            'removed_codes': removed_codes,
            'removed_count': len(removed_codes),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/watchlist/<code>', methods=['DELETE'])
def remove_watchlist_item(code):
    """移除自选股。"""
    try:
        if _is_halted():
            return _halted_response()

        code = CSVManager.validate_stock_code(code)
        with watchlist_lock:
            watchlist = _load_watchlist()
            removed = watchlist.setdefault('items', {}).pop(code, None)
            _save_watchlist(watchlist)
        return jsonify({
            'success': True,
            'removed': bool(removed),
            'code': code,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置"""
    try:
        config_file = Path("config/strategy_params.yaml")
        if config_file.exists():
            import yaml
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            return jsonify({'success': True, 'data': config})
        return jsonify({'success': False, 'error': '配置文件不存在'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/config', methods=['POST'])
def update_config():
    """更新配置"""
    try:
        new_config = request.get_json(silent=True)
        if not isinstance(new_config, dict):
            return jsonify({'success': False, 'error': '配置必须是 JSON/YAML 对象'}), 400

        validation_errors = validate_strategy_params(new_config)
        if validation_errors:
            return jsonify({
                'success': False,
                'error': '配置校验失败',
                'details': validation_errors,
            }), 400
        
        config_file = Path("config/strategy_params.yaml")
        backup_path = atomic_write_yaml(config_file, new_config)
        
        # 重新加载策略
        _reload_registry()
        
        return jsonify({'success': True, 'backup': str(backup_path) if backup_path else None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/config/strategy/<strategy_name>', methods=['POST'])
def update_single_strategy_config(strategy_name):
    """Update one strategy parameter block without discarding unrelated config."""
    try:
        if strategy_name not in registry.strategies or strategy_name == FORMULA_STRATEGY_NAME:
            return jsonify({'success': False, 'error': '策略不存在'}), 404

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('params'), dict):
            return jsonify({'success': False, 'error': 'params 必须是对象'}), 400

        config_file = Path("config/strategy_params.yaml")
        config = {}
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
        config[strategy_name] = payload['params']

        validation_errors = validate_strategy_params(config)
        if validation_errors:
            return jsonify({
                'success': False,
                'error': '配置校验失败',
                'details': validation_errors,
            }), 400

        backup_path = atomic_write_yaml(config_file, config)
        _reload_registry()

        return jsonify({'success': True, 'backup': str(backup_path) if backup_path else None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/formula/validate', methods=['POST'])
def validate_formula_api():
    """Validate a custom selection formula."""
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400
        formula = _bounded_text(payload.get('formula') or payload.get('expression'), '条件公式', max_length=2000)
        params = build_formula_params(formula, payload.get('name') or payload.get('label'))
        return jsonify({
            'success': True,
            'data': {
                'formula': params['formula'],
                'label': params['label'],
            },
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/system_status')
def get_system_status():
    """获取当前系统状态。"""
    return jsonify({
        'success': True,
        'halted': _is_halted(),
        'shutdown_requested': shutdown_event.is_set(),
        'active_provider': load_active_provider(str(_data_root_dir())),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@app.route('/api/emergency_stop', methods=['POST'])
def emergency_stop():
    """触发事故急停：记录现场、阻止新任务，并退出当前 Web 服务进程。"""
    incident_path = _trigger_emergency_stop()
    return jsonify({
        'success': True,
        'halted': True,
        'shutdown_requested': True,
        'shutdown_delay_seconds': EMERGENCY_EXIT_DELAY_SECONDS,
        'incident_path': str(incident_path),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'message': '事故急停已触发，系统正在记录现场并退出'
    })


def _terminate_current_process():
    os.kill(os.getpid(), signal.SIGTERM)


@app.route('/api/system_shutdown', methods=['POST'])
def system_shutdown():
    """关闭当前 Web 服务进程。"""
    halt_event.set()
    shutdown_event.set()
    _schedule_process_termination(0.8)
    return jsonify({
        'success': True,
        'halted': True,
        'shutdown_requested': True,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'message': '系统正在退出，Web 服务进程即将关闭'
    })


def run_web_server(host=None, port=None, debug=False, config=None, auto_port=None):
    """启动Web服务器"""
    config = config or _load_config()
    host, port = _resolve_web_address(host=host, port=port, auto_port=auto_port, config=config)
    allow_lan = bool(_config_value(config, "web", "allow_lan", default=False))
    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_lan:
        raise OSError("Web 默认只允许本机访问；如需局域网访问，请在配置中设置 web.allow_lan: true")
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"🌐 启动Web服务器: http://{display_host}:{port}")
    Thread(target=_warm_market_caches_background, daemon=True).start()
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_web_server(debug=False, config=_load_config())
