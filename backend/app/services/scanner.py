from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.database import (
    DEFAULT_MONITOR_ROOT,
    SYSTEM_ROOT,
    get_connection,
    get_setting,
    now_iso,
    rows_to_dicts,
    set_setting,
)
from app.services.extractors import (
    bullet_lines,
    compact_text,
    extract_text,
    normalize_date,
    parse_meeting,
    parse_weekly_report,
)


SUPPORTED_EXTENSIONS = {".docx", ".doc", ".xlsx", ".xls", ".pdf", ".txt", ".md", ".wpsonline"}
SYSTEM_DIR_NAME = SYSTEM_ROOT.name


def classify_document(path: Path, hint: str = "") -> str:
    name = path.name.lower()
    parent = str(path.parent).lower()
    haystack = f"{name} {parent}"
    if "周报" in haystack:
        return "weekly_report"
    if "会议纪要" in haystack or "周例会" in haystack or "启动会" in haystack:
        return "meeting"
    if "资源" in haystack or "服务器" in haystack:
        return "resource"
    if any(word in haystack for word in ["合同", "招标", "投标", "中标"]):
        return "contract_tender"
    if any(word in haystack for word in ["需求", "变更", "集成", "流程"]):
        return "requirement_change"
    if any(word in haystack for word in ["验收", "上线", "试运行"]):
        return "acceptance_launch"
    if hint:
        return hint
    return "other"


def category_label(category: str) -> str:
    labels = {
        "weekly_report": "项目周报",
        "meeting": "会议纪要",
        "resource": "资源需求",
        "contract_tender": "合同招标",
        "requirement_change": "需求变更",
        "acceptance_launch": "上线验收",
        "other": "其他资料",
    }
    return labels.get(category, category)


def should_skip(path: Path, ignored: list[str]) -> bool:
    parts = {part.lower() for part in path.parts}
    ignored_lower = {item.lower() for item in ignored}
    return SYSTEM_DIR_NAME.lower() in parts or bool(parts & ignored_lower)


def discover_files() -> list[tuple[Path, str]]:
    conn = get_connection()
    try:
        default_dir = Path(get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT)))
        monitor_types = get_setting(conn, "monitor_types", {})
        ignored = get_setting(conn, "ignored_directories", [SYSTEM_DIR_NAME, ".venv", "node_modules", "dist"])
    finally:
        conn.close()

    candidates: dict[str, tuple[Path, str]] = {}
    roots: list[tuple[Path, str]] = [(default_dir, "")]
    for key, config in monitor_types.items():
        if not config.get("enabled", True):
            continue
        for directory in config.get("directories", []):
            if directory:
                roots.append((Path(directory), key))

    for root, hint in roots:
        if not root.exists():
            continue
        if root.is_file():
            paths = [root]
        else:
            paths = [p for p in root.rglob("*") if p.is_file()]
        for path in paths:
            if should_skip(path, ignored):
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            resolved = path.resolve()
            candidates[str(resolved)] = (resolved, hint)
    return sorted(candidates.values(), key=lambda item: str(item[0]))


def upsert_document(
    conn: sqlite3.Connection,
    path: Path,
    category: str,
    status: str,
    summary: str,
    error: str,
) -> int:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO documents(
            path, name, extension, doc_category, size_bytes, modified_at,
            status, summary, error, last_indexed_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            name = excluded.name,
            extension = excluded.extension,
            doc_category = excluded.doc_category,
            size_bytes = excluded.size_bytes,
            modified_at = excluded.modified_at,
            status = excluded.status,
            summary = excluded.summary,
            error = excluded.error,
            last_indexed_at = excluded.last_indexed_at
        """,
        (
            str(path),
            path.name,
            path.suffix.lower(),
            category,
            stat.st_size,
            modified,
            status,
            summary,
            error,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM documents WHERE path = ?", (str(path),)).fetchone()
    return int(row["id"])


def create_suggestion(
    conn: sqlite3.Connection,
    document_id: int,
    suggestion_type: str,
    title: str,
    description: str,
    payload: dict[str, Any],
    confidence: float = 0.72,
) -> None:
    title = compact_text(title, 140)
    description = compact_text(description, 1200)
    if not title:
        return
    exists = conn.execute(
        """
        SELECT id FROM update_suggestions
        WHERE document_id = ? AND suggestion_type = ? AND title = ?
        """,
        (document_id, suggestion_type, title),
    ).fetchone()
    if exists:
        return
    payload = {**payload, "source_document_id": document_id}
    conn.execute(
        """
        INSERT INTO update_suggestions(
            document_id, suggestion_type, title, description, confidence,
            payload_json, status, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            document_id,
            suggestion_type,
            title,
            description,
            confidence,
            json.dumps(payload, ensure_ascii=False),
            now_iso(),
        ),
    )


