# GitHub Actions 自动构建APK指南

## 快速开始

### 1. 创建GitHub仓库

1. 访问 https://github.com/new
2. 仓库名称：`a-share-quant-selector`
3. 选择 `Private`（私有仓库，保护你的密钥）
4. 点击 `Create repository`

### 2. 推送代码到GitHub

```bash
# 进入项目目录
cd /Users/chenxingyu/DocumentsData/ElderDocuments/a-share-quant-selector

# 初始化Git仓库（如果还没有）
git init

# 添加远程仓库
git remote add origin https://github.com/YOUR_USERNAME/a-share-quant-selector.git

# 添加所有文件
git add .

# 提交
git commit -m "Initial commit: Android app with GitHub Actions"

# 推送
git push -u origin main
```

### 3. 触发自动构建

**方法一：推送到main分支**
```bash
# 任意修改后推送
git add .
git commit -m "Trigger build"
git push
```

**方法二：手动触发**
1. 访问仓库页面
2. 点击 `Actions` 标签
3. 选择 `Build Android APK`
4. 点击 `Run workflow`
5. 选择分支，点击 `Run workflow`

### 4. 下载APK

**方法一：从Actions下载**
1. 访问仓库的 `Actions` 页面
2. 点击最新的构建任务
3. 在 `Artifacts` 部分下载 `quant-selector-debug-apk`

**方法二：从Releases下载**
1. 访问仓库的 `Releases` 页面
2. 下载最新的 `app-debug.apk` 文件

## 详细步骤

### 步骤1：配置GitHub仓库

#### 1.1 创建仓库

1. 登录GitHub
2. 点击右上角 `+` → `New repository`
3. 填写信息：
   - Repository name: `a-share-quant-selector`
   - Description: `A股量化选股系统 - Android版`
   - 选择 `Private`（推荐）
   - 不要初始化README（我们已有代码）
4. 点击 `Create repository`

#### 1.2 配置Git（如果还没有）

```bash
# 检查Git配置
git config --global user.name
git config --global user.email

# 如果没有配置
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

### 步骤2：推送代码

#### 2.1 初始化仓库

```bash
cd /Users/chenxingyu/DocumentsData/ElderDocuments/a-share-quant-selector

# 检查是否已初始化
git status

# 如果没有初始化
git init
```

#### 2.2 添加远程仓库

```bash
# 替换 YOUR_USERNAME 为你的GitHub用户名
git remote add origin https://github.com/YOUR_USERNAME/a-share-quant-selector.git

# 验证远程仓库
git remote -v
```

#### 2.3 提交并推送

```bash
# 添加所有文件
git add .

# 查看将要提交的文件
git status

# 提交
git commit -m "Initial commit: Android app with GitHub Actions"

# 推送
git push -u origin main
```

### 步骤3：等待构建

1. 推送后，GitHub Actions会自动开始构建
2. 访问仓库的 `Actions` 页面查看构建进度
3. 构建通常需要10-20分钟

### 步骤4：下载APK

#### 方法A：从Artifacts下载

1. 点击最新的构建任务
2. 向下滚动到 `Artifacts` 部分
3. 点击 `quant-selector-debug-apk` 下载
4. 解压zip文件得到APK

#### 方法B：从Releases下载

1. 访问仓库的 `Releases` 页面
2. 找到最新的release
3. 下载 `app-debug.apk` 文件

### 步骤5：安装到手机

1. 将APK文件传输到手机（通过USB、邮件、云盘等）
2. 在手机上打开文件管理器
3. 找到APK文件并点击安装
4. 如果提示"未知来源"，需要在设置中允许

## 构建配置说明

### 自动触发条件

```yaml
on:
  push:
    branches: [ main, android-build ]
    paths:
      - 'android-app/**'
  workflow_dispatch:
