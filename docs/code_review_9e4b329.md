# 全项目代码审查报告

- **审查基准**：分支 `codex/tushare-comprehensive-upgrade`，提交 `9e4b329`（2026-07-23 "Fix multi-market syncs and macro GDP visualization"），工作区干净、无未提交改动。
- **审查日期**：2026-07-28
- **审查范围**：约 3.7 万行核心代码，按五个子系统全量审查：
  1. 数据层（csv_manager / data_provider / provider_router / 三家 fetcher / tushare_ext_* / price_adjustment / market_overview）
  2. 多市场域数据 + Web API + 运维（market_data/ / web_api/ / ops/）
  3. Flask 主服务与入口层（web_server.py / main.py / launch_desktop_app.py / selection_worker 等）
  4. 策略与技术计算层（strategy/ / technical / formula_engine / quant_core / kline_chart）
  5. 前端 JS + Wyckoff AI 管线 + 通知（app.js 及各 workspace / wyckoff_ai/ / dingtalk_notifier 等）
- **可信度说明**：所有 P0 高危项均经过对源码的二次抽查确认（含行级验证）。

---

## 目录

- [一、必须修复的逻辑/正确性缺陷](#一必须修复的逻辑正确性缺陷)
  - [P0：数据正确性与安全](#-p0数据正确性与安全)
  - [P1：并发与状态机](#-p1并发与状态机)
  - [P1：市场隔离与指标语义](#-p1市场隔离与指标语义)
  - [其余中危正确性问题](#其余中危正确性问题)
- [二、需要补强的健壮性问题](#二需要补强的健壮性问题)
- [三、性能优化机会](#三性能优化机会)
- [四、做得好的地方](#四做得好的地方无需改动)
- [五、建议的修复批次](#五建议的修复批次)

---

## 一、必须修复的逻辑/正确性缺陷

### 🔴 P0：数据正确性与安全

#### 1. 腾讯兜底路径静默污染真实成交额（已验证）

- **位置**：`utils/akshare_fetcher.py:818`（腾讯 fqkline 解析），配合 `utils/csv_manager.py:148-176`
- **问题**：腾讯路径硬编码 `amount=0`、`turnover=0`、`market_cap=0`。AkShare 增量更新走腾讯兜底时，CSV 合并去重 `keep='last'` 会用 0 **覆盖重叠日期的真实成交额**；`_preserve_existing_metrics` 只保护 `turnover`/`market_cap`，不保护 `amount` → 静默数据损坏。下游 `market_overview` 的全市场成交额（`market_amount_yi`）直接对 amount 求和，被系统性低估，且随每次腾讯兜底持续扩散。
- **修复**：
  1. `_preserve_existing_metrics` 增加 amount 保护（新值为 0/NaN 且旧值有效时保留旧值）；
  2. 腾讯路径写 `NaN` 而非 `0`，区分"缺数据"与"零成交"。

#### 2. `GET /api/heatmap?refresh=1` 无鉴权触发强副作用（已验证）

- **位置**：`web_server.py:3871-3903`
- **问题**：`before_request` 的 session token 校验只覆盖非 GET/HEAD/OPTIONS 请求，此 GET 端点在 `refresh=1` 时直接调用 `rebuild_market_caches(...)`（读数千 CSV + 重写缓存文件）。任何页面一个 `<img src="http://127.0.0.1:PORT/api/heatmap?refresh=1">` 即可跨站触发；且与更新作业收尾（L2002）、启动预热（L2526）三条路径并发执行**无任何互斥**，存在写冲突与重复重 IO。
- **修复**：强刷拆为独立 `POST /api/heatmap/rebuild` 纳入 token 校验（或 GET 分支对 `refresh=1` 单独调用 `_require_session_token()`）；三处重建统一走一个带模块级锁的入口。

#### 3. 事件脱敏正则漏掉 `access_token=`——钉钉 webhook 凭证明文落盘（已验证）

- **位置**：`ops/events.py:11-14`
- **问题**：模式 2 `\b((?:...|token|...)[=:])` 中下划线是 word 字符，`access_token=xxx`（钉钉 webhook URL 标准形态）在 "token" 前不产生词边界，**不会被匹配**；模式 3 `[?&](?:token|api_key|key|secret)=` 同样匹配不到 `?access_token=`。含完整 webhook URL 的异常消息会把凭证明文写进 `ops.sqlite`、`ops-events.jsonl` 与诊断导出。
- **同类旁路**：
  - `utils/error_logging.py:84-85, 107`：`error_message: str(error)` 与 traceback 不做内容级脱敏，tushare 请求异常可携带含 token 的完整 URL 写入错误报告与 system.log；
  - `launch_desktop_app.py:109`：`system_proxy: urllib.request.getproxies()` 原样写入事故文件，代理 URL 形如 `http://user:pass@host:port` 时凭据落盘；
  - `ops/tasks.py:52-55`：`source_errors` 直接放 `str(exc)` 未过 redact。
- **修复**：正则改为 `(?i)\b([\w-]*(?:token|api_key|secret|password)\s*[=:]\s*)[^\s,;&#]+` 形式并补 `oapi.dingtalk.com/robot/send?access_token=` 用例测试；error_logging 增加内容级正则清洗；代理只记录键名。

#### 4. 图形匹配的知行趋势线整体上下颠倒（已验证）

- **位置**：`strategy/pattern_feature_extractor.py:40-45`
- **问题**：`extract()` 把窗口按日期**升序**排列后调 `calculate_zhixing_trend()`；该函数内部经 `normalize_price_frame(descending=True)`（`utils/technical.py:405`）重排为**降序**并 `reset_index`，返回帧按 0..n-1 位置赋回升序帧——趋势序列整体反向。`_extract_trend_features` 里 `df.iloc[-1]`（应为最新一天）实际拿到**最老一天**的趋势值，斜率符号全部反向。趋势维度（权重 0.10~0.30）的相似度打分系统性失真。KDJ 无此问题（`KDJ()` 自动检测方向）。
- **修复**：升序窗口下直接用 `ewm`/`rolling` 计算（同 `calculate_zhixing_main_overlay` 的做法），或对 `trend_df` 先 `iloc[::-1].reset_index(drop=True)` 再赋值。

#### 5. `fut_mapping` 全量加载在真实数据量下必然 500

- **位置**：`market_data/futures.py:79-88, 383`
- **问题**：`continuous_symbols()` 通过 `_all_store_rows` 把 fut_mapping **全量历史**读入内存，超 100,000 行直接 `raise ValueError`。fut_mapping 是"连续合约 × 每交易日"映射（约 50 个连续代码 × 数十年交易日，轻松超 40 万行），全量同步后该端点必然失败；且 ValueError 被 `_call` 当成 400 INVALID_REQUEST 返回，误导为客户端错误。
- **修复**：只需每个 `continuous_symbol` 最新一行——改 SQL `MAX(data_date) GROUP BY symbol` 或 `query_latest_rows(rows_per_symbol=1)`；同时区分"数据规模超限"与"参数非法"两类错误。

---

### 🟠 P1：并发与状态机

#### 6. `TaskRegistry.snapshot` 锁外无保护迭代活 job dict

- **位置**：`web_server.py:175-181` + `ops/tasks.py:53-71`
- **问题**：注册的 lambda（`lambda: selection_jobs` 等）返回**活引用**，`snapshot()` 中迭代与字段读取都不持有对应 jobs 锁。与 `_prune_terminal_jobs` 删除键或工作线程写字段并发时会抛 `RuntimeError: dictionary changed size during iteration` 或撕裂读；而 `_active_ops_job_ids()` 被用于**作业准入判断**。
- **修复**：lambda 改为在对应锁内做浅拷贝后返回，如 `lambda: _snapshot_jobs(selection_jobs, selection_jobs_lock)`。

#### 7. daily_basic 缓存锁内执行网络调用与 `sleep(62)`

- **位置**：`utils/tushare_fetcher.py:685-717`
- **问题**：`_fetch_daily_basic_trade_date` 整个函数体（含 4 次重试、限流等待 `sleep(62)`）持有 `daily_basic_cache_lock`。任一 worker 触发限流，**所有**并发同步线程被阻塞 62 秒以上，并发同步实质退化为串行。
- **修复**：锁只保护缓存读写；网络调用移出临界区；用 per-trade_date 的 in-flight future 模式避免重复请求。

#### 8. provider 切换 TOCTOU：锁内二次检查遗漏诊断作业

- **位置**：`web_server.py:3476-3484`（对照 L3436 第一次检查）
- **问题**：`/api/provider/activate` 进入 `job_admission_lock` 后的权威二次检查只查 running_update / running_selection / sync_selection_active，**漏掉 `_find_running_diagnostic_job()`**。窗口期内诊断作业启动后 provider 仍可被切换，诊断线程会读到切换后的数据目录。
- **修复**：把诊断作业（及涉及数据目录的 Wyckoff）纳入锁内二次检查。

#### 9. 急停流程：快照写盘失败会吞掉进程终止调度

- **位置**：`web_server.py:818-824`
- **问题**：`_trigger_emergency_stop` 中 `_write_emergency_incident(...)` 若抛异常（磁盘满/权限），后续 `_schedule_process_termination()` 不执行——此时 `halt_event` 已置位，服务进入"永久拒绝所有请求但不退出"的僵死状态。
- **修复**：`try/finally` 保证终止调度必然执行；快照写失败降级为 stderr 日志。

#### 10. Wyckoff 作业无准入控制 + 前端不处理 cancelled 终态

- **位置**：`web_server.py:3212-3260`；`web/static/js/app.js:3890-3912`
- **问题**：
  1. `_start_market_wyckoff_job` 不经 `job_admission_lock`、无并发上限，连续 POST 可无限启动分析线程 + AI 外呼；
  2. 前端 `waitForWyckoffJob` 的 `while(true)` 仅处理 done/error/halted，任务被后端取消时以 1.2s 间隔**无限轮询**。
- **修复**：后端拒绝并发 Wyckoff 作业（409 + 现有 job_id）；前端补 `cancelled` 终态分支 + 最大轮询时长保险。

#### 11. `cancel_update_job` 状态机不一致 + 锁外读 job

- **位置**：`web_server.py:4281-4323`
- **问题**：不像 `_request_job_cancel` 那样置 `cancelling` 中间态，前端轮询看不到取消中；L4317 在锁释放后读 `job.get('provider')` 与工作线程写字段竞争。
- **修复**：统一走 `_request_job_cancel` 逻辑；锁内取出字段副本再用。

#### 12. 策略配置读-改-写竞态 + registry 无锁热替换

- **位置**：`web_server.py:4439-4497`
- **问题**：两个并发 POST 对 `strategy_params.yaml` 的 load→merge→write 互相覆盖丢更新；写后 `_reload_registry()` 无锁替换全局 registry，运行中的选股作业可能拿到新旧混合的策略参数。
- **修复**：加 `config_write_lock` 覆盖整个读改写+reload 序列；选股作业启动时快照 registry 引用。

---

### 🟠 P1：市场隔离与指标语义

#### 13. 港股详情弹窗导出按钮回退 A 股端点（违反市场隔离约束）

- **位置**：`web/static/js/app.js:2676, 3206`
- **问题**：`viewStockDetail` 无条件启用导出按钮，港股分支未重新禁用；`exportCurrentStock` 固定调用 `/api/stock/${code}/export`（A 股端点）。港股详情点 EXPORT CSV 会以港股 symbol 打 A 股导出端点。
- **修复**：港股分支显式 `disabled = true` 并显示"港股导出暂不支持"；`exportCurrentStock` 增加市场守卫。

#### 14. Wyckoff 轮询端点随市场切换中途串扰

- **位置**：`web/static/js/app.js:3592-3595, 3895-3898`
- **问题**：`pollWyckoffJob` / `waitForWyckoffJob` 每次轮询重新求值 `currentEquityMarket()`，任务运行中切换 A 股/港股会导致状态轮询端点中途切换，job_id 在对方市场不存在 → 轮询报错/任务状态丢失。
- **修复**：任务启动时把市场上下文固化进 job 上下文，整个轮询生命周期使用启动时的市场。

#### 15. 指标 NaN 语义缺陷簇（Python 回退恰是含 NaN 数据的实际执行路径）

C 包装层遇 NaN 主动降级到 Python 路径，因此以下缺陷是含 NaN 数据的**必经路径**：

| 位置 | 问题 | 修复 |
|---|---|---|
| `utils/technical.py:308`（KDJ 回退） | `range_val != 0` 对 NaN 判 True → rsv 变 NaN，经 SMA 递归**永久污染**后续所有 K/D/J；与 C 实现（rsv 回落 50）语义不等价 | `& ~np.isnan(range_val)`，NaN rsv 用 50 兜底与 C 对齐 |
| `utils/technical.py:197-199`（EXIST 回退） | 全 NaN 窗口 `max()=NaN`，`bool(NaN)=True` → EXIST 误报"存在" | 先 `cond.fillna(False)` 再 rolling |
| `utils/technical.py:218`（COUNT 回退） | 含 NaN 的 float 序列 `astype(int)` 抛 ValueError，被 worker 按"策略错误"静默吞掉，股票被跳过 | `cond.fillna(False).astype(int)` |
| `strategy/bowl_rebound.py:125-126` | `volume / ref_vol_1` 前日成交量为 0 时得 `inf`，`vol_surge` 恒 True 产生虚假放量信号；inf 还会写进 `signal_info`（L254 只判 NaN 没判 inf） | `ref_vol_1.replace(0, np.nan)` 后再除，inf/NaN 一律视为不放量 |

#### 16. DTW 相似度两条路径归一化不可比

- **位置**：`strategy/pattern_matcher.py:227-232, 263-273`
- **问题**：`fastdtw` 返回路径上逐点距离之和（量级 ~n），`_simple_dtw` 返回整体欧氏距离（量级 ~√n），两者共用 `sqrt(max_len)` 归一化。装了 fastdtw 后曲线得分被系统性压向 0——**同一配置在装/不装 fastdtw 的机器上选股结果不同**。
- **修复**：fastdtw 路径按路径长度（或 n）归一化，如 `1 - distance / max_len`。

#### 17. Token / API key 读取链偏离约束

- **位置**：`utils/data_provider.py:1401-1416`、`utils/tushare_fetcher.py:70-71`、`wyckoff_ai/client.py:23-26`、`wyckoff_ai/pipeline.py:134`
- **问题**：Tushare token 仍可从主 `config.yaml`（`data_source.tushare.token`）读取，错误提示还主动引导用户写入该文件（有随版本库提交外泄风险）；DeepSeek key 同样存在 config 回退，pipeline 提示引导写入 `config_local.yaml`。
- **修复**：token/key 读取链收敛到"环境变量 + 本地忽略配置"；错误提示改为引导设置 `TUSHARE_TOKEN` / `DEEPSEEK_API_KEY` 环境变量。

---

### 其余中危正确性问题

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| 18 | `utils/tushare_fetcher.py:472-489` | 最新交易日缓存无 TTL，长驻 web 进程跨天后仍以昨日为最新交易日，漏取当日数据且误判 up_to_date | 缓存带日期键或 TTL；读写加锁 |
| 19 | `market_data/services.py:218-225` | 港股 overview `close` 未判空即 `float()`，一只股票缺 close 整个端点 500 | 加 `if close is None: continue` |
| 20 | `market_data/services.py:153-156` | 复权 `latest_factor` 为 0/脏数据时 ZeroDivisionError → 500 | 非有限值时走 `resolved="raw", fallback=True` |
| 21 | `market_data/industry.py:140-154` | `_all_rows` 2 万行**静默截断**，覆盖率/成分股基于部分数据无告警 | 超限 raise（与 catalog 一致）或 SQL 侧过滤分页 |
| 22 | `utils/formula_engine.py:60, 272-277` | 沙箱本身无逃逸（AST 白名单严格），但 `ast.Pow` 无限制，`9**9**9**9` 纯常量表达式可造成 CPU/内存耗尽（Web 可触发） | 移除 `ast.Pow` 或限制幂指数为小常量 |
| 23 | `utils/dingtalk_notifier.py:766-778` | 图片通道实际不可用：K 线 PNG base64 后数百 KB，必超钉钉 webhook ~20KB 上限；失败后临时图不删除持续堆积 | 改图床/OSS 取 URL 或砍掉图片通道；finally 清理临时图 |
| 24 | `market_data/services.py:80-84` | `_basic_row` 对存量行调 `canonical_hk_symbol` 会 raise，一条脏行放大为 search/overview/heatmap 端点级 400 | try/except 跳过并计数上报 |
| 25 | `ops/logging.py:22-29` | `EventLogger._append_jsonl` 无锁，并发 rotate+append 竞态丢行 | 加锁或改 `RotatingFileHandler` |
| 26 | `utils/akshare_fetcher.py:770` | 腾讯兜底全量重抓上限 1000 天（约 4 年），不足 6 年目标却被覆盖率检查（门槛仅 ~180 天）放行，长周期策略拿到静默不足的历史 | fetch_state 标记 `coverage_truncated`，或覆盖率按请求年限校验 |
| 27 | `utils/data_provider.py:43-73` | market_cap "auto" 单位启发式（`<1e6` 判亿元）对极端值可能错误放大/缩小 1e8 倍 | provider 写入强制显式 `source_unit`，auto 触发时打日志 |
| 28 | `utils/kline_chart.py:119-138` | 数据 60~113 天时不裁剪显示窗口，画出全部 K 线，与 fast 版行为不一致 | `tail(M)` 裁剪移出 `len>=114` 分支 |
| 29 | `csrc/quant_core.c:19-61` | C 滚动均值用朴素 running-sum，成交量量级（1e8~1e10）× 数千行的累计浮点漂移可能使边界比较在 C/Python 两路径翻转 | 周期性重算窗口和，或等价性测试明确容差 |

---

## 二、需要补强的健壮性问题

### 2.1 SQLite 连接管理（三处同病）

- **位置**：`utils/tushare_ext_store.py:22-28`、`market_data/store.py:50-54`、`ops/store.py:33-36`
- **问题**：`sqlite3.Connection` 作为上下文管理器**只提交/回滚、不关闭**，每次操作靠 GC 兜底回收（工作区残留的 `futures.sqlite-wal/-shm` 即佐证）；且未设 `busy_timeout`，WAL 下并发写可能直接抛 `database is locked`。
- **修复**：统一改为 `contextlib.closing(self.connect())` 或显式 try/finally close；`sqlite3.connect(..., timeout=30)` + `PRAGMA busy_timeout`。

### 2.2 原子写不一致（项目已有标准 mkstemp+replace 模式，以下位置退化为直接 `open('w')`）

| 位置 | 文件 |
|---|---|
| `utils/market_overview.py:72-75, 308` | 全部 JSON 缓存（数 MB 的 heatmap_snapshot.json）；`tushare_stock_map.json` 还与 TushareFetcher 的原子写**并发写同一文件**（两种写法混用） |
| `utils/data_provider.py:242-247` | fetch_state.json |
| `utils/akshare_fetcher.py:395-401, 1495-1497, 1556-1558` | 覆盖基类 `_save_stock_names` 退化为直接写；update_cache 同类 |
| `web_server.py:480-484` | 指数 K 线缓存，无锁且非原子，并发刷新同一指数会交错写坏 |
| `utils/config_schema.py:176-178` | `.bak` 备份用 `write_text` 直接覆盖；tmp 写失败不清理残留 |
| `utils/csv_manager.py:133-141` | mkstemp 写后未 `os.fsync` 即 replace，掉电可能零字节替换（低危） |

- **修复**：统一收敛到一个原子写 helper（mkstemp + flush + fsync + `os.replace`）；`tushare_stock_map.json` 写入收敛到单一模块。

### 2.3 扩展库回填无限流

- **位置**：`utils/tushare_ext_sync.py`（`sync_price_tracks` L273-317、`sync_financials_for_universe` L388-423）
- **问题**：每股票×每 dataset 直接 `self.pro.xxx()`，不复用 TushareFetcher 的滑窗限流器/熔断器。全市场回填 = 数万次无节流调用，必触发限流；非权限类异常直接 raise 使整个 stage 中途报废、已耗配额作废。
- **修复**：注入复用 TushareFetcher 限流器（或独立 deque+Lock 滑窗）；"每分钟调用超限"类错误做退避重试而非上抛。

### 2.4 关闭/退出序列

- `web_server.py:4553-4565`：`/api/system/shutdown` 0.8 秒后 SIGTERM 硬杀，运行中作业不落终态、不写 incident，job 状态永久停留 `running`。→ 关闭前 set 各 cancel_events、标记 `interrupted` 并持久化，给作业 1-2 秒响应窗口。
- `launch_desktop_app.py`：后端线程 `daemon=True`，webview 窗口关闭即硬杀，正在写 CSV/缓存/ops.sqlite 的作业可能中途截断。→ 窗口 close 回调先调优雅关闭再退出。

### 2.5 前端网络健壮性

- `web/static/js/app.js:676-723`：`apiFetch` 无超时；各 workspace `requestJson` 与 `market_context.js:54-66` 同样无超时——后端卡死时按钮状态永久锁死。→ 统一 `AbortSignal.timeout(30000)` 与手动 signal 组合。
- `web/static/js/domain_sync.js:171-179`：轮询一次网络瞬断即 `stopPolling` 永久停止，后端任务还在跑。→ 网络类错误 2-3 次指数退避重试。
- 三处轮询（selection L5266 / update L4398 / diagnostic L4329）`setInterval(1000)` 无防重入，响应慢于 1s 时请求堆叠乱序。→ 改 domain_sync 已有的"完成后 setTimeout"递归模式。
- `app.js:5213`：`runSelection` 的 `abortActiveRequests()` 误杀无关的更新任务轮询 fetch。→ 按用途分组 AbortController。
- `app.js:2417-2456`：股票搜索无序列号/中止保护，慢的旧响应可覆盖新关键词结果。→ 递增请求序号或每次 abort 上一次。
- `app.js:2033-2040, 2282-2285`：heatmap 每次 init 都挂新的 window resize 监听器，失败重试时累积泄漏。→ 模块级只注册一次。

### 2.6 CDN 依赖风险

- **位置**：`web/templates/index.html:10-11`
- **问题**：echarts/chart.js 从 jsdelivr 加载：断网或 CDN 被墙时所有 K 线/热力图/宏观图不可用且只静默降级无提示；无 `integrity`（SRI），CDN 被投毒可注入任意脚本（页面持有 session token）。另外 **chart.js 全项目零引用，纯死重约 200KB**。
- **修复**：echarts 打包进 `/static/vendor/` 本地伺服（至少加 SRI + 失败提示）；删除 chart.js。

### 2.7 策略层短历史与边界

- `strategy/base_strategy.py:44`：`analyze_stock` 只要求 60 天历史，但 B1V242B/P/61 与 B2 依赖 `MA(close,160)`、`SMA(...,100,50)`、`COUNT(...,57)`；全库 `min_periods=1` 使次新股用 60 根 K 线算出严重失真的 HMSHORTWL/HMLONGYL 并可能给出信号。MinJ 系列已有 `MIN_HISTORY_DAYS`（160/114）门槛，这几个没有。→ 为 B1V242B/P/61、B2 补 `MIN_HISTORY_DAYS >= 160`。
- `strategy/formula_strategy.py:43`：缺 `stock_name and` 前置判断，名称缓存缺失时公式选股**静默漏掉整批股票**。→ 与其它策略对齐。
- `strategy/bowl_rebound.py:207-211`：`idxmax` 在 volume 全 NaN 时抛异常被吞；L143 的 `abnormal = EXIST(...)` 列计算后从未使用（双份逻辑漂移风险）。
- `strategy/pattern_matcher.py:114-115, 229`：legacy 回退 `cand.get("price_vs_short", 0)*100-100` 把缺键当 -100%（中性默认应为 1.0）；裸 `except:` 会吞 `KeyboardInterrupt`。
- `strategy/strategy_registry.py:62-79`：裸模块名导入（`import b1_v242b`），可能命中同名第三方模块，同一文件双份加载。→ 统一 `importlib.import_module(f"strategy.{stem}")`。
- `utils/formula_engine.py:311`：深度嵌套触发 `RecursionError` 未转 `FormulaError`，以 500 暴露给 Web 层。

### 2.8 跨进程与并发写

- `utils/csv_manager.py:20-33` + `main.py:153-156`：CSV 锁仅线程级；web 更新作业运行中同时跑 CLI 可并发写同一 CSV / 绕过 provider 切换守卫。→ 数据目录加文件锁（`fcntl.flock` 哨兵文件），CLI 激活前检测 web 作业锁。
- `utils/market_watchlist.py:107-142`：add/remove 读改写无锁，Flask 多线程下批量删除可互相覆盖丢条目。→ 加 `threading.Lock`。
- `utils/market_overview.py:466-491, 688-699`：`socket.setdefaulttimeout()` 进程级全局副作用，影响所有并发网络请求（含进行中的 Tushare 同步）。→ 改为对具体请求传 `timeout=`。
- `utils/selection_worker.py`（线程后端）：多 worker 共享同一批策略实例，若策略持有可变实例状态会静默错乱。→ 线程后端每线程独立实例，或审计注明策略必须无实例状态。
- `web_server.py:1873-1876`：选股取消后 `shutdown(wait=False, cancel_futures=True)`，已在运行的 chunk 继续跑完，若紧接切 provider 残留 chunk 还在读旧目录。→ chunk 内周期检查取消标志。

### 2.9 API 边界校验补齐（低危清单）

| 位置 | 问题 |
|---|---|
| `web_server.py:3281-3293` | `get_wyckoff_job_status` 缺 `_validate_job_id`（其余三个状态端点都有） |
| `web_server.py:3831` | `/api/index-kline` 的 `months` 非数字直接 500，无上界 → try/except + clamp 1..120 |
| `web_server.py:4355-4359` | `add_watchlist_item` 的 `next(...)` 无 default，并发移除时 StopIteration → 500 |
| `web_api/domain_data.py:386-410` | 作业刚终态时 cancel 返回 404"unknown job"，与 sync_status 可见性不一致 |
| `web_api/domain_data.py:73-81` | 终态作业保留按 dict 插入序截断而非终态时间 |
| `ops/health.py:76-83` | `**checks` 展开到顶层，check 名撞 `status/stores` 等保留名会覆盖聚合结果 |
| `market_data/equity_policy.py:69-74` | `strategy_scope` if/else 两分支都返回 `"a_share_only"`，死代码；新增非 A 股策略会被静默误判 |
| `market_data/store.py:56-78` | 版本检查与建表两连接两事务（TOCTOU）；`lru_cache` 无锁首次并发可能双实例 executescript |
| `market_data/economy.py:274-299` | `_release_cursor_builder` 生成的 parameter_builder 是死代码（planner 已覆盖其意图），误导维护者 |
| `web/static/js/app.js:2535-2541` | `applyMacdInputs` 覆盖丢失 `enabled` 字段 |
| `web/static/js/macro_workspace.js:31-35` 等 | `setStatus` 无空元素守卫（futures 版有），DOM 缺失抛 TypeError 中断刷新链 |
| `web/static/js/equity_router.js:33` | sessionStorage 写入无 try/catch，隐私模式下中断路由跳转 |
| `utils/kline_chart.py:55-58` | compress_image 把 JPEG 数据写进 .png 扩展名文件，消费端 MIME 判断可能异常 |

---

## 三、性能优化机会

按收益排序：

| 收益 | 位置 | 问题 | 方案 |
|---|---|---|---|
| 🔥 高 | `utils/market_overview.py:351-376` | 缓存刷新判断逐个 `read_csv` 5000+ 文件取最新日期，请求路径分钟级延迟 | mtime + 抽样；或更新完成时写 `latest_data_date` 哨兵文件 |
| 🔥 高 | `utils/stock_exporter.py:144-184` | 每次搜索全量重建索引（列全部 CSV + 5000 股票两遍拼音转换），单次数百毫秒，是搜索卡顿主因 | 模块级缓存索引（按 data_dir + mtime 失效） |
| 🔥 高 | `utils/tushare_fetcher.py:462-470` | `_to_ts_code` 每次调用从磁盘 `json.load` 数 MB 映射文件，全市场同步 5000+ 次 | 实例内缓存（mtime 失效） |
| 高 | `utils/akshare_fetcher.py:507-520`（另 L1324/L1509、tushare L1221 同类） | iterrows + **list** 成员查找 O(n×m)（5600×5000 次比较） | set + `isin` 向量化 + `dict(zip(...))` |
| 中 | `web_server.py:391-392` | `_load_config()` 每请求链路 4-6 次完整三文件 YAML 解析 | 按三文件 mtime 缓存；配置写端点主动失效 |
| 中 | `web_server.py:2755-2801` | `get_stock_detail`（高频端点）用 iterrows 构造 JSON | `to_dict('records')`，约 10-50 倍提速 |
| 中 | `utils/tushare_ext_views.py:98-119, 230-234` | 纯 Python MA O(n×window)（约 44 万次含 `pd.isna`）；每请求全量重算 MA50/200/MACD | `rolling().mean()`/`ewm()` + 按 (symbol, period, 最新 trade_date) 缓存 |
| 中 | `market_data/services.py:86-114, 203-258`、`futures.py:351` | search/overview/heatmap/contracts 每请求全量拉基础表逐行 `json.loads` + Python 过滤，零缓存 | 按 `(dataset, max_updated_at)` 进程内缓存；过滤下推 SQL |
| 中 | `market_data/industry.py:156-258` | classifications/detail 单请求 4-5 次全表扫描（classify、member 各拉两遍） | `_coverage()` 复用已加载 rows；结果按 `max_updated_at` 缓存 |
| 中 | `web_api/domain_data.py:137-150` | `/domain-status` 每次轮询全表 `COUNT(*)+MAX() GROUP BY`（可达百万行） | 按 sync_state.updated_at 短 TTL 缓存，或同步完成时写入 details |
| 中 | `web/static/js/app.js:4948-5017, 4406-4434` | 进度面板每秒全量 innerHTML 重建（进度条+完整日志），长任务越跑越卡 | 进度条只改 width/textContent；日志增量 append |
| 中 | `web/static/js/app.js:2371-2403` | STOCKS 页一次性渲染 5000+ 行 × 8 列 | 前端分页（每页 200）或增量渲染 |
| 中 | `utils/kline_chart.py:195-214`、`kline_chart_fast.py:75-118` | 每根 K 线一个 Rectangle/plot/bar 调用；dpi=120 构建 dpi=40 保存浪费 9 倍栅格化 | LineCollection/PolyCollection 批量绘制；figure dpi 设为保存 dpi |
| 中 | `strategy/strategy_registry.py:158-173` | `run_all`/`run_strategy` 旧路径无共享特征预计算，8 策略 × 每股重复算 KDJ/HMSHORTWL/HMLONGYL | 循环外先做一次 shared-features 预处理 |
| 中 | `web_server.py:2598-2612` | `/api/stocks` CSV 回退路径最坏一次请求逐股读数千文件（per_page 上限 10000） | 回退路径强制降 per_page 上限（如 200） |
| 中 | `web_server.py:3833, 3854` | `/api/index-kline`、`/api/index-detail` 每请求重建 tushare provider | 缓存命中跳过创建；provider 惰性单例 |
| 低 | `utils/provider_router.py:81-98` | 仓库探测逐 CSV `read_csv(nrows=1)` 5000+ 次，`import pandas` 在循环体内 | 直接读前两行文本解析；import 提到模块级；按目录 mtime 缓存 |
| 低 | `utils/data_provider.py:313-364, 807-812, 1239-1241` | 增量路径重复 read + 重复 `_preserve_existing_metrics`；`_quick_row_count` 逐行遍历全文件；去重集合循环内重建 O(n²) | 去掉外层预处理；块计数 `\n`；循环前构建一次 seen 集合 |
| 低 | `utils/tushare_ext_workflow.py:52`、`tushare_ext_views.py:338-350` | 只需最近 2 个交易日却全量加载 trade_cal 反序列化 | `limit=count*3` 或 SQL `WHERE is_open=1 ... LIMIT n` |
| 低 | `utils/akshare_fetcher.py:345-383` | 每 attempt×mode 新建 `requests.Session`，无连接复用（模块级 session 创建后从未使用） | 实例级持有 direct/proxy 两个 Session |
| 低 | `ops/store.py:38-49, 73-83`、`ops/health.py:52-53` | 每条事件新建连接+独立事务；dataset/symbol 过滤无索引 + 每次额外 COUNT；每 dataset 一次 get_sync_state | 线程本地连接/写队列；补索引或改 has_more 语义；批量 `list_sync_states` |
| 低 | `web/templates/index.html:10` | chart.js 全项目零引用，纯死重 ~200KB | 删除该 script 标签 |
| 低 | `web/static/js/app.js:2441-2450, 2808-2822` | 搜索排序 O(n²)（比较器内 indexOf）；详情图表每次 dispose+init | 预构建 Map(code→rank)；复用 ECharts 实例 `setOption(notMerge)` |
| 低 | `strategy/b2_beta.py:123-126`、`b1_min_j_complex.py:36-43` 等 | B2 白算一次双层 EMA 随即被覆盖；MinJ 系列信号列双算 | if/else；基础计算函数加 `skip_signal=True` |
| 低 | `utils/technical.py:616-622` | `calculate_zhixing_main_overlay` 逐元素 `Series.iloc` 做 KDJ 递归（项目他处已注明 numpy 快 50x+） | 改 numpy 数组循环或复用 `quant_core.kdj_ascending` |
| 低 | `wyckoff_ai/renderer.py:18-29`、`dingtalk_notifier.py:930-1017` | 每次分析重复 exec_module 加载绘图脚本；带图发送串行阻塞任务线程数分钟 | `lru_cache` 缓存模块；图片生成/发送移入后台队列（先修图片通道） |
| 低 | `web_server.py:926-930, 4392-4393, 4030, 3803-3806` | `_load_stock_names` 每次读两份 JSON；watchlist 循环内重建 store；GET 端点内 `ensure_market_caches` 可能触发重建；`get_stats` 每请求读 50 个 CSV | 按 mtime 缓存；提到循环外；GET 只读不建；结果缓存 60s |

---

## 四、做得好的地方（无需改动）

- **SyncEngine 状态机**：阶段划分（preflight→terminal）、权限失败降级为结构化 `PERMISSION_DENIED` warning、`_safe_set_state` 失败升级、`upsert_rows` executemany 批写、同域准入 + 全局 2 并发在锁内原子判定——实现严谨。
- **宏观 GDP 差分合规**：`_series_points` 按年分桶、缺失季度即跳过，不跨年不跨缺失季度相减，源行原样保存。
- **公式引擎沙箱无逃逸**：AST 白名单严格（无 Attribute/Subscript/字符串常量/keyword/lambda），未发现任意代码执行路径（遗留仅资源型 DoS，见问题 22）。
- **Wyckoff schema 校验扎实**：日期必须落在真实交易日（±10 天 snap）、事件价格与当日 OHLC 容差校验、全部文本截断限长——模型幻觉难以进入渲染层；renderer 只用本地可信绘图代码，AI 只提供结构化标注，架构正确。
- **前端安全**：`escapeHtml` 在所有 innerHTML 拼接处覆盖到位，未发现可利用 XSS；副作用请求均带 `X-Quant-Session`；domain_sync 用 DOM API 构建详情天然免 XSS。
- **域工作区生命周期**：futures/macro/industry/system 四个 workspace 的 deactivate（abort + 监听器 + 定时器 + 图表 dispose）规范统一。
- **约束合规确认**：job map 修剪同步移除 cancel 事件 ✅；provider 空仓库拒绝 ✅；token 主线无 `set_token`、诊断只报 `token_present` ✅；分组元数据来自 `/api/selection/options` 无第二套硬编码 ✅；`quantKlinePreferences` 版本化 + 旧键迁移 ✅；A 股/港股能力显式注册、HK policy 无涨跌停/ST 规则 ✅。
- **通知限流**：钉钉每分钟 20 条 + 660026 指数退避实现完善，requests 均带 timeout。
- **C/Python 等价性设计**：核心递归（EMA/SMA/KDJ）逐位对齐，NaN 一律降级 Python，边界清晰（残留风险仅 running-sum 漂移与 Python 回退自身 NaN 缺陷）。

---

## 五、建议的修复批次

| 批次 | 内容 | 预估 |
|---|---|---|
| **第一批：数据正确性 + 安全** | 问题 1（amount=0 污染）、2（heatmap GET 副作用 + 重建锁）、3（脱敏正则 + error_logging/代理旁路）、4（趋势线反转）、17（token 读取链收敛） | 半天 |
| **第二批：并发/状态机** | 问题 6（TaskRegistry 快照）、7（锁内 sleep(62)）、8（准入 TOCTOU）、9（急停 finally）、10（Wyckoff 准入 + cancelled 终态）、2.1（SQLite 连接管理） | 1 天 |
| **第三批：隔离 + 指标语义** | 问题 13/14（港股导出 + 轮询串扰）、15（KDJ/EXIST/COUNT/vol_ratio NaN）、16（DTW 归一化）、5（fut_mapping）、2.7（B1/B2 历史门槛 + FormulaStrategy 名称过滤） | 1 天 |
| **第四批：健壮性补强** | 2.2（原子写统一）、2.3（扩展回填限流）、2.4（关闭序列）、2.5（前端超时/轮询）、2.6（CDN 本地化）、2.9（API 边界清单） | 1-2 天 |
| **第五批：性能** | 按第三节收益从高到低：缓存刷新哨兵 → 搜索索引缓存 → `_to_ts_code` 缓存 → iterrows 向量化 → config/detail/views 缓存 → 前端增量渲染 | 按需分摊 |

每项修复完成后运行焦点测试（`tests/` 下已有对应等价性/API/前端静态测试），批次末跑全量 `.venv/bin/python -m pytest -q` 回归（基线：389 passed）。

---

*报告生成于 2026-07-28，基于五个子系统的并行深度审查与高危项源码二次验证。*
