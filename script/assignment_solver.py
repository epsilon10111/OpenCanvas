#!/usr/bin/env python3
"""
Assignment Solver - 自动完成 Canvas 作业

功能:
1. 检测新作业
2. 通知用户，等待确认（回复"1"自动完成）
3. 读取课程文件作为知识库
4. 使用 LLM 生成作业内容
5. 编译 LaTeX 生成 PDF（或生成代码等其他输出）
6. 打包文件发送给用户审阅

用法:
    python script/assignment_solver.py --check          # 检查新作业
    python script/assignment_solver.py --solve <id>     # 完成指定作业
    python script/assignment_solver.py --list-pending   # 列出待确认作业
    python script/assignment_solver.py --dry-run        # 检查新作业（不通知）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from load_settings import load_config
from state_manager import AssignmentState
from knowledge_base import (
    build_course_knowledge,
    check_course_files,
    ensure_course_files,
)

# 输出目录
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "assignments"
COURSE_ROOT = Path(__file__).parent.parent / "26SP"

# LLM 配置
LLM_MODEL = "qwen3.5-plus"


def canvas_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.strip()}"}


def get_course_map(client: httpx.Client, base_url: str, headers: dict) -> dict[int, str]:
    """获取课程 ID → 名称映射"""
    url = f"{base_url}/api/v1/courses"
    resp = client.get(url, headers=headers, params={"enrollment_state[]": "active", "per_page": 100})
    resp.raise_for_status()
    course_map = {}
    for c in resp.json():
        if isinstance(c, dict) and "id" in c:
            course_map[c["id"]] = c.get("name") or c.get("course_code") or f"课程{c['id']}"
    return course_map


def fetch_assignment_details(
    client: httpx.Client, base_url: str, headers: dict, course_id: int, assignment_id: int
) -> Optional[dict[str, Any]]:
    """获取作业详细信息"""
    url = f"{base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
    resp = client.get(url, headers=headers, params={"include[]": "submission"})
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data
    return None


def fetch_all_assignments(
    client: httpx.Client, base_url: str, headers: dict, course_ids: list[int]
) -> list[dict[str, Any]]:
    """获取所有课程的作业"""
    assignments = []
    for cid in course_ids:
        url = f"{base_url}/api/v1/courses/{cid}/assignments"
        try:
            resp = client.get(
                url,
                headers=headers,
                params={"per_page": 100, "include[]": ["submission", "assignment_group"]},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                for a in data:
                    a["_course_id"] = cid
                assignments.extend(data)
        except httpx.HTTPError as e:
            print(f"[作业] 课程{cid}获取失败：{e}", file=sys.stderr)
    return assignments


def get_course_dir(course_id: int, course_name: str = "") -> Path:
    """获取课程文件目录
    课程目录名通常是像 TC3000JSP2026-1 这样的代码，不包含 course_id
    需要通过课程名称中的课程代码来匹配
    """
    if COURSE_ROOT.exists():
        # 优先按课程名称中的课程代码匹配（如 TC3000J, ECE2160 等）
        if course_name:
            # 提取课程代码部分（如 TC3000JSP2026-1 -> TC3000）
            import re
            code_match = re.match(r'([A-Z]+\d+)', course_name)
            if code_match:
                course_code = code_match.group(1)
                for d in COURSE_ROOT.iterdir():
                    if d.is_dir() and course_code.lower() in d.name.lower():
                        return d
        # 按目录名包含 course_name 匹配
        if course_name:
            for d in COURSE_ROOT.iterdir():
                if d.is_dir() and course_name.lower() in d.name.lower():
                    return d
    # 回退：用课程 ID 构建路径
    return COURSE_ROOT / f"course_{course_id}"


def call_llm(prompt: str, system_prompt: str = None, temperature: float = 0.7) -> str:
    """调用 LLM API 生成内容"""
    import os
    # 优先级: 环境变量 > OpenClaw 配置 > Canvas config
    api_key = os.environ.get("LLM_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")

    if not api_key:
        try:
            # 尝试从 OpenClaw 配置读取
            openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
            if openclaw_config.exists():
                with open(openclaw_config, "r") as f:
                    oc_cfg = json.load(f)
                providers = oc_cfg.get("models", {}).get("providers", {})
                if "qwenProvider" in providers:
                    api_key = providers["qwenProvider"].get("apiKey", "")
        except Exception:
            pass

    if not api_key:
        # 尝试从 canvas config 的 models 部分读取
        cfg = load_config()
        models_cfg = cfg.get("models") or {}
        providers = models_cfg.get("providers") or {}
        if "qwenProvider" in providers:
            api_key = providers["qwenProvider"].get("apiKey", "")

    if not api_key:
        raise RuntimeError(
            "未找到 LLM API Key。请以下列方式之一配置:\n"
            "1. 环境变量 LLM_API_KEY\n"
            "2. OpenClaw 配置 (openclaw.json) 中的 models.providers.qwenProvider.apiKey\n"
            "3. Canvas config/config.yaml 中的 models.providers.qwenProvider.apiKey"
        )

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 32000,
    }

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def detect_assignment_type(description: str, attachments: list = None) -> str:
    """
    检测作业类型
    返回: "latex_essay" | "coding" | "mixed" | "unknown"
    """
    desc_lower = description.lower() if description else ""

    # 编程相关关键词
    code_keywords = ["code", "program", "script", "python", "java", "c++", "implement",
                     "function", "algorithm", "debug", "github", "repository"]
    # LaTeX/论文相关关键词
    essay_keywords = ["essay", "report", "paper", "write", "analysis", "discuss",
                      "reflect", "summary", "review", "literature", "argument"]

    has_code = any(k in desc_lower for k in code_keywords)
    has_essay = any(k in desc_lower for k in essay_keywords)

    if has_code and has_essay:
        return "mixed"
    elif has_code:
        return "coding"
    elif has_essay:
        return "latex_essay"
    else:
        # 默认用 LaTeX
        return "latex_essay"


def generate_latex_content(
    assignment: dict[str, Any],
    knowledge: list[dict[str, str]],
    course_name: str,
) -> str:
    """生成 LaTeX 作业内容"""
    title = assignment.get("name", "Assignment")
    description = assignment.get("description", "")
    points = assignment.get("points_possible")
    due = assignment.get("due_at")

    # 构建知识库上下文
    kb_context = ""
    for i, kb in enumerate(knowledge[:10]):  # 最多 10 个文件
        kb_context += f"\n--- 参考文件 {i+1}: {kb['file']} ---\n"
        kb_context += kb["content"][:5000] + "\n"

    system_prompt = """你是一个学术助手，擅长根据课程资料和作业要求撰写高质量的学术作业。
