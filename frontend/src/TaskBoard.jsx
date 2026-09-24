import React, { useEffect, useMemo, useRef, useState } from "react";
import ObservationInbox from "./ObservationInbox.jsx";
import { boardStatus, filterTasks, isOfficial, pendingGanttIds, selectionAfterBatch, taskStatuses, togglePageSelection } from "./taskBoardModel.js";

const date = value => value ? String(value).slice(0, 10) : "待补充";
const kind = task => task.task_kind === "baseline" ? "项目计划" : task.task_kind === "confirmed_addition" ? "确认新增" : "识别事项";
const emptyDraft = { title: "", owner: "", due_date: "", priority: "medium" };
function SelectBox({ mixed, ...props }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = !!mixed; }, [mixed]);
  return <input type="checkbox" ref={ref} {...props} />;
}

function TaskRow({ task, selected, onSelect, onPatch, onPreview, onEvidence, busy, batchBusy, compact }) {
  const [edit, setEdit] = useState({ status: task.status, progress: String(task.progress ?? 0) });
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  useEffect(() => { setEdit({ status: task.status, progress: String(task.progress ?? 0) }); }, [task.status, task.progress]);
  const writable = isOfficial(task), disabled = !writable || busy || saving || batchBusy;
  const status = boardStatus(task.status);
  const progress = Math.min(100, Math.max(0, Number(task.progress) || 0));
  const changed = edit.status !== task.status || Number(edit.progress) !== Number(task.progress ?? 0);
  const valid = edit.progress !== "" && Number.isInteger(Number(edit.progress)) && Number(edit.progress) >= 0 && Number(edit.progress) <= 100;
  const patch = async values => {
    if (saveLock.current || disabled) return;
    saveLock.current = true; setSaving(true);
    try { await onPatch(task.id, values); } finally { saveLock.current = false; setSaving(false); }
  };
  return <article className={`tb-task ${selected ? "is-selected" : ""} ${compact ? "is-card" : ""}`} data-task-id={task.id}>
    <div className="tb-select">{writable && <SelectBox aria-label={`选择任务 ${task.title}`} checked={selected} disabled={disabled} onChange={onSelect} />}</div>
    <div className="tb-title"><strong>{task.title}</strong><div className="tb-tags"><span>#{task.id}</span><span className={`task-kind ${task.task_kind}`}>{kind(task)}</span><span className={`tb-status ${status}`}>{taskStatuses.find(([s]) => s === status)?.[1]}</span>{status === "other" && <span>原状态：{task.status || "空"}</span>}</div></div>
    <div className="tb-meta"><span>责任人：{task.owner || "待补充"}</span><span>截止：{date(task.due_date)}</span><div className="tb-progress"><progress aria-label={`${task.title}进度`} max="100" value={progress} /><span>{progress}%</span></div></div>
    <div className="tb-gantt"><label><input type="checkbox" checked={Number(task.show_in_gantt) === 1} disabled={disabled} onChange={e => patch({ show_in_gantt: e.target.checked ? 1 : 0 })} />{Number(task.show_in_gantt) === 1 ? "已进甘特图" : "未进甘特图"}</label>{Number(task.show_in_gantt) === 1 && (!task.start_date || !task.due_date) && <small>日期待补充，无法绘制完整任务条</small>}{(busy || saving) && <span role="status"><span className="button-spinner" /> 保存中</span>}</div>
    <details className="tb-details"><summary>详情、来源与进度编辑</summary>
      <p>{task.description || "暂无描述"}</p><div className="tb-detail-meta"><span>开始日期：{date(task.start_date)}</span><span>状态依据截至：{date(task.status_as_of)}</span><span>记录更新：{date(task.updated_at)}</span></div>
      <div className="tb-detail-actions">{task.source_document_id && <button className="file-button" onClick={() => onPreview(task.source_document_id)}>{task.document_name || "预览来源文件"}</button>}<button className="ghost-button" onClick={() => onEvidence("task", task.id)}>查看识别依据{task.source_count ? `（${task.source_count} 个来源）` : ""}</button>{Number(task.quality_issue_count) > 0 && <span>存在待治理问题</span>}</div>
      {writable ? <form className="tb-edit" onSubmit={e => { e.preventDefault(); if (valid && changed) patch({ status: edit.status, progress: Number(edit.progress) }); }}>
        <label>执行状态<select aria-label={`${task.title}执行状态`} disabled={disabled} value={edit.status} onChange={e => setEdit(v => ({ ...v, status: e.target.value, progress: e.target.value === "completed" ? "100" : v.progress }))}>{!taskStatuses.some(([s]) => s === edit.status) && <option value={edit.status}>{edit.status || "待确认"}</option>}{taskStatuses.filter(([s]) => s !== "other").map(([s, label]) => <option key={s} value={s}>{label}</option>)}</select></label>
        <label>完成进度（%）<input aria-label={`${task.title}完成进度`} type="number" min="0" max="100" step="1" value={edit.progress} disabled={disabled} onChange={e => setEdit(v => ({ ...v, progress: e.target.value }))} /></label>
        <button type="submit" className="primary-button" disabled={disabled || !valid || !changed}>{saving ? <span className="button-spinner" /> : null}保存进度</button>
        {changed && <span>尚未保存</span>}
      </form> : <p>已归档记录只读，保留原文和全部来源。</p>}
    </details>
  </article>;
}

