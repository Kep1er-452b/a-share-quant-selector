"""
Web 服务器 - A股量化选股系统前端
"""
from flask import Flask, render_template, jsonify, request
import json
import sys
import socket
import os
import time
import uuid
from threading import Event, Lock, Thread
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.csv_manager import CSVManager
from utils.data_provider import BOARD_LABELS
from utils.selection_worker import (
    build_worker_context,
    initialize_selection_worker,
    process_selection_chunk,
)
import strategy.strategy_registry as strategy_registry_module
from strategy.strategy_registry import StrategyRegistry

app = Flask(__name__, 
            template_folder='web/templates',
            static_folder='web/static')

# 全局实例
csv_manager = CSVManager("data")
halt_event = Event()
selection_jobs = {}
selection_jobs_lock = Lock()


def _reload_registry():
    """重新加载策略注册器，确保参数变更立即生效。"""
    global registry
    registry = StrategyRegistry("config/strategy_params.yaml")
    registry.auto_register_from_directory("strategy")
    strategy_registry_module._registry = registry
    return registry


registry = _reload_registry()


def _load_config(config_path="config/config.yaml"):
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


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
    values = [_normalize_csv_value(item) for item in str(raw_value or "").split(",")]
    selected = [item for item in values if item in allowed]
    return selected or ["main", "chinext", "star"]


def _parse_requested_strategies(raw_value):
    available = registry.list_strategies()
    if not raw_value:
        return available

    requested = []
    for item in str(raw_value).split(","):
        strategy_name = item.strip()
        if strategy_name and strategy_name in registry.strategies:
            requested.append(strategy_name)

    return requested or available


def _load_stock_names():
    names_file = Path("data/stock_names.json")
    if not names_file.exists():
        return {}

    with open(names_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def _build_board_counts(stock_codes):
    counts = {"main": 0, "chinext": 0, "star": 0}
    for code in stock_codes:
        counts[_classify_board(code)] += 1
    return counts


def _is_invalid_stock_name(name):
    invalid_keywords = ['退', '未知', '退市', '已退']
    if any(keyword in name for keyword in invalid_keywords):
        return True
    return name.startswith('ST') or name.startswith('*ST')


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
    allowed_endpoints = {
        'index',
        'static',
        'get_system_status',
        'emergency_stop',
        'get_selection_job_status',
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

        candidates = []
        invalid_name_count = 0
        for code in stock_codes:
            name = stock_names.get(code, '未知')
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


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/stocks')
def get_stocks():
    """获取股票列表"""
    try:
        stocks = csv_manager.list_all_stocks()
        stock_names = _load_stock_names()

        requested_board = _normalize_csv_value(request.args.get('board'))
        if requested_board in {"main", "chinext", "star"}:
            stocks = [code for code in stocks if _classify_board(code) == requested_board]
        
        # 获取每只股票的基本信息 - 支持分页
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 500))  # 默认每页500只
        
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
                    'name': stock_names.get(code, '未知'),
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
        df = csv_manager.read_stock(code)
        if df.empty:
            return jsonify({'success': False, 'error': '股票不存在'})
        
        # 计算KDJ指标
        from utils.technical import KDJ
        kdj_df = KDJ(df, n=9, m1=3, m2=3)
        
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
                'J': round(kdj_df.iloc[i]['J'], 2)
            })
        
        return jsonify({'success': True, 'code': code, 'data': data})
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
        candidates = []
        invalid_name_count = 0
        for code in stock_codes:
            name = stock_names.get(code, '未知')
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
        stock_codes = csv_manager.list_all_stocks()
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
        stocks = csv_manager.list_all_stocks()
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
        new_config = request.json
        
        config_file = Path("config/strategy_params.yaml")
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(new_config, f, allow_unicode=True)
        
        # 重新加载策略
        _reload_registry()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/system_status')
def get_system_status():
    """获取当前系统状态。"""
    return jsonify({
        'success': True,
        'halted': _is_halted(),
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


def run_web_server(host=None, port=None, debug=False, config=None, auto_port=None):
    """启动Web服务器"""
    config = config or _load_config()
    host, port = _resolve_web_address(host=host, port=port, auto_port=auto_port, config=config)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"🌐 启动Web服务器: http://{display_host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_web_server(debug=True, config=_load_config())
