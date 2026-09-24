import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import database, main
from app.services import observations as inbox
from app.services import task_reconcile as reconcile
from app.services.data_quality import apply_quality_suggestion, _merge_records


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'test.db')
    database.init_db()
    conn = database.get_connection()
    conn.execute("UPDATE tasks SET is_archived=1")
    conn.commit()
    yield conn
    with inbox._worker_lock:
        pass
    conn.close()


def task(db, title, kind='observation', **fields):
    values = dict(title=title, task_kind=kind, created_at=database.now_iso(), updated_at=database.now_iso(), **fields)
    cursor = db.execute(f"INSERT INTO tasks({','.join(values)}) VALUES({','.join('?' for _ in values)})", list(values.values()))
    db.commit()
    return cursor.lastrowid


def document(db, end):
    doc_id = db.execute("""INSERT INTO documents(path,name,extension,doc_category,size_bytes,modified_at,status,
        last_indexed_at,knowledge_status,effective_date) VALUES(?,?,'xls','weekly_report',1,?,'extracted',?,'indexed',?)""",
        (end,end,end,end,end)).lastrowid
    db.commit()
    return doc_id


def request(group_id, action='promote', **values):
    group = inbox.preview([group_id])['items'][0]
    return dict(id=group_id,action=action,fingerprint=group['fingerprint'],**values)


def test_group_promotes_once_with_all_evidence_and_history(db):
    ids = [task(db,'数据迁移校验',source_document_id=document(db,end)) for end in ('2026-08-13','2026-08-20','2026-08-27')]
    listing = inbox.list_observations()
    assert listing['pendingGroups']==1 and listing['pendingRecords']==3
    req = request(ids[0],owner='实施单位',due_date='2026-09-30')
    first = inbox.resolve(req)
    again = inbox.resolve(req)
    assert first['targetId']==ids[0] and again['alreadyProcessed']
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE is_archived=0 AND task_kind='confirmed_addition'").fetchone()[0]==1
    assert db.execute('SELECT show_in_gantt FROM tasks WHERE id=?',(ids[0],)).fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM observation_resolutions').fetchone()[0]==3
    assert db.execute('SELECT COUNT(*) FROM entity_evidence').fetchone()[0]==3
    assert inbox.list_observations()['pendingRecords']==0
    assert len(inbox.list_observations('processed')['items'])==3
    assert all(json.loads(r[0])['task_kind']=='observation' for r in db.execute('SELECT original_json FROM observation_resolutions'))


def test_exact_duplicate_must_link_and_link_does_not_change_status(db):
    official=task(db,'第一批上线表单内部测试','baseline',status='in_progress',progress=40)
    obs=task(db,'第一批上线表单内部测试',status='completed',progress=100)
    with pytest.raises(ValueError,match='不能重复提升'):
        inbox.resolve(request(obs,keepIndependent=True))
    inbox.resolve(request(obs,'link',targetId=official))
    assert db.execute('SELECT progress FROM tasks WHERE id=?',(official,)).fetchone()[0]==40
    assert db.execute("SELECT COUNT(*) FROM data_quality_suggestions WHERE suggestion_kind='task_status_review' AND status='pending'").fetchone()[0]==1
    assert inbox.list_observations()['pendingRecords']==0


def test_near_duplicate_requires_individual_acknowledgement(db):
    task(db,'迁移一致性校验报告编制','baseline')
    obs=task(db,'迁移一致性校验报告编写')
    with pytest.raises(ValueError,match='逐项确认'):
        inbox.resolve(request(obs))
    assert inbox.resolve(request(obs,keepIndependent=True))['ok']


def test_different_batch_and_uat_are_never_auto_linked(db):
    official=dict(id=1,title='上线表单测试',description='第一批、第二批及 UAT 测试')
    obs=dict(id=2,title='上线表单测试',description='第一批内部测试已完成 73/73+')
    assert inbox.safe_match(obs,[official]) is None
    a=task(db,'上线表单测试',description='第一批内部测试')
    task(db,'上线表单测试',description='第二批内部测试')
    assert inbox.list_observations()['pendingGroups']==2


def test_stale_preview_and_invalid_dates_leave_records_unchanged(db):
    obs=task(db,'独立新增事项')
    req=request(obs)
    task(db,'独立新增事项')
    with pytest.raises(ValueError,match='重新预览'):
        inbox.resolve(req)
    with pytest.raises(ValueError,match='截止日期'):
        inbox.resolve(request(obs,due_date='2026-02-31'))
    assert db.execute('SELECT COUNT(*) FROM observation_resolutions').fetchone()[0]==0


