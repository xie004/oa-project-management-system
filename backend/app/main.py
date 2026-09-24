from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    admin_from_request,
    authenticate_admin,
    create_session_token,
    current_auth,
    ensure_auth_defaults,
    set_admin_password,
)
from app.database import (
    DEFAULT_MONITOR_ROOT,
    DEFAULT_INTELLIGENT_ANALYSIS,
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
from app.services.ai import answer_question, knowledge_rebuild_status, knowledge_status, list_chat_models, start_knowledge_rebuild_job, test_chat_model
from app.services.authority import (
    apply_authority_suggestion,
    authority_analyze_status,
    dismiss_authority_suggestion,
    list_authority_suggestions,
    list_document_authority,
    start_authority_analyze_job,
    update_document_authority,
)
from app.services.data_quality import (
    apply_all_safe_quality_suggestions,
    apply_selected_quality_suggestions,
    apply_quality_suggestion,
    bootstrap_entities,
    data_quality_status,
    dismiss_quality_suggestion,
    ensure_entity,
    entity_evidence,
    list_quality_suggestions,
    start_data_quality_job,
)
from app.services.ocr import combined_ocr_text, enqueue_document_ocr, ocr_pages, ocr_status, retry_document_ocr, worker
from app.services.scanner import (
    apply_all_suggestions,
    apply_suggestion,
    dismiss_suggestion,
    list_documents,
    list_suggestions,
    scan_all,
    test_structured_extraction,
    watcher,
)
from app.services import observations
from app.services.task_reconcile import (
    promote_task,
    start_task_progress_reconcile,
    task_reconcile_status,
)
from app.services.uploads import save_upload, upload_status, upload_targets
from app.services.wiki import (
    apply_all_wiki_suggestions,
    apply_wiki_suggestion,
    list_wiki_page_history,
    list_wiki_pages,
    list_wiki_suggestions,
    rollback_wiki_page,
    start_current_progress_refresh_job,
    start_wiki_rebuild_job,
    wiki_job_status,
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


TASK_STATUSES = {"not_started", "in_progress", "blocked", "completed", "delayed", "planned", "pending"}
TASK_PRIORITIES = {"low", "medium", "high"}
COLOR_STATUSES = {"green", "amber", "red"}
DELIVERABLE_STATUSES = {"not_started", "draft", "review", "finalized", "submitted"}


def _clean_text(value: Any, field_name: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name}必须是文本")
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"{field_name}不能为空")
    if len(cleaned) > limit:
        raise ValueError(f"{field_name}不能超过{limit}个字符")
    return cleaned


def _normalise_optional_date(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式")
    cleaned = value.strip()
    if not cleaned:
        return ""
    try:
        date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD 格式") from exc
    return cleaned


def _ensure_date_order(start_date: str, due_date: str, start_label: str = "开始日期", due_label: str = "截止日期") -> None:
    if start_date and due_date and start_date > due_date:
        raise ValueError(f"{due_label}不能早于{start_label}")


def _normalise_boolean(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in {0, 1}:
        return value
    raise ValueError(f"{field_name}必须是布尔值")


def _validation_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _normalise_task_patch_values(values: dict[str, Any]) -> dict[str, Any]:
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
        "show_in_gantt",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"不支持更新字段：{', '.join(unknown)}")
    if not values:
        raise ValueError("至少提供一个可更新字段")

    normalized = dict(values)
    if "title" in normalized:
        normalized["title"] = _clean_text(normalized["title"], "任务名称", 200, required=True)
    for key, label, limit in [("description", "任务说明", 10_000), ("owner", "负责人", 120)]:
        if key in normalized:
            normalized[key] = _clean_text(normalized[key], label, limit)
    if "status" in normalized and normalized["status"] not in TASK_STATUSES:
        raise ValueError("任务状态不正确")
    if "priority" in normalized and normalized["priority"] not in TASK_PRIORITIES:
        raise ValueError("任务优先级不正确")
    if "color_status" in normalized and normalized["color_status"] not in COLOR_STATUSES:
        raise ValueError("任务颜色状态不正确")
    for key, label in [("start_date", "开始日期"), ("due_date", "截止日期")]:
        if key in normalized:
            normalized[key] = _normalise_optional_date(normalized[key], label)
    if "progress" in normalized:
        progress = normalized["progress"]
        if isinstance(progress, bool) or not isinstance(progress, int) or not 0 <= progress <= 100:
            raise ValueError("任务进度必须是 0 到 100 的整数")
    if "show_in_gantt" in normalized:
        normalized["show_in_gantt"] = _normalise_boolean(normalized["show_in_gantt"], "甘特图显示")
    return normalized


def _normalise_deliverable_patch_values(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "requirement_source",
        "description",
        "status",
        "owner",
        "planned_date",
        "submitted_date",
        "document_id",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"不支持更新字段：{', '.join(unknown)}")
    if not values:
        raise ValueError("至少提供一个可更新字段")

    normalized = dict(values)
    if "name" in normalized:
        normalized["name"] = _clean_text(normalized["name"], "交付物名称", 200, required=True)
    for key, label, limit in [
        ("requirement_source", "需求来源", 500),
        ("description", "交付物说明", 10_000),
        ("owner", "负责人", 120),
    ]:
        if key in normalized:
            normalized[key] = _clean_text(normalized[key], label, limit)
    if "status" in normalized and normalized["status"] not in DELIVERABLE_STATUSES:
        raise ValueError("交付物状态不正确")
    for key, label in [("planned_date", "计划日期"), ("submitted_date", "提交日期")]:
        if key in normalized:
            normalized[key] = _normalise_optional_date(normalized[key], label)
    if "document_id" in normalized:
        document_id = normalized["document_id"]
        if isinstance(document_id, float):
            raise ValueError("关联文件编号不正确")
        if document_id in (None, ""):
            normalized["document_id"] = None
        elif isinstance(document_id, bool):
            raise ValueError("关联文件编号不正确")
        else:
            try:
                document_id = int(document_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("关联文件编号不正确") from exc
            if document_id <= 0:
                raise ValueError("关联文件编号不正确")
            normalized["document_id"] = document_id
    return normalized


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_operational_error_handler(_: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return JSONResponse(
            status_code=503,
            content={"detail": "数据正在更新，请稍后重试。"},
            headers={"Retry-After": "1"},
        )
    return JSONResponse(status_code=500, content={"detail": "数据库操作失败。"})


class SettingsPayload(BaseModel):
    systemName: str
    defaultMonitorDir: str
    monitorTypes: dict[str, dict[str, Any]]
    intelligentAnalysis: dict[str, Any] = Field(default_factory=dict)


class LoginPayload(BaseModel):
    username: str
    password: str


class PasswordChangePayload(BaseModel):
    currentPassword: str
    newPassword: str


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=10_000)
    owner: str = Field(default="", max_length=120)
    status: Literal["not_started", "in_progress", "blocked", "completed", "delayed", "planned", "pending"] = "not_started"
    priority: Literal["low", "medium", "high"] = "medium"
    color_status: Literal["green", "amber", "red"] = "green"
    start_date: str = ""
    due_date: str = ""
    progress: int = Field(default=0, ge=0, le=100)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _clean_text(value, "任务名称", 200, required=True)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _clean_text(value, "任务说明", 10_000)

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, value: str) -> str:
        return _clean_text(value, "负责人", 120)

    @field_validator("start_date", "due_date")
    @classmethod
    def validate_dates(cls, value: str, info) -> str:
        labels = {"start_date": "开始日期", "due_date": "截止日期"}
        return _normalise_optional_date(value, labels[info.field_name])

    @field_validator("progress", mode="before")
    @classmethod
    def validate_progress(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("任务进度必须是 0 到 100 的整数")
        return value

    @model_validator(mode="after")
    def validate_date_order(self) -> "TaskPayload":
        _ensure_date_order(self.start_date, self.due_date)
        return self


class GenericPatchPayload(BaseModel):
    values: dict[str, Any]


class TaskBulkPatchPayload(BaseModel):
    ids: list[int] = Field(default_factory=list)
    values: dict[str, Any]


class QualityBatchItemPayload(BaseModel):
    id: int = Field(gt=0)
    values: dict[str, Any] = Field(default_factory=dict)


class QualityBatchPayload(BaseModel):
    items: list[QualityBatchItemPayload] = Field(min_length=1, max_length=200)


class QuestionPayload(BaseModel):
    question: str


def merge_intelligent_analysis_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(payload or {})}
    policy = str(config.get("reviewPolicy") or "balanced")
    if policy not in {"strict", "balanced", "automatic"}:
        policy = "balanced"
    config["reviewPolicy"] = policy
    config["enabled"] = bool(config.get("enabled"))
    config["reviewOnly"] = policy == "strict"
    config["allowExternalService"] = bool(config.get("allowExternalService"))
    config["autoApplyLowRisk"] = policy in {"balanced", "automatic"}
    config["rerankerEnabled"] = bool(config.get("rerankerEnabled"))
    for key in ["timeoutSeconds", "maxTextLength"]:
        try:
            config[key] = int(config.get(key) or DEFAULT_INTELLIGENT_ANALYSIS[key])
        except (TypeError, ValueError):
            config[key] = DEFAULT_INTELLIGENT_ANALYSIS[key]
    config["timeoutSeconds"] = max(10, min(300, config["timeoutSeconds"]))
    config["maxTextLength"] = max(1000, min(80000, config["maxTextLength"]))
    return config


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_auth_defaults()
    observations.resume_batches()
    try:
        bootstrap_entities()
    except Exception:
        pass
    try:
        scan_all(force=False)
    except Exception:
        pass
    try:
        watcher.start()
    except Exception:
        pass
    try:
        worker.start()
    except Exception:
        pass


@app.on_event("shutdown")
def shutdown() -> None:
    watcher.stop()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "name": "国产化OA集成项目管理系统"}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    return current_auth(request)


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, response: Response) -> dict[str, Any]:
    if not authenticate_admin(payload.username.strip(), payload.password):
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True, "isAdmin": True, "username": "admin"}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(payload: PasswordChangePayload, _: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    set_admin_password(payload.currentPassword, payload.newPassword)
    return {"ok": True}


@app.get("/api/settings")
def get_settings(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return {
            "systemName": get_setting(conn, "system_name", "国产化OA集成项目管理系统"),
            "defaultMonitorDir": get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT)),
            "monitorTypes": get_setting(conn, "monitor_types", {}),
            "intelligentAnalysis": get_setting(conn, "intelligent_analysis", DEFAULT_INTELLIGENT_ANALYSIS),
            "lastScanAt": get_setting(conn, "last_scan_at", ""),
        }
    finally:
        conn.close()


