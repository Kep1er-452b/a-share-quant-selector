"""
Web 服务器 - A股量化选股系统前端
"""
from flask import Flask, render_template, jsonify, request
import json
import sys
import socket
import os
import secrets
import signal
import time
import uuid
from threading import Event, Lock, Thread, Timer
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta
import yaml
import pandas as pd

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.csv_manager import CSVManager
from utils.data_provider import BOARD_LABELS, create_data_provider, get_config_value, DataProviderError
from utils.config_schema import atomic_write_yaml, validate_strategy_params
from utils.local_config import load_config_file
from utils.market_overview import (
    build_heatmap_payload,
    rebuild_market_caches,
    ensure_market_caches,
    market_cache_needs_refresh,
    is_hidden_market_stock,
)
from utils.selection_worker import (
    build_worker_context,
    initialize_selection_worker,
    process_selection_chunk,
)
from utils.strategy_labels import fallback_stock_name, is_invalid_stock_name
import strategy.strategy_registry as strategy_registry_module
from strategy.strategy_registry import StrategyRegistry

app = Flask(__name__, 
            template_folder='web/templates',
            static_folder='web/static')

# 全局实例
csv_manager = CSVManager("data")
halt_event = Event()
shutdown_event = Event()
WEB_SESSION_TOKEN = secrets.token_urlsafe(32)
selection_jobs = {}
selection_jobs_lock = Lock()
update_jobs = {}
update_jobs_lock = Lock()

INDEX_KLINE_TARGETS = {
    'sh000001': {'symbol': 'sh000001', 'name': '上证指数'},
    'sz399001': {'symbol': 'sz399001', 'name': '深证成指'},
    'sz399006': {'symbol': 'sz399006', 'name': '创业板指'},
    'sh000688': {'symbol': 'sh000688', 'name': '科创50'},
}
INDEX_KLINE_CACHE_TTL_SECONDS = 15 * 60


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
        'error': '系统已急停，重启服务器后方可恢复'
    }), 503


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


def _parse_requested_boards(raw_value):
    allowed = set(BOARD_LABELS.keys()) - {"all"}
    if raw_value is None or str(raw_value).strip() == "":
        return ["main", "chinext", "star"]
    values = [_normalize_csv_value(item) for item in str(raw_value or "").split(",")]
    selected = [item for item in values if item in allowed]
    if not selected:
        raise ValueError("未选择有效板块")
    return selected


def _parse_requested_strategies(raw_value):
    available = registry.list_strategies()
    if not raw_value:
        return available

    requested = []
    invalid = []
    for item in str(raw_value).split(","):
        strategy_name = item.strip()
        if strategy_name and strategy_name in registry.strategies:
            requested.append(strategy_name)
        elif strategy_name:
            invalid.append(strategy_name)

    if invalid:
        raise ValueError(f"无效策略: {', '.join(invalid)}")
    if not requested:
        raise ValueError("未选择有效策略")
    return requested


def _load_stock_names():
    names_file = Path("data/stock_names.json")
    if not names_file.exists():
        return {}

    with open(names_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def _stock_display_name(code, stock_names):
    return stock_names.get(code) or fallback_stock_name(code)


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
        'current_step': '等待执行',
        'current_stock': None,
        'started_at': None,
        'finished_at': None,
        'error': None,
        'logs': [],
        'cache_refresh': None,
    }
    _append_job_log(job, '更新任务已创建，等待执行。')
    with update_jobs_lock:
        update_jobs[job_id] = job
    return job_id


def _create_selection_job(requested_boards, requested_strategies):
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


