#!/bin/bash
# 设置 Canvas 轮询的 crontab 任务
# 用法：./setup_cron.sh [interval_minutes]

INTERVAL=${1:-30}  # 默认 30 分钟
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

# 检查虚拟环境
if [ ! -f "$VENV_PYTHON" ]; then
    echo "错误：未找到虚拟环境 $VENV_PYTHON"
    echo "请先运行：python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# 检查 Webhook Key
if [ -z "$WECHAT_WEBHOOK_KEY" ]; then
    echo "警告：未设置 WECHAT_WEBHOOK_KEY 环境变量"
    echo "请在 ~/.bashrc 或 ~/.zshrc 中添加："
    echo "  export WECHAT_WEBHOOK_KEY=\"your_key_here\""
    echo ""
    read -p "是否现在设置？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -p "输入 Webhook Key: " WEBHOOK_KEY
        echo "export WECHAT_WEBHOOK_KEY=\"$WEBHOOK_KEY\"" >> ~/.bashrc
        echo "已添加到 ~/.bashrc，请运行 'source ~/.bashrc' 生效"
    fi
fi

# 创建日志目录
mkdir -p "$PROJECT_ROOT/logs"

# 添加到 crontab
CRON_JOB="*/$INTERVAL * * * * cd $PROJECT_ROOT && $VENV_PYTHON $SCRIPT_DIR/canvas_poll.py >> $PROJECT_ROOT/logs/poll.log 2>&1"

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "canvas_poll.py"; then
    echo "警告：crontab 中已存在 canvas_poll.py 任务"
    crontab -l | grep "canvas_poll.py"
    read -p "是否覆盖？(y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消"
        exit 0
    fi
    # 移除旧任务
    crontab -l 2>/dev/null | grep -v "canvas_poll.py" | crontab -
fi

# 添加新任务
(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

echo "✓ 已设置 crontab 任务："
echo "  频率：每 $INTERVAL 分钟"
echo "  命令：$CRON_JOB"
echo ""
echo "查看 crontab: crontab -l"
echo "查看日志：tail -f $PROJECT_ROOT/logs/poll.log"
echo "删除任务：crontab -e 并删除对应行"