@app.put("/api/settings")
def update_settings(payload: SettingsPayload, _: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    conn = get_connection()
    try:
        set_setting(conn, "system_name", payload.systemName)
        set_setting(conn, "default_monitor_dir", payload.defaultMonitorDir)
        set_setting(conn, "monitor_types", payload.monitorTypes)
        set_setting(conn, "intelligent_analysis", merge_intelligent_analysis_config(payload.intelligentAnalysis))
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


@app.post("/api/ai/test")
def ai_test(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return test_chat_model()


@app.post("/api/ai/extraction-test")
def ai_extraction_test(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    try:
        return test_structured_extraction()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"结构化抽取测试失败：{exc}") from exc


@app.get("/api/ai/models")
def ai_models(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return list_chat_models()


@app.get("/api/knowledge/status")
def api_knowledge_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return knowledge_status()


@app.post("/api/knowledge/rebuild")
def api_knowledge_rebuild(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_knowledge_rebuild_job()


@app.get("/api/knowledge/rebuild-status")
def api_knowledge_rebuild_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return knowledge_rebuild_status()


@app.get("/api/document-authority")
def api_document_authority(_: dict[str, Any] = Depends(admin_from_request)) -> list[dict[str, Any]]:
    return list_document_authority()


@app.patch("/api/document-authority/{document_id}")
def api_update_document_authority(
    document_id: int,
    payload: GenericPatchPayload,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    result = update_document_authority(document_id, payload.values)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "文档权威更新失败"))
    return result


@app.post("/api/document-authority/analyze")
def api_analyze_document_authority(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_authority_analyze_job()


@app.get("/api/document-authority/analyze-status")
def api_document_authority_analyze_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return authority_analyze_status()


@app.get("/api/document-authority/suggestions")
def api_document_authority_suggestions(
    status: str = "pending",
    _: dict[str, Any] = Depends(admin_from_request),
) -> list[dict[str, Any]]:
    return list_authority_suggestions(status)


@app.post("/api/document-authority/suggestions/{suggestion_id}/apply")
def api_apply_document_authority_suggestion(
    suggestion_id: int,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    result = apply_authority_suggestion(suggestion_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "权威建议应用失败"))
    return result


@app.post("/api/document-authority/suggestions/{suggestion_id}/dismiss")
def api_dismiss_document_authority_suggestion(
    suggestion_id: int,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    return dismiss_authority_suggestion(suggestion_id)


@app.get("/api/ocr/status")
def api_ocr_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return ocr_status()


@app.post("/api/ocr/pause")
def api_ocr_pause(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    worker.pause()
    return {"ok": True}


@app.post("/api/ocr/documents/{document_id}/start")
def api_ocr_start(document_id: int, _: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    conn = get_connection()
    try:
        document = row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
    finally:
        conn.close()
    if not document:
        raise HTTPException(status_code=404, detail="文件记录不存在。")
    path = Path(document["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="本地文件不存在。")
    result = enqueue_document_ocr(document_id, path)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "当前文件不满足 OCR 处理条件。"))
    return result


@app.post("/api/ocr/documents/{document_id}/retry")
def api_ocr_retry(document_id: int, _: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    result = retry_document_ocr(document_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "OCR 重试失败"))
    return result


@app.get("/api/wiki/pages")
def api_wiki_pages() -> list[dict[str, Any]]:
    return list_wiki_pages()


@app.get("/api/wiki/suggestions")
def api_wiki_suggestions(status: str = "pending", _: dict[str, Any] = Depends(admin_from_request)) -> list[dict[str, Any]]:
    return list_wiki_suggestions(status)


@app.post("/api/wiki/rebuild-suggestions")
def api_wiki_rebuild_suggestions(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_wiki_rebuild_job()


@app.post("/api/wiki/current-progress/refresh")
def api_wiki_current_progress_refresh(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_current_progress_refresh_job("manual")


@app.get("/api/wiki/rebuild-status")
def api_wiki_rebuild_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return wiki_job_status()


@app.post("/api/wiki/suggestions/{suggestion_id}/apply")
def api_wiki_apply(suggestion_id: int, _: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    result = apply_wiki_suggestion(suggestion_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "应用 Wiki 建议失败"))
    return result


@app.post("/api/wiki/suggestions/apply-all")
def api_wiki_apply_all(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return apply_all_wiki_suggestions()


@app.get("/api/wiki/pages/{page_key}/history")
def api_wiki_history(page_key: str, _: dict[str, Any] = Depends(admin_from_request)) -> list[dict[str, Any]]:
    return list_wiki_page_history(page_key)


@app.post("/api/wiki/pages/{page_key}/rollback/{suggestion_id}")
def api_wiki_rollback(
    page_key: str,
    suggestion_id: int,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    result = rollback_wiki_page(page_key, suggestion_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "恢复 Wiki 版本失败"))
    return result


@app.post("/api/qa/ask")
def qa_ask(payload: QuestionPayload) -> dict[str, Any]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题。")
    return answer_question(question)


@app.post("/api/data-quality/analyze")
def api_data_quality_analyze(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_data_quality_job()


@app.get("/api/data-quality/status")
def api_data_quality_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return data_quality_status()


@app.get("/api/data-quality/suggestions")
def api_data_quality_suggestions(
    status: str = "pending",
    _: dict[str, Any] = Depends(admin_from_request),
) -> list[dict[str, Any]]:
    return list_quality_suggestions(status)


@app.post("/api/data-quality/suggestions/apply-all-safe")
def api_apply_all_safe_quality_suggestions(
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    return apply_all_safe_quality_suggestions()


@app.post("/api/data-quality/suggestions/apply-selected")
def api_apply_selected_quality_suggestions(
    payload: QualityBatchPayload,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    return apply_selected_quality_suggestions([item.model_dump() for item in payload.items])


@app.post("/api/data-quality/suggestions/{suggestion_id}/apply")
def api_apply_quality_suggestion(
    suggestion_id: int,
    payload: GenericPatchPayload,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    result = apply_quality_suggestion(suggestion_id, payload.values)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "数据治理建议应用失败"))
    return result


@app.post("/api/data-quality/suggestions/{suggestion_id}/dismiss")
def api_dismiss_quality_suggestion(
    suggestion_id: int,
    _: dict[str, Any] = Depends(admin_from_request),
) -> dict[str, Any]:
    return dismiss_quality_suggestion(suggestion_id)


@app.get("/api/entities/{entity_type}/{record_id}/evidence")
def api_entity_evidence(entity_type: str, record_id: int) -> dict[str, Any]:
    if entity_type not in {"task", "risk", "milestone", "change_request", "deliverable"}:
        raise HTTPException(status_code=400, detail="事项类型不正确。")
    result = entity_evidence(entity_type, record_id)
    if not result.get("entity"):
        raise HTTPException(status_code=404, detail="未找到该事项的来源记录。")
    return result


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


def add_quality_flags(
    conn: Any,
    entity_type: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    affected: dict[int, int] = {}
    rows = conn.execute(
        """
        SELECT primary_record_id, related_record_ids_json
        FROM data_quality_suggestions
        WHERE status = 'pending' AND entity_type = ?
          AND suggestion_kind IN ('exact_duplicate', 'near_duplicate', 'invalid_record')
        """,
        (entity_type,),
    ).fetchall()
    for row in rows:
        ids = [int(row["primary_record_id"])] if row["primary_record_id"] else []
        try:
            ids.extend(int(item) for item in json.loads(row["related_record_ids_json"] or "[]"))
        except Exception:
            pass
        for record_id in ids:
            affected[record_id] = affected.get(record_id, 0) + 1
    for record in records:
        record["quality_issue_count"] = affected.get(int(record["id"]), 0)
    return records


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
                SELECT t.*, (SELECT state FROM observation_resolutions r WHERE r.task_id=t.id) AS observation_state, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'task' AND pe.record_id = t.id) AS source_count
                FROM tasks t
                LEFT JOIN documents d ON d.id = t.source_document_id
                WHERE COALESCE(t.is_archived, 0) = 0
                  AND t.task_kind IN ('baseline', 'confirmed_addition')
                ORDER BY
                    COALESCE(t.start_date, ''),
                    COALESCE(t.due_date, ''),
                    t.id
                """
            ).fetchall()
        )
        milestones = rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'milestone' AND pe.record_id = m.id) AS source_count
                FROM milestones m
                LEFT JOIN documents d ON d.id = m.source_document_id
                WHERE COALESCE(m.is_archived, 0) = 0
                ORDER BY COALESCE(m.planned_date, ''), m.id
                """
            ).fetchall()
        )
        risks = rows_to_dicts(
            conn.execute(
                """
                SELECT r.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'risk' AND pe.record_id = r.id) AS source_count
                FROM risks r
                LEFT JOIN documents d ON d.id = r.source_document_id
                WHERE COALESCE(r.is_archived, 0) = 0
                ORDER BY r.id DESC
                """
            ).fetchall()
        )
        changes = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'change_request' AND pe.record_id = c.id) AS source_count
                FROM change_requests c
                LEFT JOIN documents d ON d.id = c.source_document_id
                WHERE COALESCE(c.is_archived, 0) = 0
                ORDER BY c.id DESC
                """
            ).fetchall()
        )
        add_quality_flags(conn, "task", tasks)
        add_quality_flags(conn, "milestone", milestones)
        add_quality_flags(conn, "risk", risks)
        add_quality_flags(conn, "change_request", changes)
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
        latest_processed_weekly_date = get_setting(conn, "task_status_as_of", "")
        counts = {
            "tasks": len(tasks),
            "completedTasks": len([task for task in tasks if task["status"] == "completed"]),
            "officialTasks": len(tasks),
            "completedOfficialTasks": len([task for task in tasks if task["status"] == "completed"]),
            "observationTasks": conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE task_kind = 'observation' AND COALESCE(is_archived, 0) = 0 AND NOT EXISTS (SELECT 1 FROM observation_resolutions r WHERE r.task_id=tasks.id)"
            ).fetchone()["c"],
            "pendingStatusReviews": conn.execute(
                """
                SELECT COUNT(*) AS c FROM data_quality_suggestions
                WHERE status = 'pending' AND suggestion_kind IN ('task_status_review', 'task_match_review')
                """
            ).fetchone()["c"],
            "milestones": len(milestones),
            "openRisks": len([risk for risk in risks if risk["status"] != "closed"]),
            "changes": len(changes),
            "documents": conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"],
            "pendingSuggestions": conn.execute(
                "SELECT COUNT(*) AS c FROM update_suggestions WHERE status = 'pending'"
            ).fetchone()["c"],
            "deliverables": conn.execute(
                "SELECT COUNT(*) AS c FROM deliverables WHERE COALESCE(is_archived, 0) = 0"
            ).fetchone()["c"],
            "submittedDeliverables": conn.execute(
                "SELECT COUNT(*) AS c FROM deliverables WHERE status = 'submitted' AND COALESCE(is_archived, 0) = 0"
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
            "projectPlanBreakdown": get_setting(conn, "project_plan_breakdown", []),
            "projectPlanVersion": get_setting(conn, "project_plan_version", ""),
            "lastScanAt": get_setting(conn, "last_scan_at", ""),
            "taskStatusAsOf": max(
                [latest_processed_weekly_date, *[str(task.get("status_as_of") or "")[:10] for task in tasks]],
                default="",
            ),
        }
    finally:
        conn.close()


@app.get("/api/tasks")
def get_tasks() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT t.*, (SELECT state FROM observation_resolutions r WHERE r.task_id=t.id) AS observation_state, d.name AS document_name, d.path AS document_path,
                    COALESCE(w.period_end, SUBSTR(d.effective_date, 1, 10), SUBSTR(d.modified_at, 1, 10)) AS source_date,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'task' AND pe.record_id = t.id) AS source_count
                FROM tasks t
                LEFT JOIN documents d ON d.id = t.source_document_id
                LEFT JOIN weekly_reports w ON w.document_id = d.id
                ORDER BY
                    COALESCE(t.is_archived, 0),
                    CASE t.task_kind WHEN 'baseline' THEN 1 WHEN 'confirmed_addition' THEN 2 ELSE 3 END,
                    CASE WHEN t.task_kind IN ('baseline', 'confirmed_addition') THEN COALESCE(t.start_date, t.due_date, '') ELSE '' END,
                    CASE WHEN t.task_kind = 'observation' THEN COALESCE(w.period_end, d.effective_date, d.modified_at, t.updated_at, '') ELSE '' END DESC,
                    CASE WHEN t.task_kind IN ('baseline', 'confirmed_addition') THEN t.id ELSE 0 END,
                    t.id DESC
                """
            ).fetchall()
        )
        return add_quality_flags(conn, "task", records)
    finally:
        conn.close()


@app.post("/api/tasks")
def create_task(payload: TaskPayload) -> dict[str, Any]:
    conn = get_connection()
    try:
        now = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO tasks(
                title, description, owner, status, priority, color_status, start_date,
                due_date, progress, source, show_in_gantt, task_kind, status_confidence,
                status_update_mode, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '手动新增', 1, 'confirmed_addition', 1, 'manual', ?, ?)
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
        task_id = int(cursor.lastrowid)
        ensure_entity(conn, "task", task_id, payload.title)
        conn.commit()
        return {"ok": True, "id": task_id}
    finally:
        conn.close()


@app.patch("/api/tasks/bulk")
def bulk_update_tasks(payload: TaskBulkPatchPayload) -> dict[str, Any]:
    if not payload.ids or any(item <= 0 for item in payload.ids):
        raise HTTPException(status_code=422, detail="至少提供一个有效任务编号。")
    if set(payload.values) != {"show_in_gantt"}:
        raise HTTPException(status_code=422, detail="批量更新仅支持甘特图显示字段。")
    try:
        values = {"show_in_gantt": _normalise_boolean(payload.values["show_in_gantt"], "甘特图显示")}
    except ValueError as exc:
        raise _validation_error(exc) from exc
    ids = sorted(set(payload.ids))
    placeholders = ",".join(["?"] * len(ids))
    conn = get_connection()
    try:
        records = {
            row["id"]: row for row in conn.execute(
                f"SELECT id, task_kind, is_archived FROM tasks WHERE id IN ({placeholders})", ids
            ).fetchall()
        }
        eligible, skipped = [], []
        for task_id in ids:
            row = records.get(task_id)
            reason = ("任务不存在" if row is None else "已归档任务不可修改" if row["is_archived"]
                      else "识别事项请先关联或提升为正式任务" if row["task_kind"] not in ("baseline", "confirmed_addition") else "")
            if reason:
                skipped.append({"id": task_id, "reason": reason})
            else:
                eligible.append(task_id)
        if not eligible:
            return {"ok": True, "updated": 0, "updatedIds": [], "skipped": skipped}
        placeholders = ",".join(["?"] * len(eligible))
        assignments = ", ".join([f"{key} = ?" for key in values])
        cursor = conn.execute(
            f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id IN ({placeholders})",
            [*values.values(), now_iso(), *eligible],
        )
        conn.commit()
        return {"ok": True, "updated": cursor.rowcount, "updatedIds": eligible, "skipped": skipped}
    finally:
        conn.close()


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: GenericPatchPayload) -> dict[str, Any]:
    try:
        values = _normalise_task_patch_values(payload.values)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="任务不存在。")
        if existing["is_archived"]:
            raise HTTPException(status_code=409, detail="已归档任务只读，不可修改")
        if existing["task_kind"] == "observation":
            raise HTTPException(status_code=409, detail="识别事项请通过收件箱关联或提升，不可直接修改执行状态")
        try:
            _ensure_date_order(
                values.get("start_date", existing["start_date"] or ""),
                values.get("due_date", existing["due_date"] or ""),
            )
        except ValueError as exc:
            raise _validation_error(exc) from exc
        assignments = ", ".join([f"{key} = ?" for key in values])
        cursor = conn.execute(
            f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
            [*values.values(), now_iso(), task_id],
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="任务不存在。")
        if "title" in values:
            ensure_entity(conn, "task", task_id, str(values["title"]))
        if "status" in values or "progress" in values:
            conn.execute(
                """
                UPDATE tasks
                SET status_confidence = 1, status_update_mode = 'manual',
                    status_source_document_id = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/tasks/reconcile-progress")
def api_task_reconcile(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return start_task_progress_reconcile("manual")


@app.get("/api/tasks/reconcile-status")
def api_task_reconcile_status(_: dict[str, Any] = Depends(admin_from_request)) -> dict[str, Any]:
    return task_reconcile_status()


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = 0
    action: Literal["link", "promote", "ignore", "transfer"]
    fingerprint: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=300)
    owner: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=10)
    targetId: int | None = None
    keepIndependent: bool = False
    reason: str = Field(default="", max_length=2000)
    suggestionType: Literal["risk", "change_request", "deliverable"] | None = None


class ObservationPreview(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


class ObservationBatch(BaseModel):
    items: list[ObservationRequest] = Field(min_length=1, max_length=100)
    requestKey: str = Field(min_length=1, max_length=100)


@app.get("/api/task-observations")
def api_observations(category: Literal["all", "link", "new", "conflict", "processed"] = "all",
                     page: int = Query(default=1, ge=1), page_size: int = Query(default=15, ge=1, le=100)):
    return observations.list_observations(category, page, page_size)


@app.post("/api/task-observations/preview")
def api_observation_preview(payload: ObservationPreview, admin: dict = Depends(admin_from_request)):
    try:
        return observations.preview(payload.ids)
    except ValueError as exc:
        raise _validation_error(exc) from exc


@app.post("/api/task-observations/resolve-batch")
def api_observation_batch(payload: ObservationBatch, admin: dict = Depends(admin_from_request)):
    try:
        return observations.start_batch([item.model_dump(exclude_none=True) for item in payload.items], payload.requestKey, admin["username"])
    except ValueError as exc:
        raise _validation_error(exc) from exc


@app.get("/api/task-observations/batch-status")
def api_observation_batch_status(batch_id: str | None = None, admin: dict = Depends(admin_from_request)):
    return observations.batch_status(batch_id)


@app.post("/api/task-observations/{task_id}/resolve")
def api_observation_resolve(task_id: int, payload: ObservationRequest, admin: dict = Depends(admin_from_request)):
    try:
        return observations.resolve({**payload.model_dump(exclude_none=True), "id": task_id}, admin["username"])
    except ValueError as exc:
        raise _validation_error(exc) from exc


@app.post("/api/tasks/{task_id}/promote")
def api_promote_task(task_id: int, payload: ObservationRequest, admin: dict = Depends(admin_from_request)):
    if payload.action != "promote":
        raise HTTPException(status_code=422, detail="提升接口仅支持 promote")
    return api_observation_resolve(task_id, payload, admin)


@app.get("/api/milestones")
def get_milestones() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'milestone' AND pe.record_id = m.id) AS source_count
                FROM milestones m
                LEFT JOIN documents d ON d.id = m.source_document_id
                WHERE COALESCE(m.is_archived, 0) = 0
                ORDER BY COALESCE(m.planned_date, ''), m.id
                """
            ).fetchall()
        )
        return add_quality_flags(conn, "milestone", records)
    finally:
        conn.close()


@app.get("/api/meetings")
def get_meetings() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT m.*, d.name AS document_name, d.path AS document_path
                FROM meetings m
                LEFT JOIN documents d ON d.id = m.document_id
                ORDER BY COALESCE(m.meeting_time, '') DESC, m.id DESC
                """
            ).fetchall()
        )
        return records
    finally:
        conn.close()


@app.get("/api/changes")
def get_changes() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'change_request' AND pe.record_id = c.id) AS source_count
                FROM change_requests c
                LEFT JOIN documents d ON d.id = c.source_document_id
                WHERE COALESCE(c.is_archived, 0) = 0
                ORDER BY c.id DESC
                """
            ).fetchall()
        )
        return add_quality_flags(conn, "change_request", records)
    finally:
        conn.close()


@app.get("/api/risks")
def get_risks() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT r.*, d.name AS document_name, d.path AS document_path,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'risk' AND pe.record_id = r.id) AS source_count
                FROM risks r
                LEFT JOIN documents d ON d.id = r.source_document_id
                WHERE COALESCE(r.is_archived, 0) = 0
                ORDER BY r.id DESC
                """
            ).fetchall()
        )
        return add_quality_flags(conn, "risk", records)
    finally:
        conn.close()


@app.get("/api/documents")
def get_documents() -> list[dict[str, Any]]:
    return list_documents()


@app.get("/api/uploads/targets")
def get_upload_targets() -> list[dict[str, Any]]:
    return upload_targets()


@app.get("/api/uploads/status")
def get_upload_status() -> dict[str, Any]:
    return upload_status()


@app.post("/api/documents/upload")
def upload_document(
    file: UploadFile = File(...),
    monitor_type: str = Form("other"),
    directory_index: int = Form(0),
) -> dict[str, Any]:
    try:
        result = save_upload(file.file, file.filename or "", monitor_type, directory_index)
        watcher.start()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        file.file.close()


@app.get("/api/deliverables")
def get_deliverables() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        records = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    dv.*,
                    d.name AS document_name,
                    d.path AS document_path,
                    d.modified_at AS document_modified_at,
                    (SELECT COUNT(*) FROM project_entities pe
                     JOIN entity_evidence ev ON ev.entity_id = pe.id
                     WHERE pe.entity_type = 'deliverable' AND pe.record_id = dv.id) AS source_count
                FROM deliverables dv
                LEFT JOIN documents d ON d.id = dv.document_id
                WHERE COALESCE(dv.is_archived, 0) = 0
                ORDER BY dv.sort_order, dv.id
                """
            ).fetchall()
        )
        return add_quality_flags(conn, "deliverable", records)
    finally:
        conn.close()


