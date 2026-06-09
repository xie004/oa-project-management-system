from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.database import (
    DEFAULT_MONITOR_ROOT,
    SYSTEM_ROOT,
    get_connection,
    get_setting,
    init_db,
    now_iso,
    row_to_dict,
    rows_to_dicts,
    set_setting,
)
from app.services.exporter import build_weekly_summary, export_weekly_docx
from app.services.extractors import extract_text
from app.services.scanner import (
    apply_suggestion,
    dismiss_suggestion,
    list_documents,
    list_suggestions,
    scan_all,
    watcher,
)


FRONTEND_DIST = SYSTEM_ROOT / "frontend" / "dist"

app = FastAPI(title="国产化OA集成项目管理系统", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsPayload(BaseModel):
    systemName: str
    defaultMonitorDir: str
    monitorTypes: dict[str, dict[str, Any]]


class TaskPayload(BaseModel):
    title: str
    description: str = ""
    owner: str = ""
    status: str = "not_started"
    priority: str = "medium"
    color_status: str = "green"
    start_date: str = ""
    due_date: str = ""
    progress: int = 0


class GenericPatchPayload(BaseModel):
    values: dict[str, Any]


@app.on_event("startup")
def startup() -> None:
    init_db()
    try:
        scan_all(force=False)
    except Exception:
        pass
    try:
        watcher.start()
    except Exception:
        pass


@app.on_event("shutdown")
def shutdown() -> None:
    watcher.stop()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "name": "国产化OA集成项目管理系统"}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    conn = get_connection()
    try:
        return {
            "systemName": get_setting(conn, "system_name", "国产化OA集成项目管理系统"),
            "defaultMonitorDir": get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT)),
            "monitorTypes": get_setting(conn, "monitor_types", {}),
            "lastScanAt": get_setting(conn, "last_scan_at", ""),
        }
    finally:
        conn.close()


@app.put("/api/settings")
def update_settings(payload: SettingsPayload) -> dict[str, Any]:
    conn = get_connection()
    try:
        set_setting(conn, "system_name", payload.systemName)
        set_setting(conn, "default_monitor_dir", payload.defaultMonitorDir)
        set_setting(conn, "monitor_types", payload.monitorTypes)
        conn.execute(
            "UPDATE project_profile SET system_name = ?, updated_at = ? WHERE id = 1",
            (payload.systemName, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    watcher.start()
    return {"ok": True}


@app.post("/api/scan")
def scan(force: bool = True) -> dict[str, Any]:
    return scan_all(force=force)


def progress_from_tasks(tasks: list[dict[str, Any]]) -> int:
    if not tasks:
        return 0
    total = 0
    for task in tasks:
        if task["status"] == "completed":
            total += 100
        else:
            total += int(task.get("progress") or 0)
    return round(total / len(tasks))


def project_color_status(tasks: list[dict[str, Any]], risks: list[dict[str, Any]]) -> str:
    if any(risk["level"] == "high" and risk["status"] != "closed" for risk in risks):
        return "red"
    if any(task["color_status"] == "red" for task in tasks):
        return "red"
    if any(task["color_status"] == "amber" for task in tasks) or any(
        risk["status"] != "closed" for risk in risks
    ):
        return "amber"
    return "green"


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    conn = get_connection()
    try:
        profile = row_to_dict(conn.execute("SELECT * FROM project_profile WHERE id = 1").fetchone()) or {}
        tasks = rows_to_dicts(
            conn.execute(
                """
                SELECT t.*, d.name AS document_name, d.path AS document_path
                FROM tasks t
                LEFT JOIN documents d ON d.id = t.source_document_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        )
        milestones = rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path
                FROM milestones m
                LEFT JOIN documents d ON d.id = m.source_document_id
                ORDER BY COALESCE(m.planned_date, ''), m.id
                """
            ).fetchall()
        )
        risks = rows_to_dicts(
            conn.execute(
                """
                SELECT r.*, d.name AS document_name, d.path AS document_path
                FROM risks r
                LEFT JOIN documents d ON d.id = r.source_document_id
                ORDER BY r.id DESC
                """
            ).fetchall()
        )
        changes = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path
                FROM change_requests c
                LEFT JOIN documents d ON d.id = c.source_document_id
                ORDER BY c.id DESC
                """
            ).fetchall()
        )
        docs = rows_to_dicts(
            conn.execute(
                "SELECT * FROM documents ORDER BY modified_at DESC, id DESC LIMIT 8"
            ).fetchall()
        )
        suggestions = list_suggestions("pending")[:8]
        weekly_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT w.*, d.name AS document_name
                FROM weekly_reports w
                JOIN documents d ON d.id = w.document_id
                ORDER BY COALESCE(w.period_end, '') DESC, w.updated_at DESC
                LIMIT 8
                """
            ).fetchall()
        )
        counts = {
            "tasks": len(tasks),
            "completedTasks": len([task for task in tasks if task["status"] == "completed"]),
            "milestones": len(milestones),
            "openRisks": len([risk for risk in risks if risk["status"] != "closed"]),
            "changes": len(changes),
            "documents": conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"],
            "pendingSuggestions": conn.execute(
                "SELECT COUNT(*) AS c FROM update_suggestions WHERE status = 'pending'"
            ).fetchone()["c"],
        }
        profile["overall_progress"] = progress_from_tasks(tasks)
        profile["color_status"] = project_color_status(tasks, risks)
        return {
            "profile": profile,
            "counts": counts,
            "tasks": tasks,
            "milestones": milestones,
            "risks": risks,
            "changes": changes,
            "documents": docs,
            "suggestions": suggestions,
            "weeklyReports": weekly_rows,
            "projectGoals": get_setting(conn, "project_goals", []),
            "lastScanAt": get_setting(conn, "last_scan_at", ""),
        }
    finally:
        conn.close()


