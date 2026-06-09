# 故障排除指南

## 概述

本文档提供Android版A股量化选股系统常见问题的解决方案。

## 常见问题

### 1. 构建问题

#### 1.1 Gradle同步失败

**问题**：`Could not resolve com.chaquo.python:gradle:15.0.1`

**原因**：网络问题或镜像源配置错误

**解决方案**：

1. 检查网络连接
2. 使用国内镜像源，在 `settings.gradle.kts` 中添加：

```kotlin
dependencyResolutionManagement {
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
```

3. 清理Gradle缓存：

```bash
rm -rf ~/.gradle/caches
./gradlew clean
```

#### 1.2 SDK版本不匹配

**问题**：`Failed to find target with hash string 'android-34'`

**原因**：Android SDK未安装或版本不匹配

**解决方案**：

1. 打开Android Studio
2. 进入 `Tools` → `SDK Manager`
3. 安装对应的SDK版本
4. 同步项目

#### 1.3 Python依赖安装失败

**问题**：`pip install failed`

**原因**：网络问题或依赖不兼容

**解决方案**：

1. 使用国内镜像源：

```kotlin
python {
    pip {
        options("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
        install("flask")
    }
}
```

2. 检查依赖兼容性
3. 使用预编译的wheel包

#### 1.4 内存不足

**问题**：`OutOfMemoryError`

**原因**：JVM内存不足

**解决方案**：

1. 增加Gradle内存，在 `gradle.properties` 中：

```properties
org.gradle.jvmargs=-Xmx4096m
```

2. 增加Android Studio内存：
   - `Help` → `Edit Custom VM Options`
   - 添加 `-Xmx4096m`

3. 关闭其他应用程序

### 2. 安装问题

#### 2.1 安装失败

**问题**：`INSTALL_FAILED_ALREADY_EXISTS`

**原因**：已安装旧版本

**解决方案**：

```bash
# 卸载旧版本
adb uninstall com.quant.aselector

# 重新安装
adb install app/build/outputs/apk/debug/app-debug.apk
```

#### 2.2 签名错误

**问题**：`INSTALL_PARSE_FAILED_NO_CERTIFICATES`

**原因**：APK未签名

**解决方案**：

1. 使用Debug签名：

```bash
./gradlew assembleDebug
```

2. 或配置Release签名：

```kotlin
android {
    signingConfigs {
        create("release") {
            storeFile = file("keystore.jks")
            storePassword = "password"
            keyAlias = "alias"
            keyPassword = "password"
        }
    }
    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}
```

#### 2.3 存储空间不足

**问题**：`INSTALL_FAILED_INSUFFICIENT_STORAGE`

**原因**：设备存储空间不足

**解决方案**：

1. 清理设备存储空间
2. 卸载不常用的应用
3. 使用较小的APK（启用ProGuard）

### 3. 运行问题

#### 3.1 应用崩溃

**问题**：应用启动后立即崩溃

**原因**：代码错误或依赖问题

**解决方案**：

1. 查看崩溃日志：

```bash
adb logcat -b crash
```

2. 检查Python初始化：

```bash
adb logcat -s "python" "Chaquopy"
```

3. 检查依赖是否完整

#### 3.2 Flask服务启动失败

**问题**：Flask服务无法启动

**原因**：端口占用或Python模块错误

**解决方案**：

1. 检查端口占用：

```bash
adb shell netstat -tlnp | grep 5080
```

2. 检查Python模块：

```bash
adb logcat -s "python" "FlaskService"
```

3. 重启应用

#### 3.3 WebView加载失败

**问题**：WebView显示空白或错误

**原因**：Flask服务未启动或网络问题

**解决方案**：

1. 检查Flask服务状态
2. 检查网络连接
3. 清除WebView缓存：

```bash
adb shell pm clear com.quant.aselector
```

#### 3.4 数据获取失败

**问题**：无法获取股票数据

**原因**：网络问题或API配置错误

**解决方案**：

1. 检查网络连接
2. 验证Tushare Token
3. 检查数据源配置