@app.post("/api/deliverables/{deliverable_id}/upload")
def upload_deliverable_document(
    deliverable_id: int,
    file: UploadFile = File(...),
    monitor_type: str = Form("acceptance_launch"),
    directory_index: int = Form(0),
) -> dict[str, Any]:
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT id FROM deliverables WHERE id = ? AND COALESCE(is_archived, 0) = 0",
            (deliverable_id,),
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        raise HTTPException(status_code=404, detail="交付物不存在或已归档。")
    try:
        result = save_upload(file.file, file.filename or "", monitor_type, directory_index, deliverable_id)
        watcher.start()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        file.file.close()


@app.patch("/api/deliverables/{deliverable_id}")
def update_deliverable(deliverable_id: int, payload: GenericPatchPayload) -> dict[str, Any]:
    try:
        values = _normalise_deliverable_patch_values(payload.values)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    conn = get_connection()
    try:
        existing = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="交付物不存在。")
        try:
            _ensure_date_order(
                values.get("planned_date", existing["planned_date"] or ""),
                values.get("submitted_date", existing["submitted_date"] or ""),
                "计划日期",
                "提交日期",
            )
        except ValueError as exc:
            raise _validation_error(exc) from exc
        if values.get("document_id") is not None:
            document = conn.execute("SELECT id FROM documents WHERE id = ?", (values["document_id"],)).fetchone()
            if not document:
                raise HTTPException(status_code=422, detail="关联文件不存在。")
        assignments = ", ".join([f"{key} = ?" for key in values])
        cursor = conn.execute(
            f"UPDATE deliverables SET {assignments}, updated_at = ? WHERE id = ?",
            [*values.values(), now_iso(), deliverable_id],
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="交付物不存在。")
        if "name" in values:
            ensure_entity(conn, "deliverable", deliverable_id, str(values["name"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


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
    ocr_text = combined_ocr_text(document_id)
    pages = ocr_pages(document_id) if document.get("ocr_status") in {"completed", "partial", "failed", "processing", "pending"} else []
    text = result.text or ocr_text or document.get("summary") or result.error or "该文件暂无法生成正文预览。"
    return {
        "document": document,
        "status": result.status,
        "error": result.error,
        "text": text,
        "ocrPages": pages,
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


@app.post("/api/suggestions/apply-all")
def api_apply_all_suggestions() -> dict[str, Any]:
    return apply_all_suggestions()


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
