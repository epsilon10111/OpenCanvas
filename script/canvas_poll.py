#!/usr/bin/env python3
"""
Canvas 轮询检查脚本
定期检查通知、作业、新文件，有增量时通过企业微信 Webhook 推送

用法:
    python script/canvas_poll.py [--dry-run]
    
配置:
    - config/config.yaml 中的 canvas 配置
    - 环境变量 WECHAT_WEBHOOK_KEY (企业微信群机器人 key)
    - state/poll_state.json (自动创建，记录上次检查状态)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from load_settings import load_config

STATE_FILE = Path(__file__).parent.parent / "state" / "poll_state.json"
CHECK_WINDOW_HOURS = 24  # 检查最近 24 小时的内容


def load_state() -> dict[str, Any]:
    """加载本地状态文件"""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "last_check": None,
        "notified_assignments": [],
        "notified_files": [],
        "notified_announcements": [],
    }


def save_state(state: dict[str, Any]) -> None:
    """保存状态到本地"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def canvas_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.strip()}"}


def get_time_window() -> tuple[str, str]:
    """返回检查时间窗口的 ISO 格式时间"""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=CHECK_WINDOW_HOURS)
    return start.isoformat(), now.isoformat()


def fetch_notifications(client: httpx.Client, base_url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """获取 Canvas 通知"""
    url = f"{base_url}/api/v1/communication_channels"
    try:
        resp = client.get(url, headers=headers, params={"per_page": 50})
        resp.raise_for_status()
        channels = resp.json()
        # Canvas 通知 API 较复杂，这里简化处理
        return []
    except httpx.HTTPError as e:
        print(f"[通知] 获取失败：{e}", file=sys.stderr)
        return []


def fetch_assignments(
    client: httpx.Client, base_url: str, headers: dict[str, str], course_ids: list[int]
) -> list[dict[str, Any]]:
    """获取课程作业"""
    assignments = []
    for cid in course_ids:
        url = f"{base_url}/api/v1/courses/{cid}/assignments"
        try:
            resp = client.get(
                url,
                headers=headers,
                params={"per_page": 50, "include[]": ["submission", "assignment_group"]},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                assignments.extend(data)
        except httpx.HTTPError as e:
            print(f"[作业] 课程{cid}获取失败：{e}", file=sys.stderr)
    return assignments


def fetch_recent_files(
    client: httpx.Client, base_url: str, headers: dict[str, str], course_ids: list[int]
) -> list[dict[str, Any]]:
    """获取课程文件"""
    files = []
    for cid in course_ids:
        url = f"{base_url}/api/v1/courses/{cid}/files"
        try:
            resp = client.get(url, headers=headers, params={"per_page": 50})
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                files.extend(data)
        except httpx.HTTPError as e:
            print(f"[文件] 课程{cid}获取失败：{e}", file=sys.stderr)
    return files


def fetch_announcements(
    client: httpx.Client, base_url: str, headers: dict[str, str], course_ids: list[int]
) -> list[dict[str, Any]]:
    """获取课程公告"""
    announcements = []
    for cid in course_ids:
        url = f"{base_url}/api/v1/courses/{cid}/discussion_topics"
        try:
            resp = client.get(url, headers=headers, params={"per_page": 30, "only_announcements": True})
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                announcements.extend(data)
        except httpx.HTTPError as e:
            print(f"[公告] 课程{cid}获取失败：{e}", file=sys.stderr)
    return announcements


def filter_new_items(
    items: list[dict[str, Any]],
    notified_ids: list[int],
    id_field: str = "id",
) -> list[dict[str, Any]]:
    """过滤出未通知过的新项目"""
    return [item for item in items if item.get(id_field) not in notified_ids]


def format_markdown_digest(
    assignments: list[dict[str, Any]],
    files: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
) -> str:
    """格式化企业微信 Markdown 消息"""
    lines = ["## 📦 Canvas 更新提醒\n"]

    if assignments:
        lines.append("### 📝 新作业")
        for a in assignments[:5]:  # 最多显示 5 个
            title = a.get("name", "未命名")
            due = a.get("due_at")
            if due:
                due_str = due[:16].replace("T", " ")
                lines.append(f"- **{title}** (截止：{due_str})")
            else:
                lines.append(f"- **{title}**")
        lines.append("")

    if files:
        lines.append("### 📁 新文件")
        for f in files[:5]:
            name = f.get("display_name") or f.get("filename", "未命名")
            lines.append(f"- {name}")
        lines.append("")

    if announcements:
        lines.append("### 📢 新公告")
        for ann in announcements[:5]:
            title = ann.get("title", "未命名")
            lines.append(f"- {title}")
        lines.append("")

    if not (assignments or files or announcements):
        return ""  # 无增量

    lines.append(f"_更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}__")
    return "\n".join(lines)


def send_wechat_webhook(markdown_content: str, webhook_key: str) -> dict[str, Any]:
    """发送企业微信 Webhook 消息"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": markdown_content},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


def get_active_course_ids(client: httpx.Client, base_url: str, headers: dict[str, str]) -> list[int]:
    """获取活跃课程 ID 列表"""
    url = f"{base_url}/api/v1/courses"
    try:
        resp = client.get(url, headers=headers, params={"enrollment_state[]": "active", "per_page": 100})
        resp.raise_for_status()
        courses = resp.json()
        return [c["id"] for c in courses if isinstance(c, dict) and "id" in c]
    except httpx.HTTPError as e:
        print(f"[课程] 获取失败：{e}", file=sys.stderr)
        return []


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Canvas 轮询检查 + 企业微信通知")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不发送通知")
    args = parser.parse_args()

    # 加载配置
    cfg = load_config()
    canvas = cfg.get("canvas") or {}
    base_url = str(canvas.get("base_url", "")).rstrip("/")
    token = str(canvas.get("access_token", "")).strip()

    if not base_url or not token:
        print("[错误] 请在 config/config.yaml 填写 canvas.base_url 与 canvas.access_token", file=sys.stderr)
        sys.exit(1)

    webhook_key = os.environ.get("WECHAT_WEBHOOK_KEY")
    if not webhook_key and not args.dry_run:
        print("[错误] 请设置环境变量 WECHAT_WEBHOOK_KEY", file=sys.stderr)
        sys.exit(1)

    # 加载状态
    state = load_state()
    headers = canvas_auth_headers(token)

    with httpx.Client(timeout=60.0) as client:
        # 获取课程列表
        course_ids = get_active_course_ids(client, base_url, headers)
        if not course_ids:
            print("[轮询] 无活跃课程")
            return

        print(f"[轮询] 检查 {len(course_ids)} 门课程...")

        # 获取各类内容
        assignments = fetch_assignments(client, base_url, headers, course_ids)
        files = fetch_recent_files(client, base_url, headers, course_ids)
        announcements = fetch_announcements(client, base_url, headers, course_ids)

        # 过滤增量
        new_assignments = filter_new_items(assignments, state.get("notified_assignments", []))
        new_files = filter_new_items(files, state.get("notified_files", []))
        new_announcements = filter_new_items(announcements, state.get("notified_announcements", []))

        print(f"[轮询] 新作业:{len(new_assignments)} 新文件:{len(new_files)} 新公告:{len(new_announcements)}")

        # 无增量则退出
        if not (new_assignments or new_files or new_announcements):
            print("[轮询] 无新内容，跳过通知")
            return

        # 格式化消息
        markdown = format_markdown_digest(new_assignments, new_files, new_announcements)
        if not markdown:
            print("[轮询] 无内容可发送")
            return

        # 发送通知
        if args.dry_run:
            print("[dry-run] 将发送消息:")
            print(markdown)
        else:
            print(f"[通知] 发送企业微信消息...")
            result = send_wechat_webhook(markdown, webhook_key)
            if result.get("errcode") == 0:
                print("[通知] 发送成功 ✓")
            else:
                print(f"[通知] 发送失败：{result}", file=sys.stderr)
                sys.exit(1)

        # 更新状态
        state["last_check"] = datetime.now().isoformat()
        state["notified_assignments"] = [a["id"] for a in assignments if a.get("id")]
        state["notified_files"] = [f["id"] for f in files if f.get("id")]
        state["notified_announcements"] = [a["id"] for a in announcements if a.get("id")]
        save_state(state)
        print(f"[状态] 已更新 {STATE_FILE}")


if __name__ == "__main__":
    main()
