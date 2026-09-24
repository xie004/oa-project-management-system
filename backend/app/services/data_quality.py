from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.database import get_connection, now_iso, row_to_dict, rows_to_dicts
from app.services.extractors import compact_text
from app.services.progress import parse_progress_percent


ENTITY_TABLES: dict[str, dict[str, str]] = {
    "task": {"table": "tasks", "title": "title", "document": "source_document_id"},
    "risk": {"table": "risks", "title": "title", "document": "source_document_id"},
    "milestone": {"table": "milestones", "title": "title", "document": "source_document_id"},
    "change_request": {"table": "change_requests", "title": "title", "document": "source_document_id"},
    "deliverable": {"table": "deliverables", "title": "name", "document": "document_id"},
}

ENTITY_LABELS = {
    "task": "任务",
    "risk": "风险",
    "milestone": "里程碑",
    "change_request": "变更",
    "deliverable": "交付物",
}

STATUS_RANK = {
    "not_started": 0,
    "planned": 0,
    "pending": 0,
    "open": 0,
    "in_progress": 1,
    "blocked": 1,
    "delayed": 1,
    "submitted": 2,
    "completed": 3,
    "finalized": 3,
    "closed": 3,
}

INVALID_PATTERNS = [
    re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页", re.I),
    re.compile(r"^共\s*\d+\s*页$", re.I),
    re.compile(r"^工作表[:：]", re.I),
    re.compile(r"^[\d\s|/\\._-]+$"),
]

_job_lock = threading.Lock()
_job: dict[str, Any] = {
    "running": False,
    "progress": 0,
    "total": 0,
    "created": 0,
    "counts": {},
    "error": "",
    "startedAt": "",
    "finishedAt": "",
}


def normalize_title(value: str) -> str:
    text = (value or "").lower().strip()
    text = re.sub(r"^\s*(?:下周|本周|本月|后续|继续|完成|已完成|推进|开展|落实|跟进)\s*", "", text)
    text = re.sub(r"[（(][^）)]*(?:当前进度|完成\s*\d|进行中|已完成)[^）)]*[）)]", "", text)
    text = re.sub(r"[（(](?:已完成|完成|进行中|待确认|待协调)[）)]", "", text)
    text = re.sub(r"(?:已完成|完成|进行中|待确认|待协调)$", "", text)
    text = text.replace("mac地址", "mac地址").replace("windows", "windows")
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _exact_key(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (value or "").lower().strip())


def suggestion_fingerprint(entity_type: str, title: str) -> str:
    value = f"{entity_type}:{normalize_title(title)}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_candidate_title(value: str) -> str:
    return re.sub(
        r"^\s*(?:[（(]?\d+[）).、]|[（(]?[一二三四五六七八九十]+[）).、])\s*",
        "",
        value or "",
    ).strip()


def merge_candidate_payload(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in secondary.items():
        if value in (None, ""):
            continue
        if merged.get(key) in (None, ""):
            merged[key] = value
    if primary.get("progress") not in (None, "") or secondary.get("progress") not in (None, ""):
        merged["progress"] = max(
            parse_progress_percent(primary.get("progress")),
            parse_progress_percent(secondary.get("progress")),
        )
    primary_status = str(primary.get("status") or "")
    secondary_status = str(secondary.get("status") or "")
    if STATUS_RANK.get(secondary_status, 0) > STATUS_RANK.get(primary_status, 0):
        merged["status"] = secondary_status
    return merged


def consolidate_pending_candidates(conn: sqlite3.Connection) -> dict[str, int]:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM update_suggestions
            WHERE status = 'pending'
            ORDER BY quality_score DESC, confidence DESC, created_at, id
            """
        ).fetchall()
    )
    kept: dict[tuple[str, str], dict[str, Any]] = {}
    aggregated = 0
    rejected = 0
    for row in rows:
        title = _clean_candidate_title(row.get("title") or "")
        invalid, warnings = is_invalid_candidate(title)
        if invalid:
            conn.execute(
                """
                UPDATE update_suggestions
                SET status = 'dismissed', candidate_action = 'rejected_quality',
                    quality_warnings_json = ?, applied_at = ?
                WHERE id = ?
                """,
                (json.dumps(warnings, ensure_ascii=False), now_iso(), row["id"]),
            )
            rejected += 1
            continue
        fingerprint = suggestion_fingerprint(row["suggestion_type"], title)
        key = (row["suggestion_type"], fingerprint)
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        payload["title"] = title
        if key not in kept:
            conn.execute(
                """
                UPDATE update_suggestions
                SET title = ?, normalized_title = ?, fingerprint = ?, payload_json = ?
                WHERE id = ?
                """,
                (title, normalize_title(title), fingerprint, json.dumps(payload, ensure_ascii=False), row["id"]),
            )
            kept[key] = {**row, "title": title, "payload": payload}
            continue

        primary = kept[key]
        primary_id = int(primary["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO suggestion_sources(
                suggestion_id, document_id, evidence_hash, evidence_text, locator, observed_json, created_at
            )
            SELECT ?, document_id, evidence_hash, evidence_text, locator, observed_json, created_at
            FROM suggestion_sources WHERE suggestion_id = ?
            """,
            (primary_id, row["id"]),
        )
        merged_payload = merge_candidate_payload(primary["payload"], payload)
        existing_warnings = []
        try:
            existing_warnings = json.loads(primary.get("quality_warnings_json") or "[]")
        except Exception:
            pass
        aggregate_warning = "已聚合来自多份资料的同一候选，正式字段仍需确认"
        if aggregate_warning not in existing_warnings:
            existing_warnings.append(aggregate_warning)
        conn.execute(
            """
            UPDATE update_suggestions
            SET payload_json = ?, confidence = MAX(confidence, ?),
                quality_score = MAX(quality_score, ?), quality_warnings_json = ?
            WHERE id = ?
            """,
            (
                json.dumps(merged_payload, ensure_ascii=False),
                float(row.get("confidence") or 0),
                float(row.get("quality_score") or 0),
                json.dumps(existing_warnings, ensure_ascii=False),
                primary_id,
            ),
        )
        primary["payload"] = merged_payload
        primary["quality_warnings_json"] = json.dumps(existing_warnings, ensure_ascii=False)
        conn.execute(
            "UPDATE update_suggestions SET status = 'dismissed', candidate_action = 'aggregated', applied_at = ? WHERE id = ?",
            (now_iso(), row["id"]),
        )
        aggregated += 1
    return {"aggregated": aggregated, "rejected": rejected}


