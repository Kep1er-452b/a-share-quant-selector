# 用户上车暂停检查点

**历史检查点：已于 2026-09-13 收到“请继续”后恢复。当前已完成状态见 `server-results-verification-2026-09-13.md`。以下保留当时现场，不能作为当前故障状态。**

暂停时间：2026-09-12 22:06 左右（北京时间）。用户明确要求立即停止；收到“继续”前不得恢复实施、同步或测试。

## 已停

- 已向本机 `/api/server-results/cancel` 发出取消（200）。本轮最新任务此前已失败。
- 已停止本轮启动的 5080 测试服务 PID 25127，浏览器后续不能触发后台重试。
- 无日常任务重跑、无飞书消息发送、无 Git 提交或推送。服务器原有定时/飞书服务未停止。

## 代码状态

- 本机基线 `0f0d210`：新增 `utils/server_results/{contract,transport,service}.py`、`web_api/server_results.py`、`web/static/js/server_results.js`、测试及使用说明；已接入选股页服务器/本机来源切换。顶部 RUN 在服务器模式下仅同步。
- 服务器源码基线 `6c4fbc5`：新增 `quant_server/result_reader.py`、`tests/test_result_reader.py`，更新 AGENTS 的只读结果通道边界。
- 两端所有改动均未暂存/未提交。规划文档仍保留最初设计状态，实际状态以此检查点为准。

## 已验证

- App 自带 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` 在线；默认 `/usr/local/bin/tailscale` 是另一实例且停止。
- 通过 App 的 `nc` 代理与现有 Tailscale SSH 授权连上真实 Azure 主机 AntigravityAnchor；身份为既有 weihanchen，严格主机指纹校验通过。
- 最新真实结果为 `20260911-r2-6c60799a04e0`，发布序号 4；1048 条策略信号、41648 条评估、601 条每日追踪、15 个持续周期、4 张图表。
- 服务器结果契约和导出模块与本地源码 SHA-256 一致。
- 只读模块已安装在线上 `/opt/a-share-quant-server/quant_server/result_reader.py`；当前 gzip 版本 SHA-256 为 `ec736fc7eecddf74fc3c5bf25d00f723889d5b72e87bfc1dbe4d1e854883d334`。原始部署记录在 `/var/backups/aqs-result-reader-20260912/`，没有替换计算模块或重启服务。
- 本机首轮全量 454 passed，服务器项目全量 266 passed；后续 gzip/解压限额修改的聚焦测试为本机 12 passed、服务器 5 passed。最终代码尚需重新回归。
- 线上读取组件初版 5 tests passed；gzip 版本线上尚需重跑。线上 doctor、doctor --network 和 secret scan 均通过，飞书/worker/timer 均 active。本机服务器源码 doctor 用完整模板和占位凭据、临时目录通过（不是本机真实凭据验证）。
- 浏览器已验证服务器模式显示、真实下载中取消并变为已取消、空缓存状态及无 JS error。真实结果表/图表尚未验收。

## 尚未完成与下一步

- **尚未成功激活任何真实完整结果包，不能宣称双端已打通。** 初次 raw 传输因两分钟超时失败；改为可取消的进展超时、应用层 gzip 后下载推进到约 36%，最新任务 `04f00852183b4d13bcdead6e76ea2866` 于 14:06 UTC 失败，错误为通用“服务器读取失败”。下一步首先定位该具体制品的远端退出原因，避免只重复重试。按当前循环顺序 36% 对应 watchlist-snapshot，但需核验请求和代码，不能当已证实原因。
- 本机真实连接配置位于忽略的 `data/server_results/connection.json`（600），私钥只引用既有文件；不要输出正文。缓存尚无 current.json，失败暂存区已由 TemporaryDirectory 清理。
- 完成真实整包校验、页面分策略/分页/日报/图表/香港隔离/重新进入页面检查；检验 RUN 不启动本地 worker。修复潜在页面异步竞态及中文状态显示、空闲轮询频率。
- 补充或复核：追踪唯一键校验、可选缺失文件重试策略、数值溢出回归、传输错误诊断可用性；现有错误文本已做脱敏，但不应妨碍定位。
- 更新使用说明中的实际结果与限制、执行必要回归和 diff/秘密检查。不要运行全市场更新或发送通知作为验证。
- 浏览器会话已有 `browser`/`tab` 绑定，采用 Browser skill；测试 tab 在暂停时关闭，恢复需从现有 browser 新建 tab，不重新选择浏览器或读取完整文档。

恢复先重新核对两个工作区 Git 状态、Tailscale 和线上只读模块状态，再从下载失败点继续。
