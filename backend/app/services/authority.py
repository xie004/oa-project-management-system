from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.database import get_connection, now_iso, row_to_dict, rows_to_dicts
from app.services.extractors import compact_text, normalize_date


AUTHORITY_LABELS = {
    1: "合同正文",
    2: "合同附件/招投标/投标响应",
    3: "正式变更/确认文件",
    4: "会议纪要/决议",
    5: "周报/过程资料",
}

AUTHORITY_SCOPE_BY_LEVEL = {
    1: "合同范围、工期、费用、验收、交付物",
    2: "招投标要求、投标响应、合同附件、技术要求",
    3: "正式变更、确认事项、补充说明",
    4: "会议决议、过程协调、阶段安排",
    5: "当前进度、周计划、问题风险、过程记录",
}

AUTHORITY_WEIGHT = {
    1: 0.38,
    2: 0.30,
    3: 0.24,
    4: 0.14,
    5: 0.08,
}

KEY_AUTHORITY_TERMS = {
    "scope": ["范围", "建设内容", "需求", "功能", "集成", "模块"],
    "schedule": ["工期", "进度", "计划", "里程碑", "上线", "验收", "试运行"],
    "deliverable": ["交付物", "报告", "手册", "数据字典", "验收材料", "清单"],
    "acceptance": ["验收", "测评", "测试", "通过", "标准", "指标"],
    "cost": ["费用", "金额", "付款", "报价", "预算", "合同价"],
    "resource": ["服务器", "资源", "部署", "数据库", "中间件", "环境"],
    "change": ["变更", "调整", "确认", "补充", "优化"],
    "progress": ["当前", "本周", "进展", "完成", "风险", "协调"],
}

_authority_job_lock = threading.Lock()
_authority_job = {
    "running": False,
    "progress": 0,
    "total": 0,
    "autoApplied": 0,
    "suggested": 0,
    "error": "",
    "startedAt": "",
    "finishedAt": "",
}


def _set_authority_job(**patch) -> None:
    with _authority_job_lock:
        _authority_job.update(patch)


def authority_analyze_status() -> dict[str, Any]:
    with _authority_job_lock:
        return dict(_authority_job)


def authority_label(level: int | str | None) -> str:
    try:
        return AUTHORITY_LABELS.get(int(level or 5), "周报/过程资料")
    except (TypeError, ValueError):
        return "周报/过程资料"


def authority_weight(level: int | str | None) -> float:
    try:
        return AUTHORITY_WEIGHT.get(int(level or 5), 0.08)
    except (TypeError, ValueError):
        return 0.08


def question_scope(question: str) -> str:
    for scope, terms in KEY_AUTHORITY_TERMS.items():
        if any(term in question for term in terms):
            return scope
    return "general"


def _date_from_text(text: str) -> str:
    match = re.search(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", text or "")
    if not match:
        return ""
    return normalize_date("-".join(match.groups())) or ""


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term.lower() in text.lower() for term in terms)


def _looks_like_contract_attachment(text: str) -> bool:
    return _contains_any(text, ["附件", "招标", "投标", "中标通知", "中标公告", "中标候选", "采购需求", "响应文件"])