def is_invalid_candidate(title: str) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    normalized = normalize_title(title)
    if len(normalized) < 4:
        warnings.append("标题有效字符不足 4 个")
    if any(pattern.search(title or "") for pattern in INVALID_PATTERNS):
        warnings.append("疑似页码、表头或打印元数据")
    if (title or "").count("|") >= 4:
        warnings.append("疑似未清洗的表格行")
    return bool(warnings), warnings


def _similarity(left: str, right: str) -> float:
    left_key = normalize_title(left)
    right_key = normalize_title(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    ratio = SequenceMatcher(None, left_key, right_key).ratio()
    shorter, longer = sorted([left_key, right_key], key=len)
    containment = len(shorter) / len(longer) if len(shorter) >= 5 and shorter in longer else 0.0
    left_pairs = {left_key[index : index + 2] for index in range(max(0, len(left_key) - 1))}
    right_pairs = {right_key[index : index + 2] for index in range(max(0, len(right_key) - 1))}
    jaccard = len(left_pairs & right_pairs) / len(left_pairs | right_pairs) if left_pairs and right_pairs else 0.0
    return max(ratio, containment, jaccard * 0.9)


def document_version_group(name: str) -> str:
    stem = Path(name or "").stem.lower()
    stem = re.sub(r"\(\d+\)", "", stem)
    stem = re.sub(r"20\d{6}", "", stem)
    stem = re.sub(r"v\d+(?:\.\d+)*", "", stem, flags=re.I)
    stem = re.sub(r"(?:初稿|拟定稿|征求意见稿|送审稿|终稿|定稿|正式稿|盖章版)", "", stem)
    stem = re.sub(r"[（(][^）)]{1,8}[）)]", "", stem)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", stem)


def _record_rows(conn: sqlite3.Connection, entity_type: str, include_archived: bool = True) -> list[dict[str, Any]]:
    config = ENTITY_TABLES[entity_type]
    where = "" if include_archived else "WHERE COALESCE(is_archived, 0) = 0"
    if not include_archived and entity_type == "task":
        where += " AND NOT (task_kind='observation' AND EXISTS (SELECT 1 FROM observation_resolutions r WHERE r.task_id=tasks.id))"
    return rows_to_dicts(
        conn.execute(
            f"SELECT *, {config['title']} AS entity_title FROM {config['table']} {where} ORDER BY id"
        ).fetchall()
    )


def ensure_entity(
    conn: sqlite3.Connection,
    entity_type: str,
    record_id: int,
    title: str,
    archived: bool = False,
) -> int:
    now = now_iso()
    normalized = normalize_title(title)
    conn.execute(
        """
        INSERT INTO project_entities(
            entity_type, record_id, canonical_title, normalized_key, status, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, record_id) DO UPDATE SET
            canonical_title = excluded.canonical_title,
            normalized_key = excluded.normalized_key,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (entity_type, record_id, title, normalized, "archived" if archived else "active", now, now),
    )
    return int(
        conn.execute(
            "SELECT id FROM project_entities WHERE entity_type = ? AND record_id = ?",
            (entity_type, record_id),
        ).fetchone()["id"]
    )


def attach_evidence(
    conn: sqlite3.Connection,
    entity_id: int,
    document_id: int | None,
    snippet: str,
    *,
    suggestion_id: int | None = None,
    locator: str = "",
    observed: dict[str, Any] | None = None,
    confidence: float = 0.7,
    method: str = "system",
) -> None:
    snippet = compact_text(snippet or "来源文件记录", 1600)
    digest_source = f"{document_id or 0}|{locator}|{snippet}"
    evidence_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT OR IGNORE INTO entity_evidence(
            entity_id, document_id, suggestion_id, evidence_hash, snippet, locator,
            observed_json, confidence, extraction_method, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            document_id,
            suggestion_id,
            evidence_hash,
            snippet,
            locator,
            json.dumps(observed or {}, ensure_ascii=False),
            max(0.0, min(1.0, float(confidence or 0.7))),
            method,
            now_iso(),
        ),
    )


def bootstrap_entities(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    owns_connection = conn is None
    conn = conn or get_connection()
    counts: dict[str, int] = {}
    try:
        for entity_type, config in ENTITY_TABLES.items():
            records = _record_rows(conn, entity_type, include_archived=True)
            for record in records:
                entity_id = ensure_entity(
                    conn,
                    entity_type,
                    int(record["id"]),
                    record["entity_title"],
                    bool(record.get("is_archived")),
                )
                document_id = record.get(config["document"])
                if document_id:
                    observed = {
                        key: record.get(key)
                        for key in ["status", "progress", "owner", "due_date", "planned_date", "actual_date", "level"]
                        if key in record and record.get(key) not in (None, "")
                    }
                    attach_evidence(
                        conn,
                        entity_id,
                        int(document_id),
                        record.get("description") or record["entity_title"],
                        observed=observed,
                        confidence=0.8,
                        method="historical_backfill",
                    )
            counts[entity_type] = len(records)
        refresh_document_metadata(conn)
        if owns_connection:
            conn.commit()
        return counts
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def refresh_document_metadata(conn: sqlite3.Connection) -> None:
    documents = rows_to_dicts(conn.execute("SELECT id, name, doc_category FROM documents").fetchall())
    for document in documents:
        chunks = conn.execute(
            "SELECT text FROM knowledge_chunks WHERE document_id = ? ORDER BY chunk_index",
            (document["id"],),
        ).fetchall()
        normalized_text = re.sub(r"\s+", "", "\n".join(row["text"] or "" for row in chunks))
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest() if normalized_text else ""
        version_group = (
            document_version_group(document["name"])
            if document.get("doc_category") in {"contract_tender", "requirement_change"}
            else ""
        )
        conn.execute(
            "UPDATE documents SET content_hash = ?, version_group = ? WHERE id = ?",
            (content_hash, version_group, document["id"]),
        )


def find_matching_entity(
    conn: sqlite3.Connection,
    entity_type: str,
    title: str,
) -> tuple[dict[str, Any] | None, float]:
    rows = rows_to_dicts(
        conn.execute(
            """
            SELECT e.*, COALESCE(ev.source_count, 0) AS source_count
            FROM project_entities e
            LEFT JOIN (
                SELECT entity_id, COUNT(*) AS source_count FROM entity_evidence GROUP BY entity_id
            ) ev ON ev.entity_id = e.id
            WHERE e.entity_type = ? AND e.status = 'active'
            """,
            (entity_type,),
        ).fetchall()
    )
    scored = [(_similarity(title, row["canonical_title"]), row) for row in rows]
    if not scored:
        return None, 0.0
    score, row = max(scored, key=lambda item: item[0])
    return row, score


def _meaningful_updates(entity_type: str, record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if entity_type == "task":
        proposed_progress = parse_progress_percent(payload.get("progress"))
        if proposed_progress > parse_progress_percent(record.get("progress")):
            updates["progress"] = proposed_progress
        proposed_status = payload.get("status") or ""
        if STATUS_RANK.get(proposed_status, 0) > STATUS_RANK.get(record.get("status") or "", 0):
            updates["status"] = proposed_status
        for key in ["owner", "due_date", "start_date"]:
            if payload.get(key) and not record.get(key):
                updates[key] = payload[key]
    elif entity_type == "risk":
        if payload.get("mitigation") and not record.get("mitigation"):
            updates["mitigation"] = payload["mitigation"]
        if payload.get("status") == "closed" and record.get("status") != "closed":
            updates["status"] = "closed"
    elif entity_type == "milestone":
        if payload.get("actual_date") and not record.get("actual_date"):
            updates["actual_date"] = payload["actual_date"]
        if payload.get("status") == "completed" and record.get("status") != "completed":
            updates["status"] = "completed"
    return updates


def prepare_candidate(
    conn: sqlite3.Connection,
    document_id: int,
    entity_type: str,
    title: str,
    description: str,
    payload: dict[str, Any],
    confidence: float,
    locator: str = "",
    method: str = "rule",
) -> dict[str, Any]:
    invalid, warnings = is_invalid_candidate(title)
    quality_score = max(0.0, min(1.0, float(confidence or 0.7) - len(warnings) * 0.25))
    if invalid:
        return {
            "skip": True,
            "warnings": warnings,
            "quality_score": quality_score,
            "candidate_action": "reject",
        }

    if not conn.execute("SELECT 1 FROM project_entities LIMIT 1").fetchone():
        bootstrap_entities(conn)
    matched, similarity = find_matching_entity(conn, entity_type, title)
    result = {
        "skip": False,
        "warnings": warnings,
        "quality_score": quality_score,
        "candidate_action": "create",
        "matched_entity_type": None,
        "matched_entity_id": None,
        "similarity": similarity,
        "normalized_title": normalize_title(title),
        "fingerprint": suggestion_fingerprint(entity_type, title),
    }
    if not matched or similarity < 0.78:
        return result

    result["matched_entity_type"] = entity_type
    result["matched_entity_id"] = matched["id"]
    if similarity >= 0.93:
        config = ENTITY_TABLES[entity_type]
        record = row_to_dict(
            conn.execute(
                f"SELECT * FROM {config['table']} WHERE id = ?",
                (matched["record_id"],),
            ).fetchone()
        ) or {}
        updates = _meaningful_updates(entity_type, record, payload)
        attach_evidence(
            conn,
            int(matched["id"]),
            document_id,
            description or title,
            locator=locator,
            observed=payload,
            confidence=confidence,
            method=method,
        )
        if not updates:
            result.update({"skip": True, "candidate_action": "attach_evidence"})
        else:
            result.update({"candidate_action": "propose_update", "proposed_updates": updates})
        return result

    result["candidate_action"] = "review_match"
    result["warnings"] = [*warnings, "发现高度相似的现有事项，需确认是否合并"]
    return result


def entity_evidence(entity_type: str, record_id: int) -> dict[str, Any]:
    if entity_type not in ENTITY_TABLES:
        return {"entity": None, "evidence": []}
    conn = get_connection()
    try:
        bootstrap_entities(conn)
        conn.commit()
        entity = row_to_dict(
            conn.execute(
                "SELECT * FROM project_entities WHERE entity_type = ? AND record_id = ?",
                (entity_type, record_id),
            ).fetchone()
        )
        if not entity:
            return {"entity": None, "evidence": []}
        evidence = rows_to_dicts(
            conn.execute(
                """
                SELECT ev.*, d.name AS document_name, d.path AS document_path,
                       d.authority_level, d.authority_score, d.doc_category
                FROM entity_evidence ev
                LEFT JOIN documents d ON d.id = ev.document_id
                WHERE ev.entity_id = ?
                ORDER BY ev.created_at DESC, ev.id DESC
                """,
                (entity["id"],),
            ).fetchall()
        )
        for item in evidence:
            try:
                item["observed"] = json.loads(item.pop("observed_json") or "{}")
            except Exception:
                item["observed"] = {}
        return {"entity": entity, "evidence": evidence}
    finally:
        conn.close()


def _record_conflicts(records: list[dict[str, Any]], entity_type: str) -> dict[str, list[Any]]:
    fields = {
        "task": ["status", "progress", "owner", "start_date", "due_date"],
        "risk": ["status", "level", "mitigation"],
        "milestone": ["status", "planned_date", "actual_date"],
        "change_request": ["status", "impact", "proposer"],
        "deliverable": ["status", "owner", "planned_date", "submitted_date"],
    }[entity_type]
    conflicts: dict[str, list[Any]] = {}
    for field in fields:
        values = []
        for record in records:
            value = record.get(field)
            if value not in (None, "") and value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts[field] = values
    return conflicts


def _record_quality_warnings(
    record: dict[str, Any],
    entity_type: str,
    document_categories: dict[int, str],
) -> list[str]:
    title = record.get("entity_title") or ""
    warnings: list[str] = []
    if entity_type == "risk" and re.match(r"^(?:策略|措施|应对|建议|处理方式)[:：]", title):
        warnings.append("该记录疑似只有应对策略，应并入对应风险的 mitigation 字段，不应独立作为风险")
    document_id = record.get(ENTITY_TABLES[entity_type]["document"])
    if entity_type == "milestone" and document_categories.get(int(document_id or 0)) == "resource":
        warnings.append("该里程碑来源于资源清单；资源配置要求默认不应生成正式里程碑")
    if entity_type == "task" and re.fullmatch(r"[（(][^）)]{2,40}[）)]", title.strip()):
        warnings.append("该任务疑似只有分类项或表格残片，需确认是否属于误识别")
    return warnings


def _choose_primary(records: list[dict[str, Any]], entity_type: str) -> dict[str, Any]:
    def score(record: dict[str, Any]) -> tuple[float, int]:
        value = 0.0
        if entity_type == "task" and record.get("source") == "项目计划分解":
            value += 10
        if record.get("source_document_id") or record.get("document_id"):
            value += 2
        value += STATUS_RANK.get(record.get("status") or "", 0)
        value += min(float(record.get("progress") or 0) / 100, 1)
        return value, -int(record["id"])

    return max(records, key=score)


def _insert_quality_suggestion(
    conn: sqlite3.Connection,
    kind: str,
    entity_type: str,
    primary_record_id: int | None,
    related_ids: list[int],
    title: str,
    description: str,
    confidence: float,
    details: dict[str, Any],
    safe_auto: bool = False,
) -> None:
    signature = hashlib.sha256(
        f"{kind}|{entity_type}|{primary_record_id}|{','.join(map(str, sorted(related_ids)))}".encode("utf-8")
    ).hexdigest()
    details = {**details, "signature": signature}
    existing = conn.execute(
        """
        SELECT id FROM data_quality_suggestions
        WHERE status = 'pending' AND json_extract(details_json, '$.signature') = ?
        """,
        (signature,),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO data_quality_suggestions(
            suggestion_kind, entity_type, primary_record_id, related_record_ids_json,
            title, description, confidence, details_json, safe_auto, status, created_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            kind,
            entity_type,
            primary_record_id,
            json.dumps(related_ids, ensure_ascii=False),
            title,
            description,
            confidence,
            json.dumps(details, ensure_ascii=False),
            1 if safe_auto else 0,
            now_iso(),
        ),
    )


def analyze_data_quality() -> dict[str, Any]:
    conn = get_connection()
    counts: defaultdict[str, int] = defaultdict(int)
    try:
        bootstrap_entities(conn)
        consolidate_pending_candidates(conn)
        conn.execute("DELETE FROM data_quality_suggestions WHERE status = 'pending' AND suggestion_kind NOT IN ('task_match_review','task_status_review')")
        document_categories = {
            int(row["id"]): row["doc_category"] or ""
            for row in conn.execute("SELECT id, doc_category FROM documents").fetchall()
        }

        for entity_type in ENTITY_TABLES:
            records = _record_rows(conn, entity_type, include_archived=False)
            exact_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            invalid_ids: set[int] = set()
            for record in records:
                invalid, warnings = is_invalid_candidate(record["entity_title"])
                if invalid:
                    invalid_ids.add(int(record["id"]))
                    _insert_quality_suggestion(
                        conn,
                        "invalid_record",
                        entity_type,
                        int(record["id"]),
                        [],
                        f"疑似误识别：{record['entity_title']}",
                        "该记录疑似页码、表头、打印元数据或无有效语义，建议归档。",
                        0.99,
                        {"record": record, "warnings": warnings},
                        safe_auto=True,
                    )
                    counts["invalid_record"] += 1
                    continue
                exact_groups[_exact_key(record["entity_title"])].append(record)

            exact_member_ids: set[int] = set()
            for group in exact_groups.values():
                if len(group) < 2:
                    continue
                primary = _choose_primary(group, entity_type)
                related = [int(item["id"]) for item in group if item["id"] != primary["id"]]
                exact_member_ids.update(int(item["id"]) for item in group)
                conflicts = _record_conflicts(group, entity_type)
                record_warnings = sorted(
                    {
                        warning
                        for record in group
                        for warning in _record_quality_warnings(record, entity_type, document_categories)
                    }
                )
                _insert_quality_suggestion(
                    conn,
                    "exact_duplicate",
                    entity_type,
                    int(primary["id"]),
                    related,
                    f"合并完全重复的{ENTITY_LABELS[entity_type]}：{primary['entity_title']}",
                    f"发现 {len(group)} 条标准化标题相同的记录，建议保留一条并汇总全部来源。",
                    1.0,
                    {"records": group, "fieldConflicts": conflicts, "warnings": record_warnings},
                    safe_auto=not conflicts and not record_warnings,
                )
                counts["exact_duplicate"] += 1

            candidates = [
                record
                for record in records
                if int(record["id"]) not in exact_member_ids and int(record["id"]) not in invalid_ids
            ]
            parent = {int(record["id"]): int(record["id"]) for record in candidates}

            def find(value: int) -> int:
                while parent[value] != value:
                    parent[value] = parent[parent[value]]
                    value = parent[value]
                return value

            def union(left: int, right: int) -> None:
                left_root, right_root = find(left), find(right)
                if left_root != right_root:
                    parent[right_root] = left_root

            pair_scores: dict[tuple[int, int], float] = {}
            for index, left in enumerate(candidates):
                for right in candidates[index + 1 :]:
                    similarity = _similarity(left["entity_title"], right["entity_title"])
                    if similarity >= 0.86:
                        union(int(left["id"]), int(right["id"]))
                        pair_scores[(int(left["id"]), int(right["id"]))] = similarity
            clusters: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
            for record in candidates:
                clusters[find(int(record["id"]))].append(record)
            for group in clusters.values():
                if len(group) < 2:
                    continue
                primary = _choose_primary(group, entity_type)
                related = [int(item["id"]) for item in group if item["id"] != primary["id"]]
                scores = [
                    score
                    for (left_id, right_id), score in pair_scores.items()
                    if left_id in {int(item["id"]) for item in group}
                    and right_id in {int(item["id"]) for item in group}
                ]
                _insert_quality_suggestion(
                    conn,
                    "near_duplicate",
                    entity_type,
                    int(primary["id"]),
                    related,
                    f"确认近似{ENTITY_LABELS[entity_type]}：{primary['entity_title']}",
                    f"发现 {len(group)} 条高度相似记录，需确认是否属于同一事项。",
                    min(scores) if scores else 0.86,
                    {"records": group, "fieldConflicts": _record_conflicts(group, entity_type)},
                    safe_auto=False,
                )
                counts["near_duplicate"] += 1

        documents = rows_to_dicts(conn.execute("SELECT * FROM documents ORDER BY id").fetchall())
        version_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for document in documents:
            group_key = document.get("version_group") or ""
            if group_key:
                version_groups[group_key].append(document)
            if document.get("analysis_status") == "failed":
                _insert_quality_suggestion(
                    conn,
                    "analysis_failure",
                    "document",
                    int(document["id"]),
                    [],
                    f"模型分析失败：{document['name']}",
                    document.get("analysis_error") or "模型分析未返回有效结果。",
                    0.95,
                    {"document": document},
                )
                counts["analysis_failure"] += 1
            category = document.get("doc_category")
            level = int(document.get("authority_level") or 5)
            expected = 4 if category == "meeting" else 5 if category == "weekly_report" else None
            if expected and level != expected:
                _insert_quality_suggestion(
                    conn,
                    "authority_anomaly",
                    "document",
                    int(document["id"]),
                    [],
                    f"修正文档权威：{document['name']}",
                    f"文档分类为 {category}，但当前权威等级为 L{level}，建议修正为 L{expected}。",
                    0.98,
                    {"document": document, "expectedLevel": expected},
                    safe_auto=True,
                )
                counts["authority_anomaly"] += 1

        for group_key, group in version_groups.items():
            if len(group) < 2:
                continue

            def version_score(document: dict[str, Any]) -> tuple[float, str]:
                name = document["name"]
                score = 0.0
                if document.get("extension") == ".docx":
                    score += 1
                if any(term in name for term in ["正式", "定稿", "终稿", "盖章", "20260212"]):
                    score += 5
                if any(term in name for term in ["初稿", "征求意见"]):
                    score -= 3
                score += float(document.get("authority_score") or 0) / 100
                return score, document.get("modified_at") or ""

            primary = max(group, key=version_score)
            related = [int(item["id"]) for item in group if item["id"] != primary["id"]]
            _insert_quality_suggestion(
                conn,
                "document_version",
                "document",
                int(primary["id"]),
                related,
                f"确认资料版本组：{primary['name']}",
                f"发现 {len(group)} 份可能属于同一资料的版本，建议确认当前有效版本。",
                0.9,
                {"documents": group, "versionGroup": group_key},
                safe_auto=False,
            )
            counts["document_version"] += 1

        conn.commit()
        total = sum(counts.values())
        return {"ok": True, "created": total, "counts": dict(counts)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _set_job(**patch: Any) -> None:
    with _job_lock:
        _job.update(patch)


def data_quality_status() -> dict[str, Any]:
    with _job_lock:
        status = dict(_job)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT suggestion_kind, COUNT(*) c FROM data_quality_suggestions WHERE status = 'pending' GROUP BY suggestion_kind"
        ).fetchall()
        status["pendingCounts"] = {row["suggestion_kind"]: row["c"] for row in rows}
        status["pending"] = sum(status["pendingCounts"].values())
    finally:
        conn.close()
    return status


def _quality_worker() -> None:
    _set_job(running=True, progress=0, total=6, created=0, counts={}, error="", startedAt=now_iso(), finishedAt="")
    try:
        _set_job(progress=1)
        result = analyze_data_quality()
        _set_job(
            running=False,
            progress=6,
            created=result["created"],
            counts=result["counts"],
            finishedAt=now_iso(),
        )
    except Exception as exc:
        _set_job(running=False, error=str(exc), finishedAt=now_iso())


def start_data_quality_job() -> dict[str, Any]:
    with _job_lock:
        if _job["running"]:
            return {**_job, "alreadyRunning": True}
        _job["running"] = True
    threading.Thread(target=_quality_worker, name="data-quality-worker", daemon=True).start()
    return {**data_quality_status(), "alreadyRunning": False}


def list_quality_suggestions(status: str = "pending") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        document_map = {
            int(row["id"]): row_to_dict(row) or {}
            for row in conn.execute(
                "SELECT id, name, path, doc_category, modified_at, effective_date, status FROM documents"
            ).fetchall()
        }
        where = "WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status,) if status else ()
        rows = rows_to_dicts(
            conn.execute(
                f"SELECT * FROM data_quality_suggestions {where} ORDER BY safe_auto DESC, confidence DESC, id DESC",
                params,
            ).fetchall()
        )
        for item in rows:
            try:
                item["relatedRecordIds"] = json.loads(item.pop("related_record_ids_json") or "[]")
            except Exception:
                item["relatedRecordIds"] = []
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except Exception:
                item["details"] = {}
            details = item["details"]
            entity_type = item.get("entity_type")
            primary_id = int(item.get("primary_record_id") or 0)
            record_ids = [primary_id, *[int(value) for value in item["relatedRecordIds"]]]
            record_ids = list(dict.fromkeys(value for value in record_ids if value > 0))

            if entity_type in ENTITY_TABLES and record_ids:
                config = ENTITY_TABLES[entity_type]
                placeholders = ",".join("?" for _ in record_ids)
                current_records = rows_to_dicts(
                    conn.execute(
                        f"SELECT *, {config['title']} AS entity_title FROM {config['table']} WHERE id IN ({placeholders})",
                        record_ids,
                    ).fetchall()
                )
                records_by_id = {int(record["id"]): record for record in current_records}
                ordered_records = [records_by_id[value] for value in record_ids if value in records_by_id]
                for record in ordered_records:
                    document_id = int(record.get(config["document"]) or 0)
                    document = document_map.get(document_id) or {}
                    record["source_document_id"] = document_id or None
                    record["source_document_name"] = document.get("name") or ""
                    record["source_document_date"] = (
                        document.get("effective_date") or document.get("modified_at") or ""
                    )
                    record["source_document_category"] = document.get("doc_category") or ""
                details["records"] = ordered_records
            elif entity_type == "document" and record_ids:
                current_documents = [document_map[value] for value in record_ids if value in document_map]
                if item.get("suggestion_kind") == "document_version":
                    details["documents"] = current_documents
                elif current_documents:
                    details["document"] = current_documents[0]
        return rows
    finally:
        conn.close()


def _copy_entity_evidence(conn: sqlite3.Connection, source_entity_id: int, target_entity_id: int) -> None:
    rows = rows_to_dicts(conn.execute("SELECT * FROM entity_evidence WHERE entity_id = ?", (source_entity_id,)).fetchall())
    for item in rows:
        attach_evidence(
            conn,
            target_entity_id,
            item.get("document_id"),
            item.get("snippet") or "",
            suggestion_id=item.get("suggestion_id"),
            locator=item.get("locator") or "",
            observed=json.loads(item.get("observed_json") or "{}"),
            confidence=float(item.get("confidence") or 0.7),
            method="merged_evidence",
        )


def _merge_records(
    conn: sqlite3.Connection,
    entity_type: str,
    primary_id: int,
    related_ids: list[int],
    field_choices: dict[str, Any],
) -> None:
    config = ENTITY_TABLES[entity_type]
    table = config["table"]
    primary = row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (primary_id,)).fetchone())
    if not primary:
        raise ValueError("主记录不存在")
    primary_entity_id = ensure_entity(conn, entity_type, primary_id, primary[config["title"]], False)
    for field, value in field_choices.items():
        if field in primary and field not in {"id", "created_at", "updated_at", "is_archived", "merged_into_id"}:
            conn.execute(f"UPDATE {table} SET {field} = ?, updated_at = ? WHERE id = ?", (value, now_iso(), primary_id))
    for related_id in related_ids:
        secondary = row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (related_id,)).fetchone())
        if not secondary or related_id == primary_id:
            continue
        if entity_type == "task" and secondary.get("task_kind") == "observation":
            from app.services.observations import record_resolution
            record_resolution(conn, secondary, "linked", primary_id, actor="admin", reason="数据治理合并")
        secondary_entity_id = ensure_entity(conn, entity_type, related_id, secondary[config["title"]], True)
        _copy_entity_evidence(conn, secondary_entity_id, primary_entity_id)
        document_id = secondary.get(config["document"])
        if document_id:
            attach_evidence(
                conn,
                primary_entity_id,
                int(document_id),
                secondary.get("description") or secondary[config["title"]],
                observed={key: secondary.get(key) for key in secondary if key in {"status", "progress", "owner", "due_date", "level"}},
                confidence=0.9,
                method="historical_merge",
            )
        conn.execute(
            f"UPDATE {table} SET is_archived = 1, merged_into_id = ?, archive_reason = ?, updated_at = ? WHERE id = ?",
            (primary_id, "数据治理合并", now_iso(), related_id),
        )
        conn.execute(
            "UPDATE project_entities SET status = 'archived', merged_into_entity_id = ?, updated_at = ? WHERE id = ?",
            (primary_entity_id, now_iso(), secondary_entity_id),
        )


def apply_quality_suggestion(suggestion_id: int, values: dict[str, Any] | None = None) -> dict[str, Any]:
    values = values or {}
    conn = get_connection()
    try:
        suggestion = row_to_dict(
            conn.execute("SELECT * FROM data_quality_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        )
        if not suggestion:
            return {"ok": False, "message": "治理建议不存在。"}
        if suggestion["status"] != "pending":
            return {"ok": False, "message": "该治理建议不是待确认状态。"}
        related_ids = json.loads(suggestion.get("related_record_ids_json") or "[]")
        details = json.loads(suggestion.get("details_json") or "{}")
        kind = suggestion["suggestion_kind"]
        entity_type = suggestion["entity_type"]
        primary_id = int(values.get("primaryRecordId") or suggestion.get("primary_record_id") or 0)
        action_details: dict[str, Any] = {"kind": kind, "primaryRecordId": primary_id, "relatedRecordIds": related_ids}

        if kind in {"exact_duplicate", "near_duplicate"}:
            selected_related = [int(item) for item in values.get("relatedRecordIds", related_ids) if int(item) != primary_id]
            _merge_records(conn, entity_type, primary_id, selected_related, values.get("fieldChoices") or {})
            action_details["relatedRecordIds"] = selected_related
        elif kind in {"task_match_review", "task_status_review"}:
            if entity_type != "task" or not primary_id:
                return {"ok": False, "message": "任务进度治理建议缺少正式任务。"}
            selected_related = [int(item) for item in related_ids if int(item) != primary_id]
            if kind == "task_match_review":
                _merge_records(conn, "task", primary_id, selected_related, {})
                action_details["relatedRecordIds"] = selected_related

            if details.get("fieldsOnly"):
                from app.services.task_reconcile import normalize_task_status
                chosen = values.get("fieldChoices") or {}
                allowed = details.get("fieldConflicts") or {}
                task = dict(conn.execute("SELECT * FROM tasks WHERE id=?",(primary_id,)).fetchone())
                source_date = str(details.get("sourceDate") or "")[:10]
                if chosen and task.get("status_as_of") and source_date < task["status_as_of"][:10]:
                    raise ValueError("该建议依据早于当前状态，请保留较新记录")
                updates = {}
                for field in ('status','progress','owner','due_date'):
                    if chosen.get(field) not in (None,''):
                        if str(chosen[field]) not in [str(v) for v in allowed.get(field,[])]:
                            raise ValueError("字段值不在本次建议内")
                        updates[field] = chosen[field]
                if 'progress' in updates:
                    updates['progress'] = parse_progress_percent(updates['progress'])
                if 'status' in updates:
                    updates['status'] = normalize_task_status(updates['status'])
                if updates.get('status')=='completed' or updates.get('progress')==100:
                    updates.update(status='completed',progress=100)
                if updates:
                    assignments=','.join(f"{key}=?" for key in updates)
                    conn.execute(f"UPDATE tasks SET {assignments},status_as_of=?,status_source_document_id=?,status_update_mode='manual_review',updated_at=? WHERE id=?",
                                 [*updates.values(),source_date,details.get('documentId'),now_iso(),primary_id])
                action_details['fieldChoices']=updates
            signal = details.get("signal") or {}
            signal_status = str(signal.get("status") or "").strip()
            signal_progress = signal.get("progress")
            document_id = int(details.get("documentId") or 0) or None
            source_date = str(details.get("sourceDate") or "").strip()[:10] or None
            confidence = float(signal.get("confidence") or suggestion.get("confidence") or 0)
            current = row_to_dict(conn.execute("SELECT status,status_as_of FROM tasks WHERE id = ?", (primary_id,)).fetchone()) or {}
            should_apply_signal = bool(signal_status and signal_progress is not None)
            if kind == "task_match_review" and current.get("status") == "completed" and signal_status != "completed":
                should_apply_signal = False
                action_details["statusPreserved"] = "completed"
            if current.get("status_as_of") and (not source_date or source_date < str(current["status_as_of"])[:10]):
                should_apply_signal = False
                action_details["statusPreserved"] = "保留较新资料状态"
            if kind == "task_match_review":
                # Identity confirmation adds evidence only. Status has its own review.
                should_apply_signal = False
                if signal_status and signal_progress is not None:
                    from app.services.task_reconcile import _create_review
                    _create_review(conn, "task_status_review", primary_id, selected_related,
                                   "确认任务字段变化", "关联已完成，执行状态需另行确认。", details, confidence)
            if should_apply_signal:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, progress = ?, status_as_of = COALESCE(?, status_as_of),
                        status_source_document_id = COALESCE(?, status_source_document_id),
                        status_confidence = ?, status_update_mode = 'manual_review', updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        signal_status,
                        max(0, min(100, int(signal_progress))),
                        source_date,
                        document_id,
                        confidence,
                        now_iso(),
                        primary_id,
                    ),
                )
                action_details["appliedSignal"] = {
                    "status": signal_status,
                    "progress": max(0, min(100, int(signal_progress))),
                    "sourceDate": source_date,
                }
        elif kind == "invalid_record":
            if entity_type == "task":
                original = conn.execute("SELECT * FROM tasks WHERE id=?", (primary_id,)).fetchone()
                if original and original["task_kind"] == "observation":
                    from app.services.observations import record_resolution
                    record_resolution(conn, dict(original), "ignored", actor="admin", reason="数据治理判定为误识别")
            config = ENTITY_TABLES[entity_type]
            conn.execute(
                f"UPDATE {config['table']} SET is_archived = 1, archive_reason = ?, updated_at = ? WHERE id = ?",
                ("数据治理判定为误识别", now_iso(), primary_id),
            )
            conn.execute(
                "UPDATE project_entities SET status = 'archived', updated_at = ? WHERE entity_type = ? AND record_id = ?",
                (now_iso(), entity_type, primary_id),
            )
        elif kind == "document_version":
            document_ids = sorted(
                {
                    primary_id,
                    int(suggestion.get("primary_record_id") or 0),
                    *[int(item) for item in related_ids],
                }
                - {0}
            )
            version_group = details.get("versionGroup") or ""
            placeholders = ",".join("?" for _ in document_ids)
            conn.execute(
                f"UPDATE documents SET version_group = ?, primary_version_id = ?, is_current = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE id IN ({placeholders})",
                (version_group, primary_id, primary_id, *document_ids),
            )
        elif kind == "authority_anomaly":
            expected = int(details.get("expectedLevel") or 5)
            score = 68 if expected == 4 else 55
            scope = "会议决议、过程协调、阶段安排" if expected == 4 else "当前进度、周计划、问题风险、过程记录"
            conn.execute(
                """
                UPDATE documents SET authority_level = ?, authority_score = ?, authority_scope = ?,
                    authority_status = 'confirmed', authority_reason = ?, authority_updated_at = ?
                WHERE id = ?
                """,
                (expected, score, scope, "数据治理按文档分类修正权威等级。", now_iso(), primary_id),
            )
        elif kind == "analysis_failure":
            action_details["acknowledged"] = True
        else:
            return {"ok": False, "message": f"暂不支持治理类型 {kind}。"}

        conn.execute(
            "UPDATE data_quality_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
            (now_iso(), suggestion_id),
        )
        conn.execute(
            "INSERT INTO data_quality_actions(suggestion_id, action, details_json, created_at) VALUES(?, ?, ?, ?)",
            (suggestion_id, "apply", json.dumps(action_details, ensure_ascii=False), now_iso()),
        )
        conn.commit()
        return {"ok": True, "kind": kind, **action_details}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "message": str(exc)}
    finally:
        conn.close()


def dismiss_quality_suggestion(suggestion_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE data_quality_suggestions SET status = 'dismissed', applied_at = ? WHERE id = ? AND status = 'pending'",
            (now_iso(), suggestion_id),
        )
        conn.execute(
            "INSERT INTO data_quality_actions(suggestion_id, action, details_json, created_at) VALUES(?, 'dismiss', '{}', ?)",
            (suggestion_id, now_iso()),
        )
        conn.commit()
        return {"ok": bool(cursor.rowcount)}
    finally:
        conn.close()


def apply_all_safe_quality_suggestions() -> dict[str, Any]:
    pending = [item for item in list_quality_suggestions("pending") if item.get("safe_auto")]
    applied = 0
    failed: list[dict[str, Any]] = []
    for item in pending:
        result = apply_quality_suggestion(int(item["id"]))
        if result.get("ok"):
            applied += 1
        else:
            failed.append({"id": item["id"], "message": result.get("message") or "应用失败"})
    return {"ok": not failed, "total": len(pending), "applied": applied, "failed": failed}


def apply_selected_quality_suggestions(items: list[dict[str, Any]]) -> dict[str, Any]:
    applied = 0
    failed: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        suggestion_id = int(item.get("id") or 0)
        if suggestion_id <= 0 or suggestion_id in seen:
            continue
        seen.add(suggestion_id)
        result = apply_quality_suggestion(suggestion_id, item.get("values") or {})
        results.append({"id": suggestion_id, **result})
        if result.get("ok"):
            applied += 1
        else:
            failed.append({"id": suggestion_id, "message": result.get("message") or "应用失败"})
    return {
        "ok": not failed,
        "total": len(seen),
        "applied": applied,
        "failed": failed,
        "results": results,
    }
