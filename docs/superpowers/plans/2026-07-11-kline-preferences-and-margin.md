# K 线偏好与两融余额修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让指数和个股 K 线使用可见窗口之前的本地历史计算指标，持久化全部全局 K 线控制项，并正确展示存在发布滞后的 Tushare 两融余额。

**Architecture:** 详情 API 返回可见 K 线和独立的有限预热上下文；前端在上下文上计算可配置指标并裁剪到可见日期。浏览器使用一个版本化偏好对象并迁移旧键。Market Pulse 为两融指标单独选择不晚于市场日期的最近两个有效日期。

**Tech Stack:** Python 3、Flask、pandas、SQLite、vanilla JavaScript、ECharts、pytest。

## Global Constraints

- 所有指数和个股共享一套偏好，不按标的拆分。
- 可见数组长度继续服从 260、520、1000、全部和 2500 根服务端上限。
- 周 K、月 K 在完整日线历史聚合之后再截取。
- API 仅做向后兼容增量，不改变 provider CSV。
- 缺失数值显示 `--`，不得转换为零。
- 使用 `.venv/bin/python` 和 `.venv/bin/pytest`。

---

### Task 1: 有限的 K 线计算上下文

**Files:**
- Modify: `web_server.py`
- Modify: `utils/tushare_ext_views.py`
- Modify: `web/static/js/app.js`
- Test: `tests/test_tushare_extension_web.py`
- Test: `tests/test_tushare_extension_views.py`

**Interfaces:**
- Produces: 股票响应 `calculation_data: list[dict]`；指数 payload `calculation_candles: list[dict]`。
- Consumes: `indicator_lookback` 查询参数，整数范围 1..600，默认 300。

- [ ] **Step 1: 写股票接口失败测试**

构造 500 根日线，请求 `limit=260&indicator_lookback=200`，断言 `data` 为 260 根、`calculation_data` 为 459 根，且可见数据仍为最新 260 根。

- [ ] **Step 2: 写指数 payload 失败测试**

构造 500 根 `index_daily`，调用 `build_index_kline_payload(..., limit=260, indicator_lookback=200)`，断言 `candles` 为 260 根、`calculation_candles` 包含前置 199 根，第一根可见 candle 的 MA200 非空。

- [ ] **Step 3: 验证测试按预期失败**

Run: `.venv/bin/pytest -q tests/test_tushare_extension_web.py::test_stock_detail_api_returns_bounded_indicator_context tests/test_tushare_extension_views.py::test_index_payload_returns_bounded_indicator_context`

Expected: FAIL，缺少新参数或新响应字段。

- [ ] **Step 4: 实现服务端上下文**

在完成周期聚合和完整历史指标计算后，使用：

```python
lookback = _parse_indicator_lookback(request.args.get("indicator_lookback"))
context_count = min(total_bars, limit + max(lookback - 1, 0))
visible_rows = df.head(limit)
context_rows = df.head(context_count)
```

股票序列保持现有 newest-first API 契约。指数构建函数在完整升序数组上计算固定 MA，再分别切出 `candles` 和 `calculation_candles`。

- [ ] **Step 5: 前端请求上下文并只绘制可见范围**

新增：

```javascript
function indicatorLookback() {
    const longestMa = Math.max(2, ...state.maSettings.map(item => Number(item.window) || 0));
    const macdWarmup = Math.max(120, Number(state.macdSettings.slow || 26) * 5);
    return Math.min(600, Math.max(longestMa, macdWarmup));
}
```

股票和指数详情请求附加 `indicator_lookback`。`renderStockChart` 接收升序 calculation 数据用于 MA/MACD 计算，并按可见日期裁剪 series；K 线、tooltip、dataZoom 和右侧快照仍只使用 visible 数据。

- [ ] **Step 6: 运行相关测试**

Run: `.venv/bin/pytest -q tests/test_tushare_extension_web.py tests/test_tushare_extension_views.py`

Expected: PASS。

---

### Task 2: 版本化全局 K 线偏好

**Files:**
- Modify: `web/static/js/app.js`
- Modify: `web/templates/index.html`（仅当已有控制项缺少可表达的显隐状态时）
- Test: `tests/test_tushare_extension_frontend_static.py`
- Create: `tests/js/kline_preferences.test.js`

**Interfaces:**
- Produces: `loadKlinePreferences()`、`saveKlinePreferences()`、`normalizeKlinePreferences()`。
- Storage key: `quantKlinePreferences`，schema `version: 1`。

- [ ] **Step 1: 写偏好迁移和恢复失败测试**

Node 测试用内存 localStorage 覆盖以下行为：新对象优先；缺少新对象时读取四个旧键；损坏字段回退默认值；保存后重载保持 period、limit、MA、sequence、MACD 和 indexMonths。

