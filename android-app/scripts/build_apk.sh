#!/bin/bash

# A股量化选股系统 - Android APK构建脚本
# 使用方法: ./scripts/build_apk.sh [debug|release]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查环境
check_environment() {
    print_info "检查构建环境..."
    
    # 检查Java
    if ! command -v java &> /dev/null; then
        print_error "Java未安装，请先安装JDK 17"
        exit 1
    fi
    
    # 检查ANDROID_HOME
    if [ -z "$ANDROID_HOME" ]; then
        print_warning "ANDROID_HOME未设置，尝试使用默认路径..."
        export ANDROID_HOME="$HOME/Library/Android/sdk"
    fi
    
    if [ ! -d "$ANDROID_HOME" ]; then
        print_error "Android SDK未找到，请安装Android Studio"
        exit 1
    fi
    
    print_success "环境检查通过"
}

# 清理构建
clean_build() {
    print_info "清理构建目录..."
    ./gradlew clean
    print_success "清理完成"
}

# 构建Debug APK
build_debug() {
    print_info "构建Debug APK..."
    ./gradlew assembleDebug
    
    APK_PATH="app/build/outputs/apk/debug/app-debug.apk"
    if [ -f "$APK_PATH" ]; then
        print_success "Debug APK构建成功: $APK_PATH"
        print_info "APK大小: $(du -h "$APK_PATH" | cut -f1)"
    else
        print_error "APK构建失败"
        exit 1
    fi
}

# 构建Release APK
build_release() {
    print_info "构建Release APK..."
    ./gradlew assembleRelease
    
    APK_PATH="app/build/outputs/apk/release/app-release.apk"
    if [ -f "$APK_PATH" ]; then
        print_success "Release APK构建成功: $APK_PATH"
        print_info "APK大小: $(du -h "$APK_PATH" | cut -f1)"
    else
        print_error "APK构建失败"
        exit 1
    fi
}

# 安装到设备
install_apk() {
    local APK_PATH=$1
    
    print_info "检查连接的设备..."
    if ! command -v adb &> /dev/null; then
        print_warning "adb未找到，跳过安装"
        return
    fi
    
    DEVICES=$(adb devices | grep -v "List of devices" | grep -v "^$" | wc -l)
    if [ "$DEVICES" -eq 0 ]; then
        print_warning "未检测到设备，请连接手机或启动模拟器"
        return
    fi
    
    print_info "安装APK到设备..."
    adb install -r "$APK_PATH"
    print_success "APK已安装"
}

# 主函数
main() {
    # 切换到脚本所在目录的上级目录（android-app）
    cd "$(dirname "$0")/.."
    
    # 检查环境
    check_environment
    
    # 解析参数
    BUILD_TYPE=${1:-debug}
    
    case $BUILD_TYPE in
        debug)
            clean_build
            build_debug
            install_apk "app/build/outputs/apk/debug/app-debug.apk"
            ;;
        release)
            clean_build
            build_release
            install_apk "app/build/outputs/apk/release/app-release.apk"
            ;;
        clean)
            clean_build
            ;;
        *)
            print_error "未知的构建类型: $BUILD_TYPE"
            print_info "使用方法: $0 [debug|release|clean]"
            exit 1
            ;;
    esac
    
    print_success "构建完成！"
}

# 运行主函数
main "$@"
