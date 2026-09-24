from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from app.database import get_connection, now_iso, row_to_dict, rows_to_dicts, set_setting
from app.services.data_quality import attach_evidence, ensure_entity, normalize_title


OFFICIAL_TASK_KINDS = {"baseline", "confirmed_addition"}
STATUS_ALIASES = {
    "未开始": "not_started",
    "待开始": "not_started",
    "计划中": "planned",
    "待处理": "pending",
    "待确认": "pending",
    "进行中": "in_progress",
    "处理中": "in_progress",
    "待协调": "blocked",
    "已阻塞": "blocked",
    "已延期": "delayed",
    "已完成": "completed",
    "完成": "completed",
}
ALLOWED_SOURCE_CATEGORIES = {"weekly_report", "acceptance_launch"}
PLANNING_PATTERN = re.compile(r"(?:计划|预计|拟于|拟在|力争|目标|待|尚未|未完成|未开始).{0,14}(?:完成|上线|迁移|测试|交付)")
COMPLETION_PATTERN = re.compile(r"(?:已完成|全部完成|完成上线|上线完成|迁移完成|测试通过|验收通过|完成.{0,16}(?:工作|测试|迁移|部署|开发|联调|制作|配置|验证))")
RATIO_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*\+?")
PERCENT_PATTERN = re.compile(r"(100|\d{1,2}(?:\.\d+)?)\s*%")

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "running": False,
    "trigger": "",
    "progress": 0,
    "total": 0,
    "officialTasks": 0,
    "observationTasks": 0,
    "statusNormalized": 0,
    "evidenceAttached": 0,
    "autoUpdated": 0,
    "reviewCreated": 0,
    "statusAsOf": "",
    "error": "",
    "startedAt": "",
    "finishedAt": "",
    "refreshWikiAfter": False,
    "wikiRefresh": None,
}


def _set_job(**values: Any) -> None:
    with _job_lock:
        _job.update(values)


def task_reconcile_status() -> dict[str, Any]:
    with _job_lock:
        return dict(_job)


def normalize_task_status(value: Any) -> str:
    text = str(value or "").strip()
    if text in STATUS_ALIASES:
        return STATUS_ALIASES[text]
    if text in {"not_started", "planned", "pending", "in_progress", "blocked", "delayed", "completed"}:
        return text
    return "not_started"


def _source_date(document: dict[str, Any] | None) -> str:
    if not document:
        return ""
    return str(
        document.get("period_end")
        or document.get("effective_date")
        or document.get("modified_at")
        or ""
    )[:10]