#### 3.5 通知不显示

**问题**：选股完成但没有通知

**原因**：通知权限未开启

**解决方案**：

1. 检查通知权限：

```bash
adb shell dumpsys package com.quant.aselector | grep permission
```

2. 手动开启通知权限：
   - 进入手机设置
   - 找到应用
   - 开启通知权限

3. 检查通知渠道设置

### 4. 性能问题

#### 4.1 内存占用过高

**问题**：应用内存占用超过500MB

**原因**：内存泄漏或数据处理不当

**解决方案**：

1. 检查内存使用：

```bash
adb shell dumpsys meminfo com.quant.aselector
```

2. 优化数据处理：
   - 限制同时处理的股票数量
   - 使用流式处理
   - 及时释放资源

3. 检查内存泄漏

#### 4.2 CPU占用过高

**问题**：应用CPU占用超过50%

**原因**：计算密集或死循环

**解决方案**：

1. 检查CPU使用：

```bash
adb shell top -d 1 | grep quant
```

2. 优化计算：
   - 使用异步处理
   - 限制并发数
   - 使用缓存

3. 检查是否有死循环

#### 4.3 电池消耗过快

**问题**：应用电池消耗异常

**原因**：后台运行或网络请求频繁

**解决方案**：

1. 检查电池使用：

```bash
adb shell dumpsys batterystats --charged com.quant.aselector
```

2. 优化后台运行：
   - 使用WorkManager
   - 限制后台任务
   - 遵守电池优化策略

3. 减少网络请求

### 5. 兼容性问题

#### 5.1 Android版本不兼容

**问题**：在某些Android版本上无法运行

**原因**：API版本不匹配

**解决方案**：

1. 检查minSdk配置：

```kotlin
defaultConfig {
    minSdk = 26  // Android 8.0
    targetSdk = 34  // Android 14
}
```

2. 使用兼容性API
3. 测试不同版本

#### 5.2 设备不兼容

**问题**：在某些设备上无法运行

**原因**：设备特定问题

**解决方案**：

1. 检查设备信息：

```bash
adb shell getprop ro.product.model
adb shell getprop ro.build.version.sdk
```

2. 测试不同设备
3. 使用兼容性库

### 6. 网络问题

#### 6.1 网络连接失败

**问题**：无法连接到网络

**原因**：网络配置或权限问题

**解决方案**：

1. 检查网络权限：

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
```

2. 检查网络安全配置：

```xml
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">127.0.0.1</domain>
    </domain-config>
</network-security-config>
```

3. 检查网络状态

#### 6.2 SSL证书错误

**问题**：SSL证书验证失败

**原因**：证书配置问题

**解决方案**：

1. 使用系统证书：

```xml
<base-config cleartextTrafficPermitted="false">
    <trust-anchors>
        <certificates src="system" />
    </trust-anchors>