def _run_selection_job(job_id, requested_boards, requested_strategies):
    try:
        _update_job(
            job_id,
            status='running',
            result_time=None,
            error=None,
        )

        stock_codes = [
            code for code in csv_manager.list_all_stocks()
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

        data_dir = str(csv_manager.data_dir)
        settings = _get_web_selection_settings()
        settings['backend'] = 'thread'
        backend = _resolve_selection_backend(len(candidates), settings)
        candidate_chunks = _chunk_candidates(candidates, settings['chunk_size'])
        effective_workers = min(settings['max_workers'], max(len(candidate_chunks), 1))

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
            worker_context = build_worker_context(data_dir, requested_strategies, str(registry.params_file))
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(process_selection_chunk, chunk, "all", False, worker_context)
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    if _is_halted():
                        _update_job(job_id, status='halted', error='系统已急停')
                        _append_job_log_by_id(job_id, '任务因系统急停而终止。')
                        return
                    consume_chunk(future.result())
        else:
            worker_context = build_worker_context(data_dir, requested_strategies, str(registry.params_file))
            for chunk in candidate_chunks:
                if _is_halted():
                    _update_job(job_id, status='halted', error='系统已急停')
                    _append_job_log_by_id(job_id, '任务因系统急停而终止。')
                    return
                consume_chunk(process_selection_chunk(chunk, "all", False, worker_context))

        for strategy_name in results:
            results[strategy_name] = sorted(results[strategy_name], key=lambda item: item['code'])

        _update_job(
            job_id,
            status='completed',
            progress_pct=100,
            completed_candidates=len(candidates),
            results=results,
            result_time=_job_timestamp(),
            current_stock=None,
        )
        _append_job_log_by_id(
            job_id,
            f"执行完成，共命中 {sum(len(items) for items in results.values())} 条信号。"
        )
    except Exception as exc:
        _update_job(
            job_id,
            status='error',
            error=str(exc),
            current_stock=None,
        )
        _append_job_log_by_id(job_id, f"执行失败: {exc}")


def _emit_update_progress(job_id, payload, phase_offset=0, phase_weight=100):
    total_count = int(payload.get('total_count') or 0)
    processed_count = int(payload.get('processed_count') or 0)
    raw_progress = int(payload.get('progress_pct') or 0)
    overall_progress = min(100, max(0, phase_offset + int(raw_progress * phase_weight / 100)))
    current_stock = payload.get('current_stock')
    current_step = payload.get('current_step') or '同步数据中'

    _update_update_job(
        job_id,
        progress_pct=overall_progress,
        processed_count=processed_count,
        total_count=total_count,
        current_step=current_step,
        current_stock=current_stock,
    )


def _refresh_market_caches_for_job(job_id, data_dir):
    _append_update_job_log(job_id, '开始刷新市场缓存。')

    def cache_progress(payload):
        stage = payload.get('stage')
        if stage == 'snapshot':
            _emit_update_progress(job_id, payload, phase_offset=82, phase_weight=12)
        elif stage == 'industry':
            _emit_update_progress(job_id, payload, phase_offset=94, phase_weight=5)

    cache_result = rebuild_market_caches(
        data_dir=data_dir,
        progress_callback=cache_progress,
        preserve_existing=True,
    )

    if cache_result.get('errors'):
        _append_update_job_log(
            job_id,
            f"缓存刷新完成，但存在降级项: {cache_result['errors']}"
        )
    else:
        _append_update_job_log(job_id, '市场缓存刷新完成。')

    _update_update_job(job_id, cache_refresh=cache_result.get('errors') or {})


def _run_update_job(job_id, provider_name, provider_token):
    config = _load_config()
    data_dir = str(_config_value(config, 'data_dir', default='data'))

    try:
        if _is_halted():
            _update_update_job(job_id, status='halted', error='系统已急停')
            _append_update_job_log(job_id, '任务因系统急停而终止。')
            return

        _update_update_job(
            job_id,
            status='running',
            started_at=_job_timestamp(),
            current_step='准备更新环境',
            error=None,
            progress_pct=1,
        )
        _append_update_job_log(job_id, f'开始执行数据更新，数据源: {provider_name}。')

        provider = create_data_provider(
            provider_name=provider_name,
            data_dir=data_dir,
            config=config,
            token=(provider_token or '').strip() or None,
        )
        target_universe = provider.get_target_universe(board='all', max_stocks=None)
        _update_update_job(
            job_id,
            total_count=len(target_universe),
            current_step='分析目标股票池',
        )
        _append_update_job_log(job_id, f'目标股票池 {len(target_universe)} 只，准备开始同步。')

        def progress_callback(payload):
            _emit_update_progress(job_id, payload, phase_offset=0, phase_weight=82)
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

        provider.sync_target_data(
            target_universe,
            board='all',
            max_stocks=None,
            purpose='run',
            progress_callback=progress_callback,
            halt_checker=_is_halted,
        )

        if _is_halted():
            _update_update_job(job_id, status='halted', error='系统已急停')
            _append_update_job_log(job_id, '任务因系统急停而终止。')
            return

        _refresh_market_caches_for_job(job_id, data_dir)
        _update_update_job(
            job_id,
            status='completed',
            progress_pct=100,
            current_step='更新完成',
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, '数据更新与缓存重建已完成。')
    except InterruptedError:
        _update_update_job(
            job_id,
            status='halted',
            error='系统已急停',
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, '任务因系统急停而终止。')
    except DataProviderError as exc:
        _update_update_job(
            job_id,
            status='error',
            error=str(exc),
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, f'更新失败: {exc}')
    except Exception as exc:
        _update_update_job(
            job_id,
            status='error',
            error=str(exc),
            current_stock=None,
            finished_at=_job_timestamp(),
        )
        _append_update_job_log(job_id, f'更新失败: {exc}')


def _warm_market_caches_background():
    config = _load_config()
    data_dir = str(_config_value(config, 'data_dir', default='data'))
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
        stocks = csv_manager.list_all_stocks()
        stock_names = _load_stock_names()
        stocks = _filter_hidden_stock_codes(stocks, stock_names)

        requested_board = _normalize_csv_value(request.args.get('board'))
        if requested_board in {"main", "chinext", "star"}:
            stocks = [code for code in stocks if _classify_board(code) == requested_board]
        
        # 获取每只股票的基本信息 - 支持分页
        page = _safe_int_arg('page', 1, minimum=1, maximum=100000)
        per_page = _safe_int_arg('per_page', 500, minimum=1, maximum=1000)  # 默认每页500只
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_stocks = stocks[start_idx:end_idx]
        
        stock_list = []
        for code in paginated_stocks:
            df = csv_manager.read_stock(code)
            if not df.empty:
                latest = df.iloc[0]
                stock_list.append({
                    'code': code,
                    'name': _stock_display_name(code, stock_names),
                    'board': _classify_board(code),
                    'latest_price': round(latest['close'], 2),
                    'latest_date': latest['date'].strftime('%Y-%m-%d'),
                    'market_cap': round(latest.get('market_cap', 0) / 1e8, 2),  # 总市值，单位：亿
                    'data_count': len(df)
                })
        
        return jsonify({
            'success': True, 
            'data': stock_list, 
            'total': len(stocks),
            'page': page,
            'per_page': per_page,
            'total_pages': (len(stocks) + per_page - 1) // per_page
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/stock/<code>')
def get_stock_detail(code):
    """获取单只股票详情"""
    try:
        code = CSVManager.validate_stock_code(code)
        df = csv_manager.read_stock(code)
        if df.empty:
            return jsonify({'success': False, 'error': '股票不存在'})
        
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
        for i, (_, row) in enumerate(df.head(100).iterrows()):  # 返回最近100条
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
        
        return jsonify({'success': True, 'code': code, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/dashboard-pulse')
def get_dashboard_pulse():
    """获取首页市场强弱快照。"""
    try:
        config = _load_config()
        data_dir = str(_config_value(config, 'data_dir', default='data'))
        payload = build_heatmap_payload(data_dir=data_dir, scope='all', metric='daily')
        groups = payload.get('groups', []) or []
        ranked_groups = [
            group for group in groups
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
                'header_indices': payload.get('header_indices', []),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/select')
def run_selection():
    """执行选股"""
    try:
        requested_boards = _parse_requested_boards(request.args.get('boards'))
        requested_strategies = _parse_requested_strategies(request.args.get('strategies'))

        stock_codes = [
            code for code in csv_manager.list_all_stocks()
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

        data_dir = str(csv_manager.data_dir)
        settings = _get_web_selection_settings()
        backend = _resolve_selection_backend(len(candidates), settings)
        candidate_chunks = _chunk_candidates(candidates, settings['chunk_size'])
        effective_workers = min(settings['max_workers'], max(len(candidate_chunks), 1))

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
                initargs=(data_dir, requested_strategies, str(registry.params_file)),
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
            worker_context = build_worker_context(data_dir, requested_strategies, str(registry.params_file))
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
            worker_context = build_worker_context(data_dir, requested_strategies, str(registry.params_file))
            for chunk in candidate_chunks:
                if _is_halted():
                    return _halted_response()
                consume_chunk(process_selection_chunk(chunk, "all", False, worker_context))

        for strategy_name in results:
            results[strategy_name] = sorted(results[strategy_name], key=lambda item: item['code'])

        print(
            f"[web] 选股完成: valid={valid_total_count}, skipped={skipped_count}, "
            f"invalid_name={invalid_name_count}, "
            f"selected={sum(len(items) for items in results.values())}, "
            f"errors={error_counts}"
        )

        return jsonify({
            'success': True,
            'data': results,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'meta': {
                'boards': requested_boards,
                'strategies': requested_strategies,
                'stock_pool_size': len(candidates),
                'invalid_name_count': invalid_name_count,
                'valid_stock_count': valid_total_count,
                'skipped_stock_count': skipped_count,
                'backend': backend,
            }
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

        payload = request.get_json(silent=True) or {}
        requested_boards = _parse_requested_boards(payload.get('boards'))
        requested_strategies = _parse_requested_strategies(payload.get('strategies'))

        job_id = _create_selection_job(requested_boards, requested_strategies)
        thread = Thread(
            target=_run_selection_job,
            args=(job_id, requested_boards, requested_strategies),
            daemon=True,
        )
        thread.start()

        return jsonify({
            'success': True,
            'job_id': job_id,
            'data': _serialize_job(selection_jobs.get(job_id)),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/select/status/<job_id>')
def get_selection_job_status(job_id):
    """查询异步选股任务状态。"""
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
        stock_codes = _filter_hidden_stock_codes(csv_manager.list_all_stocks(), stock_names)
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
            strategies.append({
                'name': strategy_name,
                'param_count': len(strategy.params or {}),
            })

        return jsonify({
            'success': True,
            'data': {
                'boards': boards,
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
        stocks = _filter_hidden_stock_codes(csv_manager.list_all_stocks(), stock_names)
        board_counts = _build_board_counts(stocks)
        
        # 计算数据日期范围
        dates = []
        for code in stocks[:50]:  # 采样
            df = csv_manager.read_stock(code)
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
        config = _load_config()
        data_dir = str(_config_value(config, 'data_dir', default='data'))
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
        config = _load_config()
        data_dir = str(_config_value(config, 'data_dir', default='data'))
        scope = _normalize_csv_value(request.args.get('scope')) or 'all'
        if scope not in {'all', 'main', 'chinext', 'star'}:
            scope = 'all'

        metric = _normalize_csv_value(request.args.get('metric')) or 'daily'
        if metric not in {'daily', 'weekly', 'monthly', 'five_day'}:
            metric = 'daily'

        payload = build_heatmap_payload(data_dir=data_dir, scope=scope, metric=metric)
        return jsonify({'success': True, 'data': payload})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/heatmap/meta')
def get_heatmap_meta():
    """获取市场云图元信息。"""
    try:
        config = _load_config()
        data_dir = str(_config_value(config, 'data_dir', default='data'))
        cache_bundle = ensure_market_caches(data_dir=data_dir)
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
        cache_refresh_pending = market_cache_needs_refresh(data_dir=data_dir)

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
                    'refresh_pending': cache_refresh_pending,
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
        latest_date = ensure_market_caches(
            data_dir=str(_config_value(config, 'data_dir', default='data'))
        ).get('snapshot', {}).get('latest_date')

        return jsonify({
            'success': True,
            'data': {
                'default_provider': str(default_provider or 'akshare').lower(),
                'has_tushare_token': has_tushare_token,
                'latest_date': latest_date,
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

        payload = request.get_json(silent=True) or {}
        provider = _normalize_csv_value(payload.get('provider')) or 'akshare'
        if provider not in {'akshare', 'tushare'}:
            return jsonify({'success': False, 'error': '不支持的数据源'}), 400

        tushare_token = str(payload.get('tushare_token') or '').strip()
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
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/update/status/<job_id>')
def get_update_job_status(job_id):
    """查询异步更新任务状态。"""
    with update_jobs_lock:
        job = update_jobs.get(job_id)
        if not job:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        return jsonify({
            'success': True,
            'data': _serialize_job(job),
        })


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


@app.route('/api/system_status')
def get_system_status():
    """获取当前系统状态。"""
    return jsonify({
        'success': True,
        'halted': _is_halted(),
        'shutdown_requested': shutdown_event.is_set(),
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


@app.route('/api/emergency_stop', methods=['POST'])
def emergency_stop():
    """触发全局急停。急停后仅允许查询状态，需重启服务恢复。"""
    halt_event.set()
    return jsonify({
        'success': True,
        'halted': True,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'message': '系统已急停，重启服务器后方可恢复'
    })


def _terminate_current_process():
    os.kill(os.getpid(), signal.SIGTERM)


@app.route('/api/system_shutdown', methods=['POST'])
def system_shutdown():
    """关闭当前 Web 服务进程。"""
    halt_event.set()
    shutdown_event.set()
    timer = Timer(0.8, _terminate_current_process)
    timer.daemon = True
    timer.start()
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
