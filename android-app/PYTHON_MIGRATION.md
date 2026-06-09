# Python代码迁移指南

## 概述

本文档说明如何将原有的Python代码迁移到Android项目中。

## 迁移步骤

### 1. 复制Python代码

将以下目录复制到 `android-app/app/src/main/python/`：

```bash
# 复制主要Python文件
cp main.py android-app/app/src/main/python/
cp web_server.py android-app/app/src/main/python/
cp test_dingtalk.py android-app/app/src/main/python/
cp test_kline_chart.py android-app/app/src/main/python/

# 复制策略目录
cp -r strategy android-app/app/src/main/python/

# 复制工具目录
cp -r utils android-app/app/src/main/python/

# 复制配置目录
cp -r config android-app/app/src/main/python/

# 复制Wyckoff AI目录
cp -r wyckoff_ai android-app/app/src/main/python/
```

### 2. 修改Python代码

#### 2.1 修改 `main.py`

在文件开头添加Android适配代码：

```python
import os
import sys

# Android环境检测
def is_android():
    """检测是否在Android环境运行"""
    return 'ANDROID_DATA_DIR' in os.environ

# Android数据目录
def get_data_dir():
    """获取数据目录"""
    if is_android():
        return os.environ.get('ANDROID_DATA_DIR', '/data/data/com.quant.aselector/files/data')
    return 'data'

# 修改QuantSystem类的初始化
class QuantSystem:
    def __init__(self, config_file=None, provider_name="akshare", provider_token=None):
        if config_file is None:
            if is_android():
                config_file = os.path.join(os.environ['ANDROID_DATA_DIR'], 'config', 'config.yaml')
            else:
                config_file = "config/config.yaml"
        
        # ... 原有代码 ...
```

#### 2.2 修改 `web_server.py`

在文件开头添加Android适配函数：

```python
import os
import sys

# Android适配函数
def configure_for_android(data_dir, port=5080):
    """为Android环境配置Flask服务"""
    os.environ['ANDROID_DATA_DIR'] = data_dir
    os.environ['FLASK_PORT'] = str(port)
    
    # 创建必要的目录
    dirs = [
        os.path.join(data_dir, 'data'),
        os.path.join(data_dir, 'config'),
        os.path.join(data_dir, 'logs'),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    
    return True

def run_flask_server():
    """运行Flask服务器（Android入口）"""
    from android_adapter import get_android_config, run_flask_server as _run_flask
    _run_flask()

def shutdown_server():
    """关闭Flask服务器（Android入口）"""
    from android_adapter import shutdown_server as _shutdown
    _shutdown()
```

#### 2.3 修改 `utils/local_config.py`

修改配置文件加载逻辑：

```python
import os
from pathlib import Path

def load_config_file(config_path=None):
    """加载配置文件"""
    if config_path is None:
        # Android环境
        if 'ANDROID_DATA_DIR' in os.environ:
            config_path = os.path.join(os.environ['ANDROID_DATA_DIR'], 'config', 'config.yaml')
        else:
            config_path = 'config/config.yaml'
    
    # ... 原有代码 ...
```

#### 2.4 修改 `utils/csv_manager.py`

修改数据存储路径：

```python
import os
from pathlib import Path

class CSVManager:
    def __init__(self, data_dir=None):
        if data_dir is None:
            # Android环境
            if 'ANDROID_DATA_DIR' in os.environ:
                data_dir = os.path.join(os.environ['ANDROID_DATA_DIR'], 'data')
            else:
                data_dir = 'data'
        
        self.data_dir = Path(data_dir)
        # ... 原有代码 ...
```

#### 2.5 修改 `utils/data_provider.py`

修改数据提供者路径：

```python
import os
from pathlib import Path

class BaseDataProvider:
    def __init__(self, data_dir=None):
        if data_dir is None:
            # Android环境
            if 'ANDROID_DATA_DIR' in os.environ:
                data_dir = os.path.join(os.environ['ANDROID_DATA_DIR'], 'data')
            else:
                data_dir = 'data'
        
        self.data_dir = Path(data_dir)
        # ... 原有代码 ...
```

