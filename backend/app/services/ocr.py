from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any

import fitz

from app.database import get_connection, now_iso, row_to_dict, rows_to_dicts
from app.services.ai import _post_json, _url, ai_config
from app.services.extractors import compact_text


OCR_MIN_TEXT_CHARS = 80
OCR_RENDER_ZOOM = 1.7


class OcrWorker:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.paused = False

    def start(self) -> None:
        with self.lock:
            self.paused = False
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def pause(self) -> None:
        with self.lock:
            self.paused = True

    def _run(self) -> None:
        while True:
            with self.lock:
                if self.paused:
                    return
            document = next_pending_document()
            if not document:
                return
            process_document_ocr(int(document["id"]))
            time.sleep(0.2)


worker = OcrWorker()


def should_ocr_pdf(path: Path, extracted_text: str) -> bool:
    return path.suffix.lower() == ".pdf" and len(compact_text(extracted_text, 1000)) < OCR_MIN_TEXT_CHARS


def pdf_page_count(path: Path) -> int:
    with fitz.open(str(path)) as doc:
        return len(doc)


def enqueue_document_ocr(document_id: int, path: Path, conn=None) -> None:
    owns_connection = conn is None
    conn = conn or get_connection()
    now = now_iso()
    try:
        total = pdf_page_count(path)
        conn.execute(
            """
            UPDATE documents
            SET ocr_status = 'pending', ocr_progress = 0, ocr_pages_total = ?,
                ocr_pages_done = 0, ocr_error = '', ocr_at = ?
            WHERE id = ?
            """,
            (total, now, document_id),
        )
        for page_number in range(1, total + 1):
            conn.execute(
                """
                INSERT INTO ocr_pages(document_id, page_number, text, status, error, updated_at)
                VALUES(?, ?, '', 'pending', '', ?)
                ON CONFLICT(document_id, page_number) DO UPDATE SET
                    status = CASE WHEN ocr_pages.status = 'completed' THEN ocr_pages.status ELSE 'pending' END,
                    error = '',
                    updated_at = excluded.updated_at
                """,
                (document_id, page_number, now),
            )
        if owns_connection:
            conn.commit()
    except Exception as exc:
        conn.execute(
            "UPDATE documents SET ocr_status = 'failed', ocr_error = ?, ocr_at = ? WHERE id = ?",
            (str(exc), now, document_id),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()
    worker.start()


def next_pending_document() -> dict[str, Any] | None:
    conn = get_connection()
    try:
        return row_to_dict(
            conn.execute(
                """
                SELECT * FROM documents
                WHERE ocr_status IN ('pending', 'partial')
                ORDER BY ocr_at, id
                LIMIT 1
                """
            ).fetchone()
        )
    finally:
        conn.close()


def render_pdf_page(path: Path, page_index: int) -> bytes:
    with fitz.open(str(path)) as doc:
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(OCR_RENDER_ZOOM, OCR_RENDER_ZOOM)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")


def ocr_image_bytes(image_bytes: bytes, page_number: int, document_name: str) -> str:
    config = ai_config()
    if not config.get("apiBaseUrl") or not config.get("modelName"):
        raise ValueError("OCR 需要可用的聊天/多模态模型配置")
    image_data = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": config["modelName"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请对这页项目资料图片做 OCR。只输出页面中的中文/英文/数字正文，"
                            "保留表格行列关系，可用“ | ”分隔单元格。不要解释，不要总结，不要添加不存在的内容。"
                            f"\n文件：{document_name}\n页码：{page_number}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1800,
    }
    data = _post_json(_url(config["apiBaseUrl"], "/chat/completions"), payload, config.get("apiKey", ""), int(config.get("timeoutSeconds") or 60))
    return compact_text(data["choices"][0]["message"]["content"], 12000)


def combined_ocr_text(document_id: int, conn=None) -> str:
    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        rows = rows_to_dicts(
            conn.execute(
                "SELECT page_number, text FROM ocr_pages WHERE document_id = ? AND status = 'completed' ORDER BY page_number",
                (document_id,),
            ).fetchall()
        )
        return compact_text("\n\n".join(f"第 {row['page_number']} 页\n{row['text']}" for row in rows if row.get("text")), 100000)
    finally:
        if owns_connection:
            conn.close()


