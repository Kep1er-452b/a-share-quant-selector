# A-Share Quant Selector

一个面向本地研究与维护工作的 A 股量化数据、选股和多市场分析系统。

项目使用 Python 构建，支持独立的数据提供方仓库、技术选股策略、K 线与市场概览、Web 控制台、可选的 Wyckoff AI 分析，以及 macOS/Windows 桌面启动方式。

> 本项目仅供学习和研究使用，不构成任何投资建议。行情数据、接口权限和数据许可由使用者自行负责。

## 功能概览

### 多市场 Web 控制台

- **EQUITIES**：A 股和港股市场上下文、概览、热力图、股票列表、选股、策略、自选股和 Wyckoff 分析。
- **FUTURES**：国内期货合约目录、所选合约日线，以及独立的国际商品 CFD 观测和金油/金银/金铜比值研究。
- **MACRO**：GDP、PMI、PPI 等宏观数据；增长视图区分累计值和推导的单季度流量。
- **INDUSTRY**：行业分类、成分股、行业数据和可用时的行业指数 K 线。
- **SYSTEM**：同步任务、结构化事件、健康检查、性能指标和脱敏诊断。

页面通过 hash 路由切换工作区，数据同步在后台任务中执行，并提供进度、取消、重试和权限警告状态。

### 数据与计算

- AkShare、Tushare、Tencent 使用独立的本地股票数据仓库，可在配置或命令行中选择提供方。
- Tushare 扩展数据使用独立 SQLite 仓库，保存指数、估值、财务和市场交易快照；重型历史回补默认关闭。
- 港股、期货、全球商品观测、宏观和行业数据使用独立的领域 SQLite 仓库，不与 A 股 CSV 仓库混用。
- 全球商品第一版使用明确标注为新浪 CFD 的 Brent、Gold、Copper、Silver 日线观测；保留原始单位和换算版本，不将其包装成 ICE/COMEX 真实合约。
- 选股支持进程、线程和顺序执行；可选的 C 加速层失败时回退到 Python 实现。
- 本地 CSV 写入经过校验、去重、锁定和原子替换，选股读取会应用必要的分析视图修复而不悄悄改写源文件。

### 策略与分析

当前策略由注册器自动发现并按家族分组：

- **B1**：`B1V242BStrategy`、`B1V242PStrategy`、`B1V24261Strategy`、`B1MinJSimpleStrategy`、`B1MinJComplexStrategy`、`B1MinJ61ComplexStrategy`
- **B2**：`B2BetaStrategy`
- **Bowl**：`BowlReboundStrategy`
- **FormulaStrategy**：运行时公式策略，不作为固定注册策略保存。

项目也保留 B1 图形匹配、Tongdaxin 风格技术指标、KDJ/MACD/均线、选股结果导出和可选钉钉通知能力。策略公式说明见 [B1_PATTERN_MATCH.md](B1_PATTERN_MATCH.md)。

## 环境要求

- Python 3.10 或更新版本（持续集成使用 Python 3.11）。
- pandas、NumPy、Flask、PyYAML、matplotlib、scipy 等依赖，统一记录在 [requirements.txt](requirements.txt)。
- 使用 Tushare、DeepSeek 或钉钉时，需要对应的账户权限和本地凭据。

## 快速开始

```bash
git clone https://github.com/Kep1er-452b/a-share-quant-selector.git
cd a-share-quant-selector

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 创建本地配置；该文件被 .gitignore 忽略
cp config/config.yaml.template config/config.yaml

# 可选：使用 Tushare
export TUSHARE_TOKEN="你的_tushare_token"

# 检查本地环境，不执行全市场联网更新
.venv/bin/python main.py doctor

# 首次初始化股票数据；按需选择提供方和板块
.venv/bin/python main.py init --provider tushare --board main

# 启动 Web 控制台
.venv/bin/python main.py web --host 127.0.0.1 --port 5080
```

然后打开 <http://127.0.0.1:5080>。如果端口被占用，系统会根据配置自动选择可用端口并在终端输出实际地址。

首次全市场同步可能需要较长时间和相应的数据接口权限；应用启动不会自动执行全市场股票、财务或行情回补。

## 配置与凭据

提交到仓库的配置只提供模板：

- [config/config.yaml.template](config/config.yaml.template)：主配置模板。
- [config/config_local.yaml.template](config/config_local.yaml.template)：本地覆盖配置模板。
- [config/strategy_params.yaml](config/strategy_params.yaml)：策略参数，可版本化维护。
- [config/github.yaml.template](config/github.yaml.template)：发布配置模板，不包含真实 Token。

常用环境变量包括：

