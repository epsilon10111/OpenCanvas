#!/usr/bin/env python3
"""
Canvas 轮询检查脚本
定期检查通知、作业、新文件，有增量时通过微信发送通知

用法:
    python script/canvas_poll.py [--dry-run]
    
配置:
    - config/config.yaml 中的 canvas 配置
    - state/poll_state.json (自动创建，记录上次检查状态)
    
特点:
    - 仅增量通知 - 无新内容时不发送消息
    - 20MB 限制 - 超过 20MB 的文件只记录链接，不下载
    - 微信通知 - 直接发送消息到当前聊天
    - 完整内容 - 公告不截断，显示作者，提取图片
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from load_settings import load_config

STATE_FILE = Path(__file__).parent.parent / "state" / "poll_state.json"
NOTIFICATION_FILE = Path(__file__).parent.parent / "state" / "poll_notification.md"
SIZE_LIMIT_MB = 20
SIZE_LIMIT_BYTES = SIZE_LIMIT_MB * 1024 * 1024
DIVIDER = "━━━━━━━━━━━━━━━━━━"


class HTMLToTextParser(HTMLParser):
    """将 HTML 转换为可读文本，同时提取图片 URL"""

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.text_parts: list[str] = []
        self.images: list[str] = []
        self._in_skip = False
        self._skip_tags = {"script", "style"}
        self._skip_count = 0
        self._block_tags = {
            "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "ul", "ol", "li", "table", "tr", "blockquote",
            "pre", "hr", "br", "section", "article", "header", "footer",
        }
        self._list_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._skip_tags:
            self._skip_count += 1
            self._in_skip = True
            return
        if self._in_skip:
            return

        attrs_dict = dict(attrs)

        if tag == "img":
            src = attrs_dict.get("src", "")
            if src:
                img_url = urljoin(self.base_url, src)
                self.images.append(img_url)
            return

        if tag == "br":
            self.text_parts.append("\n")
        elif tag in self._block_tags:
            self.text_parts.append("\n")
        elif tag == "li":
            self._list_depth += 1
            self.text_parts.append("\n  " + "  " * (self._list_depth - 1) + "• ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._skip_tags:
            self._skip_count -= 1
            if self._skip_count <= 0:
                self._in_skip = False
                self._skip_count = 0
            return
        if self._in_skip:
            return

        if tag in self._block_tags:
            self.text_parts.append("\n")
        if tag == "li":
            self._list_depth = max(0, self._list_depth - 1)

    def handle_data(self, data):
        if self._in_skip:
            return
        self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        lines = raw.split("\n")
        cleaned = []
        blank_count = 0
        for line in lines:
            if line.strip():
                blank_count = 0
                cleaned.append(line)
            else:
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append("")
        return "\n".join(cleaned).strip()


def html_to_readable(html_content: str, base_url: str = "") -> tuple[str, list[str]]:
    """将 HTML 内容转换为可读文本，返回 (文本, 图片URL列表)"""
    if not html_content:
        return "", []

    parser = HTMLToTextParser(base_url)
    try:
        parser.feed(html_content)
    except Exception:
        return strip_html_tags(html_content), []

    return parser.get_text(), parser.images


def strip_html_tags(html: str) -> str:
    """简单移除 HTML 标签"""
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


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
                for a in data:
                    a["_course_id"] = cid  # 注入课程 ID
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
                for f in data:
                    f["_course_id"] = cid  # 注入课程 ID
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
                for a in data:
                    a["_course_id"] = cid  # 注入课程 ID
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


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def get_course_id(item: dict[str, Any]) -> int | None:
    """获取项目的课程 ID（优先使用我们注入的 _course_id）"""
    return item.get("_course_id") or item.get("course_id") or item.get("context_course_id") or item.get("context_id")


def format_announcement(ann: dict[str, Any], course_name: str, base_url: str) -> str:
    """格式化单个公告为微信友好的可读格式"""
    title = ann.get("title", "未命名")
    author = ann.get("user_name") or ann.get("author_name") or "未知"
    posted_at = ann.get("posted_at") or ann.get("created_at")
    message_html = ann.get("message", "")
    ann_id = ann.get("id", "")
    cid = get_course_id(ann)

    # 转换 HTML 为可读文本，提取图片
    message_text, images = html_to_readable(message_html, base_url)

    lines = []
    lines.append(f"**📢 公告 | {title}**")
    lines.append("")
    lines.append(f"▸ 课程：{course_name}")
    lines.append(f"▸ 发布者：{author}")
    if posted_at:
        try:
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            lines.append(f"▸ 时间：{dt.strftime('%Y-%m-%d %H:%M')}")
        except Exception:
            lines.append(f"▸ 时间：{posted_at[:16].replace('T', ' ')}")

    if message_text:
        lines.append("")
        lines.append(message_text)

    # 添加图片
    if images:
        lines.append("")
        for img_url in images:
            lines.append(f"[图片] {img_url}")

    # 原文链接
    if cid and ann_id:
        lines.append("")
        lines.append(f"🔗 原文链接：{base_url}/courses/{cid}/discussion_topics/{ann_id}")

    return "\n".join(lines)


def format_assignment(a: dict[str, Any], course_name: str, base_url: str) -> str:
    """格式化单个作业为微信友好的可读格式"""
    title = a.get("name", "未命名")
    cid = get_course_id(a)
    due = a.get("due_at")
    points = a.get("points_possible")
    description = a.get("description", "")
    aid = a.get("id", "")

    desc_text = ""
    if description:
        desc_text = html_to_readable(description, base_url)[0]

    lines = []
    lines.append(f"**📝 作业 | {title}**")
    lines.append("")
    lines.append(f"▸ 课程：{course_name}")
    if due:
        due_str = due[:16].replace("T", " ")
        lines.append(f"▸ 截止：{due_str}")
    if points is not None:
        lines.append(f"▸ 分数：{points}")
    if desc_text:
        lines.append("")
        lines.append(desc_text)
    if cid and aid:
        lines.append("")
        lines.append(f"🔗 查看详情：{base_url}/courses/{cid}/assignments/{aid}")
    return "\n".join(lines)


def format_file_item(f: dict[str, Any], course_name: str) -> str:
    """格式化单个文件为微信友好的可读格式"""
    name = f.get("display_name") or f.get("filename", "未命名")
    cid = get_course_id(f)
    size = f.get("size", 0)
    file_url = f.get("url", "")

    size_str = format_size(size) if size else "未知"
    over_limit = size and size > SIZE_LIMIT_BYTES
    hint = " ⚠️ 超过 20MB，不自动下载" if over_limit else ""

    lines = []
    lines.append(f"**📁 文件 | {name}**")
    lines.append("")
    lines.append(f"▸ 课程：{course_name}")
    lines.append(f"▸ 大小：{size_str}{hint}")
    if file_url:
        lines.append(f"▸ 链接：{file_url}")
    return "\n".join(lines)


def format_markdown_digest(
    assignments: list[dict[str, Any]],
    files: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    course_map: dict[int, str],
    base_url: str = "",
) -> str:
    """格式化微信友好的通知内容"""
    lines = []
    lines.append("📦 Canvas 更新提醒")
    lines.append(f"▸ 检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(DIVIDER)
    lines.append("")

    # ── 公告 ──
    if announcements:
        for ann in announcements:
            cid = get_course_id(ann)
            course_name = course_map.get(cid, f"课程{cid}") if cid else "未知课程"
            lines.append(format_announcement(ann, course_name, base_url))
            lines.append("")
            lines.append(DIVIDER)
            lines.append("")

    # ── 作业 ──
    if assignments:
        for a in assignments:
            cid = get_course_id(a)
            course_name = course_map.get(cid, f"课程{cid}") if cid else "未知课程"
            lines.append(format_assignment(a, course_name, base_url))
            lines.append("")
            lines.append(DIVIDER)
            lines.append("")

    # ── 文件 ──
    if files:
        for fi in files:
            cid = get_course_id(fi)
            course_name = course_map.get(cid, f"课程{cid}") if cid else "未知课程"
            lines.append(format_file_item(fi, course_name))
            lines.append("")
            lines.append(DIVIDER)
            lines.append("")

    if not (assignments or files or announcements):
        return ""  # 无增量

    lines.append(f"说明：超过 {SIZE_LIMIT_MB}MB 的文件不会自动下载，请手动访问链接。")
    return "\n".join(lines)


def send_wechat_message(markdown_content: str, target: str, account_id: str) -> bool:
    """通过 OpenClaw 发送微信消息"""
    import subprocess
    try:
        cmd = [
            "openclaw", "message", "send",
            "--channel", "openclaw-weixin",
            "--target", target,
            "--account", account_id,
            "--message", markdown_content
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"[通知] 微信消息发送成功 ✓")
            return True
        else:
            print(f"[通知] 发送失败：{result.stderr}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print(f"[通知] 发送超时", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[通知] 发送错误：{e}", file=sys.stderr)
        return False


def get_active_courses(
    client: httpx.Client, base_url: str, headers: dict[str, str]
) -> tuple[list[int], dict[int, str]]:
    """获取活跃课程 ID 列表和课程名映射"""
    url = f"{base_url}/api/v1/courses"
    try:
        resp = client.get(url, headers=headers, params={"enrollment_state[]": "active", "per_page": 100})
        resp.raise_for_status()
        courses = resp.json()
        course_ids = []
        course_map = {}
        for c in courses:
            if isinstance(c, dict) and "id" in c:
                course_ids.append(c["id"])
                course_map[c["id"]] = c.get("name") or c.get("course_code") or f"课程{c['id']}"
        return course_ids, course_map
    except httpx.HTTPError as e:
        print(f"[课程] 获取失败：{e}", file=sys.stderr)
        return [], {}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Canvas 轮询检查 + 微信通知")
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

    # 加载通知配置
    notify = cfg.get("notify") or {}
    wechat = notify.get("wechat") or {}
    target = wechat.get("target")
    account_id = wechat.get("account_id")

    if not target and not args.dry_run:
        print("[错误] 请在 config/config.yaml 填写 notify.wechat.target (你的微信 ID)", file=sys.stderr)
        sys.exit(1)
    if not account_id and not args.dry_run:
        print("[错误] 请在 config/config.yaml 填写 notify.wechat.account_id", file=sys.stderr)
        sys.exit(1)

    # 加载状态
    state = load_state()
    headers = canvas_auth_headers(token)

    with httpx.Client(timeout=60.0) as client:
        # 获取课程列表
        course_ids, course_map = get_active_courses(client, base_url, headers)
        if not course_ids:
            print("[轮询] 无活跃课程")
            return

        print(f"[轮询] 检查 {len(course_ids)} 门课程...")

        # 获取各类内容（函数内会注入 _course_id）
        assignments = fetch_assignments(client, base_url, headers, course_ids)
        files = fetch_recent_files(client, base_url, headers, course_ids)
        announcements = fetch_announcements(client, base_url, headers, course_ids)

        # 过滤增量
        new_assignments = filter_new_items(assignments, state.get("notified_assignments", []))
        new_files = filter_new_items(files, state.get("notified_files", []))
        new_announcements = filter_new_items(announcements, state.get("notified_announcements", []))

        # 统计超过 20MB 的文件
        large_files = [f for f in new_files if f.get("size", 0) > SIZE_LIMIT_BYTES]
        normal_files = [f for f in new_files if f.get("size", 0) <= SIZE_LIMIT_BYTES]

        print(f"[轮询] 新作业:{len(new_assignments)} 新文件:{len(new_files)} (>{SIZE_LIMIT_MB}MB:{len(large_files)}) 新公告:{len(new_announcements)}")

        # 无增量则退出
        if not (new_assignments or new_files or new_announcements):
            print("[轮询] 无新内容，跳过通知")
            return

        # 格式化消息
        markdown = format_markdown_digest(
            new_assignments, new_files, new_announcements, course_map, base_url
        )
        if not markdown:
            print("[轮询] 无内容可发送")
            return

        # 发送微信通知
        if args.dry_run:
            print("[dry-run] 将发送消息:")
            print(markdown)
        else:
            success = send_wechat_message(markdown, target, account_id)
            print(f"\n[摘要] 新作业:{len(new_assignments)} 新文件:{len(new_files)} 新公告:{len(new_announcements)}")
            if large_files:
                print(f"[注意] {len(large_files)} 个文件超过 {SIZE_LIMIT_MB}MB，仅保存链接")
            if not success:
                print("[警告] 消息发送失败，但状态已更新", file=sys.stderr)

        # 更新状态
        state["last_check"] = datetime.now().isoformat()
        state["notified_assignments"] = [a["id"] for a in assignments if a.get("id")]
        state["notified_files"] = [f["id"] for f in files if f.get("id")]
        state["notified_announcements"] = [a["id"] for a in announcements if a.get("id")]
        save_state(state)
        print(f"[状态] 已更新 {STATE_FILE}")


if __name__ == "__main__":
    main()
