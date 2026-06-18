from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from app.database import get_connection, get_setting, now_iso, row_to_dict, rows_to_dicts
from app.services.authority import AUTHORITY_LABELS, authority_label
from app.services.ai import chat_completion
from app.services.extractors import compact_text
from app.services.ocr import combined_ocr_text


WIKI_PAGES = [
    ("overview", "项目概览"),
    ("goals_scope", "建设目标与范围"),
    ("plan_milestones", "实施计划与里程碑"),
    ("current_progress", "当前进度"),
    ("risks_coordination", "风险与待协调事项"),
    ("meeting_decisions", "会议决议"),
    ("change_requests", "变更需求"),
    ("deliverables_acceptance", "交付物与验收清单"),
    ("resources_deployment", "资源与部署要求"),
]

PAGE_SOURCE_STRATEGIES: dict[str, dict[str, Any]] = {
    "overview": {
        "primary": ["baseline", "contract_tender", "requirement_change"],
        "secondary": ["meeting", "weekly_report", "acceptance_launch", "resource"],
        "keywords": ["项目", "概览", "合同", "采购", "需求", "建设", "范围", "目标"],
        "min_authority": 4,
        "pin_baseline": True,
    },
    "goals_scope": {
        "primary": ["baseline", "contract_tender", "requirement_change"],
        "secondary": ["meeting", "acceptance_launch"],
        "keywords": ["目标", "建设目标", "范围", "建设内容", "采购需求", "需求", "合同", "系统集成"],
        "min_authority": 4,
        "pin_baseline": True,
    },
    "plan_milestones": {
        "primary": ["baseline", "contract_tender", "requirement_change"],
        "secondary": ["meeting", "weekly_report"],
        "keywords": ["计划", "里程碑", "工期", "上线", "试运行", "验收", "实施"],
        "min_authority": 4,
        "pin_baseline": True,
    },
    "current_progress": {
        "primary": ["weekly_report", "meeting", "acceptance_launch"],
        "secondary": ["baseline", "contract_tender", "requirement_change"],
        "keywords": ["当前", "进展", "本周", "完成", "推进", "迁移", "表单", "调研"],
        "min_authority": 5,
    },
    "risks_coordination": {
        "primary": ["weekly_report", "meeting", "acceptance_launch"],
        "secondary": ["baseline", "contract_tender", "requirement_change", "resource"],
        "keywords": ["风险", "问题", "协调", "待协调", "影响", "资源", "瓶颈", "争议"],
        "min_authority": 5,
    },
    "meeting_decisions": {
        "primary": ["meeting"],
        "secondary": ["weekly_report", "acceptance_launch", "baseline"],
        "keywords": ["会议", "纪要", "决议", "结论", "达成共识", "议题"],
        "min_authority": 5,
    },
    "change_requests": {
        "primary": ["requirement_change", "contract_tender"],
        "secondary": ["meeting", "weekly_report", "acceptance_launch", "baseline"],
        "keywords": ["变更", "调整", "优化", "确认", "需求", "范围", "工期", "费用"],
        "min_authority": 4,
    },
    "deliverables_acceptance": {
        "primary": ["baseline", "contract_tender", "requirement_change"],
        "secondary": ["meeting", "weekly_report", "acceptance_launch"],
        "keywords": ["交付", "交付物", "验收", "报告", "手册", "数据字典", "测试", "测评"],
        "min_authority": 4,
        "pin_baseline": True,
    },
    "resources_deployment": {
        "primary": ["resource", "contract_tender", "requirement_change"],
        "secondary": ["meeting", "weekly_report", "baseline"],
        "keywords": ["资源", "服务器", "部署", "数据库", "中间件", "环境", "国产化", "集群"],
        "min_authority": 5,
    },
}

BASELINE_SOURCE = {
    "documentId": None,
    "documentName": "项目计划基线",
    "sourceType": "baseline",
    "authorityLevel": 3,
    "authorityLabel": "项目计划基线",
    "authorityScore": 78,
    "isPrimaryBasis": True,
}

