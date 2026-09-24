from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SYSTEM_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = SYSTEM_ROOT / "backend"
LEGACY_DATA_DIR = BACKEND_ROOT / "data"
# SQLite WAL/shm files are not reliable inside WPSDrive's on-demand cloud folder.
# Keep runtime data on a local filesystem; OA_PM_DATA_DIR makes migration portable.
DEFAULT_DATA_DIR = (
    Path.home() / "OAProjectManagementSystem" / "data"
    if os.name == "nt" else LEGACY_DATA_DIR
)
DATA_DIR = Path(os.environ.get("OA_PM_DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()
DB_PATH = DATA_DIR / "oa_project.db"
DEFAULT_MONITOR_ROOT = Path(
    os.environ.get("OA_PM_MONITOR_ROOT", str(SYSTEM_ROOT.parent))
).resolve()

PROJECT_PLAN_VERSION = "2026-06-09-refined-from-project-files"
SQLITE_BUSY_TIMEOUT_MS = 15_000

DEFAULT_INTELLIGENT_ANALYSIS = {
    "enabled": False,
    "provider": "openai_compatible",
    "apiBaseUrl": "",
    "apiKey": "",
    "modelName": "",
    "embeddingApiBaseUrl": "",
    "embeddingApiKey": "",
    "embeddingModelName": "",
    "embeddingTextField": "text",
    "embeddingModelField": "model",
    "embeddingVectorPath": "embedding",
    "rerankerEnabled": False,
    "rerankerApiBaseUrl": "",
    "rerankerApiKey": "",
    "rerankerModelName": "",
    "rerankerScorePath": "score",
    "analysisMode": "auto_suggest",
    "reviewPolicy": "balanced",
    "autoApplyLowRisk": True,
    "timeoutSeconds": 60,
    "maxTextLength": 12000,
    "reviewOnly": True,
    "allowExternalService": False,
}

DEFAULT_DELIVERABLES = [
    (
        "需求分析报告",
        "合同/招标要求",
        "梳理项目建设目标、业务需求、流程范围、系统集成需求和验收口径。",
        "项目经理",
    ),
    (
        "总体实施方案",
        "合同/实施管理",
        "明确实施组织、阶段计划、部署策略、迁移策略、联调测试和上线安排。",
        "实施单位",
    ),
    (
        "测试报告",
        "验收要求",
        "覆盖功能测试、集成测试、回归测试、UAT 问题闭环和测试结论。",
        "实施单位",
    ),
    (
        "数据字典",
        "验收要求",
        "整理核心业务数据结构、接口字段、流程表单字段和数据交换字段说明。",
        "实施单位",
    ),
    (
        "操作手册",
        "培训/验收要求",
        "面向普通用户、审批人员和管理员的系统操作说明。",
        "实施单位",
    ),
    (
        "安装/运维手册",
        "运维移交要求",
        "说明部署架构、安装步骤、配置项、备份恢复、巡检和常见故障处理。",
        "实施单位",
    ),
    (
        "数据迁移一致性校验报告",
        "验收硬指标",
        "说明迁移范围、抽检方法、校验结果、差异处理和最终结论。",
        "实施单位",
    ),
    (
        "等保测评报告",
        "安全测评要求",
        "完成不低于二级等保测评相关材料和测评报告归档。",
        "测评单位/实施单位",
    ),
    (
        "软件测评报告",
        "验收要求",
        "完成第三方或内部软件测评材料、问题整改和测评结论归档。",
        "测评单位/实施单位",
    ),
    (
        "上线切换方案",
        "上线管理要求",
        "包含上线步骤、回退预案、应急联系人、停机窗口和试运行安排。",
        "项目经理",
    ),
    (
        "培训材料与培训签到",
        "培训/验收要求",
        "沉淀用户培训课件、培训记录、签到表和培训问题反馈。",
        "实施单位",
    ),
    (
        "验收报告",
        "验收硬指标",
        "形成验收申请、验收意见、问题闭环、验收结论和签字盖章材料。",
        "项目经理",
    ),
]

PROJECT_GOALS = [
    "建设国产化协同办公平台，覆盖公文、流程表单、行政办公、财务预算报销、项目/采购、人事、会议日程、移动办公等协同办公场景。",
    "完成 OA、档案管理、电子签章及国产化基础软件环境的一体化部署，支持本地私有化部署、国产操作系统/数据库/中间件、统一身份认证和移动端使用。",
    "完成旧 OA、档案、签章等历史数据迁移，重点保证组织架构、用户、历史公文、正文附件、签批单、审批记录、档案目录及原文可查询、可追溯、可校验。",
    "完成不少于 300 个业务流程表单的梳理、设计、配置、测试与上线，优先保障公文、财务预算报销、项目经费台账、行政办公、人事相关流程。",
    "完成与院内业务系统的集成联调，通过统一身份认证、单点登录、待办/已办/消息、数据交换平台等方式支撑业务协同。",
    "满足上线、试运行、测评和验收要求，形成需求分析、测试报告、数据字典、操作手册、安装/运维手册、数据迁移一致性校验报告、测评报告和验收报告等交付物。",
]

PROJECT_PLAN_MILESTONES = [
    ("合同签订与项目启动", "合同签订，明确合同范围、实施周期、付款与验收约束。", "2026-02-12", "2026-02-12", "completed", "green"),
    ("启动会与主计划初版完成", "召开启动会，形成项目组织、沟通机制、主计划初版和启动会纪要。", "2026-03-13", "2026-03-13", "completed", "green"),
    ("测试环境基础部署完成", "完成测试环境检查、堡垒机账号、数据库安装、产品部署和授权协调。", "2026-03-27", "2026-03-27", "completed", "green"),
    ("迁移策略与样例验证完成", "完成数据迁移范围确认、迁移策略汇报、组织架构同步和流程表单样例同步验证。", "2026-04-17", "2026-04-17", "completed", "green"),
    ("签章与档案系统测试部署完成", "完成签章服务、档案系统部署和相关联系人对接。", "2026-05-09", "2026-05-09", "completed", "green"),
    ("公文数据首轮试迁移完成", "完成公文模板首轮迁移和可视化校验，后续进入数据校验与增量迁移准备。", "2026-05-29", "2026-05-29", "completed", "green"),
    ("系统集成清单确认", "完成院内业务系统集成清单输出，明确统一身份认证、单点登录、待办/已办和数据交换平台方向。", "2026-06-05", "2026-06-05", "completed", "green"),
    ("生产资源方案确认", "完成 OA、档案、电子签章等生产环境服务器资源方案确认。", "2026-06-05", "2026-06-05", "completed", "green"),
    ("财务预算报销方案确认", "完成项目经费台账、预算控制、报销填报优化、科目归集和财务端建模方案确认。", "2026-06-14", "", "in_progress", "amber"),
    ("表单批量试迁移完成", "完成 300+ 流程表单数据批量试迁移、附件迁移校验和异常问题闭环。", "2026-06-30", "", "in_progress", "amber"),
    ("新样式表单与前台逻辑完成", "完成 300+ 新样式流程表单制作、前台逻辑梳理和关键流程配置。", "2026-06-30", "", "in_progress", "amber"),
    ("生产资源申请与环境部署完成", "完成服务器资源申请审批、生产环境部署、基础软件和集群配置。", "2026-07-05", "", "planned", "amber"),
    ("系统集成开发联调完成", "完成 OA 与档案、签章、统一身份认证、数据交换平台及相关业务系统联调。", "2026-07-25", "", "planned", "green"),
    ("内部验证与问题整改完成", "完成内部验证、迁移数据抽检、重点流程回归测试和问题清单闭环。", "2026-08-10", "", "planned", "green"),
    ("UAT 用户测试完成", "组织科学城总部及各基地关键用户开展 UAT 测试，形成用户测试记录和问题闭环。", "2026-08-20", "", "planned", "green"),
    ("上线申请提交", "完成上线申请、切换方案、培训安排、旧系统跳转提示和应急预案。", "2026-08-31", "", "planned", "green"),
    ("正式切换上线", "按分批次上线策略正式切换，旧系统保留跳转提示并进行增量迁移。", "2026-09-07", "", "planned", "green"),
    ("试运行满 30 日", "正式上线后不少于 30 日试运行，覆盖科学城总部、琶洲、顺德、东莞基地。", "2026-10-07", "", "planned", "green"),
    ("测评与验收资料齐套", "完成不低于二级等保测评、软件测评、迁移一致性校验和验收材料汇编。", "2026-10-15", "", "planned", "green"),
    ("项目整体验收完成", "组织验收小组完成整体验收，形成验收报告并进入质保期。", "2026-10-30", "", "planned", "green"),
]

PROJECT_PLAN_BREAKDOWN = [
    {
        "phase": "1. 合同与启动基线",
        "period": "2026-02-12 至 2026-03-13",
        "status": "completed",
        "progress": 100,
        "objective": "明确合同范围、实施组织、沟通机制和主计划初版。",
        "work": [
            "合同签订及采购需求范围确认",
            "提交实施人员名单、工作计划和项目启动材料",
            "召开启动会，明确周例会和问题协调机制",
            "形成项目主计划初版和启动会纪要",
        ],
        "deliverables": ["合同文件", "启动会会议纪要", "实施人员名单", "项目主计划初版"],
        "basis": "合同履行条款、2026-03-09 至 2026-03-13 周报、启动会纪要",
    },
    {
        "phase": "2. 环境准备与迁移策略",
        "period": "2026-03-16 至 2026-04-17",
        "status": "completed",
        "progress": 100,
        "objective": "完成测试环境可用、迁移策略明确、样例迁移验证通过。",
        "work": [
            "完成测试环境检查、堡垒机账号、VPN 和授权协调",
            "完成测试环境数据库安装和产品部署",
            "梳理旧系统表单、组织架构和数据迁移范围",
            "验证流程表单单据同步、底表和公文迁移效果",
            "部署签章服务并启动档案系统对接准备",
        ],
        "deliverables": ["测试环境部署记录", "迁移策略", "样例迁移验证记录", "问题清单"],
        "basis": "2026-03-16、2026-03-23、2026-04-07、2026-04-13 周报",
    },
    {
        "phase": "3. 基础系统部署与首轮迁移",
        "period": "2026-04-18 至 2026-05-22",
        "status": "completed",
        "progress": 85,
        "objective": "完成签章、档案、基础迁移调试，进入批量迁移和新表单制作阶段。",
        "work": [
            "完成档案系统和签章系统部署",
            "完成 FW 附件拷贝和流程关联附件迁移调试",
            "完成表单、公文样式迁移和生产环境部署方案确认",
            "启动公文数据、表单数据批量迁移和新样式表单制作",
            "启动办公室、行政科等业务需求调研",
        ],
        "deliverables": ["签章/档案部署记录", "生产环境部署方案", "首轮迁移结果", "业务调研记录"],
        "basis": "2026-05-06、2026-05-18 周报，2026-05-21 周例会纪要",
    },
    {
        "phase": "4. 当前冲刺：表单、流程、财务与集成确认",
        "period": "2026-05-25 至 2026-06-30",
        "status": "in_progress",
        "progress": 45,
        "objective": "6 月底完成 300+ 表单批量试迁移、新样式表单及前台逻辑，固化财务与系统集成方案。",
        "work": [
            "表单数据批量试迁移从 121/300+ 推进至完成并完成抽检",
            "新样式流程表单从 119/300+ 推进至完成，覆盖公文、人事、财务等重点流程",
            "完成 FW 系统流程节点梳理和旧系统流程参考搭建规则",
            "确认项目经费台账、预算控制、报销填报优化和财务端建模方案",
            "持续推进系统集成对接，确认对接方式、责任人、接口清单和费用事项",
            "提交 OA、档案、电子签章服务器资源申请",
        ],
        "deliverables": ["表单迁移清单", "新样式表单清单", "流程节点清单", "财务方案", "系统集成需求清单", "服务器资源申请记录"],
        "basis": "2026-05-25、2026-06-01 周报，2026-05-28、2026-06-04 周例会纪要",
    },
    {
        "phase": "5. 生产环境部署与系统联调",
        "period": "2026-07-01 至 2026-07-31",
        "status": "planned",
        "progress": 0,
        "objective": "完成生产环境部署、系统集成开发联调、迁移演练和内部验证准备。",
        "work": [
            "完成生产服务器资源审批、操作系统、数据库、中间件和集群部署",
            "完成 OA、档案、电子签章联调，验证公文归档、签章和验章能力",
            "通过数据交换平台推进统一身份认证、单点登录、待办/已办、消息集成",
            "完成财务预算报销、项目经费台账等重点流程开发与测试",
            "组织一次全量+增量迁移演练，形成问题清单和整改闭环",
        ],
        "deliverables": ["生产环境部署记录", "联调记录", "迁移演练报告", "问题整改清单"],
        "basis": "招标/需求文件系统集成要求、2026-05-28 周例会确定的统一数据交换平台方向",
    },
    {
        "phase": "6. 内部验证、UAT 与上线准备",
        "period": "2026-08-01 至 2026-08-31",
        "status": "planned",
        "progress": 0,
        "objective": "完成内部验证、关键用户 UAT、培训和上线申请，按 8 月底提交上线申请。",
        "work": [
            "完成内部验证和重点流程回归测试",
            "组织科学城总部及琶洲、顺德、东莞基地关键用户开展 UAT",
            "完成上线培训、操作手册、常见问题和支持机制准备",
            "制定分批次上线、旧系统跳转提示、全量+增量迁移和回退方案",
            "提交上线申请和上线评审材料",
        ],
        "deliverables": ["内部验证记录", "UAT 测试记录", "培训材料", "上线申请", "切换方案", "应急预案"],
        "basis": "2026-05-28 周例会：8 月底提交上线申请、内部验证后开展 UAT",
    },
    {
        "phase": "7. 正式切换与试运行",
        "period": "2026-09-01 至 2026-10-07",
        "status": "planned",
        "progress": 0,
        "objective": "9 月初正式切换，完成不少于 30 日试运行并稳定运行。",
        "work": [
            "按流程类型分批切换上线，旧系统保留跳转提示",
            "执行上线前全量迁移和上线后增量迁移",
            "监控待办、流程、公文、签章、档案、财务等关键业务运行",
            "建立试运行问题日报/周报，完成高优先级问题闭环",
            "确认试运行覆盖科学城总部及各基地",
        ],
        "deliverables": ["上线记录", "增量迁移记录", "试运行报告", "问题闭环清单"],
        "basis": "合同试运行不少于 1 个月要求、2026-05-28 周例会调整后的上线计划",
    },
    {
        "phase": "8. 测评、验收与质保移交",
        "period": "2026-10-08 至 2026-10-30",
        "status": "planned",
        "progress": 0,
        "objective": "完成测评、验收资料、整体验收和质保期移交。",
        "work": [
            "完成不低于二级等级保护测评和软件测评",
            "完成数据迁移一致性校验报告并经双方抽检确认",
            "汇总需求分析报告、测试报告、数据字典、操作手册、安装手册、运维手册",
            "提交验收申请函，配合 7 日内组织验收",
            "形成验收报告并进入 1 年质保期",
        ],
        "deliverables": ["等保测评报告", "软件测评报告", "数据迁移一致性校验报告", "验收材料汇编", "验收报告"],
        "basis": "合同验收前置条件、招标文件验收要求",
    },
]

PROJECT_PLAN_TASK_VERSION = "2026-06-09-refined-plan-tasks-v1"

PROJECT_PLAN_TASKS = [
    ("合同签订与采购需求确认", "确认合同、采购需求、合同履行周期、交付验收标准和付款节点。", "你本人/项目经理", "completed", "high", "green", "2026-02-12", "2026-02-12", 100),
    ("项目启动会与实施机制建立", "召开启动会，明确项目组织、周例会机制、沟通机制和主计划初版。", "项目经理", "completed", "high", "green", "2026-03-09", "2026-03-13", 100),
    ("测试环境与账号授权准备", "完成测试环境检查、堡垒机账号、VPN、操作系统授权和数据库安装。", "实施单位", "completed", "high", "green", "2026-03-16", "2026-03-27", 100),
    ("测试环境产品部署", "完成 OA、数据库及基础组件测试环境部署，支撑迁移验证。", "实施单位", "completed", "high", "green", "2026-03-23", "2026-03-27", 100),
    ("旧系统表单与迁移范围摸底", "梳理旧系统表单、组织架构、用户和历史数据迁移范围。", "项目经理/实施单位", "completed", "medium", "green", "2026-03-16", "2026-04-17", 100),
    ("迁移策略与样例迁移验证", "完成迁移策略汇报确认，验证组织架构、流程表单、底表和公文样例迁移效果。", "实施单位", "completed", "high", "green", "2026-03-23", "2026-04-17", 100),
    ("签章系统测试部署", "完成电子签章服务部署、联系人对接和初步集成准备。", "实施单位", "completed", "medium", "green", "2026-04-07", "2026-05-09", 100),
    ("档案系统测试部署", "完成档案系统部署和档案调研对接准备。", "实施单位", "completed", "medium", "green", "2026-04-07", "2026-05-09", 100),
    ("公文数据迁移调试与首轮试迁移", "完成公文模板迁移调试、公文 6/6 试迁移和首轮数据校验。", "实施单位", "completed", "high", "green", "2026-05-06", "2026-05-29", 100),
    ("生产环境部署方案确认", "确认生产环境采用标准集群部署方案和服务器资源规格。", "项目经理/实施单位", "completed", "high", "green", "2026-05-18", "2026-06-05", 100),
    ("系统集成需求清单输出", "梳理院内业务系统集成范围，输出系统集成需求清单。", "项目经理", "completed", "high", "green", "2026-05-25", "2026-06-05", 100),
    ("财务预算报销方案确认", "确认项目经费台账、预算控制、报销填报优化、科目归集和财务端建模规则。", "项目经理/财务部门/实施单位", "in_progress", "high", "amber", "2026-06-01", "2026-06-14", 70),
    ("表单数据批量试迁移", "将表单数据批量试迁移从 121/300+ 推进至完成，并完成附件与数据抽检。", "实施单位", "in_progress", "high", "amber", "2026-05-25", "2026-06-30", 40),
    ("新样式流程表单制作", "将新样式流程表单从 119/300+ 推进至完成，覆盖公文、人事、财务等重点流程。", "实施单位", "in_progress", "high", "amber", "2026-05-25", "2026-06-30", 40),
    ("FW 系统流程节点梳理", "参考旧系统流程梳理节点、角色授权、流转规则和暂缓项。", "实施单位/项目经理", "in_progress", "medium", "amber", "2026-05-21", "2026-06-30", 45),
    ("服务器资源申领流程提交", "按已确认方案提交 OA、档案、电子签章等生产资源申请并跟踪审批。", "你本人/项目经理", "in_progress", "high", "amber", "2026-06-05", "2026-07-05", 20),
    ("系统集成对接推进", "确认统一身份认证、单点登录、待办/已办、消息和数据交换平台对接方式。", "项目经理/实施单位", "in_progress", "high", "amber", "2026-06-05", "2026-07-25", 25),
    ("生产环境部署实施", "完成生产服务器、操作系统、数据库、中间件、集群和基础软件部署。", "实施单位", "not_started", "high", "green", "2026-07-01", "2026-07-05", 0),
    ("OA 与档案/签章联调", "验证公文办结归档、电子签章、验章、附件和版式文件能力。", "实施单位", "not_started", "high", "green", "2026-07-06", "2026-07-20", 0),
    ("重点业务流程开发与联调", "完成财务预算报销、项目经费台账、行政办公、人事等重点流程开发联调。", "实施单位/业务部门", "not_started", "high", "green", "2026-07-06", "2026-07-25", 0),
    ("全量与增量迁移演练", "组织全量+增量迁移演练，形成迁移报告和问题整改清单。", "实施单位/项目经理", "not_started", "high", "green", "2026-07-20", "2026-07-31", 0),
    ("内部验证与问题整改", "完成内部验证、迁移数据抽检、重点流程回归测试和问题闭环。", "项目经理/实施单位", "not_started", "high", "green", "2026-08-01", "2026-08-10", 0),
    ("UAT 用户测试", "组织科学城总部及各基地关键用户开展 UAT，形成测试记录和问题闭环。", "项目经理/业务部门", "not_started", "high", "green", "2026-08-11", "2026-08-20", 0),
    ("上线培训与操作材料准备", "准备培训材料、操作手册、常见问题和支持机制。", "实施单位/项目经理", "not_started", "medium", "green", "2026-08-11", "2026-08-25", 0),
    ("上线申请与切换方案提交", "提交上线申请，完成分批切换、旧系统跳转、应急预案和回退方案。", "你本人/项目经理", "not_started", "high", "green", "2026-08-21", "2026-08-31", 0),
    ("正式切换上线", "按流程类型分批切换上线，执行上线前全量迁移和上线后增量迁移。", "项目经理/实施单位", "not_started", "high", "green", "2026-09-01", "2026-09-07", 0),
    ("试运行跟踪与问题闭环", "试运行不少于 30 日，跟踪关键业务运行并闭环高优先级问题。", "项目经理/实施单位", "not_started", "high", "green", "2026-09-08", "2026-10-07", 0),
    ("等保测评与软件测评", "完成不低于二级等保测评和软件测评。", "实施单位/第三方机构", "not_started", "high", "green", "2026-10-08", "2026-10-15", 0),
    ("数据迁移一致性校验报告", "完成迁移一致性校验并经双方技术人员抽检确认签字。", "实施单位/项目经理", "not_started", "high", "green", "2026-10-08", "2026-10-15", 0),
    ("验收材料汇编与验收申请", "汇总需求分析、测试报告、数据字典、操作手册、安装/运维手册并提交验收申请。", "实施单位/项目经理", "not_started", "high", "green", "2026-10-15", "2026-10-22", 0),
    ("项目整体验收", "组织验收小组完成整体验收，形成验收报告并进入质保期。", "你本人/项目经理/验收小组", "not_started", "high", "green", "2026-10-23", "2026-10-30", 0),
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    # WAL improves reader/writer concurrency. Keep FULL durability so a power
    # interruption cannot acknowledge a project-data write that never reaches disk.
    conn.execute("PRAGMA synchronous = FULL")
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
        "acceptance_launch": {
            "label": "交付物及验收",
            "directories": [str(root / "交付物及验收")],
            "patterns": ["交付", "验收", "报告", "手册", "上线"],
            "enabled": True,
        },
        "other": {
            "label": "其他项目资料",
            "directories": [str(root / "项目资料")],
            "patterns": [],
            "enabled": True,
        },
    }


def _migrate_legacy_database_once() -> None:
    """Copy an intact legacy DB off the sync drive before enabling WAL writes."""
    legacy = LEGACY_DATA_DIR / "oa_project.db"
    if DATA_DIR != DEFAULT_DATA_DIR.resolve() or DB_PATH.exists() or not legacy.exists():
        return
    wal = legacy.with_name(legacy.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("旧数据库仍有未合并的 WAL 数据，请先关闭旧服务并备份，不能只复制主文件。")
    temporary = DB_PATH.with_name("oa_project.migrating.db")
    source = destination = None
    try:
        # WPS on-demand sidecar placeholders can make normal SQLite open fail.
        # A zero-sized WAL plus immutable main file gives a consistent snapshot.
        source = sqlite3.connect(f"file:{legacy.as_posix()}?mode=ro&immutable=1", uri=True)
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("旧数据库完整性检查失败，未执行迁移。")
        destination = sqlite3.connect(temporary)
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("新数据库完整性检查失败，未执行迁移。")
        destination.close()
        destination = None
        source.close()
        source = None
        temporary.replace(DB_PATH)
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if temporary.exists():
            temporary.unlink()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_database_once()
    conn = get_connection()
    try:
        # WAL lets readers continue while a short write transaction is active.
        # It is persistent for this database and only needs to be requested at startup.
        conn.execute("PRAGMA journal_mode = WAL")
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
                last_indexed_at TEXT NOT NULL,
                analysis_status TEXT NOT NULL DEFAULT 'not_analyzed',
                analysis_at TEXT,
                analysis_error TEXT,
                analysis_raw_response TEXT,
                knowledge_status TEXT NOT NULL DEFAULT 'not_indexed',
                knowledge_indexed_at TEXT,
                knowledge_error TEXT,
                ocr_status TEXT NOT NULL DEFAULT 'not_required',
                ocr_progress INTEGER NOT NULL DEFAULT 0,
                ocr_pages_total INTEGER NOT NULL DEFAULT 0,
                ocr_pages_done INTEGER NOT NULL DEFAULT 0,
                ocr_error TEXT,
                ocr_at TEXT,
                authority_level INTEGER NOT NULL DEFAULT 5,
                authority_score REAL NOT NULL DEFAULT 45,
                authority_scope TEXT NOT NULL DEFAULT '项目过程资料',
                version_label TEXT,
                effective_date TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                authority_reason TEXT,
                authority_status TEXT NOT NULL DEFAULT 'pending',
                authority_note TEXT,
                authority_updated_at TEXT,
                content_hash TEXT,
                version_group TEXT,
                primary_version_id INTEGER,
                extraction_quality REAL NOT NULL DEFAULT 0
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
                show_in_gantt INTEGER NOT NULL DEFAULT 0,
                task_kind TEXT NOT NULL DEFAULT 'observation',
                status_as_of TEXT,
                status_source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                status_confidence REAL NOT NULL DEFAULT 0,
                status_update_mode TEXT NOT NULL DEFAULT 'manual',
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                is_archived INTEGER NOT NULL DEFAULT 0,
                merged_into_id INTEGER,
                archive_reason TEXT,
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
                is_archived INTEGER NOT NULL DEFAULT 0,
                merged_into_id INTEGER,
                archive_reason TEXT,
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
                is_archived INTEGER NOT NULL DEFAULT 0,
                merged_into_id INTEGER,
                archive_reason TEXT,
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
                is_archived INTEGER NOT NULL DEFAULT 0,
                merged_into_id INTEGER,
                archive_reason TEXT,
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
                normalized_title TEXT,
                fingerprint TEXT,
                candidate_action TEXT NOT NULL DEFAULT 'create',
                matched_entity_type TEXT,
                matched_entity_id INTEGER,
                similarity REAL NOT NULL DEFAULT 0,
                quality_score REAL NOT NULL DEFAULT 0,
                quality_warnings_json TEXT NOT NULL DEFAULT '[]',
                evidence_text TEXT,
                evidence_locator TEXT,
                created_at TEXT NOT NULL,
                applied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS suggestion_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL REFERENCES update_suggestions(id) ON DELETE CASCADE,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                evidence_hash TEXT NOT NULL,
                evidence_text TEXT,
                locator TEXT,
                observed_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(suggestion_id, evidence_hash)
            );

            CREATE TABLE IF NOT EXISTS deliverables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                requirement_source TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'not_started',
                owner TEXT,
                planned_date TEXT,
                submitted_date TEXT,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                merged_into_id INTEGER,
                archive_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                summary TEXT,
                char_length INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(document_id, chunk_index)
            );

            CREATE TABLE IF NOT EXISTS knowledge_vectors (
                chunk_id INTEGER PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                embedding_model TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ocr_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                text TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(document_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS wiki_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content TEXT,
                source_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                source_cutoff_date TEXT,
                latest_available_date TEXT,
                publish_mode TEXT NOT NULL DEFAULT 'manual',
                generation_id TEXT,
                published_suggestion_id INTEGER,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_json TEXT NOT NULL DEFAULT '[]',
                generation_mode TEXT NOT NULL DEFAULT 'rules',
                generation_error TEXT,
                strategy_json TEXT NOT NULL DEFAULT '{}',
                generation_id TEXT,
                source_cutoff_date TEXT,
                apply_mode TEXT,
                auto_apply_reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                applied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS document_authority_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                authority_level INTEGER NOT NULL,
                authority_score REAL NOT NULL DEFAULT 50,
                authority_scope TEXT,
                version_label TEXT,
                effective_date TEXT,
                is_current INTEGER NOT NULL DEFAULT 1,
                authority_reason TEXT,
                authority_note TEXT,
                confidence REAL NOT NULL DEFAULT 0.7,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                applied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS project_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                canonical_title TEXT NOT NULL,
                normalized_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                merged_into_entity_id INTEGER REFERENCES project_entities(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_type, record_id)
            );

            CREATE TABLE IF NOT EXISTS entity_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL REFERENCES project_entities(id) ON DELETE CASCADE,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                suggestion_id INTEGER REFERENCES update_suggestions(id) ON DELETE SET NULL,
                evidence_hash TEXT NOT NULL,
                snippet TEXT NOT NULL,
                locator TEXT,
                observed_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.7,
                extraction_method TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL,
                UNIQUE(entity_id, evidence_hash)
            );

            CREATE TABLE IF NOT EXISTS data_quality_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_kind TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                primary_record_id INTEGER,
                related_record_ids_json TEXT NOT NULL DEFAULT '[]',
                title TEXT NOT NULL,
                description TEXT,
                confidence REAL NOT NULL DEFAULT 0.7,
                details_json TEXT NOT NULL DEFAULT '{}',
                safe_auto INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                applied_at TEXT
            );

            CREATE TABLE IF NOT EXISTS data_quality_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER REFERENCES data_quality_suggestions(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(doc_category);
            CREATE INDEX IF NOT EXISTS idx_suggestions_status ON update_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
            CREATE INDEX IF NOT EXISTS idx_deliverables_status ON deliverables(status);
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON knowledge_chunks(document_id);
            CREATE INDEX IF NOT EXISTS idx_ocr_pages_document ON ocr_pages(document_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_suggestions_status ON wiki_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_document_authority_suggestions_status ON document_authority_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_entities_key ON project_entities(entity_type, normalized_key, status);
            CREATE INDEX IF NOT EXISTS idx_entity_evidence_entity ON entity_evidence(entity_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_quality_suggestions_status ON data_quality_suggestions(status, suggestion_kind);
            """
        )
        ensure_schema(conn)
        from app.services.observations import ensure_observation_schema
        ensure_observation_schema(conn)
        ensure_performance_indexes(conn)
        seed_defaults(conn)
        ensure_project_plan(conn)
        ensure_deliverables(conn)
        conn.commit()
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    document_columns = {
        "analysis_status": "TEXT NOT NULL DEFAULT 'not_analyzed'",
        "analysis_at": "TEXT",
        "analysis_error": "TEXT",
        "analysis_raw_response": "TEXT",
        "knowledge_status": "TEXT NOT NULL DEFAULT 'not_indexed'",
        "knowledge_indexed_at": "TEXT",
        "knowledge_error": "TEXT",
        "ocr_status": "TEXT NOT NULL DEFAULT 'not_required'",
        "ocr_progress": "INTEGER NOT NULL DEFAULT 0",
        "ocr_pages_total": "INTEGER NOT NULL DEFAULT 0",
        "ocr_pages_done": "INTEGER NOT NULL DEFAULT 0",
        "ocr_error": "TEXT",
        "ocr_at": "TEXT",
        "authority_level": "INTEGER NOT NULL DEFAULT 5",
        "authority_score": "REAL NOT NULL DEFAULT 45",
        "authority_scope": "TEXT NOT NULL DEFAULT '项目过程资料'",
        "version_label": "TEXT",
        "effective_date": "TEXT",
        "is_current": "INTEGER NOT NULL DEFAULT 1",
        "authority_reason": "TEXT",
        "authority_status": "TEXT NOT NULL DEFAULT 'pending'",
        "authority_note": "TEXT",
        "authority_updated_at": "TEXT",
        "content_hash": "TEXT",
        "version_group": "TEXT",
        "primary_version_id": "INTEGER",
        "extraction_quality": "REAL NOT NULL DEFAULT 0",
        "recognition_rule_version": "TEXT",
        "recognition_coverage": "REAL NOT NULL DEFAULT 0",
        "recognition_candidate_count": "INTEGER NOT NULL DEFAULT 0",
        "recognition_rejected_count": "INTEGER NOT NULL DEFAULT 0",
        "recognition_failed_batches": "INTEGER NOT NULL DEFAULT 0",
        "recognition_report_json": "TEXT",
    }
    for name, definition in document_columns.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recognition_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
            candidate_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            evidence_text TEXT NOT NULL DEFAULT '', locator TEXT NOT NULL DEFAULT '',
            reason_code TEXT NOT NULL, reason TEXT NOT NULL,
            source_method TEXT NOT NULL DEFAULT 'rule', batch_index INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recognition_preview_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, trigger TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0, rejected_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0,
            invalid_count INTEGER NOT NULL DEFAULT 0, match_change_count INTEGER NOT NULL DEFAULT 0,
            progress_change_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
            current_document TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            before_hash TEXT NOT NULL DEFAULT '', after_hash TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS recognition_preview_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES recognition_preview_runs(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            document_name TEXT NOT NULL, source_date TEXT NOT NULL DEFAULT '',
            result_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '', evidence_text TEXT NOT NULL DEFAULT '',
            locator TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
            matched_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            matched_task_title TEXT NOT NULL DEFAULT '', match_score REAL NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_recognition_preview_results_run ON recognition_preview_results(run_id, result_type, id);
        CREATE INDEX IF NOT EXISTS idx_recognition_rejections_document ON recognition_rejections(document_id, id);
        """
    )

    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "show_in_gantt" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN show_in_gantt INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            UPDATE tasks
            SET show_in_gantt = CASE
                WHEN COALESCE(source, '') IN ('项目计划分解', '初始计划') THEN 1
                ELSE 0
            END
            """
        )
    task_column_definitions = {
        "task_kind": "TEXT NOT NULL DEFAULT 'observation'",
        "status_as_of": "TEXT",
        "status_source_document_id": "INTEGER REFERENCES documents(id) ON DELETE SET NULL",
        "status_confidence": "REAL NOT NULL DEFAULT 0",
        "status_update_mode": "TEXT NOT NULL DEFAULT 'manual'",
    }
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    task_kind_was_added = "task_kind" not in task_columns
    for name, definition in task_column_definitions.items():
        if name not in task_columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
    task_kind_filter = "" if task_kind_was_added else (
        "WHERE task_kind IS NULL OR task_kind = '' "
        "OR task_kind NOT IN ('baseline', 'confirmed_addition', 'observation')"
    )
    conn.execute(
        f"""
        UPDATE tasks
        SET task_kind = CASE
            WHEN COALESCE(source, '') IN ('项目计划分解', '初始计划') THEN 'baseline'
            WHEN COALESCE(source, '') = '手动新增' THEN 'confirmed_addition'
            ELSE 'observation'
        END
        {task_kind_filter}
        """
    )
    archive_columns = {
        "is_archived": "INTEGER NOT NULL DEFAULT 0",
        "merged_into_id": "INTEGER",
        "archive_reason": "TEXT",
    }
    for table in ["tasks", "milestones", "risks", "change_requests", "deliverables"]:
        table_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in archive_columns.items():
            if name not in table_columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    suggestion_columns = {
        "normalized_title": "TEXT",
        "fingerprint": "TEXT",
        "candidate_action": "TEXT NOT NULL DEFAULT 'create'",
        "matched_entity_type": "TEXT",
        "matched_entity_id": "INTEGER",
        "similarity": "REAL NOT NULL DEFAULT 0",
        "quality_score": "REAL NOT NULL DEFAULT 0",
        "quality_warnings_json": "TEXT NOT NULL DEFAULT '[]'",
        "evidence_text": "TEXT",
        "evidence_locator": "TEXT",
    }
    current_suggestion_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(update_suggestions)").fetchall()
    }
    for name, definition in suggestion_columns.items():
        if name not in current_suggestion_columns:
            conn.execute(f"ALTER TABLE update_suggestions ADD COLUMN {name} {definition}")

    wiki_suggestion_columns = {
        "generation_mode": "TEXT NOT NULL DEFAULT 'rules'",
        "generation_error": "TEXT",
        "strategy_json": "TEXT NOT NULL DEFAULT '{}'",
        "generation_id": "TEXT",
        "source_cutoff_date": "TEXT",
        "apply_mode": "TEXT",
        "auto_apply_reason": "TEXT",
    }
    current_wiki_suggestion_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(wiki_suggestions)").fetchall()
    }
    for name, definition in wiki_suggestion_columns.items():
        if name not in current_wiki_suggestion_columns:
            conn.execute(f"ALTER TABLE wiki_suggestions ADD COLUMN {name} {definition}")
    wiki_page_columns = {
        "source_cutoff_date": "TEXT",
        "latest_available_date": "TEXT",
        "publish_mode": "TEXT NOT NULL DEFAULT 'manual'",
        "generation_id": "TEXT",
        "published_suggestion_id": "INTEGER",
    }
    current_wiki_page_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()
    }
    for name, definition in wiki_page_columns.items():
        if name not in current_wiki_page_columns:
            conn.execute(f"ALTER TABLE wiki_pages ADD COLUMN {name} {definition}")
    fts_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_chunks_fts'"
    ).fetchone()
    fts_sql = fts_row["sql"] if fts_row else ""
    fts_columns = [row["name"] for row in conn.execute("PRAGMA table_info(knowledge_chunks_fts)").fetchall()]
    if fts_columns and (fts_columns != ["text"] or "content=" in fts_sql):
        conn.execute("DROP TABLE knowledge_chunks_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
        USING fts5(text)
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ocr_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            text TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(document_id, page_number)
        );
        CREATE TABLE IF NOT EXISTS wiki_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            content TEXT,
            source_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft',
            source_cutoff_date TEXT,
            latest_available_date TEXT,
            publish_mode TEXT NOT NULL DEFAULT 'manual',
            generation_id TEXT,
            published_suggestion_id INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wiki_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_key TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_json TEXT NOT NULL DEFAULT '[]',
            generation_mode TEXT NOT NULL DEFAULT 'rules',
            generation_error TEXT,
            strategy_json TEXT NOT NULL DEFAULT '{}',
            generation_id TEXT,
            source_cutoff_date TEXT,
            apply_mode TEXT,
            auto_apply_reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS document_authority_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            authority_level INTEGER NOT NULL,
            authority_score REAL NOT NULL DEFAULT 50,
            authority_scope TEXT,
            version_label TEXT,
            effective_date TEXT,
            is_current INTEGER NOT NULL DEFAULT 1,
            authority_reason TEXT,
            authority_note TEXT,
            confidence REAL NOT NULL DEFAULT 0.7,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS project_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            canonical_title TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            merged_into_entity_id INTEGER REFERENCES project_entities(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_type, record_id)
        );
        CREATE TABLE IF NOT EXISTS suggestion_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL REFERENCES update_suggestions(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            evidence_hash TEXT NOT NULL,
            evidence_text TEXT,
            locator TEXT,
            observed_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(suggestion_id, evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS entity_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES project_entities(id) ON DELETE CASCADE,
            document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
            suggestion_id INTEGER REFERENCES update_suggestions(id) ON DELETE SET NULL,
            evidence_hash TEXT NOT NULL,
            snippet TEXT NOT NULL,
            locator TEXT,
            observed_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 0.7,
            extraction_method TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL,
            UNIQUE(entity_id, evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS data_quality_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_kind TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            primary_record_id INTEGER,
            related_record_ids_json TEXT NOT NULL DEFAULT '[]',
            title TEXT NOT NULL,
            description TEXT,
            confidence REAL NOT NULL DEFAULT 0.7,
            details_json TEXT NOT NULL DEFAULT '{}',
            safe_auto INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS data_quality_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER REFERENCES data_quality_suggestions(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ocr_pages_document ON ocr_pages(document_id);
        CREATE INDEX IF NOT EXISTS idx_wiki_suggestions_status ON wiki_suggestions(status);
        CREATE INDEX IF NOT EXISTS idx_documents_authority ON documents(authority_level, authority_score);
        CREATE INDEX IF NOT EXISTS idx_document_authority_suggestions_status ON document_authority_suggestions(status);
        CREATE INDEX IF NOT EXISTS idx_entities_key ON project_entities(entity_type, normalized_key, status);
        CREATE INDEX IF NOT EXISTS idx_suggestion_sources_suggestion ON suggestion_sources(suggestion_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_entity_evidence_entity ON entity_evidence(entity_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_quality_suggestions_status ON data_quality_suggestions(status, suggestion_kind);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO suggestion_sources(
            suggestion_id, document_id, evidence_hash, evidence_text, locator, observed_json, created_at
        )
        SELECT id, document_id,
               lower(hex(randomblob(16))),
               COALESCE(evidence_text, description, title),
               COALESCE(evidence_locator, ''),
               payload_json,
               created_at
        FROM update_suggestions
        WHERE document_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM suggestion_sources ss WHERE ss.suggestion_id = update_suggestions.id
          )
        """
    )


def ensure_performance_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes only after compatibility columns have been added."""
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_modified ON documents(modified_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_documents_ocr_status ON documents(ocr_status, ocr_at, id);
        CREATE INDEX IF NOT EXISTS idx_tasks_active_schedule ON tasks(is_archived, start_date, due_date, id);
        CREATE INDEX IF NOT EXISTS idx_tasks_kind_status ON tasks(is_archived, task_kind, status, id);
        CREATE INDEX IF NOT EXISTS idx_milestones_active_schedule ON milestones(is_archived, planned_date, id);
        CREATE INDEX IF NOT EXISTS idx_risks_active ON risks(is_archived, id DESC);
        CREATE INDEX IF NOT EXISTS idx_changes_active ON change_requests(is_archived, id DESC);
        CREATE INDEX IF NOT EXISTS idx_deliverables_active_status ON deliverables(is_archived, status, id);
        CREATE INDEX IF NOT EXISTS idx_ocr_pages_pending ON ocr_pages(document_id, status, page_number);
        """
    )