请使用英文撰写，严格遵循作业要求。
输出格式：直接输出完整的 LaTeX 文档代码（从 \\documentclass 到 \\end{document}），不要有其他解释。
"""

    prompt = f"""请根据以下作业要求和课程参考资料，完成一份高质量的 LaTeX 作业。

## 作业信息
- 标题：{title}
- 课程：{course_name}
{f"- 分数：{points}" if points else ""}
{f"- 截止：{due}" if due else ""}

## 作业要求
{description if description else "(无详细描述)"}

## 课程参考资料
{kb_context if kb_context else "(无参考资料)"}

## 要求
1. 直接输出完整的 LaTeX 文档，包含：
   - \\documentclass{{article}}
   - 必要的 package（amsmath, amssymb, geometry 等）
   - title, author, date
   - \\begin{{document}} ... \\end{{document}}
2. 使用英文撰写
3. 严格遵循作业要求中的所有评分标准
4. 如有数学公式，使用 proper LaTeX 语法
5. 如有引用，使用 \\cite 格式
6. 输出中不要有任何解释文字，只输出 LaTeX 代码
"""

    return call_llm(prompt, system_prompt)


def generate_coding_content(
    assignment: dict[str, Any],
    knowledge: list[dict[str, str]],
    course_name: str,
) -> str:
    """生成编程作业内容"""
    title = assignment.get("name", "Assignment")
    description = assignment.get("description", "")

    kb_context = ""
    for i, kb in enumerate(knowledge[:10]):
        kb_context += f"\n--- 参考文件 {i+1}: {kb['file']} ---\n"
        kb_context += kb["content"][:5000] + "\n"

    system_prompt = """你是一个编程助手，擅长根据课程资料和作业要求编写高质量的代码。
请直接输出代码，不要有多余解释。
"""

    prompt = f"""请根据以下编程作业要求和课程参考资料，完成代码。

## 作业信息
- 标题：{title}
- 课程：{course_name}

## 作业要求
{description if description else "(无详细描述)"}

## 课程参考资料
{kb_context if kb_context else "(无参考资料)"}

