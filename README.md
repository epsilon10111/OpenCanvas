# OpenCanvas

Canvas 课程自动化工具集：**课程文件下载** + **轮询增量通知**。

## 运行环境（OpenClaw）

本项目**设计为在 OpenClaw 可执行环境中运行**：轮询脚本 `script/canvas_poll.py` 在发送通知时会调用本机 **`openclaw` CLI**（`openclaw message send`），因此需保证当前环境已安装并配置好 OpenClaw，且 `openclaw` 在 `PATH` 中可用。

- **下载与连通性测试**：`script/download_courses.py`、`script/test_canvas.py` 仅需 Python 3.12 与网络；可与轮询放在同一 OpenClaw 工作目录下维护。
- **定时轮询**：若在 OpenClaw 侧用计划任务 / Agent 周期执行，只需周期性调用 `python script/canvas_poll.py`（或先 `--dry-run` 调试）；亦可参考 `script/README_POLL.md` 中的 crontab 思路（在同等环境中配置）。

可选：使用 Conda 时参考仓库根目录 `environment.yml` 创建环境。

## 功能

### 课程文件下载

自动下载 Canvas 课程文件到本地，支持学期过滤、仅建目录不下载等。

```bash
python script/download_courses.py -init
```

### 轮询通知

定期检查 Canvas **新作业、新文件、新公告**；有增量时通过 **OpenClaw 微信通道**推送（需本机 `openclaw`）。

```bash
python script/canvas_poll.py [--dry-run]
```

配置说明（含 `notify.wechat` 等）见 [轮询通知配置指南](script/README_POLL.md)。

## 快速开始

1. 复制配置模板

   ```bash
   cp config/config.example.yaml config/config.yaml
   ```

2. 编辑 `config/config.yaml`，填写 Canvas 凭证（`canvas.base_url`、`canvas.access_token`）与下载目录（`download.root`）等。

3. （可选）轮询相关字段可参考 `config/config.poll.example.yaml`，将其中 **`poll:`** 以及与通知相关的段落**合并进**同一 `config/config.yaml`（具体键名以 `script/README_POLL.md` 与脚本为准）。

   > 说明：`config.poll.example.yaml` 中的 `poll:` 为配置示例；当前 `canvas_poll.py` 以脚本内逻辑与 `canvas` / `notify` 配置为主，若需完全由 YAML 驱动轮询参数，需在脚本中另行接入。

4. 下载课程文件

   ```bash
   python script/download_courses.py -init
   ```

5. （可选）测试 Canvas API

   ```bash
   python script/test_canvas.py
   ```

## 项目结构

```
OpenCanvas/
├── script/
│   ├── download_courses.py    # 课程文件下载
│   ├── canvas_poll.py         # 轮询通知（调用 openclaw 发微信）
│   ├── test_canvas.py         # Canvas API 连通性测试
│   ├── setup_cron.sh          # Cron 示例脚本（Linux）
│   ├── README_POLL.md         # 轮询与通知详细说明
│   └── load_settings.py       # 从 config/config.yaml 加载配置
├── config/
│   ├── config.yaml            # 主配置（需手动创建，勿提交密钥）
│   ├── config.example.yaml    # 下载等基础配置模板
│   └── config.poll.example.yaml  # 轮询 poll 段等配置示例（可与主配置合并）
├── state/                     # 状态文件（如 poll 状态，自动创建）
├── logs/                      # 日志目录（建议创建）
└── environment.yml            # Conda 环境示例
```
