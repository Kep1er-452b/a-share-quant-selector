# A股量化选股系统 - Android迁移计划

## 1. 项目概述

### 1.1 迁移目标
将macOS上的A股量化选股系统完整迁移到Android平台，打包成APK直接安装到Android 16手机。

### 1.2 技术方案
**WebView + Flask + Chaquopy**
- **Android原生层**：使用Kotlin/Java开发，负责App生命周期、权限管理、WebView容器
- **Python层**：使用Chaquopy嵌入Python解释器，运行现有Flask服务和选股逻辑
- **前端层**：复用现有Web前端（HTML/CSS/JS），通过WebView展示

### 1.3 架构图
```
┌─────────────────────────────────────────────────┐
│                   Android App                    │
│  ┌─────────────────────────────────────────────┐ │
│  │              WebView Container              │ │
│  │  ┌─────────────────────────────────────┐   │ │
│  │  │        Flask Web Server             │   │ │
│  │  │  ┌─────────────────────────────┐   │   │ │
│  │  │  │     Python Backend          │   │   │ │
│  │  │  │  - 选股策略                  │   │   │ │
│  │  │  │  - 数据获取                  │   │   │ │
│  │  │  │  - 技术指标                  │   │   │ │
│  │  │  └─────────────────────────────┘   │   │ │
│  │  └─────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────┐ │
│  │           Android Native Services           │ │
│  │  - 通知服务                                  │ │
│  │  - 网络管理                                  │ │
│  │  - 存储管理                                  │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

## 2. 开发环境要求

### 2.1 必需软件
1. **Android Studio** (最新稳定版)
   - 下载：https://developer.android.com/studio
   - 包含Android SDK、模拟器等

2. **JDK 17** (Android Studio通常自带)
   - 或单独安装：OpenJDK 17

3. **Android SDK**
   - API Level: 34 (Android 14) 或更高
   - Build Tools: 34.0.0 或更高
   - 通过Android Studio的SDK Manager安装

4. **Chaquopy** (Python for Android)
   - 版本：15.0.1 或更高
   - 通过Gradle插件自动下载

### 2.2 系统要求
- **操作系统**：macOS 10.15+ / Windows 10+ / Linux (Ubuntu 20.04+)
- **内存**：至少16GB RAM（推荐32GB）
- **磁盘空间**：至少50GB可用空间
- **网络**：需要下载Android SDK、依赖库等

## 3. 项目结构

```
android-app/
├── MIGRATION_PLAN.md              # 本文档
├── SETUP_GUIDE.md                 # 环境搭建指南
├── app/                           # Android应用模块
│   ├── build.gradle.kts           # 应用级构建配置
│   ├── src/
│   │   ├── main/
│   │   │   ├── AndroidManifest.xml
│   │   │   ├── java/com/quant/aselector/
│   │   │   │   ├── MainActivity.kt
│   │   │   │   ├── FlaskService.kt
│   │   │   │   ├── NotificationHelper.kt
│   │   │   │   └── ConfigManager.kt
│   │   │   ├── python/            # Python代码
│   │   │   │   ├── main.py
│   │   │   │   ├── web_server.py
│   │   │   │   ├── strategy/
│   │   │   │   ├── utils/
│   │   │   │   └── config/
│   │   │   ├── res/
│   │   │   │   ├── layout/
│   │   │   │   ├── values/
│   │   │   │   └── xml/
│   │   │   └── assets/            # 静态资源
│   │   │       └── web/           # Web前端文件
│   │   └── test/
│   └── proguard-rules.pro
├── build.gradle.kts               # 项目级构建配置
├── settings.gradle.kts            # 项目设置
├── gradle.properties              # Gradle属性
├── gradle/
│   └── wrapper/
│       ├── gradle-wrapper.jar
│       └── gradle-wrapper.properties
├── gradlew                        # Gradle Wrapper (Unix)
├── gradlew.bat                    # Gradle Wrapper (Windows)
└── scripts/
    ├── build_apk.sh               # APK构建脚本
    └── setup_environment.sh       # 环境设置脚本
