"""Observation inbox: evidence first, explicit promotion, durable batch jobs.

Business updates and each batch result commit in the same SQLite transaction.
This makes retry/restart safe without introducing an external task queue.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from app.database import get_connection, now_iso, rows_to_dicts
from app.services.data_quality import attach_evidence, ensure_entity, normalize_title

_worker_lock = threading.Lock()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_observation_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS observation_resolutions (
            task_id INTEGER PRIMARY KEY REFERENCES tasks(id),
            state TEXT NOT NULL, target_id INTEGER, suggestion_id INTEGER,
            actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
            original_json TEXT NOT NULL, batch_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observation_batches (
            id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL, actor TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS observation_batch_items (
            batch_id TEXT NOT NULL REFERENCES observation_batches(id),
            ordinal INTEGER NOT NULL, request_json TEXT NOT NULL,
            result_json TEXT, PRIMARY KEY(batch_id, ordinal)
        );
    """)
    # Only a recorded historical merge/archive establishes a resolved identity.
    for row in conn.execute("SELECT * FROM tasks WHERE task_kind='observation' AND is_archived=1"):
        record_resolution(conn, dict(row), 'linked' if row['merged_into_id'] else 'ignored',
                          row['merged_into_id'], actor='migration', reason=row['archive_reason'] or '历史归档')


def record_resolution(conn, task, state, target_id=None, *, actor='admin', reason='', batch_id=None, suggestion_id=None):
    conn.execute("""INSERT OR IGNORE INTO observation_resolutions
        (task_id,state,target_id,suggestion_id,actor,reason,original_json,batch_id,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""", (task['id'], state, target_id, suggestion_id, actor, reason,
                                       _json(task), batch_id, now_iso()))


def scope_signature(task):
    text = f"{task.get('title') or ''} {task.get('description') or ''}".lower()
    # A missing qualifier is not equivalent to a specific batch or testing phase.
    batches = sorted(set(re.findall(r'第[一二三四五六七八九十\d]+[批期阶段]+', text)))
    phases = sorted(word for word in ('内部测试', '联调', 'uat', '回归', '试运行', '验收测试') if word in text)
    return batches, phases


def compatible_scope(left, right):
    return scope_signature(left) == scope_signature(right)


