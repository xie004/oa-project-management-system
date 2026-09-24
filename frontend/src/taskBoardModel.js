export const taskStatuses = [["not_started", "待开始"], ["in_progress", "进行中"], ["blocked", "待协调"], ["completed", "已完成"], ["delayed", "已延期"], ["other", "待核对状态"]];
export function boardStatus(value) {
  const aliases = { planned: "not_started", pending: "not_started", open: "not_started", "计划中": "not_started", "未开始": "not_started", "待开始": "not_started", "待确认": "not_started", "已完成": "completed", "进行中": "in_progress", "待协调": "blocked", "已延期": "delayed" };
  const key = aliases[value] || value;
  return taskStatuses.some(([s]) => s === key) ? key : "other";
}
export const isOfficial = t => !Number(t.is_archived) && ["baseline", "confirmed_addition"].includes(t.task_kind);
export function filterTasks(tasks, { scope = "official", query = "", status = "all", gantt = "all", sort = "default" } = {}) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const result = tasks.filter(t => (scope === "archived" ? Number(t.is_archived) === 1 : isOfficial(t)) &&
    (status === "all" || boardStatus(t.status) === status) &&
    (gantt === "all" || (Number(t.show_in_gantt) === 1) === (gantt === "in")) &&
    words.every(w => [t.title, t.owner, t.description, t.document_name, String(t.id)].join(" ").toLowerCase().includes(w)));
  if (sort === "due") result.sort((a, b) => (a.due_date || "9999").localeCompare(b.due_date || "9999") || a.id - b.id);
  if (sort === "updated") result.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "") || b.id - a.id);
  if (sort === "progress") result.sort((a, b) => Number(b.progress || 0) - Number(a.progress || 0) || a.id - b.id);
  return result;
}
export const togglePageSelection = (selected, ids) => ids.every(id => selected.includes(id)) ? selected.filter(id => !ids.includes(id)) : [...new Set([...selected, ...ids])];
export const pendingGanttIds = (tasks, selected, value) => tasks.filter(t => selected.includes(t.id) && isOfficial(t) && Number(t.show_in_gantt || 0) !== Number(value)).map(t => t.id);
export const selectionAfterBatch = (selected, result) => {
  if (!result) return selected;
  // A successful batch finishes the selected work queue. Keep only records
  // explicitly reported as failed so users can correct and retry them.
  const failed = new Set((result.skipped || []).map(item => Number(item.id)));
  return selected.filter(id => failed.has(Number(id)));
};
