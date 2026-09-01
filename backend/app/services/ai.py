from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from app.database import (
    DEFAULT_INTELLIGENT_ANALYSIS,
    get_connection,
    get_setting,
    now_iso,
    row_to_dict,
    rows_to_dicts,
    set_setting,
)
from app.services.extractors import compact_text
from app.services.authority import (
    authority_source_payload,
    authority_weight,
    question_scope,
    source_conflict_note,
)


CHUNK_SIZE = 900
CHUNK_OVERLAP = 140
QA_SOURCE_LIMIT = 6
QA_CONTEXT_SOURCE_LIMIT = 4
QA_CONTEXT_CHARS_PER_SOURCE = 620
RETRIEVAL_LIMIT = 12
LIKE_CANDIDATE_LIMIT = 80
MIN_SOURCE_SCORE = 0.3

_knowledge_job_lock = threading.Lock()
_knowledge_job = {
    "running": False,
    "progress": 0,
    "total": 0,
    "indexedDocuments": 0,
    "chunks": 0,
    "vectors": 0,
    "failed": 0,
    "error": "",
    "startedAt": "",
    "finishedAt": "",
}


def _set_knowledge_job(**patch) -> None:
    with _knowledge_job_lock:
        _knowledge_job.update(patch)


def knowledge_rebuild_status() -> dict[str, Any]:
    with _knowledge_job_lock:
        return dict(_knowledge_job)


def ai_config() -> dict[str, Any]:
    conn = get_connection()
    try:
        return {**DEFAULT_INTELLIGENT_ANALYSIS, **get_setting(conn, "intelligent_analysis", {})}
    finally:
        conn.close()


def _url(base: str, endpoint: str) -> str:
    base = (base or "").rstrip("/")
    if not base:
        raise ValueError("API 地址未配置")
    if base.endswith(endpoint) or base.endswith(f"/v1{endpoint}"):
        return base
    return f"{base}{endpoint}"


def _get_json(url: str, api_key: str = "", timeout: int = 60) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"模型服务返回 {exc.code}: {detail}") from exc


def _post_json(url: str, payload: dict[str, Any], api_key: str = "", timeout: int = 60) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"模型服务返回 {exc.code}: {detail}") from exc


def list_chat_models(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(config or ai_config())}
    if not config.get("apiBaseUrl"):
        raise ValueError("聊天模型 API 地址未配置")
    started = time.perf_counter()
    data = _get_json(
        _url(config["apiBaseUrl"], "/models"),
        config.get("apiKey", ""),
        int(config.get("timeoutSeconds") or 60),
    )
    raw_models = data.get("data") if isinstance(data, dict) else []
    models = []
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict) and item.get("id"):
                models.append({"id": str(item["id"]), "ownedBy": item.get("owned_by", "")})
            elif isinstance(item, str):
                models.append({"id": item, "ownedBy": ""})
    return {
        "ok": True,
        "models": models,
        "defaultModel": models[0]["id"] if models else "",
        "elapsedMs": round((time.perf_counter() - started) * 1000),
    }


def _field_path(data: Any, path: str) -> Any:
    current = data
    for part in (path or "").split("."):
        if part == "":
            continue
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


def _save_model_name(model_name: str) -> None:
    conn = get_connection()
    try:
        current = {**DEFAULT_INTELLIGENT_ANALYSIS, **get_setting(conn, "intelligent_analysis", {})}
        current["modelName"] = model_name
        set_setting(conn, "intelligent_analysis", current)
        conn.commit()
    finally:
        conn.close()


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("content"):
                    parts.append(str(item["content"]))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _extract_chat_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    for value in (
        message.get("content"),
        message.get("text"),
        first.get("text"),
        data.get("content"),
        data.get("text"),
        data.get("response"),
    ):
        text = _content_to_text(value).strip()
        if text:
            return text
    return ""


def _without_thinking_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    updated = [dict(message) for message in messages]
    for message in reversed(updated):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                message["content"] = f"{content}\n\n/no_think\n请直接输出最终答案，不要输出推理过程。"
            break
    return updated