def infer_document_authority(document: dict[str, Any], text: str = "") -> dict[str, Any]:
    name = document.get("name") or Path(document.get("path") or "").name
    path = document.get("path") or ""
    category = document.get("doc_category") or ""
    haystack = f"{name}\n{path}\n{compact_text(text, 1600)}"
    name_haystack = f"{name}\n{path}"
    lower = haystack.lower()

    level = 5
    score = 45.0
    confidence = 0.55
    reason = "未识别到明确权威特征，按过程资料处理。"
    status = "pending"
    scope = AUTHORITY_SCOPE_BY_LEVEL[5]

    if _contains_any(name_haystack, ["合同"]) and not _looks_like_contract_attachment(name_haystack) and not _contains_any(name_haystack, ["周报", "会议纪要"]):
        level, score, confidence, status = 1, 98.0, 0.96, "confirmed"
        reason = "文件名或内容明确包含合同正文特征。"
    elif _contains_any(haystack, ["招标", "投标", "中标", "合同附件", "技术规范", "采购需求", "响应文件"]):
        level, score, confidence, status = 2, 88.0, 0.9, "confirmed"
        reason = "文件名或内容明确包含招投标、投标响应或合同附件特征。"
    elif _contains_any(haystack, ["正式变更", "变更确认", "补充协议", "确认函", "签字确认", "盖章确认"]):
        level, score, confidence, status = 3, 82.0, 0.86, "confirmed"
        reason = "文件名或内容包含正式变更或确认文件特征。"
    elif category == "meeting" or _contains_any(haystack, ["会议纪要", "周例会", "启动会", "会议决议", "会议记录"]):
        level, score, confidence, status = 4, 68.0, 0.84, "confirmed"
        reason = "文件名或分类显示为会议纪要/决议。"
    elif category == "weekly_report" or _contains_any(haystack, ["周报", "本周工作", "下周计划", "周工作"]):
        level, score, confidence, status = 5, 55.0, 0.86, "confirmed"
        reason = "文件名或分类显示为项目周报/过程资料。"
    elif category == "contract_tender":
        level, score, confidence, status = 2, 72.0, 0.68, "pending"
        reason = "系统分类为合同招投标，但无法进一步确认是否为合同正文或附件。"
    elif category == "requirement_change":
        level, score, confidence, status = 3, 62.0, 0.62, "pending"
        reason = "系统分类为需求/变更资料，需确认是否属于正式确认文件。"
    elif category == "resource":
        level, score, confidence, status = 5, 50.0, 0.7, "confirmed"
        reason = "系统分类为资源需求资料，通常用于部署和过程决策跟踪。"

    if "作废" in haystack or "废止" in haystack or "历史版本" in haystack:
        score = min(score, 40)
        status = "pending"
        reason = f"{reason} 同时检测到作废/历史版本提示，需管理员确认是否当前有效。"

    effective_date = _date_from_text(name) or _date_from_text(text) or (document.get("modified_at") or "")[:10]
    version_label = ""
    version_match = re.search(r"(v\d+(?:\.\d+)*|V\d+(?:\.\d+)*|第[一二三四五六七八九十\d]+版|终稿|定稿)", name)
    if version_match:
        version_label = version_match.group(1)

    scope = AUTHORITY_SCOPE_BY_LEVEL.get(level, scope)
    return {
        "authority_level": level,
        "authority_label": authority_label(level),
        "authority_score": score,
        "authority_scope": scope,
        "version_label": version_label,
        "effective_date": effective_date,
        "is_current": 1,
        "authority_reason": reason,
        "authority_status": status,
        "authority_note": "",
        "confidence": confidence,
    }


