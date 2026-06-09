# 测试和验证指南

## 概述

本文档说明如何测试和验证Android版A股量化选股系统的功能。

## 测试环境

### 1. 硬件要求

- **Android手机**：Android 16 (API 36) 或更高版本
- **存储空间**：至少2GB可用空间
- **内存**：至少4GB RAM

### 2. 软件要求

- **Android Studio**：最新稳定版
- **JDK**：17或更高版本
- **Android SDK**：API 34 或更高
- **ADB**：Android Debug Bridge

### 3. 网络要求

- **网络连接**：WiFi或移动数据
- **网络权限**：允许应用访问网络

## 测试步骤

### 1. 构建测试

#### 1.1 检查Gradle配置

```bash
cd android-app
./gradlew tasks
```

**预期输出**：显示所有可用的Gradle任务

#### 1.2 构建Debug APK

```bash
./scripts/build_apk.sh debug
```

**预期输出**：
- 构建成功
- 生成APK文件：`app/build/outputs/apk/debug/app-debug.apk`
- APK大小：约100-200MB

#### 1.3 检查APK内容

```bash
# 查看APK信息
aapt dump badging app/build/outputs/apk/debug/app-debug.apk | head -20

# 查看APK大小
ls -lh app/build/outputs/apk/debug/app-debug.apk
```

### 2. 安装测试

#### 2.1 连接设备

```bash
# 检查设备连接
adb devices

# 输出示例：
# List of devices attached
# XXXXXXXX	device
```

#### 2.2 安装APK

```bash
# 安装APK
adb install app/build/outputs/apk/debug/app-debug.apk

# 如果已安装，使用-r覆盖安装
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

**预期输出**：`Success`

#### 2.3 检查安装状态

```bash
# 查看已安装的应用
adb shell pm list packages | grep quant

# 输出示例：
# package:com.quant.aselector
```

### 3. 启动测试

#### 3.1 启动应用

```bash
# 启动应用
adb shell am start -n com.quant.aselector/.MainActivity
```

**预期行为**：
- 应用启动成功
- 显示启动画面
- 进入主界面

#### 3.2 检查日志

```bash
# 查看应用日志
adb logcat -s "QuantApplication" "MainActivity" "FlaskService"

# 查看Python日志
adb logcat -s "python"
```

**预期日志**：
```
D/QuantApplication: Application initialized
D/MainActivity: onCreate
D/FlaskService: FlaskService created
I/FlaskService: Starting Flask server...
```

### 4. 功能测试

#### 4.1 Flask服务启动测试

**测试步骤**：
1. 启动应用
2. 等待Flask服务启动
3. 观察状态栏显示

**预期行为**：
- 状态栏显示"Flask服务已启动"
- WebView加载成功
- 显示系统概览页面

**验证方法**：
```bash
# 检查Flask服务是否运行
adb shell netstat -tlnp | grep 5080

# 输出示例：
# tcp 0 0 127.0.0.1:5080 0.0.0.0:* LISTEN
```

#### 4.2 WebView加载测试

**测试步骤**：
1. 启动应用
2. 等待页面加载完成
3. 检查页面内容

**预期行为**：
- 页面加载成功
- 显示系统概览
- 无JavaScript错误

**验证方法**：
```bash
# 检查WebView日志
adb logcat -s "chromium" "WebView"
```

#### 4.3 股票数据获取测试

**测试步骤**：
1. 进入"STOCKS"页面
2. 搜索股票代码（如：000001）
3. 查看股票详情

**预期行为**：
- 搜索成功
- 显示股票信息
- K线图加载成功

**验证方法**：
```bash
# 检查网络请求
adb logcat -s "OkHttp" "Retrofit"
```

#### 4.4 选股功能测试

**测试步骤**：
1. 进入"SELECTION"页面
2. 选择策略（如：BowlReboundStrategy）
3. 点击"RUN"按钮
4. 等待选股完成

**预期行为**：
- 选股任务启动成功
- 显示进度信息
- 选股结果显示

**验证方法**：
```bash
# 检查选股日志
adb logcat -s "SelectionWorker" "StrategyRegistry"
```

#### 4.5 通知功能测试

**测试步骤**：
1. 执行选股任务
2. 等待任务完成
3. 检查通知栏

**预期行为**：
- 通知发送成功
- 显示选股结果
- 点击通知打开应用

**验证方法**：
```bash
# 检查通知日志
adb logcat -s "NotificationHelper" "NotificationManager"
```

#### 4.6 后台运行测试

**测试步骤**：
1. 启动应用
2. 按Home键回到桌面
3. 等待一段时间
4. 重新打开应用

**预期行为**：
- 应用进入后台模式
- 服务继续运行
- 重新打开后恢复正常

**验证方法**：
```bash
# 检查后台服务
adb shell dumpsys activity services com.quant.aselector