def wait_batch(batch_id):
    for _ in range(100):
        result=inbox.batch_status(batch_id)
        if not result['running']: return result
        time.sleep(.02)
    pytest.fail('batch did not finish')


def test_batch_partial_success_idempotency_and_persistence(db):
    a=task(db,'新增甲事项'); b=task(db,'新增乙事项')
    items=[request(a),request(b,due_date='bad-date')]
    result=inbox.start_batch(items,'fixed-key')
    done=wait_batch(result['id'])
    assert done['succeeded']==1 and done['failed']==1
    assert inbox.start_batch(items,'fixed-key')['id']==result['id']
    assert inbox.batch_status(result['id'])['items'][1]['error']
    assert inbox.list_observations()['pendingRecords']==1
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE is_archived=0 AND task_kind='confirmed_addition'").fetchone()[0]==1
    inbox.resume_batches()
    assert wait_batch(result['id'])['succeeded']==1


def test_safe_batch_link_and_conflicting_batch_link(db):
    target=task(db,'数据迁移核验','baseline')
    obs=task(db,'数据迁移核验')
    target2=task(db,'上线表单测试','baseline',description='全部批次 UAT')
    obs2=task(db,'上线表单测试',description='第一批内部测试')
    result=inbox.start_batch([request(obs,'link',targetId=target),request(obs2,'link',targetId=target2)],'links')
    done=wait_batch(result['id'])
    assert done['succeeded']==1 and done['failed']==1


def test_transfer_retains_sources_and_only_creates_pending_suggestion(db):
    obs=task(db,'系统容量不足',source_document_id=document(db,'2026-08-27'))
    count=db.execute('SELECT COUNT(*) FROM risks').fetchone()[0]
    inbox.resolve(request(obs,'transfer',suggestionType='risk',reason='应归类为风险'))
    assert db.execute('SELECT COUNT(*) FROM risks').fetchone()[0]==count
    row=db.execute("SELECT * FROM update_suggestions WHERE title='系统容量不足'").fetchone()
    assert row['status']=='pending' and row['suggestion_type']=='risk'
    assert db.execute('SELECT COUNT(*) FROM suggestion_sources WHERE suggestion_id=?',(row['id'],)).fetchone()[0]==1


def test_ignore_and_quality_merge_close_inbox_without_deleting(db):
    target=task(db,'资料归档','baseline'); obs=task(db,'资料归档')
    _merge_records(db,'task',target,[obs],{})
    db.commit()
    assert inbox.list_observations()['pendingRecords']==0
    other=task(db,'页脚垃圾')
    inbox.resolve(request(other,'ignore',reason='页脚误识别'))
    assert db.execute('SELECT COUNT(*) FROM tasks WHERE id=?',(other,)).fetchone()[0]==1
    assert len(inbox.list_observations('processed')['items'])==2


def test_latest_evidence_updates_but_old_evidence_only_attaches(db):
    target=task(db,'第一批上线表单内部测试','baseline',description='第一批内部测试',status_as_of='2026-08-20',progress=50,status='in_progress')
    old=task(db,'第一批上线表单内部测试',description='第一批内部测试已完成 20/73',source_document_id=document(db,'2026-08-13'))
    latest=task(db,'第一批上线表单内部测试',description='第一批内部测试已完成 73/73+',source_document_id=document(db,'2026-08-27'))
    result=reconcile.reconcile_task_progress()
    row=db.execute('SELECT * FROM tasks WHERE id=?',(target,)).fetchone()
    assert row['status']=='completed' and row['progress']==100 and row['status_as_of']=='2026-08-27'
    assert db.execute('SELECT COUNT(*) FROM observation_resolutions WHERE task_id IN (?,?)',(old,latest)).fetchone()[0]==2
    assert inbox.list_observations()['pendingRecords']==0
    before=db.execute('SELECT COUNT(*) FROM entity_evidence').fetchone()[0]
    reconcile.reconcile_task_progress()
    assert db.execute('SELECT COUNT(*) FROM entity_evidence').fetchone()[0]==before


