#!/bin/bash

# A股量化选股系统 - Android开发环境设置脚本
# 使用方法: ./scripts/setup_environment.sh

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

# 检查操作系统
check_os() {
    print_info "检查操作系统..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        print_success "检测到macOS"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        print_success "检测到Linux"
    else
        print_error "不支持的操作系统: $OSTYPE"
        exit 1
    fi
}

# 检查并安装Homebrew（macOS）
install_homebrew() {
    if [[ "$OS" == "macos" ]]; then
        if ! command -v brew &> /dev/null; then
            print_info "安装Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            print_success "Homebrew安装完成"
        else
            print_success "Homebrew已安装"
        fi
    fi
}

# 检查并安装JDK
install_jdk() {
    print_info "检查JDK..."
    
    if command -v java &> /dev/null; then
        JAVA_VERSION=$(java -version 2>&1 | head -n 1 | cut -d'"' -f2 | cut -d'.' -f1)
        if [ "$JAVA_VERSION" -ge 17 ]; then
            print_success "JDK $JAVA_VERSION 已安装"
            return
        fi
    fi
    
    print_info "安装JDK 17..."
    if [[ "$OS" == "macos" ]]; then
        brew install openjdk@17
        echo 'export JAVA_HOME=/usr/local/opt/openjdk@17' >> ~/.zshrc
        echo 'export PATH=$JAVA_HOME/bin:$PATH' >> ~/.zshrc
    elif [[ "$OS" == "linux" ]]; then
        sudo apt-get update
        sudo apt-get install -y openjdk-17-jdk
        echo 'export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64' >> ~/.bashrc
        echo 'export PATH=$JAVA_HOME/bin:$PATH' >> ~/.bashrc
    fi
    
    print_success "JDK 17安装完成"
}

# 检查并安装Android SDK
install_android_sdk() {
    print_info "检查Android SDK..."
    
    if [ -n "$ANDROID_HOME" ] && [ -d "$ANDROID_HOME" ]; then
        print_success "Android SDK已安装: $ANDROID_HOME"
        return
    fi
    
    print_warning "Android SDK未找到"
    print_info "请按照以下步骤安装Android Studio和SDK："
    print_info "1. 访问 https://developer.android.com/studio"
    print_info "2. 下载并安装Android Studio"
    print_info "3. 在Android Studio中安装Android SDK (API 34)"
    print_info "4. 设置ANDROID_HOME环境变量"
    print_info ""
    print_info "安装完成后，请重新运行此脚本"
    
    read -p "是否已安装Android Studio? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
}

# 配置环境变量
setup_environment_variables() {
    print_info "配置环境变量..."
    
    # 检查是否已配置
    if grep -q "ANDROID_HOME" ~/.zshrc 2>/dev/null || grep -q "ANDROID_HOME" ~/.bashrc 2>/dev/null; then
        print_success "环境变量已配置"
        return
    fi
    
    # 添加到shell配置文件
    SHELL_CONFIG="$HOME/.zshrc"
    if [ ! -f "$SHELL_CONFIG" ]; then
        SHELL_CONFIG="$HOME/.bashrc"
    fi
    
    cat >> "$SHELL_CONFIG" << 'EOF'

# Android开发环境
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$PATH:$ANDROID_HOME/emulator
export PATH=$PATH:$ANDROID_HOME/platform-tools
export PATH=$PATH:$ANDROID_HOME/tools
export PATH=$PATH:$ANDROID_HOME/tools/bin
EOF
    
    print_success "环境变量配置完成"
    print_warning "请运行 'source $SHELL_CONFIG' 或重启终端使配置生效"
}

# 验证安装
verify_installation() {
    print_info "验证安装..."
    
    # 检查Java
    if command -v java &> /dev/null; then
        print_success "Java: $(java -version 2>&1 | head -n 1)"
    else
        print_error "Java未找到"
    fi
    
    # 检查Android SDK
    if [ -n "$ANDROID_HOME" ] && [ -d "$ANDROID_HOME" ]; then
        print_success "Android SDK: $ANDROID_HOME"
    else
        print_warning "Android SDK未找到"
    fi
    
    # 检查adb
    if command -v adb &> /dev/null; then
        print_success "adb: $(adb version | head -n 1)"
    else
        print_warning "adb未找到"
    fi
}

# 主函数
main() {
    echo "=========================================="
    echo "A股量化选股系统 - Android环境设置"
    echo "=========================================="
    echo ""
    
    check_os
    install_homebrew
    install_jdk
    install_android_sdk
    setup_environment_variables
    verify_installation
    
    echo ""
    echo "=========================================="
    print_success "环境设置完成！"
    echo "=========================================="
    echo ""
    print_info "下一步："
    print_info "1. 在Android Studio中打开 android-app 项目"
    print_info "2. 等待Gradle同步完成"
    print_info "3. 运行 ./scripts/build_apk.sh debug 构建APK"
    echo ""
}

# 运行主函数
main