def save_weekly_report(conn: sqlite3.Connection, document_id: int, text: str, filename: str) -> None:
    parsed = parse_weekly_report(text, filename)
    now = now_iso()
    conn.execute(
        """
        INSERT INTO weekly_reports(
            document_id, period_start, period_end, progress_text, next_plan_text,
            coordination_text, risk_text, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            progress_text = excluded.progress_text,
            next_plan_text = excluded.next_plan_text,
            coordination_text = excluded.coordination_text,
            risk_text = excluded.risk_text,
            updated_at = excluded.updated_at
        """,
        (
            document_id,
            parsed["period_start"],
            parsed["period_end"],
            parsed["progress_text"],
            parsed["next_plan_text"],
            parsed["coordination_text"],
            parsed["risk_text"],
            now,
            now,
        ),
    )

    for line in bullet_lines(parsed["next_plan_text"], 8):
        create_suggestion(
            conn,
            document_id,
            "task",
            line,
            f"从周报“下周工作计划”识别到的任务：{line}",
            {
                "title": line,
                "description": f"来源：{filename}\n{line}",
                "status": "not_started",
                "priority": "medium",
                "color_status": "green",
                "progress": 0,
                "source": "周报自动识别",
            },
            0.76,
        )

    for line in bullet_lines(parsed["progress_text"], 10):
        if any(word in line for word in ["完成", "已完成", "确认", "敲定"]):
            create_suggestion(
                conn,
                document_id,
                "milestone",
                line,
                f"从周报“本周工作进展”识别到的完成/阶段性成果：{line}",
                {
                    "title": line,
                    "description": f"来源：{filename}\n{line}",
                    "status": "completed",
                    "color_status": "green",
                    "actual_date": parsed["period_end"],
                },
                0.7,
            )

    for line in bullet_lines(parsed["risk_text"], 5):
        create_suggestion(
            conn,
            document_id,
            "risk",
            line[:90],
            f"从周报“存在问题或风险”识别到的风险：{line}",
            {
                "title": line[:90],
                "description": line,
                "level": "medium",
                "status": "open",
                "mitigation": "",
            },
            0.82,
        )


def save_meeting(conn: sqlite3.Connection, document_id: int, text: str, filename: str) -> None:
    parsed = parse_meeting(text, filename)
    now = now_iso()
    conn.execute(
        """
        INSERT INTO meetings(
            document_id, title, meeting_time, location, participants, topics,
            decisions, summary, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            title = excluded.title,
            meeting_time = excluded.meeting_time,
            location = excluded.location,
            participants = excluded.participants,
            topics = excluded.topics,
            decisions = excluded.decisions,
            summary = excluded.summary,
            updated_at = excluded.updated_at
        """,
        (
            document_id,
            parsed["title"],
            parsed["meeting_time"],
            parsed["location"],
            parsed["participants"],
            parsed["topics"],
            parsed["decisions"],
            parsed["summary"],
            now,
            now,
        ),
    )

    for line in bullet_lines(parsed["decisions"], 8):
        create_suggestion(
            conn,
            document_id,
            "task",
            line,
            f"从会议决议识别到的待办或阶段事项：{line}",
            {
                "title": line,
                "description": f"会议：{parsed['title']}\n{line}",
                "status": "not_started",
                "priority": "high" if any(word in line for word in ["尽快", "次日", "按期", "上线"]) else "medium",
                "color_status": "amber" if any(word in line for word in ["尽快", "按期", "上线"]) else "green",
                "progress": 0,
                "source": "会议纪要自动识别",
            },
            0.78,
        )

    text_for_changes = f"{parsed['topics']}\n{parsed['decisions']}"
    for line in bullet_lines(text_for_changes, 12):
        if any(word in line for word in ["变更", "调整", "优化", "预算", "报销", "授权", "流程"]):
            create_suggestion(
                conn,
                document_id,
                "change_request",
                line[:90],
                f"从会议内容识别到可能的需求/流程变更：{line}",
                {
                    "title": line[:90],
                    "description": line,
                    "proposer": "",
                    "impact": "待确认",
                    "status": "pending",
                },
                0.68,
            )