- [ ] **Step 2: 验证测试失败**

Run: `node --test tests/js/kline_preferences.test.js`

Expected: FAIL，偏好函数或统一 key 尚不存在。

- [ ] **Step 3: 实现偏好模块并初始化 state**

统一对象形状：

```javascript
{
  version: 1,
  period: 'daily',
  limit: '260',
  movingAverages: [{ window: 50, color: '#ffd700', enabled: true }],
  sequenceEnabled: true,
  macd: { fast: 12, slow: 26, signal: 9, enabled: true },
  indexMonths: 3,
}
```

规范化 period、limit、数值上下限、颜色和布尔值。新对象存在时不再从旧键覆盖。

- [ ] **Step 4: 所有控制事件调用统一保存**

周期、范围、MA 添加/删除或启用、十三转、MACD 参数/启用和首页指数月份在操作后调用 `saveKlinePreferences()`。打开任意新详情时使用全局 `state.currentStockPeriod`。

- [ ] **Step 5: 运行 JS 与静态测试**

Run: `node --test tests/js/kline_preferences.test.js && node --check web/static/js/app.js && .venv/bin/pytest -q tests/test_tushare_extension_frontend_static.py`

Expected: PASS。

---

### Task 3: 两融余额使用最近有效日期

**Files:**
- Modify: `utils/tushare_ext_views.py`
- Modify: `web/static/js/app.js`
- Test: `tests/test_tushare_extension_views.py`
- Test: `tests/test_tushare_extension_frontend_static.py`

**Interfaces:**
- Produces: margin metric 增加 `as_of_date` 与 `previous_as_of_date`。
- Consumes: `margin` 数据集中不晚于 Market Pulse 日期的最近两个有效交易日。

- [ ] **Step 1: 写滞后日期失败测试**

市场日期为 `20260709`，margin 仅有 `20260708` 和 `20260706`。断言余额使用 7 月 8 日、delta 与 7 月 6 日相比，并返回两个日期。

- [ ] **Step 2: 写缺失值渲染约束测试**

静态前端测试断言卡片支持 `截至 MM-DD`，且 `formatTradingValue(null, ...)` 返回 `--` 而不是通过 `Number(null)` 得到零。

- [ ] **Step 3: 验证测试失败**

Run: `.venv/bin/pytest -q tests/test_tushare_extension_views.py::test_market_trading_summary_uses_latest_available_margin_date tests/test_tushare_extension_frontend_static.py`

Expected: FAIL，现有逻辑只查询市场日且 null 被格式化为 0。

- [ ] **Step 4: 实现两融独立日期选择**

新增 helper 返回不晚于 cutoff 的最近数据日期；再以第一日期为 cutoff 查上一日期。使用这两个日期查询并汇总 margin。通过 `_metric(..., as_of_date=..., previous_as_of_date=...)` 返回日期元数据。

- [ ] **Step 5: 修复渲染和日期标注**

`formatTradingValue` 与 `formatTradingDelta` 在转换 Number 前显式判断 `value === null || value === undefined || value === ''`。卡片 subline 对带 `as_of_date` 的指标显示 `截至 MM-DD · 较前值 ...`。

- [ ] **Step 6: 运行相关测试**

Run: `.venv/bin/pytest -q tests/test_tushare_extension_views.py tests/test_tushare_extension_frontend_static.py`

Expected: PASS。

---

### Task 4: 集成验证与交接更新

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Tasks 1-3 的最终行为。
- Produces: 当前 commit 对应的架构和验证记录。

- [ ] **Step 1: 运行语法和定向测试**

Run: `.venv/bin/python -m py_compile web_server.py utils/tushare_ext_views.py`

Run: `node --check web/static/js/app.js`

Run: `.venv/bin/pytest -q tests/test_tushare_extension_web.py tests/test_tushare_extension_views.py tests/test_tushare_extension_frontend_static.py`

- [ ] **Step 2: 运行完整回归**

Run: `.venv/bin/pytest -q`

Expected: 0 failed。

- [ ] **Step 3: 浏览器和重启验证**

启动 `127.0.0.1:5080`，验证股票与指数 260 根视图第一根 MA200、全局周期/范围/MA/十三转/MACD/指数月份恢复，以及两融余额显示最近日期。退出再启动应用后复查偏好。

- [ ] **Step 4: 更新交接文档并检查差异**

在 `AGENTS.md` Current Handoff 写入本次新不变量、测试结果与浏览器结果，然后运行：

```bash
git diff --check
git status --short --branch
git diff --stat
```

- [ ] **Step 5: 提交实现**

```bash
git add AGENTS.md web_server.py utils/tushare_ext_views.py web/static/js/app.js web/templates/index.html tests
git commit -m "Fix K-line context preferences and margin balance"
```

