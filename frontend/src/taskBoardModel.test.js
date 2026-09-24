import test from 'node:test';
import assert from 'node:assert/strict';
import { boardStatus, filterTasks, pendingGanttIds, selectionAfterBatch, togglePageSelection } from './taskBoardModel.js';
const tasks = [
  { id: 1, title: '环境部署', owner: '张工', task_kind: 'baseline', status: 'planned', show_in_gantt: 1, due_date: '2026-09-01' },
  { id: 2, title: '数据迁移', task_kind: 'confirmed_addition', status: '已完成', show_in_gantt: 0 },
  { id: 3, title: '原文识别', task_kind: 'observation', show_in_gantt: 0 },
  { id: 4, title: '历史任务', task_kind: 'baseline', is_archived: 1, show_in_gantt: 0 },
  { id: 5, title: '异常状态任务', task_kind: 'baseline', status: 'legacy_unknown', show_in_gantt: 0 }
];
test('default scope excludes observations and archives without hiding unknown statuses', () => {
  assert.deepEqual(filterTasks(tasks).map(t => t.id), [1, 2, 5]);
  assert.equal(boardStatus('legacy_unknown'), 'other');
  assert.equal(boardStatus('已完成'), 'completed');
});
test('select all is page-scoped and toggle clears only this page', () => {
  assert.deepEqual(togglePageSelection([], [1, 2]), [1, 2]);
  assert.deepEqual(togglePageSelection([1, 2], [1, 2]), []);
  assert.deepEqual(togglePageSelection([1, 5], [1]), [5]);
});
test('gantt actions skip already-added, observations and archived tasks', () => {
  assert.deepEqual(pendingGanttIds(tasks, [1, 2, 3, 4, 5], 1), [2, 5]);
  assert.deepEqual(pendingGanttIds(tasks, [1, 2, 3, 4, 5], 0), [1]);
});
test('success clears only successful selection; failure retains selection', () => {
  assert.deepEqual(selectionAfterBatch([1, 2], false), [1, 2]);
  assert.deepEqual(selectionAfterBatch([1, 2], { updatedIds: [1] }), [2]);
  assert.deepEqual(selectionAfterBatch([1, 2], { updatedIds: [1, 2] }), []);
});
test('gantt filter hides added tasks without deleting them', () => {
  assert.deepEqual(filterTasks(tasks, { gantt: 'out' }).map(t => t.id), [2, 5]);
  assert.equal(tasks.length, 5);
});
test('query, status and archive filters and dates with missing values', () => {
  assert.deepEqual(filterTasks(tasks, { query: '环境 张工' }).map(t => t.id), [1]);
  assert.deepEqual(filterTasks(tasks, { status: 'completed' }).map(t => t.id), [2]);
  assert.deepEqual(filterTasks(tasks, { scope: 'archived' }).map(t => t.id), [4]);
  assert.equal(filterTasks(tasks, { sort: 'due' })[0].id, 1);
});