def save_resource_suggestions(conn: sqlite3.Connection, document_id: int, text: str, filename: str) -> None:
    if not text.strip():
        return
    create_suggestion(
        conn,
        document_id,
        "milestone",
        "服务器资源需求清单已更新",
        f"检测到资源需求类资料：{filename}。建议确认是否更新服务器资源里程碑。",
        {
            "title": "服务器资源需求清单已更新",
            "description": f"来源：{filename}",
            "status": "in_progress",
            "color_status": "amber",
        },
        0.72,
    )


def index_document(path: Path, hint: str = "", force: bool = False) -> dict[str, Any]:
    path = path.resolve()
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM documents WHERE path = ?", (str(path),)).fetchone()
        if existing and not force:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            if existing["modified_at"] == modified and existing["size_bytes"] == stat.st_size:
                return {"path": str(path), "status": "unchanged"}

        category = classify_document(path, hint)
        result = extract_text(path)
        summary = compact_text(result.text, 500) if result.text else category_label(category)
        document_id = upsert_document(conn, path, category, result.status, summary, result.error)
        if force:
            conn.execute(
                "DELETE FROM update_suggestions WHERE document_id = ? AND status = 'pending'",
                (document_id,),
            )
        if category != "weekly_report":
            conn.execute("DELETE FROM weekly_reports WHERE document_id = ?", (document_id,))
        if category != "meeting":
            conn.execute("DELETE FROM meetings WHERE document_id = ?", (document_id,))

        if result.text:
            if category == "weekly_report":
                save_weekly_report(conn, document_id, result.text, path.name)
            elif category == "meeting":
                save_meeting(conn, document_id, result.text, path.name)
            elif category == "resource":
                save_resource_suggestions(conn, document_id, result.text, path.name)
            elif category == "requirement_change":
                for line in bullet_lines(result.text, 16):
                    if any(word in line for word in ["变更", "优化", "调整", "集成", "需求"]):
                        create_suggestion(
                            conn,
                            document_id,
                            "change_request",
                            line[:90],
                            f"从需求/变更类资料识别到的事项：{line}",
                            {
                                "title": line[:90],
                                "description": line,
                                "proposer": "",
                                "impact": "待确认",
                                "status": "pending",
                            },
                            0.65,
                        )

        conn.commit()
        return {"path": str(path), "status": result.status, "category": category}
    except Exception as exc:
        conn.rollback()
        return {"path": str(path), "status": "error", "error": str(exc)}
    finally:
        conn.close()


def scan_all(force: bool = False) -> dict[str, Any]:
    files = discover_files()
    results = [index_document(path, hint, force) for path, hint in files]
    conn = get_connection()
    try:
        set_setting(conn, "last_scan_at", now_iso())
        conn.commit()
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"total": len(results), "counts": counts, "results": results}


def list_documents() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM documents
            ORDER BY modified_at DESC, id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)
    finally:
        conn.close()


def list_suggestions(status: str = "pending") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        params: tuple[Any, ...] = ()
        where = ""
        if status:
            where = "WHERE s.status = ?"
            params = (status,)
        rows = conn.execute(
            f"""
            SELECT s.*, d.name AS document_name, d.path AS document_path, d.doc_category
            FROM update_suggestions s
            LEFT JOIN documents d ON d.id = s.document_id
            {where}
            ORDER BY s.created_at DESC, s.id DESC
            """,
            params,
        ).fetchall()
        suggestions = rows_to_dicts(rows)
        for item in suggestions:
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except Exception:
                item["payload"] = {}
        return suggestions
    finally:
        conn.close()


