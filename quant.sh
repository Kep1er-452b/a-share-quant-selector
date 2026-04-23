#!/bin/bash
# A股量化选股系统 - 快捷命令脚本

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUANT_DIR="$SCRIPT_DIR"
if [ -x "$QUANT_DIR/.venv/bin/python" ]; then
    PYTHON="${PYTHON:-$QUANT_DIR/.venv/bin/python}"
else
    PYTHON="${PYTHON:-$(command -v python3)}"
fi

if [ -z "$PYTHON" ]; then
    echo "未找到 python3，请先安装或把它加入 PATH。"
    exit 1
fi

cd "$QUANT_DIR" || exit 1

case "$1" in
    init)
        $PYTHON main.py init "${@:2}"
        ;;
    run)
        $PYTHON main.py run "${@:2}"
        ;;
    web)
        $PYTHON main.py web "${@:2}"
        ;;
    *)
        echo "使用方法: $0 {init|run|web}"
        echo ""
        echo "命令说明:"
        echo "  init     - 首次全量抓取6年历史数据"
        echo "  run      - 完整流程（更新+选股+通知）"
        echo "  web      - 启动 Web 界面"
        exit 1
        ;;
esac
