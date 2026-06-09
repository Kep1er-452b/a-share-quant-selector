# Android开发环境搭建指南

## 1. 安装Android Studio

### 1.1 下载Android Studio
访问：https://developer.android.com/studio

下载适用于macOS的版本（.dmg文件）

### 1.2 安装步骤
1. 双击下载的.dmg文件
2. 将Android Studio拖动到Applications文件夹
3. 首次启动时，选择"Standard"安装类型
4. 等待Android SDK下载完成（可能需要较长时间）

### 1.3 配置Android SDK
1. 打开Android Studio
2. 进入 `Android Studio` → `Preferences` → `Appearance & Behavior` → `System Settings` → `Android SDK`
3. 在"SDK Platforms"选项卡中，勾选：
   - Android 14 (API 34)
   - Android 13 (API 33)
4. 在"SDK Tools"选项卡中，勾选：
   - Android SDK Build-Tools
   - Android SDK Command-line Tools
   - Android Emulator
   - Android SDK Platform-Tools
5. 点击"Apply"下载安装

## 2. 安装JDK

### 2.1 检查现有JDK
```bash
java -version
```

### 2.2 安装JDK 17（如果需要）
```bash
# 使用Homebrew安装
brew install openjdk@17

# 配置环境变量
echo 'export JAVA_HOME=/usr/local/opt/openjdk@17' >> ~/.zshrc
echo 'export PATH=$JAVA_HOME/bin:$PATH' >> ~/.zshrc
source ~/.zshrc
```

## 3. 配置环境变量

### 3.1 添加到 ~/.zshrc
```bash
# Android SDK
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$PATH:$ANDROID_HOME/emulator
export PATH=$PATH:$ANDROID_HOME/platform-tools
export PATH=$PATH:$ANDROID_HOME/tools
export PATH=$PATH:$ANDROID_HOME/tools/bin

# Java
export JAVA_HOME=/usr/local/opt/openjdk@17
export PATH=$JAVA_HOME/bin:$PATH
```

### 3.2 使配置生效
```bash
source ~/.zshrc
```

## 4. 验证安装

### 4.1 验证Android SDK
```bash
adb version
```

### 4.2 验证Java
```bash
java -version
javac -version
```

### 4.3 验证Gradle
```bash
gradle --version
```

## 5. 创建Android虚拟设备（可选）

### 5.1 打开AVD Manager
1. 在Android Studio中，点击 `Tools` → `AVD Manager`
2. 点击 `Create Virtual Device`

### 5.2 选择设备
1. 选择 `Phone` → `Pixel 7`（或类似设备）
2. 点击 `Next`

### 5.3 选择系统镜像
1. 选择 `API 34`（Android 14）或更高版本
2. 下载系统镜像（如果未下载）
3. 点击 `Next` → `Finish`

## 6. 克隆项目并打开

### 6.1 克隆项目
```bash
cd /Users/chenxingyu/DocumentsData/ElderDocuments/a-share-quant-selector
```

### 6.2 在Android Studio中打开
1. 打开Android Studio
2. 选择 `Open an Existing Project`
3. 导航到 `a-share-quant-selector/android-app` 文件夹
4. 点击 `Open`

### 6.3 等待Gradle同步
- 首次打开会自动下载依赖
- 可能需要10-30分钟
- 查看底部状态栏显示同步进度

## 7. 配置ChaQuopy

### 7.1 ChaQuopy简介
ChaQuopy是一个Android Studio插件，允许在Android应用中运行Python代码。

### 7.2 验证ChaQuopy配置
在 `app/build.gradle.kts` 中应该有：
```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python") version "15.0.1"
}
```

### 7.3 配置Python版本
```kotlin
python {
    version = "3.8"
    pip {
        install("flask")
        install("pandas")
        install("numpy")
        // ... 其他依赖
    }
}
```

## 8. 运行项目

### 8.1 连接真机或启动模拟器
**真机连接**：
1. 在手机上启用"开发者选项"
2. 启用"USB调试"
3. 使用USB线连接手机
4. 在手机上确认允许调试

**模拟器**：
1. 在AVD Manager中启动模拟器
2. 等待模拟器完全启动

### 8.2 运行App
1. 在Android Studio中，点击 `Run` → `Run 'app'`
2. 或点击工具栏的绿色三角形按钮
3. 选择目标设备（真机或模拟器）
4. 等待构建和安装

## 9. 常见问题

### 9.1 Gradle同步失败
**问题**：`Could not resolve com.chaquo.python:gradle:15.0.1`
**解决**：
1. 检查网络连接
2. 在 `settings.gradle.kts` 中添加镜像源：
```kotlin
dependencyResolutionManagement {
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        google()
        mavenCentral()
    }
}
```

### 9.2 SDK版本不匹配
**问题**：`Failed to find target with hash string 'android-34'`
**解决**：
1. 打开SDK Manager
2. 安装对应的SDK版本
3. 同步项目

### 9.3 Python依赖安装失败
**问题**：`pip install failed`
**解决**：
1. 检查网络连接
2. 使用国内镜像源：
```kotlin
pip {
    options("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
    install("flask")
}
```

### 9.4 内存不足
**问题**：`OutOfMemoryError`
**解决**：
1. 增加Gradle内存，在 `gradle.properties` 中：
```properties
org.gradle.jvmargs=-Xmx4096m
```
2. 增加Android Studio内存：
   - `Help` → `Edit Custom VM Options`
   - 添加 `-Xmx4096m`

## 10. 开发工具

### 10.1 推荐插件
1. **Kotlin Plugin** - Kotlin语言支持
2. **Python Plugin** - Python代码支持（可选）
3. **ADB Idea** - ADB快捷命令
4. **Android Drawable Importer** - 图标导入

### 10.2 调试工具
1. **Logcat** - 查看日志输出
2. **Android Profiler** - 性能分析
3. **Layout Inspector** - UI检查
4. **Database Inspector** - 数据库查看

## 11. 构建APK

### 11.1 Debug版本
```bash
./gradlew assembleDebug
```
输出位置：`app/build/outputs/apk/debug/app-debug.apk`

### 11.2 Release版本
```bash
./gradlew assembleRelease
```
输出位置：`app/build/outputs/apk/release/app-release.apk`

### 11.3 安装到手机
```bash
# 使用ADB安装
adb install app/build/outputs/apk/debug/app-debug.apk

# 或者直接传输APK到手机安装
```

## 12. 下一步

环境搭建完成后，请继续阅读：
- `PROJECT_STRUCTURE.md` - 项目结构详解
- `PYTHON_MIGRATION.md` - Python代码迁移指南
- `ANDROID_DEVELOPMENT.md` - Android开发指南

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09