_wiki_job_lock = threading.Lock()
_wiki_job = {
    "running": False,
    "progress": 0,
    "total": len(WIKI_PAGES),
    "created": 0,
    "error": "",
    "startedAt": "",
    "finishedAt": "",
}


def _set_job(**patch) -> None:
    with _wiki_job_lock:
        _wiki_job.update(patch)


def wiki_job_status() -> dict[str, Any]:
    with _wiki_job_lock:
        return dict(_wiki_job)


def ensure_wiki_pages(conn=None) -> None:
    owns_connection = conn is None
    conn = conn or get_connection()
    now = now_iso()
    try:
        for page_key, title in WIKI_PAGES:
            conn.execute(
                """
                INSERT INTO wiki_pages(page_key, title, content, source_json, status, updated_at)
                VALUES(?, ?, '', '[]', 'draft', ?)
                ON CONFLICT(page_key) DO NOTHING
                """,
                (page_key, title, now),
            )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def list_wiki_pages() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        ensure_wiki_pages(conn)
        rows = rows_to_dicts(conn.execute("SELECT * FROM wiki_pages ORDER BY id").fetchall())
        for row in rows:
            try:
                row["sources"] = json.loads(row.pop("source_json") or "[]")
            except Exception:
                row["sources"] = []
        return rows
    finally:
        conn.close()


def _document_text(conn, doc: dict[str, Any]) -> str:
    ocr_text = combined_ocr_text(int(doc["id"]), conn)
    if ocr_text:
        return ocr_text
    rows = rows_to_dicts(
        conn.execute(
            "SELECT text FROM knowledge_chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc["id"],),
        ).fetchall()
    )
    return compact_text("\n".join(row["text"] for row in rows), 18000)


def _source_type(doc: dict[str, Any]) -> str:
    category = doc.get("doc_category") or "other"
    if category == "contract_tender":
        name = doc.get("name") or ""
        if "需求" in name:
            return "requirement_change"
    return category


def _baseline_text(conn, page_key: str = "") -> str:
    goals = get_setting(conn, "project_goals", []) or []
    breakdown = get_setting(conn, "project_plan_breakdown", []) or []
    version = get_setting(conn, "project_plan_version", "") or ""
    parts = [f"项目计划基线版本：{version}"]
    if goals:
        parts.append("建设目标：")
        parts.extend(f"- {goal}" for goal in goals)
    if breakdown:
        parts.append("阶段计划：")
        for phase in breakdown:
            parts.append(
                f"- {phase.get('phase', '')}（{phase.get('period', '')}）：{phase.get('objective', '')}"
            )
            for work in (phase.get("work") or [])[:4]:
                parts.append(f"  - {work}")
            for deliverable in (phase.get("deliverables") or [])[:4]:
                parts.append(f"  - 交付物：{deliverable}")
    return compact_text("\n".join(parts), 12000)


def _baseline_doc(conn, page_key: str) -> dict[str, Any]:
    text = _baseline_text(conn, page_key)
    return {
        "id": None,
        "name": "项目计划基线",
        "doc_category": "baseline",
        "source_type": "baseline",
        "text": text,
        "authority_level": 3,
        "authority_score": 78,
        "modified_at": get_setting(conn, "project_plan_version", ""),
        "knowledge_status": "baseline",
        "status": "baseline",
    }


