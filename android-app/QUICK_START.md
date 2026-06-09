# 快速开始 - 获取APK安装包

## 最简单的方式：GitHub Actions自动构建

### 第1步：创建GitHub仓库（2分钟）

1. 访问 https://github.com/new
2. 填写：
   - Repository name: `a-share-quant-selector`
   - 选择 **Private**（私有仓库）
   - **不要**勾选任何初始化选项
3. 点击 `Create repository`

### 第2步：推送代码（3分钟）

在终端执行以下命令（替换 `YOUR_USERNAME` 为你的GitHub用户名）：

```bash
cd /Users/chenxingyu/DocumentsData/ElderDocuments/a-share-quant-selector

# 初始化Git
git init

# 添加远程仓库
git remote add origin https://github.com/YOUR_USERNAME/a-share-quant-selector.git

# 添加所有文件
git add .

# 提交
git commit -m "Initial commit"

# 推送
git push -u origin main
```

### 第3步：等待构建（10-20分钟）

1. 访问你的仓库页面
2. 点击 `Actions` 标签
3. 你会看到 `Build Android APK` 正在运行
4. 等待绿色勾号出现（约10-20分钟）

### 第4步：下载APK（1分钟）

**方法A：从Artifacts下载**
1. 点击最新的构建任务
2. 向下滚动到 `Artifacts` 部分
3. 点击 `quant-selector-debug-apk` 下载
4. 解压zip文件得到 `app-debug.apk`

**方法B：从Releases下载**
1. 访问仓库的 `Releases` 页面
2. 下载最新的 `app-debug.apk`

### 第5步：安装到手机（1分钟）

1. 将APK传输到手机（USB/邮件/微信/云盘）
2. 在手机上打开文件管理器
3. 点击APK文件安装
4. 如果提示"未知来源"，在设置中允许

---

## 使用脚本一键设置

如果你觉得手动操作麻烦，可以使用我准备的脚本：

```bash
cd /Users/chenxingyu/DocumentsData/ElderDocuments/a-share-quant-selector/android-app

# 替换 YOUR_USERNAME 为你的GitHub用户名
chmod +x scripts/setup_github.sh
./scripts/setup_github.sh YOUR_USERNAME
```

脚本会自动：
- 初始化Git仓库
- 配置远程仓库
- 提交代码
- 推送到GitHub

---

## 配置你的密钥

在推送代码之前，你需要配置Tushare和DeepSeek密钥：

1. 打开文件：`android-app/app/src/main/python/android_adapter.py`
2. 找到以下代码并替换密钥：

```python
# Tushare Token
'tushare': {
    'token': 'YOUR_TUSHARE_TOKEN'  # 替换为你的Token
},

# DeepSeek API Key
'deepseek_api_key': 'YOUR_DEEPSEEK_API_KEY'  # 替换为你的Key
```

---

## 常见问题

### Q: 构建失败怎么办？

A: 查看Actions页面的构建日志，通常是网络问题，重新触发构建即可。

### Q: 如何重新构建？

A: 在Actions页面点击 `Run workflow` 按钮，或推送任意修改。

### Q: APK有多大？

A: 约100-200MB（包含Python解释器和所有依赖）。

### Q: 支持哪些Android版本？

A: 支持Android 8.0 (API 26) 及以上版本。

### Q: 需要联网吗？

A: 首次使用需要联网获取股票数据，之后可以离线查看已有数据。

---

## 总结

整个过程大约需要 **20-30分钟**：

| 步骤 | 时间 | 说明 |
|------|------|------|
| 创建仓库 | 2分钟 | 在GitHub上创建 |
| 推送代码 | 3分钟 | 终端执行命令 |
| 等待构建 | 10-20分钟 | 自动进行 |
| 下载安装 | 2分钟 | 传输到手机 |

**总计**：约20-30分钟

---

## 获取帮助

- 查看详细文档：`android-app/GITHUB_ACTIONS_GUIDE.md`
- 查看故障排除：`android-app/TROUBLESHOOTING.md`
- 提交Issue：在GitHub仓库中提交

---

**最后更新**: 2026-06-09