def _chat_payload(model_name: str, messages: list[dict[str, str]], max_tokens: int, disable_thinking: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if disable_thinking:
        payload["enable_thinking"] = False
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def chat_completion(
    messages: list[dict[str, str]],
    config: dict[str, Any] | None = None,
    max_tokens: int = 1000,
    retry_on_model_error: bool = True,
    disable_thinking: bool = False,
) -> str:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(config or ai_config())}
    if not config.get("apiBaseUrl"):
        raise ValueError("聊天模型 API 地址未配置")
    model_name = config.get("modelName")
    if not model_name:
        models = list_chat_models(config)
        model_name = models.get("defaultModel")
        if model_name:
            _save_model_name(model_name)
    if not model_name:
        raise ValueError("聊天模型名称未配置，且未能从 /models 自动读取模型")
    payload = _chat_payload(model_name, messages, max_tokens, disable_thinking=disable_thinking)
    try:
        data = _post_json(
            _url(config["apiBaseUrl"], "/chat/completions"),
            payload,
            config.get("apiKey", ""),
            int(config.get("timeoutSeconds") or 60),
        )
    except RuntimeError as exc:
        if not retry_on_model_error or "404" not in str(exc):
            raise
        refreshed = list_chat_models(config)
        fallback_model = refreshed.get("defaultModel")
        if not fallback_model or fallback_model == model_name:
            raise
        _save_model_name(fallback_model)
        payload = _chat_payload(fallback_model, messages, max_tokens, disable_thinking=disable_thinking)
        data = _post_json(
            _url(config["apiBaseUrl"], "/chat/completions"),
            payload,
            config.get("apiKey", ""),
            int(config.get("timeoutSeconds") or 60),
        )
    answer = _extract_chat_content(data)
    if answer:
        return answer

    no_think_payload = _chat_payload(
        payload["model"],
        _without_thinking_messages(messages),
        max(max_tokens, 1800),
        disable_thinking=True,
    )
    try:
        data = _post_json(
            _url(config["apiBaseUrl"], "/chat/completions"),
            no_think_payload,
            config.get("apiKey", ""),
            int(config.get("timeoutSeconds") or 60),
        )
        answer = _extract_chat_content(data)
        if answer:
            return answer
    except RuntimeError:
        # Some OpenAI-compatible servers reject thinking-control fields. Retry
        # once with only the prompt-level /no_think instruction.
        no_think_payload.pop("enable_thinking", None)
        no_think_payload.pop("chat_template_kwargs", None)
        data = _post_json(
            _url(config["apiBaseUrl"], "/chat/completions"),
            no_think_payload,
            config.get("apiKey", ""),
            int(config.get("timeoutSeconds") or 60),
        )
        answer = _extract_chat_content(data)
        if answer:
            return answer
    finish_reason = ""
    try:
        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
    except Exception:
        finish_reason = ""
    raise ValueError(f"模型未返回最终答案内容{f'，结束原因：{finish_reason}' if finish_reason else ''}")


def test_chat_model() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        models = list_chat_models()
        answer = chat_completion(
            [
                {"role": "system", "content": "你是项目管理系统的模型连通性测试助手。"},
                {"role": "user", "content": "请用一句中文回复：模型连接正常。"},
            ],
            max_tokens=80,
        )
        return {
            "ok": True,
            "answer": answer,
            "models": models.get("models", []),
            "modelName": ai_config().get("modelName") or models.get("defaultModel", ""),
            "elapsedMs": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {"ok": False, "answer": "", "elapsedMs": round((time.perf_counter() - started) * 1000), "error": str(exc)}


def embedding(text: str, config: dict[str, Any] | None = None) -> list[float]:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(config or ai_config())}
    if not config.get("embeddingApiBaseUrl") or not config.get("embeddingModelName"):
        raise ValueError("Embedding API 地址或模型名称未配置")
    payload = {
        config.get("embeddingTextField") or "text": text,
        config.get("embeddingModelField") or "model": config["embeddingModelName"],
    }
    data = _post_json(
        config["embeddingApiBaseUrl"],
        payload,
        config.get("embeddingApiKey", ""),
        int(config.get("timeoutSeconds") or 60),
    )
    vector = _field_path(data, config.get("embeddingVectorPath") or "embedding")
    if not isinstance(vector, list) or not vector:
        raise ValueError("Embedding 返回结果中未找到向量数组")
    return [float(item) for item in vector]


