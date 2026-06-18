import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BookOpen,
  CalendarDays,
  Check,
  Circle,
  ClipboardCheck,
  Download,
  FileText,
  Flag,
  FolderCog,
  Gauge,
  GitPullRequest,
  GripVertical,
  Kanban,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Lock,
  LogOut,
  MessageSquareText,
  Milestone,
  Plus,
  RefreshCw,
  RotateCcw,
  Settings,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import "./styles.css";

const api = {
  async error(response) {
    const text = await response.text();
    try {
      const data = JSON.parse(text);
      return data.detail || text;
    } catch {
      return text;
    }
  },
  async get(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error(await this.error(response));
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw new Error(await this.error(response));
    return response.json();
  },
  async put(path, body) {
    const response = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await this.error(response));
    return response.json();
  },
  async patch(path, body) {
    const response = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await this.error(response));
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
  { id: "deliverables", label: "交付物清单", icon: ClipboardCheck },
  { id: "authority", label: "文档权威", icon: ShieldCheck, adminOnly: true },
  { id: "wiki", label: "项目 Wiki", icon: BookOpen },
  { id: "qa", label: "智能问答", icon: MessageSquareText },
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
  dismissed: "已忽略",
  draft: "草稿",
  review: "评审中",
  finalized: "已定稿",
  submitted: "已提交"
};

const ocrStatusText = {
  not_required: "不需要",
  pending: "待OCR",
  processing: "OCR中",
  completed: "已OCR",
  failed: "OCR失败",
  partial: "部分完成"
};

const authorityLabels = {
  1: "合同正文",
  2: "合同附件/招投标/投标响应",
  3: "正式变更/确认文件",
  4: "会议纪要/决议",
  5: "周报/过程资料"
};