```

- 推送到 `main` 或 `android-build` 分支时自动构建
- 只有修改 `android-app` 目录下的文件才会触发
- 支持手动触发

### 构建环境

- **操作系统**: Ubuntu Latest
- **JDK**: 17 (Eclipse Temurin)
- **Android SDK**: API 34
- **Gradle**: 8.2

### 构建产物

- **Debug APK**: `app-debug.apk`
- **保留时间**: 30天

## 常见问题

### Q1: 构建失败怎么办？

**查看错误日志**：
1. 点击失败的构建任务
2. 展开失败的步骤
3. 查看错误信息

**常见原因**：
- 依赖下载失败：重新触发构建
- 代码错误：检查代码并修复
- 内存不足：GitHub Actions会自动重试

### Q2: 如何更新密钥？

1. 编辑 `android-app/app/src/main/python/android_adapter.py`
2. 替换Tushare Token和DeepSeek API Key
3. 提交并推送

```bash
git add .
git commit -m "Update API keys"
git push
```

### Q3: 如何自定义构建？

编辑 `.github/workflows/build-android.yml`：

```yaml
# 修改Android SDK版本
- name: Install Android SDK components
  run: |
    sdkmanager "platforms;android-34" "build-tools;34.0.0"

# 修改Gradle版本
- name: Create gradle wrapper
  run: |
    gradle wrapper --gradle-version 8.2
```

### Q4: 构建需要多长时间？

- 首次构建：15-25分钟（下载依赖）
- 后续构建：5-15分钟（有缓存）

### Q5: 如何加快构建速度？

1. **使用缓存**：GitHub Actions会自动缓存Gradle依赖
2. **减少依赖**：只安装必要的Python包
3. **并行构建**：默认已启用

## 高级配置

### 配置签名（发布版本）

1. 生成签名密钥：
```bash
keytool -genkey -v -keystore my-release-key.jks -keyalg RSA -keysize 2048 -validity 10000 -alias my-alias
```

2. 添加到GitHub Secrets：
   - 访问仓库 Settings → Secrets and variables → Actions
   - 添加以下secrets：
     - `KEYSTORE_BASE64`: base64编码的keystore文件
     - `KEYSTORE_PASSWORD`: keystore密码
     - `KEY_ALIAS`: 密钥别名
     - `KEY_PASSWORD`: 密钥密码

3. 修改工作流文件：
```yaml
- name: Decode keystore
  run: |
    echo "${{ secrets.KEYSTORE_BASE64 }}" | base64 -d > android-app/app/my-release-key.jks

- name: Build Release APK
  working-directory: android-app
  run: ./gradlew assembleRelease
  env:
    KEYSTORE_FILE: my-release-key.jks
    KEYSTORE_PASSWORD: ${{ secrets.KEYSTORE_PASSWORD }}
    KEY_ALIAS: ${{ secrets.KEY_ALIAS }}
    KEY_PASSWORD: ${{ secrets.KEY_PASSWORD }}
```

### 添加通知

在工作流文件中添加：
```yaml
- name: Notify on success
  if: success()
  uses: actions/github-script@v6
  with:
    script: |
      github.rest.issues.create({
        owner: context.repo.owner,
        repo: context.repo.repo,
        title: 'APK构建成功',
        body: 'APK已构建完成，请在Actions中下载。'
      })
```

## 安全注意事项

### 1. 保护密钥

- **不要**将密钥直接写在代码中
- **使用** GitHub Secrets 存储敏感信息
- **设置** 仓库为私有

### 2. 限制权限

- 只给必要的协作者访问权限
- 定期更换密钥
- 监控仓库访问日志

### 3. 审查代码

- 定期检查构建日志
- 确保没有敏感信息泄露
- 使用 `.gitignore` 排除敏感文件

## 相关链接

- [GitHub Actions文档](https://docs.github.com/en/actions)
- [Android构建文档](https://developer.android.com/build)
- [Gradle文档](https://docs.gradle.org/)

## 获取帮助

如果遇到问题：

1. 查看本文档的常见问题部分
2. 搜索GitHub Issues
3. 查看构建日志
4. 提交新的Issue

---

**最后更新**: 2026-06-09
