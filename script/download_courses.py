"""
使用 Canvas API 下载当前用户 active 注册课程的文件。
-init：列出课程并确认后下载；匹配 folder_only 规则的课程仅创建目录。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Union
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from load_settings import load_config

INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_path_segment(name: str, fallback: str = "untitled") -> str:
    s = INVALID_WIN_CHARS.sub("_", (name or "").strip())
    s = s.rstrip(" .")
    return s or fallback


def parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section and "rel='next'" not in section:
            continue
        m = re.search(r"<([^>]+)>", section)
        if m:
            return m.group(1)
    return None


QueryParams = Union[Mapping[str, Any], list[tuple[str, Any]], None]


def paginate_get_list(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    static_params: QueryParams = None,
) -> Iterable[dict[str, Any]]:
    next_url: str | None = url
    params: Any = None
    if isinstance(static_params, list):
        params = list(static_params)
    elif static_params:
        params = dict(static_params)
    while next_url:
        resp = client.get(next_url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError(f"期望列表响应：{next_url!r}")
        for item in data:
            if isinstance(item, dict):
                yield item
        link = resp.headers.get("Link")
        next_url = parse_next_link(link)
        params = None


def canvas_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token.strip()}"}


def active_courses_rows_from_courses_api(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    enrollment_states: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    与网页「课程」列表一致：List your courses。
    部分学校 enrollments+include=course 不返回嵌套 course，此处更可靠。
    """
    url = f"{base_url}/api/v1/courses"
    params: list[tuple[str, Any]] = [
        ("include[]", "sections"),
        ("include[]", "term"),
        ("per_page", 100),
    ]
    for s in enrollment_states:
        params.append(("enrollment_state[]", s))
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for course in paginate_get_list(client, url, headers, params):
        if not isinstance(course, dict) or course.get("id") is None:
            continue
        rows.append(({}, course))
    return rows