const authorityStatusText = {
  confirmed: "已确认",
  pending: "待确认",
  manual: "手动调整"
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

function LoginModal({ open, onClose, onLogin }) {
  const [draft, setDraft] = useState({ username: "admin", password: "" });
  const [error, setError] = useState("");
  if (!open) return null;

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    const ok = await onLogin(draft);
    if (!ok) {
      setError("账号或密码不正确");
      return;
    }
    setDraft({ username: "admin", password: "" });
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <form className="modal auth-modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <h2>管理员登录</h2>
            <p>登录后可进入系统设置</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" title="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="auth-form">
          <label>
            账号
            <input value={draft.username} onChange={(e) => setDraft({ ...draft, username: e.target.value })} autoFocus />
          </label>
          <label>
            密码
            <input
              value={draft.password}
              onChange={(e) => setDraft({ ...draft, password: e.target.value })}
              type="password"
              autoComplete="current-password"
            />
          </label>
          {error && <span className="error">{error}</span>}
          <button className="primary-button" type="submit">
            <Lock size={16} />
            登录
          </button>
        </div>
      </form>
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

const defaultIntelligentAnalysis = {
  enabled: false,
  provider: "openai_compatible",
  apiBaseUrl: "",
  apiKey: "",
  modelName: "",
  embeddingApiBaseUrl: "",
  embeddingApiKey: "",
  embeddingModelName: "",
  embeddingTextField: "text",
  embeddingModelField: "model",
  embeddingVectorPath: "embedding",
  rerankerEnabled: false,
  rerankerApiBaseUrl: "",
  rerankerApiKey: "",
  rerankerModelName: "",
  rerankerScorePath: "score",
  analysisMode: "auto_suggest",
  autoApplyLowRisk: true,
  timeoutSeconds: 60,
  maxTextLength: 12000,
  reviewOnly: true,
  allowExternalService: false
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

function DashboardWorkspace({ widgets, renderWidget, resetSignal }) {
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
  };

  useEffect(() => {
    if (resetSignal) resetLayout();
  }, [resetSignal]);

  return (
    <div className="dashboard-workspace">
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

function Dashboard({ data, onScan, onPreview, resetLayoutSignal, scanLoading }) {
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
        <button className="icon-button" onClick={onScan} title="重新扫描" disabled={scanLoading}>
          {scanLoading ? <span className="button-spinner" /> : <RefreshCw size={17} />}
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
        <Stat label="交付物" value={`${data.counts?.submittedDeliverables || 0}/${data.counts?.deliverables || 0}`} icon={ClipboardCheck} tone="green" />
        <Stat label="资料文件" value={data.counts?.documents || 0} icon={FolderCog} tone="neutral" />
      </div>

      <DashboardWorkspace widgets={dashboardWidgets} renderWidget={renderDashboardWidget} resetSignal={resetLayoutSignal} />
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

function SuggestionsView({ suggestions, onApply, onApplyAll, onDismiss, onPreview, actionStates = {} }) {
  return (
    <Section
      title="待确认智能建议"
      action={
        <button className="primary-button" onClick={onApplyAll} disabled={!suggestions.length || actionStates["apply-all-suggestions"]}>
          {actionStates["apply-all-suggestions"] ? <span className="button-spinner" /> : <Check size={16} />}
          {actionStates["apply-all-suggestions"] ? "应用中" : "全部应用"}
        </button>
      }
    >
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
              <button className="primary-button" onClick={() => onApply(item.id)} disabled={actionStates[`apply-suggestion-${item.id}`]}>
                {actionStates[`apply-suggestion-${item.id}`] ? <span className="button-spinner" /> : <Check size={16} />}
                {actionStates[`apply-suggestion-${item.id}`] ? "应用中" : "应用"}
              </button>
              <button className="ghost-button" onClick={() => onDismiss(item.id)} disabled={actionStates[`dismiss-suggestion-${item.id}`]}>
                {actionStates[`dismiss-suggestion-${item.id}`] ? <span className="button-spinner" /> : <X size={16} />}
                {actionStates[`dismiss-suggestion-${item.id}`] ? "处理中" : "忽略"}
              </button>
            </div>
          </div>
        ))}
        {!suggestions.length && <Empty />}
      </div>
    </Section>
  );
}

function DocumentsView({ documents, onPreview, isAdmin, onStartOcr, onRetryOcr, actionStates = {} }) {
  return (
    <Section title="资料台账">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>文件名</th>
              <th>类型</th>
              <th>状态</th>
              <th>OCR</th>
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
                <td>
                  <div className="ocr-cell">
                    <span>{ocrStatusText[doc.ocr_status] || doc.ocr_status || "-"}</span>
                    {!!doc.ocr_pages_total && <small>{doc.ocr_pages_done || 0}/{doc.ocr_pages_total} · {doc.ocr_progress || 0}%</small>}
                    {doc.ocr_error && <small className="error">{doc.ocr_error}</small>}
                    {isAdmin && doc.extension === ".pdf" && (
                      <div className="button-row compact-buttons">
                        <button className="ghost-button" onClick={() => onStartOcr(doc.id)} disabled={actionStates[`ocr-start-${doc.id}`]}>
                          {actionStates[`ocr-start-${doc.id}`] ? <span className="button-spinner" /> : null}
                          {actionStates[`ocr-start-${doc.id}`] ? "启动中" : "OCR"}
                        </button>
                        {["failed", "partial"].includes(doc.ocr_status) && (
                          <button className="ghost-button" onClick={() => onRetryOcr(doc.id)} disabled={actionStates[`ocr-retry-${doc.id}`]}>
                            {actionStates[`ocr-retry-${doc.id}`] ? <span className="button-spinner" /> : null}
                            {actionStates[`ocr-retry-${doc.id}`] ? "重试中" : "重试"}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </td>
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

const deliverableStatuses = [
  ["not_started", "未开始", "neutral"],
  ["draft", "草稿", "blue"],
  ["review", "评审中", "amber"],
  ["finalized", "已定稿", "green"],
  ["submitted", "已提交", "green"]
];

function DeliverablesView({ deliverables, documents, onPatch, onPreview }) {
  const total = deliverables.length;
  const submitted = deliverables.filter((item) => item.status === "submitted").length;
  const ready = deliverables.filter((item) => ["finalized", "submitted"].includes(item.status)).length;
  const linked = deliverables.filter((item) => item.document_id).length;

  return (
    <div className="view-grid">
      <div className="stats-grid deliverable-stats">
        <Stat label="交付物总数" value={total} icon={ClipboardCheck} tone="blue" />
        <Stat label="已提交" value={submitted} icon={Check} tone="green" />
        <Stat label="已定稿/已提交" value={ready} icon={Flag} tone="amber" />
        <Stat label="已关联文件" value={linked} icon={FolderCog} tone="neutral" />
      </div>
      <Section title="交付物台账">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>交付物</th>
                <th>依据</th>
                <th>状态</th>
                <th>责任人</th>
                <th>计划日期</th>
                <th>提交日期</th>
                <th>关联文件</th>
              </tr>
            </thead>
            <tbody>
              {deliverables.map((item) => (
                <tr key={item.id}>
                  <td className="wide-cell">
                    <strong>{item.name}</strong>
                    <p>{item.description}</p>
                  </td>
                  <td>{item.requirement_source || "-"}</td>
                  <td>
                    <select value={item.status} onChange={(e) => onPatch(item.id, { status: e.target.value })}>
                      {deliverableStatuses.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
                    </select>
                  </td>
                  <td>
                    <input value={item.owner || ""} onChange={(e) => onPatch(item.id, { owner: e.target.value })} />
                  </td>
                  <td>
                    <input type="date" value={item.planned_date || ""} onChange={(e) => onPatch(item.id, { planned_date: e.target.value })} />
                  </td>
                  <td>
                    <input type="date" value={item.submitted_date || ""} onChange={(e) => onPatch(item.id, { submitted_date: e.target.value })} />
                  </td>
                  <td className="deliverable-file-cell">
                    <select
                      value={item.document_id || ""}
                      onChange={(e) => onPatch(item.id, { document_id: e.target.value ? Number(e.target.value) : null })}
                    >
                      <option value="">未关联</option>
                      {documents.map((doc) => <option value={doc.id} key={doc.id}>{doc.name}</option>)}
                    </select>
                    {item.document_name && (
                      <FileButton documentId={item.document_id} name={item.document_name} onPreview={onPreview} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!deliverables.length && <Empty />}
        </div>
      </Section>
    </div>
  );
}

function AuthorityView({
  authorityDocs,
  authoritySuggestions,
  analyzeStatus,
  onAnalyze,
  onPatch,
  onApplySuggestion,
  onDismissSuggestion,
  onPreview,
  actionStates = {}
}) {
  const running = !!analyzeStatus?.running || !!actionStates["analyze-authority"];
  const pending = authoritySuggestions.length;
  const confirmed = authorityDocs.filter((doc) => ["confirmed", "manual"].includes(doc.authority_status)).length;
  const high = authorityDocs.filter((doc) => Number(doc.authority_level || 5) <= 2).length;
  const indexed = authorityDocs.filter((doc) => Number(doc.chunk_count || 0) > 0).length;

  return (
    <div className="view-grid">
      <div className="stats-grid deliverable-stats">
        <Stat label="权威已确认" value={confirmed} icon={ShieldCheck} tone="green" />
        <Stat label="高权威资料" value={high} icon={Flag} tone="blue" />
        <Stat label="待确认建议" value={pending} icon={AlertTriangle} tone="amber" />
        <Stat label="已入知识库" value={indexed} icon={BookOpen} tone="neutral" />
      </div>
      <Section
        title="文档权威分析"
        action={
          <button className="ghost-button" onClick={onAnalyze} disabled={running}>
            {running ? <span className="button-spinner" /> : <RefreshCw size={16} />}
            {running ? "分析中" : "重新分析"}
          </button>
        }
      >
        {running ? (
          <div className="wiki-job-banner">
            <span className="qa-spinner" />
            <div>
              <strong>正在后台分析文档权威层级</strong>
              <p>
                进度 {analyzeStatus.progress || 0}/{analyzeStatus.total || 0}，
                自动确认 {analyzeStatus.autoApplied || 0} 个，
                生成建议 {analyzeStatus.suggested || 0} 个。
              </p>
            </div>
          </div>
        ) : (
          <p className="muted-text">系统会优先按文件名、目录、资料分类和正文特征判断权威层级。高置信自动生效，模糊资料进入待确认建议。</p>
        )}
        {!!analyzeStatus?.error && <div className="error">权威分析失败：{analyzeStatus.error}</div>}
      </Section>

      <Section title="待确认权威建议">
        <div className="suggestion-list">
          {authoritySuggestions.map((item) => (
            <div className="suggestion" key={item.id}>
              <div className="suggestion-main">
                <div className="suggestion-title">
                  <StatusPill value={item.authority_label} color="blue" />
                  <strong>{item.document_name}</strong>
                </div>
                <p>{item.authority_reason}</p>
                <small>
                  适用范围：{item.authority_scope || "-"} · 置信度 {Math.round((item.confidence || 0) * 100)}%
                </small>
                <small>
                  <FileButton documentId={item.document_id} name={item.document_path} onPreview={onPreview} />
                </small>
              </div>
              <div className="suggestion-actions">
                <button className="primary-button" onClick={() => onApplySuggestion(item.id)} disabled={actionStates[`apply-authority-${item.id}`]}>
                  {actionStates[`apply-authority-${item.id}`] ? <span className="button-spinner" /> : <Check size={16} />}
                  {actionStates[`apply-authority-${item.id}`] ? "应用中" : "应用"}
                </button>
                <button className="ghost-button" onClick={() => onDismissSuggestion(item.id)} disabled={actionStates[`dismiss-authority-${item.id}`]}>
                  {actionStates[`dismiss-authority-${item.id}`] ? <span className="button-spinner" /> : <X size={16} />}
                  {actionStates[`dismiss-authority-${item.id}`] ? "处理中" : "忽略"}
                </button>
              </div>
            </div>
          ))}
          {!authoritySuggestions.length && <Empty text="暂无待确认权威建议" />}
        </div>
      </Section>

      <Section title="文档权威台账">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>文件</th>
                <th>权威等级</th>
                <th>权威分</th>
                <th>当前有效</th>
                <th>适用范围</th>
                <th>版本/日期</th>
                <th>状态</th>
                <th>判断依据与备注</th>
              </tr>
            </thead>
            <tbody>
              {authorityDocs.map((doc) => (
                <tr key={doc.id}>
                  <td className="wide-cell">
                    <FileButton documentId={doc.id} name={doc.name} onPreview={onPreview} />
                    <p>{typeText[doc.doc_category] || doc.doc_category} · {doc.chunk_count || 0} 个片段</p>
                  </td>
                  <td>
                    <select
                      value={doc.authority_level || 5}
                      onChange={(e) => onPatch(doc.id, { authority_level: Number(e.target.value) })}
                    >
                      {Object.entries(authorityLabels).map(([value, label]) => (
                        <option value={value} key={value}>{value}. {label}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      value={Math.round(doc.authority_score || 0)}
                      onChange={(e) => onPatch(doc.id, { authority_score: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={!!doc.is_current}
                        onChange={(e) => onPatch(doc.id, { is_current: e.target.checked })}
                      />
                      有效
                    </label>
                  </td>
                  <td>
                    <textarea
                      rows={2}
                      value={doc.authority_scope || ""}
                      onChange={(e) => onPatch(doc.id, { authority_scope: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      value={doc.version_label || ""}
                      placeholder="版本"
                      onChange={(e) => onPatch(doc.id, { version_label: e.target.value })}
                    />
                    <input
                      type="date"
                      value={formatDate(doc.effective_date) === "-" ? "" : formatDate(doc.effective_date)}
                      onChange={(e) => onPatch(doc.id, { effective_date: e.target.value })}
                    />
                  </td>
                  <td><StatusPill value={authorityStatusText[doc.authority_status] || doc.authority_status} color={doc.authority_status === "pending" ? "amber" : "green"} /></td>
                  <td className="wide-cell">
                    <p>{doc.authority_reason || "-"}</p>
                    <textarea
                      rows={2}
                      value={doc.authority_note || ""}
                      placeholder="管理员备注"
                      onChange={(e) => onPatch(doc.id, { authority_note: e.target.value })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!authorityDocs.length && <Empty />}
        </div>
      </Section>
    </div>
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

function WikiView({ pages, suggestions, isAdmin, onRebuild, onApply, onApplyAll, rebuildStatus, actionStates = {}, onPreview }) {
  const running = !!rebuildStatus?.running || !!actionStates["rebuild-wiki"];
  const renderSources = (sources = []) => (
    <div className="wiki-source-list">
      {sources.slice(0, 5).map((source, index) => (
        <div className="wiki-source-item" key={`${source.documentId || source.documentName || "baseline"}-${index}`}>
          <div className="source-title-line">
            <StatusPill value={source.authorityLabel || source.sourceType || "来源"} color={source.isPrimaryBasis ? "green" : "blue"} />
            {source.isPrimaryBasis && <span className="mini-badge">主依据</span>}
          </div>
          {source.documentId ? (
            <FileButton documentId={source.documentId} name={source.documentName} onPreview={onPreview} />
          ) : (
            <strong>{source.documentName || "项目计划基线"}</strong>
          )}
          {source.snippet && <p>{source.snippet}</p>}
        </div>
      ))}
      {!sources.length && <p className="muted-text">暂无来源记录</p>}
    </div>
  );
  return (
    <div className="view-grid">
      <Section
        title="项目 Wiki"
        action={
          isAdmin && (
            <button className="ghost-button" onClick={onRebuild} disabled={running}>
              {running ? <span className="button-spinner" /> : <RefreshCw size={16} />}
              {running ? "生成中" : "生成更新建议"}
            </button>
          )
        }
      >
        {running && (
          <div className="wiki-job-banner">
            <span className="qa-spinner" />
            <div>
              <strong>Wiki 更新建议正在后台生成</strong>
              <p>进度 {rebuildStatus.progress || 0}/{rebuildStatus.total || 9}，可切换页面或关闭浏览器，不影响后台任务。</p>
            </div>
          </div>
        )}
        <div className="wiki-grid">
          {pages.map((page) => (
            <article className="wiki-card" key={page.page_key}>
              <div className="wiki-card-head">
                <strong>{page.title}</strong>
                <span>{formatDate(page.updated_at)}</span>
              </div>
              {renderSources(page.sources || [])}
              <pre className="wiki-content">{page.content || "暂无内容，生成并确认 Wiki 更新建议后显示。"}</pre>
            </article>
          ))}
          {!pages.length && <Empty />}
        </div>
      </Section>
      {isAdmin && (
        <Section
          title="Wiki 更新建议"
          action={
            <button className="primary-button" onClick={onApplyAll} disabled={!suggestions.length || actionStates["apply-all-wiki"]}>
              {actionStates["apply-all-wiki"] ? <span className="button-spinner" /> : <Check size={16} />}
              {actionStates["apply-all-wiki"] ? "应用中" : "全部应用"}
            </button>
          }
        >
          <div className="suggestion-list">
            {suggestions.map((item) => (
              <div className="suggestion" key={item.id}>
                <div className="suggestion-main">
                  <div className="suggestion-title">
                    <StatusPill value="pending" color="blue" />
                    <strong>{item.title}</strong>
                  </div>
                  <pre className="wiki-suggestion-content">{item.content}</pre>
                  {renderSources(item.sources || [])}
                </div>
                <div className="suggestion-actions">
                  <button className="primary-button" onClick={() => onApply(item.id)} disabled={actionStates[`apply-wiki-${item.id}`]}>
                    {actionStates[`apply-wiki-${item.id}`] ? <span className="button-spinner" /> : <Check size={16} />}
                    {actionStates[`apply-wiki-${item.id}`] ? "应用中" : "应用"}
                  </button>
                </div>
              </div>
            ))}
            {!suggestions.length && <Empty text="暂无待确认 Wiki 建议" />}
          </div>
        </Section>
      )}
    </div>
  );
}

function StructuredAnswer({ answer, onPreview }) {
  const structured = answer.structured || {};
  return (
    <Section title="回答">
      <div className="qa-answer-card">
        <h3>结论</h3>
        <p>{structured.answer_summary || answer.answer}</p>
        {!!(structured.key_points || []).length && (
          <>
            <h3>要点</h3>
            <ul>{structured.key_points.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </>
        )}
        {!!(structured.evidence || []).length && (
          <>
            <h3>依据</h3>
            <ul>{structured.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </>
        )}
        {!!(structured.unknowns || []).length && (
          <>
            <h3>待确认</h3>
            <ul>{structured.unknowns.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </>
        )}
      </div>
      <div className="source-list">
        {(answer.sources || []).map((source, index) => (
          <div className="source-card" key={`${source.documentId || source.documentName || "wiki"}-${index}`}>
            <div className="source-card-head">
              <div className="source-title-line">
                <strong>来源 {index + 1}</strong>
                {source.authorityLabel && <StatusPill value={source.authorityLabel} color={source.isPrimaryBasis ? "green" : "blue"} />}
                {source.isPrimaryBasis && <span className="mini-badge">主依据</span>}
              </div>
              {source.documentId ? (
                <FileButton documentId={source.documentId} name={source.documentName} onPreview={onPreview} />
              ) : (
                <span>{source.documentName || "项目 Wiki"}</span>
              )}
            </div>
            <p>{source.snippet}</p>
            {source.conflictNote && <p className="conflict-note">{source.conflictNote}</p>}
          </div>
        ))}
        {!(answer.sources || []).length && <Empty text="暂无来源" />}
      </div>
    </Section>
  );
}

function QAView({ onPreview }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const ask = async (event) => {
    event.preventDefault();
    if (!question.trim()) return;
    try {
      setLoading(true);
      setError("");
      setAnswer(await api.post("/api/qa/ask", { question }));
    } catch (err) {
      setError(err.message || "问答失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="view-grid">
      <Section title="智能问答">
        <form className="qa-form" onSubmit={ask}>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={3}
            placeholder="请输入与项目资料相关的问题"
          />
          <button className="primary-button" type="submit" disabled={loading}>
            {loading ? <span className="button-spinner" /> : <MessageSquareText size={16} />}
            {loading ? "查询中" : "提问"}
          </button>
        </form>
      </Section>
      {loading && (
        <div className="qa-loading">
          <span className="qa-spinner" />
          <div>
            <strong>正在检索项目资料并生成回答</strong>
            <p>已进入知识库召回、来源筛选和模型生成流程，复杂问题可能需要稍等。</p>
          </div>
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {answer && <StructuredAnswer answer={answer} onPreview={onPreview} />}
    </div>
  );
}

function SettingsView({
  settings,
  setSettings,
  onSave,
  onScan,
  onChangePassword,
  onTestModel,
  modelTest,
  onLoadModels,
  modelList,
  onRebuildKnowledge,
  knowledgeStatus,
  knowledgeRebuildStatus,
  actionStates = {}
}) {
  const [passwordDraft, setPasswordDraft] = useState({ currentPassword: "", newPassword: "", confirmPassword: "" });
  const [passwordError, setPasswordError] = useState("");
  const intelligentAnalysis = { ...defaultIntelligentAnalysis, ...(settings.intelligentAnalysis || {}) };
  const knowledgeRunning = !!knowledgeRebuildStatus?.running;
  const updateType = (key, patch) => {
    setSettings({
      ...settings,
      monitorTypes: {
        ...settings.monitorTypes,
        [key]: { ...settings.monitorTypes[key], ...patch }
      }
    });
  };
  const updateAnalysis = (patch) => {
    setSettings({
      ...settings,
      intelligentAnalysis: {
        ...intelligentAnalysis,
        ...patch
      }
    });
  };

  const submitPassword = async (event) => {
    event.preventDefault();
    setPasswordError("");
    if (!passwordDraft.newPassword || passwordDraft.newPassword.length < 4) {
      setPasswordError("新密码至少需要 4 位");
      return;
    }
    if (passwordDraft.newPassword !== passwordDraft.confirmPassword) {
      setPasswordError("两次新密码不一致");
      return;
    }
    const ok = await onChangePassword({
      currentPassword: passwordDraft.currentPassword,
      newPassword: passwordDraft.newPassword
    });
    if (ok) setPasswordDraft({ currentPassword: "", newPassword: "", confirmPassword: "" });
  };

  return (
    <div className="view-grid">
      <Section
        title="系统设置"
        action={
          <div className="button-row">
            <button className="ghost-button" onClick={onScan} disabled={actionStates["scan"]}>
              {actionStates["scan"] ? <span className="button-spinner" /> : <RefreshCw size={16} />}
              {actionStates["scan"] ? "扫描中" : "扫描"}
            </button>
            <button className="primary-button" onClick={onSave} disabled={actionStates["save-settings"]}>
              {actionStates["save-settings"] ? <span className="button-spinner" /> : <Check size={16} />}
              {actionStates["save-settings"] ? "保存中" : "保存"}
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

      <Section title="智能文件分析">
        <div className="section-inline-action">
          <button className="ghost-button" onClick={onTestModel} type="button" disabled={actionStates["test-model"]}>
            {actionStates["test-model"] ? <span className="button-spinner" /> : <Sparkles size={16} />}
            {actionStates["test-model"] ? "测试中" : "测试连接"}
          </button>
          {modelTest && (
            <span className={modelTest.ok ? "notice" : "error"}>
              {modelTest.ok ? `连接正常：${modelTest.modelName || "已自动选择模型"}，${modelTest.elapsedMs}ms` : modelTest.error}
            </span>
          )}
        </div>
        <div className="analysis-settings-grid">
          <div className="monitor-item analysis-switch-card">
            <div className="monitor-head">
              <strong>启用智能分析</strong>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={!!intelligentAnalysis.enabled}
                  onChange={(e) => updateAnalysis({ enabled: e.target.checked })}
                />
                <span />
              </label>
            </div>
            <p>启用后可在后续扫描流程中调用模型，生成待确认的任务、风险、里程碑、交付物更新建议。</p>
          </div>
          <label>
            模型服务类型
            <select value={intelligentAnalysis.provider} onChange={(e) => updateAnalysis({ provider: e.target.value })}>
              <option value="openai_compatible">兼容 OpenAI API</option>
              <option value="local">本地模型服务</option>
              <option value="custom">自定义服务</option>
            </select>
          </label>
          <label>
            API 地址
            <input
              value={intelligentAnalysis.apiBaseUrl || ""}
              onChange={(e) => updateAnalysis({ apiBaseUrl: e.target.value })}
              placeholder="例如 http://localhost:11434/v1 或 https://.../v1"
            />
          </label>
          <label>
            API Key
            <input
              value={intelligentAnalysis.apiKey || ""}
              onChange={(e) => updateAnalysis({ apiKey: e.target.value })}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <label>
            模型名称
            <input
              value={intelligentAnalysis.modelName || ""}
              onChange={(e) => updateAnalysis({ modelName: e.target.value })}
              placeholder="可留空，测试时自动读取 /models 的第一个模型"
            />
          </label>
          <div className="model-picker">
            <button className="ghost-button" onClick={onLoadModels} type="button" disabled={actionStates["load-models"]}>
              {actionStates["load-models"] ? <span className="button-spinner" /> : <RefreshCw size={16} />}
              {actionStates["load-models"] ? "读取中" : "读取模型"}
            </button>
            <select
              value={intelligentAnalysis.modelName || ""}
              onChange={(e) => updateAnalysis({ modelName: e.target.value })}
              disabled={!modelList.length}
            >
              <option value="">{modelList.length ? "自动选择第一个模型" : "尚未读取模型"}</option>
              {modelList.map((model) => (
                <option value={model.id} key={model.id}>{model.id}</option>
              ))}
            </select>
          </div>
          <label>
            超时时间（秒）
            <input
              type="number"
              min="10"
              max="300"
              value={intelligentAnalysis.timeoutSeconds || 60}
              onChange={(e) => updateAnalysis({ timeoutSeconds: Number(e.target.value) })}
            />
          </label>
          <label>
            单次最大文本长度
            <input
              type="number"
              min="1000"
              max="80000"
              step="1000"
              value={intelligentAnalysis.maxTextLength || 12000}
              onChange={(e) => updateAnalysis({ maxTextLength: Number(e.target.value) })}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={!!intelligentAnalysis.reviewOnly}
              onChange={(e) => updateAnalysis({ reviewOnly: e.target.checked })}
            />
            仅生成待确认建议
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={!!intelligentAnalysis.allowExternalService}
              onChange={(e) => updateAnalysis({ allowExternalService: e.target.checked })}
            />
            允许发送内容到外部模型服务
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={!!intelligentAnalysis.autoApplyLowRisk}
              onChange={(e) => updateAnalysis({ autoApplyLowRisk: e.target.checked })}
            />
            自动更新低风险内容
          </label>
        </div>
      </Section>

      <Section title="Embedding 与重排序">
        <div className="analysis-settings-grid">
          <label>
            Embedding API 地址
            <input
              value={intelligentAnalysis.embeddingApiBaseUrl || ""}
              onChange={(e) => updateAnalysis({ embeddingApiBaseUrl: e.target.value })}
              placeholder="本地 embedding 服务地址"
            />
          </label>
          <label>
            Embedding API Key
            <input
              value={intelligentAnalysis.embeddingApiKey || ""}
              onChange={(e) => updateAnalysis({ embeddingApiKey: e.target.value })}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <label>
            Embedding 模型名
            <input
              value={intelligentAnalysis.embeddingModelName || ""}
              onChange={(e) => updateAnalysis({ embeddingModelName: e.target.value })}
            />
          </label>
          <label>
            文本字段
            <input
              value={intelligentAnalysis.embeddingTextField || "text"}
              onChange={(e) => updateAnalysis({ embeddingTextField: e.target.value })}
            />
          </label>
          <label>
            模型字段
            <input
              value={intelligentAnalysis.embeddingModelField || "model"}
              onChange={(e) => updateAnalysis({ embeddingModelField: e.target.value })}
            />
          </label>
          <label>
            向量字段路径
            <input
              value={intelligentAnalysis.embeddingVectorPath || "embedding"}
              onChange={(e) => updateAnalysis({ embeddingVectorPath: e.target.value })}
              placeholder="embedding 或 data.0.embedding"
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={!!intelligentAnalysis.rerankerEnabled}
              onChange={(e) => updateAnalysis({ rerankerEnabled: e.target.checked })}
            />
            启用 Reranker
          </label>
          <label>
            Reranker API 地址
            <input
              value={intelligentAnalysis.rerankerApiBaseUrl || ""}
              onChange={(e) => updateAnalysis({ rerankerApiBaseUrl: e.target.value })}
            />
          </label>
          <label>
            Reranker API Key
            <input
              value={intelligentAnalysis.rerankerApiKey || ""}
              onChange={(e) => updateAnalysis({ rerankerApiKey: e.target.value })}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <label>
            Reranker 模型名
            <input
              value={intelligentAnalysis.rerankerModelName || ""}
              onChange={(e) => updateAnalysis({ rerankerModelName: e.target.value })}
            />
          </label>
          <label>
            分数字段路径
            <input
              value={intelligentAnalysis.rerankerScorePath || "score"}
              onChange={(e) => updateAnalysis({ rerankerScorePath: e.target.value })}
            />
          </label>
        </div>
      </Section>

      <Section
        title="知识库索引"
        action={
          <button className="ghost-button" onClick={onRebuildKnowledge} type="button" disabled={knowledgeRunning || actionStates["rebuild-knowledge"]}>
            {knowledgeRunning || actionStates["rebuild-knowledge"] ? <span className="button-spinner" /> : <RefreshCw size={16} />}
            {knowledgeRunning || actionStates["rebuild-knowledge"] ? "重建中" : "重建索引"}
          </button>
        }
      >
        {knowledgeRunning && (
          <div className="wiki-job-banner">
            <span className="qa-spinner" />
            <div>
              <strong>知识库索引正在后台重建</strong>
              <p>
                进度 {knowledgeRebuildStatus.progress || 0}/{knowledgeRebuildStatus.total || 0}，
                已索引 {knowledgeRebuildStatus.indexedDocuments || 0} 个文档，
                片段 {knowledgeRebuildStatus.chunks || 0}，
                向量 {knowledgeRebuildStatus.vectors || 0}，
                失败 {knowledgeRebuildStatus.failed || 0}。
              </p>
            </div>
          </div>
        )}
        {!!knowledgeRebuildStatus?.error && (
          <div className="error">知识库重建失败：{knowledgeRebuildStatus.error}</div>
        )}
        <div className="knowledge-status-grid">
          <Stat label="已索引文档" value={knowledgeStatus?.indexed_documents || 0} icon={FileText} tone="blue" />
          <Stat label="知识片段" value={knowledgeStatus?.chunks || 0} icon={ListChecks} tone="green" />
          <Stat label="向量数量" value={knowledgeStatus?.vectors || 0} icon={Sparkles} tone="amber" />
          <Stat label="失败文档" value={knowledgeStatus?.failed_documents || 0} icon={AlertTriangle} tone="red" />
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

      <Section title="管理员密码">
        <form className="settings-grid password-form" onSubmit={submitPassword}>
          <label>
            当前密码
            <input
              value={passwordDraft.currentPassword}
              onChange={(e) => setPasswordDraft({ ...passwordDraft, currentPassword: e.target.value })}
              type="password"
              autoComplete="current-password"
            />
          </label>
          <label>
            新密码
            <input
              value={passwordDraft.newPassword}
              onChange={(e) => setPasswordDraft({ ...passwordDraft, newPassword: e.target.value })}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <label>
            确认新密码
            <input
              value={passwordDraft.confirmPassword}
              onChange={(e) => setPasswordDraft({ ...passwordDraft, confirmPassword: e.target.value })}
              type="password"
              autoComplete="new-password"
            />
          </label>
          <div className="password-actions">
            {passwordError && <span className="error">{passwordError}</span>}
            <button className="ghost-button" type="submit" disabled={actionStates["change-password"]}>
              {actionStates["change-password"] ? <span className="button-spinner" /> : <KeyRound size={16} />}
              {actionStates["change-password"] ? "修改中" : "修改密码"}
            </button>
          </div>
        </form>
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
  const [deliverables, setDeliverables] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [wikiPages, setWikiPages] = useState([]);
  const [wikiSuggestions, setWikiSuggestions] = useState([]);
  const [wikiRebuildStatus, setWikiRebuildStatus] = useState({});
  const wikiWasRunningRef = useRef(false);
  const [authorityDocs, setAuthorityDocs] = useState([]);
  const [authoritySuggestions, setAuthoritySuggestions] = useState([]);
  const [authorityAnalyzeStatus, setAuthorityAnalyzeStatus] = useState({});
  const authorityWasRunningRef = useRef(false);
  const [weekly, setWeekly] = useState({});
  const [settings, setSettings] = useState({ monitorTypes: {} });
  const [modelTest, setModelTest] = useState(null);
  const [modelList, setModelList] = useState([]);
  const [knowledgeStatus, setKnowledgeStatus] = useState({});
  const [knowledgeRebuildStatus, setKnowledgeRebuildStatus] = useState({});
  const knowledgeWasRunningRef = useRef(false);
  const [auth, setAuth] = useState({ isAdmin: false, username: "" });
  const [loginOpen, setLoginOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [dashboardResetSignal, setDashboardResetSignal] = useState(0);
  const [actionStates, setActionStates] = useState({});

  const isActionLoading = (key) => !!actionStates[key];
  const setActionLoading = (key, value) => {
    setActionStates((current) => ({ ...current, [key]: value }));
  };

  const refresh = async () => {
    setError("");
    const [dashboard, taskList, milestoneList, meetingList, changeList, documentList, deliverableList, suggestionList, wikiPageList, weeklyData] =
      await Promise.all([
        api.get("/api/dashboard"),
        api.get("/api/tasks"),
        api.get("/api/milestones"),
        api.get("/api/meetings"),
        api.get("/api/changes"),
        api.get("/api/documents"),
        api.get("/api/deliverables"),
        api.get("/api/suggestions"),
        api.get("/api/wiki/pages"),
        api.get("/api/weekly-summary")
      ]);
    setDashboardData(dashboard);
    setTasks(taskList);
    setMilestones(milestoneList);
    setMeetings(meetingList);
    setChanges(changeList);
    setDocuments(documentList);
    setDeliverables(deliverableList);
    setSuggestions(suggestionList);
    setWikiPages(wikiPageList);
    setWeekly(weeklyData);
  };

  const refreshAuth = async () => {
    try {
      setAuth(await api.get("/api/auth/me"));
    } catch {
      setAuth({ isAdmin: false, username: "" });
    }
  };

  const loadSettings = async () => {
    setSettings(await api.get("/api/settings"));
  };

  const loadKnowledgeStatus = async () => {
    setKnowledgeStatus(await api.get("/api/knowledge/status"));
  };

  const loadKnowledgeRebuildStatus = async () => {
    if (auth.isAdmin) setKnowledgeRebuildStatus(await api.get("/api/knowledge/rebuild-status"));
  };

  const loadWikiSuggestions = async () => {
    if (auth.isAdmin) setWikiSuggestions(await api.get("/api/wiki/suggestions"));
  };

  const loadWikiRebuildStatus = async () => {
    if (auth.isAdmin) setWikiRebuildStatus(await api.get("/api/wiki/rebuild-status"));
  };

  const loadAuthority = async () => {
    if (!auth.isAdmin) return;
    const [docs, items, status] = await Promise.all([
      api.get("/api/document-authority"),
      api.get("/api/document-authority/suggestions"),
      api.get("/api/document-authority/analyze-status")
    ]);
    setAuthorityDocs(docs);
    setAuthoritySuggestions(items);
    setAuthorityAnalyzeStatus(status);
  };

  useEffect(() => {
    Promise.all([refresh(), refreshAuth()])
      .catch((err) => setError(err.message || "加载失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!auth.isAdmin) {
      setSettings({ monitorTypes: {} });
      setAuthorityDocs([]);
      setAuthoritySuggestions([]);
      if (active === "settings") setActive("dashboard");
      if (active === "authority") setActive("dashboard");
      return;
    }
    Promise.all([loadSettings(), loadKnowledgeStatus(), loadKnowledgeRebuildStatus(), loadWikiSuggestions(), loadWikiRebuildStatus(), loadAuthority()]).catch((err) => setError(err.message || "系统设置加载失败"));
  }, [auth.isAdmin]);

  useEffect(() => {
    if (!auth.isAdmin) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const status = await api.get("/api/wiki/rebuild-status");
        setWikiRebuildStatus(status);
        if (status.running || wikiWasRunningRef.current) {
          await loadWikiSuggestions();
        }
        wikiWasRunningRef.current = !!status.running;
      } catch {
        // keep the main page calm if a polling request fails once
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [auth.isAdmin]);

  useEffect(() => {
    if (!auth.isAdmin) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const status = await api.get("/api/document-authority/analyze-status");
        setAuthorityAnalyzeStatus(status);
        if (status.running || authorityWasRunningRef.current) {
          const [docs, items] = await Promise.all([
            api.get("/api/document-authority"),
            api.get("/api/document-authority/suggestions")
          ]);
          setAuthorityDocs(docs);
          setAuthoritySuggestions(items);
        }
        authorityWasRunningRef.current = !!status.running;
      } catch {
        // keep polling quiet on transient admin/session/network issues
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [auth.isAdmin]);

  useEffect(() => {
    if (!auth.isAdmin) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const status = await api.get("/api/knowledge/rebuild-status");
        setKnowledgeRebuildStatus(status);
        if (status.running || knowledgeWasRunningRef.current) {
          await loadKnowledgeStatus();
        }
        knowledgeWasRunningRef.current = !!status.running;
      } catch {
        // keep polling quiet on transient admin/session/network issues
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [auth.isAdmin]);

  const runAction = async (key, action, success) => {
    try {
      setActionLoading(key, true);
      setNotice("");
      setError("");
      await action();
      await refresh();
      setNotice(success);
      return true;
    } catch (err) {
      setError(err.message || "操作失败");
      return false;
    } finally {
      setActionLoading(key, false);
    }
  };

  const scanNow = () => runAction("scan", () => api.post("/api/scan"), "扫描完成");
  const saveSettings = async () => {
    const ok = await runAction("save-settings", () => api.put("/api/settings", settings), "设置已保存");
    if (ok) await loadSettings();
    return ok;
  };
  const loginAdmin = async (payload) => {
    try {
      setNotice("");
      setError("");
      await api.post("/api/auth/login", payload);
      await refreshAuth();
      await loadSettings();
      setLoginOpen(false);
      setNotice("管理员已登录");
      return true;
    } catch (err) {
      setError(err.message || "登录失败");
      return false;
    }
  };
  const logoutAdmin = async () => {
    try {
      setNotice("");
      setError("");
      await api.post("/api/auth/logout");
      await refreshAuth();
      setSettings({ monitorTypes: {} });
      if (active === "settings") setActive("dashboard");
      setNotice("已退出管理员");
    } catch (err) {
      setError(err.message || "退出失败");
    }
  };
  const changeAdminPassword = (payload) => runAction("change-password", () => api.post("/api/auth/change-password", payload), "管理员密码已修改");
  const testModel = async () => {
    try {
      setActionLoading("test-model", true);
      setModelTest(null);
      setModelTest(await api.post("/api/ai/test"));
    } catch (err) {
      setModelTest({ ok: false, error: err.message || "测试失败" });
    } finally {
      setActionLoading("test-model", false);
    }
  };
  const loadModels = async () => {
    try {
      setActionLoading("load-models", true);
      setError("");
      const data = await api.get("/api/ai/models");
      setModelList(data.models || []);
      const intelligentAnalysis = { ...defaultIntelligentAnalysis, ...(settings.intelligentAnalysis || {}) };
      if (!intelligentAnalysis.modelName && data.defaultModel) {
        setSettings({
          ...settings,
          intelligentAnalysis: {
            ...intelligentAnalysis,
            modelName: data.defaultModel
          }
        });
      }
      setNotice(data.models?.length ? `已读取 ${data.models.length} 个模型` : "未读取到可用模型");
    } catch (err) {
      setError(err.message || "读取模型失败");
    } finally {
      setActionLoading("load-models", false);
    }
  };
  const rebuildKnowledge = async () => {
    try {
      setActionLoading("rebuild-knowledge", true);
      setNotice("");
      setError("");
      const status = await api.post("/api/knowledge/rebuild");
      setKnowledgeRebuildStatus(status);
      setNotice(status.alreadyRunning ? "知识库索引已在后台重建中" : "知识库索引已开始后台重建");
      await loadKnowledgeStatus();
    } catch (err) {
      setError(err.message || "启动知识库重建失败");
    } finally {
      setActionLoading("rebuild-knowledge", false);
    }
  };
  const applySuggestion = (id) => runAction(`apply-suggestion-${id}`, () => api.post(`/api/suggestions/${id}/apply`), "建议已应用");
  const applyAllSuggestions = async () => {
    try {
      setActionLoading("apply-all-suggestions", true);
      setNotice("");
      setError("");
      const result = await api.post("/api/suggestions/apply-all");
      await refresh();
      const total = result.total || 0;
      const applied = result.applied || 0;
      const failed = result.failed || [];
      if (!total) {
        setNotice("暂无待应用的智能建议");
      } else if (!failed.length) {
        setNotice(`智能建议已全部应用，共 ${applied} 条`);
      } else if (applied > 0) {
        setNotice(`已应用 ${applied}/${total} 条，${failed.length} 条失败仍保留`);
        setError(failed.map((item) => `#${item.id}：${item.message}`).join("；"));
      } else {
        setError(`全部应用失败：${failed.map((item) => `#${item.id}：${item.message}`).join("；")}`);
      }
      return !failed.length;
    } catch (err) {
      setError(err.message || "智能建议批量应用失败");
      return false;
    } finally {
      setActionLoading("apply-all-suggestions", false);
    }
  };
  const dismissSuggestion = (id) => runAction(`dismiss-suggestion-${id}`, () => api.post(`/api/suggestions/${id}/dismiss`), "建议已忽略");
  const createTask = (payload) => runAction("create-task", () => api.post("/api/tasks", payload), "任务已新增");
  const patchTask = (id, values) => runAction(`patch-task-${id}`, () => api.patch(`/api/tasks/${id}`, { values }), "任务已更新");
  const patchDeliverable = (id, values) => runAction(`patch-deliverable-${id}`, () => api.patch(`/api/deliverables/${id}`, { values }), "交付物已更新");
  const analyzeAuthority = async () => {
    try {
      setActionLoading("analyze-authority", true);
      setNotice("");
      setError("");
      const status = await api.post("/api/document-authority/analyze");
      setAuthorityAnalyzeStatus(status);
      setNotice(status.alreadyRunning ? "文档权威分析已在后台运行" : "文档权威分析已开始后台运行");
      await loadAuthority();
    } catch (err) {
      setError(err.message || "启动文档权威分析失败");
    } finally {
      setActionLoading("analyze-authority", false);
    }
  };
  const patchAuthority = async (id, values) => {
    const ok = await runAction(`patch-authority-${id}`, () => api.patch(`/api/document-authority/${id}`, { values }), "文档权威已更新");
    if (ok) await loadAuthority();
  };
  const applyAuthoritySuggestion = async (id) => {
    const ok = await runAction(`apply-authority-${id}`, () => api.post(`/api/document-authority/suggestions/${id}/apply`), "权威建议已应用");
    if (ok) await loadAuthority();
  };
  const dismissAuthoritySuggestion = async (id) => {
    const ok = await runAction(`dismiss-authority-${id}`, () => api.post(`/api/document-authority/suggestions/${id}/dismiss`), "权威建议已忽略");
    if (ok) await loadAuthority();
  };
  const startOcr = (id) => runAction(`ocr-start-${id}`, () => api.post(`/api/ocr/documents/${id}/start`), "OCR 已启动");
  const retryOcr = (id) => runAction(`ocr-retry-${id}`, () => api.post(`/api/ocr/documents/${id}/retry`), "OCR 已重新入队");
  const rebuildWikiSuggestions = async () => {
    try {
      setActionLoading("rebuild-wiki", true);
      setNotice("");
      setError("");
      const status = await api.post("/api/wiki/rebuild-suggestions");
      setWikiRebuildStatus(status);
      setNotice(status.alreadyRunning ? "Wiki 更新建议已在后台生成中" : "Wiki 更新建议已开始后台生成");
      await loadWikiSuggestions();
    } catch (err) {
      setError(err.message || "启动 Wiki 生成失败");
    } finally {
      setActionLoading("rebuild-wiki", false);
    }
  };
  const applyWikiSuggestion = async (id) => {
    const ok = await runAction(`apply-wiki-${id}`, () => api.post(`/api/wiki/suggestions/${id}/apply`), "Wiki 建议已应用");
    if (ok) await loadWikiSuggestions();
  };
  const applyAllWikiSuggestions = async () => {
    const ok = await runAction("apply-all-wiki", () => api.post("/api/wiki/suggestions/apply-all"), "Wiki 建议已全部应用");
    if (ok) await loadWikiSuggestions();
  };
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
    if (active === "dashboard") {
      return <Dashboard data={dashboardData} onScan={scanNow} onPreview={openPreview} resetLayoutSignal={dashboardResetSignal} scanLoading={isActionLoading("scan")} />;
    }
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
      return <SuggestionsView suggestions={suggestions} onApply={applySuggestion} onApplyAll={applyAllSuggestions} onDismiss={dismissSuggestion} onPreview={openPreview} actionStates={actionStates} />;
    }
    if (active === "documents") {
      return <DocumentsView documents={documents} onPreview={openPreview} isAdmin={auth.isAdmin} onStartOcr={startOcr} onRetryOcr={retryOcr} actionStates={actionStates} />;
    }
    if (active === "deliverables") {
      return <DeliverablesView deliverables={deliverables} documents={documents} onPatch={patchDeliverable} onPreview={openPreview} />;
    }
    if (active === "authority" && auth.isAdmin) {
      return (
        <AuthorityView
          authorityDocs={authorityDocs}
          authoritySuggestions={authoritySuggestions}
          analyzeStatus={authorityAnalyzeStatus}
          onAnalyze={analyzeAuthority}
          onPatch={patchAuthority}
          onApplySuggestion={applyAuthoritySuggestion}
          onDismissSuggestion={dismissAuthoritySuggestion}
          onPreview={openPreview}
          actionStates={actionStates}
        />
      );
    }
    if (active === "qa") return <QAView onPreview={openPreview} />;
    if (active === "wiki") {
      return (
        <WikiView
          pages={wikiPages}
          suggestions={wikiSuggestions}
          isAdmin={auth.isAdmin}
          onRebuild={rebuildWikiSuggestions}
          onApply={applyWikiSuggestion}
          onApplyAll={applyAllWikiSuggestions}
          rebuildStatus={wikiRebuildStatus}
          actionStates={actionStates}
          onPreview={openPreview}
        />
      );
    }
    if (active === "weekly") return <WeeklyView weekly={weekly} onPreview={openPreview} />;
    if (active === "settings" && auth.isAdmin) {
      return (
        <SettingsView
          settings={settings}
          setSettings={setSettings}
          onSave={saveSettings}
          onScan={scanNow}
          onChangePassword={changeAdminPassword}
          onTestModel={testModel}
          modelTest={modelTest}
          onLoadModels={loadModels}
          modelList={modelList}
          onRebuildKnowledge={rebuildKnowledge}
          knowledgeStatus={knowledgeStatus}
          knowledgeRebuildStatus={knowledgeRebuildStatus}
          actionStates={actionStates}
        />
      );
    }
    return null;
  };
  const visibleNavItems = navItems.filter((item) => !item.adminOnly && item.id !== "settings" || auth.isAdmin);

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
          {visibleNavItems.map((item) => {
            const Icon = item.icon;
            return (
              <button className={active === item.id ? "active" : ""} key={item.id} onClick={() => setActive(item.id)}>
                <Icon size={18} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          {auth.isAdmin ? (
            <button className="sidebar-auth-button" onClick={logoutAdmin} title="退出管理员">
              <LogOut size={13} />
              退出管理员
            </button>
          ) : (
            <button className="sidebar-auth-button" onClick={() => setLoginOpen(true)} title="管理员登录">
              <Lock size={13} />
              管理员登录
            </button>
          )}
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <div className="eyebrow">国产化OA集成项目管理系统</div>
            <h1>{visibleNavItems.find((item) => item.id === active)?.label || "项目管理"}</h1>
          </div>
          <div className="top-actions">
            {notice && <span className="notice">{notice}</span>}
            {error && <span className="error">{error}</span>}
            {active === "dashboard" && (
              <button className="ghost-button topbar-reset-button" onClick={() => setDashboardResetSignal((value) => value + 1)} title="恢复布局">
                <RotateCcw size={16} />
                恢复布局
              </button>
            )}
            <button className="icon-button" onClick={() => runAction("refresh", refresh, "已刷新")} title="刷新" disabled={isActionLoading("refresh")}>
              {isActionLoading("refresh") ? <span className="button-spinner" /> : <RefreshCw size={18} />}
            </button>
          </div>
        </header>
        {content()}
      </main>
      <PreviewModal preview={preview} loading={previewLoading} onClose={() => setPreview(null)} />
      <LoginModal open={loginOpen} onClose={() => setLoginOpen(false)} onLogin={loginAdmin} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
