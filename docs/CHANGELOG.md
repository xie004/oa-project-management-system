# 更新日志

本文档记录稳定版本、主要变更、验证结果和回退方式。每次功能完成并验证后，应先提交代码，再打 tag，再补充本文件。

## 2026-06-23 - 甘特图展示优化

- Tag：`gantt-optimization-20260623`
- Commit：`5d36831`
- 主要变更：
  - 甘特图默认只展示计划级任务，隐藏自动识别的过程任务。
  - 增加“阶段总览 / 任务明细 / 里程碑”三种视图。
  - 增加“月 / 周 / 日”时间尺度和“今天”竖线。
  - 任务看板增加“进入甘特图”开关和批量进入/移出功能。
  - 从甘特图点击任务可跳转到任务看板并高亮定位。
- 验证结果：
  - `python -m compileall backend/app` 通过。
  - `npm run build` 通过。
  - 浏览器实测甘特图、任务看板、任务定位联动正常。
- 回退方式：
  - `git checkout gantt-optimization-20260623`
  - 如需回到该版本并继续开发，可从该 tag 新建分支。

## 2026-06-17 - 多模态 OCR 与 LLM Wiki 基础

- Tag：`multimodal-ocr-wiki-20260617-143800`
- Commit：`eb38590`
- 主要变更：
  - 增加多模态 OCR 和项目 Wiki 基础能力。
  - 图片版 PDF 可进入 OCR 队列。
  - 智能问答开始优先基于 Wiki 结构化内容回答。

## 2026-06-16 - 智能问答与模型配置增强

- Tag：`qa-response-ux-20260616-171800`
- Commit：`4f2b4a5`
- 主要变更：
  - 优化问答回答结构和前端展示体验。
  - 降低 Markdown 标记直接暴露在页面上的概率。

- Tag：`ai-model-discovery-20260616-170500`
- Commit：`c259d1b`
- 主要变更：
  - 增加模型发现和端点规范化能力。

- Tag：`ai-knowledge-qa-20260616-164500`
- Commit：`6b2964c`
- 主要变更：
  - 增加内置知识库问答能力。
  - 增加知识库索引、切片和问答接口。

## 2026-06-15 - 智能分析配置与交付物追踪

- Tag：`analysis-deliverables-20260615-171227`
- Commit：`ca343d6`
- 主要变更：
  - 增加智能文件分析配置。
  - 增加交付物清单追踪。
  - 支持交付物状态、责任人和关联资料。

## 2026-06-09 - 基础项目管理能力

- Tag：`simple-admin-permissions-20260609-152831`
- Commit：`84fa005`
- 主要变更：
  - 增加简单管理员权限。
  - 管理员登录后显示系统设置。

- Tag：`dashboard-reset-topbar-20260609-143953`
- Commit：`c0b0aef`
- 主要变更：
  - 将首页“恢复布局”按钮移动到顶部操作区。

- Tag：`dashboard-inline-task-status-20260609-143445`
- Commit：`b1bb053`
- 主要变更：
  - 首页任务状态展示调整到当前项目区域。

- Tag：`dashboard-draggable-layout-20260609-142042`
- Commit：`cfe5cf4`
- 主要变更：
  - 首页支持拖放和调整布局。

- Tag：`plan-tasks-synced-20260609-135834`
- Commit：`84329dc`
- 主要变更：
  - 同步甘特图和任务看板的项目计划分解内容。

- Tag：`milestones-synced-20260609-134850`
- 主要变更：
  - 同步里程碑页面内容。

- Tag：`detailed-plan-20260609-133854`
- 主要变更：
  - 根据项目资料和当前进度细化项目目标与计划。

- Tag：`baseline-before-detailed-plan-20260609-132951`
- 主要变更：
  - 在细化项目计划前建立回退基线。
