# A-Share Quant Selector

基于 Python + akshare 的A股量化选股系统，实现碗口反弹策略，支持Web管理界面和钉钉自动通知。

## ✨ 核心功能

- 📈 **碗口反弹策略** - 基于KDJ、趋势线和成交量异动的智能选股
- 🔔 **自动通知** - 选股结果自动推送到钉钉群
- 🌐 **Web管理** - 可视化查看股票数据、K线图、KDJ指标
- ⏰ **定时任务** - 支持 crontab 每日自动执行
- 🔄 **智能过滤** - 自动过滤退市、ST、债基、ETF等无效标的

## 🚀 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/Dzy-HW-XD/a-share-quant-selector.git
cd a-share-quant-selector

# 2. 安装依赖
pip3 install -r requirements.txt

# 3. 配置钉钉通知（可选）
# 编辑 config/config.yaml 填写 webhook 和 secret

# 4. 首次全量抓取数据
python3 main.py init

# 5. 执行选股
python3 main.py run

# 6. 启动Web界面
python3 main.py web
```

## 📊 策略说明

### 碗口反弹策略 (BowlReboundStrategy) - 分类标记版

策略基于以下逻辑选股，并将选出的股票按三种类型分类标记：

#### 选股条件
1. **上升趋势** - 知行短期趋势线 > 知行多空线
2. **异动放量阳线** - 近期(M天内)存在放量阳线（成交量>=前日*N倍，且收盘价>开盘价）
3. **KDJ低位** - J值 <= 阈值，处于超卖区域

#### 分类标记（优先级从高到低）

| 分类 | 图标 | 条件 | 参数 |
|------|------|------|------|
| **回落碗中** | 🥣 | 价格位于知行短期趋势线和知行多空线之间 | - |
| **靠近多空线** | 📊 | 价格距离知行多空线 ±duokong_pct% | `duokong_pct`: 默认3% |
| **靠近短期趋势线** | 📈 | 价格距离知行短期趋势线 ±short_pct% | `short_pct`: 默认2% |

> **注意**：回落碗中优先级最高。当股票同时满足多个条件时，优先标记为"回落碗中"。

## 🛠️ 技术栈

- **Python 3.8+** - 核心语言
- **akshare** - A股实时/历史数据获取
- **pandas/numpy** - 数据处理与技术指标计算
- **Flask** - Web管理界面
- **钉钉机器人** - 消息推送

## 📁 项目结构

```
├── main.py              # 主程序入口
├── web_server.py        # Web服务器
├── quant.sh             # 快捷命令脚本
├── strategy/            # 策略模块
│   ├── __init__.py      # 策略自动注册
│   ├── base_strategy.py # 策略基类
│   └── bowl_rebound.py  # 碗口反弹策略（分类标记版）
├── utils/               # 工具模块
│   ├── akshare_fetcher.py
│   ├── csv_manager.py
│   ├── technical.py     # 技术指标(KDJ等)
│   └── dingtalk_notifier.py
├── config/              # 配置文件
│   ├── config.yaml      # 主配置
│   ├── strategy_params.yaml
│   └── crontab.txt
├── web/                 # Web前端
│   ├── templates/
│   └── static/
└── data/                # 股票数据（CSV格式）
```

## 📝 命令说明

| 命令 | 说明 |
|------|------|
| `python3 main.py init` | 首次全量抓取6年历史数据 |
| `python3 main.py update` | 每日增量更新 |
| `python3 main.py select` | 执行选股策略（全部分类） |
| `python3 main.py select --category bowl_center` | 只选回落碗中的股票 |
| `python3 main.py select --category near_duokong` | 只选靠近多空线的股票 |
| `python3 main.py select --category near_short_trend` | 只选靠近短期趋势线的股票 |
| `python3 main.py run` | 完整流程（更新+选股+通知） |
| `python3 main.py run --category near_duokong` | 完整流程，只选靠近多空线的股票 |
| `python3 main.py web` | 启动Web界面 (默认端口5000) |
| `python3 main.py --version` | 显示版本信息 |

或使用快捷脚本：
```bash
./quant.sh init
./quant.sh run
./quant.sh web
```

## ⚙️ 策略配置

编辑 `config/strategy_params.yaml` 调整参数：

```yaml
# 碗口反弹策略（分类标记版）
BowlReboundStrategy:
  N: 2.4            # 成交量倍数
  M: 20             # 回溯天数
  CAP: 4000000000   # 流通市值门槛（40亿）
  J_VAL: 0          # J值上限
  M1: 14            # MA周期1
  M2: 28            # MA周期2
  M3: 57            # MA周期3
  M4: 114           # MA周期4
  duokong_pct: 3    # 距离多空线百分比（分类用）
  short_pct: 2      # 距离短期趋势线百分比（分类用）
```

## 🌐 Web界面

访问 `http://localhost:5000` 可查看：

- 📊 **系统概览** - 股票数量、最新数据日期
- 📈 **股票列表** - 所有股票基本信息，支持搜索，点击查看K线图
- 🎯 **选股结果** - 执行选股并查看信号详情
- ⚙️ **策略配置** - 在线修改策略参数

## ⏰ 定时任务

添加到 crontab 实现每日自动选股：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每天15:05执行，仅工作日）
5 15 * * 1-5 cd /root/quant-csv && /usr/bin/python3 main.py run >> /var/log/quant-csv/run.log 2>&1
```

## 🔧 扩展新策略

1. 在 `strategy/` 目录创建新文件，继承 `BaseStrategy`
2. 实现 `calculate_indicators()` 和 `select_stocks()` 方法
3. 在 `config/strategy_params.yaml` 添加参数
4. 系统自动识别并执行

示例：
```python
from strategy.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    def __init__(self, params=None):
        super().__init__("我的策略", params)
    
    def calculate_indicators(self, df):
        # 计算指标
        return df
    
    def select_stocks(self, df, stock_name=''):
        # 选股逻辑
        return signals
```

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 📄 License

MIT License

---

**GitHub**: https://github.com/Dzy-HW-XD/a-share-quant-selector
