# OpenCanvas

Canvas 课程自动化工具集 - 下载文件 + 轮询通知

## 功能

### 📥 课程文件下载
自动下载 Canvas 课程文件到本地，支持学期过滤、目录仅创建等。

```bash
python script/download_courses.py -init
```

### 📦 轮询通知（新增）
定期检查 Canvas 新作业、新文件、新公告，通过企业微信推送。

```bash
python script/canvas_poll.py [--dry-run]
```

详见 [轮询通知配置指南](script/README_POLL.md)

## 快速开始

1. 复制配置模板
   ```bash
   cp config/config.example.yaml config/config.yaml
   ```

2. 编辑 `config/config.yaml` 填写 Canvas 凭证

3. 下载课程文件
   ```bash
   python script/download_courses.py -init
   ```

## 项目结构

```
OpenCanvas/
├── script/
│   ├── download_courses.py    # 课程文件下载
│   ├── canvas_poll.py         # 轮询通知
│   ├── setup_cron.sh          # Cron 设置脚本
│   └── load_settings.py       # 配置加载
├── config/
│   ├── config.yaml            # 主配置（需手动创建）
│   └── config.example.yaml    # 配置模板
├── state/                     # 状态文件（自动创建）
└── logs/                      # 日志文件（建议创建）
```
