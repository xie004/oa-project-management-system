import pytest
from fastapi import HTTPException
from app import database, main

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'board.db')
    database.init_db()
    conn = database.get_connection()
    conn.execute('UPDATE tasks SET is_archived=1')
    conn.commit()
    yield conn
    conn.close()

def add(db, kind='baseline', archived=0):
    cur=db.execute("INSERT INTO tasks(title,task_kind,is_archived,show_in_gantt,created_at,updated_at) VALUES('test',?,?,0,?,?)", (kind, archived, database.now_iso(), database.now_iso()))
    db.commit()
    return cur.lastrowid

def test_gantt_batch_reports_exact_success_and_skips_ineligible(db):
    official, observation, archived = add(db), add(db, 'observation'), add(db, archived=1)
    result=main.bulk_update_tasks(main.TaskBulkPatchPayload(ids=[official, official, observation, archived, 99999], values={'show_in_gantt': 1}))
    assert result['updated']==1
    assert result['updatedIds']==[official]
    assert {i['id'] for i in result['skipped']}=={observation, archived, 99999}
    assert db.execute('SELECT show_in_gantt FROM tasks WHERE id=?', (official,)).fetchone()[0]==1
    assert db.execute('SELECT show_in_gantt FROM tasks WHERE id=?', (observation,)).fetchone()[0]==0
    main.bulk_update_tasks(main.TaskBulkPatchPayload(ids=[official], values={'show_in_gantt': 0}))
    assert db.execute('SELECT show_in_gantt FROM tasks WHERE id=?', (official,)).fetchone()[0]==0

def test_gantt_batch_all_ineligible_preserves_data(db):
    observation=add(db, 'observation')
    result=main.bulk_update_tasks(main.TaskBulkPatchPayload(ids=[observation], values={'show_in_gantt': True}))
    assert result['updatedIds']==[] and len(result['skipped'])==1

def test_archive_is_readonly_even_via_direct_patch(db):
    archived=add(db, archived=1)
    with pytest.raises(HTTPException) as err:
        main.update_task(archived, main.GenericPatchPayload(values={'show_in_gantt': 1}))
    assert err.value.status_code==409
