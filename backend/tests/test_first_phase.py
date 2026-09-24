import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fitz
from fastapi import HTTPException
from pydantic import ValidationError

from app import database, main
from app.services import ocr, scanner


class IsolatedDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = database.DATA_DIR
        self.original_db_path = database.DB_PATH
        database.DATA_DIR = Path(self.temp_dir.name)
        database.DB_PATH = database.DATA_DIR / "test.db"
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        database.DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def fetch_one(self, statement, parameters=()):
        conn = database.get_connection()
        try:
            return conn.execute(statement, parameters).fetchone()
        finally:
            conn.close()

    def test_init_db_preserves_manual_project_plan_edits(self):
        milestone_title = database.PROJECT_PLAN_MILESTONES[0][0]
        task_title = database.PROJECT_PLAN_TASKS[0][0]
        conn = database.get_connection()
        try:
            conn.execute(
                """
                UPDATE milestones
                SET description = ?, actual_date = ?, status = ?, color_status = ?
                WHERE title = ?
                """,
                ("人工维护说明", "2099-01-01", "delayed", "red", milestone_title),
            )
            conn.execute(
                """
                UPDATE tasks
                SET progress = ?, status = ?, show_in_gantt = ?
                WHERE title = ? AND source = '项目计划分解'
                """,
                (73, "in_progress", 0, task_title),
            )
            conn.execute(
                "UPDATE project_profile SET description = ?, target_date = ? WHERE id = 1",
                ("人工维护的项目简介", "2099-12-31"),
            )
            conn.commit()
        finally:
            conn.close()

        database.init_db()

        milestone = self.fetch_one("SELECT * FROM milestones WHERE title = ?", (milestone_title,))
        self.assertEqual(milestone["description"], "人工维护说明")
        self.assertEqual(milestone["actual_date"], "2099-01-01")
        self.assertEqual(milestone["status"], "delayed")
        self.assertEqual(milestone["color_status"], "red")

        task = self.fetch_one(
            "SELECT * FROM tasks WHERE title = ? AND source = '项目计划分解'", (task_title,)
        )
        self.assertEqual(task["progress"], 73)
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["show_in_gantt"], 0)

        profile = self.fetch_one("SELECT * FROM project_profile WHERE id = 1")
        self.assertEqual(profile["description"], "人工维护的项目简介")
        self.assertEqual(profile["target_date"], "2099-12-31")

    def test_sqlite_connections_use_wal_and_busy_timeout(self):
        conn = database.get_connection()
        try:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertGreaterEqual(busy_timeout, database.SQLITE_BUSY_TIMEOUT_MS)

    def test_locked_sqlite_requests_return_a_retryable_response(self):
        response = asyncio.run(
            main.sqlite_operational_error_handler(None, sqlite3.OperationalError("database is locked"))
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertIn("请稍后重试", response.body.decode("utf-8"))

    def test_legacy_schema_adds_columns_before_new_performance_indexes(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.executescript(
                """
                CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE project_profile (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, system_name TEXT NOT NULL,
                    phase TEXT NOT NULL, owner TEXT, description TEXT, start_date TEXT,
                    target_date TEXT, overall_progress INTEGER NOT NULL DEFAULT 0,
                    color_status TEXT NOT NULL DEFAULT 'green', updated_at TEXT NOT NULL
                );
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, extension TEXT NOT NULL, doc_category TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, modified_at TEXT NOT NULL, status TEXT NOT NULL,
                    summary TEXT, error TEXT, last_indexed_at TEXT NOT NULL
                );
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
                    owner TEXT, status TEXT NOT NULL DEFAULT 'not_started',
                    priority TEXT NOT NULL DEFAULT 'medium', color_status TEXT NOT NULL DEFAULT 'green',
                    start_date TEXT, due_date TEXT, progress INTEGER NOT NULL DEFAULT 0,
                    source TEXT, source_document_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
                    planned_date TEXT, actual_date TEXT, status TEXT NOT NULL DEFAULT 'planned',
                    color_status TEXT NOT NULL DEFAULT 'green', source_document_id INTEGER,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE risks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
                    level TEXT NOT NULL DEFAULT 'medium', status TEXT NOT NULL DEFAULT 'open',
                    mitigation TEXT, source_document_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
                    proposer TEXT, impact TEXT, status TEXT NOT NULL DEFAULT 'pending',
                    source_document_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE deliverables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, requirement_source TEXT,
                    description TEXT, status TEXT NOT NULL DEFAULT 'not_started', owner TEXT,
                    planned_date TEXT, submitted_date TEXT, document_id INTEGER, sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        original_db_path = database.DB_PATH
        database.DB_PATH = legacy_path
        try:
            database.init_db()
            conn = database.get_connection()
            try:
                document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
                task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
                index_names = {row["name"] for row in conn.execute("PRAGMA index_list(tasks)")}
            finally:
                conn.close()
        finally:
            database.DB_PATH = original_db_path

        self.assertIn("ocr_status", document_columns)
        self.assertIn("show_in_gantt", task_columns)
        self.assertIn("is_archived", task_columns)
        self.assertIn("idx_tasks_active_schedule", index_names)

    def test_task_payload_and_patch_reject_invalid_business_values(self):
        with self.assertRaises(ValidationError):
            main.TaskPayload(title="   ")
        with self.assertRaises(ValidationError):
            main.TaskPayload(title="有效任务", progress=101)
        with self.assertRaises(ValidationError):
            main.TaskPayload(title="有效任务", progress=True)
        with self.assertRaises(ValidationError):
            main.TaskPayload(title="有效任务", start_date="2026-10-02", due_date="2026-10-01")

        task_id = main.create_task(
            main.TaskPayload(
                title="隔离测试任务",
                status="in_progress",
                progress=20,
                start_date="2026-09-01",
                due_date="2026-09-02",
            )
        )["id"]

        for values in (
            {"status": "bad_status"},
            {"progress": -1},
            {"due_date": "2026-08-31"},
            {"unknown_field": "value"},
        ):
            with self.assertRaises(HTTPException) as raised:
                main.update_task(task_id, main.GenericPatchPayload(values=values))
            self.assertEqual(raised.exception.status_code, 422)

        with self.assertRaises(HTTPException) as raised:
            main.update_task(999999, main.GenericPatchPayload(values={"progress": 30}))
        self.assertEqual(raised.exception.status_code, 404)

        main.update_task(task_id, main.GenericPatchPayload(values={"progress": 30, "show_in_gantt": True}))
        task = self.fetch_one("SELECT progress, show_in_gantt FROM tasks WHERE id = ?", (task_id,))
        self.assertEqual(task["progress"], 30)
        self.assertEqual(task["show_in_gantt"], 1)

    def test_deliverable_patch_validates_record_and_related_document(self):
        deliverable = self.fetch_one("SELECT id FROM deliverables ORDER BY id LIMIT 1")
        deliverable_id = int(deliverable["id"])

        for values in ({"name": " "}, {"status": "unknown"}, {"document_id": 999999}, {"document_id": 1.5}):
            with self.assertRaises(HTTPException) as raised:
                main.update_deliverable(deliverable_id, main.GenericPatchPayload(values=values))
            self.assertEqual(raised.exception.status_code, 422)

        with self.assertRaises(HTTPException) as raised:
            main.update_deliverable(999999, main.GenericPatchPayload(values={"name": "不存在"}))
        self.assertEqual(raised.exception.status_code, 404)

    def test_ocr_page_limit_prevents_unbounded_queueing(self):
        pdf_path = Path(self.temp_dir.name) / "many-pages.pdf"
        pdf = fitz.open()
        for _ in range(ocr.MAX_OCR_PAGES + 1):
            pdf.new_page()
        pdf.save(pdf_path)
        pdf.close()

        reason = ocr.ocr_skip_reason(pdf_path, "")
        self.assertIsNotNone(reason)
        self.assertIn("超过 OCR 处理上限", reason)
        self.assertFalse(ocr.should_ocr_pdf(pdf_path, ""))

        result = scanner.index_document(pdf_path)
        self.assertEqual(result["status"], "indexed")
        document = self.fetch_one(
            "SELECT ocr_status, ocr_pages_total FROM documents WHERE path = ?", (str(pdf_path.resolve()),)
        )
        self.assertEqual(document["ocr_status"], "skipped")
        self.assertEqual(document["ocr_pages_total"], 0)

    def test_document_indexing_keeps_ai_disabled_and_commits_short_stages(self):
        text_path = Path(self.temp_dir.name) / "项目周报.txt"
        text_path.write_text(
            "本周完成测试环境部署、接口联调和迁移样例核验。\n"
            "下周继续推进历史数据迁移、问题闭环和用户测试准备工作。",
            encoding="utf-8",
        )

        result = scanner.index_document(text_path)

        self.assertEqual(result["status"], "indexed")
        document = self.fetch_one(
            "SELECT status, knowledge_status, analysis_status FROM documents WHERE path = ?",
            (str(text_path.resolve()),),
        )
        self.assertEqual(document["status"], "indexed")
        self.assertEqual(document["knowledge_status"], "indexed")
        # Rule-based recognition now runs even when the optional model is disabled.
        self.assertEqual(document["analysis_status"], "analyzed")

    def test_overlapping_scan_returns_a_clear_noop_result(self):
        acquired = scanner._scan_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            result = scanner.scan_all()
        finally:
            scanner._scan_lock.release()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
