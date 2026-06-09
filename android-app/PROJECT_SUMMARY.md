# Android迁移项目总结

## 项目概述

本项目将macOS上的A股量化选股系统完整迁移到Android平台，打包成APK直接安装到Android 16手机。

## 技术方案

**WebView + Flask + Chaquopy**
- **Android原生层**：Kotlin + WebView + Chaquopy
- **Python层**：Flask + pandas + numpy + akshare
- **前端层**：HTML/CSS/JavaScript（复用原有Web前端）

## 项目结构

```
android-app/
├── README.md                      # 项目说明
├── MIGRATION_PLAN.md              # 迁移计划
├── SETUP_GUIDE.md                 # 环境搭建指南
├── PYTHON_MIGRATION.md            # Python代码迁移指南
├── KEY_CONFIGURATION.md           # 密钥配置指南
├── TESTING_GUIDE.md               # 测试指南
├── TROUBLESHOOTING.md             # 故障排除指南
├── app/                           # Android应用模块
│   ├── build.gradle.kts           # 应用级构建配置
│   ├── proguard-rules.pro         # ProGuard配置
│   └── src/
│       └── main/
│           ├── AndroidManifest.xml
│           ├── java/com/quant/aselector/
│           │   ├── MainActivity.kt
│           │   ├── FlaskService.kt
│           │   ├── NotificationHelper.kt
│           │   └── QuantApplication.kt
│           ├── python/            # Python代码（已复制）
│           │   ├── android_adapter.py
│           │   ├── main.py
│           │   ├── web_server.py
│           │   ├── strategy/
│           │   ├── utils/
│           │   ├── config/
│           │   └── wyckoff_ai/
│           ├── res/               # Android资源
│           └── assets/            # 静态资源
├── build.gradle.kts               # 项目级构建配置
├── settings.gradle.kts            # 项目设置
├── gradle.properties              # Gradle属性
├── gradle/                        # Gradle Wrapper
└── scripts/
    ├── build_apk.sh               # APK构建脚本
    ├── setup_environment.sh       # 环境设置脚本
    └── copy_python_code.sh        # Python代码复制脚本
```

## 已完成的工作

### 1. 项目结构创建
- ✅ Android项目目录结构
- ✅ Gradle构建配置
- ✅ AndroidManifest.xml
- ✅ 资源文件（布局、颜色、主题等）

### 2. Android层开发
- ✅ MainActivity.kt - 主Activity，WebView容器
- ✅ FlaskService.kt - Flask服务管理
- ✅ NotificationHelper.kt - 通知功能
- ✅ QuantApplication.kt - 应用初始化

### 3. Python代码移植
- ✅ 复制所有Python代码到android-app/app/src/main/python/
- ✅ 创建android_adapter.py适配层
- ✅ 保留原有策略、工具、配置等模块

### 4. 文档编写
- ✅ 迁移计划文档
- ✅ 环境搭建指南
- ✅ Python代码迁移指南
- ✅ 密钥配置指南
- ✅ 测试指南
- ✅ 故障排除指南

### 5. 构建脚本
- ✅ APK构建脚本
- ✅ 环境设置脚本
- ✅ Python代码复制脚本

## 核心功能

### 1. 选股策略
- BowlReboundStrategy（碗口反弹策略）
- B1系列策略（V2.42B, V2.42P, V2.42.61等）
- B2BetaStrategy
- 自定义公式策略

### 2. 数据获取
- akshare数据源
- tushare数据源
- 腾讯数据源
- 智能增量更新

### 3. 技术指标
- KDJ指标
- 均线系统（MA, EMA）
- 知行趋势线
- 威科夫分析

### 4. 通知功能
- Android本地通知（默认禁用）
- 钉钉通知（可选）
- 选股结果推送

### 5. Web界面
- 系统概览
- 市场云图
- 股票列表
- 选股执行
- 策略配置
- 自选股票
- 威科夫分析

## 环境要求

### 开发环境
- **操作系统**：macOS 10.15+ / Windows 10+ / Linux (Ubuntu 20.04+)
- **Android Studio**：最新稳定版
- **JDK**：17或更高版本
- **Android SDK**：API 34 (Android 14) 或更高
- **内存**：至少16GB RAM（推荐32GB）
- **磁盘空间**：至少50GB可用空间

### 运行环境
- **Android版本**：Android 8.0 (API 26) 或更高
- **存储空间**：至少2GB可用空间
- **内存**：至少4GB RAM
- **网络**：WiFi或移动数据