def split_chunks(text: str) -> list[str]:
    text = compact_text(text, 100000)
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start : start + CHUNK_SIZE].strip()
        if len(chunk) >= 30:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def index_document_knowledge(
    document_id: int,
    text: str,
    document_name: str,
    config: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(config or ai_config())}
    owns_connection = conn is None
    conn = conn or get_connection()
    now = now_iso()
    try:
        conn.execute("DELETE FROM knowledge_vectors WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE document_id = ?)", (document_id,))
        conn.execute("DELETE FROM knowledge_chunks_fts WHERE rowid IN (SELECT id FROM knowledge_chunks WHERE document_id = ?)", (document_id,))
        conn.execute("DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,))
        chunks = split_chunks(text)
        indexed_vectors = 0
        for index, chunk in enumerate(chunks):
            cursor = conn.execute(
                """
                INSERT INTO knowledge_chunks(document_id, chunk_index, text, summary, char_length, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (document_id, index, chunk, compact_text(chunk, 180), len(chunk), now, now),
            )
            chunk_id = cursor.lastrowid
            conn.execute("INSERT INTO knowledge_chunks_fts(rowid, text) VALUES(?, ?)", (chunk_id, chunk))
            try:
                vector = embedding(chunk, config)
                conn.execute(
                    """
                    INSERT INTO knowledge_vectors(chunk_id, embedding_model, vector_json, dimension, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (chunk_id, config.get("embeddingModelName", ""), json.dumps(vector), len(vector), now),
                )
                indexed_vectors += 1
            except Exception:
                pass
        conn.execute(
            """
            UPDATE documents
            SET knowledge_status = ?, knowledge_indexed_at = ?, knowledge_error = ?
            WHERE id = ?
            """,
            ("indexed" if chunks else "empty", now, "" if chunks else "未抽取到可索引正文", document_id),
        )
        try:
            from app.services.authority import analyze_document_authority

            analyze_document_authority(document_id, text, conn=conn)
        except Exception:
            pass
        if owns_connection:
            conn.commit()
        return {"ok": True, "chunks": len(chunks), "vectors": indexed_vectors}
    except Exception as exc:
        if owns_connection:
            conn.rollback()
        conn.execute(
            "UPDATE documents SET knowledge_status = 'failed', knowledge_error = ? WHERE id = ?",
            (str(exc), document_id),
        )
        if owns_connection:
            conn.commit()
        return {"ok": False, "error": str(exc)}
    finally:
        if owns_connection:
            conn.close()


def rebuild_knowledge() -> dict[str, Any]:
    from app.services.extractors import extract_text

    conn = get_connection()
    try:
        docs = rows_to_dicts(conn.execute("SELECT * FROM documents WHERE status = 'indexed' ORDER BY id").fetchall())
    finally:
        conn.close()
    counts = {"documents": 0, "chunks": 0, "vectors": 0, "failed": 0}
    for doc in docs:
        result = extract_text(Path(doc["path"]))
        if not result.text:
            counts["failed"] += 1
            continue
        indexed = index_document_knowledge(doc["id"], result.text, doc["name"])
        counts["documents"] += 1
        counts["chunks"] += indexed.get("chunks", 0)
        counts["vectors"] += indexed.get("vectors", 0)
        if not indexed.get("ok"):
            counts["failed"] += 1
    conn = get_connection()
    try:
        set_setting(conn, "knowledge_last_rebuild_at", now_iso())
        conn.commit()
    finally:
        conn.close()
    return counts


def _knowledge_rebuild_worker() -> None:
    from app.services.extractors import extract_text

    conn = get_connection()
    try:
        docs = rows_to_dicts(conn.execute("SELECT * FROM documents WHERE status = 'indexed' ORDER BY id").fetchall())
    finally:
        conn.close()
    _set_knowledge_job(
        running=True,
        progress=0,
        total=len(docs),
        indexedDocuments=0,
        chunks=0,
        vectors=0,
        failed=0,
        error="",
        startedAt=now_iso(),
        finishedAt="",
    )
    counts = {"documents": 0, "chunks": 0, "vectors": 0, "failed": 0}
    try:
        for index, doc in enumerate(docs, start=1):
            result = extract_text(Path(doc["path"]))
            if not result.text:
                counts["failed"] += 1
            else:
                indexed = index_document_knowledge(doc["id"], result.text, doc["name"])
                counts["documents"] += 1
                counts["chunks"] += indexed.get("chunks", 0)
                counts["vectors"] += indexed.get("vectors", 0)
                if not indexed.get("ok"):
                    counts["failed"] += 1
            _set_knowledge_job(
                progress=index,
                indexedDocuments=counts["documents"],
                chunks=counts["chunks"],
                vectors=counts["vectors"],
                failed=counts["failed"],
            )
        conn = get_connection()
        try:
            set_setting(conn, "knowledge_last_rebuild_at", now_iso())
            conn.commit()
        finally:
            conn.close()
        _set_knowledge_job(running=False, finishedAt=now_iso())
    except Exception as exc:
        _set_knowledge_job(running=False, error=str(exc), finishedAt=now_iso())


def start_knowledge_rebuild_job() -> dict[str, Any]:
    with _knowledge_job_lock:
        if _knowledge_job["running"]:
            return {"ok": True, "alreadyRunning": True, **dict(_knowledge_job)}
    _set_knowledge_job(
        running=True,
        progress=0,
        total=0,
        indexedDocuments=0,
        chunks=0,
        vectors=0,
        failed=0,
        error="",
        startedAt=now_iso(),
        finishedAt="",
    )
    thread = threading.Thread(target=_knowledge_rebuild_worker, daemon=True)
    thread.start()
    return {"ok": True, "started": True, **knowledge_rebuild_status()}


def knowledge_status() -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM documents WHERE knowledge_status = 'indexed') AS indexed_documents,
                (SELECT COUNT(*) FROM knowledge_chunks) AS chunks,
                (SELECT COUNT(*) FROM knowledge_vectors) AS vectors,
                (SELECT COUNT(*) FROM documents WHERE knowledge_status = 'failed') AS failed_documents
            """
        ).fetchone()
        return {
            **row_to_dict(row),
            "lastRebuildAt": get_setting(conn, "knowledge_last_rebuild_at", ""),
        }
    finally:
        conn.close()


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _query_terms(query: str) -> list[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", query).strip().lower()
    words = [word for word in normalized.split() if len(word) >= 2]
    chinese_text = "".join(re.findall(r"[\u4e00-\u9fff]+", query))
    stop_chars = set("的了吗呢啊吧是有和与及或在对把被为以从到中上下一个这个那个请问关于项目")
    compacted = "".join(char for char in chinese_text if char not in stop_chars)
    grams: list[str] = []
    for size in (4, 3, 2):
        grams.extend(compacted[index : index + size] for index in range(max(0, len(compacted) - size + 1)))
    priority_terms = [
        term
        for term in [
            "建设目标",
            "项目目标",
            "建设内容",
            "项目范围",
            "采购需求",
            "合同要求",
            "交付物",
            "验收",
            "里程碑",
            "实施计划",
            "当前进度",
            "会议纪要",
            "周报",
            "风险",
            "变更",
            "资源",
            "部署",
            "国产化",
            "OA",
            "集成",
        ]
        if term in query
    ]
    return _dedupe(priority_terms + words + grams)[:18]


def _tokenize_query(query: str) -> str:
    terms = [term.replace('"', "") for term in _query_terms(query) if term.strip()]
    return " OR ".join(f'"{term}"' for term in terms[:8]) or query


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _term_score(row: dict[str, Any], terms: list[str]) -> float:
    text = f"{row.get('document_name') or ''}\n{row.get('text') or ''}"
    score = 0.0
    for term in terms:
        occurrences = text.count(term)
        if not occurrences:
            continue
        weight = min(len(term), 6) / 6
        if term in (row.get("document_name") or ""):
            weight += 0.35
        score += min(occurrences, 4) * weight
    return score


def _baseline_source(query: str) -> dict[str, Any] | None:
    scope = question_scope(query)
    if scope not in {"scope", "schedule", "deliverable", "acceptance", "resource", "general"} and not any(
        word in query for word in ["目标", "范围", "建设内容", "计划", "里程碑", "交付", "验收", "资源"]
    ):
        return None
    conn = get_connection()
    try:
        goals = get_setting(conn, "project_goals", []) or []
        breakdown = get_setting(conn, "project_plan_breakdown", []) or []
        version = get_setting(conn, "project_plan_version", "") or ""
    finally:
        conn.close()
    if not goals and not breakdown:
        return None
    parts = [f"项目计划基线版本：{version}"]
    if goals:
        parts.append("建设目标：")
        parts.extend(f"- {goal}" for goal in goals)
    if breakdown:
        parts.append("阶段计划：")
        for phase in breakdown:
            parts.append(f"- {phase.get('phase', '')}（{phase.get('period', '')}）：{phase.get('objective', '')}")
            for work in (phase.get("work") or [])[:4]:
                parts.append(f"  - {work}")
            for deliverable in (phase.get("deliverables") or [])[:4]:
                parts.append(f"  - 交付物：{deliverable}")
    text = compact_text("\n".join(parts), 6000)
    return {
        "id": -1,
        "document_id": None,
        "document_name": "项目计划基线",
        "document_path": "",
        "text": text,
        "chunk_index": 0,
        "authority_level": 3,
        "authority_score": 78,
        "authority_scope": "建设目标、实施计划、里程碑、交付物基线",
        "is_current": 1,
        "score": 0.7 + min(_term_score({"document_name": "项目计划基线", "text": text}, _query_terms(query)) / 10, 0.6),
        "source_type": "baseline",
    }


def _document_source_type(item: dict[str, Any]) -> str:
    if item.get("source_type"):
        return str(item["source_type"])
    category = item.get("doc_category") or ""
    name = item.get("document_name") or ""
    if category == "contract_tender" and any(term in name for term in ["需求", "采购需求"]):
        return "requirement"
    return category or "document"


def retrieve(query: str) -> list[dict[str, Any]]:
    conn = get_connection()
    hits: dict[int, dict[str, Any]] = {}
    terms = _query_terms(query)
    try:
        fts_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path,
                       d.doc_category, d.authority_level, d.authority_score, d.authority_scope,
                       d.version_label, d.version_group, d.effective_date, d.is_current,
                       bm25(knowledge_chunks_fts) AS rank
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE knowledge_chunks_fts MATCH ?
                  AND d.is_current = 1
                ORDER BY rank
                LIMIT ?
                """,
                (_tokenize_query(query), RETRIEVAL_LIMIT),
            ).fetchall()
        )
        for row in fts_rows:
            hits[row["id"]] = {**row, "score": 0.45 + min(_term_score(row, terms) / 10, 0.45)}
    except Exception:
        pass
    try:
        if terms:
            where = " OR ".join(["c.text LIKE ?"] * len(terms))
            like_rows = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT c.*, d.name AS document_name, d.path AS document_path,
                           d.doc_category, d.authority_level, d.authority_score, d.authority_scope,
                           d.version_label, d.version_group, d.effective_date, d.is_current
                    FROM knowledge_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE d.is_current = 1 AND ({where})
                    ORDER BY c.updated_at DESC, c.id DESC
                    LIMIT ?
                    """,
                    [*[f"%{term}%" for term in terms], LIKE_CANDIDATE_LIMIT],
                ).fetchall()
            )
            scored_like_rows = sorted(
                ((0.15 + min(_term_score(row, terms) / 8, 0.75), row) for row in like_rows),
                reverse=True,
                key=lambda item: item[0],
            )
            for score, row in scored_like_rows[:RETRIEVAL_LIMIT]:
                if row["id"] in hits:
                    hits[row["id"]]["score"] += score
                else:
                    hits[row["id"]] = {**row, "score": score}
    except Exception:
        pass
    try:
        query_vector = embedding(query)
        vector_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path,
                       d.doc_category, d.authority_level, d.authority_score, d.authority_scope,
                       d.version_label, d.version_group, d.effective_date, d.is_current,
                       v.vector_json
                FROM knowledge_vectors v
                JOIN knowledge_chunks c ON c.id = v.chunk_id
                JOIN documents d ON d.id = c.document_id
                WHERE d.is_current = 1
                """
            ).fetchall()
        )
        scored = []
        for row in vector_rows:
            score = _cosine(query_vector, json.loads(row["vector_json"]))
            scored.append((score, row))
        for score, row in sorted(scored, reverse=True, key=lambda item: item[0])[:RETRIEVAL_LIMIT]:
            if row["id"] in hits:
                hits[row["id"]]["score"] += score
            else:
                hits[row["id"]] = {**row, "score": score}
    except Exception:
        pass
    finally:
        conn.close()

    baseline = _baseline_source(query)
    if baseline:
        hits[-1] = baseline

    scope = question_scope(query)
    for item in hits.values():
        level = int(item.get("authority_level") or 5)
        bonus = authority_weight(level)
        if item.get("source_type") == "baseline":
            bonus += 0.18
        if scope == "progress" and level in {4, 5}:
            bonus += 0.14
        elif scope in {"scope", "schedule", "deliverable", "acceptance", "cost", "resource", "change"} and level <= 3:
            bonus += 0.16
        if not int(item.get("is_current", 1)):
            bonus -= 0.18
        item["relevance_score"] = float(item.get("score") or 0)
        item["score"] = item["relevance_score"] + bonus
    ranked = sorted(hits.values(), reverse=True, key=lambda item: item["score"])
    deduplicated: list[dict[str, Any]] = []
    seen_versions: set[str] = set()
    for item in ranked:
        if float(item.get("score") or 0) < MIN_SOURCE_SCORE:
            continue
        version_key = "baseline" if item.get("source_type") == "baseline" else (
            item.get("version_group") or f"document-{item.get('document_id')}"
        )
        if version_key in seen_versions:
            continue
        seen_versions.add(str(version_key))
        deduplicated.append(item)
        if len(deduplicated) >= QA_SOURCE_LIMIT:
            break
    return deduplicated


