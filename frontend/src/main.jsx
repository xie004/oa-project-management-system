import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  Circle,
  Download,
  FileText,
  Flag,
  FolderCog,
  Gauge,
  GitPullRequest,
  GripVertical,
  Kanban,
  LayoutDashboard,
  ListChecks,
  Milestone,
  Plus,
  RefreshCw,
  RotateCcw,
  Settings,
  Sparkles,
  X
} from "lucide-react";
import "./styles.css";

const api = {
  async get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async put(path, body) {
    const response = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async patch(path, body) {
    const response = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
};

const navItems = [
  { id: "dashboard", label: "仪表盘", icon: LayoutDashboard },
  { id: "gantt", label: "甘特图", icon: CalendarDays },
  { id: "board", label: "任务看板", icon: Kanban },
  { id: "milestones", label: "里程碑", icon: Milestone },
  { id: "meetings", label: "会议纪要", icon: FileText },
  { id: "changes", label: "变更需求", icon: GitPullRequest },
  { id: "suggestions", label: "智能建议", icon: Sparkles },
  { id: "documents", label: "资料台账", icon: FolderCog },
  { id: "weekly", label: "周报汇总", icon: ListChecks },
  { id: "settings", label: "系统设置", icon: Settings }
];

const statusText = {
  not_started: "待开始",
  in_progress: "进行中",
  blocked: "待协调",
  completed: "已完成",
  delayed: "已延期",
  planned: "计划中",
  pending: "待确认",
  open: "未关闭",
  closed: "已关闭",
  applied: "已应用",
  dismissed: "已忽略"
};

const colorText = {
  green: "绿色",
  amber: "黄色",
  red: "红色"
};

const typeText = {
  weekly_report: "项目周报",
  meeting: "会议纪要",
  resource: "资源需求",
  contract_tender: "合同招标",
  requirement_change: "需求变更",
  acceptance_launch: "上线验收",
  other: "其他资料",
  task: "任务",
  risk: "风险",
  milestone: "里程碑",
  change_request: "变更"
};

const refinedMilestoneTitles = new Set([
  "合同签订与项目启动",
  "启动会与主计划初版完成",
  "测试环境基础部署完成",
  "迁移策略与样例验证完成",
  "签章与档案系统测试部署完成",
  "公文数据首轮试迁移完成",
  "系统集成清单确认",
  "生产资源方案确认",
  "财务预算报销方案确认",
  "表单批量试迁移完成",
  "新样式表单与前台逻辑完成",
  "生产资源申请与环境部署完成",
  "系统集成开发联调完成",
  "内部验证与问题整改完成",
  "UAT 用户测试完成",
  "上线申请提交",
  "正式切换上线",
  "试运行满 30 日",
  "测评与验收资料齐套",
  "项目整体验收完成"
]);

function safeDate(value) {
  if (!value) return null;
  const date = new Date(`${value}`.slice(0, 10));
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  if (!value) return "-";
  return `${value}`.slice(0, 10);
}

function statusLabel(value) {
  return statusText[value] || value || "-";
}

function Stat({ label, value, icon: Icon, tone = "neutral" }) {
  return (
    <div className={`stat stat-${tone}`}>
      <Icon size={20} />
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}

function StatusPill({ value, color }) {
  return <span className={`pill ${color || value || "neutral"}`}>{statusLabel(value) || colorText[color]}</span>;
}

function Section({ title, action, children }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Empty({ text = "暂无数据" }) {
  return <div className="empty">{text}</div>;
}

function FileButton({ documentId, name, onPreview }) {
  if (!documentId || !name) return <span>{name || "-"}</span>;
  return (
    <button className="file-link" onClick={() => onPreview(documentId)} title="预览文件">
      <FileText size={14} />
      {name}
    </button>
  );
}

function PreviewModal({ preview, loading, onClose }) {
  if (!preview && !loading) return null;
  const doc = preview?.document || {};
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="modal-head">
          <div>
            <h2>{loading ? "文件预览" : doc.name}</h2>
            <p>{doc.path || ""}</p>
          </div>
          <button className="icon-button" onClick={onClose} title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="modal-meta">
          {doc.doc_category && <StatusPill value={typeText[doc.doc_category] || doc.doc_category} color="blue" />}
          {preview?.status && <StatusPill value={preview.status} color={preview.status === "error" ? "red" : "green"} />}
          {doc.modified_at && <span>{formatDate(doc.modified_at)}</span>}
        </div>
        {loading ? (
          <div className="empty">正在读取文件内容</div>
        ) : (
          <pre className="preview-text">{preview?.text || preview?.error || "暂无可预览内容"}</pre>
        )}
      </div>
    </div>
  );
}

function ProjectPlanContent({ profile = {}, goals = [], milestones = [], breakdown = [] }) {
  const planItems = milestones.filter((item) =>
    ["合同签订与项目启动", "启动会与主计划初版完成", "测试环境基础部署完成", "迁移策略与样例验证完成", "表单批量试迁移完成", "新样式表单与前台逻辑完成", "系统集成开发联调完成", "UAT 用户测试完成", "正式切换上线", "项目整体验收完成"].includes(item.title)
  );
  return (
    <>
      <div className="goal-grid">
        <div>
          <div className="mini-title">建设目标</div>
          <ul className="goal-list">
            {(goals || []).map((goal, index) => <li key={`${goal}-${index}`}>{goal}</li>)}
            {!goals?.length && <li>{profile.description || "暂无项目目标"}</li>}
          </ul>
        </div>
        <div>
          <div className="mini-title">计划周期</div>
          <div className="plan-dates">
            <span>启动：{formatDate(profile.start_date)}</span>
            <span>目标验收：{formatDate(profile.target_date)}</span>
          </div>
          <div className="plan-strip">
            {planItems.map((item) => (
              <div className={`plan-node ${item.color_status || "green"}`} key={item.id}>
                <Circle size={10} />
                <span>{item.title}</span>
                <small>{formatDate(item.planned_date)}</small>
              </div>
            ))}
          </div>
        </div>
      </div>
      {!!breakdown?.length && (
        <div className="phase-grid">
          {breakdown.map((phase) => (
            <div className={`phase-card ${phase.status || "planned"}`} key={phase.phase}>
              <div className="phase-head">
                <div>
                  <strong>{phase.phase}</strong>
                  <span>{phase.period}</span>
                </div>
                <StatusPill value={phase.status} color={phase.status === "completed" ? "green" : phase.status === "in_progress" ? "amber" : "blue"} />
              </div>
              <p>{phase.objective}</p>
              <div className="phase-progress">
                <span style={{ width: `${phase.progress || 0}%` }} />
              </div>
              <div className="phase-cols">
                <div>
                  <div className="mini-title">重点工作</div>
                  <ul>
                    {(phase.work || []).slice(0, 6).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div>
                  <div className="mini-title">交付物</div>
                  <ul>
                    {(phase.deliverables || []).slice(0, 6).map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              </div>
              <small>依据：{phase.basis}</small>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function ProjectPlanPanel({ profile = {}, goals = [], milestones = [], breakdown = [] }) {
  return (
    <Section title="项目目标与计划">
      <ProjectPlanContent profile={profile} goals={goals} milestones={milestones} breakdown={breakdown} />
    </Section>
  );
}

const dashboardLayoutKey = "oa-dashboard-layout-v3";
const taskStatusViewKey = "oa-task-status-view-v1";
const defaultDashboardLayout = [
  { id: "risks", span: 12 },
  { id: "suggestions", span: 4 },
  { id: "documents", span: 4 },
  { id: "projectPlan", span: 4 }
];

const taskStatusColors = {
  completed: "#16835d",
  in_progress: "#2563eb",
  blocked: "#b7791f",
  delayed: "#c24135",
  not_started: "#64748b",
  other: "#475569"
};

function clampSpan(value) {
  return Math.max(3, Math.min(12, Number(value) || 4));
}

function normalizeDashboardLayout(layout) {
  const knownIds = defaultDashboardLayout.map((item) => item.id);
  const fallback = defaultDashboardLayout.map((item) => ({ ...item }));
  if (!Array.isArray(layout)) return fallback;
  const normalized = [];
  for (const item of layout) {
    if (item && knownIds.includes(item.id) && !normalized.some((existing) => existing.id === item.id)) {
      normalized.push({ id: item.id, span: clampSpan(item.span) });
    }
  }
  for (const item of fallback) {
    if (!normalized.some((existing) => existing.id === item.id)) normalized.push(item);
  }
  return normalized;
}

function loadDashboardLayout() {
  try {
    return normalizeDashboardLayout(JSON.parse(localStorage.getItem(dashboardLayoutKey)));
  } catch {
    return normalizeDashboardLayout();
  }
}

function loadTaskStatusView() {
  try {
    return localStorage.getItem(taskStatusViewKey) === "list" ? "list" : "stack";
  } catch {
    return "stack";
  }
}

function TaskStatusInline({ items, view, onViewChange }) {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  const visibleItems = items.filter((item) => item.value > 0);

  return (
    <div className="hero-task-status">
      <div className="hero-panel-head">
        <div>
          <span>任务状态</span>
          <strong>{total}</strong>
        </div>
        <div className="status-mode" aria-label="任务状态显示类型">
          <button className={view === "stack" ? "active" : ""} onClick={() => onViewChange("stack")} type="button">
            条形
          </button>
          <button className={view === "list" ? "active" : ""} onClick={() => onViewChange("list")} type="button">
            列表
          </button>
        </div>
      </div>

      {!visibleItems.length ? (
        <div className="status-empty">暂无任务</div>
      ) : view === "list" ? (
        <div className="status-list">
          {visibleItems.map((item) => (
            <div className="status-list-row" key={item.key}>
              <span>
                <i style={{ backgroundColor: item.color }} />
                {item.label}
              </span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="status-stack" aria-label="任务状态分布">
            {visibleItems.map((item) => (
              <span
                key={item.key}
                style={{
                  width: `${Math.max(7, (item.value / total) * 100)}%`,
                  backgroundColor: item.color
                }}
                title={`${item.label}：${item.value}`}
              />
            ))}
          </div>
          <div className="status-legend">
            {visibleItems.map((item) => (
              <span key={item.key}>
                <i style={{ backgroundColor: item.color }} />
                {item.label} {item.value}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function DashboardWidget({ widget, title, action, children, onWidgetDragStart, onResizeStart, dragging }) {
  return (
    <section
      className={`dashboard-widget section ${dragging ? "dragging" : ""}`}
      data-widget-id={widget.id}
      style={{ gridColumn: `span ${widget.span}` }}
    >
      <div className="section-head widget-head" onMouseDown={(event) => onWidgetDragStart(event, widget.id)}>
        <div className="widget-title">
          <GripVertical size={17} />
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      <div className="dashboard-widget-body">{children}</div>
      <button
        className="resize-handle"
        onMouseDown={(event) => onResizeStart(event, widget.id, widget.span)}
        title="调整宽度"
        aria-label="调整宽度"
      />
    </section>
  );
}

function DashboardWorkspace({ widgets, renderWidget, onReset }) {
  const [layout, setLayout] = useState(loadDashboardLayout);
  const [draggingId, setDraggingId] = useState("");

  useEffect(() => {
    localStorage.setItem(dashboardLayoutKey, JSON.stringify(layout));
  }, [layout]);

  const moveWidget = (sourceId, targetId) => {
    if (!sourceId || sourceId === targetId) return;
    setLayout((current) => {
      const next = [...current];
      const sourceIndex = next.findIndex((item) => item.id === sourceId);
      const targetIndex = next.findIndex((item) => item.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0) return current;
      const [moved] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, moved);
      return next;
    });
  };

  const updateSpan = (id, span) => {
    setLayout((current) => current.map((item) => (item.id === id ? { ...item, span: clampSpan(span) } : item)));
  };

  const startResize = (event, id, startSpan) => {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const onMove = (moveEvent) => {
      const delta = Math.round((moveEvent.clientX - startX) / 90);
      updateSpan(id, startSpan + delta);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const startWidgetDrag = (event, id) => {
    if (event.target.closest("button,a,input,select,textarea")) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    setDraggingId(id);
    document.body.style.userSelect = "none";

    const onMove = (moveEvent) => {
      if (Math.abs(moveEvent.clientX - startX) + Math.abs(moveEvent.clientY - startY) > 6) {
        document.body.classList.add("dashboard-is-dragging");
      }
    };
    const onUp = (upEvent) => {
      const target = document.elementFromPoint(upEvent.clientX, upEvent.clientY)?.closest(".dashboard-widget");
      const targetId = target?.dataset?.widgetId;
      moveWidget(id, targetId);
      setDraggingId("");
      document.body.style.userSelect = "";
      document.body.classList.remove("dashboard-is-dragging");
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const resetLayout = () => {
    const reset = normalizeDashboardLayout();
    setLayout(reset);
    localStorage.setItem(dashboardLayoutKey, JSON.stringify(reset));
    onReset?.();
  };

  return (
    <div className="dashboard-workspace">
      <div className="dashboard-workspace-head">
        <button className="ghost-button" onClick={resetLayout}>
          <RotateCcw size={16} />
          恢复布局
        </button>
      </div>
      <div className="dashboard-grid">
        {layout.map((item) => {
          const config = widgets[item.id];
          if (!config) return null;
          return (
            <DashboardWidget
              key={item.id}
              widget={item}
              title={config.title}
              action={config.action}
              dragging={draggingId === item.id}
              onWidgetDragStart={startWidgetDrag}
              onResizeStart={startResize}
            >
              {renderWidget(item.id)}
            </DashboardWidget>
          );
        })}
      </div>
    </div>
  );
}

function Dashboard({ data, onScan, onPreview }) {
  const [taskStatusView, setTaskStatusView] = useState(loadTaskStatusView);

  useEffect(() => {
    try {
      localStorage.setItem(taskStatusViewKey, taskStatusView);
    } catch {
      // Browser storage can be unavailable in privacy modes; the control still works for the session.
    }
  }, [taskStatusView]);

  const taskStatus = useMemo(() => {
    const counts = {};
    for (const task of data.tasks || []) {
      const key = task.status || "not_started";
      counts[key] = (counts[key] || 0) + 1;
    }
    const orderedKeys = ["completed", "in_progress", "blocked", "delayed", "not_started"];
    const ordered = orderedKeys.map((key) => ({
      key,
      label: statusLabel(key),
      value: counts[key] || 0,
      color: taskStatusColors[key]
    }));
    const other = Object.keys(counts)
      .filter((key) => !orderedKeys.includes(key))
      .sort()
      .map((key) => ({
        key,
        label: statusLabel(key),
        value: counts[key],
        color: taskStatusColors.other
      }));
    return [...ordered, ...other];
  }, [data.tasks]);

  const profile = data.profile || {};
  const dashboardWidgets = {
    risks: { title: "近期风险" },
    suggestions: {
      title: "智能更新",
      action: (
        <button className="icon-button" onClick={onScan} title="重新扫描">
          <RefreshCw size={17} />
        </button>
      )
    },
    documents: { title: "最近资料" },
    projectPlan: { title: "项目目标与计划" }
  };

  const renderDashboardWidget = (id) => {
    if (id === "risks") {
      return (
        <div className="list">
          {(data.risks || []).slice(0, 6).map((risk) => (
            <div className="list-row" key={risk.id}>
              <div>
                <strong>{risk.title}</strong>
                <p>{risk.description}</p>
                {risk.document_name && <p><FileButton documentId={risk.source_document_id} name={risk.document_name} onPreview={onPreview} /></p>}
              </div>
              <StatusPill value={risk.status} color={risk.level === "high" ? "red" : "amber"} />
            </div>
          ))}
          {!(data.risks || []).length && <Empty />}
        </div>
      );
    }

    if (id === "suggestions") {
      return (
        <div className="list compact-list">
          {(data.suggestions || []).map((item) => (
            <div className="list-row" key={item.id}>
              <div>
                <strong>{item.title}</strong>
                <p><FileButton documentId={item.document_id} name={item.document_name} onPreview={onPreview} /></p>
              </div>
              <StatusPill value={typeText[item.suggestion_type]} color="blue" />
            </div>
          ))}
          {!(data.suggestions || []).length && <Empty />}
        </div>
      );
    }

    if (id === "documents") {
      return (
        <div className="list compact-list">
          {(data.documents || []).map((doc) => (
            <div className="list-row" key={doc.id}>
              <div>
                <strong><FileButton documentId={doc.id} name={doc.name} onPreview={onPreview} /></strong>
                <p>{formatDate(doc.modified_at)} · {typeText[doc.doc_category] || doc.doc_category}</p>
              </div>
              <StatusPill value={doc.status} color={doc.status === "error" ? "red" : "green"} />
            </div>
          ))}
          {!(data.documents || []).length && <Empty />}
        </div>
      );
    }

    if (id === "projectPlan") {
      return (
        <ProjectPlanContent
          profile={profile}
          goals={data.projectGoals || []}
          milestones={data.milestones || []}
          breakdown={data.projectPlanBreakdown || []}
        />
      );
    }

    return null;
  };

  return (
    <div className="view-grid">
      <div className={`hero-status ${profile.color_status || "green"}`}>
        <div>
          <div className="eyebrow">当前项目</div>
          <h1>{profile.name || "协同办公平台（OA）集成项目"}</h1>
          <div className="hero-meta">
            <span>阶段：{profile.phase || "开发实施"}</span>
            <span>状态：{colorText[profile.color_status] || profile.color_status}</span>
            <span>最近扫描：{formatDate(data.lastScanAt)}</span>
          </div>
        </div>
        <div className="hero-side">
          <TaskStatusInline items={taskStatus} view={taskStatusView} onViewChange={setTaskStatusView} />
          <div className="progress-ring">
            <div className="progress-number">{profile.overall_progress || 0}%</div>
            <div className="progress-label">总体进度</div>
          </div>
        </div>
      </div>

      <div className="stats-grid">
        <Stat label="任务总数" value={data.counts?.tasks || 0} icon={ListChecks} tone="blue" />
        <Stat label="已完成任务" value={data.counts?.completedTasks || 0} icon={Check} tone="green" />
        <Stat label="开放风险" value={data.counts?.openRisks || 0} icon={AlertTriangle} tone="amber" />
        <Stat label="待确认建议" value={data.counts?.pendingSuggestions || 0} icon={Sparkles} tone="red" />
        <Stat label="里程碑" value={data.counts?.milestones || 0} icon={Flag} tone="neutral" />
        <Stat label="资料文件" value={data.counts?.documents || 0} icon={FolderCog} tone="neutral" />
      </div>

      <DashboardWorkspace widgets={dashboardWidgets} renderWidget={renderDashboardWidget} />
    </div>
  );
}

function Gantt({ tasks }) {
  const dated = tasks.filter((task) => safeDate(task.start_date) || safeDate(task.due_date));
  const starts = dated.map((task) => safeDate(task.start_date) || safeDate(task.due_date));
  const ends = dated.map((task) => safeDate(task.due_date) || safeDate(task.start_date));
  const minDate = starts.length ? new Date(Math.min(...starts.map((date) => date.getTime()))) : new Date("2026-03-01");
  const maxDate = ends.length ? new Date(Math.max(...ends.map((date) => date.getTime()))) : new Date("2026-07-31");
  minDate.setDate(minDate.getDate() - 7);
  maxDate.setDate(maxDate.getDate() + 14);
  const totalDays = Math.max(1, Math.ceil((maxDate - minDate) / 86400000));

  const barStyle = (task) => {
    const start = safeDate(task.start_date) || safeDate(task.due_date) || minDate;
    const end = safeDate(task.due_date) || safeDate(task.start_date) || maxDate;
    const left = Math.max(0, Math.round(((start - minDate) / 86400000 / totalDays) * 100));
    const width = Math.max(4, Math.round((((end - start) / 86400000 + 1) / totalDays) * 100));
    return { left: `${left}%`, width: `${Math.min(width, 100 - left)}%` };
  };

  return (
    <Section title="甘特图">
      <div className="gantt">
        <div className="gantt-scale">
          <span>{formatDate(minDate.toISOString())}</span>
          <span>{formatDate(maxDate.toISOString())}</span>
        </div>
        {tasks.map((task) => (
          <div className="gantt-row" key={task.id}>
            <div className="gantt-info">
              <strong>{task.title}</strong>
              <span>{task.owner || "-"} · {statusLabel(task.status)}</span>
            </div>
            <div className="gantt-track">
              <div className={`gantt-bar ${task.color_status || "green"}`} style={barStyle(task)}>
                <span>{task.progress || 0}%</span>
              </div>
            </div>
          </div>
        ))}
        {!tasks.length && <Empty />}
      </div>
    </Section>
  );
}

function TaskBoard({ tasks, onCreate, onPatch, onPreview }) {
  const [draft, setDraft] = useState({ title: "", owner: "", due_date: "", priority: "medium" });
  const columns = [
    ["not_started", "待开始"],
    ["in_progress", "进行中"],
    ["blocked", "待协调"],
    ["completed", "已完成"],
    ["delayed", "已延期"]
  ];
  const submit = async (event) => {
    event.preventDefault();
    if (!draft.title.trim()) return;
    await onCreate({
      ...draft,
      status: "not_started",
      color_status: draft.priority === "high" ? "amber" : "green",
      progress: 0
    });
    setDraft({ title: "", owner: "", due_date: "", priority: "medium" });
  };

  return (
    <div className="view-grid">
      <Section title="新增任务">
        <form className="task-form" onSubmit={submit}>
          <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="任务名称" />
          <input value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} placeholder="责任人" />
          <input type="date" value={draft.due_date} onChange={(e) => setDraft({ ...draft, due_date: e.target.value })} />
          <select value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })}>
            <option value="high">高</option>
            <option value="medium">中</option>
            <option value="low">低</option>
          </select>
          <button className="primary-button" type="submit">
            <Plus size={16} />
            新增
          </button>
        </form>
      </Section>
      <div className="board">
        {columns.map(([status, label]) => (
          <div className="board-column" key={status}>
            <div className="board-head">{label}</div>
            {tasks.filter((task) => task.status === status).map((task) => (
              <div className="task-card" key={task.id}>
                <div className="task-card-top">
                  <strong>{task.title}</strong>
                  <StatusPill color={task.color_status} value={task.color_status} />
                </div>
                <p>{task.description || task.source || ""}</p>
                <div className="task-meta">
                  <span>{task.owner || "-"}</span>
                  <span>{formatDate(task.due_date)}</span>
                </div>
                {task.document_name && (
                  <div className="source-line">
                    <FileButton documentId={task.source_document_id} name={task.document_name} onPreview={onPreview} />
                  </div>
                )}
                <div className="task-actions">
                  <select value={task.status} onChange={(e) => onPatch(task.id, { status: e.target.value })}>
                    {columns.map(([value, text]) => <option value={value} key={value}>{text}</option>)}
                  </select>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={task.progress}
                    onChange={(e) => onPatch(task.id, { progress: Number(e.target.value) })}
                  />
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MilestoneView({ milestones, profile, goals, breakdown, onPreview }) {
  const plannedMilestones = milestones.filter((item) => refinedMilestoneTitles.has(item.title));
  return (
    <Section title="关键里程碑">
      <div className="milestone-summary">
        <div>
          <strong>{plannedMilestones.length}</strong>
          <span>新版计划里程碑</span>
        </div>
        <div>
          <strong>{plannedMilestones.filter((item) => item.status === "completed").length}</strong>
          <span>已完成</span>
        </div>
        <div>
          <strong>{plannedMilestones.filter((item) => item.status === "in_progress").length}</strong>
          <span>进行中</span>
        </div>
        <div>
          <strong>{plannedMilestones.filter((item) => item.status === "planned").length}</strong>
          <span>计划中</span>
        </div>
      </div>
      <div className="timeline">
        {plannedMilestones.map((item) => (
          <div className="timeline-item" key={item.id}>
            <div className={`timeline-dot ${item.color_status || "green"}`} />
            <div>
              <div className="timeline-title">
                <strong>{item.title}</strong>
                <StatusPill value={item.status} color={item.color_status} />
              </div>
              <p>{item.description}</p>
              {item.document_name && (
                <p><FileButton documentId={item.source_document_id} name={item.document_name} onPreview={onPreview} /></p>
              )}
              <span>{formatDate(item.actual_date || item.planned_date)}</span>
            </div>
          </div>
        ))}
        {!plannedMilestones.length && <Empty />}
      </div>
    </Section>
  );
}

function MeetingView({ meetings, onPreview }) {
  return (
    <Section title="会议纪要">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>会议</th>
              <th>时间</th>
              <th>参会人员</th>
              <th>决议事项</th>
              <th>来源文件</th>
            </tr>
          </thead>
          <tbody>
            {meetings.map((item) => (
              <tr key={item.id}>
                <td>{item.title}</td>
                <td>{formatDate(item.meeting_time)}</td>
                <td>{item.participants || "-"}</td>
                <td className="wide-cell">{item.decisions || item.summary || "-"}</td>
                <td><FileButton documentId={item.document_id} name={item.document_name} onPreview={onPreview} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!meetings.length && <Empty />}
      </div>
    </Section>
  );
}

function ChangesView({ changes, onPreview }) {
  return (
    <Section title="变更需求">
      <div className="list">
        {changes.map((item) => (
          <div className="list-row" key={item.id}>
            <div>
              <strong>{item.title}</strong>
              <p>{item.description}</p>
              <small>影响范围：{item.impact || "待确认"}</small>
              {item.document_name && (
                <div className="source-line">
                  <FileButton documentId={item.source_document_id} name={item.document_name} onPreview={onPreview} />
                </div>
              )}
            </div>
            <StatusPill value={item.status} color={item.status === "pending" ? "amber" : "green"} />
          </div>
        ))}
        {!changes.length && <Empty />}
      </div>
    </Section>
  );
}

function SuggestionsView({ suggestions, onApply, onDismiss, onPreview }) {
  return (
    <Section title="待确认智能建议">
      <div className="suggestion-list">
        {suggestions.map((item) => (
          <div className="suggestion" key={item.id}>
            <div className="suggestion-main">
              <div className="suggestion-title">
                <StatusPill value={typeText[item.suggestion_type]} color="blue" />
                <strong>{item.title}</strong>
              </div>
              <p>{item.description}</p>
              <small>
                <FileButton documentId={item.document_id} name={item.document_name || "无来源文件"} onPreview={onPreview} />
                <span> · 置信度 {Math.round((item.confidence || 0) * 100)}%</span>
              </small>
            </div>
            <div className="suggestion-actions">
              <button className="primary-button" onClick={() => onApply(item.id)}>
                <Check size={16} />
                应用
              </button>
              <button className="ghost-button" onClick={() => onDismiss(item.id)}>
                <X size={16} />
                忽略
              </button>
            </div>
          </div>
        ))}
        {!suggestions.length && <Empty />}
      </div>
    </Section>
  );
}

function DocumentsView({ documents, onPreview }) {
  return (
    <Section title="资料台账">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>文件名</th>
              <th>类型</th>
              <th>状态</th>
              <th>修改时间</th>
              <th>路径</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id}>
                <td><FileButton documentId={doc.id} name={doc.name} onPreview={onPreview} /></td>
                <td>{typeText[doc.doc_category] || doc.doc_category}</td>
                <td><StatusPill value={doc.status} color={doc.status === "error" ? "red" : "green"} /></td>
                <td>{formatDate(doc.modified_at)}</td>
                <td className="path-cell">
                  <FileButton documentId={doc.id} name={doc.path} onPreview={onPreview} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!documents.length && <Empty />}
      </div>
    </Section>
  );
}

function WeeklyView({ weekly, onPreview }) {
  const latest = weekly.latest_weekly || {};
  const profile = weekly.profile || {};
  return (
    <div className="view-grid">
      <Section
        title="周报汇总"
        action={
          <a className="primary-button" href="/api/weekly-summary/export">
            <Download size={16} />
            导出 Word
          </a>
        }
      >
        <div className="summary-head">
          <div>
            <strong>{profile.name || "协同办公平台（OA）集成项目"}</strong>
            <p>
              <FileButton documentId={latest.document_id} name={latest.document_name || "暂无周报文件"} onPreview={onPreview} />
              <span> · {formatDate(latest.period_start)} 至 {formatDate(latest.period_end)}</span>
            </p>
          </div>
          <StatusPill value={profile.phase || "开发实施"} color="blue" />
        </div>
      </Section>
      <div className="two-col">
        <Section title="本周工作进展">
          <pre className="text-block">{latest.progress_text || "暂无"}</pre>
        </Section>
        <Section title="下周工作计划">
          <pre className="text-block">{latest.next_plan_text || "暂无"}</pre>
        </Section>
      </div>
      <div className="two-col">
        <Section title="需协调事项">
          <pre className="text-block">{latest.coordination_text || "暂无"}</pre>
        </Section>
        <Section title="风险问题">
          <div className="list">
            {(weekly.open_risks || []).map((risk) => (
              <div className="list-row" key={risk.id}>
                <div>
                  <strong>{risk.title}</strong>
                  <p>{risk.description}</p>
                </div>
                <StatusPill value={risk.level} color={risk.level === "high" ? "red" : "amber"} />
              </div>
            ))}
            {!(weekly.open_risks || []).length && <pre className="text-block">{latest.risk_text || "暂无"}</pre>}
          </div>
        </Section>
      </div>
    </div>
  );
}

function SettingsView({ settings, setSettings, onSave, onScan }) {
  const updateType = (key, patch) => {
    setSettings({
      ...settings,
      monitorTypes: {
        ...settings.monitorTypes,
        [key]: { ...settings.monitorTypes[key], ...patch }
      }
    });
  };

  return (
    <div className="view-grid">
      <Section
        title="系统设置"
        action={
          <div className="button-row">
            <button className="ghost-button" onClick={onScan}>
              <RefreshCw size={16} />
              扫描
            </button>
            <button className="primary-button" onClick={onSave}>
              <Check size={16} />
              保存
            </button>
          </div>
        }
      >
        <div className="settings-grid">
          <label>
            系统名称
            <input value={settings.systemName || ""} onChange={(e) => setSettings({ ...settings, systemName: e.target.value })} />
          </label>
          <label>
            默认监控目录
            <input
              value={settings.defaultMonitorDir || ""}
              onChange={(e) => setSettings({ ...settings, defaultMonitorDir: e.target.value })}
            />
          </label>
        </div>
      </Section>

      <Section title="分类监控目录">
        <div className="monitor-grid">
          {Object.entries(settings.monitorTypes || {}).map(([key, config]) => (
            <div className="monitor-item" key={key}>
              <div className="monitor-head">
                <strong>{config.label || typeText[key] || key}</strong>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={config.enabled !== false}
                    onChange={(e) => updateType(key, { enabled: e.target.checked })}
                  />
                  <span />
                </label>
              </div>
              <label>
                目录
                <textarea
                  value={(config.directories || []).join("\n")}
                  onChange={(e) => updateType(key, { directories: e.target.value.split(/\n+/).map((v) => v.trim()).filter(Boolean) })}
                  rows={3}
                />
              </label>
              <label>
                关键词
                <input
                  value={(config.patterns || []).join("、")}
                  onChange={(e) => updateType(key, { patterns: e.target.value.split(/[、,，\s]+/).map((v) => v.trim()).filter(Boolean) })}
                />
              </label>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}

function App() {
  const [active, setActive] = useState("dashboard");
  const [dashboardData, setDashboardData] = useState({});
  const [tasks, setTasks] = useState([]);
  const [milestones, setMilestones] = useState([]);
  const [meetings, setMeetings] = useState([]);
  const [changes, setChanges] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [weekly, setWeekly] = useState({});
  const [settings, setSettings] = useState({ monitorTypes: {} });
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    setError("");
    const [dashboard, taskList, milestoneList, meetingList, changeList, documentList, suggestionList, weeklyData, settingData] =
      await Promise.all([
        api.get("/api/dashboard"),
        api.get("/api/tasks"),
        api.get("/api/milestones"),
        api.get("/api/meetings"),
        api.get("/api/changes"),
        api.get("/api/documents"),
        api.get("/api/suggestions"),
        api.get("/api/weekly-summary"),
        api.get("/api/settings")
      ]);
    setDashboardData(dashboard);
    setTasks(taskList);
    setMilestones(milestoneList);
    setMeetings(meetingList);
    setChanges(changeList);
    setDocuments(documentList);
    setSuggestions(suggestionList);
    setWeekly(weeklyData);
    setSettings(settingData);
  };

  useEffect(() => {
    refresh()
      .catch((err) => setError(err.message || "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  const runAction = async (action, success) => {
    try {
      setNotice("");
      setError("");
      await action();
      await refresh();
      setNotice(success);
    } catch (err) {
      setError(err.message || "操作失败");
    }
  };

  const scanNow = () => runAction(() => api.post("/api/scan"), "扫描完成");
  const saveSettings = () => runAction(() => api.put("/api/settings", settings), "设置已保存");
  const applySuggestion = (id) => runAction(() => api.post(`/api/suggestions/${id}/apply`), "建议已应用");
  const dismissSuggestion = (id) => runAction(() => api.post(`/api/suggestions/${id}/dismiss`), "建议已忽略");
  const createTask = (payload) => runAction(() => api.post("/api/tasks", payload), "任务已新增");
  const patchTask = (id, values) => runAction(() => api.patch(`/api/tasks/${id}`, { values }), "任务已更新");
  const openPreview = async (documentId) => {
    if (!documentId) return;
    try {
      setPreviewLoading(true);
      setPreview(null);
      setError("");
      const data = await api.get(`/api/documents/${documentId}/preview`);
      setPreview(data);
    } catch (err) {
      setError(err.message || "文件预览失败");
    } finally {
      setPreviewLoading(false);
    }
  };

  const content = () => {
    if (loading) return <div className="loading">加载中</div>;
    if (active === "dashboard") return <Dashboard data={dashboardData} onScan={scanNow} onPreview={openPreview} />;
    if (active === "gantt") return <Gantt tasks={tasks} />;
    if (active === "board") return <TaskBoard tasks={tasks} onCreate={createTask} onPatch={patchTask} onPreview={openPreview} />;
    if (active === "milestones") {
      return (
        <MilestoneView
          milestones={milestones}
          profile={dashboardData.profile}
          goals={dashboardData.projectGoals || []}
          breakdown={dashboardData.projectPlanBreakdown || []}
          onPreview={openPreview}
        />
      );
    }
    if (active === "meetings") return <MeetingView meetings={meetings} onPreview={openPreview} />;
    if (active === "changes") return <ChangesView changes={changes} onPreview={openPreview} />;
    if (active === "suggestions") {
      return <SuggestionsView suggestions={suggestions} onApply={applySuggestion} onDismiss={dismissSuggestion} onPreview={openPreview} />;
    }
    if (active === "documents") return <DocumentsView documents={documents} onPreview={openPreview} />;
    if (active === "weekly") return <WeeklyView weekly={weekly} onPreview={openPreview} />;
    if (active === "settings") return <SettingsView settings={settings} setSettings={setSettings} onSave={saveSettings} onScan={scanNow} />;
    return null;
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Gauge size={24} />
          <div>
            <strong>国产化OA</strong>
            <span>项目管理系统</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button className={active === item.id ? "active" : ""} key={item.id} onClick={() => setActive(item.id)}>
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <div className="eyebrow">国产化OA集成项目管理系统</div>
            <h1>{navItems.find((item) => item.id === active)?.label || "项目管理"}</h1>
          </div>
          <div className="top-actions">
            {notice && <span className="notice">{notice}</span>}
            {error && <span className="error">{error}</span>}
            <button className="icon-button" onClick={() => runAction(refresh, "已刷新")} title="刷新">
              <RefreshCw size={18} />
            </button>
          </div>
        </header>
        {content()}
      </main>
      <PreviewModal preview={preview} loading={previewLoading} onClose={() => setPreview(null)} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
