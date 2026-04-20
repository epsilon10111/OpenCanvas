# Canvas 轮询通知配置指南

## 📦 功能说明

定时检查 Canvas 的**新作业、新文件、新公告**，有增量时通过**企业微信群机器人**推送通知。

**特点：**
- ✅ 仅增量通知 - 无新内容时不调用 API，0 token 消耗
- ✅ 本地状态追踪 - 自动记录已通知的项目 ID
- ✅ 企业微信推送 - 群机器人方式，配置简单
- ✅ 可配置检查窗口 - 默认检查最近 24 小时

---

## 🔧 配置步骤

### 1. 企业微信群机器人

1. 在企业微信群里添加「群机器人」
2. 复制 Webhook 地址中的 `key` 参数
   ```
   https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY_HERE
   ```
3. 设置环境变量：
   ```bash
   export WECHAT_WEBHOOK_KEY="YOUR_KEY_HERE"
   ```

### 2. 配置 Canvas 凭证

编辑 `config/config.yaml`，确保填写了：
```yaml
canvas:
  base_url: "https://your-canvas-school.com"
  access_token: "YOUR_CANVAS_TOKEN"
```

### 3. 测试运行

```bash
cd /home/admin/OpenCanvas

# 干跑测试（不发送通知）
python script/canvas_poll.py --dry-run

# 正式运行
python script/canvas_poll.py
```

---

## ⏰ 设置定时任务

### 方案 A: crontab（推荐）

```bash
crontab -e
```

添加以下行（每 30 分钟检查一次）：
```cron
*/30 * * * * cd /home/admin/OpenCanvas && /path/to/venv/bin/python script/canvas_poll.py >> logs/poll.log 2>&1
```

### 方案 B: systemd timer

创建 `/etc/systemd/system/canvas-poll.service`：
```ini
[Unit]
Description=Canvas Poll Service

[Service]
Type=oneshot
User=admin
WorkingDirectory=/home/admin/OpenCanvas
Environment=WECHAT_WEBHOOK_KEY=YOUR_KEY
ExecStart=/path/to/venv/bin/python script/canvas_poll.py
```

创建 `/etc/systemd/system/canvas-poll.timer`：
```ini
[Unit]
Description=Run Canvas Poll every 30 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
```

启用：
```bash
sudo systemctl enable --now canvas-poll.timer
```

---

## 📂 文件说明

```
OpenCanvas/
├── script/
│   ├── canvas_poll.py          # 轮询主脚本
│   └── README_POLL.md          # 本文档
├── config/
│   ├── config.yaml             # 主配置
│   └── config.poll.example.yaml # 轮询配置示例
├── state/
│   └── poll_state.json         # 自动创建，记录通知状态
└── logs/
    └── poll.log                # 建议的日志路径
```

---

## 🔍 状态文件

`state/poll_state.json` 记录：
```json
{
  "last_check": "2026-04-20T17:30:00+08:00",
  "notified_assignments": [123, 456, 789],
  "notified_files": [111, 222],
  "notified_announcements": [333]
}
```

**手动重置通知**：删除此文件，下次运行会重新通知所有内容。

---

## ⚠️ 注意事项

1. **Webhook Key 安全**
   - 不要提交到 Git
   - 泄露后立即在群里删除机器人并重新添加

2. **频率限制**
   - 企业微信 Webhook 有频率限制，建议 ≥5 分钟间隔
   - Canvas API 也有速率限制，避免过频检查

3. **失败处理**
   - 网络错误会写入日志，不影响状态文件
   - 企业微信返回错误时会记录完整响应

4. **时区**
   - 脚本使用 UTC 时间，显示时间会转为本地时间

---

## 🛠️ 故障排查

### 无通知发送
- 检查 `state/poll_state.json` 是否已记录所有项目
- 删除状态文件重置
- 检查 Canvas 是否有新内容

### 企业微信发送失败
- 检查 `WECHAT_WEBHOOK_KEY` 环境变量
- 确认企业微信机器人已启用
- 查看日志中的完整错误响应

### Canvas API 错误
- 检查 `access_token` 是否有效
- 确认 `base_url` 正确
- 检查网络连接
