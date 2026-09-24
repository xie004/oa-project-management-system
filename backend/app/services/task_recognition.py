from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from app.database import get_connection, now_iso, rows_to_dicts
from app.services.ai import ai_config, chat_completion
from app.services.data_quality import normalize_title
from app.services.extractors import compact_text, extract_text
from app.services.progress import parse_progress_percent
from app.services.task_matching import ranked_matches, safe_match, task_scope
from app.services.task_reconcile import infer_progress_signal

RULE_VERSION = "task-recognition-v2.0-accuracy-first"
ACTION_WORDS = ("完成", "推进", "跟进", "开展", "组织", "编制", "输出", "提交", "提供", "确认", "梳理", "配置", "部署", "迁移", "测试", "验证", "联调", "开发", "申请", "协调", "整改", "处理", "上线", "验收", "对接", "制作", "调研")
PLAN_WORDS = ("计划", "下周", "下一步", "待", "需", "需要", "请", "安排", "拟")
NEGATIVE_WORDS = ("尚未", "未完成", "未通过", "无法完成", "暂缓", "取消")
HEADER_WORDS = ("本周工作进展", "本周进展", "工作进展", "下周工作计划", "下周计划", "下一步计划", "工作计划", "会议决议", "会议决定", "议定事项", "待办事项", "责任事项", "存在问题", "风险", "需协调")
SECTION_ALIASES = {
    "progress": ("本周工作进展", "本周进展", "工作进展", "完成情况", "当前进展"),
    "plan": ("下周工作计划", "下周计划", "下一步计划", "后续计划", "工作计划"),
    "decision": ("会议决议", "会议决定", "议定事项", "待办事项", "责任事项", "会议结论"),
    "risk": ("存在问题或风险", "存在问题", "问题及风险", "风险事项"),
    "coordination": ("需协调及解决事项", "需协调", "协调事项"),
}
_job_lock = threading.Lock()


def _source_normal(value: str) -> str:
    return re.sub(r"[\s|,，。；;：:、（）()\[\]【】'\"`]+", "", value or "").lower()


def evidence_in_source(evidence: str, source: str) -> bool:
    needle, haystack = _source_normal(evidence), _source_normal(source)
    if len(needle) < 4:
        return False
    if needle in haystack:
        return True
    # Permit a short verbatim excerpt with an ellipsis, but never a paraphrase.
    parts = [part for part in re.split(r"[…\.]{2,}", needle) if len(part) >= 4]
    return len(parts) >= 2 and all(part in haystack for part in parts)


def _line(value: str) -> tuple[str, str]:
    match = re.match(r"^(\[[^\]]+\])\s*(.*)$", value.strip())
    return (match.group(1), match.group(2).strip()) if match else ("", value.strip())


def _section_for(content: str, current: str) -> str:
    compact = re.sub(r"^\s*(?:[（(]?[一二三四五六七八九十\d]+[.、)）]|[-•·])\s*", "", content).strip("：: ").lower()
    for section, aliases in SECTION_ALIASES.items():
        if any(alias.lower() in compact for alias in aliases):
            return section
    return current


