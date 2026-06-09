#!/bin/bash

# A股量化选股系统 - GitHub仓库设置脚本
# 使用方法: ./scripts/setup_github.sh YOUR_USERNAME

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# 检查参数
if [ -z "$1" ]; then
    print_error "请提供GitHub用户名"
    echo "使用方法: $0 YOUR_USERNAME"
    exit 1
fi

GITHUB_USERNAME=$1
REPO_NAME="a-share-quant-selector"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

print_info "GitHub用户名: $GITHUB_USERNAME"
print_info "仓库名称: $REPO_NAME"
print_info "项目目录: $PROJECT_ROOT"

# 检查Git
if ! command -v git &> /dev/null; then
    print_error "Git未安装，请先安装Git"
    exit 1
fi

# 检查GitHub CLI（可选）
if command -v gh &> /dev/null; then
    print_info "检测到GitHub CLI"
    USE_GH_CLI=true
else
    print_warning "未检测到GitHub CLI，将使用手动方式"
    USE_GH_CLI=false
fi

# 进入项目目录
cd "$PROJECT_ROOT"

# 检查是否已初始化Git
if [ ! -d ".git" ]; then
    print_info "初始化Git仓库..."
    git init
    print_success "Git仓库初始化完成"
else
    print_info "Git仓库已存在"
fi

# 创建.gitignore（如果不存在）
if [ ! -f ".gitignore" ]; then
    print_info "创建.gitignore..."
    cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
.venv/
*.egg-info/
dist/
build/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Android
android-app/.gradle/
android-app/build/
android-app/app/build/
android-app/local.properties
*.apk
*.aab

# Data
data/
*.csv
*.json
!config/*.json

# Logs
logs/
*.log

# Config
config/config.yaml
config/config_local.yaml
config/github.yaml

# Outputs
outputs/
stock-selected/
EOF
    print_success ".gitignore创建完成"
fi

# 配置远程仓库
REMOTE_URL="https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"

print_info "配置远程仓库: $REMOTE_URL"

# 检查是否已配置远程仓库
if git remote get-url origin &> /dev/null; then
    CURRENT_REMOTE=$(git remote get-url origin)
    if [ "$CURRENT_REMOTE" != "$REMOTE_URL" ]; then
        print_warning "远程仓库已配置为: $CURRENT_REMOTE"
        read -p "是否更新为新的远程仓库? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            git remote set-url origin "$REMOTE_URL"
            print_success "远程仓库已更新"
        fi
    else
        print_success "远程仓库已正确配置"
    fi
else
    git remote add origin "$REMOTE_URL"
    print_success "远程仓库已添加"
fi

# 添加所有文件
print_info "添加文件..."
git add .

# 显示状态
print_info "Git状态:"
git status

# 提交
print_info "提交文件..."
read -p "请输入提交消息 (默认: 'Initial commit: Android app'): " COMMIT_MESSAGE
COMMIT_MESSAGE=${COMMIT_MESSAGE:-"Initial commit: Android app"}

git commit -m "$COMMIT_MESSAGE"
print_success "文件已提交"

# 推送
print_info "推送到GitHub..."
read -p "是否现在推送到GitHub? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    git push -u origin main
    print_success "代码已推送到GitHub"
    
    echo ""
    echo "=========================================="
    print_success "设置完成！"
    echo "=========================================="
    echo ""
    print_info "下一步："
    print_info "1. 访问 https://github.com/$GITHUB_USERNAME/$REPO_NAME"
    print_info "2. 点击 'Actions' 标签查看构建状态"
    print_info "3. 等待构建完成（约10-20分钟）"
    print_info "4. 在 'Artifacts' 或 'Releases' 中下载APK"
    echo ""
    print_info "详细说明请查看: android-app/GITHUB_ACTIONS_GUIDE.md"
else
    print_info "代码已提交但未推送"
    print_info "稍后推送请运行: git push -u origin main"
fi
