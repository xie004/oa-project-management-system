import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.services import scanner
from app.services.task_matching import compatible_scope, safe_match, task_similarity
from app.services.task_recognition import (
    RULE_VERSION, _preview_worker, evidence_in_source, rule_candidates,
    semantic_batches, semantic_units,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "recognition.db")
    database.init_db()
    conn = database.get_connection()
    conn.execute("UPDATE tasks SET is_archived=1")
    conn.commit()
    yield conn
    conn.close()


def add_document(db, path, name="周报.xlsx", category="weekly_report"):
    return db.execute("""INSERT INTO documents(path,name,extension,doc_category,size_bytes,modified_at,status,last_indexed_at,knowledge_status)
        VALUES(?,?,'.xlsx',?,1,'2026-09-20','indexed','2026-09-20','indexed')""", (str(path), name, category)).lastrowid


def test_template_variants_and_more_than_twelve_items_are_not_truncated():
    body = "\n".join(f"[工作表 工作/行 {i+2}] 推进第{i+1}项数据迁移测试" for i in range(15))
    text = "[工作表 工作/行 1] 下一步计划\n" + body
    items, rejected = rule_candidates(text, "weekly_report")
    assert len(items) == 15
    assert not rejected
    assert all(item["task_mode"] == "planned" for item in items)


def test_progress_is_evidence_not_milestone_and_plans_are_distinct():
    text = """[工作表 周报/行 1] 当前进展
[工作表 周报/行 2] 第一批表单内部测试已完成73/73+
[工作表 周报/行 3] 下一步计划
[工作表 周报/行 4] 开展第二批表单内部测试"""
    items, _ = rule_candidates(text, "weekly_report")
    assert [item["task_mode"] for item in items] == ["progress", "planned"]
    assert items[0]["status"] == "completed" and items[0]["progress"] == 100
    assert all(item["type"] == "task" for item in items)


def test_headers_and_non_actionable_rows_are_filtered_with_reason():
    text = """[工作表 周报/行 1] 本周工作进展
[工作表 周报/行 2] 序号 | 工作内容 | 完成情况 | 责任人
[工作表 周报/行 3] 项目总体情况良好
[工作表 周报/行 4] 完成档案数据迁移验证"""
    items, rejected = rule_candidates(text, "weekly_report")
    assert len(items) == 1 and "档案数据迁移" in items[0]["title"]
    assert any(item["reasonCode"] == "not_actionable" for item in rejected)


def test_evidence_must_exist_verbatim_and_batches_cover_all_text():
    source = "[段落 1] 完成历史数据迁移校验并形成报告"
    assert evidence_in_source("完成历史数据迁移校验", source)
    assert not evidence_in_source("历史数据已百分之百迁移成功", source)
    long_text = "\n".join(f"[段落 {i}] " + "测试内容" * 100 for i in range(40))
    batches = semantic_batches(long_text, 1500)
    assert len(batches) > 12
    assert "段落 39" in batches[-1]


def test_matching_keeps_batch_and_environment_boundaries():
    first = {"id": 1, "title": "第一批表单内部测试", "description": ""}
    second = {"id": 2, "title": "第二批表单内部测试", "description": ""}
    production = {"id": 3, "title": "生产环境部署", "description": ""}
    test_env = {"id": 4, "title": "测试环境部署", "description": ""}
    assert not compatible_scope(first, second)
    assert not compatible_scope(production, test_env)
    assert safe_match(first, [second]) is None
    assert task_similarity(first, second) < .90


def test_rule_only_scan_persists_verified_candidates_and_metadata(db, tmp_path, monkeypatch):
    path = tmp_path / "weekly.txt"
    text = "[段落 1] 三、下周工作计划\n[段落 2] 编制数据迁移一致性校验报告"
    path.write_text(text, encoding="utf-8")
    doc_id = add_document(db, path)
    milestone_count = db.execute("SELECT COUNT(*) FROM milestones").fetchone()[0]
    db.commit()
    monkeypatch.setattr(scanner, "ai_config", lambda: {"enabled": False, "maxTextLength": 12000})
    result = scanner.save_ai_analysis_suggestions(doc_id, text, path.name, "weekly_report")
    assert result["items"] == 1 and result["coverage"] == 1
    row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row["recognition_rule_version"] == RULE_VERSION
    assert row["recognition_candidate_count"] == 1
    assert db.execute("SELECT COUNT(*) FROM update_suggestions WHERE suggestion_type='task'").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM milestones").fetchone()[0] == milestone_count