def test_ambiguous_official_match_not_automatic(db):
    task(db,'数据批量迁移','baseline'); task(db,'数据批量迁移','confirmed_addition')
    obs=task(db,'数据批量迁移',description='迁移完成',source_document_id=document(db,'2026-08-27'))
    reconcile.reconcile_task_progress()
    assert inbox.list_observations()['pendingRecords']==1


def test_public_read_admin_writes_and_dashboard_count(db):
    obs=task(db,'待提升工作')
    client=TestClient(main.app)  # No context manager: do not start real scanner/watchers.
    assert client.get('/api/task-observations').status_code==200
    req=request(obs)
    assert client.post('/api/task-observations/preview',json={'ids':[obs]}).status_code==401
    assert client.post('/api/task-observations/resolve-batch',json={'items':[req],'requestKey':'abc'}).status_code==401
    assert client.post(f'/api/tasks/{obs}/promote',json=req).status_code==401
    main.app.dependency_overrides[main.admin_from_request]=lambda: {'username':'test-admin','isAdmin':True}
    try:
        assert client.post(f'/api/task-observations/{obs}/resolve',json=req).status_code==200
        dashboard=client.get('/api/dashboard').json()
        assert dashboard['counts']['officialTasks']==1
        assert dashboard['counts']['observationTasks']==0
    finally:
        main.app.dependency_overrides.clear()


def test_migration_idempotent_and_preserves_task_values(db):
    obs=task(db,'保留历史值',status='in_progress',progress=25)
    inbox.ensure_observation_schema(db); db.commit()
    inbox.ensure_observation_schema(db); db.commit()
    row=db.execute('SELECT * FROM tasks WHERE id=?',(obs,)).fetchone()
    assert row['task_kind']=='observation' and row['progress']==25
    assert not db.execute('SELECT * FROM observation_resolutions WHERE task_id=?',(obs,)).fetchone()


def test_pending_batch_can_resume_after_restart(db):
    obs=task(db,'恢复后台任务')
    req=request(obs)
    db.execute("INSERT INTO observation_batches(id,request_key,request_json,actor,created_at) VALUES('resume','resume',?,'admin',?)",(json.dumps([req]),database.now_iso()))
    db.execute("INSERT INTO observation_batch_items(batch_id,ordinal,request_json) VALUES('resume',0,?)",(json.dumps(req),))
    db.commit()
    inbox.resume_batches()
    result=wait_batch('resume')
    assert result['succeeded']==1
    assert inbox.list_observations()['pendingRecords']==0


def test_field_review_updates_only_selected_fields_and_rejects_older_evidence(db):
    target=task(db,'字段审核任务','baseline',owner='原责任人',status_as_of='2026-08-20')
    obs=task(db,'字段审核任务',owner='新责任人',progress=70,status='in_progress',source_document_id=document(db,'2026-08-27'))
    inbox.resolve(request(obs,'link',targetId=target))
    review=db.execute("SELECT id FROM data_quality_suggestions WHERE suggestion_kind='task_status_review' ORDER BY id DESC").fetchone()[0]
    result=apply_quality_suggestion(review,{'fieldChoices':{'owner':'新责任人'}})
    assert result['ok'],result
    row=db.execute('SELECT * FROM tasks WHERE id=?',(target,)).fetchone()
    assert row['owner']=='新责任人' and row['progress']==0
    older=task(db,'字段审核任务',owner='旧资料责任人',source_document_id=document(db,'2026-08-13'))
    inbox.resolve(request(older,'link',targetId=target))
    review=db.execute("SELECT id FROM data_quality_suggestions WHERE suggestion_kind='task_status_review' ORDER BY id DESC").fetchone()[0]
    result=apply_quality_suggestion(review,{'fieldChoices':{'owner':'旧资料责任人'}})
    assert not result['ok']
    assert db.execute('SELECT owner FROM tasks WHERE id=?',(target,)).fetchone()[0]=='新责任人'


def test_completed_task_never_auto_regresses(db):
    target=task(db,'校验任务','baseline',status='completed',progress=100,status_as_of='2026-08-20')
    task(db,'校验任务',description='已完成 73/75',source_document_id=document(db,'2026-08-27'))
    reconcile.reconcile_task_progress()
    assert db.execute('SELECT progress FROM tasks WHERE id=?',(target,)).fetchone()[0]==100
    assert db.execute("SELECT COUNT(*) FROM data_quality_suggestions WHERE suggestion_kind='task_status_review' AND status='pending'").fetchone()[0]==1