def _task_similarity(left: str, right: str) -> float:
    left_key = normalize_title(left)
    right_key = normalize_title(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    shorter, longer = sorted([left_key, right_key], key=len)
    containment = len(shorter) / len(longer) if len(shorter) >= 5 and shorter in longer else 0.0
    left_pairs = {left_key[index : index + 2] for index in range(max(0, len(left_key) - 1))}
    right_pairs = {right_key[index : index + 2] for index in range(max(0, len(right_key) - 1))}
    overlap = len(left_pairs & right_pairs) / len(left_pairs | right_pairs) if left_pairs and right_pairs else 0.0
    return max(SequenceMatcher(None, left_key, right_key).ratio(), containment, overlap * 0.92)


def infer_progress_signal(text: str, observed: dict[str, Any] | None = None) -> dict[str, Any] | None:
    observed = observed or {}
    content = re.sub(r"\s+", "", f"{text or ''} {observed.get('description') or ''}")
    if not content or PLANNING_PATTERN.search(content):
        return None

    ratio = RATIO_PATTERN.search(content)
    if ratio:
        completed = float(ratio.group(1))
        total = float(ratio.group(2))
        if total > 0:
            progress = max(0, min(100, int(round(completed / total * 100))))
            return {
                "status": "completed" if completed >= total else "in_progress",
                "progress": 100 if completed >= total else progress,
                "confidence": 0.98,
                "reason": f"识别到明确完成比例 {ratio.group(0)}",
            }

    percent = PERCENT_PATTERN.search(content)
    if percent:
        progress = max(0, min(100, int(round(float(percent.group(1))))))
        if progress > 0:
            return {
                "status": "completed" if progress >= 100 else "in_progress",
                "progress": progress,
                "confidence": 0.96,
                "reason": f"识别到明确进度 {percent.group(0)}",
            }

    explicit_completed = bool(COMPLETION_PATTERN.search(content))
    if explicit_completed:
        return {
            "status": "completed",
            "progress": 100,
            "confidence": 0.95,
            "reason": "来源明确表述任务已经完成",
        }
    return None


def _create_review(
    conn,
    kind: str,
    task_id: int,
    related_ids: list[int],
    title: str,
    description: str,
    details: dict[str, Any],
    confidence: float,
) -> bool:
    existing = conn.execute(
        "SELECT details_json,related_record_ids_json FROM data_quality_suggestions WHERE suggestion_kind=? AND entity_type='task' AND primary_record_id=? AND status='pending'",
        (kind,task_id),
    ).fetchall()
    for row in existing:
        old = json.loads(row['details_json'] or '{}')
        if old.get('documentId') == details.get('documentId') and old.get('sourceDate') == details.get('sourceDate') and set(json.loads(row['related_record_ids_json'] or '[]')) == set(related_ids):
            return False
    conn.execute(
        """
        INSERT INTO data_quality_suggestions(
            suggestion_kind, entity_type, primary_record_id, related_record_ids_json,
            title, description, confidence, details_json, safe_auto, status, created_at
        ) VALUES(?, 'task', ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
        """,
        (
            kind,
            task_id,
            json.dumps(related_ids, ensure_ascii=False),
            title,
            description,
            confidence,
            json.dumps(details, ensure_ascii=False),
            now_iso(),
        ),
    )
    return True


def _load_document(conn, document_id: int | None) -> dict[str, Any] | None:
    if not document_id:
        return None
    return row_to_dict(
        conn.execute(
            """
            SELECT d.*, w.period_end
            FROM documents d
            LEFT JOIN weekly_reports w ON w.document_id = d.id
            WHERE d.id = ?
            """,
            (document_id,),
        ).fetchone()
    )


def _apply_signal(
    conn,
    official: dict[str, Any],
    observation: dict[str, Any],
    document: dict[str, Any],
    signal: dict[str, Any],
    similarity: float,
    snippet: str,
) -> tuple[bool, bool]:
    from app.services.observations import compatible_scope
    if not compatible_scope(observation, official):
        return False, False
    source_date = _source_date(document)
    if not source_date or document.get("doc_category") not in ALLOWED_SOURCE_CATEGORIES:
        return False, False
    current_date = str(official.get("status_as_of") or "")[:10]
    if current_date and source_date < current_date:
        return False, False
    if similarity < 0.90:
        created = _create_review(
            conn,
            "task_match_review",
            int(official["id"]),
            [int(observation["id"])],
            f"确认任务匹配：{observation['title']}",
            f"识别事项可能对应正式任务“{official['title']}”，需确认后再更新状态。",
            {
                "similarity": similarity,
                "signal": signal,
                "documentId": document["id"],
                "sourceDate": source_date,
            },
            similarity,
        )
        return False, created
    if document.get("knowledge_status") != "indexed":
        return False, False
    latest = conn.execute("""SELECT MAX(COALESCE((SELECT MAX(period_end) FROM weekly_reports w WHERE w.document_id=d.id),
        NULLIF(substr(d.effective_date,1,10),''),substr(d.modified_at,1,10))) FROM documents d
        WHERE d.knowledge_status='indexed' AND d.doc_category=?""",(document.get("doc_category"),)).fetchone()[0]
    if latest and source_date < latest:
        return False, False
    if float(signal.get("confidence") or 0) < 0.90:
        return False, False
    if normalize_task_status(official.get("status")) == "completed" and signal["status"] != "completed":
        created = _create_review(
            conn,
            "task_status_review",
            int(official["id"]),
            [int(observation["id"])],
            f"任务状态可能回退：{official['title']}",
            "较新资料出现了低于已完成状态的表述，系统未自动回退。",
            {"signal": signal, "documentId": document["id"], "sourceDate": source_date},
            float(signal["confidence"]),
        )
        return False, created

    entity_id = ensure_entity(conn, "task", int(official["id"]), official["title"])
    attach_evidence(
        conn,
        entity_id,
        int(document["id"]),
        snippet,
        locator="任务进度自动回写",
        observed={"status": signal["status"], "progress": signal["progress"], "sourceDate": source_date},
        confidence=float(signal["confidence"]),
        method="high_confidence_progress_reconcile",
    )
    changed = (
        normalize_task_status(official.get("status")) != signal["status"]
        or int(official.get("progress") or 0) != int(signal["progress"])
        or current_date != source_date
    )
    if changed:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, status_as_of = ?, status_source_document_id = ?,
                status_confidence = ?, status_update_mode = 'auto_evidence', updated_at = ?
            WHERE id = ?
            """,
            (
                signal["status"],
                signal["progress"],
                source_date,
                document["id"],
                signal["confidence"],
                now_iso(),
                official["id"],
            ),
        )
    return changed, False


def reconcile_task_progress(trigger: str = "manual") -> dict[str, Any]:
    conn = get_connection()
    updated_task_ids: set[int] = set()
    counts = {
        "officialTasks": 0,
        "observationTasks": 0,
        "statusNormalized": 0,
        "evidenceAttached": 0,
        "autoUpdated": 0,
        "reviewCreated": 0,
        "statusAsOf": "",
    }
    try:
        tasks = rows_to_dicts(conn.execute("SELECT * FROM tasks WHERE COALESCE(is_archived, 0) = 0 ORDER BY id").fetchall())
        for task in tasks:
            normalized_status = normalize_task_status(task.get("status"))
            if normalized_status != task.get("status"):
                conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (normalized_status, now_iso(), task["id"]))
                task["status"] = normalized_status
                counts["statusNormalized"] += 1

        official = [task for task in tasks if task.get("task_kind") in OFFICIAL_TASK_KINDS]
        from app.services.observations import safe_match, link_evidence, candidates
        resolved = {r[0] for r in conn.execute("SELECT task_id FROM observation_resolutions")}
        observations = [task for task in tasks if task.get("task_kind") == "observation" and task['id'] not in resolved]
        observations.sort(key=lambda t: (_source_date(_load_document(conn,t.get('source_document_id'))),t['id']))
        counts["officialTasks"] = len(official)
        counts["observationTasks"] = len(observations)
        _set_job(total=len(observations) + len(official), officialTasks=len(official), observationTasks=len(observations))

        for task in official:
            if int(task.get("progress") or 0) >= 100 and normalize_task_status(task.get("status")) != "completed":
                conn.execute(
                    """
                    UPDATE tasks SET status = 'completed', progress = 100,
                        status_confidence = 1, status_update_mode = 'normalized_progress', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), task["id"]),
                )
                task["status"] = "completed"
                counts["statusNormalized"] += 1

        for index, observation in enumerate(observations, start=1):
            _set_job(progress=index)
            if not official:
                continue
            similarity, matched = max(
                ((_task_similarity(observation["title"], task["title"]), task) for task in official),
                key=lambda item: item[0],
            )
            if similarity < 0.72:
                continue
            document = _load_document(conn, observation.get("source_document_id"))
            if not document:
                continue
            snippet = f"{observation.get('title') or ''}\n{observation.get('description') or ''}".strip()
            signal = infer_progress_signal(
                snippet,
                {"status": observation.get("status"), "progress": observation.get("progress"), "description": observation.get("description")},
            )
            safe = safe_match(observation, official)
            field_conflict = any(observation.get(k) and matched.get(k) and observation[k]!=matched[k] for k in ('owner','due_date'))
            if not safe or field_conflict:
                if signal:
                    created = _create_review(conn,"task_match_review",matched['id'],[observation['id']],
                        f"确认任务匹配：{observation['title']}","匹配存在范围、目标或字段歧义，未自动关联。",
                        {"signal":signal,"documentId":document['id'],"sourceDate":_source_date(document)},similarity)
                    counts['reviewCreated'] += int(created)
                continue
            link_evidence(conn,[observation],matched,actor='system')
            counts['evidenceAttached'] += 1
            changed, reviewed = _apply_signal(conn, matched, observation, document, signal, similarity, snippet) if signal else (False,False)
            if changed:
                updated_task_ids.add(int(matched["id"]))
                counts["autoUpdated"] = len(updated_task_ids)
                matched.update({"status": signal["status"], "progress": signal["progress"], "status_as_of": _source_date(document)})
            if reviewed:
                counts["reviewCreated"] += 1

        latest_evidence_date = conn.execute(
            """
            SELECT MAX(COALESCE(w.period_end, SUBSTR(d.effective_date, 1, 10), SUBSTR(d.modified_at, 1, 10))) AS latest
            FROM tasks t
            JOIN documents d ON d.id = t.source_document_id
            LEFT JOIN weekly_reports w ON w.document_id = d.id
            WHERE COALESCE(t.is_archived, 0) = 0 AND t.task_kind = 'observation'
            """
        ).fetchone()["latest"] or ""
        counts["statusAsOf"] = latest_evidence_date
        if latest_evidence_date:
            set_setting(conn, "task_status_as_of", latest_evidence_date)

        conn.commit()
        return {"ok": True, "trigger": trigger, **counts}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _worker(trigger: str) -> None:
    try:
        result = reconcile_task_progress(trigger)
        _set_job(running=False, finishedAt=now_iso(), **{key: value for key, value in result.items() if key in _job})
    except Exception as exc:
        _set_job(running=False, error=str(exc), finishedAt=now_iso())
    finally:
        with _job_lock:
            refresh_wiki = bool(_job.get("refreshWikiAfter"))
        if refresh_wiki:
            try:
                from app.services.wiki import start_current_progress_refresh_job

                wiki_result = start_current_progress_refresh_job("weekly_scan_after_task_reconcile")
                _set_job(wikiRefresh=wiki_result)
            except Exception as exc:
                _set_job(wikiRefresh={"ok": False, "error": str(exc)})


