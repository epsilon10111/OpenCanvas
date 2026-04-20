# Canvas 轮询通知配置指南

## 📦 功能说明

定时检查 Canvas 的**新作业、新文件、新公告**，有增量时通过微信发送通知。

**特点：**
- ✅ 仅增量通知 - 无新内容时不发送消息 (0 打扰)
- ✅ 20MB 限制 - 超过 20MB 的文件只记录链接，不下载
- ✅ 微信通知 - 直接发送消息到微信聊天
- ✅ 可配置检查窗口 - 默认检查最近 24 小时

---

## 🔧 配置步骤

### 1. 配置 Canvas 凭证

编辑 `config/config.yaml`，确保填写了：
```yaml
canvas:
  base_url: "https://your-canvas-school.com"
  access_token: "YOUR_CANVAS_TOKEN"
```

### 2. 配置微信通知

在 `config/config.yaml` 中添加：
```yaml
notify:
  wechat:
    target: "你的微信 ID@im.wechat"        # 从聊天上下文获取
    account_id: "你的账号 ID-im-bot"       # 从聊天上下文获取
```

**获取方法：**
- `target`: 当前聊天的 chat_id (格式如 `o9cq8067r57_xxx@im.wechat`)
- `account_id`: OpenClaw 微信账号 ID (格式如 `fc9e8e358015-im-bot`)

### 3. 测试运行

```bash
cd /home/admin/OpenCanvas

# 干跑测试（不发送消息）
python script/canvas_poll.py --dry-run

# 正式运行（有更新时会发微信消息）
python script/canvas_poll.py
```

---

## ⏰ 设置定时任务

### 方案 A: crontab（推荐）

```bash
crontab -e
```

添加以下行（每 10 分钟检查一次）：
```cron
*/10 * * * * cd /home/admin/OpenCanvas && .venv/bin/python script/canvas_poll.py >> logs/poll.log 2>&1
```

### 方案 B: 使用 setup_cron.sh 脚本

```bash
# 设置每 30 分钟检查一次
./script/setup_cron.sh 30

# 查看 crontab
crontab -l

# 查看日志
tail -f logs/poll.log
```

---

## 📂 文件说明

```
OpenCanvas/
├── script/
│   ├── canvas_poll.py          # 轮询主脚本
│   ├── download_courses.py     # 下载脚本（含 20MB 限制）
│   ├── setup_cron.sh           # Cron 设置脚本
│   └── README_POLL.md          # 本文档
├── config/
│   ├── config.yaml             # 主配置
│   └── config.poll.example.yaml # 轮询配置示例
├── state/
│   ├── poll_state.json         # 自动创建，记录通知状态
│   └── poll_notification.md    # 自动创建，生成的通知文件
└── logs/
    └── poll.log                # 建议的日志路径
```

---

## 📋 通知文件示例

`state/poll_notification.md` 内容示例：

```markdown
# 📦 Canvas 更新提醒

_检查时间：2026-04-20 17:30:00_

## 📝 新作业

### Assignment 3: Technical Report
- 课程：TC3000JSP2026-1 Technical Communication
- 截止：2026-04-25 23:59
- 分数：100

## 📁 新文件

### Lecture_Slides_Week5.pdf
- 课程：TC3000JSP2026-1 Technical Communication
- 大小：15.3 MB

### Large_Video_File.mp4
- 课程：TC3000JSP2026-1 Technical Communication
- 大小：156.8 MB ⚠️ **超过 20MB，不自动下载**
- 链接：https://canvas-school.com/files/12345/download

## 📢 新公告

### Midterm Exam Schedule Released
- 课程：TC3000JSP2026-1 Technical Communication
- 摘要：The midterm exam will be held on May 1st...

---
**说明**: 超过 20MB 的文件不会自动下载，请手动访问链接。
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

### 20MB 限制
- **下载脚本** (`download_courses.py`): 超过 20MB 的文件保存为 `.url` 链接文件
- **轮询脚本** (`canvas_poll.py`): 超过 20MB 的文件在通知中标记，不自动下载
- 链接文件位置：与课程文件同目录，文件名如 `Large_File.pdf.url`

### 频率限制
- Canvas API 有速率限制，建议检查间隔 ≥5 分钟
- 默认 10 分钟检查一次，既能及时通知又不会过于频繁

### 失败处理
- 网络错误会写入日志，不影响状态文件
- 查看 `logs/poll.log` 排查问题

### 时区
- 脚本使用 UTC 时间，显示时间会转为本地时间

---

## 🛠️ 故障排查

### 无通知文件生成
- 检查 `state/poll_state.json` 是否已记录所有项目
- 删除状态文件重置
- 检查 Canvas 是否有新内容

### 下载被跳过
- 查看日志中是否有 `⚠️ 跳过 (>20MB)` 提示
- 超过 20MB 的文件会生成 `.url` 链接文件
- 手动访问链接下载大文件

### Canvas API 错误
- 检查 `access_token` 是否有效
- 确认 `base_url` 正确
- 检查网络连接

---

## 🔗 相关文档

- [Canvas API 文档](https://canvas.instructure.com/doc/api/)
- [项目 README](../README.md)