```

## 4. 核心功能迁移

### 4.1 数据获取模块
**原功能**：使用akshare/tushare获取A股数据
**Android适配**：
- 保留完整的数据获取逻辑
- 修改存储路径到Android内部存储
- 添加网络权限和状态检测
- 优化移动端网络请求（超时、重试）

**修改文件**：
- `utils/akshare_fetcher.py` - 适配Android路径
- `utils/tushare_fetcher.py` - 适配Android路径
- `utils/data_provider.py` - 修改默认路径

### 4.2 选股策略模块
**原功能**：BowlRebound、B1系列、B2等策略
**Android适配**：
- 完整保留所有策略逻辑
- 优化内存使用（移动端内存有限）
- 添加进度回调（通知用户选股进度）

**修改文件**：
- `strategy/*.py` - 无需修改
- `utils/selection_worker.py` - 添加进度回调

### 4.3 Web服务模块
**原功能**：Flask提供Web API和页面
**Android适配**：
- 修改监听地址为127.0.0.1（仅本地访问）
- 优化端口选择（避免冲突）
- 添加生命周期管理（App暂停/恢复时控制Flask）

**修改文件**：
- `web_server.py` - 修改默认配置
- `main.py` - 适配Android启动

### 4.4 通知模块
**原功能**：钉钉机器人通知
**Android适配**：
- 新增Android本地通知
- 保留钉钉通知作为备选
- 使用Android WorkManager处理后台任务

**新增文件**：
- `NotificationHelper.kt` - Android通知封装
- `android_notifier.py` - Python层通知接口

### 4.5 配置管理
**原功能**：YAML配置文件
**Android适配**：
- 使用Android SharedPreferences存储密钥
- 配置文件打包到assets
- 支持运行时修改配置

**修改文件**：
- `utils/local_config.py` - 适配Android路径
- `config/config.yaml.template` - 添加默认密钥

## 5. 密钥管理

### 5.1 需要迁移的密钥
1. **Tushare Token**
   - 环境变量：`TUSHARE_TOKEN`
   - 配置文件：`config/config.yaml`

2. **DeepSeek API Key**
   - 环境变量：`DEEPSEEK_API_KEY`
   - 配置文件：`config/config.yaml`

### 5.2 密钥存储方案
**方案A：打包到APK（推荐，仅供个人使用）**
- 将密钥硬编码到配置文件
- 打包到APK的assets中
- 优点：简单，无需用户输入
- 缺点：密钥可被反编译获取

**方案B：首次启动输入**
- 首次启动时要求用户输入密钥
- 存储到Android SharedPreferences
- 优点：密钥不打包到APK
- 缺点：需要用户手动输入

**建议**：由于仅供个人使用，采用方案A，简化使用流程。

## 6. Android权限

### 6.1 必需权限
```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
<uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
```

### 6.2 权限用途
- **INTERNET**：获取股票数据、调用API
- **ACCESS_NETWORK_STATE**：检测网络状态
- **READ/WRITE_EXTERNAL_STORAGE**：读写CSV数据文件
- **POST_NOTIFICATIONS**：发送本地通知
- **FOREGROUND_SERVICE**：后台运行Flask服务
- **RECEIVE_BOOT_COMPLETED**：开机自启（可选）

## 7. 性能优化

### 7.1 内存优化
- 限制同时处理的股票数量
- 使用流式处理减少内存占用
- 及时释放不用的DataFrame

### 7.2 电池优化
- 使用WorkManager调度后台任务
- 限制后台运行时间
- 遵守Android电池优化策略

### 7.3 存储优化
- 使用SQLite存储股票数据（可选）
- 压缩CSV文件
- 定期清理缓存

## 8. 测试计划

### 8.1 单元测试
- Python策略逻辑测试
- Android组件测试

### 8.2 集成测试
- Flask服务启动测试
- WebView加载测试
- 通知功能测试

### 8.3 性能测试
- 内存使用测试
- 电池消耗测试
- 网络请求测试

## 9. 发布和分发

### 9.1 APK构建
```bash
# Debug版本
./gradlew assembleDebug

# Release版本
./gradlew assembleRelease
```

### 9.2 签名配置
- 使用自签名证书
- 生成keystore文件
- 配置签名信息

### 9.3 安装方式
1. 直接安装APK
2. 通过ADB安装
3. 通过文件管理器安装

## 10. 已知限制

### 10.1 平台限制
- Android后台运行限制（Doze模式）
- 存储访问限制（Scoped Storage）
- 网络访问限制（省电模式）

### 10.2 功能限制
- 不支持pywebview桌面窗口
- 不支持C加速层（需要交叉编译）
- 部分依赖库可能需要Android版本

### 10.3 性能限制
- 移动端CPU性能较弱
- 内存容量有限
- 电池消耗需要控制

## 11. 后续优化方向

### 11.1 功能增强
- 添加SQLite数据库支持
- 实现数据同步功能
- 添加图表交互优化

### 11.2 性能优化
- 使用Cython优化关键代码
- 实现增量更新
- 添加缓存机制

### 11.3 用户体验
- 添加Material Design UI
- 实现深色模式
- 添加手势操作

## 12. 时间规划

| 阶段 | 任务 | 预计时间 |
|------|------|----------|
| 1 | 环境搭建 | 1-2天 |
| 2 | 项目结构创建 | 1天 |
| 3 | Python代码移植 | 2-3天 |
| 4 | Android层开发 | 3-4天 |
| 5 | 集成测试 | 2-3天 |
| 6 | 优化和调试 | 2-3天 |
| **总计** | | **12-16天** |

## 13. 风险和应对

### 13.1 技术风险
- **Chaquopy兼容性**：部分Python库可能不支持Android
  - 应对：提前测试关键依赖，准备替代方案
  
- **Flask性能**：移动端性能可能不足
  - 应对：优化代码，减少并发，添加缓存

### 13.2 时间风险
- **依赖下载**：Android SDK、依赖库下载时间长
  - 应对：提前下载，使用镜像源

- **调试困难**：移动端调试比桌面端复杂
  - 应对：添加详细日志，使用Android Studio调试工具

## 14. 参考资料

### 14.1 官方文档
- [Android Developer](https://developer.android.com/)
- [Chaquopy Documentation](https://chaquo.com/chaquopy/)
- [Flask Documentation](https://flask.palletsprojects.com/)

### 14.2 示例项目
- [Chaquopy Samples](https://github.com/chaquo/chaquopy-sample)
- [Android Python Integration](https://github.com/AndroidPythonIntegration)

### 14.3 社区资源
- [Stack Overflow - Chaquopy](https://stackoverflow.com/questions/tagged/chaquopy)
- [GitHub Issues](https://github.com/chaquo/chaquopy/issues)

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09