def _apply_authority(conn: sqlite3.Connection, document_id: int, result: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE documents
        SET authority_level = ?,
            authority_score = ?,
            authority_scope = ?,
            version_label = ?,
            effective_date = ?,
            is_current = ?,
            authority_reason = ?,
            authority_status = ?,
            authority_note = COALESCE(NULLIF(authority_note, ''), ?),
            authority_updated_at = ?
        WHERE id = ?
        """,
        (
            int(result.get("authority_level") or 5),
            float(result.get("authority_score") or 45),
            result.get("authority_scope") or "",
            result.get("version_label") or "",
            result.get("effective_date") or "",
            int(result.get("is_current", 1)),
            result.get("authority_reason") or "",
            result.get("authority_status") or "confirmed",
            result.get("authority_note") or "",
            now_iso(),
            document_id,
        ),
    )


def _upsert_authority_suggestion(conn: sqlite3.Connection, document_id: int, result: dict[str, Any]) -> None:
    existing = conn.execute(
        """
        SELECT id FROM document_authority_suggestions
        WHERE document_id = ? AND status = 'pending'
        """,
        (document_id,),
    ).fetchone()
    params = (
        int(result.get("authority_level") or 5),
        float(result.get("authority_score") or 45),
        result.get("authority_scope") or "",
        result.get("version_label") or "",
        result.get("effective_date") or "",
        int(result.get("is_current", 1)),
        result.get("authority_reason") or "",
        result.get("authority_note") or "",
        float(result.get("confidence") or 0.6),
    )
    if existing:
        conn.execute(
            """
            UPDATE document_authority_suggestions
            SET authority_level = ?, authority_score = ?, authority_scope = ?, version_label = ?,
                effective_date = ?, is_current = ?, authority_reason = ?, authority_note = ?,
                confidence = ?
            WHERE id = ?
            """,
            (*params, existing["id"]),
        )
        return
    conn.execute(
        """
        INSERT INTO document_authority_suggestions(
            document_id, authority_level, authority_score, authority_scope, version_label,
            effective_date, is_current, authority_reason, authority_note, confidence, status, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (document_id, *params, now_iso()),
    )


def analyze_document_authority(
    document_id: int,
    text: str = "",
    conn: sqlite3.Connection | None = None,
    force: bool = False,
) -> dict[str, Any]:
    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        document = row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
        if not document:
            return {"ok": False, "error": "文档不存在"}
        if not force and document.get("authority_status") == "manual":
            return {"ok": True, "skipped": True, "reason": "manual"}
        result = infer_document_authority(document, text)
        if result["authority_status"] == "confirmed" and result["confidence"] >= 0.8:
            _apply_authority(conn, document_id, result)
            action = "auto_applied"
        else:
            conn.execute(
                """
                UPDATE documents
                SET authority_level = ?, authority_score = ?, authority_scope = ?,
                    version_label = ?, effective_date = ?, is_current = ?,
                    authority_reason = ?, authority_status = 'pending',
                    authority_updated_at = ?
                WHERE id = ? AND authority_status != 'manual'
                """,
                (
                    int(result.get("authority_level") or 5),
                    float(result.get("authority_score") or 45),
                    result.get("authority_scope") or "",
                    result.get("version_label") or "",
                    result.get("effective_date") or "",
                    int(result.get("is_current", 1)),
                    result.get("authority_reason") or "",
                    now_iso(),
                    document_id,
                ),
            )
            _upsert_authority_suggestion(conn, document_id, result)
            action = "suggested"
        if owns_connection:
            conn.commit()
        return {"ok": True, "action": action, **result}
    except Exception as exc:
        if owns_connection:
            conn.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        if owns_connection:
            conn.close()


def list_document_authority() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    d.*,
                    (
                        SELECT COUNT(*) FROM knowledge_chunks c WHERE c.document_id = d.id
                    ) AS chunk_count
                FROM documents d
                ORDER BY d.authority_level, d.authority_score DESC, d.modified_at DESC, d.id DESC
                """
            ).fetchall()
        )
        for row in rows:
            row["authority_label"] = authority_label(row.get("authority_level"))
            row["is_current"] = bool(row.get("is_current"))
        return rows
    finally:
        conn.close()


def update_document_authority(document_id: int, values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "authority_level",
        "authority_score",
        "authority_scope",
        "version_label",
        "effective_date",
        "is_current",
        "authority_reason",
        "authority_note",
        "authority_status",
    }
    patch = {key: values[key] for key in values if key in allowed}
    if not patch:
        return {"ok": True}
    if "authority_level" in patch:
        level = int(patch["authority_level"])
        if level not in AUTHORITY_LABELS:
            return {"ok": False, "message": "权威等级不正确"}
        patch["authority_level"] = level
        patch.setdefault("authority_score", max(45, 105 - level * 12))
        patch.setdefault("authority_scope", AUTHORITY_SCOPE_BY_LEVEL[level])
    if "is_current" in patch:
        patch["is_current"] = 1 if patch["is_current"] else 0
    patch["authority_status"] = "manual"
    conn = get_connection()
    try:
        assignments = ", ".join([f"{key} = ?" for key in patch])
        conn.execute(
            f"UPDATE documents SET {assignments}, authority_updated_at = ? WHERE id = ?",
            [*patch.values(), now_iso(), document_id],
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def list_authority_suggestions(status: str = "pending") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT s.*, d.name AS document_name, d.path AS document_path, d.doc_category
                FROM document_authority_suggestions s
                JOIN documents d ON d.id = s.document_id
                WHERE s.status = ?
                ORDER BY s.created_at DESC, s.id DESC
                """,
                (status,),
            ).fetchall()
        )
        for row in rows:
            row["authority_label"] = authority_label(row.get("authority_level"))
            row["is_current"] = bool(row.get("is_current"))
        return rows
    finally:
        conn.close()


