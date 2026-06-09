"""
Android适配层 - 为Android环境配置Flask服务
"""
import os
import sys
import json
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 全局变量
_flask_app = None
_flask_thread = None
_android_config = None

def get_android_data_dir():
    """获取Android数据目录"""
    # 在Android上，数据存储在应用内部存储
    return os.environ.get('ANDROID_DATA_DIR', '/data/data/com.quant.aselector/files')

def get_android_config():
    """获取Android配置"""
    global _android_config
    
    if _android_config is None:
        data_dir = get_android_data_dir()
        config_path = os.path.join(data_dir, 'config', 'config.yaml')
        
        # 默认配置
        _android_config = {
            'data_dir': os.path.join(data_dir, 'data'),
            'web': {
                'host': '127.0.0.1',
                'port': 5080,
                'auto_port': False,
                'allow_lan': False
            },
            'data_source': {
                'default_provider': 'akshare',
                'tushare': {
                    'token': os.environ.get('TUSHARE_TOKEN', '')
                },
                'akshare': {
                    'allow_mock_data': False
                }
            },
            'dingtalk': {
                'enabled': False,
                'webhook_url': '',
                'secret': ''
            },
            'wyckoff_ai': {
                'provider': 'deepseek',
                'base_url': 'https://api.deepseek.com',
                'model': 'deepseek-v4-pro',
                'timeout_seconds': 90,
                'deepseek_api_key': os.environ.get('DEEPSEEK_API_KEY', '')
            },
            'selection': {
                'mode': 'parallel',
                'backend': 'thread',  # Android上使用线程而非进程
                'max_workers': 4,
                'chunk_size': 50
            },
            'update': {
                'lookback_days': 10,
                'skip_failed': True
            }
        }
        
        # 尝试加载配置文件
        if os.path.exists(config_path):
            try:
                import yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    file_config = yaml.safe_load(f) or {}
                    _android_config.update(file_config)
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}")
    
    return _android_config

def configure_for_android(data_dir, port=5080):
    """为Android环境配置Flask服务"""
    global _android_config
    
    logger.info(f"Configuring for Android: data_dir={data_dir}, port={port}")
    
    # 设置环境变量
    os.environ['ANDROID_DATA_DIR'] = data_dir
    os.environ['FLASK_PORT'] = str(port)
    
    # 创建必要的目录
    dirs_to_create = [
        os.path.join(data_dir, 'data'),
        os.path.join(data_dir, 'config'),
        os.path.join(data_dir, 'logs'),
        os.path.join(data_dir, 'outputs'),
        os.path.join(data_dir, 'stock-selected'),
    ]
    
    for dir_path in dirs_to_create:
        os.makedirs(dir_path, exist_ok=True)
    
    # 更新配置
    config = get_android_config()
    config['data_dir'] = os.path.join(data_dir, 'data')
    config['web']['port'] = port
    
    # 导入并配置web_server
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    return config

def run_flask_server():
    """运行Flask服务器"""
    global _flask_app
    
    try:
        # 导入web_server模块
        from web_server import app, run_web_server
        
        config = get_android_config()
        host = config['web']['host']
        port = config['web']['port']
        
        logger.info(f"Starting Flask server on {host}:{port}")
        
        # 运行Flask服务器
        _flask_app = app
        run_web_server(host=host, port=port, debug=False, config=config, auto_port=False)
        
    except Exception as e:
        logger.error(f"Failed to run Flask server: {e}")
        raise

def shutdown_server():
    """关闭Flask服务器"""
    global _flask_app
    
    if _flask_app is not None:
        logger.info("Shutting down Flask server")
        # Flask服务器会在主线程结束时自动关闭
        _flask_app = None

def get_server_status():
    """获取服务器状态"""
    return {
        'running': _flask_app is not None,
        'port': get_android_config()['web']['port'],
        'data_dir': get_android_config()['data_dir']
    }

def run_selection_android():
    """Android端执行选股"""
    try:
        from main import QuantSystem
        
        config = get_android_config()
        data_dir = config['data_dir']
        
        logger.info("Starting stock selection...")
        
        # 创建量化系统实例
        quant = QuantSystem(
            config_file=os.path.join(get_android_data_dir(), 'config', 'config.yaml'),
            provider_name=config['data_source']['default_provider']
        )
        
        # 执行选股
        results, stock_names = quant.select_stocks(
            category='all',
            max_stocks=None,
            return_data=False,
            board='all',
            strategy_filter='all'
        )
        
        # 统计结果
        total_count = sum(len(signals) for signals in results.values())
        
        logger.info(f"Selection completed: {total_count} stocks selected")
        
        return {
            'success': True,
            'count': total_count,
            'strategy': 'all',
            'results': results
        }
        
    except Exception as e:
        logger.error(f"Selection failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }

# Android JNI接口（如果需要）
def initialize_android(android_context):
    """Android初始化接口"""
    try:
        # 获取应用上下文
        app_context = android_context.getApplicationContext()
        
        # 获取文件目录
        files_dir = app_context.getFilesDir().getAbsolutePath()
        
        # 配置Android环境
        configure_for_android(files_dir)
        
        logger.info(f"Android initialized: files_dir={files_dir}")
        return True
        
    except Exception as e:
        logger.error(f"Android initialization failed: {e}")
        return False
