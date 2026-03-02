# A-Share Quant Selector

基于 Python + akshare 的A股量化选股系统，实现碗口反弹策略，支持K线图可视化、Web管理界面和钉钉自动通知。

## ✨ 核心功能

- 📈 **碗口反弹策略** - 基于KDJ、趋势线和成交量异动的智能选股
- 🥣 **智能分类** - 自动将选股结果分类为：回落碗中、靠近多空线、靠近短期趋势线
- 📊 **K线图可视化** - 为每只入选股票生成K线图（含趋势线和成交量），发送到钉钉
- 🔔 **自动通知** - 选股结果和K线图自动推送到钉钉群
- 🌐 **Web管理** - 可视化查看股票数据、K线图、KDJ指标
- 🔄 **智能更新** - 自动判断是否需要更新数据（3点前不更新，检查当天数据）

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

# 5. 执行选股（自动更新数据、选股、发送钉钉）
python3 main.py run

# 6. 快速测试（只处理前500只股票）
python3 main.py run --max-stocks 500

# 7. 启动Web界面
python3 main.py web
```

## 📊 策略说明

### 碗口反弹策略 (BowlReboundStrategy)

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

#### 技术指标定义

**知行短期趋势线**
```
EMA(EMA(CLOSE, 10), 10)
```
对收盘价连续做两次10日指数移动平均

**知行多空线**
```
(MA(CLOSE, 14) + MA(CLOSE, 28) + MA(CLOSE, 57) + MA(CLOSE, 114)) / 4
```
四条均线平均值

## 🛠️ 技术栈

- **Python 3.8+** - 核心语言
- **akshare** - A股实时/历史数据获取
- **pandas/numpy** - 数据处理与技术指标计算
- **matplotlib** - K线图生成
- **Flask** - Web管理界面
- **钉钉机器人** - 消息推送

## 📁 项目结构

```
├── main.py              # 主程序入口
├── web_server.py        # Web服务器
├── strategy/            # 策略模块
│   ├── __init__.py
│   ├── base_strategy.py # 策略基类
│   ├── bowl_rebound.py  # 碗口反弹策略
│   └── strategy_registry.py # 策略注册器
├── utils/               # 工具模块
│   ├── akshare_fetcher.py  # 数据获取
│   ├── csv_manager.py      # CSV数据管理
│   ├── technical.py        # 技术指标(KDJ/EMA/MA等)
│   ├── kline_chart.py      # K线图生成（标准版）
│   ├── kline_chart_fast.py # K线图生成（快速版）
│   └── dingtalk_notifier.py # 钉钉通知
├── config/              # 配置文件
│   ├── config.yaml
│   ├── strategy_params.yaml
│   └── github.yaml
├── web/                 # Web前端
│   ├── templates/
│   └── static/
└── data/                # 股票数据（CSV格式，自动创建）
```

## 📝 命令说明

| 命令 | 说明 |
|------|------|
| `python3 main.py init` | 首次全量抓取6年历史数据 |
| `python3 main.py update` | 每日增量更新（收盘后执行） |
| `python3 main.py run` | 完整流程：更新数据 → 选股 → 发送钉钉（含K线图） |
| `python3 main.py run --max-stocks 500` | 快速测试模式，只处理前500只股票 |
| `python3 main.py run --category bowl_center` | 只筛选回落碗中的股票 |
| `python3 main.py web` | 启动Web界面 (默认端口5000) |
| `python3 main.py --version` | 显示版本信息 |

### 智能更新逻辑

`python3 main.py run` 会自动判断是否需要更新数据：

1. **3点前** - 不更新，使用本地已有数据
2. **3点后** - 检查每只股票是否有当天数据
3. **100%有当天数据** - 跳过更新，直接使用
4. **否则** - 执行增量更新

## ⚙️ 策略配置

编辑 `config/strategy_params.yaml` 调整参数：

```yaml
# 碗口反弹策略
BowlReboundStrategy:
  N: 2.4            # 成交量倍数（放量阳线判断）
  M: 20             # 回溯天数（查找关键K线）
  CAP: 4000000000   # 流通市值门槛（40亿）
  J_VAL: 0          # J值上限（KDJ超卖阈值）
  M1: 14            # MA周期1（多空线计算）
  M2: 28            # MA周期2（多空线计算）
  M3: 57            # MA周期3（多空线计算）
  M4: 114           # MA周期4（多空线计算）
  duokong_pct: 3    # 距离多空线百分比（分类用）
  short_pct: 2      # 距离短期趋势线百分比（分类用）
```

## 📱 钉钉通知格式

选股结果发送到钉钉的格式：

```
🎯 BowlReboundStrategy:
N: 2.4 (成交量倍数)
M: 20 (回溯天数)
CAP: 4000000000 (40亿市值门槛)
J_VAL: 0 (J值上限)
duokong_pct: 3
short_pct: 2
M1: 14 (MA周期)
M2: 28 (MA周期)
M3: 57 (MA周期)
M4: 114 (MA周期)

⏰ 2026-03-02 15:30

🥣 回落碗中: 2 只
📊 靠近多空线: 1 只
📈 靠近短期趋势线: 0 只
📈 共选出: 3 只

---

### 📊 000001 平安银行
**分类**: 🥣 回落碗中
**价格**: 10.85 | **J值**: -7.65
**关键K线日期**: 02-28
**入选理由**: 回落碗中

[K线图图片]
```

K线图包含：
- 20天K线（涨红跌绿）
- 短期趋势线（蓝色）
- 多空线（绿色）
- 成交量（红绿柱）

## 🌐 Web界面

访问 `http://localhost:5000` 可查看：

- 📊 **系统概览** - 股票数量、最新数据日期
- 📈 **股票列表** - 所有股票基本信息，支持搜索
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

## 🤖 开发团队

本项目使用多Agent协作开发：

| Agent | 职责 |
|-------|------|
| **Main** | 项目经理，协调Agent，技术兜底 |
| **Developer** | 代码实现、单元测试、自测报告 |
| **QA** | 集成测试、问题诊断、验收把关 |
| **Release** | Git推送、版本管理 |

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 📄 License

MIT License

---

**GitHub**: https://github.com/Dzy-HW-XD/a-share-quant-selector