def _source_payload(item: dict[str, Any], primary_level: int, conflict_note: str) -> dict[str, Any]:
    if item.get("source_type") == "baseline":
        return {
            "documentId": None,
            "documentName": "项目计划基线",
            "snippet": compact_text(item["text"], 260),
            "score": round(float(item.get("score") or 0), 4),
            "authorityLevel": 3,
            "authorityLabel": "项目计划基线",
            "authorityScore": 78,
            "isPrimaryBasis": True,
            "conflictNote": "",
            "sourceType": "baseline",
        }
    return {
        "documentId": item["document_id"],
        "documentName": item["document_name"],
        "snippet": compact_text(item["text"], 260),
        "score": round(float(item.get("score") or 0), 4),
        **authority_source_payload(
            item,
            primary=int(item.get("authority_level") or 5) == primary_level,
            conflict_note=conflict_note if int(item.get("authority_level") or 5) > primary_level else "",
        ),
        "sourceType": _document_source_type(item),
    }


def answer_question(question: str) -> dict[str, Any]:
    sources = retrieve(question)
    scope = question_scope(question)
    primary_level = min([int(item.get("authority_level") or 5) for item in sources], default=5)
    conflict_note = source_conflict_note(
        [
            {
                "authorityLevel": int(item.get("authority_level") or 5),
                "authorityScore": float(item.get("authority_score") or 45),
            }
            for item in sources
        ],
        scope,
    )
    fallback_sources = [_source_payload(item, primary_level, conflict_note) for item in sources]
    try:
        from app.services.wiki import answer_from_wiki

        wiki_answer = answer_from_wiki(question, fallback_sources)
        if wiki_answer:
            wiki_answer["authorityScope"] = scope
            wiki_answer["conflictNote"] = wiki_answer.get("conflictNote") or conflict_note
            return wiki_answer
    except Exception:
        pass
    if not sources:
        return {
            "answer": "未在项目资料中找到依据。",
            "structured": {
                "answer_summary": "未在项目资料中找到依据。",
                "key_points": [],
                "evidence": [],
                "unknowns": ["当前 Wiki 和资料索引中没有检索到可直接支撑该问题的内容。"],
            },
            "sources": [],
            "retrievalMode": "hybrid",
            "authorityScope": scope,
            "conflictNote": "",
        }

    authority_instruction = (
        "必须同时考虑相关度、文档权威层级和时效性。"
        "涉及建设目标、范围、验收、交付物、工期、费用时，优先引用合同、需求、招投标、正式确认文件和项目计划基线；"
        "会议纪要、周报只能作为过程补充。若低权威资料与高权威资料存在不同表述，应在 unknowns 中提示需确认是否形成正式变更。"
    )
    context = "\n\n".join(
        f"【来源{index + 1}】{item.get('document_name')}（权威：{_source_payload(item, primary_level, conflict_note).get('authorityLabel')}，权威分：{item.get('authority_score') or 78}）\n{compact_text(item['text'], QA_CONTEXT_CHARS_PER_SOURCE)}"
        for index, item in enumerate(sources[:QA_CONTEXT_SOURCE_LIMIT])
    )
    try:
        answer = chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是国产化 OA 集成项目管理系统的资料问答助手。"
                        "只能依据提供的项目资料回答，不得编造。"
                        "请只输出 JSON 对象，字段为 answer_summary、key_points、evidence、unknowns、sources。"
                        "key_points、evidence、unknowns 必须是字符串数组；sources 可以是来源编号数组。"
                        "回答要能基于资料作归纳，但每个判断必须能回溯到资料依据。"
                        "不要输出 Markdown，不要使用 #、*、表格符号。"
                        f"{authority_instruction}"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"问题：{question}\n\n"
                        "请基于以下资料给出结构化回答。"
                        "如果资料不足，请明确写入 unknowns，不要用猜测补足。\n"
                        f"权威规则：{authority_instruction}\n"
                        f"冲突提示：{conflict_note or '当前未识别到明显权威冲突。'}\n\n"
                        f"资料片段：\n{context}"
                    ),
                },
            ],
            max_tokens=1100,
        )
        structured = _parse_answer_json(answer)
        if conflict_note:
            unknowns = structured.setdefault("unknowns", [])
            if conflict_note not in unknowns:
                unknowns.append(conflict_note)
    except Exception as exc:
        answer = f"已找到相关项目资料，但模型生成回答失败：{exc}"
        structured = {
            "answer_summary": answer,
            "key_points": [],
            "evidence": [],
            "unknowns": [conflict_note] if conflict_note else [],
        }
    return {
        "answer": structured.get("answer_summary") or answer,
        "structured": structured,
        "sources": fallback_sources,
        "retrievalMode": "hybrid",
        "authorityScope": scope,
        "conflictNote": conflict_note,
    }

def _parse_answer_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").replace("json\n", "", 1).replace("JSON\n", "", 1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except Exception:
        return {"answer_summary": raw.replace("#", "").replace("*", ""), "key_points": [], "evidence": [], "unknowns": []}
    return {
        "answer_summary": str(data.get("answer_summary") or data.get("answer") or "").replace("#", "").replace("*", ""),
        "key_points": [str(item).replace("#", "").replace("*", "") for item in data.get("key_points", []) if item],
        "evidence": [str(item).replace("#", "").replace("*", "") for item in data.get("evidence", []) if item],
        "unknowns": [str(item).replace("#", "").replace("*", "") for item in data.get("unknowns", []) if item],
    }