def seed_defaults(conn: sqlite3.Connection) -> None:
    now = now_iso()
    settings_count = conn.execute("SELECT COUNT(*) AS c FROM system_settings").fetchone()["c"]
    if settings_count == 0:
        set_setting(conn, "system_name", "国产化OA集成项目管理系统")
        set_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT))
        set_setting(conn, "monitor_types", default_monitor_types(DEFAULT_MONITOR_ROOT))
        set_setting(conn, "ignored_directories", ["oa-project-management-system", ".venv", "node_modules", "dist"])
        set_setting(conn, "last_scan_at", "")
    else:
        monitor_types = get_setting(conn, "monitor_types", {})
        if isinstance(monitor_types, dict):
            defaults = default_monitor_types(Path(get_setting(conn, "default_monitor_dir", str(DEFAULT_MONITOR_ROOT))))
            changed = False
            for key in ("acceptance_launch", "other"):
                if key not in monitor_types:
                    monitor_types[key] = defaults[key]
                    changed = True
            if changed:
                set_setting(conn, "monitor_types", monitor_types)
    intelligent_analysis = get_setting(conn, "intelligent_analysis")
    if intelligent_analysis is None:
        set_setting(conn, "intelligent_analysis", DEFAULT_INTELLIGENT_ANALYSIS)
    elif "reviewPolicy" not in intelligent_analysis:
        intelligent_analysis = {**DEFAULT_INTELLIGENT_ANALYSIS, **intelligent_analysis}
        intelligent_analysis["reviewPolicy"] = "balanced"
        intelligent_analysis["reviewOnly"] = False
        intelligent_analysis["autoApplyLowRisk"] = True
        set_setting(conn, "intelligent_analysis", intelligent_analysis)

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
                progress, source, task_kind, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '初始计划', 'baseline', ?, ?)
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