def similarity(left, right):
    a, b = normalize_title(left), normalize_title(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def candidates(task, officials):
    result = []
    for target in officials:
        score = similarity(task['title'], target['title'])
        if score < .65:
            continue
        scope_ok = compatible_scope(task, target)
        exact = normalize_title(task['title']) == normalize_title(target['title']) and scope_ok
        result.append({**target, 'similarity': round(score, 4), 'exact': exact, 'scopeCompatible': scope_ok,
                       'reason': '标准标题及工作范围相同' if exact else ('标题近似，需核对工作范围' if scope_ok else '批次或测试阶段不一致，需逐项确认')})
    return sorted(result, key=lambda x: (-x['similarity'], x['id']))[:5]


def safe_match(task, officials):
    matches = candidates(task, officials)
    # Require one strong candidate, no competing plausible identity, and equal scope.
    if not matches or matches[0]['similarity'] < .9 or not matches[0]['scopeCompatible']:
        return None
    if len(matches) > 1 and matches[1]['similarity'] >= .8:
        return None
    return matches[0]


def _tasks(conn):
    tasks = rows_to_dicts(conn.execute("""SELECT t.*, d.name document_name,
        COALESCE((SELECT MAX(w.period_end) FROM weekly_reports w WHERE w.document_id=d.id),
                 NULLIF(substr(d.effective_date,1,10),''),substr(d.modified_at,1,10),'') source_date
        FROM tasks t LEFT JOIN documents d ON d.id=t.source_document_id ORDER BY t.id""").fetchall())
    evidence = {}
    for row in conn.execute("""SELECT e.*,p.record_id,d.name document_name,
        COALESCE((SELECT MAX(w.period_end) FROM weekly_reports w WHERE w.document_id=d.id),
                 NULLIF(substr(d.effective_date,1,10),''),substr(d.modified_at,1,10),'') source_date
        FROM entity_evidence e JOIN project_entities p ON p.id=e.entity_id
        LEFT JOIN documents d ON d.id=e.document_id WHERE p.entity_type='task'
        ORDER BY source_date,e.id"""):
        evidence.setdefault(row['record_id'], []).append(dict(row))
    for task in tasks:
        task['evidence'] = evidence.get(task['id'], [])
    return tasks


def groups(conn):
    tasks = _tasks(conn)
    officials = [t for t in tasks if not t['is_archived'] and t['task_kind'] in ('baseline','confirmed_addition')]
    resolved = {r['task_id']: dict(r) for r in conn.execute('SELECT * FROM observation_resolutions')}
    grouped = {}
    for task in tasks:
        if task['task_kind'] != 'observation' or task['is_archived'] or task['id'] in resolved:
            continue
        key = _json([normalize_title(task['title']) or str(task['id']), scope_signature(task)])
        grouped.setdefault(key, []).append(task)
    result = []
    for members in grouped.values():
        members.sort(key=lambda t: (t['source_date'], t['id']), reverse=True)
        first = members[0]
        matches = candidates(first, officials)
        differences = []
        for field in ('status','progress','owner','due_date'):
            observed = list(dict.fromkeys(str(t.get(field) or '') for t in members))
            current = matches[0].get(field) if matches else None
            if len(observed) > 1 or (matches and str(current or '') not in observed):
                differences.append({'field': field, 'current': current, 'observed': observed})
        conflicts = any(d['field'] in ('owner','due_date') and len([v for v in d['observed'] if v]) > 1 for d in differences)
        conflicts |= bool(matches and (not matches[0]['scopeCompatible'] or
                           (len(matches)>1 and matches[1]['similarity']>=.8)))
        conflicts |= bool(matches and any(d['field'] in ('owner','due_date') and d['current'] and
                            any(v and v != str(d['current']) for v in d['observed']) for d in differences))
        category = 'conflict' if conflicts else 'link' if matches else 'new'
        fingerprint = hashlib.sha256(_json(members).encode()).hexdigest()
        result.append({'id': min(t['id'] for t in members), 'title': first['title'], 'members': members,
                       'sourceCount': len({doc for t in members for doc in [t.get('source_document_id'), *[e.get('document_id') for e in t.get('evidence',[])]] if doc}),
                       'sourceDate': first['source_date'], 'candidates': matches, 'differences': differences,
                       'category': category, 'fingerprint': fingerprint, 'owner': first.get('owner') or '',
                       'due_date': first.get('due_date') or ''})
    for item in resolved.values():
        task = json.loads(item['original_json'])
        result.append({'id': task['id'], 'title': task['title'], 'members': [task], 'sourceCount': len({d for d in [task.get('source_document_id'), *[e.get('document_id') for e in task.get('evidence',[])]] if d}),
                       'sourceDate': task.get('source_date') or '', 'candidates': [], 'differences': [],
                       'category': 'processed', 'resolution': item | {'original_json': None}})
    return sorted(result, key=lambda g: (g['sourceDate'], g['id']), reverse=True)


def list_observations(category='all', page=1, page_size=15):
    conn = get_connection()
    try:
        all_groups = groups(conn)
        counts = {key: sum(g['category']==key for g in all_groups) for key in ('link','new','conflict','processed')}
        pending = [g for g in all_groups if g['category']!='processed']
        selected = pending if category=='all' else [g for g in all_groups if g['category']==category]
        return {'items': selected[(page-1)*page_size:page*page_size], 'total':len(selected), 'counts':counts,
                'pendingGroups':len(pending), 'pendingRecords':sum(len(g['members']) for g in pending),
                'page':page, 'pageSize':page_size}
    finally:
        conn.close()


def preview(ids):
    conn = get_connection()
    try:
        by_id = {g['id']:g for g in groups(conn) if g['category']!='processed'}
        if not ids or len(ids)>100:
            raise ValueError('请选择 1 至 100 组识别事项')
        if any(i not in by_id for i in ids):
            raise ValueError('部分事项已处理或分组发生变化，请刷新后重选')
        return {'items':[by_id[i] for i in dict.fromkeys(ids)]}
    finally:
        conn.close()


def link_evidence(conn, observations, target, *, actor='admin', batch_id=None):
    entity_id = ensure_entity(conn, 'task', target['id'], target['title'])
    from app.services.data_quality import _copy_entity_evidence
    for task in observations:
        existing = conn.execute("SELECT id FROM project_entities WHERE entity_type='task' AND record_id=?", (task['id'],)).fetchone()
        if existing and existing['id'] != entity_id:
            _copy_entity_evidence(conn, existing['id'], entity_id)
        attach_evidence(conn, entity_id, task.get('source_document_id'),
                        f"{task['title']}\n{task.get('description') or ''}", locator=f"识别事项 #{task['id']}",
                        observed={k:task.get(k) for k in ('status','progress','owner','due_date','source_date')},
                        confidence=similarity(task['title'], target['title']), method='observation_link')
        record_resolution(conn, task, 'linked', target['id'], actor=actor, batch_id=batch_id, reason='追加来源；正式字段另行审核')
    _settle_reviews(conn,[t['id'] for t in observations])


def _settle_reviews(conn, member_ids):
    # Only identity/duplicate reviews wholly covered by this action are superseded.
    for row in conn.execute("SELECT * FROM data_quality_suggestions WHERE entity_type='task' AND status='pending'"):
        if row['suggestion_kind'] not in ('task_match_review','exact_duplicate','near_duplicate','invalid_record'):
            continue
        ids = set(json.loads(row['related_record_ids_json'] or '[]'))
        if row['suggestion_kind'] != 'task_match_review':
            ids.add(row['primary_record_id'])
        covered = set(member_ids)
        if row['suggestion_kind'] in ('exact_duplicate','near_duplicate'):
            for item_id in ids:
                task = conn.execute('SELECT task_kind FROM tasks WHERE id=?',(item_id,)).fetchone()
                if task and task['task_kind'] in ('baseline','confirmed_addition'):
                    covered.add(item_id)
        if ids and ids <= covered:
            conn.execute("UPDATE data_quality_suggestions SET status='dismissed',applied_at=? WHERE id=?", (now_iso(),row['id']))


def _resolve(conn, request, actor, batch_id=None):
    group_id = int(request.get('id') or 0)
    resolution = conn.execute('SELECT * FROM observation_resolutions WHERE task_id=?',(group_id,)).fetchone()
    if resolution:
        return {'ok':True,'id':group_id,'alreadyProcessed':True,'state':resolution['state'],'targetId':resolution['target_id']}
    group = next((g for g in groups(conn) if g['id']==group_id and g['category']!='processed'),None)
    if not group:
        raise ValueError('事项已归档或不存在，请刷新列表')
    if request.get('fingerprint') != group['fingerprint']:
        raise ValueError('资料或分组已变化，请重新预览后提交')
    members = group['members']
    first = members[0]
    action = request.get('action')
    target_id = None
    if action == 'link':
        target_id = int(request.get('targetId') or 0)
        target = conn.execute("SELECT * FROM tasks WHERE id=? AND is_archived=0 AND task_kind IN ('baseline','confirmed_addition')",(target_id,)).fetchone()
        if not target:
            raise ValueError('请选择有效的正式任务')
        if batch_id and (group['category']=='conflict' or not safe_match(first, [dict(t) for t in conn.execute("SELECT * FROM tasks WHERE is_archived=0 AND task_kind IN ('baseline','confirmed_addition')")]) or group['candidates'][0]['id']!=target_id):
            raise ValueError('批量关联仅适用于目标唯一、无冲突的高置信匹配，请单独确认')
        link_evidence(conn, members, dict(target), actor=actor, batch_id=batch_id)
        field_conflicts = {}
        for field in ('status','progress','owner','due_date'):
            value = first.get(field)
            if value not in (None,'') and str(value)!=str(target[field]):
                field_conflicts[field] = [str(target[field] or ''), str(value)]
        if field_conflicts:
            from app.services.task_reconcile import _create_review
            _create_review(conn,'task_status_review',target_id,[t['id'] for t in members],
                f"确认字段变化：{target['title']}", '已关联来源，正式字段未改动；请选择需要采用的字段。',
                {'fieldsOnly':True,'fieldConflicts':field_conflicts,'sourceDate':first.get('source_date'),
                 'documentId':first.get('source_document_id')}, 0.9)

    elif action == 'promote':
        title = str(request.get('title',first['title'])).strip()
        owner = str(request.get('owner',first.get('owner') or '')).strip()
        due = str(request.get('due_date',first.get('due_date') or '')).strip()
        if not title or len(title)>300 or len(owner)>200:
            raise ValueError('标题必填且不超过300字；责任人不超过200字')
        if due:
            try:
                if date.fromisoformat(due).isoformat()!=due: raise ValueError()
            except ValueError:
                raise ValueError('截止日期须为 YYYY-MM-DD')
        officials = [dict(t) for t in conn.execute("SELECT * FROM tasks WHERE is_archived=0 AND task_kind IN ('baseline','confirmed_addition')")]
        matches = candidates({**first,'title':title}, officials)
        if any(m['exact'] for m in matches):
            raise ValueError('已有同名同范围正式任务，请关联已有任务，不能重复提升')
        if (matches or group['category']=='conflict') and request.get('keepIndependent') is not True:
            raise ValueError('存在近似任务或字段冲突，请逐项确认保持独立')
        target_id = group_id
        primary = next(t for t in members if t['id']==target_id)
        link_evidence(conn, members, {**primary,'title':title}, actor=actor,batch_id=batch_id)
        from app.services.task_reconcile import normalize_task_status
        from app.services.progress import parse_progress_percent
        progress = parse_progress_percent(first.get('progress'))
        status = normalize_task_status(first.get('status'))
        if progress == 100 or status == 'completed':
            status, progress = 'completed', 100
        conn.execute("""UPDATE tasks SET title=?,owner=?,due_date=?,task_kind='confirmed_addition',
            description=?,status=?,progress=?,status_as_of=?,status_source_document_id=?,source_document_id=?,
            show_in_gantt=0,status_update_mode='manual_promote',updated_at=? WHERE id=?""",
                     (title,owner,due,first.get('description'),status,progress,first.get('source_date') or None,
                      first.get('source_document_id'),first.get('source_document_id'),now_iso(),target_id))
        for task in members:
            conn.execute("UPDATE observation_resolutions SET state='promoted',reason='管理员确认新增正式任务' WHERE task_id=?",(task['id'],))
    elif action in ('ignore','transfer'):
        reason = str(request.get('reason') or '').strip()
        if not reason:
            raise ValueError('请填写处理原因')
        suggestion_id = None
        if action == 'transfer':
            kind = request.get('suggestionType')
            if kind not in ('risk','change_request','deliverable'):
                raise ValueError('仅可转为风险、变更或交付物建议')
            payload = {'title':first['title'],'description':first.get('description') or '',
                       'source_document_id':first.get('source_document_id'),'owner':first.get('owner') or '',
                       'source':'识别事项转交','status':'not_started' if kind=='deliverable' else 'open' if kind=='risk' else 'pending',
                       'observationIds':[m['id'] for m in members]}
            suggestion_id = conn.execute("""INSERT INTO update_suggestions
                (document_id,suggestion_type,title,description,payload_json,status,created_at,evidence_text)
                VALUES(?,?,?,?,?,'pending',?,?)""",(first.get('source_document_id'),kind,first['title'],first.get('description'),
                         _json(payload),now_iso(),first.get('description'))).lastrowid
            for task in members:
                if task.get('source_document_id'):
                    conn.execute('INSERT OR IGNORE INTO suggestion_sources(suggestion_id,document_id,evidence_hash,evidence_text,locator,created_at) VALUES(?,?,?,?,?,?)',
                                 (suggestion_id,task['source_document_id'],str(task['id']),task.get('description') or task['title'],f"识别事项 #{task['id']}",now_iso()))
        for task in members:
            record_resolution(conn,task,'ignored' if action=='ignore' else 'transferred',actor=actor,reason=reason,
                              batch_id=batch_id,suggestion_id=suggestion_id)
            if action=='ignore':
                conn.execute("UPDATE tasks SET is_archived=1,archive_reason=?,updated_at=? WHERE id=?",(reason,now_iso(),task['id']))
                conn.execute("UPDATE project_entities SET status='archived',updated_at=? WHERE entity_type='task' AND record_id=?",(now_iso(),task['id']))
    else:
        raise ValueError('不支持的处理操作')
    _settle_reviews(conn,[m['id'] for m in members])
    return {'ok':True,'id':group_id,'action':action,'targetId':target_id,'records':len(members)}


def resolve(request, actor='admin'):
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        result = _resolve(conn,request,actor)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def batch_status(batch_id=None):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM observation_batches WHERE id=?',(batch_id,)).fetchone() if batch_id else conn.execute(
            "SELECT * FROM observation_batches ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created_at DESC LIMIT 1").fetchone()
        if not row: return {'running':False,'items':[]}
        items = [json.loads(r['result_json']) if r['result_json'] else {'pending':True} for r in conn.execute(
            'SELECT result_json FROM observation_batch_items WHERE batch_id=? ORDER BY ordinal',(row['id'],))]
        done = [i for i in items if not i.get('pending')]
        return {'id':row['id'],'running':row['status'] in ('queued','running'),'status':row['status'],
                'total':len(items),'progress':len(done),'succeeded':sum(bool(i.get('ok')) for i in done),
                'failed':sum(not i.get('ok') for i in done),'items':items,'error':row['error'],
                'createdAt':row['created_at'],'finishedAt':row['finished_at']}
    finally:
        conn.close()