def start_task_progress_reconcile(trigger: str = "manual", *, refresh_wiki_after: bool = False) -> dict[str, Any]:
    with _job_lock:
        if _job["running"]:
            if refresh_wiki_after:
                _job["refreshWikiAfter"] = True
            return {"ok": True, "alreadyRunning": True, **dict(_job)}
    _set_job(
        running=True,
        trigger=trigger,
        progress=0,
        total=0,
        officialTasks=0,
        observationTasks=0,
        statusNormalized=0,
        evidenceAttached=0,
        autoUpdated=0,
        reviewCreated=0,
        statusAsOf="",
        error="",
        startedAt=now_iso(),
        finishedAt="",
        refreshWikiAfter=refresh_wiki_after,
        wikiRefresh=None,
    )
    threading.Thread(target=_worker, args=(trigger,), daemon=True, name="task-progress-reconcile").start()
    return {"ok": True, "started": True, **task_reconcile_status()}


def promote_task(task_id: int) -> dict[str, Any]:
    # Compatibility for internal callers; still use the same preview/validation rules.
    from app.services.observations import preview, resolve
    try:
        group = preview([task_id])["items"][0]
        return resolve({"id":task_id,"action":"promote","fingerprint":group["fingerprint"]})
    except ValueError as exc:
        return {"ok":False,"message":str(exc)}
