import React, { useEffect, useRef, useState } from "react";

const categories = { all: "全部待处理", link: "待关联", new: "疑似新增", conflict: "存在冲突", processed: "已处理" };
const states = { linked: "已关联", promoted: "已提升", ignored: "已忽略", transferred: "已转交" };
const fields = { status: "状态", progress: "进度", owner: "责任人", due_date: "截止日期" };
const statusLabels = { not_started: "未开始", in_progress: "进行中", completed: "已完成", blocked: "待协调", delayed: "已延期", pending: "待确认", planned: "计划中" };
const show = (value) => statusLabels[value] || String(value ?? "待补充");
const requestKey = () => globalThis.crypto?.randomUUID?.() || `obs-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export default function ObservationInbox({ api, tasks, isAdmin, onChanged, onPreview, onEvidence }) {
  const [category, setCategory] = useState("all");
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ items: [], counts: {} });
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [drafts, setDrafts] = useState(null);
  const [batch, setBatch] = useState(null);
  const finished = useRef("");
  const submission = useRef(null);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const officials = tasks.filter(t => !Number(t.is_archived) && ["baseline", "confirmed_addition"].includes(t.task_kind));
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision(v => v + 1);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.get(`/api/task-observations?category=${category}&page=${page}`).then(result => {
      if (cancelled) return;
      if (!result.items.length && page > 1) { setPage(page - 1); return; }
      setData(result);
      setSelected(current => current.filter(id => result.items.some(g => g.id === id)));
    }).catch(err => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, category, page, revision, tasks]);

  useEffect(() => {
    if (!isAdmin) { setDrafts(null); setSelected([]); return; }
    let cancelled = false, timer;
    const poll = async () => {
      try {
        const result = await api.get("/api/task-observations/batch-status");
        if (cancelled) return;
        setBatch(result);
        if (result.id && !result.running && finished.current !== result.id) {
          finished.current = result.id;
          refresh();
          await onChangedRef.current?.();
        }
      } catch (err) { if (!cancelled) setError(`读取批次状态失败：${err.message}`); }
      if (!cancelled) timer = setTimeout(poll, 2500);
    };
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, isAdmin]);

  const chooseCategory = value => { setCategory(value); setPage(1); setSelected([]); setError(""); };
  const toggle = id => setSelected(current => current.includes(id) ? current.filter(v => v !== id) : [...current, id]);
  const open = async (ids, action) => {
    if (!ids.length || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.post("/api/task-observations/preview", { ids });
      setDrafts(result.items.map(g => ({ ...g, action, targetId: g.candidates[0]?.id || "", keepIndependent: false, reason: "", suggestionType: "risk" })));
      submission.current = null;
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const edit = (id, values) => { submission.current = null; setDrafts(current => current.map(g => g.id === id ? { ...g, ...values } : g)); };
  const remove = id => { submission.current = null; setDrafts(current => current.filter(g => g.id !== id)); };
  const blocked = g => g.action === "promote" && (!g.title.trim() || g.candidates.some(c => c.exact) || ((g.candidates.length > 0 || g.category === "conflict") && !g.keepIndependent)) ||
    g.action === "link" && !g.targetId || ["ignore", "transfer"].includes(g.action) && !g.reason.trim();
  const submit = async () => {
    if (busy || !drafts?.length || drafts.some(blocked)) return;
    setBusy(true); setError("");
    const items = drafts.map(g => ({ id: g.id, fingerprint: g.fingerprint, action: g.action, title: g.title, owner: g.owner || "", due_date: g.due_date || "", targetId: Number(g.targetId) || null, keepIndependent: g.keepIndependent, reason: g.reason, suggestionType: g.suggestionType }));
    try {
      if (items.length === 1) {
        const result = await api.post(`/api/task-observations/${items[0].id}/resolve`, items[0]);
        setNotice(result.alreadyProcessed ? "该事项已处理，无需重复操作" : "处理成功；关联证据不会直接修改正式任务字段");
        await onChangedRef.current?.();
      } else {
        submission.current ||= { requestKey: requestKey(), items };
        const result = await api.post("/api/task-observations/resolve-batch", submission.current);
        setBatch(result);
        setNotice("已提交后台处理，可切换页面或关闭网页；失败项仍保留待处理。");
      }
      setDrafts(null); setSelected([]); refresh();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const source = task => task.source_document_id ? <button className="file-button" onClick={() => onPreview(task.source_document_id)}>{task.document_name || `来源文件 #${task.source_document_id}`}</button> : <span>无关联文件</span>;
  const memberDetails = g => <details className="observation-details"><summary>查看 {g.members.length} 条原始记录及来源</summary>
    {g.members.map(t => <article key={t.id}>
      <strong>#{t.id} {t.title}</strong><p>{t.description || "未提供原文描述"}</p>
      <div className="observation-meta"><span>{show(t.status)} · {t.progress || 0}%</span><span>责任人：{t.owner || "待补充"}</span><span>截止：{t.due_date || "待补充"}</span><span>资料：{t.source_date || "日期未识别"}</span></div>
      {source(t)} <button className="ghost-button" onClick={() => onEvidence("task", t.id)}>查看识别依据与时间线</button>
      {!!t.evidence?.length && <details><summary>已有来源时间线（{t.evidence.length} 条证据）</summary>{t.evidence.map(e => <div className="observation-diffs" key={e.id}><span>{e.source_date || "日期未识别"} · {e.locator || "原文片段"}</span><p>{e.snippet}</p>{e.document_id && <button className="file-button" onClick={() => onPreview(e.document_id)}>{e.document_name || `来源文件 #${e.document_id}`}</button>}</div>)}</details>}

    </article>)}
  </details>;

  return <section className="section observation-inbox">
    <div className="observation-toolbar"><h2>识别事项收件箱</h2><button className="ghost-button" disabled={loading} onClick={refresh}>{loading && <span className="button-spinner" />}刷新</button></div>
    <p className="muted">待处理 {data.pendingGroups || 0} 组 / {data.pendingRecords || 0} 条原始记录。优先关联已有任务；只有确认新增才增加正式任务总数。</p>
    <div className="observation-toolbar" role="group" aria-label="识别事项分类">
      {Object.entries(categories).map(([key, label]) => <button key={key} className={category === key ? "primary-button" : "ghost-button"} onClick={() => chooseCategory(key)}>{label} {key === "all" ? data.pendingGroups || 0 : data.counts[key] || 0}</button>)}
    </div>
    {isAdmin && category !== "processed" && <div className="observation-toolbar">
      <label><input type="checkbox" aria-label="全选当前页" disabled={!data.items.length || loading} checked={data.items.length > 0 && data.items.every(g => selected.includes(g.id))} onChange={e => setSelected(e.target.checked ? data.items.map(g => g.id) : [])} /> 全选当前页</label>
      <span>已选 {selected.length} 组</span>
      <button className="primary-button" disabled={!selected.length || busy || batch?.running} onClick={() => open(selected, "promote")}>{busy && <span className="button-spinner" />}批量提升为正式任务（{selected.length}）</button>
      <button className="ghost-button" disabled={!selected.length || busy || batch?.running} onClick={() => open(selected, "link")}>批量关联</button>
      <button className="ghost-button" disabled={!selected.length || busy || batch?.running} onClick={() => open(selected, "ignore")}>批量忽略</button>
    </div>}
    {notice && <div className="observation-notice" role="status">{notice}</div>}
    {error && <div className="observation-error" role="alert">{error}</div>}
    {batch?.id && <div className="observation-notice" role="status">
      {batch.running && <span className="button-spinner" />} {batch.running ? "后台处理中" : "最近批次"}：{batch.progress}/{batch.total}，成功 {batch.succeeded}，失败 {batch.failed}
      {batch.running && <progress value={batch.progress} max={batch.total || 1} />}
      {batch.error && <p className="observation-error">{batch.error}</p>}
      {batch.items?.filter(i => i.error).map((i, index) => <p key={index}>#{i.id}：{i.error}</p>)}
    </div>}
    {loading && <p role="status"><span className="button-spinner" /> 正在加载识别事项…</p>}
    {!loading && !data.items.length && <p className="muted">此分类暂无事项。</p>}
    {data.items.map(g => <article className="observation-card" key={g.id}>
      <div className="observation-toolbar"><label>{isAdmin && g.category !== "processed" && <input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggle(g.id)} />} <strong>{g.title}</strong></label><span className="pill">{categories[g.category]}</span><span>{g.sourceCount} 份来源 · {g.sourceDate || "资料日期未识别"}</span></div>
      <p>{g.members[0]?.description || "请展开原始记录核对识别内容"}</p>
      {g.candidates[0] && <div className="observation-notice">推荐关联：{g.candidates[0].title}（文本相似度 {Math.round(g.candidates[0].similarity * 100)}%）<br />{g.candidates[0].reason}；相似度不代表同一事项，需核对范围。</div>}
      {g.differences.length > 0 && <div className="observation-diffs">{g.differences.map(d => <div key={d.field}>{fields[d.field]}：正式值 {show(d.current)} → 识别值 {d.observed.map(show).join(" / ")}</div>)}</div>}
      {g.resolution && <p>{states[g.resolution.state]}{g.resolution.target_id ? ` · 关联任务 #${g.resolution.target_id}` : ""} · {g.resolution.actor} · {g.resolution.created_at}<br />{g.resolution.reason}</p>}
      {memberDetails(g)}
      {isAdmin && g.category !== "processed" && <div className="observation-toolbar">
        <button className="ghost-button" disabled={busy} onClick={() => open([g.id], "link")}>关联已有任务</button>
        <button className="primary-button" disabled={busy} onClick={() => open([g.id], "promote")}>提升为正式任务</button>
        <button className="ghost-button" disabled={busy} onClick={() => open([g.id], "transfer")}>转为其他建议</button>
        <button className="ghost-button" disabled={busy} onClick={() => open([g.id], "ignore")}>忽略/归档</button>
      </div>}
    </article>)}
    <div className="observation-toolbar"><button className="ghost-button" disabled={page <= 1 || loading} onClick={() => { setPage(p => p - 1); setSelected([]); }}>上一页</button><span>第 {page} 页 · 共 {data.total || 0} 组</span><button className="ghost-button" disabled={page * (data.pageSize || 15) >= data.total || loading} onClick={() => { setPage(p => p + 1); setSelected([]); }}>下一页</button></div>

    {drafts && <div className="modal-backdrop"><section className="modal observation-modal" role="dialog" aria-modal="true" aria-labelledby="observation-dialog-title">
      <div className="modal-head"><div><h2 id="observation-dialog-title">确认处理识别事项（{drafts.length} 组）</h2><p>每组仅生成一条正式任务，采用组内最新记录的状态与进度；缺失信息显示待补充；默认不进入甘特图。批量关联只处理高置信、无冲突项。</p></div><button aria-label="关闭确认窗口" className="ghost-button" disabled={busy} onClick={() => setDrafts(null)}>关闭</button></div>
      <div className="observation-modal-body">
        {error && <div role="alert" className="observation-error">{error}</div>}
        {drafts.map(g => <article className="observation-card" key={g.id}>
          <div className="observation-toolbar"><strong>#{g.id} · {g.members.length} 条来源记录</strong><button className="ghost-button" disabled={busy} onClick={() => remove(g.id)}>移除此项</button></div>
          <fieldset disabled={busy} className="observation-form">
            <label>处理方式<select value={g.action} onChange={e => edit(g.id, { action: e.target.value })}><option value="promote">提升为正式任务</option><option value="link">关联已有任务</option><option value="ignore">忽略/归档</option><option value="transfer">转为其他建议</option></select></label>
            <label>任务标题<input value={g.title} maxLength={300} disabled={g.action !== "promote"} onChange={e => edit(g.id, { title: e.target.value })} placeholder="标题必填" /></label>
            {g.action === "promote" && <><label>责任人<input value={g.owner} onChange={e => edit(g.id, { owner: e.target.value })} placeholder="待补充" /></label><label>截止日期（可留空）<input type="date" value={g.due_date} onChange={e => edit(g.id, { due_date: e.target.value })} /></label></>}
            {g.action === "link" && <label>选择正式任务<select value={g.targetId} onChange={e => edit(g.id, { targetId: Number(e.target.value) })}><option value="">请选择</option>{officials.map(t => <option key={t.id} value={t.id}>#{t.id} {t.title}</option>)}</select></label>}
            {g.action === "transfer" && <label>建议类型<select value={g.suggestionType} onChange={e => edit(g.id, { suggestionType: e.target.value })}><option value="risk">风险</option><option value="change_request">变更需求</option><option value="deliverable">交付物</option></select></label>}
            {["ignore", "transfer"].includes(g.action) && <label>处理原因（必填）<input value={g.reason} onChange={e => edit(g.id, { reason: e.target.value })} /></label>}
          </fieldset>
          {g.candidates.map(c => <p key={c.id} className="observation-notice">{c.exact ? "已有同名同范围任务，不能重复提升" : "疑似重复"}：#{c.id} {c.title} · {Math.round(c.similarity * 100)}% · {c.reason}</p>)}
          {g.action === "promote" && (g.candidates.length > 0 || g.category === "conflict") && !g.candidates.some(c => c.exact) && <label className="observation-confirm"><input type="checkbox" disabled={busy} checked={g.keepIndependent} onChange={e => edit(g.id, { keepIndependent: e.target.checked })} /> 我已逐项核对，确认这是独立工作，保持独立并新增正式任务</label>}
          {g.differences.map(d => <p key={d.field}>{fields[d.field]}：{show(d.current)} → {d.observed.map(show).join(" / ")}</p>)}
          {memberDetails(g)}
        </article>)}
      </div>
      <div className="observation-modal-footer"><button className="primary-button" disabled={busy || !drafts.length || drafts.some(blocked)} onClick={submit}>{busy && <span className="button-spinner" />}{busy ? "提交中…" : `确认处理 ${drafts.length} 组`}</button><span>重复或冲突项请逐项处理，或移除后再提交。</span></div>
    </section></div>}
  </section>;
}