def enrollment_active_course_rows(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    enrollment_states: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """后备：enrollments 接口；仅保留带嵌套 course 对象的条目。"""
    url = f"{base_url}/api/v1/users/self/enrollments"
    params: list[tuple[str, Any]] = [
        ("include[]", "course"),
        ("include[]", "term"),
        ("per_page", 100),
    ]
    # enrollments 的 state[] 与 courses 的 enrollment_state[] 取值略有不同
    enroll_map = {"invited_or_pending": "invited"}
    for s in enrollment_states:
        params.append(("state[]", enroll_map.get(s, s)))
    allowed = {s.lower() for s in enrollment_states}
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for enr in paginate_get_list(client, url, headers, params):
        es = (enr.get("enrollment_state") or "").lower()
        if es and es not in allowed:
            continue
        course = enr.get("course")
        if not isinstance(course, dict):
            continue
        if course.get("id") is None:
            continue
        rows.append((enr, course))
    return rows


def collect_course_rows(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    enrollment_states: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = active_courses_rows_from_courses_api(client, base_url, headers, enrollment_states)
    if not rows:
        rows = enrollment_active_course_rows(client, base_url, headers, enrollment_states)
    return rows


def term_label_blob(enrollment: dict[str, Any], course: dict[str, Any]) -> str:
    """拼接学期展示名与 SIS ID 等，供子串匹配。"""
    term: dict[str, Any] | None = None
    t0 = course.get("term")
    if isinstance(t0, dict):
        term = t0
    elif isinstance(enrollment.get("term"), dict):
        term = enrollment["term"]
    if not term:
        return ""
    parts: list[str] = []
    for k in ("name", "sis_term_id", "workflow_state"):
        v = term.get(k)
        if v is not None and str(v).strip():
            parts.append(str(v).strip())
    tid = term.get("id")
    if tid is not None:
        parts.append(str(tid))
    return " ".join(parts)


def filter_rows_by_term_substrings(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    substrings: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """仅保留学期标签文本中包含任一则子串的课程；无学期信息的不保留。"""
    if not substrings:
        return rows
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for enr, course in rows:
        blob = term_label_blob(enr, course).lower()
        if not blob.strip():
            continue
        if any(s.lower() in blob for s in substrings if s):
            out.append((enr, course))
    return out


def dedupe_courses(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[int, tuple[list[dict[str, Any]], dict[str, Any]]]:
    """course_id -> (该课所有 enrollments, 代表 course 对象)。"""
    by_id: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for enr, course in rows:
        cid = int(course["id"])
        by_id.setdefault(cid, []).append((enr, course))
    out: dict[int, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    for cid, lst in by_id.items():
        enrs = [e for e, _ in lst]
        rep_course = lst[-1][1]
        out[cid] = (enrs, rep_course)
    return out


def text_matches_folder_only(text: str, patterns: list[str]) -> bool:
    t = (text or "").lower()
    for p in patterns:
        if p and p.lower() in t:
            return True
    return False


def course_is_folder_only(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    course_id: int,
    enrollments: list[dict[str, Any]],
    course: dict[str, Any],
    patterns: list[str],
) -> bool:
    if not patterns:
        return False
    blobs = [
        course.get("name") or "",
        course.get("course_code") or "",
        course.get("friendly_name") or "",
    ]
    for enr in enrollments:
        blobs.append(enr.get("role") or "")
        blobs.append(enr.get("type") or "")
    if text_matches_folder_only(" ".join(blobs), patterns):
        return True

    sections = course.get("sections")
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec_blob = " ".join(
                str(sec.get(k) or "")
                for k in ("name", "sis_section_id", "integration_id")
            )
            if text_matches_folder_only(sec_blob, patterns):
                return True
        return False

    # 列表未带 sections 时再请求详情；部分学校对学生令牌禁止 GET /courses/:id，会 401
    try:
        detail = client.get(
            f"{base_url}/api/v1/courses/{course_id}",
            headers=headers,
            params={"include[]": "sections"},
        )
        detail.raise_for_status()
        body = detail.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            return False
        raise
    for sec in body.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sec_blob = " ".join(
            str(sec.get(k) or "")
            for k in ("name", "sis_section_id", "integration_id")
        )
        if text_matches_folder_only(sec_blob, patterns):
            return True
    return False


def folder_full_name_cached(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    cache: dict[int, str],
    folder_id: int,
) -> str:
    if folder_id in cache:
        return cache[folder_id]
    r = client.get(f"{base_url}/api/v1/folders/{folder_id}", headers=headers)
    r.raise_for_status()
    data = r.json()
    full = str(data.get("full_name") or data.get("name") or "")
    cache[folder_id] = full
    return full


def relative_dir_from_folder_full_name(full_name: str) -> Path:
    """把 Canvas full_name 转成相对路径；去掉常见根前缀。"""
    parts = [p for p in full_name.replace("\\", "/").split("/") if p and p != "."]
    drop_prefixes = ("course files", "files", "course_files")
    while parts and parts[0].lower() in drop_prefixes:
        parts.pop(0)
    return Path(*parts) if parts else Path()


def strip_access_token_from_url(url: str) -> str:
    """避免在日志里打印带 access_token 的 URL。"""
    try:
        p = urlparse(url)
        q = parse_qs(p.query, keep_blank_values=True)
        q = {k: v for k, v in q.items() if k.lower() != "access_token"}
        pairs: list[tuple[str, str]] = []
        for k, vs in q.items():
            for v in vs:
                pairs.append((k, v))
        new_query = urlencode(pairs)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))
    except Exception:
        return url


def download_course_files(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    course_id: int,
    course_dir: Path,
) -> tuple[int, int]:
    """返回 (成功数, 跳过/失败数)。"""
    url = f"{base_url}/api/v1/courses/{course_id}/files"
    params: dict[str, Any] = {"per_page": 100}
    folder_cache: dict[int, str] = {}
    ok = 0
    bad = 0
    for f in paginate_get_list(client, url, headers, params):
        display = f.get("display_name") or f.get("filename") or f"file_{f.get('id')}"
        fid = f.get("folder_id")
        rel = Path()
        if fid is not None:
            full = folder_full_name_cached(client, base_url, headers, folder_cache, int(fid))
            rel = relative_dir_from_folder_full_name(full)
        target_dir = course_dir / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / sanitize_path_segment(str(display), fallback=f"file_{f.get('id')}")
        if target_path.is_file():
            stem, suf = target_path.stem, target_path.suffix
            target_path = target_dir / sanitize_path_segment(f"{stem}_{f.get('id')}{suf}", fallback=f"file_{f.get('id')}")

        file_url = f.get("url")
        if not file_url:
            bad += 1
            continue
        try:
            with client.stream("GET", str(file_url), headers=headers, follow_redirects=True, timeout=120.0) as resp:
                resp.raise_for_status()
                with target_path.open("wb") as out:
                    for chunk in resp.iter_bytes():
                        out.write(chunk)
            ok += 1
        except httpx.HTTPError:
            bad += 1
            print(f"  跳过文件：{display} ({strip_access_token_from_url(str(file_url))})", file=sys.stderr)
    return ok, bad


def unique_course_dir(root: Path, course_name: str, course_id: int) -> Path:
    base = sanitize_path_segment(course_name, fallback=f"course_{course_id}")
    p = root / base
    if not p.exists():
        return p
    p2 = root / sanitize_path_segment(f"{course_name} [{course_id}]", fallback=f"course_{course_id}")
    return p2


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Canvas 课程文件（active 注册）。")
    parser.add_argument(
        "-init",
        action="store_true",
        help="检索课程并确认后下载到 config.download.root",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="跳过交互确认（等同输入 yes），适合脚本/自动化测试",
    )
    args = parser.parse_args()
    if not args.init:
        parser.error("请使用：python script/download_courses.py -init")

    cfg = load_config()
    canvas = cfg.get("canvas") or {}
    base_url = str(canvas.get("base_url", "")).rstrip("/")
    token = str(canvas.get("access_token", "")).strip()
    if not base_url or not token:
        print("请在 config/config.yaml 填写 canvas.base_url 与 canvas.access_token。", file=sys.stderr)
        sys.exit(1)

    dl = cfg.get("download") or {}
    root = Path(str(dl.get("root") or "")).expanduser()
    if not str(dl.get("root", "")).strip():
        print("请在 config/config.yaml 的 download.root 填写本地下载目录。", file=sys.stderr)
        sys.exit(1)

    raw_patterns = dl.get("folder_only_if_contains")
    if raw_patterns is None:
        patterns = ["Undergraduate Students"]
    elif isinstance(raw_patterns, list):
        patterns = [str(x) for x in raw_patterns if str(x).strip()]
    else:
        print("download.folder_only_if_contains 应为字符串列表。", file=sys.stderr)
        sys.exit(1)

    raw_states = dl.get("enrollment_states")
    if raw_states is None:
        enrollment_states = ["active"]
    elif isinstance(raw_states, list):
        enrollment_states = [str(x).strip() for x in raw_states if str(x).strip()]
    else:
        print("download.enrollment_states 应为字符串列表（如 active、invited）。", file=sys.stderr)
        sys.exit(1)
    if not enrollment_states:
        enrollment_states = ["active"]

    raw_tf = dl.get("term_filter")
    term_substrings: list[str] = []
    if raw_tf is not None:
        if not isinstance(raw_tf, dict):
            print("download.term_filter 应为对象，例如 term_filter: { name_substrings: [...] }。", file=sys.stderr)
            sys.exit(1)
        raw_subs = raw_tf.get("name_substrings")
        if raw_subs is None:
            term_substrings = []
        elif isinstance(raw_subs, list):
            term_substrings = [str(x).strip() for x in raw_subs if str(x).strip()]
        else:
            print("download.term_filter.name_substrings 应为字符串列表。", file=sys.stderr)
            sys.exit(1)

    headers = canvas_auth_headers(token)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        rows = collect_course_rows(client, base_url, headers, enrollment_states)
        before_term = len(rows)
        rows = filter_rows_by_term_substrings(rows, term_substrings)
        grouped = dedupe_courses(rows)

        plans: list[tuple[int, dict[str, Any], list[dict[str, Any]], bool, Path]] = []
        for cid, (enrs, course) in sorted(grouped.items(), key=lambda x: (str(x[1][1].get("name") or ""), x[0])):
            name = str(course.get("name") or course.get("course_code") or f"course_{cid}")
            folder_only = course_is_folder_only(client, base_url, headers, cid, enrs, course, patterns)
            course_dir = unique_course_dir(root, name, cid)
            plans.append((cid, course, enrs, folder_only, course_dir))

        print(f"检索到的课程（enrollment_states={enrollment_states}）：")
        if term_substrings:
            print(
                f"学期标签过滤 term_filter.name_substrings={term_substrings!r}："
                f"{len(plans)} 门（过滤前注册行 {before_term} 条）"
            )
        if not plans:
            print(
                "（无）可检查：1) download.enrollment_states 2) download.term_filter.name_substrings 是否与 "
                "Canvas 中学期名称一致（可在下列课程卡片上核对学期文案）。",
                file=sys.stderr,
            )
        for cid, course, enrs, folder_only, course_dir in plans:
            flag = " [仅创建目录，不下载文件]" if folder_only else ""
            tlabel = term_label_blob(enrs[0] if enrs else {}, course) or "（无）"
            print(f"- {course.get('name') or course.get('course_code')} (id={cid})  学期: {tlabel}{flag}")
            print(f"  -> {course_dir}{flag}")

        if args.yes:
            ans = "yes"
        else:
            ans = input("是否开始执行？输入 yes 继续，其它退出： ").strip().lower()
        if ans != "yes":
            print("已取消。")
            return

        root.mkdir(parents=True, exist_ok=True)
        for cid, course, _enrs, folder_only, course_dir in plans:
            title = course.get("name") or course.get("course_code") or str(cid)
            course_dir.mkdir(parents=True, exist_ok=True)
            if folder_only:
                print(f"仅创建目录：{title}")
                continue
            print(f"下载课程文件：{title}")
            ok, bad = download_course_files(client, base_url, headers, cid, course_dir)
            print(f"  完成：成功 {ok}，失败/跳过 {bad}")


if __name__ == "__main__":
    main()