### 3. 创建Android适配模块

创建 `android_adapter.py` 文件（已在项目中）：

```python
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
    return os.environ.get('ANDROID_DATA_DIR', '/data/data/com.quant.aselector/files')

def get_android_config():
    """获取Android配置"""
    global _android_config
    
    if _android_config is None:
        data_dir = get_android_data_dir()
        
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
                    'token': os.environ.get('TUSHARE_TOKEN', 'YOUR_TUSHARE_TOKEN')
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
                'deepseek_api_key': os.environ.get('DEEPSEEK_API_KEY', 'YOUR_DEEPSEEK_API_KEY')
            },
            'selection': {
                'mode': 'parallel',
                'backend': 'thread',
                'max_workers': 4,
                'chunk_size': 50
            }
        }
    
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
        _flask_app = None

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
```

### 4. 处理依赖兼容性

#### 4.1 不兼容的依赖

以下依赖在Android上可能需要特殊处理：

1. **pywebview** - 不支持Android，需要移除
2. **matplotlib** - 可能需要静态编译版本
3. **scipy** - 可能需要静态编译版本

#### 4.2 替代方案

1. **pywebview** → 使用Android WebView
2. **matplotlib** → 使用Web端图表库（Chart.js, ECharts）
3. **scipy** → 使用纯Python实现或预编译版本

### 5. 配置文件处理

#### 5.1 打包配置文件

将配置文件打包到APK的assets目录：

```bash
# 复制配置模板
cp config/config.yaml.template android-app/app/src/main/assets/config/
cp config/strategy_params.yaml android-app/app/src/main/assets/config/
cp config/trade_calendar_seed_2026.json android-app/app/src/main/assets/config/
```

#### 5.2 首次运行初始化

在应用首次运行时，将assets中的配置文件复制到内部存储：

```python
def initialize_config():
    """初始化配置文件"""
    import shutil
    from pathlib import Path
    
    # Android assets路径
    assets_dir = Path('/android_asset')
    config_dir = Path(get_android_data_dir()) / 'config'
    
    # 复制配置文件
    config_files = [
        'config.yaml.template',
        'strategy_params.yaml',
        'trade_calendar_seed_2026.json',
    ]
    
    for file_name in config_files:
        src = assets_dir / 'config' / file_name
        dst = config_dir / file_name
        
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
```

### 6. 测试迁移

#### 6.1 单元测试

运行Python单元测试：

```bash
cd android-app/app/src/main/python
python -m pytest tests/
```

#### 6.2 集成测试

在Android设备上测试：

1. 构建Debug APK
2. 安装到设备
3. 打开应用
4. 测试各项功能

### 7. 常见问题

#### 7.1 导入错误

**问题**：`ModuleNotFoundError: No module named 'xxx'`

**解决**：在 `app/build.gradle.kts` 中添加依赖：

```kotlin
python {
    pip {
        install("xxx")
    }
}
```

#### 7.2 路径错误

**问题**：文件路径不存在

**解决**：使用 `get_android_data_dir()` 获取正确路径

#### 7.3 编码错误

**问题**：中文编码错误

**解决**：确保所有文件使用UTF-8编码

### 8. 性能优化

#### 8.1 减少内存使用

- 使用流式处理
- 及时释放资源
- 限制并发数

#### 8.2 优化启动速度

- 延迟加载非必要模块
- 使用缓存
- 异步初始化

#### 8.3 优化网络请求

- 使用连接池
- 实现请求缓存
- 添加重试机制

### 9. 下一步

完成Python代码迁移后，请继续阅读：

- `ANDROID_DEVELOPMENT.md` - Android开发指南
- `TESTING_GUIDE.md` - 测试指南
- `TROUBLESHOOTING.md` - 故障排除指南

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09