def update_document_ocr_rollup(conn, document_id: int) -> str:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM ocr_pages
        WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    total = int(row["total"] or 0)
    done = int(row["done"] or 0)
    failed = int(row["failed"] or 0)
    progress = round(done * 100 / total) if total else 0
    if total and done == total:
        status = "completed"
    elif done and failed:
        status = "partial"
    elif failed and failed == total:
        status = "failed"
    elif done:
        status = "partial"
    else:
        status = "pending"
    conn.execute(
        """
        UPDATE documents
        SET ocr_status = ?, ocr_progress = ?, ocr_pages_total = ?,
            ocr_pages_done = ?, ocr_at = ?
        WHERE id = ?
        """,
        (status, progress, total, done, now_iso(), document_id),
    )
    return status


def process_document_ocr(document_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        document = row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
        if not document:
            return {"ok": False, "error": "文档不存在"}
        path = Path(document["path"])
        if not path.exists():
            conn.execute(
                "UPDATE documents SET ocr_status = 'failed', ocr_error = ?, ocr_at = ? WHERE id = ?",
                ("本地文件不存在", now_iso(), document_id),
            )
            conn.commit()
            return {"ok": False, "error": "本地文件不存在"}
        conn.execute("UPDATE documents SET ocr_status = 'processing', ocr_error = '', ocr_at = ? WHERE id = ?", (now_iso(), document_id))
        conn.commit()
    finally:
        conn.close()

    conn = get_connection()
    try:
        pages = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ocr_pages WHERE document_id = ? AND status != 'completed' ORDER BY page_number",
                (document_id,),
            ).fetchall()
        )
    finally:
        conn.close()

    for page in pages:
        page_number = int(page["page_number"])
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE ocr_pages SET status = 'processing', error = '', updated_at = ? WHERE document_id = ? AND page_number = ?",
                (now_iso(), document_id, page_number),
            )
            conn.commit()
        finally:
            conn.close()
        try:
            image_bytes = render_pdf_page(path, page_number - 1)
            text = ocr_image_bytes(image_bytes, page_number, document["name"])
            conn = get_connection()
            try:
                conn.execute(
                    """
                    UPDATE ocr_pages
                    SET text = ?, status = 'completed', error = '', updated_at = ?
                    WHERE document_id = ? AND page_number = ?
                    """,
                    (text, now_iso(), document_id, page_number),
                )
                update_document_ocr_rollup(conn, document_id)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            conn = get_connection()
            try:
                conn.execute(
                    """
                    UPDATE ocr_pages
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE document_id = ? AND page_number = ?
                    """,
                    (str(exc), now_iso(), document_id, page_number),
                )
                status = update_document_ocr_rollup(conn, document_id)
                conn.execute("UPDATE documents SET ocr_error = ? WHERE id = ?", (str(exc), document_id))
                conn.commit()
                if status == "failed":
                    return {"ok": False, "error": str(exc)}
            finally:
                conn.close()

    conn = get_connection()
    try:
        status = update_document_ocr_rollup(conn, document_id)
        text = combined_ocr_text(document_id, conn)
        if text:
            from app.services.ai import index_document_knowledge

            index_document_knowledge(document_id, text, document["name"], conn=conn)
            conn.execute(
                """
                UPDATE documents
                SET status = 'indexed', summary = ?, error = ''
                WHERE id = ?
                """,
                (compact_text(text, 500), document_id),
            )
        conn.commit()
        return {"ok": status in {"completed", "partial"}, "status": status, "textLength": len(text)}
    finally:
        conn.close()


def retry_document_ocr(document_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        document = row_to_dict(conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone())
        if not document:
            return {"ok": False, "error": "文档不存在"}
        conn.execute(
            """
            UPDATE ocr_pages
            SET status = 'pending', error = '', updated_at = ?
            WHERE document_id = ? AND status != 'completed'
            """,
            (now_iso(), document_id),
        )
        conn.execute("UPDATE documents SET ocr_status = 'pending', ocr_error = '', ocr_at = ? WHERE id = ?", (now_iso(), document_id))
        conn.commit()
    finally:
        conn.close()
    worker.start()
    return {"ok": True}


def ocr_status() -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN ocr_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN ocr_status = 'processing' THEN 1 ELSE 0 END) AS processing,
                SUM(CASE WHEN ocr_status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN ocr_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN ocr_status = 'partial' THEN 1 ELSE 0 END) AS partial
            FROM documents
            """
        ).fetchone()
        active = row_to_dict(conn.execute("SELECT id, name, ocr_progress FROM documents WHERE ocr_status = 'processing' LIMIT 1").fetchone())
        return {**(row_to_dict(row) or {}), "active": active}
    finally:
        conn.close()


def ocr_pages(document_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return rows_to_dicts(
            conn.execute(
                "SELECT * FROM ocr_pages WHERE document_id = ? ORDER BY page_number",
                (document_id,),
            ).fetchall()
        )
    finally:
        conn.close()
