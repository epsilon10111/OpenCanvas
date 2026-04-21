#!/usr/bin/env python3
"""
Assignment 状态管理 - 持久化存储，支持重启恢复

状态文件: state/assignments.json
结构:
{
    "assignments": {
        "<assignment_id>": {
            "id": int,
            "course_id": int,
            "course_name": str,
            "title": str,
            "description": str,
            "due_at": str,
            "status": "pending" | "approved" | "running" | "completed" | "failed",
            "created_at": str,
            "notified_at": str,
            "approved_at": str | null,
            "completed_at": str | null,
            "error": str | null,
            "output_files": [str]  // 完成后的文件路径列表
        }
    }
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

STATE_FILE = Path(__file__).parent.parent / "state" / "assignments.json"


class AssignmentState:
    """Assignment 状态管理器"""

    def __init__(self, state_file: Path = None):
        self._file = state_file or STATE_FILE
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self._file.exists():
            try:
                with self._file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"assignments": {}}

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def add(self, assignment: dict[str, Any]) -> dict[str, Any]:
        """添加新 assignment 记录，返回完整记录"""
        aid = str(assignment["id"])
        record = {
            "id": assignment["id"],
            "course_id": assignment.get("course_id"),
            "course_name": assignment.get("course_name", ""),
            "title": assignment.get("title", ""),
            "description": assignment.get("description", ""),
            "due_at": assignment.get("due_at"),
            "points_possible": assignment.get("points_possible"),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "notified_at": datetime.now().isoformat(),
            "approved_at": None,
            "completed_at": None,
            "error": None,
            "output_files": [],
        }
        self._data["assignments"][aid] = record
        self._save()
        return record

    def get(self, assignment_id: int) -> Optional[dict[str, Any]]:
        return self._data["assignments"].get(str(assignment_id))

    def update(self, assignment_id: int, **kwargs) -> Optional[dict[str, Any]]:
        record = self._data["assignments"].get(str(assignment_id))
        if not record:
            return None
        record.update(kwargs)
        self._save()
        return record

    def approve(self, assignment_id: int) -> Optional[dict[str, Any]]:
        return self.update(
            str(assignment_id) if isinstance(assignment_id, str) else assignment_id,
            status="approved",
            approved_at=datetime.now().isoformat(),
        )

    def mark_running(self, assignment_id: int) -> Optional[dict[str, Any]]:
        return self.update(
            str(assignment_id) if isinstance(assignment_id, str) else assignment_id,
            status="running",
        )

    def mark_completed(self, assignment_id: int, output_files: list[str]) -> Optional[dict[str, Any]]:
        return self.update(
            str(assignment_id) if isinstance(assignment_id, str) else assignment_id,
            status="completed",
            completed_at=datetime.now().isoformat(),
            output_files=output_files,
        )

    def mark_failed(self, assignment_id: int, error: str) -> Optional[dict[str, Any]]:
        return self.update(
            str(assignment_id) if isinstance(assignment_id, str) else assignment_id,
            status="failed",
            error=error,
        )

    def list_by_status(self, status: str = None) -> list[dict[str, Any]]:
        items = list(self._data["assignments"].values())
        if status:
            items = [a for a in items if a["status"] == status]
        return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)

    def list_pending(self) -> list[dict[str, Any]]:
        return self.list_by_status("pending")

    def exists(self, assignment_id: int) -> bool:
        return str(assignment_id) in self._data["assignments"]

    def clear_completed(self, days_old: int = 30):
        """清理超过指定天数的已完成记录"""
        cutoff = datetime.now().timestamp() - days_old * 86400
        to_remove = []
        for aid, record in self._data["assignments"].items():
            if record["status"] == "completed" and record.get("completed_at"):
                try:
                    ct = datetime.fromisoformat(record["completed_at"]).timestamp()
                    if ct < cutoff:
                        to_remove.append(aid)
                except Exception:
                    pass
        for aid in to_remove:
            del self._data["assignments"][aid]
        if to_remove:
            self._save()