```bash
export TUSHARE_TOKEN="..."
export DEEPSEEK_API_KEY="..."
# 可选：把选股结果和 Wyckoff 输出放到指定目录
export A_SHARE_QUANT_OUTPUT_ROOT="..."
```

不要提交真实 Token、API Key、Webhook、个人行情仓库、日志或导出文件。运行时数据默认写入本地可写目录；打包应用会使用用户的应用数据目录。

Tushare 扩展同步默认只运行轻量阶段。确需执行完整价格或财务历史回补时，可在本地配置中显式启用：

```bash
export AQS_TUSHARE_EXTENSION_FULL_BACKFILL=1
```

## CLI 命令

入口文件是 `main.py`，可用 `--help` 查看完整选项。

| 命令 | 用途 |
| --- | --- |
| `init` | 初始化或增量同步股票数据 |
| `select` | 使用本地数据执行选股 |
| `run` | 执行更新、选股和可选通知的完整流程 |
| `web` | 启动 Flask Web 控制台 |
| `calendar` | 查看或更新交易日历缓存 |
| `doctor` | 执行离线健康检查或受控的数据源 smoke 检查 |
| `export` | 按股票代码、名称或拼音导出单股 CSV |

常用示例：

```bash
# 只同步主板
.venv/bin/python main.py init --provider tushare --board main

# 使用指定策略筛选本地数据
.venv/bin/python main.py select --provider tushare --strategy B1V24261Strategy

# 本地数据过期时也强制筛选，不自动更新
.venv/bin/python main.py select --strategy BowlReboundStrategy --force-select

# 更新并筛选，限制股票数量以便进行小批量验证
.venv/bin/python main.py run --provider tushare --max-stocks 500

# 启用 B1 图形匹配
.venv/bin/python main.py run --b1-match --min-similarity 70 --lookback-days 30

# 查看或更新交易日历
.venv/bin/python main.py calendar --provider tushare --update --years 2026 2027

# 导出单股数据
.venv/bin/python main.py export 000001 --force-export
```

板块筛选支持 `all`、`main`、`chinext` 和 `star`。`--board` 与 `--max-stocks` 同时使用时，会先按板块过滤，再截取股票数量。

## 本地数据布局

运行时目录不属于源码发布内容，且已被 Git 忽略：

```text
data/providers/<provider>/              # A 股 CSV 仓库
data/providers/tushare/extended/        # Tushare 扩展 SQLite 仓库
<runtime-data-root>/markets/            # 港股、期货、全球商品观测领域仓库
<runtime-data-root>/economy/            # 宏观领域仓库
<runtime-data-root>/industry/           # 行业领域仓库
<runtime-output-root>/                  # 选股结果和 Wyckoff 输出
logs/                                   # 运行日志和诊断信息
```

不同数据提供方的 CSV 不会合并到同一个可变仓库。行情数量单位、权限和更新时间也可能因提供方不同而不同，请勿直接跨仓库比较原始数量。

## 可选能力

- **Wyckoff AI**：需要 DeepSeek 兼容接口和 `DEEPSEEK_API_KEY`；核心数据同步和技术选股不依赖 AI。
- **C 加速**：使用 `scripts/build_quant_core.py` 构建可选扩展；Python 实现始终保留作为回退。
- **桌面应用**：`launch_desktop_app.py` 提供 pywebview 启动入口，`build_macos_app.py` 负责 macOS 打包准备。
- **钉钉通知**：启用前请将 Webhook 和签名密钥放在本地配置或环境变量中，不要写入提交。

## 开发与验证

安装依赖后运行完整测试：

```bash
.venv/bin/pytest -q
node --check web/static/js/app.js
git diff --check
```

GitHub Actions 会在 `main` 推送和 Pull Request 中运行 Python 测试。涉及前端工作区时，除了语法检查，还应启动本地 Web 应用验证实际路由、加载状态和浏览器控制台。

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。

## 项目结构

```text
main.py                 # CLI 和 QuantSystem 编排
web_server.py           # Web 应用、后台任务和兼容接口
web_api/                # 按市场/领域拆分的 API 蓝图
market_data/            # 港股、期货、全球商品、宏观、行业仓库与同步引擎
strategy/               # 自动发现的选股策略
utils/                  # 数据源、技术指标、CSV、路径和选股执行器
ops/                    # 事件、任务、健康、性能和诊断
web/                    # HTML、CSS、JavaScript 和本地 ECharts
wyckoff_ai/             # Wyckoff 数据、提示词和 AI 管线
tests/                  # 回归、等价性、Web 和安全测试
config/*.template       # 可提交的配置模板
```

## 许可证

本项目采用 [MIT License](LICENSE)。

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。金融市场有风险，使用者应独立判断并承担相应责任。
