from __future__ import annotations

import json
import math
import re
import sqlite3
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


CHUNK_SIZE = 900
CHUNK_OVERLAP = 140
QA_SOURCE_LIMIT = 6
QA_CONTEXT_SOURCE_LIMIT = 4
QA_CONTEXT_CHARS_PER_SOURCE = 620
RETRIEVAL_LIMIT = 12
LIKE_CANDIDATE_LIMIT = 80
MIN_SOURCE_SCORE = 0.3


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


def chat_completion(messages: list[dict[str, str]], config: dict[str, Any] | None = None, max_tokens: int = 1000) -> str:
    config = {**DEFAULT_INTELLIGENT_ANALYSIS, **(config or ai_config())}
    if not config.get("apiBaseUrl"):
        raise ValueError("聊天模型 API 地址未配置")
    model_name = config.get("modelName")
    if not model_name:
        models = list_chat_models(config)
        model_name = models.get("defaultModel")
        if model_name:
            conn = get_connection()
            try:
                current = {**DEFAULT_INTELLIGENT_ANALYSIS, **get_setting(conn, "intelligent_analysis", {})}
                if not current.get("modelName"):
                    current["modelName"] = model_name
                    set_setting(conn, "intelligent_analysis", current)
                    conn.commit()
            finally:
                conn.close()
    if not model_name:
        raise ValueError("聊天模型名称未配置，且未能从 /models 自动读取模型")
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    data = _post_json(
        _url(config["apiBaseUrl"], "/chat/completions"),
        payload,
        config.get("apiKey", ""),
        int(config.get("timeoutSeconds") or 60),
    )
    return data["choices"][0]["message"]["content"].strip()


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
    stop_chars = set("的是了吗呢啊和与及或在有为对把将被请问一下哪些什么多少是否")
    compacted = "".join(char for char in chinese_text if char not in stop_chars)
    grams: list[str] = []
    for size in (4, 3, 2):
        grams.extend(compacted[index : index + size] for index in range(max(0, len(compacted) - size + 1)))
    priority_terms = [
        term
        for term in [
            "项目",
            "建设",
            "目标",
            "建设目标",
            "计划",
            "里程碑",
            "任务",
            "风险",
            "交付",
            "交付物",
            "服务器",
            "资源",
            "需求",
            "验收",
            "会议",
            "变更",
        ]
        if term in query
    ]
    return _dedupe(priority_terms + words + grams)[:16]


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


def retrieve(query: str) -> list[dict[str, Any]]:
    conn = get_connection()
    hits: dict[int, dict[str, Any]] = {}
    try:
        fts_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT c.*, d.name AS document_name, d.path AS document_path, bm25(knowledge_chunks_fts) AS rank
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE knowledge_chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (_tokenize_query(query), RETRIEVAL_LIMIT),
            ).fetchall()
        )
        terms = _query_terms(query)
        for row in fts_rows:
            hits[row["id"]] = {**row, "score": 0.45 + min(_term_score(row, terms) / 10, 0.45)}
    except Exception:
        pass
    try:
        terms = _query_terms(query)
        if terms:
            where = " OR ".join(["c.text LIKE ?"] * len(terms))
            like_rows = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT c.*, d.name AS document_name, d.path AS document_path
                    FROM knowledge_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE {where}
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
                SELECT c.*, d.name AS document_name, d.path AS document_path, v.vector_json
                FROM knowledge_vectors v
                JOIN knowledge_chunks c ON c.id = v.chunk_id
                JOIN documents d ON d.id = c.document_id
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
    ranked = sorted(hits.values(), reverse=True, key=lambda item: item["score"])
    return [item for item in ranked if float(item.get("score") or 0) >= MIN_SOURCE_SCORE][:QA_SOURCE_LIMIT]


def answer_question(question: str) -> dict[str, Any]:
    sources = retrieve(question)
    if not sources:
        return {
            "answer": "未在项目资料中找到依据。",
            "sources": [],
            "retrievalMode": "hybrid",
        }
    context = "\n\n".join(
        f"来源{index + 1}：{item['document_name']}\n{compact_text(item['text'], QA_CONTEXT_CHARS_PER_SOURCE)}"
        for index, item in enumerate(sources[:QA_CONTEXT_SOURCE_LIMIT])
    )
    try:
        answer = chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是项目资料问答助手，只能依据给定项目资料回答。"
                        "请用中文回答，尽量结构化、具体、完整，但避免冗长；优先按要点列出结论、依据和待确认事项。"
                        "每个关键结论后标注对应来源编号，例如“（来源1）”。"
                        "不要编造资料中没有的信息；资料不足时必须回答：未在项目资料中找到依据。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"问题：{question}\n\n"
                        "请基于以下项目资料详细回答。若资料中有多个相关片段，请综合归纳；"
                        "若只能找到部分依据，请说明“根据现有资料可确认”和“仍需补充确认”的内容。\n\n"
                        f"项目资料：\n{context}"
                    ),
                },
            ],
            max_tokens=900,
        )
    except Exception as exc:
        answer = f"已找到相关项目资料，但聊天模型暂不可用：{exc}"
    return {
        "answer": answer,
        "sources": [
            {
                "documentId": item["document_id"],
                "documentName": item["document_name"],
                "snippet": compact_text(item["text"], 260),
                "score": round(float(item.get("score") or 0), 4),
            }
            for item in sources
        ],
        "retrievalMode": "hybrid",
    }