def test_unmatched_progress_is_recorded_as_rejection_not_new_task(db, tmp_path, monkeypatch):
    path = tmp_path / "weekly-progress.txt"
    text = "[段落 1] 二、本周工作进展\n[段落 2] 完成完全未知专项的内部测试73/73"
    path.write_text(text, encoding="utf-8")
    doc_id = add_document(db, path, "进度周报.txt")
    db.commit()
    monkeypatch.setattr(scanner, "ai_config", lambda: {"enabled": False, "maxTextLength": 12000})
    result = scanner.save_ai_analysis_suggestions(doc_id, text, path.name, "weekly_report")
    assert result["items"] == 0 and result["rejected"] >= 1
    assert db.execute("SELECT COUNT(*) FROM update_suggestions WHERE suggestion_type='task'").fetchone()[0] == 0
    assert db.execute("SELECT reason_code FROM recognition_rejections WHERE document_id=?", (doc_id,)).fetchone()[0] == "unmatched_progress"


def test_preview_worker_never_changes_formal_state(db, tmp_path, monkeypatch):
    import app.services.task_recognition as recognition
    official = db.execute("""INSERT INTO tasks(title,status,progress,task_kind,is_archived,created_at,updated_at)
        VALUES('数据迁移校验','in_progress',40,'baseline',0,?,?)""", (database.now_iso(), database.now_iso())).lastrowid
    path = tmp_path / "preview.txt"; path.write_text("[段落 1] 本周进展\n[段落 2] 数据迁移校验已完成100%", encoding="utf-8")
    doc_id = add_document(db, path); db.execute("INSERT INTO weekly_reports(document_id,period_end,created_at,updated_at) VALUES(?,'2026-09-20',?,?)", (doc_id,database.now_iso(),database.now_iso()))
    run_id = "test-run"; db.execute("INSERT INTO recognition_preview_runs(id,status,trigger,started_at) VALUES(?,'queued','test',?)", (run_id,database.now_iso())); db.commit()
    monkeypatch.setattr(recognition, "analyze_task_candidates", lambda *a, **k: {"candidates":[{"type":"task","title":"数据迁移校验","description":"数据迁移校验已完成100%","evidence":"数据迁移校验已完成100%","locator":"段落2","task_mode":"progress","confidence":.98}],"rejected":[],"batch":{"failedBatches":[]},"coverage":1,"ruleVersion":RULE_VERSION})
    before = tuple(db.execute("SELECT status,progress,updated_at FROM tasks WHERE id=?", (official,)).fetchone())
    _preview_worker(run_id)
    after = tuple(db.execute("SELECT status,progress,updated_at FROM tasks WHERE id=?", (official,)).fetchone())
    run = db.execute("SELECT * FROM recognition_preview_runs WHERE id=?", (run_id,)).fetchone()
    assert before == after
    assert run["status"] == "completed" and run["before_hash"] == run["after_hash"]
    assert db.execute("SELECT result_type FROM recognition_preview_results WHERE run_id=?", (run_id,)).fetchone()[0] == "progress_change"


def test_document_listing_exposes_recognition_quality_details(db, tmp_path):
    path = tmp_path / "quality.txt"; path.write_text("标题", encoding="utf-8")
    doc_id = add_document(db, path, "质量周报.txt")
    db.execute("""UPDATE documents SET recognition_rule_version=?,recognition_coverage=.75,
        recognition_candidate_count=3,recognition_rejected_count=2,recognition_failed_batches=1,
        recognition_report_json=? WHERE id=?""", (RULE_VERSION, json.dumps({"failedBatches":[{"batchIndex":2,"error":"模型返回为空"}]}, ensure_ascii=False), doc_id))
    db.execute("""INSERT INTO recognition_rejections(document_id,candidate_type,title,evidence_text,locator,reason_code,reason,source_method,created_at)
        VALUES(?,'task',?,?,?,?,?,?,?)""", (doc_id,"页脚","第 5 页 共 5 页","第5页","page_footer","页码或页脚","rule",database.now_iso()))
    db.commit()
    item = next(row for row in scanner.list_documents() if row["id"] == doc_id)
    assert item["recognition_report"]["failedBatches"][0]["error"] == "模型返回为空"
    assert item["recognition_rejection_examples"][0]["reason"] == "页码或页脚"


def test_recognition_preview_api_is_admin_only_and_returns_persistent_results(db, monkeypatch):
    client = TestClient(main.app)
    assert client.get("/api/tasks/recognition-preview-status").status_code == 401
    assert client.post("/api/tasks/recognition-preview").status_code == 401
    main.app.dependency_overrides[main.admin_from_request] = lambda: {"username":"test-admin", "isAdmin":True}
    monkeypatch.setattr(main, "start_preview", lambda trigger: {"id":"preview-test", "status":"running", "running":True, "trigger":trigger})
    try:
        started = client.post("/api/tasks/recognition-preview").json()
        assert started["id"] == "preview-test" and started["running"]
        assert client.get("/api/tasks/recognition-preview-status").status_code == 200
        results = client.get("/api/tasks/recognition-preview-results?result_type=all").json()
        assert results["items"] == [] and results["total"] == 0
    finally:
        main.app.dependency_overrides.clear()