def start_batch(items, request_key, actor='admin'):
    if not items or len(items)>100 or not request_key or len(request_key)>100:
        raise ValueError('请选择1至100组事项，并提供有效请求标识')
    conn=get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT id,request_json FROM observation_batches WHERE request_key=?',(request_key,)).fetchone()
        if existing:
            if existing['request_json']!=_json(items): raise ValueError('请求标识已使用，请重新提交')
            batch_id=existing['id']
        else:
            batch_id=uuid.uuid4().hex
            conn.execute('INSERT INTO observation_batches(id,request_key,request_json,actor,created_at) VALUES(?,?,?,?,?)',
                         (batch_id,request_key,_json(items),actor,now_iso()))
            conn.executemany('INSERT INTO observation_batch_items(batch_id,ordinal,request_json) VALUES(?,?,?)',
                             [(batch_id,i,_json(item)) for i,item in enumerate(items)])
        conn.commit()
    finally:
        conn.close()
    resume_batches()
    return batch_status(batch_id)


def _drain():
    # Blocking lock also prevents a just-enqueued batch from missing a worker wakeup.
    with _worker_lock:
        while True:
            conn=get_connection()
            try:
                conn.execute('BEGIN IMMEDIATE')
                row=conn.execute("""SELECT i.*,b.actor FROM observation_batch_items i
                    JOIN observation_batches b ON b.id=i.batch_id
                    WHERE i.result_json IS NULL AND b.status IN ('queued','running')
                    ORDER BY b.created_at,i.ordinal LIMIT 1""").fetchone()
                if not row:
                    conn.rollback()
                    break
                conn.execute("UPDATE observation_batches SET status='running' WHERE id=?",(row['batch_id'],))
                conn.execute('SAVEPOINT item')
                try:
                    result=_resolve(conn,json.loads(row['request_json']),row['actor'],row['batch_id'])
                except Exception as exc:
                    conn.execute('ROLLBACK TO item')
                    request=json.loads(row['request_json'])
                    result={'ok':False,'id':request.get('id'),'error':str(exc)}
                conn.execute('RELEASE item')
                conn.execute('UPDATE observation_batch_items SET result_json=? WHERE batch_id=? AND ordinal=?',
                             (_json(result),row['batch_id'],row['ordinal']))
                remaining=conn.execute('SELECT COUNT(*) FROM observation_batch_items WHERE batch_id=? AND result_json IS NULL',(row['batch_id'],)).fetchone()[0]
                if not remaining:
                    conn.execute("UPDATE observation_batches SET status='completed',finished_at=? WHERE id=?",(now_iso(),row['batch_id']))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if 'row' in locals() and row:
                    conn.execute("UPDATE observation_batches SET status='failed',error=?,finished_at=? WHERE id=?",(str(exc),now_iso(),row['batch_id']))
                    conn.commit()
                break
            finally:
                conn.close()


def resume_batches():
    threading.Thread(target=_drain,daemon=True,name='observation-batches').start()
