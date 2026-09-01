from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from typing import Any

from app.database import get_connection, get_setting, now_iso, row_to_dict, rows_to_dicts
from app.services.authority import authority_label
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
        "keywords": ["项目", "概览", "合同", "采购", "需求", "建设", "范围", "目标", "合同金额", "合同总额", "实施周期", "验收"],
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

PAGE_GUIDANCE = {
    "overview": "概括项目背景、建设内容、合同边界、当前阶段和总体状态，不罗列目录或一般性条款。",
    "goals_scope": "明确建设目标、系统范围、数据迁移、系统集成和部署边界。合同与需求优先，过程资料不得改写正式范围。",
    "plan_milestones": "整理合同工期、计划基线、关键里程碑和当前计划偏差，区分正式约束与内部计划。",
    "current_progress": "以最新周报和阶段汇报为主，概括已完成、进行中、下一步和进度偏差，不用旧会议覆盖最新进展。",
    "risks_coordination": "区分风险事件、影响、应对措施和待协调事项，优先使用最新周报与明确会议结论。",
    "meeting_decisions": "只提取已经明确形成的会议决议、责任主体、期限和待办，不把讨论过程写成正式结论。",
    "change_requests": "区分正式变更、待确认变化和一般优化建议；没有正式文件时不得声称合同范围已变更。",
    "deliverables_acceptance": "整理合同、需求和招投标要求的交付物、验收条件、测评要求与当前准备情况。",
    "resources_deployment": "整理服务器、操作系统、数据库、中间件、网络、安全和高可用部署要求，并标明待确认资源。",
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
    "modelSucceeded": 0,
    "fallbackCount": 0,
    "currentPage": "",
    "generationId": "",
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
    name = doc.get("name") or ""
    if any(
        word in name.lower()
        for word in [
            "二维码", "激活校验码", "产品镜像", "安装包", "授权码",
            "密码", "password", "api_key", "apikey", "license"
        ]
    ):
        return "other"
    if "请示" in name:
        return "other"
    if "周报" in name:
        return "weekly_report"
    if any(word in name for word in ["会议纪要", "会议记录", "周例会"]):
        return "meeting"
    if any(word in name for word in ["阶段性工作汇报", "阶段汇报", "工作汇报"]):
        return "acceptance_launch"
    if any(word in name for word in ["服务器资源", "资源需求", "资源清单"]):
        return "resource"
    if "需求" in name and not any(word in name for word in ["资源需求", "服务器资源"]):
        return "requirement_change"
    if any(word in name for word in ["合同", "招标", "投标"]):
        return "contract_tender"
    category = doc.get("doc_category") or "other"
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


def _logical_version_key(doc: dict[str, Any]) -> str:
    explicit = (doc.get("version_group") or "").strip()
    if explicit:
        return explicit
    name = re.sub(r"\.[^.]+$", "", doc.get("name") or "").lower()
    if doc.get("doc_category") in {"contract_tender", "requirement_change"}:
        name = re.sub(r"初稿|拟定稿|草稿|征求意见稿|送审稿|正式稿|最终稿|修订稿", "", name)
        name = re.sub(r"20\d{6}|20\d{2}[-_.]\d{1,2}[-_.]\d{1,2}|v\d+(?:\.\d+)*", "", name)
    else:
        name = re.sub(r"[（(]\d+[）)]$", "", name)
    name = re.sub(r"[\s_\-—()（）\[\]【】]+", "", name)
    return f"{doc.get('doc_category') or 'other'}:{name}" if name else f"document-{doc['id']}"


def _document_preference(doc: dict[str, Any]) -> float:
    name = doc.get("name") or ""
    score = (7 - int(doc.get("authority_level") or 5)) * 10
    score += float(doc.get("authority_score") or 45) / 10
    if "合同" in name and not any(word in name for word in ["招标", "投标", "需求"]):
        score += 12
    if "需求" in name:
        score += 8
    if "中标" in name or "投标响应" in name:
        score += 6
    if any(word in name for word in ["正式稿", "最终稿"]):
        score += 5
    if any(word in name for word in ["初稿", "拟定稿", "草稿", "征求意见"]):
        score -= 12
    if doc.get("primary_version_id") and int(doc.get("primary_version_id")) == int(doc["id"]):
        score += 20
    return score


def _load_documents_for_wiki(conn) -> list[dict[str, Any]]:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM documents
            WHERE is_current = 1
              AND (status = 'indexed' OR ocr_status IN ('completed', 'partial'))
            ORDER BY authority_level, authority_score DESC, modified_at DESC, id DESC
            """
        ).fetchall()
    )
    rows.sort(key=_document_preference, reverse=True)
    docs = []
    seen_version_groups: set[str] = set()
    seen_content_hashes: set[str] = set()
    for doc in rows:
        version_group = _logical_version_key(doc)
        if version_group in seen_version_groups:
            continue
        content_hash = (doc.get("content_hash") or "").strip()
        if content_hash and content_hash in seen_content_hashes:
            continue
        text = _document_text(conn, doc)
        if not text:
            continue
        doc["text"] = text
        doc["source_type"] = _source_type(doc)
        docs.append(doc)
        seen_version_groups.add(version_group)
        if content_hash:
            seen_content_hashes.add(content_hash)
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
    source_type = doc.get("source_type") or doc.get("doc_category") or "other"
    source_labels = {
        "weekly_report": "周报/过程资料",
        "meeting": "会议纪要/决议",
        "acceptance_launch": "阶段汇报/过程确认",
        "requirement_change": "需求/正式确认文件",
        "resource": "资源/部署资料",
    }
    return {
        "documentId": doc.get("id"),
        "documentName": doc.get("name"),
        "sourceType": source_type,
        "authorityLevel": level,
        "authorityLabel": source_labels.get(source_type, authority_label(level)),
        "authorityScore": float(doc.get("authority_score") or 45),
        "isPrimaryBasis": primary,
        "snippet": compact_text(snippet, 260),
        "note": note,
    }


def _document_date_value(doc: dict[str, Any]) -> int:
    text = f"{doc.get('name', '')} {doc.get('effective_date', '')} {doc.get('modified_at', '')}"
    matches = re.findall(r"(20\d{2})[-_.年]?([01]\d)[-_.月]?([0-3]\d)", text)
    if not matches:
        return 0
    values = []
    for year, month, day in matches:
        try:
            values.append(datetime(int(year), int(month), int(day)).toordinal())
        except ValueError:
            continue
    return max(values, default=0)


def _page_name_bonus(doc: dict[str, Any], page_key: str) -> float:
    name = doc.get("name") or ""
    bonus = 0.0
    if "合同" in name and page_key in {"overview", "goals_scope", "plan_milestones", "change_requests", "deliverables_acceptance"}:
        bonus += 5.0
    if "需求" in name and page_key in {"overview", "goals_scope", "change_requests", "deliverables_acceptance", "resources_deployment"}:
        bonus += 5.0
    if any(word in name for word in ["中标", "投标响应"]):
        bonus += 2.0
    if "周报" in name and page_key in {"current_progress", "risks_coordination"}:
        bonus += 3.0
    if "会议纪要" in name and page_key == "meeting_decisions":
        bonus += 3.0
    return bonus


def _select_page_sources(conn, docs: list[dict[str, Any]], page_key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    strategy = PAGE_SOURCE_STRATEGIES.get(page_key, PAGE_SOURCE_STRATEGIES["overview"])
    baseline = _baseline_doc(conn, page_key)
    candidates = [baseline, *docs]
    allowed = set(strategy["primary"] + strategy["secondary"])
    eligible = [doc for doc in candidates if (doc.get("source_type") or _source_type(doc)) in allowed]
    dated = sorted(
        [doc for doc in eligible if _document_date_value(doc)],
        key=_document_date_value,
        reverse=True,
    )
    recency_bonus = {str(doc.get("id") or "baseline"): max(0.0, 6.0 - index * 0.7) for index, doc in enumerate(dated[:9])}
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in eligible:
        source_type = doc.get("source_type") or _source_type(doc)
        level = int(doc.get("authority_level") or 5)
        authority_bonus = max(0, 7 - level) * 1.2 + float(doc.get("authority_score") or 45) / 100
        if source_type in strategy["primary"]:
            source_bonus = 5.2 - strategy["primary"].index(source_type) * 0.7
        else:
            source_bonus = 1.6 - strategy["secondary"].index(source_type) * 0.15
        matched_text = _matched_snippet(doc, page_key)
        keyword_bonus = _keyword_score(f"{doc.get('name', '')}\n{matched_text}", strategy["keywords"])
        if keyword_bonus <= 0 and source_type != "baseline":
            keyword_bonus = 0.2
        freshness = recency_bonus.get(str(doc.get("id") or "baseline"), 0.0) if page_key in {
            "current_progress", "risks_coordination", "meeting_decisions"
        } else 0.0
        draft_penalty = 7.0 if any(word in (doc.get("name") or "") for word in ["初稿", "拟定稿", "草稿", "征求意见"]) else 0.0
        scored.append((source_bonus + authority_bonus + keyword_bonus + freshness + _page_name_bonus(doc, page_key) - draft_penalty, doc))
    ranked = [doc for _, doc in sorted(scored, reverse=True, key=lambda item: item[0])]
    primary_candidates = [doc for doc in ranked if (doc.get("source_type") or _source_type(doc)) in strategy["primary"]]
    non_draft_primary = [
        doc for doc in primary_candidates
        if not any(word in (doc.get("name") or "") for word in ["初稿", "拟定稿", "草稿", "征求意见"])
    ]
    primary = (non_draft_primary if len(non_draft_primary) >= 3 else primary_candidates)[:4]
    if strategy.get("pin_baseline") and baseline.get("text") and baseline not in primary:
        primary = [baseline, *primary[:3]]
    primary.sort(
        key=lambda doc: (
            0 if int(doc.get("authority_level") or 5) <= 2 else 1,
            0 if doc.get("source_type") == "baseline" else 1,
            -_document_date_value(doc),
        )
    )
    secondary_types = set(strategy["secondary"])
    if page_key in {"current_progress", "risks_coordination", "meeting_decisions"}:
        secondary_types.update(strategy["primary"])
    secondary = [
        doc for doc in ranked
        if doc not in primary and (doc.get("source_type") or _source_type(doc)) in secondary_types
    ][:2]
    warnings: list[str] = []
    high_authority = [doc for doc in primary if int(doc.get("authority_level") or 5) <= strategy.get("min_authority", 5)]
    if not high_authority:
        warnings.append("缺少高权威主依据，建议补充合同、需求、招投标或正式确认资料后再固化本页。")
    if any(any(word in (doc.get("name") or "") for word in ["初稿", "拟定稿", "草稿"]) for doc in primary):
        warnings.append("主依据仍包含未确认版本，建议先在数据治理中确认正式版本。")
    return primary, secondary, warnings


def _matched_snippet(doc: dict[str, Any], page_key: str) -> str:
    strategy = PAGE_SOURCE_STRATEGIES.get(page_key, PAGE_SOURCE_STRATEGIES["overview"])
    text = doc.get("text") or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    scored: list[tuple[float, int, str]] = []
    for index, line in enumerate(lines):
        if any(
            phrase in line
            for phrase in [
                "智慧经营平台", "广州交易集团", "科学城（广州）建设发展集团",
                "本授权书声明", "经营范围：", "本合同生效日起十个工作日内"
            ]
        ):
            continue
        keyword_hits = sum(1 for keyword in strategy["keywords"] if keyword in line)
        if not keyword_hits:
            continue
        score = keyword_hits * 3 + min(len(line), 240) / 120
        if any(phrase in line for phrase in ["合同金额", "合同总额", "实施周期", "建设目标", "建设内容", "验收要求"]):
            score += 6
        if re.search(r"\.{5,}|…{3,}|\|\s*\d+\s*$", line):
            score -= 5
        if len(line) < 8:
            score -= 2
        scored.append((score, index, line))
    best = sorted(scored, reverse=True)[:12]
    matched = [compact_text(line, 420) for _, _, line in best]
    if not matched:
        matched = [line for line in lines[:12] if not re.search(r"\.{5,}|…{3,}", line)]
    return compact_text("\n".join(matched), 1500)


def _fallback_from_selected(
    title: str,
    page_key: str,
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    if not primary and not secondary:
        return "暂无可用资料。请确认项目资料已完成正文抽取或 OCR。", []

    parts = [
        title,
        "本页为规则降级整理结果，管理员确认后方可写入正式 Wiki。",
    ]
    sources: list[dict[str, Any]] = []
    if warnings:
        parts.append("来源治理提醒：")
        parts.extend(f"- {warning}" for warning in warnings)
    if primary:
        parts.append("主依据摘要：")
        for index, doc in enumerate(primary, start=1):
            snippet = _matched_snippet(doc, page_key)
            parts.append(f"{index}. {doc.get('name')}：{compact_text(snippet, 480)}")
            sources.append({**_source_payload(doc, snippet, True), "sourceRef": f"S{index}"})
    if secondary:
        parts.append("补充依据摘要：")
        for offset, doc in enumerate(secondary, start=len(primary) + 1):
            snippet = _matched_snippet(doc, page_key)
            parts.append(f"{offset}. {doc.get('name')}：{compact_text(snippet, 360)}")
            sources.append({**_source_payload(doc, snippet, False), "sourceRef": f"S{offset}"})
    parts.append("待确认事项：模型本轮未能完成归纳，请结合来源文件确认后再应用。")
    return compact_text("\n".join(parts), 6000), sources


def _fallback_wiki_content(title: str, docs: list[dict[str, Any]], page_key: str) -> tuple[str, list[dict[str, Any]]]:
    conn = get_connection()
    try:
        primary, secondary, warnings = _select_page_sources(conn, docs, page_key)
    finally:
        conn.close()
    return _fallback_from_selected(title, page_key, primary, secondary, warnings)


def _load_wiki_materials() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        ensure_wiki_pages(conn)
        docs = _load_documents_for_wiki(conn)
        return docs
    finally:
        conn.close()


def _replace_pending_wiki_suggestions(items: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM wiki_suggestions WHERE status = 'pending'")
        for item in items:
            conn.execute(
                """
                INSERT INTO wiki_suggestions(
                    page_key, title, content, source_json, generation_mode,
                    generation_error, strategy_json, generation_id, status, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    item["page_key"],
                    item["title"],
                    item["content"],
                    json.dumps(item["sources"][:10], ensure_ascii=False),
                    item["generation_mode"],
                    item.get("generation_error") or "",
                    json.dumps(item.get("strategy") or {}, ensure_ascii=False),
                    item.get("generation_id") or "",
                    item["created_at"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _wiki_prompt(
    page_key: str,
    title: str,
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    materials = []
    for index, doc in enumerate([*primary, *secondary], start=1):
        role = "主依据" if doc in primary else "补充依据"
        materials.append(
            f"[S{index}] {role}｜{doc.get('name')}｜{authority_label(int(doc.get('authority_level') or 5))}\n"
            f"{_matched_snippet(doc, page_key)}"
        )
    return f"""
请为“国产化OA集成项目管理系统”的 Wiki 页面“{title}”生成可审核的更新建议。
页面要求：{PAGE_GUIDANCE.get(page_key, '')}

必须遵守：
1. 只依据下列来源，不补造事实、日期、数量、责任人或结论。
2. 合同和正式需求优先于计划基线，计划基线优先于会议和周报；低权威资料不得覆盖高权威资料。
   合同正文中的金额、工期、付款和验收条款优先；采购需求中的预算或最高限价不得表述为合同成交金额。
3. 过程类页面优先使用最新周报/会议；如资料冲突，写入 conflicts 或 unknowns，不自行裁决为正式变更。
4. 过滤目录、页眉页脚、无关资格条款和重复表述；状态类信息必须注明来源时间，避免把旧进展写成当前状态。
5. 输出纯 JSON 对象，不要 Markdown、代码围栏、# 或 *。
6. 对变更需求页面，会议和周报中的变化必须写成“会议提出”或“待正式确认”，不得表述为已经形成正式变更。

JSON 字段：
summary: 1至3段准确、简洁的综合说明；
key_points: 4至10条具体事实，每条末尾可用【S1】标注依据；
current_status: 0至6条当前状态或进展，仅适用于该页面；
conflicts: 来源冲突数组；
unknowns: 资料不足或待确认数组。

来源治理提醒：{json.dumps(warnings, ensure_ascii=False)}
来源材料：
{chr(10).join(materials) or '无可用来源'}
""".strip()


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [compact_text(str(item), 500) for item in value if str(item).strip()][:limit]


def _significant_numbers(text: str) -> set[str]:
    normalized = (text or "").replace(",", "").replace("，", "")
    values = set(re.findall(r"(?<![A-Za-z])\d{2,}(?:\.\d+)?", normalized))
    values.update(
        match.group(1)
        for match in re.finditer(r"(?<![A-Za-z])(\d+(?:\.\d+)?)(?=万元|元|个月|天|日|年|套|台|个|份|%)", normalized)
    )
    return values


def _validate_model_grounding(data: dict[str, Any], selected: list[dict[str, Any]], page_key: str) -> None:
    context = "\n".join(
        f"{doc.get('name', '')}\n{_matched_snippet(doc, page_key)}" for doc in selected
    ).replace(",", "").replace("，", "")
    claims = json.dumps(data, ensure_ascii=False)
    unsupported = sorted(value for value in _significant_numbers(claims) if value not in context)
    if unsupported:
        raise ValueError(f"模型包含来源片段中不存在的数字：{', '.join(unsupported[:8])}")


def _character_bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
    return {cleaned[index : index + 2] for index in range(max(0, len(cleaned) - 1))}


def _ground_model_citations(data: dict[str, Any], selected: list[dict[str, Any]], page_key: str) -> None:
    source_grams = [
        _character_bigrams(f"{doc.get('name', '')}\n{_matched_snippet(doc, page_key)}") for doc in selected
    ]
    for field in ["key_points", "current_status", "conflicts", "unknowns"]:
        values = data.get(field)
        if not isinstance(values, list):
            continue
        grounded = []
        for value in values:
            text = re.sub(r"【S\d+(?:[、,，/]S?\d+)*】", "", str(value)).strip()
            grams = _character_bigrams(text)
            scores = [len(grams & candidate) for candidate in source_grams]
            ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
            refs = [index + 1 for index in ranked[: (2 if field == "conflicts" else 1)] if scores[index] >= 2]
            grounded.append(f"{text}{''.join(f'【S{ref}】' for ref in refs)}")
        data[field] = grounded


def _wiki_content_from_model(page_key: str, title: str, data: dict[str, Any], warnings: list[str]) -> str:
    summary = compact_text(str(data.get("summary") or ""), 1800)
    key_points = _string_list(data.get("key_points"), 10)
    current_status = _string_list(data.get("current_status"), 6)
    conflicts = _string_list(data.get("conflicts"), 6)
    unknowns = _string_list(data.get("unknowns"), 6)
    if not summary or not key_points:
        raise ValueError("模型结果缺少 summary 或 key_points")
    parts = [title, "综合说明", summary, "关键事实"]
    parts.extend(f"{index}. {item}" for index, item in enumerate(key_points, start=1))
    if current_status and page_key in {
        "plan_milestones", "current_progress", "risks_coordination", "meeting_decisions",
        "change_requests", "deliverables_acceptance", "resources_deployment"
    }:
        parts.append("当前状态")
        parts.extend(f"{index}. {item}" for index, item in enumerate(current_status, start=1))
    if conflicts:
        parts.append("来源冲突")
        parts.extend(f"{index}. {item}" for index, item in enumerate(conflicts, start=1))
    combined_unknowns = [*unknowns, *warnings]
    if combined_unknowns:
        parts.append("待确认事项")
        parts.extend(f"{index}. {item}" for index, item in enumerate(combined_unknowns, start=1))
    return compact_text("\n".join(parts), 9000)


def _generate_one_suggestion(page_key: str, title: str, docs: list[dict[str, Any]], generation_id: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        primary, secondary, warnings = _select_page_sources(conn, docs, page_key)
    finally:
        conn.close()
    selected = [*primary, *secondary]
    fallback_content, fallback_sources = _fallback_from_selected(title, page_key, primary, secondary, warnings)
    strategy = {
        "primaryCount": len(primary),
        "secondaryCount": len(secondary),
        "highAuthorityCount": sum(1 for doc in primary if int(doc.get("authority_level") or 5) <= 2),
        "primarySources": [doc.get("name") for doc in primary],
        "secondarySources": [doc.get("name") for doc in secondary],
        "warnings": warnings,
    }
    item = {
        "page_key": page_key,
        "title": f"{title}（LLM 归纳）",
        "content": fallback_content,
        "sources": fallback_sources,
        "generation_mode": "rules",
        "generation_error": "",
        "generation_id": generation_id,
        "created_at": now_iso(),
        "strategy": strategy,
    }
    if not selected:
        item["generation_error"] = "没有可用来源"
        return item

    prompt = _wiki_prompt(page_key, title, primary, secondary, warnings)
    raw = ""
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": "你是项目 Wiki 编审助手，严格依据来源并只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200 if page_key == "overview" else 900,
            disable_thinking=True,
        )
        data = _parse_json_object(raw)
        _validate_model_grounding(data, selected, page_key)
        _ground_model_citations(data, selected, page_key)
        item["content"] = _wiki_content_from_model(page_key, title, data, warnings)
        item["generation_mode"] = "llm"
    except Exception as first_error:
        if raw:
            try:
                repair_prompt = (
                    f"前次输出校验失败：{first_error}\n请重新检查来源并修复。"
                    if "来源片段中不存在的数字" in str(first_error)
                    else "请按 summary、key_points、current_status、conflicts、unknowns 字段修复以下输出，只返回 JSON：\n"
                    + compact_text(raw, 5000)
                )
                repaired = chat_completion(
                    [
                        {"role": "system", "content": "你只负责生成合法、来源可核验的 JSON，不增加新事实。"},
                        {
                            "role": "user",
                            "content": f"{prompt}\n\n{repair_prompt}",
                        },
                    ],
                    max_tokens=1200 if page_key == "overview" else 900,
                    disable_thinking=True,
                )
                data = _parse_json_object(repaired)
                _validate_model_grounding(data, selected, page_key)
                _ground_model_citations(data, selected, page_key)
                item["content"] = _wiki_content_from_model(page_key, title, data, warnings)
                item["generation_mode"] = "llm_repaired"
            except Exception as repair_error:
                item["generation_error"] = f"{first_error}；结构修复失败：{repair_error}"
        else:
            item["generation_error"] = str(first_error)
    mode_label = "LLM 归纳" if item["generation_mode"].startswith("llm") else "规则降级"
    item["title"] = f"{title}（{mode_label}）"
    return item


def rebuild_wiki_suggestions() -> dict[str, Any]:
    docs = _load_wiki_materials()
    generation_id = now_iso()
    items = [_generate_one_suggestion(page_key, title, docs, generation_id) for page_key, title in WIKI_PAGES]
    _replace_pending_wiki_suggestions(items)
    return {
        "ok": True,
        "created": len(items),
        "modelSucceeded": sum(1 for item in items if item["generation_mode"].startswith("llm")),
        "fallbackCount": sum(1 for item in items if item["generation_mode"] == "rules"),
    }


def _wiki_worker() -> None:
    generation_id = now_iso()
    _set_job(
        running=True, progress=0, total=len(WIKI_PAGES), created=0,
        modelSucceeded=0, fallbackCount=0, currentPage="", generationId=generation_id,
        error="", startedAt=now_iso(), finishedAt=""
    )
    try:
        docs = _load_wiki_materials()
        items = []
        for index, (page_key, title) in enumerate(WIKI_PAGES, start=1):
            _set_job(currentPage=title)
            item = _generate_one_suggestion(page_key, title, docs, generation_id)
            items.append(item)
            model_succeeded = sum(1 for value in items if value["generation_mode"].startswith("llm"))
            fallback_count = sum(1 for value in items if value["generation_mode"] == "rules")
            _set_job(
                progress=index,
                created=len(items),
                modelSucceeded=model_succeeded,
                fallbackCount=fallback_count,
            )
        _replace_pending_wiki_suggestions(items)
        _set_job(running=False, currentPage="", finishedAt=now_iso())
    except Exception as exc:
        _set_job(running=False, currentPage="", error=str(exc), finishedAt=now_iso())


def start_wiki_rebuild_job() -> dict[str, Any]:
    with _wiki_job_lock:
        if _wiki_job["running"]:
            return {"ok": True, "alreadyRunning": True, **dict(_wiki_job)}
    _set_job(
        running=True, progress=0, total=len(WIKI_PAGES), created=0,
        modelSucceeded=0, fallbackCount=0, currentPage="", error="",
        startedAt=now_iso(), finishedAt=""
    )
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
            try:
                row["strategy"] = json.loads(row.pop("strategy_json") or "{}")
            except Exception:
                row["strategy"] = {}
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