def ensure_deliverables(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS c FROM deliverables").fetchone()["c"] > 0:
        return
    now = now_iso()
    conn.executemany(
        """
        INSERT INTO deliverables(
            name, requirement_source, description, status, owner, planned_date,
            submitted_date, document_id, sort_order, created_at, updated_at
        )
        VALUES(?, ?, ?, 'not_started', ?, '', '', NULL, ?, ?, ?)
        """,
        [(*item, index + 1, now, now) for index, item in enumerate(DEFAULT_DELIVERABLES)],
    )


def ensure_project_plan(conn: sqlite3.Connection) -> None:
    """Add missing baseline records without changing user-maintained project data.

    Project plan constants are useful defaults, but they must not become a hidden
    migration mechanism.  In particular, title-based updates/deletes cannot tell a
    seeded record from a record that a user has subsequently edited.
    """
    plan_version_changed = get_setting(conn, "project_plan_version") != PROJECT_PLAN_VERSION
    if get_setting(conn, "project_goals") is None:
        set_setting(conn, "project_goals", PROJECT_GOALS)
    if get_setting(conn, "project_plan_breakdown") is None:
        set_setting(conn, "project_plan_breakdown", PROJECT_PLAN_BREAKDOWN)
    if plan_version_changed:
        set_setting(conn, "project_plan_version", PROJECT_PLAN_VERSION)

    now = now_iso()

    if plan_version_changed:
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

    if get_setting(conn, "project_plan_task_version") != PROJECT_PLAN_TASK_VERSION:
        for item in PROJECT_PLAN_TASKS:
            exists = conn.execute(
                """
                SELECT id FROM tasks
                WHERE title = ?
                  AND source_document_id IS NULL
                  AND COALESCE(is_archived, 0) = 0
                """,
                (item[0],),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO tasks(
                    title, description, owner, status, priority, color_status, start_date,
                    due_date, progress, source, show_in_gantt, task_kind, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, '项目计划分解', 1, 'baseline', ?, ?)
                """,
                (*item, now, now),
            )
        set_setting(conn, "project_plan_task_version", PROJECT_PLAN_TASK_VERSION)