</base-config>
```

2. 添加自定义证书
3. 使用网络安全配置

### 7. 存储问题

#### 7.1 存储权限被拒

**问题**：无法访问存储

**原因**：权限未授予

**解决方案**：

1. 检查存储权限：

```bash
adb shell dumpsys package com.quant.aselector | grep permission
```

2. 手动授予权限：

```bash
adb shell pm grant com.quant.aselector android.permission.READ_EXTERNAL_STORAGE
adb shell pm grant com.quant.aselector android.permission.WRITE_EXTERNAL_STORAGE
```

3. 使用Scoped Storage

#### 7.2 存储空间不足

**问题**：无法写入数据

**原因**：存储空间不足

**解决方案**：

1. 检查存储空间：

```bash
adb shell df -h
```

2. 清理存储空间
3. 使用压缩存储

### 8. 通知问题

#### 8.1 通知权限被拒

**问题**：无法发送通知

**原因**：权限未授予

**解决方案**：

1. 检查通知权限：

```bash
adb shell dumpsys package com.quant.aselector | grep permission
```

2. 手动授予权限：

```bash
adb shell pm grant com.quant.aselector android.permission.POST_NOTIFICATIONS
```

3. 引导用户开启权限

#### 8.2 通知不显示

**问题**：通知发送但不显示

**原因**：通知渠道配置问题

**解决方案**：

1. 检查通知渠道：

```bash
adb shell dumpsys notification | grep quant
```

2. 创建通知渠道：

```kotlin
val channel = NotificationChannel(
    CHANNEL_ID,
    "通知渠道名称",
    NotificationManager.IMPORTANCE_DEFAULT
)
notificationManager.createNotificationChannel(channel)
```

3. 检查通知优先级

### 9. Python问题

#### 9.1 Python模块导入失败

**问题**：`ModuleNotFoundError`

**原因**：模块未打包或路径错误

**解决方案**：

1. 检查模块是否在build.gradle中配置：

```kotlin
python {
    pip {
        install("flask")
        install("pandas")
    }
}
```

2. 检查Python路径：

```python
import sys
print(sys.path)
```

3. 使用绝对路径导入

#### 9.2 Python版本不兼容

**问题**：Python版本不匹配

**原因**：Chaquopy配置的Python版本与代码不兼容

**解决方案**：

1. 检查Python版本配置：

```kotlin
python {
    version = "3.8"
}
```

2. 使用兼容的Python语法
3. 测试不同版本

### 10. 其他问题

#### 10.1 应用无响应（ANR）

**问题**：应用界面卡死

**原因**：主线程阻塞

**解决方案**：

1. 检查ANR日志：

```bash
adb logcat -b events | grep anr
```

2. 使用异步处理：

```kotlin
CoroutineScope(Dispatchers.IO).launch {
    // 耗时操作
    withContext(Dispatchers.Main) {
        // 更新UI
    }
}
```

3. 避免主线程阻塞

#### 10.2 崩溃报告

**问题**：应用频繁崩溃

**原因**：代码错误或内存问题

**解决方案**：

1. 收集崩溃日志：

```bash
adb logcat -b crash > crash.log
```

2. 分析崩溃原因
3. 修复代码错误

## 调试工具

### 1. ADB命令

```bash
# 查看设备信息
adb devices
adb shell getprop ro.product.model

# 查看应用信息
adb shell dumpsys package com.quant.aselector

# 查看进程信息
adb shell ps | grep quant

# 查看内存信息
adb shell dumpsys meminfo com.quant.aselector

# 查看网络信息
adb shell netstat -tlnp | grep 5080

# 查看日志
adb logcat -s "QuantApplication" "MainActivity" "FlaskService"
```

### 2. Android Studio调试

1. **Logcat**：查看实时日志
2. **Profiler**：性能分析
3. **Layout Inspector**：UI检查
4. **Database Inspector**：数据库查看

### 3. Python调试

```python
# 添加日志
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.debug("调试信息")

# 打印到Logcat
import sys
sys.stdout = open('/dev/stdout', 'w')
print("Python输出")
```

## 获取帮助

### 1. 查看日志

```bash
# 查看完整日志
adb logcat > full.log

# 查看特定标签日志
adb logcat -s "python" "FlaskService" "MainActivity"

# 查看错误日志
adb logcat *:E
```

### 2. 搜索问题

1. 查看本文档的故障排除部分
2. 搜索GitHub Issues
3. 查看官方文档

### 3. 提交Issue

如果问题无法解决，请提交Issue：

1. 描述问题现象
2. 提供错误日志
3. 说明复现步骤
4. 提供设备信息

## 预防措施

### 1. 定期备份

```bash
# 备份应用数据
adb backup -f backup.ab com.quant.aselector

# 恢复应用数据
adb restore backup.ab
```

### 2. 监控性能

```bash
# 监控内存
adb shell dumpsys meminfo com.quant.aselector

# 监控CPU
adb shell top -d 1 | grep quant

# 监控电池
adb shell dumpsys batterystats --charged com.quant.aselector
```

### 3. 更新依赖

定期更新依赖库版本，修复已知问题。

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09
