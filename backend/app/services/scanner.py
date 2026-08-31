from __future__ import annotations

import hashlib
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
from app.services.ai import ai_config, chat_completion, index_document_knowledge
from app.services.authority import analyze_document_authority
from app.services.data_quality import (
    ENTITY_TABLES,
    attach_evidence,
    ensure_entity,
    find_matching_entity,
    merge_candidate_payload,
    normalize_title,
    prepare_candidate,
    suggestion_fingerprint,
)
from app.services.ocr import enqueue_document_ocr, should_ocr_pdf


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
    locator: str = "",
    method: str = "rule",
    evidence_text: str = "",
) -> None:
    title = compact_text(title, 140)
    description = compact_text(description, 1200)
    if not title:
        return
    payload = {**payload, "source_document_id": document_id}
    candidate = prepare_candidate(
        conn,
        document_id,
        suggestion_type,
        title,
        evidence_text or description,
        payload,
        confidence,
        locator=locator,
        method=method,
    )
    if candidate.get("skip"):
        return
    exists = conn.execute(
        """
        SELECT * FROM update_suggestions
        WHERE status = 'pending' AND suggestion_type = ?
          AND (fingerprint = ? OR normalized_title = ?)
        """,
        (suggestion_type, candidate.get("fingerprint"), candidate.get("normalized_title")),
    ).fetchone()
    if exists:
        _add_suggestion_source(
            conn,
            int(exists["id"]),
            document_id,
            evidence_text or description,
            locator,
            payload,
        )
        try:
            existing_payload = json.loads(exists["payload_json"] or "{}")
        except Exception:
            existing_payload = {}
        merged_payload = merge_candidate_payload(existing_payload, payload)
        try:
            warnings = json.loads(exists["quality_warnings_json"] or "[]")
        except Exception:
            warnings = []
        aggregate_warning = "已聚合来自多份资料的同一候选，正式字段仍需确认"
        if aggregate_warning not in warnings:
            warnings.append(aggregate_warning)
        conn.execute(
            """
            UPDATE update_suggestions
            SET confidence = MAX(confidence, ?), quality_score = MAX(quality_score, ?),
                payload_json = ?, quality_warnings_json = ?
            WHERE id = ?
            """,
            (
                confidence,
                float(candidate.get("quality_score") or confidence),
                json.dumps(merged_payload, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                exists["id"],
            ),
        )
        return
    if candidate.get("proposed_updates"):
        payload["proposed_updates"] = candidate["proposed_updates"]
    cursor = conn.execute(
        """
        INSERT INTO update_suggestions(
            document_id, suggestion_type, title, description, confidence,
            payload_json, status, normalized_title, fingerprint, candidate_action,
            matched_entity_type, matched_entity_id, similarity, quality_score,
            quality_warnings_json, evidence_text, evidence_locator, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            suggestion_type,
            title,
            description,
            confidence,
            json.dumps(payload, ensure_ascii=False),
            candidate.get("normalized_title") or normalize_title(title),
            candidate.get("fingerprint") or suggestion_fingerprint(suggestion_type, title),
            candidate.get("candidate_action") or "create",
            candidate.get("matched_entity_type"),
            candidate.get("matched_entity_id"),
            float(candidate.get("similarity") or 0),
            float(candidate.get("quality_score") or confidence),
            json.dumps(candidate.get("warnings") or [], ensure_ascii=False),
            evidence_text or description,
            locator,
            now_iso(),
        ),
    )
    _add_suggestion_source(
        conn,
        int(cursor.lastrowid),
        document_id,
        evidence_text or description,
        locator,
        payload,
    )


def _add_suggestion_source(
    conn: sqlite3.Connection,
    suggestion_id: int,
    document_id: int | None,
    evidence_text: str,
    locator: str,
    observed: dict[str, Any],
) -> None:
    evidence_text = compact_text(evidence_text or "来源文件记录", 1600)
    digest = hashlib.sha256(f"{document_id or 0}|{locator}|{evidence_text}".encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO suggestion_sources(
            suggestion_id, document_id, evidence_hash, evidence_text, locator, observed_json, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            suggestion_id,
            document_id,
            digest,
            evidence_text,
            locator,
            json.dumps(observed or {}, ensure_ascii=False),
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
            locator="周报/下周工作计划",
            evidence_text=line,
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
                locator="周报/本周工作进展",
                evidence_text=line,
            )

    risk_items: list[dict[str, str]] = []
    for line in bullet_lines(parsed["risk_text"], 8):
        if re.match(r"^(?:策略|措施|应对|建议|处理方式)[:：]", line):
            if risk_items:
                risk_items[-1]["mitigation"] = re.sub(r"^[^:：]+[:：]", "", line).strip()
            continue
        if not any(word in line for word in ["风险", "影响", "可能", "导致", "滞后", "无法", "不足", "超期", "问题"]):
            continue
        risk_items.append({"title": line[:90], "description": line, "mitigation": ""})
    for item in risk_items[:5]:
        create_suggestion(
            conn,
            document_id,
            "risk",
            item["title"],
            f"从周报“存在问题或风险”识别到的风险：{item['description']}",
            {
                "title": item["title"],
                "description": item["description"],
                "level": "medium",
                "status": "open",
                "mitigation": item["mitigation"],
            },
            0.82,
            locator="周报/存在问题或风险",
            evidence_text=item["description"],
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

    for line in bullet_lines(parsed["decisions"], 12):
        if not any(word in line for word in ["需", "请", "负责", "完成", "提供", "确认", "组织", "推进", "跟进", "提交", "梳理", "配置", "申请", "发送", "开展", "落实", "安排"]):
            continue
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
            locator="会议纪要/决议",
            evidence_text=line,
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
                locator="会议纪要/议题或决议",
                evidence_text=line,
            )


def save_resource_suggestions(conn: sqlite3.Connection, document_id: int, text: str, filename: str) -> None:
    # 资源清单本身作为资料和部署依据，不默认制造里程碑。
    return


AI_TYPE_ALLOWLIST = {
    "weekly_report": {"task", "risk", "milestone"},
    "meeting": {"task", "risk", "milestone", "change_request"},
    "contract_tender": {"milestone", "change_request", "deliverable"},
    "requirement_change": {"milestone", "change_request", "deliverable"},
    "resource": {"task", "risk"},
    "acceptance_launch": {"milestone", "risk", "deliverable"},
    "other": {"task", "risk", "milestone", "change_request", "deliverable"},
}


def _parse_ai_items(raw: str) -> list[dict[str, Any]]:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("模型返回为空")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    items = json.loads(cleaned)
    if not isinstance(items, list):
        raise ValueError("模型未返回 JSON 数组")
    return [item for item in items if isinstance(item, dict)]


def _validate_ai_items(items: list[dict[str, Any]], allowed_types: set[str]) -> list[dict[str, Any]]:
    required = ["type", "title", "description", "confidence", "evidence", "locator"]
    errors: list[str] = []
    for index, item in enumerate(items):
        missing = [key for key in required if item.get(key) in (None, "")]
        if missing:
            errors.append(f"第 {index + 1} 项缺少 {', '.join(missing)}")
        if item.get("type") not in allowed_types:
            errors.append(f"第 {index + 1} 项 type 不在允许范围")
        try:
            confidence = float(item.get("confidence"))
            if confidence < 0 or confidence > 1:
                errors.append(f"第 {index + 1} 项 confidence 超出 0-1")
        except (TypeError, ValueError):
            errors.append(f"第 {index + 1} 项 confidence 不是数字")
    if errors:
        raise ValueError("；".join(errors[:8]))
    return items


def test_structured_extraction() -> dict[str, Any]:
    config = ai_config()
    sample = (
        "会议决定：实施单位于2026年9月10日前完成测试环境部署，责任人为张工。\n"
        "风险：历史数据字段映射未确认，可能影响迁移进度；应对措施为本周组织专题确认。"
    )
    answer = chat_completion(
        [
            {
                "role": "system",
                "content": "你是结构化抽取测试器，只输出严格 JSON 数组，不使用 Markdown。",
            },
            {
                "role": "user",
                "content": (
                    "从以下测试文本提取 task 和 risk。每项必须包含 type、title、description、"
                    "confidence、evidence、locator；风险措施写入 mitigation。\n" + sample
                ),
            },
        ],
        config,
        max_tokens=900,
    )
    items = _parse_ai_items(answer)
    errors: list[str] = []
    for index, item in enumerate(items):
        missing = [key for key in ["type", "title", "description", "confidence", "evidence", "locator"] if item.get(key) in (None, "")]
        if missing:
            errors.append(f"第 {index + 1} 项缺少字段：{', '.join(missing)}")
        if item.get("type") not in {"task", "risk"}:
            errors.append(f"第 {index + 1} 项 type 不合规")
    if not items:
        errors.append("模型未返回任何结构化事项")
    return {
        "ok": not errors,
        "items": items,
        "errors": errors,
        "raw": compact_text(answer, 3000),
    }


def save_ai_analysis_suggestions(conn: sqlite3.Connection, document_id: int, text: str, filename: str, category: str) -> None:
    config = ai_config()
    if not config.get("enabled"):
        conn.execute(
            "UPDATE documents SET analysis_status = 'not_analyzed', analysis_error = '' WHERE id = ?",
            (document_id,),
        )
        return
    conn.execute(
        "UPDATE documents SET analysis_status = 'analyzing', analysis_error = '' WHERE id = ?",
        (document_id,),
    )
    answer = ""
    repaired = ""
    try:
        allowed_types = AI_TYPE_ALLOWLIST.get(category, AI_TYPE_ALLOWLIST["other"])
        prompt = f"""
请基于以下项目文件内容，识别可能需要更新到项目管理系统的事项。
只返回 JSON 数组，每项必须包含 type、title、description、confidence、evidence、locator，
可选包含 owner、status、progress、due_date、planned_date、actual_date、level、mitigation。
type 只能是：{', '.join(sorted(allowed_types))}。
title 必须是可独立管理的项目事项，禁止输出页码、页眉、表头、章节名和纯说明文字。
风险必须说明风险事件或影响，应对策略写入 mitigation，不得单独作为风险。
合同/招投标/需求文件只提取正式约束、交付物、验收和里程碑，不把描述性段落拆成过程任务。
evidence 必须是文件中的简短原文依据，locator 说明章节、页码、表格或段落位置。
文件名：{filename}
分类：{category}
内容：
{compact_text(text, int(config.get('maxTextLength') or 12000))}
"""
        answer = chat_completion(
            [
                {"role": "system", "content": "你是项目管理资料分析助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            config,
        )
        try:
            items = _validate_ai_items(_parse_ai_items(answer), allowed_types)
        except Exception as first_error:
            repair_prompt = f"""
下面是一次项目事项抽取的无效输出，请将它修复为严格 JSON 数组。
不得补造原输出中不存在的事项。每项保留 type、title、description、confidence、evidence、locator。
允许的 type：{', '.join(sorted(allowed_types))}。
无效输出：
{compact_text(answer, 8000) or '[空输出]'}
"""
            repaired = chat_completion(
                [
                    {"role": "system", "content": "你是 JSON 结构修复器，只输出 JSON 数组。"},
                    {"role": "user", "content": repair_prompt},
                ],
                config,
            )
            try:
                items = _validate_ai_items(_parse_ai_items(repaired), allowed_types)
            except Exception as second_error:
                raise ValueError(f"首次解析失败：{first_error}；结构修复失败：{second_error}") from second_error
        for item in items[:12]:
            suggestion_type = item.get("type")
            if suggestion_type not in allowed_types:
                continue
            title = item.get("title") or ""
            description = item.get("description") or title
            if suggestion_type == "risk" and not any(
                word in f"{title}{description}" for word in ["风险", "影响", "可能", "导致", "无法", "不足", "滞后", "超期", "问题"]
            ):
                continue
            payload = {
                "title": title,
                "description": description,
                "status": item.get("status") or ("pending" if suggestion_type == "change_request" else "not_started"),
                "source": "大模型分析",
            }
            for key in ["owner", "progress", "due_date", "planned_date", "actual_date", "level", "mitigation"]:
                if item.get(key) not in (None, ""):
                    payload[key] = item[key]
            create_suggestion(
                conn,
                document_id,
                suggestion_type,
                title,
                description,
                payload,
                float(item.get("confidence") or 0.7),
                locator=str(item.get("locator") or "模型抽取"),
                method="llm",
                evidence_text=str(item.get("evidence") or description),
            )
        conn.execute(
            "UPDATE documents SET analysis_status = 'analyzed', analysis_at = ?, analysis_error = '', analysis_raw_response = ? WHERE id = ?",
            (now_iso(), compact_text(repaired or answer, 12000), document_id),
        )
    except Exception as exc:
        conn.execute(
            "UPDATE documents SET analysis_status = 'failed', analysis_at = ?, analysis_error = ?, analysis_raw_response = ? WHERE id = ?",
            (now_iso(), str(exc), compact_text(repaired or answer, 12000), document_id),
        )


def auto_link_deliverables(conn: sqlite3.Connection, document_id: int, filename: str) -> None:
    normalized = filename.lower()
    rows = conn.execute(
        "SELECT * FROM deliverables WHERE document_id IS NULL AND COALESCE(is_archived, 0) = 0"
    ).fetchall()
    for row in rows:
        name = row["name"]
        keywords = [part for part in re.split(r"[/、\s（）()]+", name) if len(part) >= 2]
        if name in filename or any(keyword.lower() in normalized for keyword in keywords):
            conn.execute(
                "UPDATE deliverables SET document_id = ?, updated_at = ? WHERE id = ?",
                (document_id, now_iso(), row["id"]),
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

        if should_ocr_pdf(path, result.text):
            enqueue_document_ocr(document_id, path, conn=conn)
        elif path.suffix.lower() == ".pdf":
            conn.execute(
                "UPDATE documents SET ocr_status = 'not_required', ocr_progress = 0, ocr_error = '' WHERE id = ?",
                (document_id,),
            )

        if result.text:
            index_document_knowledge(document_id, result.text, path.name, conn=conn)
            analyze_document_authority(document_id, result.text, conn=conn, force=force)
            auto_link_deliverables(conn, document_id, path.name)
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
                            locator="需求或变更资料/正文",
                            evidence_text=line,
                        )
            save_ai_analysis_suggestions(conn, document_id, result.text, path.name, category)
        else:
            analyze_document_authority(document_id, "", conn=conn, force=force)

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
            SELECT s.*, d.name AS document_name, d.path AS document_path, d.doc_category,
                   (SELECT COUNT(*) FROM suggestion_sources ss WHERE ss.suggestion_id = s.id) AS source_count
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
            try:
                item["quality_warnings"] = json.loads(item.pop("quality_warnings_json") or "[]")
            except Exception:
                item["quality_warnings"] = []
        return suggestions
    finally:
        conn.close()


DELIVERABLE_GENERIC_TERMS = {
    "报告",
    "材料",
    "文档",
    "清单",
    "方案",
    "手册",
    "记录",
    "文件",
    "测评",
    "项目",
    "交付",
    "交付物",
}


def _normalize_deliverable_title(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (value or "").lower())


def _deliverable_title_tokens(value: str) -> set[str]:
    normalized = _normalize_deliverable_title(value)
    tokens = {term for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", value or "") if term not in DELIVERABLE_GENERIC_TERMS}
    tokens.update(
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
        if normalized[index : index + 2] not in DELIVERABLE_GENERIC_TERMS
    )
    return {token for token in tokens if len(token) >= 2}


def _deliverable_match_score(suggestion_title: str, existing_name: str) -> float:
    left = _normalize_deliverable_title(suggestion_title)
    right = _normalize_deliverable_title(existing_name)
    if not left or not right:
        return 0
    if left == right:
        return 1.0
    shorter, longer = sorted([left, right], key=len)
    if len(shorter) >= 6 and shorter in longer:
        return 0.92
    left_tokens = _deliverable_title_tokens(suggestion_title)
    right_tokens = _deliverable_title_tokens(existing_name)
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    coverage = overlap / min(len(left_tokens), len(right_tokens))
    jaccard = overlap / union
    return max(jaccard, coverage * 0.78)


def _find_matching_deliverable(conn: sqlite3.Connection, title: str) -> dict[str, Any] | None:
    rows = rows_to_dicts(
        conn.execute(
            "SELECT * FROM deliverables WHERE COALESCE(is_archived, 0) = 0 ORDER BY sort_order, id"
        ).fetchall()
    )
    scored = sorted(
        ((_deliverable_match_score(title, row["name"]), row) for row in rows),
        reverse=True,
        key=lambda item: item[0],
    )
    if not scored:
        return None
    score, row = scored[0]
    return row if score >= 0.86 else None


def _append_suggestion_description(existing: str, addition: str) -> str:
    existing = (existing or "").strip()
    addition = compact_text(addition or "", 900)
    if not addition:
        return existing
    if not existing:
        return addition
    if addition in existing:
        return existing
    return compact_text(f"{existing}\n\n智能建议补充：{addition}", 1800)


def _apply_deliverable_suggestion(
    conn: sqlite3.Connection,
    suggestion: sqlite3.Row,
    payload: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    title = compact_text(payload.get("title") or suggestion["title"], 160)
    description = compact_text(payload.get("description") or suggestion["description"], 1200)
    if not title:
        return {"ok": False, "message": "交付物建议缺少标题。"}

    source_document_id = payload.get("source_document_id") or suggestion["document_id"]
    status = payload.get("status") or "not_started"
    owner = payload.get("owner") or ""
    requirement_source = payload.get("source") or "智能建议"
    matched = _find_matching_deliverable(conn, title)

    if matched:
        updates: dict[str, Any] = {
            "description": _append_suggestion_description(matched.get("description") or "", description),
        }
        if not matched.get("owner") and owner:
            updates["owner"] = owner
        if not matched.get("requirement_source") and requirement_source:
            updates["requirement_source"] = requirement_source
        if not matched.get("document_id") and source_document_id:
            updates["document_id"] = source_document_id
        if (matched.get("status") or "not_started") == "not_started" and status:
            updates["status"] = status

        assignments = ", ".join([f"{key} = ?" for key in updates])
        conn.execute(
            f"UPDATE deliverables SET {assignments}, updated_at = ? WHERE id = ?",
            [*updates.values(), now, matched["id"]],
        )
        return {"ok": True, "deliverableId": matched["id"], "action": "updated"}

    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS value FROM deliverables").fetchone()["value"]
    cursor = conn.execute(
        """
        INSERT INTO deliverables(
            name, requirement_source, description, status, owner, planned_date,
            submitted_date, document_id, sort_order, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            requirement_source,
            description,
            status,
            owner,
            payload.get("planned_date", ""),
            payload.get("submitted_date", ""),
            source_document_id,
            next_order,
            now,
            now,
        ),
    )
    return {"ok": True, "deliverableId": cursor.lastrowid, "action": "created"}


def _attach_suggestion_sources(
    conn: sqlite3.Connection,
    suggestion: sqlite3.Row,
    entity_id: int,
    payload: dict[str, Any],
) -> None:
    sources = conn.execute(
        "SELECT * FROM suggestion_sources WHERE suggestion_id = ? ORDER BY created_at, id",
        (suggestion["id"],),
    ).fetchall()
    if not sources:
        sources = [
            {
                "document_id": suggestion["document_id"],
                "evidence_text": suggestion["evidence_text"] or suggestion["description"] or suggestion["title"],
                "locator": suggestion["evidence_locator"] or "",
                "observed_json": suggestion["payload_json"],
            }
        ]
    for source in sources:
        try:
            observed = json.loads(source["observed_json"] or "{}")
        except Exception:
            observed = payload
        attach_evidence(
            conn,
            entity_id,
            source["document_id"],
            source["evidence_text"] or suggestion["title"],
            suggestion_id=int(suggestion["id"]),
            locator=source["locator"] or "",
            observed=observed,
            confidence=float(suggestion["confidence"] or 0.7),
            method="confirmed_suggestion",
        )


def _apply_matched_suggestion(
    conn: sqlite3.Connection,
    suggestion: sqlite3.Row,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    suggestion_type = suggestion["suggestion_type"]
    if suggestion_type not in ENTITY_TABLES:
        return None
    matched_entity_id = suggestion["matched_entity_id"]
    candidate_action = suggestion["candidate_action"] or "create"
    entity = None
    similarity = float(suggestion["similarity"] or 0)
    if matched_entity_id:
        entity = conn.execute(
            "SELECT * FROM project_entities WHERE id = ? AND status = 'active'",
            (matched_entity_id,),
        ).fetchone()
    if not entity:
        matched, current_similarity = find_matching_entity(conn, suggestion_type, suggestion["title"])
        if matched and current_similarity >= 0.93:
            entity = matched
            similarity = current_similarity
            candidate_action = "attach_evidence"
    if not entity or (candidate_action == "create" and similarity < 0.93):
        return None

    config = ENTITY_TABLES[suggestion_type]
    record = conn.execute(
        f"SELECT * FROM {config['table']} WHERE id = ? AND COALESCE(is_archived, 0) = 0",
        (entity["record_id"],),
    ).fetchone()
    if not record:
        return None

    proposed_updates = payload.get("proposed_updates") or {}
    allowed_updates = {
        "task": {"status", "progress", "owner", "start_date", "due_date"},
        "risk": {"status", "level", "mitigation"},
        "milestone": {"status", "planned_date", "actual_date"},
        "change_request": {"status", "impact", "proposer"},
        "deliverable": {"status", "owner", "planned_date", "submitted_date"},
    }[suggestion_type]
    updates = {key: value for key, value in proposed_updates.items() if key in allowed_updates}
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE {config['table']} SET {assignments}, updated_at = ? WHERE id = ?",
            [*updates.values(), now_iso(), entity["record_id"]],
        )
    _attach_suggestion_sources(conn, suggestion, int(entity["id"]), payload)
    return {
        "ok": True,
        "action": "updated" if updates else "evidence_attached",
        "entityId": entity["id"],
        "recordId": entity["record_id"],
        "updates": updates,
    }


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
        matched_result = _apply_matched_suggestion(conn, suggestion, payload)
        if matched_result:
            conn.execute(
                "UPDATE update_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
                (now, suggestion_id),
            )
            conn.commit()
            return matched_result

        if suggestion_type == "task":
            cursor = conn.execute(
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
            record_id = int(cursor.lastrowid)
        elif suggestion_type == "risk":
            cursor = conn.execute(
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
            record_id = int(cursor.lastrowid)
        elif suggestion_type == "milestone":
            cursor = conn.execute(
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
            record_id = int(cursor.lastrowid)
        elif suggestion_type == "change_request":
            cursor = conn.execute(
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
            record_id = int(cursor.lastrowid)
        elif suggestion_type == "deliverable":
            deliverable_result = _apply_deliverable_suggestion(conn, suggestion, payload, now)
            if not deliverable_result.get("ok"):
                return deliverable_result
            record_id = int(deliverable_result["deliverableId"])
        else:
            return {"ok": False, "message": f"暂不支持应用 {suggestion_type} 类型。"}

        config = ENTITY_TABLES[suggestion_type]
        entity_id = ensure_entity(
            conn,
            suggestion_type,
            record_id,
            payload.get("title") or suggestion["title"],
            False,
        )
        _attach_suggestion_sources(conn, suggestion, entity_id, payload)

        conn.execute(
            "UPDATE update_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
            (now, suggestion_id),
        )
        conn.commit()
        if suggestion_type == "deliverable":
            return {"ok": True, "entityId": entity_id, **deliverable_result}
        return {"ok": True, "entityId": entity_id, "recordId": record_id, "action": "created"}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def apply_all_suggestions() -> dict[str, Any]:
    conn = get_connection()
    try:
        ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM update_suggestions WHERE status = 'pending' ORDER BY created_at, id"
            ).fetchall()
        ]
    finally:
        conn.close()

    applied = 0
    failed: list[dict[str, Any]] = []
    for suggestion_id in ids:
        result = apply_suggestion(int(suggestion_id))
        if result.get("ok"):
            applied += 1
        else:
            failed.append({"id": suggestion_id, "message": result.get("message", "应用失败")})
    return {"ok": not failed, "applied": applied, "failed": failed, "total": len(ids)}


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
