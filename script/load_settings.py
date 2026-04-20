"""加载项目根目录下 config/config.yaml。"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        print(
            f"未找到配置文件：{CONFIG_PATH}\n"
            f"请复制 config/config.example.yaml 为 config/config.yaml 并填写。",
            file=sys.stderr,
        )
        sys.exit(1)
    with CONFIG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}