@app.get("/api/tasks")
def get_tasks() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT t.*, d.name AS document_name, d.path AS document_path
                FROM tasks t
                LEFT JOIN documents d ON d.id = t.source_document_id
                ORDER BY t.id DESC
                """
            ).fetchall()
        )
    finally:
        conn.close()


@app.post("/api/tasks")
def create_task(payload: TaskPayload) -> dict[str, Any]:
    conn = get_connection()
    try:
        now = now_iso()
        conn.execute(
            """
            INSERT INTO tasks(
                title, description, owner, status, priority, color_status, start_date,
                due_date, progress, source, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '手动新增', ?, ?)
            """,
            (
                payload.title,
                payload.description,
                payload.owner,
                payload.status,
                payload.priority,
                payload.color_status,
                payload.start_date,
                payload.due_date,
                payload.progress,
                now,
                now,
            ),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: GenericPatchPayload) -> dict[str, Any]:
    allowed = {
        "title",
        "description",
        "owner",
        "status",
        "priority",
        "color_status",
        "start_date",
        "due_date",
        "progress",
    }
    values = {key: value for key, value in payload.values.items() if key in allowed}
    if not values:
        return {"ok": True}
    conn = get_connection()
    try:
        assignments = ", ".join([f"{key} = ?" for key in values])
        conn.execute(
            f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
            [*values.values(), now_iso(), task_id],
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/milestones")
def get_milestones() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path
                FROM milestones m
                LEFT JOIN documents d ON d.id = m.source_document_id
                ORDER BY COALESCE(m.planned_date, ''), m.id
                """
            ).fetchall()
        )
    finally:
        conn.close()


@app.get("/api/meetings")
def get_meetings() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path
                FROM meetings m
                LEFT JOIN documents d ON d.id = m.document_id
                ORDER BY COALESCE(m.meeting_time, '') DESC, m.id DESC
                """
            ).fetchall()
        )
    finally:
        conn.close()


@app.get("/api/changes")
def get_changes() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path
                FROM change_requests c
                LEFT JOIN documents d ON d.id = c.source_document_id
                ORDER BY c.id DESC
                """
            ).fetchall()
        )
    finally:
        conn.close()


@app.get("/api/risks")
def get_risks() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT r.*, d.name AS document_name, d.path AS document_path
                FROM risks r
                LEFT JOIN documents d ON d.id = r.source_document_id
                ORDER BY r.id DESC
                """
            ).fetchall()
        )
    finally:
        conn.close()


@app.get("/api/documents")
def get_documents() -> list[dict[str, Any]]:
    return list_documents()


@app.get("/api/documents/{document_id}/preview")
def preview_document(document_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        document = row_to_dict(
            conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        )
    finally:
        conn.close()
    if not document:
        raise HTTPException(status_code=404, detail="文件记录不存在。")

    path = Path(document["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="本地文件不存在，可能已移动或删除。")

    result = extract_text(path)
    text = result.text or document.get("summary") or result.error or "该文件暂无法生成正文预览。"
    return {
        "document": document,
        "status": result.status,
        "error": result.error,
        "text": text,
    }


@app.get("/api/suggestions")
def get_suggestions(status: str = "pending") -> list[dict[str, Any]]:
    return list_suggestions(status)


@app.post("/api/suggestions/{suggestion_id}/apply")
def api_apply_suggestion(suggestion_id: int) -> dict[str, Any]:
    result = apply_suggestion(suggestion_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "应用建议失败。"))
    return result


@app.post("/api/suggestions/{suggestion_id}/dismiss")
def api_dismiss_suggestion(suggestion_id: int) -> dict[str, Any]:
    return dismiss_suggestion(suggestion_id)


@app.get("/api/weekly-summary")
def weekly_summary() -> dict[str, Any]:
    return build_weekly_summary()


@app.get("/api/weekly-summary/export")
def weekly_summary_export() -> FileResponse:
    path = export_weekly_docx()
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}", response_class=HTMLResponse, response_model=None)
def serve_frontend(full_path: str):
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        """
        <html>
          <head><title>国产化OA集成项目管理系统</title></head>
          <body>
            <h1>国产化OA集成项目管理系统</h1>
            <p>前端尚未构建，请在 frontend 目录执行 npm install 和 npm run build。</p>
          </body>
        </html>
        """,
        status_code=200,
    )
