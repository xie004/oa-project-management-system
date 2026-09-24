from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from app import database
from app.services import scanner, uploads
from app.services.wiki import wiki_pages_for_category


@pytest.fixture()
def isolated_upload_database(tmp_path):
    original_data_dir = database.DATA_DIR
    original_db_path = database.DB_PATH
    database.DATA_DIR = tmp_path / "data"
    database.DB_PATH = database.DATA_DIR / "test.db"
    database.init_db()
    uploads._upload_jobs.clear()
    monitor_dir = tmp_path / "weekly"
    conn = database.get_connection()
    try:
        database.set_setting(conn, "default_monitor_dir", str(tmp_path))
        database.set_setting(
            conn,
            "monitor_types",
            {
                "weekly_report": {
                    "label": "项目周报",
                    "directories": [str(monitor_dir)],
                    "patterns": ["周报"],
                    "enabled": True,
                }
            },
        )
        conn.commit()
    finally:
        conn.close()
    yield monitor_dir
    uploads._upload_jobs.clear()
    database.DB_PATH = original_db_path
    database.DATA_DIR = original_data_dir


def test_upload_is_saved_to_configured_monitor_directory_without_overwrite(isolated_upload_database):
    monitor_dir = isolated_upload_database
    with patch.object(uploads.threading, "Thread") as thread:
        first = uploads.save_upload(BytesIO(b"first"), "项目周报.txt", "weekly_report", 0)
        second = uploads.save_upload(BytesIO(b"second"), "项目周报.txt", "weekly_report", 0)

    first_path = Path(first["job"]["path"])
    second_path = Path(second["job"]["path"])
    assert first_path.parent == monitor_dir.resolve()
    assert first_path.read_bytes() == b"first"
    assert second_path.read_bytes() == b"second"
    assert first_path.name == "项目周报.txt"
    assert second_path.name == "项目周报 (1).txt"
    assert thread.call_count == 2
    assert thread.return_value.start.call_count == 2


def test_upload_rejects_unknown_directory_and_extension(isolated_upload_database):
    with pytest.raises(ValueError, match="监控目录"):
        uploads.save_upload(BytesIO(b"test"), "资料.txt", "meeting", 0)
    with pytest.raises(ValueError, match="暂不支持"):
        uploads.save_upload(BytesIO(b"test"), "程序.exe", "weekly_report", 0)


def test_wiki_refresh_pages_follow_document_category():
    assert wiki_pages_for_category("weekly_report") == ["current_progress", "risks_coordination"]
    assert wiki_pages_for_category("contract_tender") == [
        "overview",
        "goals_scope",
        "plan_milestones",
        "deliverables_acceptance",
    ]
    assert "deliverables_acceptance" in wiki_pages_for_category("other", include_deliverables=True)


def test_deliverable_upload_links_document_and_queues_related_wiki(isolated_upload_database):
    conn = database.get_connection()
    try:
        deliverable_id = conn.execute("SELECT id FROM deliverables ORDER BY id LIMIT 1").fetchone()["id"]
    finally:
        conn.close()

    with patch.object(uploads.threading, "Thread"):
        result = uploads.save_upload(
            BytesIO("测试报告正文".encode("utf-8")),
            "测试报告.txt",
            "weekly_report",
            0,
            deliverable_id,
        )

    def fake_index(path, category, force=False):
        conn = database.get_connection()
        try:
            scanner.upsert_document(conn, path, "weekly_report", "indexed", "测试报告正文", "")
            conn.commit()
        finally:
            conn.close()
        return {"status": "indexed", "category": category}

    with patch.object(uploads, "index_document", side_effect=fake_index), patch.object(
        uploads,
        "queue_wiki_refresh",
        return_value={"ok": True, "queued": True},
    ) as queue:
        uploads._process_upload(result["job"]["id"])

    conn = database.get_connection()
    try:
        linked = conn.execute("SELECT document_id FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
        document = conn.execute("SELECT id FROM documents WHERE name = '测试报告.txt'").fetchone()
    finally:
        conn.close()
    assert linked["document_id"] == document["id"]
    queue.assert_called_once_with("weekly_report", trigger=f"upload:{result['job']['id']}", include_deliverables=True)
    assert uploads.upload_status()["jobs"][0]["status"] == "completed"