# 检查进程状态
adb shell ps | grep quant
```

### 5. 性能测试

#### 5.1 内存使用测试

**测试步骤**：
1. 启动应用
2. 执行选股任务
3. 监控内存使用

**验证方法**：
```bash
# 查看内存使用
adb shell dumpsys meminfo com.quant.aselector

# 实时监控内存
adb shell top -d 1 | grep quant
```

**预期结果**：
- 内存使用：<500MB
- 无内存泄漏
- 无OOM（Out of Memory）错误

#### 5.2 CPU使用测试

**测试步骤**：
1. 启动应用
2. 执行选股任务
3. 监控CPU使用

**验证方法**：
```bash
# 查看CPU使用
adb shell top -d 1 | grep quant

# 查看进程CPU使用
adb shell ps -p $(adb shell pidof com.quant.aselector) -o %cpu
```

**预期结果**：
- CPU使用：<50%（平均）
- 无CPU占用过高

#### 5.3 电池消耗测试

**测试步骤**：
1. 启动应用
2. 后台运行1小时
3. 检查电池消耗

**验证方法**：
```bash
# 查看电池使用
adb shell dumpsys batterystats --charged com.quant.aselector

# 查看电池状态
adb shell dumpsys battery
```

**预期结果**：
- 电池消耗：<5%（1小时）
- 无异常耗电

#### 5.4 网络流量测试

**测试步骤**：
1. 启动应用
2. 执行数据更新
3. 监控网络流量

**验证方法**：
```bash
# 查看网络使用
adb shell cat /proc/$(adb shell pidof com.quant.aselector)/net/dev

# 实时监控网络
adb shell iftop -i wlan0
```

**预期结果**：
- 网络流量合理
- 无异常流量

### 6. 兼容性测试

#### 6.1 Android版本测试

**测试版本**：
- Android 14 (API 34)
- Android 15 (API 35)
- Android 16 (API 36)

**测试内容**：
- 应用启动
- 功能正常
- 无崩溃

#### 6.2 设备兼容性测试

**测试设备**：
- 不同品牌（华为、小米、OPPO、vivo等）
- 不同屏幕尺寸
- 不同分辨率

**测试内容**：
- UI显示正常
- 功能正常
- 无兼容性问题

### 7. 安全测试

#### 7.1 权限测试

**测试步骤**：
1. 检查应用权限
2. 测试权限拒绝情况
3. 验证权限使用

**验证方法**：
```bash
# 查看应用权限
adb shell dumpsys package com.quant.aselector | grep permission

# 测试权限拒绝
adb shell pm revoke com.quant.aselector android.permission.INTERNET
```

**预期结果**：
- 权限正确申请
- 权限拒绝时有提示
- 无越权行为

#### 7.2 数据安全测试

**测试步骤**：
1. 检查数据存储
2. 验证数据加密
3. 测试数据备份

**验证方法**：
```bash
# 查看数据目录
adb shell ls -la /data/data/com.quant.aselector/files/

# 检查配置文件
adb shell cat /data/data/com.quant.aselector/files/config/config.yaml
```

**预期结果**：
- 数据存储安全
- 敏感信息加密
- 备份正常

### 8. 稳定性测试

#### 8.1 长时间运行测试

**测试步骤**：
1. 启动应用
2. 连续运行24小时
3. 监控应用状态

**验证方法**：
```bash
# 监控应用状态
adb shell dumpsys activity processes com.quant.aselector