def _load_documents_for_wiki(conn) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM documents
            WHERE status = 'indexed' OR ocr_status IN ('completed', 'partial')
            ORDER BY authority_level, authority_score DESC, modified_at DESC, id DESC
            """
        ).fetchall()
    )
    docs = []
    for doc in rows:
        text = _document_text(conn, doc)
        if not text:
            continue
        doc["text"] = text
        doc["source_type"] = _source_type(doc)
        docs.append(doc)
    return docs


def _keyword_score(text: str, keywords: list[str]) -> float:
    if not text:
        return 0
    score = 0.0
    for keyword in keywords:
        if keyword in text:
            score += 1.0 + min(text.count(keyword), 5) * 0.25
    return score


def _source_payload(doc: dict[str, Any], snippet: str, primary: bool, note: str = "") -> dict[str, Any]:
    if doc.get("source_type") == "baseline":
        return {
            **BASELINE_SOURCE,
            "snippet": compact_text(snippet, 260),
            "isPrimaryBasis": primary,
            "note": note,
        }
    level = int(doc.get("authority_level") or 5)
    return {
        "documentId": doc.get("id"),
        "documentName": doc.get("name"),
        "sourceType": doc.get("source_type") or doc.get("doc_category") or "other",
        "authorityLevel": level,
        "authorityLabel": authority_label(level),
        "authorityScore": float(doc.get("authority_score") or 45),
        "isPrimaryBasis": primary,
        "snippet": compact_text(snippet, 260),
        "note": note,
    }


def _select_page_sources(conn, docs: list[dict[str, Any]], page_key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    strategy = PAGE_SOURCE_STRATEGIES.get(page_key, PAGE_SOURCE_STRATEGIES["overview"])
    baseline = _baseline_doc(conn, page_key)
    candidates = [baseline, *docs]
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in candidates:
        source_type = doc.get("source_type") or _source_type(doc)
        if source_type not in set(strategy["primary"] + strategy["secondary"]):
            continue
        level = int(doc.get("authority_level") or 5)
        authority_bonus = max(0, 7 - level) * 1.2 + float(doc.get("authority_score") or 45) / 100
        source_bonus = 4.0 if source_type in strategy["primary"] else 1.4
        keyword_bonus = _keyword_score(f"{doc.get('name', '')}\n{doc.get('text', '')}", strategy["keywords"])
        if keyword_bonus <= 0 and source_type != "baseline":
            keyword_bonus = 0.2
        scored.append((source_bonus + authority_bonus + keyword_bonus, doc))
    ranked = [doc for _, doc in sorted(scored, reverse=True, key=lambda item: item[0])]
    primary = [doc for doc in ranked if (doc.get("source_type") or _source_type(doc)) in strategy["primary"]][:5]
    if strategy.get("pin_baseline") and baseline.get("text") and baseline not in primary:
        primary = [baseline, *primary[:4]]
    secondary = [doc for doc in ranked if doc not in primary][:4]
    warnings: list[str] = []
    high_authority = [doc for doc in primary if int(doc.get("authority_level") or 5) <= strategy.get("min_authority", 5)]
    if not high_authority:
        warnings.append("缺少高权威主依据，建议补充合同、需求、招投标或正式确认资料后再固化本页。")
    return primary, secondary, warnings


def _matched_snippet(doc: dict[str, Any], page_key: str) -> str:
    strategy = PAGE_SOURCE_STRATEGIES.get(page_key, PAGE_SOURCE_STRATEGIES["overview"])
    text = doc.get("text") or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matched = [line for line in lines if any(keyword in line for keyword in strategy["keywords"])]
    if not matched:
        matched = lines[:10]
    return compact_text("\n".join(matched[:12]), 1400)


def _fallback_wiki_content(title: str, docs: list[dict[str, Any]], page_key: str) -> tuple[str, list[dict[str, Any]]]:
    conn = get_connection()
    try:
        primary, secondary, warnings = _select_page_sources(conn, docs, page_key)
    finally:
        conn.close()

    if not primary and not secondary:
        return "暂无可用资料。请确认项目资料已完成正文抽取或 OCR。", []

    parts = [
        title,
        "本页由系统按页面主题、资料权威层级和内容匹配度整理，管理员确认后写入正式 Wiki。",
    ]
    sources: list[dict[str, Any]] = []
    if warnings:
        parts.append("来源治理提醒：")
        parts.extend(f"- {warning}" for warning in warnings)
    if primary:
        parts.append("主依据：")
        for doc in primary:
            snippet = _matched_snippet(doc, page_key)
            parts.append(f"\n来源：{doc.get('name')}\n{snippet}")
            sources.append(_source_payload(doc, snippet, True))
    if secondary:
        parts.append("\n补充依据：")
        for doc in secondary:
            snippet = _matched_snippet(doc, page_key)
            parts.append(f"\n来源：{doc.get('name')}\n{snippet}")
            sources.append(_source_payload(doc, snippet, False))
    parts.append("\n待确认事项：以上内容为系统自动整理建议，正式写入前请确认来源是否充分、是否存在正式变更文件。")
    return compact_text("\n".join(parts), 16000), sources


def _load_wiki_materials() -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    conn = get_connection()
    try:
        ensure_wiki_pages(conn)
        docs = _load_documents_for_wiki(conn)
        source_text = compact_text("\n\n".join(f"文件ID {doc.get('id')}：{doc.get('name')}\n{doc.get('text')}" for doc in docs), 30000)
        sources = [{"documentId": doc.get("id"), "documentName": doc.get("name")} for doc in docs]
        return docs, source_text, sources
    finally:
        conn.close()


def _clear_pending_wiki_suggestions() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM wiki_suggestions WHERE status = 'pending'")
        conn.commit()
    finally:
        conn.close()


def _insert_wiki_suggestion(page_key: str, title: str, content: str, sources: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO wiki_suggestions(page_key, title, content, source_json, status, created_at)
            VALUES(?, ?, ?, ?, 'pending', ?)
            """,
            (page_key, title, content, json.dumps(sources[:10], ensure_ascii=False), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _generate_one_suggestion(page_key: str, title: str, docs: list[dict[str, Any]], source_text: str, sources: list[dict[str, Any]]) -> bool:
    content, fallback_sources = _fallback_wiki_content(title, docs, page_key)
    _insert_wiki_suggestion(page_key, f"{title}（来源治理整理）", content, fallback_sources)
    return True


def rebuild_wiki_suggestions() -> dict[str, Any]:
    docs, source_text, sources = _load_wiki_materials()
    _clear_pending_wiki_suggestions()
    created = 0
    for page_key, title in WIKI_PAGES:
        if _generate_one_suggestion(page_key, title, docs, source_text, sources):
            created += 1
    return {"ok": True, "created": created}


def _wiki_worker() -> None:
    _set_job(running=True, progress=0, total=len(WIKI_PAGES), created=0, error="", startedAt=now_iso(), finishedAt="")
    try:
        docs, source_text, sources = _load_wiki_materials()
        _clear_pending_wiki_suggestions()
        created = 0
        for index, (page_key, title) in enumerate(WIKI_PAGES, start=1):
            if _generate_one_suggestion(page_key, title, docs, source_text, sources):
                created += 1
            _set_job(progress=index, created=created)
            time.sleep(0.1)
        _set_job(running=False, finishedAt=now_iso())
    except Exception as exc:
        _set_job(running=False, error=str(exc), finishedAt=now_iso())


def start_wiki_rebuild_job() -> dict[str, Any]:
    with _wiki_job_lock:
        if _wiki_job["running"]:
            return {"ok": True, "alreadyRunning": True, **dict(_wiki_job)}
    _set_job(running=True, progress=0, total=len(WIKI_PAGES), created=0, error="", startedAt=now_iso(), finishedAt="")
    thread = threading.Thread(target=_wiki_worker, daemon=True)
    thread.start()
    return {"ok": True, "started": True, **wiki_job_status()}


def list_wiki_suggestions(status: str = "pending") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT * FROM wiki_suggestions WHERE status = ? ORDER BY created_at DESC, id DESC",
                (status,),
            ).fetchall()
        )
        for row in rows:
            try:
                row["sources"] = json.loads(row.pop("source_json") or "[]")
            except Exception:
                row["sources"] = []
        return rows
    finally:
        conn.close()


