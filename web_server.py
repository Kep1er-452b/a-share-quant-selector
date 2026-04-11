"""
Web 服务器 - A股量化选股系统前端
"""
from flask import Flask, render_template, jsonify, request, send_from_directory
import json
import sys
import socket
from pathlib import Path
from datetime import datetime
import pandas as pd
import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.csv_manager import CSVManager
from strategy.strategy_registry import get_registry

app = Flask(__name__, 
            template_folder='web/templates',
            static_folder='web/static')

# 全局实例
csv_manager = CSVManager("data")
registry = get_registry("config/strategy_params.yaml")

# 加载策略
registry.auto_register_from_directory("strategy")


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


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/stocks')
def get_stocks():
    """获取股票列表"""
    try:
        stocks = csv_manager.list_all_stocks()
        
        # 加载股票名称
        names_file = Path("data/stock_names.json")
        stock_names = {}
        if names_file.exists():
            with open(names_file, 'r', encoding='utf-8') as f:
                stock_names = json.load(f)
        
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
        stock_codes = csv_manager.list_all_stocks()
        
        # 加载股票名称
        names_file = Path("data/stock_names.json")
        stock_names = {}
        if names_file.exists():
            with open(names_file, 'r', encoding='utf-8') as f:
                stock_names = json.load(f)
        
        # 构建数据字典
        stock_data = {}
        for code in stock_codes:
            df = csv_manager.read_stock(code)
            if not df.empty and len(df) >= 60:
                stock_data[code] = (stock_names.get(code, '未知'), df)
        
        # 执行选股
        results = {}
        for strategy_name, strategy in registry.strategies.items():
            signals = []
            for code, (name, df) in stock_data.items():
                result = strategy.analyze_stock(code, name, df)
                if result:
                    signals.append({
                        'code': result['code'],
                        'name': result.get('name', stock_names.get(code, '未知')),
                        'signals': result['signals']
                    })
            results[strategy_name] = signals
        
        return jsonify({'success': True, 'data': results, 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
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
                'strategies': len(registry.strategies)
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
        import yaml
        new_config = request.json
        
        config_file = Path("config/strategy_params.yaml")
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(new_config, f, allow_unicode=True)
        
        # 重新加载策略
        global registry
        registry = get_registry("config/strategy_params.yaml")
        registry.auto_register_from_directory("strategy")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


def run_web_server(host=None, port=None, debug=False, config=None, auto_port=None):
    """启动Web服务器"""
    config = config or _load_config()
    host, port = _resolve_web_address(host=host, port=port, auto_port=auto_port, config=config)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"🌐 启动Web服务器: http://{display_host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    run_web_server(debug=True, config=_load_config())
