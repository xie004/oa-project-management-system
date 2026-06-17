from __future__ import annotations

import json
from typing import Any

from app.database import get_connection, now_iso, row_to_dict, rows_to_dicts
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
    return compact_text("\n".join(row["text"] for row in rows), 12000)


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
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


def rebuild_wiki_suggestions() -> dict[str, Any]:
    conn = get_connection()
    try:
        ensure_wiki_pages(conn)
        docs = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM documents
                WHERE status = 'indexed' OR ocr_status IN ('completed', 'partial')
                ORDER BY modified_at DESC, id DESC
                LIMIT 18
                """
            ).fetchall()
        )
        materials = []
        sources = []
        for doc in docs:
            text = _document_text(conn, doc)
            if not text:
                continue
            materials.append(f"文件ID {doc['id']}：{doc['name']}\n{text}")
            sources.append({"documentId": doc["id"], "documentName": doc["name"]})
        source_text = compact_text("\n\n".join(materials), 28000)
        conn.execute("DELETE FROM wiki_suggestions WHERE status = 'pending'")
        created = 0
    finally:
        conn.close()

    for page_key, title in WIKI_PAGES:
        prompt = f"""
请基于项目资料，为“{title}”生成项目 Wiki 页面更新建议。
只返回 JSON 对象，字段：
title: 页面标题
content: 正文，使用纯文本小标题和短段落，不要输出 Markdown 符号，不要使用 # 或 *
sources: 数组，每项包含 documentId、documentName、snippet
要求：
1. 只依据资料，不编造。
2. 内容面向项目经理和领导，具体、清晰、可用于项目管理。
3. 每个重要结论尽量在 sources 中给出来源。

项目资料：
{source_text}
"""
        try:
            answer = chat_completion(
                [
                    {"role": "system", "content": "你是项目知识库整理助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1400,
            )
            data = _parse_json_object(answer)
            content = compact_text(str(data.get("content") or ""), 12000)
            if not content:
                continue
            model_sources = data.get("sources") if isinstance(data.get("sources"), list) else sources[:6]
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO wiki_suggestions(page_key, title, content, source_json, status, created_at)
                    VALUES(?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        page_key,
                        str(data.get("title") or title),
                        content,
                        json.dumps(model_sources[:8], ensure_ascii=False),
                        now_iso(),
                    ),
                )
                conn.commit()
                created += 1
            finally:
                conn.close()
        except Exception as exc:
            conn = get_connection()
            try:
                conn.execute(
                    """
                    INSERT INTO wiki_suggestions(page_key, title, content, source_json, status, created_at)
                    VALUES(?, ?, ?, '[]', 'pending', ?)
                    """,
                    (page_key, f"{title}（生成失败）", f"生成失败：{exc}", now_iso()),
                )
                conn.commit()
                created += 1
            finally:
                conn.close()
    return {"ok": True, "created": created}


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


def retrieve_wiki(question: str) -> list[dict[str, Any]]:
    terms = [term for term in question.replace("？", " ").replace("?", " ").split() if len(term) >= 2]
    for word in ["项目", "建设", "目标", "范围", "计划", "里程碑", "风险", "会议", "变更", "交付", "验收", "资源", "部署", "进度"]:
        if word in question and word not in terms:
            terms.append(word)
    conn = get_connection()
    try:
        pages = rows_to_dicts(conn.execute("SELECT * FROM wiki_pages WHERE status = 'published' OR content != '' ORDER BY id").fetchall())
    finally:
        conn.close()
    hits = []
    for page in pages:
        text = f"{page['title']}\n{page.get('content') or ''}"
        score = sum(text.count(term) for term in terms) if terms else 0
        if score:
            hits.append({**page, "score": score})
    return sorted(hits, reverse=True, key=lambda item: item["score"])[:4]


def answer_from_wiki(question: str, fallback_sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    pages = retrieve_wiki(question)
    if not pages:
        return None
    context = "\n\n".join(f"Wiki来源{index + 1}：{page['title']}\n{compact_text(page.get('content') or '', 1800)}" for index, page in enumerate(pages))
    prompt = f"""
请只依据以下 Wiki 和项目资料来源回答问题，输出 JSON 对象：
answer_summary: 一段简明结论
key_points: 字符串数组，列出具体要点
evidence: 字符串数组，说明依据
unknowns: 字符串数组，说明资料不足或待确认事项
sources: 数组，每项包含 documentId、documentName、snippet；如果来源来自 Wiki 页面，可 documentId 为空
不要输出 Markdown，不要使用 # 或 *。

问题：{question}

Wiki资料：
{context}
"""
    try:
        answer = chat_completion(
            [
                {"role": "system", "content": "你是项目知识库问答助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
        )
        data = _parse_json_object(answer)
    except Exception as exc:
        data = {
            "answer_summary": f"已找到相关 Wiki 内容，但模型暂不可用：{exc}",
            "key_points": [],
            "evidence": [],
            "unknowns": [],
            "sources": [],
        }
    sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    normalized_sources = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        normalized_sources.append(
            {
                "documentId": source.get("documentId"),
                "documentName": source.get("documentName") or source.get("title") or "项目 Wiki",
                "snippet": source.get("snippet") or "",
            }
        )
    if not normalized_sources:
        normalized_sources = fallback_sources
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