## 要求
1. 根据作业要求编写完整可运行的代码
2. 代码要有清晰的注释
3. 遵循最佳实践和代码规范
4. 如有多个文件，请在每个文件开头注明文件名（用注释包裹）
5. 输出中只输出代码，不要有解释文字
"""

    return call_llm(prompt, system_prompt)


def compile_latex(latex_content: str, output_dir: Path) -> tuple[bool, str, str]:
    """
    编译 LaTeX 生成 PDF
    返回: (success, pdf_path, log_or_error)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / "assignment.tex"
        tex_path.write_text(latex_content, encoding="utf-8")

        # 使用 xelatex 编译
        try:
            result = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "assignment.tex"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=120,
            )

            pdf_path = Path(tmpdir) / "assignment.pdf"
            if pdf_path.exists():
                # 复制到输出目录
                target_pdf = output_dir / "assignment.pdf"
                target_tex = output_dir / "assignment.tex"
                shutil.copy2(pdf_path, target_pdf)
                shutil.copy2(tex_path, target_tex)
                return True, str(target_pdf), ""
            else:
                return False, "", result.stdout[-2000:]
        except FileNotFoundError:
            # xelatex 不存在，尝试安装
            return False, "", "xelatex 未安装"
        except subprocess.TimeoutExpired:
            return False, "", "编译超时"


def parse_code_files_from_response(response: str) -> list[tuple[str, str]]:
    """
    从 LLM 响应中解析多个代码文件
    返回: [(filename, content), ...]
    """
    import re

    files = []
    # 匹配 ```language\n...``` 代码块
    pattern = r"```(\w*)\n(.*?)```"
    matches = re.findall(pattern, response, re.DOTALL)

    if not matches:
        # 没有代码块，当作单个文件
        files.append(("output.py", response))
        return files

    for i, (lang, content) in enumerate(matches):
        lang = lang.lower() if lang else "txt"
        ext_map = {"python": "py", "java": "java", "cpp": "cpp", "c": "c",
                   "javascript": "js", "typescript": "ts", "html": "html",
                   "css": "css", "sql": "sql", "bash": "sh", "shell": "sh"}
        ext = ext_map.get(lang, lang)

        # 尝试从内容中提取文件名注释
        name_pattern = r"#\s*filename:\s*(.+)|//\s*filename:\s*(.+)|/\*\s*filename:\s*(.+)\s*\*/"
        name_match = re.search(name_pattern, content[:200])
        if name_match:
            filename = name_match.group(1) or name_match.group(2) or name_match.group(3)
            filename = filename.strip()
        else:
            filename = f"output_{i+1}.{ext}"

        files.append((filename, content))

    return files


def package_files(file_paths: list[str], output_dir: Path) -> str:
    """打包文件为 ZIP"""
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "submission.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            p = Path(fp)
            if p.exists():
                zf.write(p, p.name)

    return str(zip_path)


def send_wechat_message(content: str, target: str, account_id: str) -> bool:
    """发送微信消息"""
    try:
        cmd = [
            "openclaw", "message", "send",
            "--channel", "openclaw-weixin",
            "--target", target,
            "--account", account_id,
            "--message", content,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"[通知] 发送错误: {e}", file=sys.stderr)
        return False


