# OpenCanvas

Canvas 课程自动化工具集：**课程文件下载** + **增量轮询通知** + **作业自动完成**。

## 功能

### 1. 课程文件下载

自动下载 Canvas 课程文件到本地，支持学期过滤、仅建目录不下载、大文件跳过等。

```bash
python script/download_courses.py -init [--yes]
```

### 2. 轮询通知（公告 / 文件 / 作业）

每 10 分钟定期检查 Canvas **新作业、新文件、新公告**；有增量时通过 **微信** 推送。

```bash
python script/canvas_poll.py [--dry-run]
```

**通知格式特点：**
- 完整正文（不截断）
- 显示课程名、发布者、时间
- 提取 HTML 中的图片 URL
- 附带原文链接
- 微信友好的排版格式

### 3. 作业自动完成（Assignment Solver）⚡

自动检测新作业 → 通知你 → 回复确认 → 自动完成并发送文件审阅。

**工作流程：**
1. 轮询发现未提交的新作业
2. 微信通知：`📦 发现新作业 + 详情 + 回复 1 自动完成`
3. 你回复 `1` 确认
4. 自动执行：
   - 检查课程文件（知识库）
   - 读取课程 PDF/DOCX/PPTX 作为参考
   - 检测作业类型（论文 / 编程 / 混合）
   - LLM 生成内容（LaTeX 或代码）
   - 编译 LaTeX 生成 PDF
   - 打包文件发送给你审阅
5. **不自动提交到 Canvas**，由你手动提交

**手动命令：**
```bash
# 检查新作业
python script/assignment_solver.py --check

# 完成指定作业
python script/assignment_solver.py --solve <assignment_id>

# 列出待确认作业
python script/assignment_solver.py --list-pending
```

**输出示例：**
```
✅ 作业完成：In-class Assignment: Engineering Design 1
▸ 课程：TC3000JSP2026-1
▸ 类型：latex_essay
▸ 文件：2 个
文件已发送，请审阅。
```

## 环境要求

- Python 3.12+
- 已安装 OpenClaw（轮询通知通过 `openclaw message send` 发送微信消息）
- xelatex / pdflatex（编译 LaTeX）
- 可选 Conda 环境（见 `environment.yml`）

**Python 依赖：**
```bash
pip install httpx PyPDF2 python-docx python-pptx
```

**LaTeX 环境：**
```bash
# Ubuntu/Debian
sudo apt-get install texlive-xetex

# 或使用 tectonic（轻量替代）
curl --proto '=https' --tlsv1.2 -fsSL https://tectonic-typesetting.github.io/install.sh | sh
```

## 快速开始

1. **配置** — 编辑 `config/config.yaml`：
   ```yaml
   canvas:
     base_url: "https://your-canvas-instance.edu"
     access_token: "your-api-token"

   download:
     root: "/path/to/courses"
     term_filter:
       name_substrings: ["2025-2026 Spring"]

   notify:
     wechat:
       target: "your-wechat-id@im.wechat"
       account_id: "bot-account-id"
   ```

2. **下载课程文件：**
   ```bash
   python script/download_courses.py -init
   ```

3. **运行轮询：**
   ```bash
   python script/canvas_poll.py  # 或 --dry-run 先测试
   ```

4. **设置定时任务（可选）：**
   ```bash
   crontab -e
   # 每 10 分钟执行一次
   */10 * * * * cd /path/to/OpenCanvas && python script/canvas_poll.py >> logs/poll.log 2>&1
   ```

## 项目结构

```
OpenCanvas/
├── script/
│   ├── download_courses.py      # 课程文件下载
│   ├── canvas_poll.py           # 轮询通知（公告/文件/作业）
│   ├── assignment_solver.py     # ⚡ 作业自动完成引擎
│   ├── knowledge_base.py        # 课程知识库（PDF/DOCX 文本提取）
│   ├── state_manager.py         # 作业状态持久化（支持重启恢复）
│   ├── test_canvas.py           # Canvas API 连通性测试
│   ├── setup_cron.sh            # Cron 示例脚本
│   ├── README_POLL.md           # 轮询详细说明
│   └── load_settings.py         # 配置加载
├── config/
│   └── config.yaml              # 主配置（勿提交密钥）
├── state/                       # 状态文件（自动创建）
│   ├── poll_state.json          # 轮询状态
│   └── assignments.json         # 作业状态
├── output/                      # 作业输出目录
│   └── assignments/
│       └── <assignment_id>/     # 每个作业的产出
├── 26SP/                        # 课程文件下载目录
│   └── <课程名>/
├── logs/                        # 日志
└── environment.yml              # Conda 环境示例
```

## 作业完成流程详解

```
新作业检测
    ↓
微信通知（附作业详情）
    ↓
用户回复 "1" 确认
    ↓
检查课程文件 → 构建知识库
    ↓
检测作业类型
    ├─ LaTeX 论文 → LLM 生成 → xelatex 编译 → PDF
    ├─ 编程作业 → LLM 生成代码 → 解析多文件
    └─ 混合作业 → 同时生成
    ↓
打包文件（zip）
    ↓
发送给用户审阅
    ↓
状态更新为 completed
```

## 状态管理

作业状态持久化在 `state/assignments.json`，支持：
- `pending` — 待确认
- `approved` — 已确认，等待执行
- `running` — 正在完成
- `completed` — 已完成
- `failed` — 失败（附错误信息）

服务器重启后状态自动恢复。

## 注意事项

- ⚠️ **不会自动提交到 Canvas**，所有产出仅发送给你审阅
- 大文件（>20MB）仅保存链接，不下载
- 课程文件作为知识库参考，最多读取 20 个文件
- LLM API Key 可从环境变量 `LLM_API_KEY` 或 OpenClaw 配置中读取