def apply_authority_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        suggestion = row_to_dict(
            conn.execute("SELECT * FROM document_authority_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        )
        if not suggestion:
            return {"ok": False, "message": "权威建议不存在"}
        if suggestion["status"] != "pending":
            return {"ok": False, "message": "该权威建议不是待确认状态"}
        _apply_authority(
            conn,
            int(suggestion["document_id"]),
            {**suggestion, "authority_status": "confirmed"},
        )
        conn.execute(
            "UPDATE document_authority_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
            (now_iso(), suggestion_id),
        )
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def dismiss_authority_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE document_authority_suggestions SET status = 'dismissed' WHERE id = ?",
            (suggestion_id,),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _load_document_text(conn: sqlite3.Connection, document_id: int) -> str:
    rows = rows_to_dicts(
        conn.execute(
            "SELECT text FROM knowledge_chunks WHERE document_id = ? ORDER BY chunk_index LIMIT 8",
            (document_id,),
        ).fetchall()
    )
    if rows:
        return compact_text("\n".join(row["text"] for row in rows), 6000)
    return ""


def _authority_worker() -> None:
    conn = get_connection()
    try:
        docs = rows_to_dicts(conn.execute("SELECT * FROM documents ORDER BY id").fetchall())
    finally:
        conn.close()
    _set_authority_job(
        running=True,
        progress=0,
        total=len(docs),
        autoApplied=0,
        suggested=0,
        error="",
        startedAt=now_iso(),
        finishedAt="",
    )
    auto_applied = 0
    suggested = 0
    try:
        for index, doc in enumerate(docs, start=1):
            conn = get_connection()
            try:
                text = _load_document_text(conn, int(doc["id"]))
                result = analyze_document_authority(int(doc["id"]), text, conn=conn, force=True)
                conn.commit()
                if result.get("action") == "auto_applied":
                    auto_applied += 1
                elif result.get("action") == "suggested":
                    suggested += 1
            finally:
                conn.close()
            _set_authority_job(progress=index, autoApplied=auto_applied, suggested=suggested)
            time.sleep(0.03)
        _set_authority_job(running=False, finishedAt=now_iso())
    except Exception as exc:
        _set_authority_job(running=False, error=str(exc), finishedAt=now_iso())


def start_authority_analyze_job() -> dict[str, Any]:
    with _authority_job_lock:
        if _authority_job["running"]:
            return {"ok": True, "alreadyRunning": True, **dict(_authority_job)}
    _set_authority_job(running=True, progress=0, total=0, autoApplied=0, suggested=0, error="", startedAt=now_iso(), finishedAt="")
    thread = threading.Thread(target=_authority_worker, daemon=True)
    thread.start()
    return {"ok": True, "started": True, **authority_analyze_status()}


def authority_source_payload(row: dict[str, Any], primary: bool = False, conflict_note: str = "") -> dict[str, Any]:
    level = int(row.get("authority_level") or 5)
    return {
        "authorityLevel": level,
        "authorityLabel": authority_label(level),
        "authorityScore": float(row.get("authority_score") or 45),
        "isPrimaryBasis": primary,
        "conflictNote": conflict_note,
    }


def source_conflict_note(sources: list[dict[str, Any]], scope: str) -> str:
    if scope not in {"scope", "schedule", "deliverable", "acceptance", "cost", "resource", "change"}:
        return ""
    levels = [int(item.get("authorityLevel") or item.get("authority_level") or 5) for item in sources]
    if not levels or min(levels) >= 4:
        return ""
    if any(level >= 4 for level in levels) and min(levels) <= 2:
        return "如会议纪要、周报与合同/招投标文件存在不同表述，应以更高权威资料为准；低权威资料需确认是否已形成正式变更。"
    return ""