def apply_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        suggestion = conn.execute(
            "SELECT * FROM update_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()
        if not suggestion:
            return {"ok": False, "message": "建议不存在。"}
        if suggestion["status"] != "pending":
            return {"ok": False, "message": "该建议不是待确认状态。"}
        payload = json.loads(suggestion["payload_json"])
        now = now_iso()
        suggestion_type = suggestion["suggestion_type"]

        if suggestion_type == "task":
            conn.execute(
                """
                INSERT INTO tasks(
                    title, description, owner, status, priority, color_status,
                    start_date, due_date, progress, source, source_document_id,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("title", suggestion["title"]),
                    payload.get("description", suggestion["description"]),
                    payload.get("owner", ""),
                    payload.get("status", "not_started"),
                    payload.get("priority", "medium"),
                    payload.get("color_status", "green"),
                    payload.get("start_date", ""),
                    payload.get("due_date", ""),
                    int(payload.get("progress", 0) or 0),
                    payload.get("source", "智能建议"),
                    payload.get("source_document_id"),
                    now,
                    now,
                ),
            )
        elif suggestion_type == "risk":
            conn.execute(
                """
                INSERT INTO risks(
                    title, description, level, status, mitigation, source_document_id,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("title", suggestion["title"]),
                    payload.get("description", suggestion["description"]),
                    payload.get("level", "medium"),
                    payload.get("status", "open"),
                    payload.get("mitigation", ""),
                    payload.get("source_document_id"),
                    now,
                    now,
                ),
            )
        elif suggestion_type == "milestone":
            conn.execute(
                """
                INSERT INTO milestones(
                    title, description, planned_date, actual_date, status, color_status,
                    source_document_id, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("title", suggestion["title"]),
                    payload.get("description", suggestion["description"]),
                    payload.get("planned_date", ""),
                    payload.get("actual_date", ""),
                    payload.get("status", "planned"),
                    payload.get("color_status", "green"),
                    payload.get("source_document_id"),
                    now,
                    now,
                ),
            )
        elif suggestion_type == "change_request":
            conn.execute(
                """
                INSERT INTO change_requests(
                    title, description, proposer, impact, status, source_document_id,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("title", suggestion["title"]),
                    payload.get("description", suggestion["description"]),
                    payload.get("proposer", ""),
                    payload.get("impact", "待确认"),
                    payload.get("status", "pending"),
                    payload.get("source_document_id"),
                    now,
                    now,
                ),
            )
        else:
            return {"ok": False, "message": f"暂不支持应用 {suggestion_type} 类型。"}

        conn.execute(
            "UPDATE update_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
            (now, suggestion_id),
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def dismiss_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE update_suggestions SET status = 'dismissed' WHERE id = ?",
            (suggestion_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


class ProjectFileEventHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.callback(event.src_path)


class ScanWatcher:
    def __init__(self) -> None:
        self.observer: Observer | None = None
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def _schedule_scan(self, changed_path: str) -> None:
        if Path(changed_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(4.0, lambda: scan_all(force=False))
            self.timer.daemon = True
            self.timer.start()

    def start(self) -> dict[str, Any]:
        self.stop()
        conn = get_connection()
        try:
            default_dir = Path(get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT)))
            monitor_types = get_setting(conn, "monitor_types", {})
        finally:
            conn.close()

        roots = {str(default_dir)}
        for config in monitor_types.values():
            if not config.get("enabled", True):
                continue
            for directory in config.get("directories", []):
                if directory:
                    roots.add(str(Path(directory)))

        observer = Observer()
        handler = ProjectFileEventHandler(self._schedule_scan)
        watched: list[str] = []
        for root in sorted(roots):
            path = Path(root)
            if path.exists() and path.is_dir():
                observer.schedule(handler, str(path), recursive=True)
                watched.append(str(path))
        observer.daemon = True
        if watched:
            observer.start()
            self.observer = observer
        return {"watched": watched}

    def stop(self) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None


watcher = ScanWatcher()