## 使用说明

### 1. 环境搭建

```bash
# 1. 安装Android Studio
# 访问 https://developer.android.com/studio

# 2. 运行环境设置脚本
cd android-app
chmod +x scripts/setup_environment.sh
./scripts/setup_environment.sh

# 3. 在Android Studio中打开项目
# File -> Open -> 选择 android-app 目录
```

### 2. 配置密钥

编辑 `app/src/main/python/android_adapter.py`，替换密钥：

```python
# Tushare Token
'tushare': {
    'token': 'YOUR_ACTUAL_TUSHARE_TOKEN'
},

# DeepSeek API Key
'deepseek_api_key': 'YOUR_ACTUAL_DEEPSEEK_API_KEY'
```

### 3. 构建APK

```bash
# 构建Debug版本
./scripts/build_apk.sh debug

# 构建Release版本
./scripts/build_apk.sh release
```

### 4. 安装到手机

```bash
# 使用ADB安装
adb install app/build/outputs/apk/debug/app-debug.apk

# 或者直接传输APK到手机安装
```

### 5. 运行应用

1. 打开应用
2. 等待Flask服务启动
3. 系统会自动初始化数据目录
4. 开始使用选股功能

## 已知限制

### 1. 平台限制
- Android后台运行限制（Doze模式）
- 存储访问限制（Scoped Storage）
- 网络访问限制（省电模式）

### 2. 功能限制
- 不支持pywebview桌面窗口
- 不支持C加速层（需要交叉编译）
- 部分依赖库可能需要Android版本

### 3. 性能限制
- 移动端CPU性能较弱
- 内存容量有限
- 电池消耗需要控制

## 后续优化方向

### 1. 功能增强
- 添加SQLite数据库支持
- 实现数据同步功能
- 添加图表交互优化

### 2. 性能优化
- 使用Cython优化关键代码
- 实现增量更新
- 添加缓存机制

### 3. 用户体验
- 添加Material Design UI
- 实现深色模式
- 添加手势操作

## 注意事项

### 1. 密钥安全
- 密钥已内置到APK中
- 仅供个人使用，请勿分发
- 如需更安全的存储，使用Android Keystore

### 2. 数据安全
- 数据存储在应用内部存储
- 其他应用无法访问
- 定期备份重要数据

### 3. 网络安全
- 使用HTTPS连接
- 验证SSL证书
- 防止中间人攻击

## 测试验证

### 1. 构建测试
```bash
# 检查Gradle配置
./gradlew tasks

# 构建Debug APK
./scripts/build_apk.sh debug
```

### 2. 安装测试
```bash
# 检查设备连接
adb devices

# 安装APK
adb install app/build/outputs/apk/debug/app-debug.apk
```

### 3. 功能测试
```bash
# 启动应用
adb shell am start -n com.quant.aselector/.MainActivity

# 查看日志
adb logcat -s "QuantApplication" "MainActivity" "FlaskService"
```

## 故障排除

### 1. 构建失败
- 检查Gradle配置
- 清理构建缓存：`./gradlew clean`
- 检查依赖版本

### 2. 安装失败
- 卸载旧版本：`adb uninstall com.quant.aselector`
- 重新安装：`adb install app-debug.apk`

### 3. 启动崩溃
- 查看崩溃日志：`adb logcat -b crash`
- 检查Python初始化：`adb logcat -s "python"`

### 4. Flask服务启动失败
- 检查端口占用：`adb shell netstat -tlnp | grep 5080`
- 检查Python模块：`adb logcat -s "python" "FlaskService"`

## 参考资料

### 官方文档
- [Android Developer](https://developer.android.com/)
- [Chaquopy Documentation](https://chaquo.com/chaquopy/)
- [Flask Documentation](https://flask.palletsprojects.com/)

### 示例项目
- [Chaquopy Samples](https://github.com/chaquo/chaquopy-sample)
- [Android Python Integration](https://github.com/AndroidPythonIntegration)

### 社区资源
- [Stack Overflow - Chaquopy](https://stackoverflow.com/questions/tagged/chaquopy)
- [GitHub Issues](https://github.com/chaquo/chaquopy/issues)

## 更新日志

### v1.0.0 (2026-06-09)
- 初始版本
- 完整迁移macOS系统功能
- 支持Android 16 (API 36)
- 实现WebView + Flask架构
- 添加Android本地通知

## 许可证

MIT License

## 联系方式

如有问题或建议，请提交Issue或Pull Request。

---

**注意**：本软件仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。