def apply_wiki_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        suggestion = row_to_dict(conn.execute("SELECT * FROM wiki_suggestions WHERE id = ?", (suggestion_id,)).fetchone())
        if not suggestion:
            return {"ok": False, "message": "Wiki 建议不存在"}
        if suggestion["status"] != "pending":
            return {"ok": False, "message": "该 Wiki 建议不是待确认状态"}
        now = now_iso()
        conn.execute(
            """
            INSERT INTO wiki_pages(page_key, title, content, source_json, status, updated_at)
            VALUES(?, ?, ?, ?, 'published', ?)
            ON CONFLICT(page_key) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                source_json = excluded.source_json,
                status = 'published',
                updated_at = excluded.updated_at
            """,
            (suggestion["page_key"], suggestion["title"], suggestion["content"], suggestion["source_json"], now),
        )
        conn.execute("UPDATE wiki_suggestions SET status = 'applied', applied_at = ? WHERE id = ?", (now, suggestion_id))
        conn.commit()
        return {"ok": True}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def apply_all_wiki_suggestions() -> dict[str, Any]:
    conn = get_connection()
    try:
        ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM wiki_suggestions WHERE status = 'pending' ORDER BY created_at, id"
            ).fetchall()
        ]
    finally:
        conn.close()

    applied = 0
    failed: list[dict[str, Any]] = []
    for suggestion_id in ids:
        result = apply_wiki_suggestion(int(suggestion_id))
        if result.get("ok"):
            applied += 1
        else:
            failed.append({"id": suggestion_id, "message": result.get("message", "应用失败")})
    return {"ok": not failed, "applied": applied, "failed": failed, "total": len(ids)}


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _question_terms(question: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", question or "")
    stop_words = {"请问", "一个", "什么", "哪些", "怎么", "如何", "是否", "项目", "目前", "现在"}
    business_terms = [
        "建设目标",
        "项目目标",
        "目标",
        "范围",
        "建设内容",
        "采购需求",
        "合同",
        "需求",
        "计划",
        "里程碑",
        "工期",
        "上线",
        "试运行",
        "进度",
        "当前进度",
        "验收",
        "交付物",
        "交付",
        "风险",
        "问题",
        "协调",
        "会议",
        "纪要",
        "决议",
        "变更",
        "资源",
        "部署",
        "环境",
    ]
    matched = [term for term in business_terms if term in question]
    return _dedupe([*matched, *[term for term in terms if term not in stop_words]])


def retrieve_wiki(question: str) -> list[dict[str, Any]]:
    terms = _question_terms(question)
    intent_pages = _intent_pages(question)
    conn = get_connection()
    try:
        pages = rows_to_dicts(conn.execute("SELECT * FROM wiki_pages WHERE status = 'published' OR content != '' ORDER BY id").fetchall())
    finally:
        conn.close()
    hits = []
    for page in pages:
        text = f"{page['title']}\n{page.get('content') or ''}"
        score = sum(text.count(term) for term in terms) if terms else 0
        if page.get("page_key") in intent_pages:
            score += 8 if page.get("page_key") == intent_pages[0] else 5
        if score:
            hits.append({**page, "score": score})
    return sorted(hits, reverse=True, key=lambda item: item["score"])[:4]


def _intent_pages(question: str) -> list[str]:
    if any(word in question for word in ["建设目标", "目标", "范围", "建设内容"]):
        return ["goals_scope", "overview"]
    if any(word in question for word in ["计划", "里程碑", "工期", "上线", "试运行"]):
        return ["plan_milestones", "overview"]
    if any(word in question for word in ["进度", "当前", "完成", "本周", "下周"]):
        return ["current_progress", "risks_coordination"]
    if any(word in question for word in ["验收", "交付", "交付物", "报告", "手册"]):
        return ["deliverables_acceptance", "goals_scope"]
    if any(word in question for word in ["风险", "问题", "协调"]):
        return ["risks_coordination", "current_progress"]
    if any(word in question for word in ["会议", "决议", "纪要"]):
        return ["meeting_decisions"]
    if any(word in question for word in ["变更", "调整", "优化"]):
        return ["change_requests"]
    if any(word in question for word in ["资源", "服务器", "部署", "环境"]):
        return ["resources_deployment"]
    return ["overview", "goals_scope"]


def _clean_answer_text(value: str) -> str:
    return re.sub(r"[#*`]+", "", str(value or "")).strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[\n。；;，,！？!?]+", text or "")
    return [_clean_answer_text(part) for part in parts if _clean_answer_text(part)]


def _source_from_page(page: dict[str, Any], snippet: str) -> dict[str, Any]:
    sources = []
    try:
        sources = json.loads(page.get("source_json") or "[]")
    except Exception:
        sources = []
    primary = next((item for item in sources if item.get("isPrimaryBasis")), None)
    if primary:
        return {**primary, "snippet": primary.get("snippet") or snippet}
    return {
        "documentId": None,
        "documentName": page.get("title") or "项目 Wiki",
        "snippet": snippet,
        "sourceType": "wiki",
        "authorityLabel": "项目 Wiki",
        "authorityLevel": 4,
        "authorityScore": 65,
        "isPrimaryBasis": False,
    }


def _local_wiki_answer(question: str, pages: list[dict[str, Any]], fallback_sources: list[dict[str, Any]], reason: str = "") -> dict[str, Any]:
    terms = _question_terms(question)
    selected: list[tuple[dict[str, Any], str]] = []
    seen = set()
    for page in pages:
        sentences = _split_sentences(f"{page.get('title') or ''}\n{page.get('content') or ''}")
        matched = [sentence for sentence in sentences if any(term in sentence for term in terms)]
        if not matched:
            matched = sentences[:5]
        for sentence in matched:
            key = sentence[:80]
            if key in seen:
                continue
            seen.add(key)
            selected.append((page, compact_text(sentence, 240)))
            if len(selected) >= 8:
                break
        if len(selected) >= 8:
            break

    if not selected:
        return {
            "answer": "未在项目资料中找到依据。",
            "structured": {
                "answer_summary": "未在项目资料中找到依据。",
                "key_points": [],
                "evidence": [],
                "unknowns": ["当前 Wiki 中没有检索到可直接支撑该问题的内容。"],
            },
            "sources": fallback_sources,
            "retrievalMode": "wiki",
        }

    titles = []
    for page, _ in selected:
        title = _clean_answer_text(page.get("title") or "项目 Wiki")
        if title and title not in titles:
            titles.append(title)
    answer_summary = f"根据项目 Wiki 中的{'、'.join(titles[:3])}，可确认与该问题相关的要点如下。"
    key_points = [snippet for _, snippet in selected[:5]]
    evidence = [f"{_clean_answer_text(page.get('title') or '项目 Wiki')}：{snippet}" for page, snippet in selected[:5]]
    unknowns = []
    if reason:
        unknowns.append("模型当前不可用，本次回答由系统基于已发布 Wiki 内容本地整理。")
    unknowns.append("如需形成正式结论，建议结合来源文件原文进一步确认。")

    sources = []
    for page, snippet in selected[:6]:
        source = _source_from_page(page, snippet)
        sources.append(source)
    if fallback_sources:
        for source in fallback_sources:
            if not any(item.get("documentId") == source.get("documentId") and item.get("documentName") == source.get("documentName") for item in sources):
                sources.append(source)
            if len(sources) >= 8:
                break
    return {
        "answer": answer_summary,
        "structured": {
            "answer_summary": answer_summary,
            "key_points": key_points,
            "evidence": evidence,
            "unknowns": unknowns,
        },
        "sources": sources,
        "retrievalMode": "wiki",
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("模型返回为空")
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("模型未返回 JSON 对象")
    return data


def _rank_fallback_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        sources,
        key=lambda source: (
            0 if source.get("isPrimaryBasis") else 1,
            int(source.get("authorityLevel") or 5),
            -float(source.get("authorityScore") or 0),
        ),
    )


def _normalize_model_source(source: dict[str, Any], ranked_sources: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(source.get("documentName") or source.get("title") or "项目 Wiki")
    matched = next(
        (
            item
            for item in ranked_sources
            if item.get("documentName") and item.get("documentName") == name
        ),
        None,
    )
    if matched:
        return {
            **matched,
            "snippet": source.get("snippet") or matched.get("snippet") or "",
        }
    if "项目计划基线" in name or source.get("sourceType") == "baseline":
        return {
            "documentId": None,
            "documentName": "项目计划基线",
            "snippet": source.get("snippet") or "",
            "sourceType": "baseline",
            "authorityLabel": "项目计划基线",
            "authorityLevel": 3,
            "authorityScore": 78,
            "isPrimaryBasis": True,
        }
    return {
        "documentId": source.get("documentId"),
        "documentName": name,
        "snippet": source.get("snippet") or "",
        "sourceType": source.get("sourceType") or "wiki",
        "authorityLabel": source.get("authorityLabel") or "项目 Wiki",
        "authorityLevel": source.get("authorityLevel") or 4,
        "authorityScore": source.get("authorityScore") or 65,
        "isPrimaryBasis": bool(source.get("isPrimaryBasis")),
    }


def answer_from_wiki(question: str, fallback_sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    pages = retrieve_wiki(question)
    if not pages:
        return None
    ranked_fallback_sources = _rank_fallback_sources(fallback_sources)
    context = "\n\n".join(
        f"Wiki 来源{index + 1}：{page['title']}\n{compact_text(page.get('content') or '', 1800)}"
        for index, page in enumerate(pages)
    )
    authority_context = "\n\n".join(
        f"权威来源{index + 1}：{source.get('documentName')}（{source.get('authorityLabel') or source.get('sourceType') or '项目资料'}）\n{compact_text(source.get('snippet') or '', 900)}"
        for index, source in enumerate(ranked_fallback_sources[:6])
        if source.get("snippet")
    )
    prompt = f"""
请只依据以下 Wiki 和项目资料来源回答问题，输出 JSON 对象：
answer_summary: 一段简明结论
key_points: 字符串数组，列出具体要点
evidence: 字符串数组，说明依据
unknowns: 字符串数组，说明资料不足或待确认事项
sources: 数组，每项包含 documentId、documentName、snippet
不要输出 Markdown，不要使用 # 或 *。
涉及目标、范围、计划、验收、交付物时，必须优先使用“权威来源片段”中的合同、需求、招投标和项目计划基线；会议纪要、周报只作过程补充。
如果 Wiki 与权威来源片段表述不同，以权威来源片段为主，并在 unknowns 说明需确认是否已形成正式变更。
问题：{question}

权威来源片段：
{authority_context or "无"}

Wiki 资料：
{context}
"""
    try:
        answer = chat_completion(
            [
                {"role": "system", "content": "你是项目知识库问答助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
        )
        data = _parse_json_object(answer)
    except Exception as exc:
        return _local_wiki_answer(question, pages, fallback_sources, str(exc))

    normalized_sources = []
    raw_sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        normalized_sources.append(_normalize_model_source(source, ranked_fallback_sources))
    if not normalized_sources:
        for source in ranked_fallback_sources[:4]:
            normalized_sources.append(source)
        for page in pages[:4]:
            normalized_sources.append(_source_from_page(page, compact_text(page.get("content") or "", 240)))
    for source in ranked_fallback_sources:
        if not any(item.get("documentId") == source.get("documentId") and item.get("documentName") == source.get("documentName") for item in normalized_sources):
            normalized_sources.append(source)
        if len(normalized_sources) >= 8:
            break

    return {
        "answer": data.get("answer_summary") or "未在项目资料中找到依据。",
        "structured": {
            "answer_summary": data.get("answer_summary") or "",
            "key_points": data.get("key_points") if isinstance(data.get("key_points"), list) else [],
            "evidence": data.get("evidence") if isinstance(data.get("evidence"), list) else [],
            "unknowns": data.get("unknowns") if isinstance(data.get("unknowns"), list) else [],
        },
        "sources": normalized_sources,
        "retrievalMode": "wiki",
    }