def send_wechat_file(file_path: str, target: str, account_id: str, caption: str = "") -> bool:
    """发送微信文件/图片"""
    try:
        cmd = [
            "openclaw", "message", "send",
            "--channel", "openclaw-weixin",
            "--target", target,
            "--account", account_id,
            "--message", caption,
            "--file", file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.returncode == 0
    except Exception as e:
        print(f"[文件] 发送错误: {e}", file=sys.stderr)
        return False


def check_new_assignments(dry_run: bool = False) -> list[dict[str, Any]]:
    """
    检查新作业
    返回: 新作业列表
    """
    cfg = load_config()
    canvas = cfg.get("canvas") or {}
    base_url = str(canvas.get("base_url", "")).rstrip("/")
    token = str(canvas.get("access_token", "")).strip()

    if not base_url or not token:
        print("[错误] 请配置 canvas.base_url 和 canvas.access_token", file=sys.stderr)
        return []

    state = AssignmentState()
    headers = canvas_auth_headers(token)

    with httpx.Client(timeout=60.0) as client:
        course_map = get_course_map(client, base_url, headers)
        course_ids = list(course_map.keys())

        assignments = fetch_all_assignments(client, base_url, headers, course_ids)

        # 过滤：只保留未提交的、未过期且在未来 30 天内的作业
        now = datetime.now(timezone.utc)
        new_assignments = []
        for a in assignments:
            aid = a.get("id")
            if not aid or state.exists(aid):
                continue

            due_at = a.get("due_at")

            # 检查是否已提交
            submission = a.get("submission")
            if submission and submission.get("workflow_state") == "submitted":
                state.add({
                    "id": aid,
                    "course_id": a.get("_course_id"),
                    "course_name": course_map.get(a.get("_course_id"), ""),
                    "title": a.get("name", ""),
                    "description": a.get("description", ""),
                    "due_at": due_at,
                    "status": "completed",
                    "completed_at": datetime.now().isoformat(),
                })
                continue

            # 跳过已过期的作业（标记为已完成）
            if due_at:
                try:
                    due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                    if due_dt < now:
                        state.add({
                            "id": aid,
                            "course_id": a.get("_course_id"),
                            "course_name": course_map.get(a.get("_course_id"), ""),
                            "title": a.get("name", ""),
                            "description": a.get("description", ""),
                            "due_at": due_at,
                            "status": "completed",
                            "completed_at": datetime.now().isoformat(),
                        })
                        continue
                    # 跳过超过 30 天的作业
                    if (due_dt - now).days > 30:
                        state.add({
                            "id": aid,
                            "course_id": a.get("_course_id"),
                            "course_name": course_map.get(a.get("_course_id"), ""),
                            "title": a.get("name", ""),
                            "description": a.get("description", ""),
                            "due_at": due_at,
                            "status": "completed",
                            "completed_at": datetime.now().isoformat(),
                        })
                        continue
                except Exception:
                    pass

            # 未提交的新作业（未过期）
            a["course_name"] = course_map.get(a.get("_course_id"), "")
            new_assignments.append(a)

            # 添加到状态
            state.add({
                "id": aid,
                "course_id": a.get("_course_id"),
                "course_name": a["course_name"],
                "title": a.get("name", ""),
                "description": a.get("description", ""),
                "due_at": due_at,
            })

    return new_assignments


def format_assignment_notification(assignment: dict[str, Any]) -> str:
    """格式化作业通知"""
    title = assignment.get("name", "未命名")
    course = assignment.get("course_name", "")
    due = assignment.get("due_at")
    points = assignment.get("points_possible")

    lines = []
    lines.append("📦 发现新作业")
    lines.append("")
    lines.append(f"**📝 {title}**")
    lines.append(f"▸ 课程：{course}")
    if due:
        due_str = due[:16].replace("T", " ")
        lines.append(f"▸ 截止：{due_str}")
    if points is not None:
        lines.append(f"▸ 分数：{points}")
    lines.append("")
    lines.append("回复 **1** 自动完成此作业")

    return "\n".join(lines)


def solve_assignment(assignment_id: int) -> dict[str, Any]:
    """
    完成指定作业
    返回: 结果信息
    """
    cfg = load_config()
    canvas = cfg.get("canvas") or {}
    base_url = str(canvas.get("base_url", "")).rstrip("/")
    token = str(canvas.get("access_token", "")).strip()
    headers = canvas_auth_headers(token)

    state = AssignmentState()
    record = state.get(assignment_id)
    if not record:
        return {"success": False, "error": "作业记录不存在"}

    state.mark_running(assignment_id)

    try:
        with httpx.Client(timeout=120.0) as client:
            # 1. 获取作业详情
            assignment = fetch_assignment_details(
                client, base_url, headers,
                record["course_id"], assignment_id
            )
            if not assignment:
                state.mark_failed(assignment_id, "无法获取作业详情")
                return {"success": False, "error": "无法获取作业详情"}

            course_name = record.get("course_name", "")
            course_dir = get_course_dir(record["course_id"], course_name)

            # 2. 确保课程文件已下载
            print(f"[solver] 检查课程文件: {course_dir}")
            ensure_course_files(record["course_id"], base_url, token, course_dir)

            # 3. 构建知识库
            print(f"[solver] 构建知识库...")
            knowledge = build_course_knowledge(course_dir)
            print(f"[solver] 知识库: {len(knowledge)} 个文件")

            # 4. 检测作业类型
            description = assignment.get("description", "")
            assignment_type = detect_assignment_type(description)
            print(f"[solver] 作业类型: {assignment_type}")

            # 5. 生成内容
            output_dir = OUTPUT_DIR / str(assignment_id)
            output_files = []

            if assignment_type == "latex_essay":
                print(f"[solver] 生成 LaTeX 内容...")
                latex_content = generate_latex_content(assignment, knowledge, course_name)

                print(f"[solver] 编译 LaTeX...")
                success, pdf_path, log = compile_latex(latex_content, output_dir)
                if success:
                    tex_path = str(output_dir / "assignment.tex")
                    output_files = [pdf_path, tex_path]
                else:
                    # 编译失败，只保存 tex
                    tex_path = str(output_dir / "assignment.tex")
                    Path(tex_path).write_text(latex_content, encoding="utf-8")
                    output_files = [tex_path]
                    print(f"[solver] LaTeX 编译失败，仅保存 tex 文件")

            elif assignment_type == "coding":
                print(f"[solver] 生成代码...")
                code_response = generate_coding_content(assignment, knowledge, course_name)

                # 解析代码文件
                code_files = parse_code_files_from_response(code_response)
                for filename, content in code_files:
                    fp = output_dir / filename
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(content, encoding="utf-8")
                    output_files.append(str(fp))

            elif assignment_type == "mixed":
                print(f"[solver] 混合作业类型...")
                # 先生成 LaTeX
                latex_content = generate_latex_content(assignment, knowledge, course_name)
                success, pdf_path, log = compile_latex(latex_content, output_dir)
                if success:
                    output_files.append(pdf_path)
                tex_path = str(output_dir / "assignment.tex")
                Path(tex_path).write_text(latex_content, encoding="utf-8")
                output_files.append(tex_path)

                # 再生成代码（如果需要）
                code_response = generate_coding_content(assignment, knowledge, course_name)
                code_files = parse_code_files_from_response(code_response)
                for filename, content in code_files:
                    fp = output_dir / filename
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(content, encoding="utf-8")
                    output_files.append(str(fp))

            else:
                # 未知类型，默认 LaTeX
                print(f"[solver] 未知类型，默认 LaTeX...")
                latex_content = generate_latex_content(assignment, knowledge, course_name)
                success, pdf_path, log = compile_latex(latex_content, output_dir)
                if success:
                    output_files.append(pdf_path)
                tex_path = str(output_dir / "assignment.tex")
                Path(tex_path).write_text(latex_content, encoding="utf-8")
                output_files.append(tex_path)

            # 6. 打包（如果有多个文件）
            final_files = output_files
            if len(output_files) > 1:
                zip_path = package_files(output_files, output_dir)
                final_files = [zip_path] + output_files

            # 7. 发送给用户
            notify = cfg.get("notify") or {}
            wechat = notify.get("wechat") or {}
            target = wechat.get("target")
            account_id = wechat.get("account_id")

            if target and account_id:
                # 发送通知
                msg_lines = [
                    f"✅ 作业完成：{assignment.get('name', '')}",
                    f"▸ 课程：{course_name}",
                    f"▸ 类型：{assignment_type}",
                    f"▸ 文件：{len(output_files)} 个",
                    "",
                    "文件已发送，请审阅。",
                ]
                send_wechat_message("\n".join(msg_lines), target, account_id)

                # 发送文件
                for fp in final_files[:5]:  # 最多 5 个文件
                    if Path(fp).exists():
                        caption = f"📄 {Path(fp).name}"
                        send_wechat_file(fp, target, account_id, caption)
            else:
                print(f"[solver] 输出目录: {output_dir}")
                print(f"[solver] 输出文件: {output_files}")

            # 8. 更新状态
            state.mark_completed(assignment_id, output_files)

            return {
                "success": True,
                "assignment_id": assignment_id,
                "assignment_type": assignment_type,
                "output_files": output_files,
                "output_dir": str(output_dir),
            }

    except Exception as e:
        state.mark_failed(assignment_id, str(e))
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Assignment Solver")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--check", action="store_true", help="检查新作业")
    group.add_argument("--solve", type=int, metavar="ID", help="完成指定作业")
    group.add_argument("--list-pending", action="store_true", help="列出待确认作业")
    parser.add_argument("--dry-run", action="store_true", help="检查新作业（不通知）")

    args = parser.parse_args()

    if args.check:
        new_assignments = check_new_assignments(dry_run=args.dry_run)
        if new_assignments:
            print(f"发现 {len(new_assignments)} 个新作业：")
            for a in new_assignments:
                print(format_assignment_notification(a))
                print()
        else:
            print("没有新作业")

    elif args.dry_run:
        new_assignments = check_new_assignments(dry_run=True)
        if new_assignments:
            print(f"[dry-run] 发现 {len(new_assignments)} 个新作业：")
            for a in new_assignments:
                print(format_assignment_notification(a))
                print()
        else:
            print("[dry-run] 没有新作业")

    elif args.solve:
        result = solve_assignment(args.solve)
        if result["success"]:
            print(f"✅ 作业 {args.solve} 完成")
            print(f"   类型: {result['assignment_type']}")
            print(f"   文件: {result['output_files']}")
            print(f"   目录: {result['output_dir']}")
        else:
            print(f"❌ 作业 {args.solve} 失败: {result['error']}")

    elif args.list_pending:
        state = AssignmentState()
        pending = state.list_pending()
        if pending:
            print("待确认作业：")
            for a in pending:
                print(f"  - [{a['id']}] {a['title']} ({a['course_name']})")
        else:
            print("没有待确认作业")


if __name__ == "__main__":
    main()
