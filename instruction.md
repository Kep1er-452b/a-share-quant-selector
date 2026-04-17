# A股量化系统终端指令清单

这份文档按当前仓库源码整理，目标是把这个项目在终端里的可用命令一次性列清楚。

说明：

- 以下内容以 `main.py`、`test_dingtalk.py`、`quant.sh`、`web_server.py` 当前代码为准。
- 目前主入口实际支持的子命令有：`init`、`select`、`run`、`web`、`calendar`。
- 仓库里有少量旧文档仍提到 `python3 main.py update`，但当前版本并不支持这个命令。

## 1. 主入口

主入口命令格式：

```bash
python3 main.py [通用参数] <command> [参数]
```

可用 `command`：

- `init`：初始化或补齐股票数据
- `select`：只做筛选，不默认更新数据
- `run`：完整流程，更新数据并执行选股
- `web`：启动 Web 界面
- `calendar`：查看或更新本地交易日历缓存

查看版本：

```bash
python3 main.py --version
```

## 2. `main.py` 通用参数

这些参数由 `main.py` 统一解析，通常可与不同子命令组合使用：

| 参数 | 可选值 / 示例 | 说明 |
|---|---|---|
| `--version` | `python3 main.py --version` | 显示版本信息并退出 |
| `--max-stocks` | `--max-stocks 500` | 限制处理股票数量；若与 `--board` 同时使用，则先按板块过滤再截取前 N 只 |
| `--config` | `--config config/config.yaml` | 指定配置文件路径 |
| `--board` | `all` / `main` / `chinext` / `star` | 股票池范围：全市场 / 主板 / 创业板 / 科创板 |
| `--provider` | `akshare` / `tushare` | 指定数据源；未指定时，交互式终端会提示选择 |
| `--strategy` | `BowlReboundStrategy` | 指定只执行某个策略；未指定时，交互式终端可选择 |
| `--category` | `all` / `bowl_center` / `near_duokong` / `near_short_trend` | 分类筛选结果 |
| `--host` | `--host 0.0.0.0` | Web 服务监听地址，仅 `web` 命令有意义 |
| `--port` | `--port 5080` | Web 服务端口，仅 `web` 命令有意义 |
| `--update` | `python3 main.py calendar --update` | 在 `calendar` 命令中主动更新交易日历缓存 |
| `--years` | `--years 2026 2027` | `calendar` 命令要查看/更新的年份列表 |
| `--force-select` | `python3 main.py select --force-select` | 在 `select` 命令里强制使用当前本地数据，即使数据不是最新 |
| `--b1-match` | `python3 main.py run --b1-match` | 在 `run` 命令中启用 B1 完美图形匹配 |
| `--lookback-days` | `--lookback-days 30` | B1 匹配回看天数 |
| `--min-similarity` | `--min-similarity 70` | B1 匹配最小相似度阈值 |

## 3. 主命令详解

### 3.1 `init`

用途：

- 首次抓取数据
- 按股票池补齐缺失数据
- 对过期数据做智能续抓

常用命令：

```bash
python3 main.py init
python3 main.py init --board main
python3 main.py init --board chinext
python3 main.py init --board star
python3 main.py init --provider tushare
python3 main.py init --board main --max-stocks 500
```

说明：

- 交互式终端下，如果没有显式写 `--provider`，运行 `init` 时会先询问数据源。
- 如果使用 `tushare`，程序会按下面顺序寻找 Token：
  1. 环境变量 `TUSHARE_TOKEN`
  2. `config/config.yaml` 中的 `data_source.tushare.token`
  3. 终端交互输入

### 3.2 `select`

用途：

- 只对本地已有数据做选股
- 不默认更新数据
- 如果数据过期，交互式终端会先询问是否补齐

常用命令：

```bash
python3 main.py select
python3 main.py select --strategy BowlReboundStrategy
python3 main.py select --strategy B1V242BStrategy
python3 main.py select --strategy B2BetaStrategy
python3 main.py select --category bowl_center
python3 main.py select --board main --max-stocks 300
python3 main.py select --provider tushare --strategy B1V242BStrategy
python3 main.py select --strategy BowlReboundStrategy --force-select
```

说明：

- 若本地数据已是最新，直接筛选。
- 若本地数据过期：
  - 交互式终端会询问是否先抓取/补齐再筛选。
  - 非交互式场景会直接停止。
  - 可用 `--force-select` 强制继续。

### 3.3 `run`

用途：

- 执行完整流程：更新数据 -> 选股 -> 通知
- 若配置 `dingtalk.enabled: true`，会发送钉钉消息；否则会跳过通知

常用命令：

