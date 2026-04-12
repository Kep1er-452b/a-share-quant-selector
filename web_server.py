"""
Web 服务器 - A股量化选股系统前端
"""
from flask import Flask, render_template, jsonify, request
import json
import sys
import socket
from threading import Event
from pathlib import Path
from datetime import datetime
import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.csv_manager import CSVManager
from utils.data_provider import BOARD_LABELS
import strategy.strategy_registry as strategy_registry_module
from strategy.strategy_registry import StrategyRegistry

app = Flask(__name__, 
            template_folder='web/templates',
            static_folder='web/static')

# 全局实例
csv_manager = CSVManager("data")
halt_event = Event()


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


@app.before_request
def block_requests_after_halt():
    allowed_endpoints = {'index', 'static', 'get_system_status', 'emergency_stop'}
    if request.endpoint in allowed_endpoints:
        return None

    if _is_halted() and request.path.startswith('/api/'):
        return _halted_response()

    return None


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
        
        # 构建数据字典
        stock_data = {}
        for code in stock_codes:
            if _is_halted():
                return _halted_response()
            df = csv_manager.read_stock(code)
            if not df.empty and len(df) >= 60:
                stock_data[code] = (stock_names.get(code, '未知'), df)
        
        # 执行选股
        results = {}
        for strategy_name in requested_strategies:
            if _is_halted():
                return _halted_response()

            strategy = registry.get_strategy(strategy_name)
            if strategy is None:
                continue

            signals = []
            for code, (name, df) in stock_data.items():
                if _is_halted():
                    return _halted_response()

                result = strategy.analyze_stock(code, name, df)
                if result:
                    signals.append({
                        'code': result['code'],
                        'name': result.get('name', stock_names.get(code, '未知')),
                        'signals': result['signals']
                    })
            results[strategy_name] = signals
        
        return jsonify({
            'success': True,
            'data': results,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'meta': {
                'boards': requested_boards,
                'strategies': requested_strategies,
                'stock_pool_size': len(stock_data),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


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