# 检查崩溃日志
adb logcat -b crash
```

**预期结果**：
- 无崩溃
- 无内存泄漏
- 服务稳定运行

#### 8.2 压力测试

**测试步骤**：
1. 启动应用
2. 频繁切换页面
3. 反复执行选股任务

**验证方法**：
```bash
# 监控应用状态
adb shell dumpsys activity processes com.quant.aselector

# 检查ANR日志
adb logcat -b events | grep anr
```

**预期结果**：
- 无ANR（Application Not Responding）
- 无崩溃
- 响应流畅

## 测试报告模板

### 测试环境

- **设备型号**：XXX
- **Android版本**：XXX
- **应用版本**：XXX
- **测试日期**：XXX

### 测试结果

| 测试项 | 预期结果 | 实际结果 | 状态 |
|--------|----------|----------|------|
| 应用启动 | 启动成功 | 启动成功 | ✅ |
| Flask服务 | 启动成功 | 启动成功 | ✅ |
| WebView加载 | 加载成功 | 加载成功 | ✅ |
| 股票数据 | 获取成功 | 获取成功 | ✅ |
| 选股功能 | 执行成功 | 执行成功 | ✅ |
| 通知功能 | 发送成功 | 发送成功 | ✅ |
| 后台运行 | 运行正常 | 运行正常 | ✅ |
| 内存使用 | <500MB | 350MB | ✅ |
| CPU使用 | <50% | 30% | ✅ |
| 电池消耗 | <5%/h | 3%/h | ✅ |

### 问题记录

| 问题描述 | 严重程度 | 状态 | 解决方案 |
|----------|----------|------|----------|
| 无 | - | - | - |

### 测试结论

- **测试通过**：是/否
- **建议发布**：是/否
- **备注**：XXX

## 自动化测试

### 1. 单元测试

```bash
# 运行Python单元测试
cd android-app/app/src/main/python
python -m pytest tests/

# 运行Android单元测试
cd android-app
./gradlew test
```

### 2. 集成测试

```bash
# 运行Android集成测试
cd android-app
./gradlew connectedAndroidTest
```

### 3. UI测试

```bash
# 运行UI测试
cd android-app
./gradlew connectedAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.quant.aselector.MainActivityTest
```

## 持续集成

### 1. GitHub Actions

创建 `.github/workflows/android.yml`：

```yaml
name: Android CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3
    
    - name: Set up JDK 17
      uses: actions/setup-java@v3
      with:
        java-version: '17'
        distribution: 'temurin'
    
    - name: Grant execute permission for gradlew
      run: chmod +x gradlew
    
    - name: Build with Gradle
      run: ./gradlew build
    
    - name: Run tests
      run: ./gradlew test
```

### 2. 本地CI脚本

创建 `scripts/ci.sh`：

```bash
#!/bin/bash

set -e

echo "Running CI checks..."

# 1. 构建检查
echo "1. Building..."
./gradlew build

# 2. 单元测试
echo "2. Running unit tests..."
./gradlew test

# 3. 代码检查
echo "3. Running lint..."
./gradlew lint

# 4. 生成APK
echo "4. Generating APK..."
./gradlew assembleDebug

echo "CI checks passed!"
```

## 故障排除

### 1. 构建失败

**问题**：`Build failed`

**解决**：
- 检查Gradle配置
- 清理构建缓存：`./gradlew clean`
- 检查依赖版本

### 2. 安装失败

**问题**：`INSTALL_FAILED_ALREADY_EXISTS`

**解决**：
```bash
# 卸载旧版本
adb uninstall com.quant.aselector

# 重新安装
adb install app/build/outputs/apk/debug/app-debug.apk
```

### 3. 启动崩溃

**问题**：应用启动后立即崩溃

**解决**：
```bash
# 查看崩溃日志
adb logcat -b crash

# 检查Python初始化
adb logcat -s "python" "Chaquopy"
```

### 4. Flask服务启动失败

**问题**：Flask服务无法启动

**解决**：
```bash
# 检查端口占用
adb shell netstat -tlnp | grep 5080

# 检查Python模块
adb logcat -s "python" "FlaskService"
```

## 下一步

完成测试后，请继续阅读：

- `TROUBLESHOOTING.md` - 故障排除指南
- `RELEASE_GUIDE.md` - 发布指南

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09