```bash
python3 main.py run
python3 main.py run --provider tushare
python3 main.py run --board main
python3 main.py run --board main --max-stocks 500
python3 main.py run --strategy BowlReboundStrategy
python3 main.py run --strategy B1V242BStrategy
python3 main.py run --strategy B2BetaStrategy
python3 main.py run --category bowl_center
python3 main.py run --category near_duokong
python3 main.py run --category near_short_trend
```

说明：

- 交互式终端下，如果没有写 `--provider`，运行 `run` 时会先询问数据源。
- 交互式终端下，如果没有写 `--strategy`，运行 `run` 时会提示选择策略。
- 当指定 `--strategy` 时，`run` 仍然保留“先更新，再筛选”的语义。

### 3.4 `run` + B1 完美图形匹配

用途：

- 在完整流程基础上，增加 B1 完美图形相似度匹配排序

常用命令：

```bash
python3 main.py run --b1-match
python3 main.py run --b1-match --lookback-days 30
python3 main.py run --b1-match --min-similarity 70
python3 main.py run --b1-match --max-stocks 100
python3 main.py run --b1-match --category bowl_center
python3 main.py run --b1-match --strategy B1V242BStrategy
python3 main.py run --b1-match --board star --lookback-days 40 --min-similarity 75
```

说明：

- `--b1-match` 只在 `run` 命令中生效。
- 如果没有匹配到超过阈值的股票，则会跳过该部分通知。
- `--lookback-days` 和 `--min-similarity` 未填写时，会回退到策略配置默认值。

### 3.5 `web`

用途：

- 启动 Web 管理界面

常用命令：

```bash
python3 main.py web
python3 main.py web --host 127.0.0.1
python3 main.py web --host 0.0.0.0 --port 5080
python3 main.py web --port 5090
```

说明：

- 默认地址来自配置文件 `config/config.yaml` 的 `web.host` 和 `web.port`。
- 配置模板默认值是：

```yaml
web:
  host: "127.0.0.1"
  port: 5080
  auto_port: true
```

- 如果端口已被占用，且 `auto_port: true`，系统会自动尝试顺延端口。

### 3.6 `calendar`

用途：

- 查看当前本地交易日历缓存状态
- 主动更新 Tushare 交易日历缓存
- 在 `trade_cal` 不可用时，为系统提供本地兜底交易日判断依据

常用命令：

```bash
python3 main.py calendar --provider tushare
python3 main.py calendar --provider tushare --update
python3 main.py calendar --provider tushare --update --years 2026
python3 main.py calendar --provider tushare --update --years 2026 2027
```

说明：

- 系统已内置一份 `2026` 年交易日历种子文件：`config/trade_calendar_seed_2026.json`。
- 运行 `calendar --provider tushare` 时会显示：
  - 当前是否有缓存
  - 已缓存哪些年份
  - 缓存截至到哪一天
- 运行 `calendar --provider tushare --update --years ...` 时，会联网调用 Tushare 的 `trade_cal` 更新本地缓存。
- 本地缓存文件会写到 `data/trade_calendar_cache.json`。
- 如果 `trade_cal` 临时无响应、超时或被限流，系统会在终端打印类似提示：

```text
trade_cal 无响应，将使用本地交易日历缓存。请确保本地交易日历缓存已为最新。
```

- 如果连本地缓存也没有，系统会继续退回到“按工作日近似判断”的最后兜底方案。
- `web` 模式下如果服务端进程里触发了这类降级，相关提示也会输出到启动 Web 的那个终端窗口里。

## 4. 当前可用策略名

按当前 `strategy/` 目录静态扫描，仓库内可见的策略类包括：

- `BowlReboundStrategy`
- `B1V242BStrategy`
- `B2BetaStrategy`

说明：

- 系统会动态扫描 `strategy/` 目录并注册继承 `BaseStrategy` 的策略类。
- 如果你后续新增策略文件，运行时通常也会自动出现在可选策略列表中。

## 5. 分类参数 `--category`

可选值如下：

| 值 | 含义 |
|---|---|
| `all` | 全部分类 |
| `bowl_center` | 回落碗中 |
| `near_duokong` | 靠近多空线 |
| `near_short_trend` | 靠近短期趋势线 |

示例：

```bash
python3 main.py run --category bowl_center
python3 main.py select --category near_duokong
python3 main.py run --b1-match --category near_short_trend
```

## 6. 交互式行为

如果你是在真正的终端里直接运行，而不是通过脚本重定向：

- 运行 `python3 main.py init` / `select` / `run` / `web` / `calendar` 且未写 `--provider` 时，会提示选择：
  - `akshare`
  - `tushare`
- 运行 `python3 main.py select` / `run` 且未写 `--strategy` 时，会提示选择：
  - `all`
  - `BowlReboundStrategy`
  - `B1V242BStrategy`
  - `B2BetaStrategy`

