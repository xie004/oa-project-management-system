from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SYSTEM_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = SYSTEM_ROOT / "backend"
DATA_DIR = BACKEND_ROOT / "data"
DB_PATH = DATA_DIR / "oa_project.db"
DEFAULT_MONITOR_ROOT = Path(
    os.environ.get("OA_PM_MONITOR_ROOT", str(SYSTEM_ROOT.parent))
).resolve()

PROJECT_GOALS = [
    "完成国产化 OA 平台与院内相关业务系统的集成建设，支撑公文、流程、表单、档案、签章等关键协同办公场景。",
    "完成历史数据、流程表单和组织权限的迁移与校验，保障新旧系统切换过程可控。",
    "形成可跟踪、可验收、可归档的项目资料体系，支撑上线、试运行和最终验收。",
]

PROJECT_PLAN_MILESTONES = [
    ("项目组织与启动", "成立项目组织，明确工作机制、参会人员、实施节奏和沟通机制。", "2026-03-11", "2026-03-11", "completed", "green"),
    ("需求调研与范围确认", "完成业务范围、系统集成范围、流程表单和迁移对象梳理。", "2026-04-15", "", "completed", "green"),
    ("测试环境与资源准备", "完成测试环境、服务器资源清单和基础部署条件确认。", "2026-06-20", "", "in_progress", "amber"),
    ("表单与数据迁移", "完成历史数据试迁移、新样式流程表单制作和前台逻辑整理。", "2026-06-30", "", "in_progress", "amber"),
    ("系统集成开发与联调", "完成 OA 与相关业务系统接口对接、联调验证和问题整改。", "2026-07-15", "", "planned", "green"),
    ("业务测试与问题整改", "组织重点业务部门开展测试，形成问题清单并闭环整改。", "2026-07-31", "", "planned", "green"),
    ("上线切换与试运行", "完成上线计划、切换方案、用户验证和试运行跟踪。", "2026-08-15", "", "planned", "green"),
    ("项目验收与资料归档", "完成验收材料、交付物、会议纪要、变更记录和总结归档。", "2026-09-15", "", "planned", "green"),
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    value = row["value"]
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    conn.execute(
        """
        INSERT INTO system_settings(key, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, encoded, now_iso()),
    )


def default_monitor_types(root: Path = DEFAULT_MONITOR_ROOT) -> dict[str, dict[str, Any]]:
    return {
        "weekly_report": {
            "label": "项目周报",
            "directories": [str(root / "项目周例会")],
            "patterns": ["周报"],
            "enabled": True,
        },
        "meeting": {
            "label": "会议纪要",
            "directories": [str(root / "项目周例会"), str(root / "会议")],
            "patterns": ["会议纪要", "会议"],
            "enabled": True,
        },
        "resource": {
            "label": "资源需求",
            "directories": [str(root / "资源需求")],
            "patterns": ["资源", "服务器"],
            "enabled": True,
        },
        "contract_tender": {
            "label": "合同招标",
            "directories": [str(root)],
            "patterns": ["合同", "招标", "投标", "中标"],
            "enabled": True,
        },
        "requirement_change": {
            "label": "需求变更",
            "directories": [str(root)],
            "patterns": ["需求", "变更", "流程", "集成"],
            "enabled": True,
        },
    }


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL,
                system_name TEXT NOT NULL,
                phase TEXT NOT NULL,
                owner TEXT,
                description TEXT,
                start_date TEXT,
                target_date TEXT,
                overall_progress INTEGER NOT NULL DEFAULT 0,
                color_status TEXT NOT NULL DEFAULT 'green',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                extension TEXT NOT NULL,
                doc_category TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                modified_at TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                error TEXT,
                last_indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS weekly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
                period_start TEXT,
                period_end TEXT,
                progress_text TEXT,
                next_plan_text TEXT,
                coordination_text TEXT,
                risk_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
                title TEXT,
                meeting_time TEXT,
                location TEXT,
                participants TEXT,
                topics TEXT,
                decisions TEXT,
                summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                owner TEXT,
                status TEXT NOT NULL DEFAULT 'not_started',
                priority TEXT NOT NULL DEFAULT 'medium',
                color_status TEXT NOT NULL DEFAULT 'green',
                start_date TEXT,
                due_date TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                source TEXT,
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                planned_date TEXT,
                actual_date TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                color_status TEXT NOT NULL DEFAULT 'green',
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                level TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'open',
                mitigation TEXT,
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS change_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                proposer TEXT,
                impact TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS update_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                suggestion_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                confidence REAL NOT NULL DEFAULT 0.7,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                applied_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(doc_category);
            CREATE INDEX IF NOT EXISTS idx_suggestions_status ON update_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
            """
        )
        seed_defaults(conn)
        ensure_project_plan(conn)
        conn.commit()
    finally:
        conn.close()


def seed_defaults(conn: sqlite3.Connection) -> None:
    now = now_iso()
    settings_count = conn.execute("SELECT COUNT(*) AS c FROM system_settings").fetchone()["c"]
    if settings_count == 0:
        set_setting(conn, "system_name", "国产化OA集成项目管理系统")
        set_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT))
        set_setting(conn, "monitor_types", default_monitor_types(DEFAULT_MONITOR_ROOT))
        set_setting(conn, "ignored_directories", ["oa-project-management-system", ".venv", "node_modules", "dist"])
        set_setting(conn, "last_scan_at", "")

    profile = conn.execute("SELECT id FROM project_profile WHERE id = 1").fetchone()
    if not profile:
        conn.execute(
            """
            INSERT INTO project_profile(
                id, name, system_name, phase, owner, description, start_date, target_date,
                overall_progress, color_status, updated_at
            )
            VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "协同办公平台（OA）集成项目",
                "国产化OA集成项目管理系统",
                "开发实施",
                "广东质检院",
                "用于跟踪国产化 OA 集成项目的进度、任务、里程碑、会议、变更和周报。",
                "2026-03-11",
                "",
                45,
                "green",
                now,
            ),
        )

    if conn.execute("SELECT COUNT(*) AS c FROM milestones").fetchone()["c"] == 0:
        milestones = [
            ("项目启动会完成", "启动项目组织、会议机制和实施安排。", "2026-03-11", "2026-03-11", "completed", "green"),
            ("合同与需求资料归档", "合同、招标、需求文件进入项目资料库。", "2026-02-12", "2026-02-12", "completed", "green"),
            ("系统集成需求清单确认", "梳理并确认系统集成范围、对接对象和需求清单。", "2026-06-10", "", "in_progress", "green"),
            ("服务器资源申请与确认", "完成 OA、档案、E 签宝等相关服务器资源申请。", "2026-06-20", "", "in_progress", "amber"),
            ("表单迁移与前台逻辑落地", "完成新表单样式制作、前台逻辑梳理和批量迁移。", "2026-06-30", "", "in_progress", "amber"),
            ("上线准备与试运行", "完成上线计划、切换安排、试运行验证。", "", "", "planned", "green"),
            ("项目验收", "完成验收材料汇总、问题闭环和验收确认。", "", "", "planned", "green"),
        ]
        conn.executemany(
            """
            INSERT INTO milestones(
                title, description, planned_date, actual_date, status, color_status,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*item, now, now) for item in milestones],
        )

    if conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"] == 0:
        tasks = [
            ("表单数据批量试迁移", "按周推进历史表单和流程数据试迁移。", "实施单位", "in_progress", "high", "amber", "2026-05-01", "2026-06-30", 40),
            ("新样式流程表单制作", "完成新系统表单样式和前台逻辑梳理。", "实施单位", "in_progress", "high", "amber", "2026-05-01", "2026-06-30", 40),
            ("FW 系统流程节点梳理", "梳理旧系统流程节点、角色授权和迁移规则。", "项目经理", "in_progress", "medium", "green", "2026-05-15", "", 35),
            ("财务预算报销业务梳理", "完成财务报销、预算管控和项目经费台账规则确认。", "项目经理", "in_progress", "high", "amber", "2026-06-01", "", 30),
            ("系统集成对接推进", "确认业务系统对接范围并推进接口需求。", "项目经理", "in_progress", "high", "green", "2026-05-20", "", 55),
            ("生产环境服务器资源方案确认", "确认生产环境资源规格和部署组件。", "实施单位", "completed", "medium", "green", "2026-05-20", "2026-06-05", 100),
            ("服务器资源申领流程提交", "按已定方案提交资源申请流程并跟踪审批。", "你本人", "not_started", "high", "amber", "2026-06-05", "2026-06-20", 0),
        ]
        conn.executemany(
            """
            INSERT INTO tasks(
                title, description, owner, status, priority, color_status, start_date, due_date,
                progress, source, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '初始计划', ?, ?)
            """,
            [(*item, now, now) for item in tasks],
        )

    if conn.execute("SELECT COUNT(*) AS c FROM risks").fetchone()["c"] == 0:
        conn.execute(
            """
            INSERT INTO risks(
                title, description, level, status, mitigation, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "组织机构和流程调整影响授权与流程配置",
                "单位内部预计 7 月份组织机构和流程调整，可能影响角色授权、流程配置和上线节奏。",
                "medium",
                "open",
                "优先推进数据迁移、表单样式制作和系统集成，待调整时间明确后同步修订计划。",
                now,
                now,
            ),
        )


def ensure_project_plan(conn: sqlite3.Connection) -> None:
    if get_setting(conn, "project_goals") is None:
        set_setting(conn, "project_goals", PROJECT_GOALS)

    now = now_iso()
    conn.execute(
        """
        UPDATE project_profile
        SET target_date = CASE WHEN COALESCE(target_date, '') = '' THEN ? ELSE target_date END,
            description = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            "2026-09-15",
            "围绕国产化 OA 集成建设，跟踪需求、开发、迁移、集成、测试、上线、验收全过程。",
            now,
        ),
    )

    for item in PROJECT_PLAN_MILESTONES:
        exists = conn.execute("SELECT id FROM milestones WHERE title = ?", (item[0],)).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO milestones(
                title, description, planned_date, actual_date, status, color_status,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*item, now, now),
        )
