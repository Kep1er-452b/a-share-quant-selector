#!/bin/bash

# A股量化选股系统 - Python代码复制脚本
# 使用方法: ./scripts/copy_python_code.sh

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

# 获取项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ANDROID_APP_DIR="$PROJECT_ROOT/android-app"
PYTHON_DEST_DIR="$ANDROID_APP_DIR/app/src/main/python"
ASSETS_DEST_DIR="$ANDROID_APP_DIR/app/src/main/assets"

# 源目录
SOURCE_DIR="$PROJECT_ROOT"

print_info "项目根目录: $PROJECT_ROOT"
print_info "Android项目目录: $ANDROID_APP_DIR"
print_info "Python目标目录: $PYTHON_DEST_DIR"

# 创建目标目录
print_info "创建目标目录..."
mkdir -p "$PYTHON_DEST_DIR"
mkdir -p "$ASSETS_DEST_DIR/config"
mkdir -p "$ASSETS_DEST_DIR/web"

# 复制Python文件
print_info "复制Python文件..."

# 主要Python文件
MAIN_FILES=(
    "main.py"
    "web_server.py"
    "test_dingtalk.py"
    "test_kline_chart.py"
)

for file in "${MAIN_FILES[@]}"; do
    if [ -f "$SOURCE_DIR/$file" ]; then
        cp "$SOURCE_DIR/$file" "$PYTHON_DEST_DIR/"
        print_success "复制: $file"
    else
        print_warning "文件不存在: $file"
    fi
done

# 复制Python目录
PYTHON_DIRS=(
    "strategy"
    "utils"
    "wyckoff_ai"
    "wyckoff-second"
    "tests"
    "scripts"
    "benchmarks"
    "prompts"
)

for dir in "${PYTHON_DIRS[@]}"; do
    if [ -d "$SOURCE_DIR/$dir" ]; then
        cp -r "$SOURCE_DIR/$dir" "$PYTHON_DEST_DIR/"
        print_success "复制目录: $dir"
    else
        print_warning "目录不存在: $dir"
    fi
done

# 复制配置文件
print_info "复制配置文件..."

CONFIG_FILES=(
    "config/config.yaml.template"
    "config/strategy_params.yaml"
    "config/trade_calendar_seed_2026.json"
    "config/github.yaml.template"
)

for file in "${CONFIG_FILES[@]}"; do
    if [ -f "$SOURCE_DIR/$file" ]; then
        cp "$SOURCE_DIR/$file" "$ASSETS_DEST_DIR/config/"
        print_success "复制配置: $file"
    else
        print_warning "配置文件不存在: $file"
    fi
done

# 复制Web前端文件
print_info "复制Web前端文件..."

if [ -d "$SOURCE_DIR/web" ]; then
    cp -r "$SOURCE_DIR/web" "$ASSETS_DEST_DIR/"
    print_success "复制Web前端"
else
    print_warning "Web目录不存在"
fi

# 复制文档文件
print_info "复制文档文件..."

DOC_FILES=(
    "README.md"
    "CHANGELOG.md"
    "B1_PATTERN_MATCH.md"
    "instruction.md"
    "requirements.txt"
)

for file in "${DOC_FILES[@]}"; do
    if [ -f "$SOURCE_DIR/$file" ]; then
        cp "$SOURCE_DIR/$file" "$ANDROID_APP_DIR/"
        print_success "复制文档: $file"
    else
        print_warning "文档文件不存在: $file"
    fi
done

# 复制.gitignore
if [ -f "$SOURCE_DIR/.gitignore" ]; then
    cp "$SOURCE_DIR/.gitignore" "$ANDROID_APP_DIR/"
    print_success "复制: .gitignore"
fi

# 创建Android适配模块（如果不存在）
if [ ! -f "$PYTHON_DEST_DIR/android_adapter.py" ]; then
    print_info "创建Android适配模块..."
    # 这里会创建android_adapter.py，但实际上我们已经有了
    print_warning "android_adapter.py已存在，跳过创建"
fi

# 创建__init__.py文件
print_info "创建__init__.py文件..."

INIT_DIRS=(
    "strategy"
    "utils"
    "wyckoff_ai"
)

for dir in "${INIT_DIRS[@]}"; do
    if [ -d "$PYTHON_DEST_DIR/$dir" ] && [ ! -f "$PYTHON_DEST_DIR/$dir/__init__.py" ]; then
        touch "$PYTHON_DEST_DIR/$dir/__init__.py"
        print_success "创建: $dir/__init__.py"
    fi
done

# 统计复制的文件
print_info "统计复制的文件..."
PYTHON_COUNT=$(find "$PYTHON_DEST_DIR" -name "*.py" | wc -l)
CONFIG_COUNT=$(find "$ASSETS_DEST_DIR/config" -type f | wc -l)
WEB_COUNT=$(find "$ASSETS_DEST_DIR/web" -type f 2>/dev/null | wc -l || echo 0)

print_success "复制完成！"
print_info "Python文件: $PYTHON_COUNT"
print_info "配置文件: $CONFIG_COUNT"
print_info "Web文件: $WEB_COUNT"

# 显示目录结构
print_info "Python目录结构:"
find "$PYTHON_DEST_DIR" -maxdepth 2 -type d | sort | head -20

print_success "代码复制完成！"