## 7. 钉钉测试命令

辅助脚本：`test_dingtalk.py`

用途：

- 测试钉钉机器人发送能力
- 可用模拟数据测试
- 也可触发真实选股后发送

命令：

```bash
python3 test_dingtalk.py
python3 test_dingtalk.py --real
python3 test_dingtalk.py --real --max-stocks 100
python3 test_dingtalk.py --category bowl_center
python3 test_dingtalk.py --real --category bowl_center
python3 test_dingtalk.py --category near_duokong
python3 test_dingtalk.py --category near_short_trend
```

参数说明：

| 参数 | 可选值 | 说明 |
|---|---|---|
| `--real` | 无 | 执行真实选股；不写时使用模拟数据 |
| `--category` | `all` / `bowl_center` / `near_duokong` / `near_short_trend` | 只发送指定分类 |
| `--max-stocks` | 整数 | 限制真实选股时处理的股票数量 |

## 8. Web 服务直接启动命令

除了 `python3 main.py web`，仓库里还可以直接运行：

```bash
python3 web_server.py
```

说明：

- 这个入口不接受命令行参数。
- 代码里会直接按默认方式启动：
  - 读取配置文件
  - `debug=True`
  - 自动解析监听地址和端口
- 日常使用更推荐 `python3 main.py web`，因为更方便显式传 `--host` 和 `--port`。

## 9. 快捷脚本 `quant.sh`

仓库里有一个 shell 包装脚本：

```bash
bash quant.sh init
bash quant.sh run
bash quant.sh web
```

它也支持把后续参数原样透传给 `main.py`，例如：

```bash
bash quant.sh run --board main --max-stocks 200
bash quant.sh web --port 5090
```

说明：

- 当前脚本只封装了 `init`、`run`、`web`，没有封装 `select`。
- 脚本里写死了：
  - `QUANT_DIR="/root/quant-csv"`
  - `PYTHON="/usr/bin/python3"`
- 因此它更像部署环境下的快捷启动脚本，不一定适合你当前这台机器直接使用，使用前最好先改路径。

## 10. 开发/调试脚本

这两个脚本带有 `__main__` 测试入口，但更偏向开发调试，不是常规业务命令：

```bash
python3 utils/kline_chart.py
python3 utils/kline_chart_fast.py
```

说明：

- `utils/kline_chart.py` 会生成一份内置测试数据的 K 线图。
- `utils/kline_chart_fast.py` 会尝试读取固定路径 `/root/quant-csv/data` 下的股票数据做测试。
- 这两条命令更适合开发者自测图表模块，不建议当作日常操作命令。

## 11. 配置相关常用终端命令

虽然不是 Python 子命令，但实际使用这个系统时通常会先执行这些：

```bash
pip3 install -r requirements.txt
cp config/config.yaml.template config/config.yaml
export TUSHARE_TOKEN="你的_tushare_token"
```

如果你准备使用 `tushare`，建议至少配置其中一种：

- 环境变量 `TUSHARE_TOKEN`
- `config/config.yaml` 中的 `data_source.tushare.token`

如果你准备发送钉钉消息，还需要在 `config/config.yaml` 里配置：

- `dingtalk.enabled: true`
- `dingtalk.webhook_url`
- `dingtalk.secret`

## 12. 已过时或当前不可用的命令

下面这些内容在仓库某些文档里还能看到，但按当前源码并不能直接这样用：

### 12.1 `python3 main.py update`

当前 `main.py` 的 `command` 只接受：

- `init`
- `select`
- `run`
- `web`

因此下面这种旧写法现在无效：

```bash
python3 main.py update
```

它仍然出现在 `config/crontab.txt` 的注释示例里，但不是当前可执行命令。

### 12.2 CLI 里没有单独暴露 `schedule`

代码中存在 `run_schedule()` 方法，但当前命令行参数并没有提供单独的 `schedule` 子命令，所以目前不能直接这样运行：

```bash
python3 main.py schedule
```

## 13. 最常用的一组命令

如果你只想记住最核心的一套，基本就是下面这些：

```bash
pip3 install -r requirements.txt
cp config/config.yaml.template config/config.yaml
export TUSHARE_TOKEN="你的_tushare_token"

python3 main.py init
python3 main.py run
python3 main.py select --strategy BowlReboundStrategy
python3 main.py run --b1-match
python3 main.py web
python3 test_dingtalk.py
```

## 14. 一句话总结

日常终端使用时，最重要的入口其实只有两个：

- `python3 main.py ...`：系统主入口
- `python3 test_dingtalk.py ...`：钉钉通知测试入口

其余像 `web_server.py`、`quant.sh`、`utils/kline_chart*.py` 都更偏辅助或调试用途。