const recognitionTypes = { all: "全部差异", new: "疑似新增", duplicate: "重复/应聚合", invalid: "疑似误识别", match_change: "匹配变化", progress_change: "进度变化", failed: "处理失败" };
function RecognitionPreview({ api, isAdmin, onPreview }) {
  const [status, setStatus] = useState({ status: "idle", running: false });
  const [data, setData] = useState({ items: [], total: 0 });
  const [type, setType] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const load = async chosen => {
    if (!isAdmin) return;
    const current = await api.get("/api/tasks/recognition-preview-status");
    setStatus(current);
    if (current.id) setData(await api.get(`/api/tasks/recognition-preview-results?run_id=${current.id}&result_type=${chosen || type}`));
  };
  useEffect(() => { if (isAdmin) load(type).catch(err => setError(err.message)); }, [isAdmin, type]);
  useEffect(() => {
    if (!isAdmin || !status.running) return undefined;
    const timer = setInterval(() => load(type).catch(err => setError(err.message)), 2500);
    return () => clearInterval(timer);
  }, [isAdmin, status.running, type]);
  useEffect(() => { if (status.running) setOpen(true); }, [status.running]);
  if (!isAdmin) return null;
  const start = async () => {
    if (loading || status.running) return;
    setLoading(true); setError("");
    try { setOpen(true); const result = await api.post("/api/tasks/recognition-preview"); setStatus(result); await load(type); }
    catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };
  const counts = [["new", status.new_count], ["duplicate", status.duplicate_count], ["invalid", status.invalid_count], ["match_change", status.match_change_count], ["progress_change", status.progress_change_count], ["failed", status.failed_count]];
  return <details className="section recognition-preview" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>识别质量历史试算{status.running ? ` · 处理中 ${status.progress || 0}/${status.total || 0}` : status.finished_at ? ` · 最近完成 ${String(status.finished_at).slice(0, 19).replace("T", " ")}` : ""}</summary>
    <p className="tb-help">只对最近三期已索引周报及同期会议纪要生成差异报告，不修改任务、来源证据或 Wiki。</p>
    <div className="recognition-actions"><button className="primary-button" disabled={loading || status.running} onClick={start}>{loading || status.running ? <span className="button-spinner" /> : null}{status.running ? "试算中" : "开始历史试算"}</button>{status.running && <span>正在处理：{status.current_document || "准备资料"}</span>}{status.finished_at && <span className={status.unchanged ? "recognition-safe" : "recognition-alert"}>{status.unchanged ? "正式任务、证据和 Wiki 均未改变" : "检测到正式数据摘要变化，请核查"}</span>}</div>
    {status.id && <><div className="recognition-counts">{counts.map(([key, value]) => <button key={key} className={type === key ? "active" : ""} onClick={() => setType(key)}><strong>{value || 0}</strong><span>{recognitionTypes[key]}</span></button>)}</div>
      <div className="recognition-filter"><label>结果分类<select value={type} onChange={e => setType(e.target.value)}>{Object.entries(recognitionTypes).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label><span>共 {data.total || 0} 条</span></div>
      <div className="recognition-results">{data.items?.map(item => <article key={item.id}><div><span className={`recognition-type ${item.result_type}`}>{recognitionTypes[item.result_type] || item.result_type}</span><strong>{item.title || item.document_name}</strong><span>{item.source_date || "日期未知"}</span></div><p>{item.explanation}</p>{item.evidence_text && <blockquote>{item.evidence_text}</blockquote>}<div><button className="file-button" onClick={() => onPreview(item.document_id)}>{item.document_name}</button>{item.locator && <span>位置：{item.locator}</span>}{item.matched_task_title && <span>匹配：#{item.matched_task_id} {item.matched_task_title}（{Math.round((item.match_score || 0) * 100)}%）</span>}</div></article>)}{!data.items?.length && <p className="tb-help">当前分类暂无结果。</p>}</div>
    </>}
    {error && <div className="tb-result">{error}</div>}
  </details>;
}

export default function TaskBoard({ api, tasks, onCreate, onPatch, onBulkPatch, onChanged, onReconcile, reconcileStatus, isAdmin, actionStates, onPreview, onEvidence, focusedTaskId, onFocusedTaskHandled }) {
  const [draft, setDraft] = useState(emptyDraft);
  const [scope, setScope] = useState("official");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [gantt, setGantt] = useState("all");
  const [sort, setSort] = useState("default");
  const [view, setView] = useState("list");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(12);
  const [selected, setSelected] = useState([]);
  const [hideAdded, setHideAdded] = useState(true);
  const [localBusy, setLocalBusy] = useState(false);
  const [message, setMessage] = useState("");
  const batchLock = useRef(false);
  const createLock = useRef(false);
  const focusCallback = useRef(onFocusedTaskHandled);
  focusCallback.current = onFocusedTaskHandled;
  const counts = useMemo(() => ({ official: tasks.filter(isOfficial).length, observation: tasks.filter(t => !Number(t.is_archived) && t.task_kind === "observation" && !t.observation_state).length, archived: tasks.filter(t => Number(t.is_archived) === 1).length }), [tasks]);
  const filtered = useMemo(() => filterTasks(tasks, { scope, query, status, gantt, sort }), [tasks, scope, query, status, gantt, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages);
  const shown = filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const pageIds = shown.filter(isOfficial).map(t => t.id);
  const allSelected = pageIds.length > 0 && pageIds.every(id => selected.includes(id));
  const addIds = pendingGanttIds(tasks, selected, 1), removeIds = pendingGanttIds(tasks, selected, 0);
  const busy = localBusy || !!actionStates?.["bulk-patch-tasks"];
  useEffect(() => { setPage(1); setSelected([]); }, [scope, query, status, gantt, sort, pageSize, view]);
  useEffect(() => { setSelected(current => current.filter(id => pageIds.includes(id))); }, [tasks, currentPage, pageSize, scope, query, status, gantt, sort, view]);
  useEffect(() => {
    if (!focusedTaskId) return;
    const target = tasks.find(t => t.id === focusedTaskId);
    if (!target || (!isOfficial(target) && !Number(target.is_archived))) { focusCallback.current?.(); return; }
    const targetScope = Number(target.is_archived) ? "archived" : "official";
    setScope(targetScope); setQuery(""); setStatus("all"); setGantt("all"); setSort("default"); setView("list");
    const index = filterTasks(tasks, { scope: targetScope }).findIndex(t => t.id === focusedTaskId);
    const targetPage = Math.floor(index / pageSize) + 1;
    const timer = window.setTimeout(() => setPage(targetPage), 0);
    const scroll = window.setTimeout(() => {
      const node = document.querySelector(`[data-task-id="${focusedTaskId}"]`);
      node?.scrollIntoView({ behavior: "smooth", block: "center" }); node?.classList.add("focused");
      focusCallback.current?.();
    }, 150);
    return () => { clearTimeout(timer); clearTimeout(scroll); };
  }, [focusedTaskId, tasks, pageSize]);
  const bulk = async value => {
    const ids = value ? addIds : removeIds;
    if (!ids.length || busy || batchLock.current) return;
    batchLock.current = true; setLocalBusy(true); setMessage("");
    try {
      const result = await onBulkPatch(ids, { show_in_gantt: value ? 1 : 0 });
      if (!result) { setMessage("操作未完成，已保留勾选，请查看错误后重试。"); return; }
      const failed = result.skipped || [];
      // Entries already in the requested state are handled too; retain failures only.
      setSelected(current => selectionAfterBatch(current, result));
      setMessage(`已${value ? "加入" : "移出"} ${result.updated || 0} 条${failed.length ? `；${failed.length} 条未处理并保留勾选：${failed.map(item => `#${item.id} ${item.reason}`).join("；")}` : "，已清空勾选"}${value && hideAdded ? "；已加入任务从当前待加入列表隐藏" : ""}。任务记录仍保留，可切换“全部任务”查看。`);
      if (value && hideAdded) setGantt("out");
    } catch (err) { setMessage(`操作失败，勾选已保留：${err.message}`); }
    finally { batchLock.current = false; setLocalBusy(false); }
  };
  const submit = async e => {
    e.preventDefault(); if (!draft.title.trim() || createLock.current) return;
    createLock.current = true;
    try { if (await onCreate({ ...draft, title: draft.title.trim(), status: "not_started", color_status: draft.priority === "high" ? "amber" : "green", show_in_gantt: 1, progress: 0 })) setDraft(emptyDraft); }
    finally { createLock.current = false; }
  };
  const resetFilters = () => { setQuery(""); setStatus("all"); setGantt("all"); setSort("default"); };
  const renderTask = (task, compact = false) => <TaskRow key={task.id} task={task} compact={compact} selected={selected.includes(task.id)} onSelect={() => setSelected(s => s.includes(task.id) ? s.filter(id => id !== task.id) : [...s, task.id])} onPatch={onPatch} onPreview={onPreview} onEvidence={onEvidence} busy={!!actionStates?.[`patch-task-${task.id}`]} batchBusy={busy} />;
  return <div className="view-grid task-workspace">
    <section className="section tb-toolbar"><div className="tb-toolbar-head"><h2>任务管理</h2><div className="segmented" aria-label="任务范围">{[["official", "正式任务"], ["observation", "识别事项"], ["archived", "已归档"]].map(([value, label]) => <button key={value} disabled={busy} aria-pressed={scope === value} className={scope === value ? "active" : ""} onClick={() => setScope(value)}>{label} {counts[value]}</button>)}</div>{isAdmin && <button className="ghost-button" disabled={busy || reconcileStatus?.running || actionStates?.["reconcile-tasks"]} onClick={onReconcile}>{reconcileStatus?.running || actionStates?.["reconcile-tasks"] ? <span className="button-spinner" /> : null}重算任务进度</button>}</div>
      <p className="tb-help">正式任务参与仪表盘统计；识别事项先关联或提升。进入甘特图只是显示设置，不会删除或归档任务。</p>
      {reconcileStatus?.running && <div className="task-reconcile-progress" role="status"><span className="button-spinner" />后台重算 {reconcileStatus.progress || 0}/{reconcileStatus.total || 0}</div>}
      {scope !== "observation" && <>
        <div className="tb-filters"><label>搜索<input type="search" placeholder="任务名称、责任人、来源或编号" value={query} disabled={busy} onChange={e => setQuery(e.target.value)} /></label><label>执行状态<select value={status} disabled={busy} onChange={e => setStatus(e.target.value)}><option value="all">全部状态</option>{taskStatuses.map(([s, text]) => <option value={s} key={s}>{text}</option>)}</select></label><label>甘特图<select value={gantt} disabled={busy} onChange={e => { setGantt(e.target.value); setSelected([]); }}><option value="all">全部任务</option><option value="out">未进甘特图</option><option value="in">已进甘特图</option></select></label><label>排序<select value={sort} disabled={busy} onChange={e => setSort(e.target.value)}><option value="default">项目计划顺序</option><option value="due">截止日期优先</option><option value="updated">最近更新优先</option><option value="progress">进度从高到低</option></select></label><button className="ghost-button" disabled={busy} onClick={resetFilters}>清除筛选</button></div>
        <div className="tb-viewbar"><span>筛选结果 {filtered.length} / {counts[scope]} 条</span><div className="segmented" aria-label="任务显示方式"><button className={view === "list" ? "active" : ""} aria-pressed={view === "list"} disabled={busy} onClick={() => setView("list")}>任务列表</button><button className={view === "board" ? "active" : ""} aria-pressed={view === "board"} disabled={busy} onClick={() => setView("board")}>分组看板</button></div></div>
        {scope === "official" && <div className="tb-bulk"><label><SelectBox checked={allSelected} mixed={!allSelected && pageIds.some(id => selected.includes(id))} disabled={busy || !pageIds.length} onChange={() => setSelected(s => togglePageSelection(s, pageIds))} />全选当前页（{pageIds.length}）</label><span>已选 {selected.length} 条</span><button className="ghost-button" disabled={busy || !selected.length} onClick={() => setSelected([])}>取消选择</button><button className="primary-button" disabled={busy || !addIds.length} onClick={() => bulk(true)}>{busy && <span className="button-spinner" />}批量进入甘特图（{addIds.length}）</button><button className="ghost-button" disabled={busy || !removeIds.length} onClick={() => bulk(false)}>{busy && <span className="button-spinner" />}批量移出甘特图（{removeIds.length}）</button><label title="加入成功后自动切换到未进甘特图列表；任务不会从系统删除"><input type="checkbox" checked={hideAdded} disabled={busy} onChange={e => setHideAdded(e.target.checked)} />加入后切换到“未进甘特图”</label>{selected.length > 0 && addIds.length === 0 && removeIds.length > 0 && <small className="tb-bulk-note">所选任务已全部在甘特图中；任务看板会保留记录。</small>}</div>}
        {message && <div className="tb-result" role="status">{message}</div>}
      </>}
    </section>
    <RecognitionPreview api={api} isAdmin={isAdmin} onPreview={onPreview} />
    {scope === "observation" ? <ObservationInbox api={api} tasks={tasks} isAdmin={isAdmin} onChanged={onChanged} onPreview={onPreview} onEvidence={onEvidence} /> : <>
      {scope === "official" && <details className="section tb-create"><summary>＋ 新增正式任务</summary><form onSubmit={submit} className="task-form"><label>任务名称<input required value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} placeholder="任务名称" /></label><label>责任人<input value={draft.owner} onChange={e => setDraft({ ...draft, owner: e.target.value })} placeholder="待补充" /></label><label>截止日期<input type="date" value={draft.due_date} onChange={e => setDraft({ ...draft, due_date: e.target.value })} /></label><label>优先级<select value={draft.priority} onChange={e => setDraft({ ...draft, priority: e.target.value })}><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label><button type="submit" className="primary-button" disabled={actionStates?.["create-task"] || !draft.title.trim()}>{actionStates?.["create-task"] && <span className="button-spinner" />}新增任务</button></form></details>}
      {!shown.length ? <div className="section tb-empty">没有符合筛选条件的任务。<button className="ghost-button" onClick={resetFilters}>显示全部任务</button></div> : view === "list" ? <div className="tb-list">{shown.map(t => renderTask(t))}</div> : <div className="tb-board">{taskStatuses.filter(([s]) => s !== "other" || shown.some(t => boardStatus(t.status) === "other")).map(([s, label]) => { const members = shown.filter(t => boardStatus(t.status) === s); return <section className="tb-lane" key={s}><h3>{label}<span>{filtered.filter(t => boardStatus(t.status) === s).length}</span></h3><small>本页 {members.length} 条</small>{members.length ? members.map(t => renderTask(t, true)) : <p className="tb-help">本页暂无任务</p>}</section>; })}</div>}
      <div className="section tb-pagination"><span>第 {currentPage}/{pages} 页 · 共 {filtered.length} 条；全选仅作用于本页</span><label>每页<select value={pageSize} disabled={busy} onChange={e => setPageSize(Number(e.target.value))}>{[12, 24, 48].map(n => <option key={n} value={n}>{n} 条</option>)}</select></label><button className="ghost-button" disabled={busy || currentPage <= 1} onClick={() => { setPage(currentPage - 1); setSelected([]); }}>上一页</button><button className="ghost-button" disabled={busy || currentPage >= pages} onClick={() => { setPage(currentPage + 1); setSelected([]); }}>下一页</button></div>
    </>}
  </div>;
}