def semantic_units(text: str, category: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    section = "decision" if category == "meeting" else "other"
    for raw in text.splitlines():
        locator, content = _line(raw)
        content = re.sub(r"^\s*(?:[（(]?[一二三四五六七八九十\d]+[.、)）]|[-•·])\s*", "", content).strip()
        if not content:
            continue
        new_section = _section_for(content, section)
        if new_section != section or any(word == content.strip("：:") for word in HEADER_WORDS):
            section = new_section
            if len(content) <= 24:
                continue
        # Table rows frequently repeat the section name in the first cell.
        cells = [cell.strip() for cell in content.split("|") if cell.strip()]
        if len(cells) > 1:
            row_section = next((key for key, names in SECTION_ALIASES.items() if any(any(name in cell for name in names) for cell in cells)), section)
            data_cells = [cell for cell in cells if not any(name in cell for names in SECTION_ALIASES.values() for name in names)]
            content = "；".join(data_cells) or content
            section = row_section
        if re.fullmatch(r"(?:序号|工作内容|完成情况|责任人|计划时间|备注|进度|状态)[|；\s]*", content):
            continue
        units.append({"text": content, "locator": locator or f"{category}/正文", "section": section})
    # Merge only obvious wrapped prose; keep independently actionable lines separate.
    merged: list[dict[str, Any]] = []
    for unit in units:
        actionable = any(word in unit["text"].lower() for word in ACTION_WORDS)
        if merged and not actionable and unit["section"] == merged[-1]["section"] and len(unit["text"]) < 55 and not unit["locator"].startswith("[工作表"):
            merged[-1]["text"] = f"{merged[-1]['text']}；{unit['text']}"
            merged[-1]["locator"] = f"{merged[-1]['locator']}—{unit['locator']}"
        else:
            merged.append(unit)
    return merged


def _title(text: str) -> str:
    value = re.sub(r"^(?:任务|事项|计划|进展|工作)\s*[：:]\s*", "", text).strip()
    value = re.sub(r"\s*[（(](?:已完成|进行中|未完成|计划中|待确认)[）)]\s*$", "", value)
    return compact_text(value, 140)


def rule_candidates(text: str, category: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates, rejected = [], []
    for unit in semantic_units(text, category):
        content, section = unit["text"], unit["section"]
        has_action = any(word in content.lower() for word in ACTION_WORDS)
        if section not in {"plan", "decision", "progress"}:
            continue
        if not has_action or len(_source_normal(content)) < 6:
            rejected.append({**unit, "type": "task", "title": _title(content), "reasonCode": "not_actionable", "reason": "没有识别到可独立管理的动作"})
            continue
        mode = "progress" if section == "progress" else "planned"
        status = "not_started"
        progress = 0
        signal = infer_progress_signal(content)
        if mode == "progress" and signal:
            status, progress = signal["status"], signal["progress"]
        title = _title(content)
        candidates.append({"type": "task", "title": title, "description": content, "evidence": content,
                           "locator": unit["locator"], "section": section, "task_mode": mode,
                           "owner": "", "due_date": "", "status": status, "progress": progress,
                           "confidence": .88 if mode == "planned" else .92, "method": "rule"})
    return candidates, rejected


def semantic_batches(text: str, max_chars: int = 8000) -> list[str]:
    max_chars = max(1500, min(12000, int(max_chars or 8000)))
    batches, current, length = [], [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if current and length + len(line) + 1 > max_chars:
            batches.append("\n".join(current)); current, length = [], 0
        # One pathological line must still be processed completely in pieces.
        while len(line) > max_chars:
            if current:
                batches.append("\n".join(current)); current, length = [], 0
            batches.append(line[:max_chars]); line = line[max_chars:]
        if line:
            current.append(line); length += len(line) + 1
    if current:
        batches.append("\n".join(current))
    return batches


def _parse_json(raw: str) -> list[dict[str, Any]]:
    value = (raw or "").strip()
    if value.startswith("```"):
        value = value.strip("`"); value = re.sub(r"^json\s*", "", value, flags=re.I)
    start, end = value.find("["), value.rfind("]")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 数组")
    data = json.loads(value[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("模型输出不是数组")
    return [item for item in data if isinstance(item, dict)]


def model_task_candidates(text: str, category: str, config: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    config = config or ai_config()
    if not config.get("enabled"):
        return [], [], {"totalBatches": 0, "successfulBatches": 0, "failedBatches": [], "raw": []}
    batches = semantic_batches(text, min(8000, int(config.get("maxTextLength") or 12000)))
    accepted, rejected, failed, raw_values = [], [], [], []
    for index, batch in enumerate(batches, 1):
        prompt = f"""从项目资料批次中识别任务候选或已有任务的进度证据。只输出 JSON 数组。
每项字段：type 固定 task、title、description、evidence、locator、task_mode(planned/progress)、confidence；可选 owner、due_date、status、progress。
evidence 必须逐字来自本批次；title 必须是完整可管理事项，禁止页码、表头、章节名；不要补造责任人和日期。
普通完成情况是 progress，不是里程碑；计划/预计与已完成严格区分。资料类型：{category}\n批次：\n{batch}"""
        first = repaired = ""
        try:
            first = chat_completion([{"role": "system", "content": "你是准确优先的任务识别器，只输出 JSON。"}, {"role": "user", "content": prompt}], config, max_tokens=1800)
            try:
                items = _parse_json(first)
            except Exception:
                repaired = chat_completion([{"role": "system", "content": "只修复为 JSON 数组，不增加事实。"}, {"role": "user", "content": first or "[空输出]"}], config, max_tokens=1800)
                items = _parse_json(repaired)
            raw_values.append(compact_text(repaired or first, 4000))
            for item in items:
                evidence = str(item.get("evidence") or "").strip()
                title = _title(str(item.get("title") or ""))
                if not title or not evidence_in_source(evidence, batch):
                    rejected.append({"type": "task", "title": title, "evidence": evidence, "locator": str(item.get("locator") or f"模型批次 {index}"), "reasonCode": "evidence_not_found", "reason": "模型依据无法在原文批次中定位", "method": "llm", "batchIndex": index})
                    continue
                mode = str(item.get("task_mode") or "").lower()
                if mode not in {"planned", "progress"}:
                    mode = "progress" if infer_progress_signal(evidence) else "planned"
                accepted.append({"type": "task", "title": title, "description": str(item.get("description") or evidence),
                                 "evidence": evidence, "locator": str(item.get("locator") or f"模型批次 {index}"),
                                 "section": mode, "task_mode": mode, "owner": str(item.get("owner") or ""),
                                 "due_date": str(item.get("due_date") or ""), "status": str(item.get("status") or "not_started"),
                                 "progress": parse_progress_percent(item.get("progress")),
                                 "confidence": min(.95, max(.5, float(item.get("confidence") or .7))), "method": "llm", "batchIndex": index})
        except Exception as exc:
            failed.append({"batchIndex": index, "error": str(exc), "textPreview": compact_text(batch, 240)})
    return accepted, rejected, {"totalBatches": len(batches), "successfulBatches": len(batches) - len(failed), "failedBatches": failed, "raw": raw_values}


def aggregate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in candidates:
        scope = task_scope(item)
        key = json.dumps([normalize_title(item.get("title") or ""), scope], ensure_ascii=False, sort_keys=True)
        if key not in groups:
            groups[key] = {**item, "sources": [{"evidence": item.get("evidence"), "locator": item.get("locator"), "method": item.get("method")} ]}
            continue
        current = groups[key]
        current["sources"].append({"evidence": item.get("evidence"), "locator": item.get("locator"), "method": item.get("method")})
        if float(item.get("confidence") or 0) > float(current.get("confidence") or 0):
            for field in ("title", "description", "evidence", "locator", "owner", "due_date", "status", "progress", "confidence", "task_mode", "method"):
                current[field] = item.get(field)
    return list(groups.values())


def analyze_task_candidates(text: str, category: str, include_model: bool = True) -> dict[str, Any]:
    rules, rejected = rule_candidates(text, category)
    model, model_rejected, batch = model_task_candidates(text, category) if include_model else ([], [], {"totalBatches": 0, "successfulBatches": 0, "failedBatches": [], "raw": []})
    accepted = aggregate_candidates([*rules, *model])
    coverage = 1.0 if not batch["totalBatches"] else batch["successfulBatches"] / batch["totalBatches"]
    return {"ruleVersion": RULE_VERSION, "candidates": accepted, "rejected": [*rejected, *model_rejected],
            "coverage": round(coverage, 4), "batch": batch}


def save_recognition_report(document_id: int, result: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM recognition_rejections WHERE document_id=?", (document_id,))
        for item in result["rejected"]:
            conn.execute("""INSERT INTO recognition_rejections(document_id,candidate_type,title,evidence_text,locator,reason_code,reason,source_method,batch_index,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (document_id, item.get("type", "task"), item.get("title", ""), item.get("evidence", item.get("text", "")),
                item.get("locator", ""), item.get("reasonCode", "filtered"), item.get("reason", "未通过准确性校验"), item.get("method", "rule"), item.get("batchIndex"), now_iso()))
        report = {"ruleVersion": result["ruleVersion"], "coverage": result["coverage"], "candidateCount": len(result["candidates"]),
                  "rejectedCount": len(result["rejected"]), "failedBatches": result["batch"].get("failedBatches", [])}
        conn.execute("""UPDATE documents SET recognition_rule_version=?,recognition_coverage=?,recognition_candidate_count=?,
            recognition_rejected_count=?,recognition_failed_batches=?,recognition_report_json=? WHERE id=?""",
            (result["ruleVersion"], result["coverage"], len(result["candidates"]), len(result["rejected"]),
             len(result["batch"].get("failedBatches", [])), json.dumps(report, ensure_ascii=False), document_id))
        conn.commit()
    finally:
        conn.close()


def _state_hash(conn) -> str:
    payload = {
        "tasks": [tuple(row) for row in conn.execute("SELECT id,title,status,progress,owner,due_date,is_archived,merged_into_id,task_kind,status_as_of FROM tasks ORDER BY id")],
        "evidence": [tuple(row) for row in conn.execute("SELECT id,entity_id,document_id,locator,observed_json FROM entity_evidence ORDER BY id")],
        "wiki": [tuple(row) for row in conn.execute("SELECT * FROM wiki_pages ORDER BY id")],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, default=str).encode()).hexdigest()


def _document_text(document: dict[str, Any]) -> str:
    path = Path(document.get("path") or "")
    if path.exists():
        return extract_text(path).text
    conn = get_connection()
    try:
        rows = conn.execute("SELECT chunk_text FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_index", (document["id"],)).fetchall()
        return "\n".join(row[0] for row in rows)
    finally:
        conn.close()


def _preview_documents(conn) -> list[dict[str, Any]]:
    weekly = rows_to_dicts(conn.execute("""SELECT d.*,w.period_end source_date FROM documents d JOIN weekly_reports w ON w.document_id=d.id
        WHERE d.status IN ('indexed','extracted') AND d.knowledge_status='indexed' ORDER BY w.period_end DESC,d.id DESC LIMIT 3""").fetchall())
    if not weekly:
        return []
    oldest, newest = min(d["source_date"] or "" for d in weekly), max(d["source_date"] or "" for d in weekly)
    meetings = rows_to_dicts(conn.execute("""SELECT d.*,COALESCE(NULLIF(substr(d.effective_date,1,10),''),substr(d.modified_at,1,10)) source_date
        FROM documents d WHERE d.doc_category='meeting' AND d.status IN ('indexed','extracted') AND d.knowledge_status='indexed'
        AND COALESCE(NULLIF(substr(d.effective_date,1,10),''),substr(d.modified_at,1,10)) BETWEEN ? AND ? ORDER BY source_date DESC,d.id DESC""", (oldest, newest)).fetchall())
    result = {item["id"]: item for item in [*weekly, *meetings]}
    return sorted(result.values(), key=lambda item: (item.get("source_date") or "", item["id"]), reverse=True)


def _preview_worker(run_id: str) -> None:
    conn = get_connection()
    try:
        documents = _preview_documents(conn)
        before = _state_hash(conn)
        officials = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE COALESCE(is_archived,0)=0 AND task_kind IN ('baseline','confirmed_addition')").fetchall())
        observations = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE COALESCE(is_archived,0)=0 AND task_kind='observation'").fetchall())
        conn.execute("UPDATE recognition_preview_runs SET status='running',total=?,before_hash=? WHERE id=?", (len(documents), before, run_id)); conn.commit()
        counts = Counter()
        for index, document in enumerate(documents, 1):
            conn.execute("UPDATE recognition_preview_runs SET progress=?,current_document=? WHERE id=?", (index - 1, document["name"], run_id)); conn.commit()
            try:
                text = _document_text(document)
                result = analyze_task_candidates(text, document["doc_category"], include_model=True)
                items = [*result["candidates"], *[{**item, "_rejected": True} for item in result["rejected"]]]
                for item in items:
                    matches = ranked_matches(item, officials)
                    best = matches[0] if matches else None
                    existing_obs = ranked_matches(item, observations, floor=.86)
                    signal = infer_progress_signal(item.get("evidence") or item.get("description") or "")
                    if item.get("_rejected"):
                        kind, explanation = "invalid", item.get("reason") or "未通过原文或可执行性校验"
                    elif item.get("task_mode") == "progress":
                        if safe_match(item, officials) and signal:
                            kind, explanation = "progress_change", "可作为正式任务的进度证据；试算未执行回写"
                        elif best:
                            kind, explanation = "match_change", "进展与现有任务存在相似性，但匹配或范围未达到自动关联条件"
                        else:
                            kind, explanation = "invalid", "进展未匹配到正式任务，准确优先模式不把它新增为任务"
                    elif safe_match(item, officials):
                        kind, explanation = "duplicate", "与现有正式任务高置信匹配，应追加来源而不是新增"
                    elif existing_obs and existing_obs[0]["similarity"] >= .90:
                        kind, explanation = "duplicate", "与现有识别事项重复，应聚合来源"
                    elif best:
                        kind, explanation = "match_change", "存在近似任务，但批次、阶段或目标唯一性需确认"
                    else:
                        kind, explanation = "new", "未发现可靠的现有事项匹配，可作为疑似新增候选"
                    counts[kind] += 1
                    conn.execute("""INSERT INTO recognition_preview_results(run_id,document_id,document_name,source_date,result_type,title,explanation,
                        evidence_text,locator,confidence,matched_task_id,matched_task_title,match_score,details_json,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, document["id"], document["name"], document.get("source_date") or "", kind,
                        item.get("title", ""), explanation, item.get("evidence", item.get("text", "")), item.get("locator", ""), float(item.get("confidence") or 0),
                        best.get("id") if best else None, best.get("title", "") if best else "", float(best.get("similarity") or 0) if best else 0,
                        json.dumps({"taskMode": item.get("task_mode"), "sources": item.get("sources", []), "reasonCode": item.get("reasonCode"), "signal": signal}, ensure_ascii=False), now_iso()))
                counts["candidate"] += len(result["candidates"]); counts["rejected"] += len(result["rejected"])
                counts["failed"] += len(result["batch"].get("failedBatches", []))
            except Exception as exc:
                counts["failed"] += 1
                conn.execute("""INSERT INTO recognition_preview_results(run_id,document_id,document_name,source_date,result_type,explanation,details_json,created_at)
                    VALUES(?,?,?,?, 'failed', ?, ?, ?)""", (run_id, document["id"], document["name"], document.get("source_date") or "", "文档试算失败", json.dumps({"error": str(exc)}, ensure_ascii=False), now_iso()))
            conn.commit()
        after = _state_hash(conn)
        conn.execute("""UPDATE recognition_preview_runs SET status='completed',progress=total,candidate_count=?,rejected_count=?,new_count=?,duplicate_count=?,
            invalid_count=?,match_change_count=?,progress_change_count=?,failed_count=?,current_document='',after_hash=?,finished_at=? WHERE id=?""",
            (counts["candidate"], counts["rejected"], counts["new"], counts["duplicate"], counts["invalid"], counts["match_change"], counts["progress_change"], counts["failed"], after, now_iso(), run_id))
        conn.commit()
    except Exception as exc:
        conn.rollback(); conn.execute("UPDATE recognition_preview_runs SET status='failed',error=?,finished_at=? WHERE id=?", (str(exc), now_iso(), run_id)); conn.commit()
    finally:
        conn.close()


def start_preview(trigger: str = "manual") -> dict[str, Any]:
    with _job_lock:
        conn = get_connection()
        try:
            running = conn.execute("SELECT * FROM recognition_preview_runs WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
            if running:
                return {**dict(running), "alreadyRunning": True}
            run_id = str(uuid.uuid4())
            conn.execute("INSERT INTO recognition_preview_runs(id,status,trigger,started_at) VALUES(?,'queued',?,?)", (run_id, trigger, now_iso())); conn.commit()
        finally:
            conn.close()
        threading.Thread(target=_preview_worker, args=(run_id,), daemon=True, name="task-recognition-preview").start()
        return preview_status(run_id)


def preview_status(run_id: str | None = None) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM recognition_preview_runs WHERE id=?" if run_id else "SELECT * FROM recognition_preview_runs ORDER BY started_at DESC LIMIT 1", (run_id,) if run_id else ()).fetchone()
        if not row:
            return {"status": "idle", "running": False, "progress": 0, "total": 0}
        result = dict(row); result["running"] = result["status"] in {"queued", "running"}; result["unchanged"] = bool(result["after_hash"] and result["before_hash"] == result["after_hash"])
        return result
    finally:
        conn.close()


def preview_results(run_id: str | None = None, result_type: str = "all", page: int = 1, page_size: int = 30) -> dict[str, Any]:
    status = preview_status(run_id)
    if not status.get("id"):
        return {"items": [], "total": 0, "page": 1, "pageSize": page_size, "status": status}
    clauses, params = ["run_id=?"], [status["id"]]
    if result_type != "all": clauses.append("result_type=?"); params.append(result_type)
    where = " AND ".join(clauses); page, page_size = max(1, page), max(1, min(100, page_size))
    conn = get_connection()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM recognition_preview_results WHERE {where}", params).fetchone()[0]
        rows = rows_to_dicts(conn.execute(f"SELECT * FROM recognition_preview_results WHERE {where} ORDER BY source_date DESC,id LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall())
        for row in rows:
            try: row["details"] = json.loads(row.pop("details_json") or "{}")
            except Exception: row["details"] = {}
        return {"items": rows, "total": total, "page": page, "pageSize": page_size, "status": status}
    finally:
        conn.close()
