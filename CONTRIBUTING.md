# 贡献指南

感谢你关注 A-Share Quant Selector。提交 Issue 或 Pull Request 前，请先确认改动与项目目标相关，并避免提交本地数据、日志或凭据。

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config/config.yaml.template config/config.yaml
```

需要访问数据服务时，请使用环境变量或本地配置文件提供凭据；不要把真实 Token、Webhook 或 API Key 写入代码、测试数据、Issue 或提交记录。

## 提交前检查

```bash
.venv/bin/pytest -q
git diff --check
```

涉及 Web 前端时，也请执行对应的 JavaScript 语法检查，并在本地启动 Web 应用验证实际交互。除非任务明确要求，不要用全市场更新或通知发送来验证普通代码改动。

## Pull Request 建议

- 说明改动目的、影响范围和验证方式。
- 保持一个 Pull Request 聚焦一个主题。
- 更新受影响的文档和测试。
- 不要提交 `data/`、`logs/`、运行输出、本地配置、编辑器状态或备份文件。
- 涉及数据源、策略公式或市场规则时，补充可复现的回归测试。
