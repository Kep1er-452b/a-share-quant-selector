# Windows 快速启动

## 第一次启动 Web 端

1. 安装 Python 3.10+，安装时勾选 `Add python.exe to PATH`。
2. 双击项目根目录的 `Start-Quant-Web.bat`。
3. 脚本会自动创建 Windows 版 `.venv`、安装依赖，并打开 `http://127.0.0.1:5080`。

如果从 Mac 搬来的 `.venv` 还在，启动脚本会自动把它重命名为 `.venv_mac_backup_yyyyMMdd_HHmmss`，再创建 Windows 虚拟环境。

## 日常命令

PowerShell 中可以这样运行：

```powershell
.\quant.ps1 web
.\quant.ps1 init --provider tushare
.\quant.ps1 run --provider tushare
.\quant.ps1 select --strategy B1V24261Strategy --force-select
```

单股 CSV 导出现在会写到当前 Windows 用户的 `Downloads` 目录。

## 重新全量抓取

迁移过来的旧行情 CSV 已经清理。重新抓取时建议先设置 Tushare Token：

```powershell
$env:TUSHARE_TOKEN = "你的 token"
.\quant.ps1 init --provider tushare
```
