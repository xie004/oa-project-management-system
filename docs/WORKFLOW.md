# 协作与迭代工作流

本文档规定 Codex 对话和代码迭代的协作方式，目标是减少上下文混杂，保证每个版本都有明确记录和回退点。

## 对话分工

### 主窗口

主窗口长期保留，用于管理系统大方向。

主窗口负责：

- 讨论系统总体目标、阶段计划和优先级。
- 判断需求是小修、功能优化还是重大变更。
- 形成大功能的实施方案。
- 记录版本状态、稳定 tag、已知问题和下一步计划。
- 接收功能窗口完成后的结果汇报。

主窗口不建议承担：

- 大量连续代码修改。
- 多个功能并行实现。
- 长时间调试某个单点问题。

### 功能窗口

功能窗口用于执行具体功能、修复或优化。

适合单独开功能窗口的情况：

- 涉及后端、前端、数据库多处修改。
- 需要较多测试或浏览器验证。
- 会影响核心数据模型、智能分析、Wiki、问答、权限、扫描等关键链路。
- 需要形成独立提交和稳定版本 tag。

可以留在主窗口处理的情况：

- 文案修改。
- 小样式调整。
- 单个按钮位置调整。
- 不涉及数据结构和核心逻辑的轻量修复。

## 标准流程

1. 在主窗口提出需求或问题。
2. 主窗口先判断范围和影响。
3. 小修可直接处理；大功能先形成实施方案。
4. 为大功能新开功能窗口，并把方案作为功能窗口的起点。
5. 功能窗口完成开发、验证、提交和推送。
6. 功能窗口返回主窗口以下信息：
   - 完成内容
   - 提交号
   - tag
   - 验证结果
   - 遗留问题
7. 主窗口更新 `docs/ROADMAP.md` 和 `docs/CHANGELOG.md`。

## 提交与 tag 规则

每个稳定功能完成后应执行：

```powershell
git status --short
git add <changed-files>
git commit -m "<短英文提交说明>"
git tag <feature-name>-<YYYYMMDD>
git push origin master
git push origin <tag-name>
```

tag 命名建议：

- 功能优化：`gantt-optimization-20260623`
- 权限增强：`admin-permissions-20260623`
- Wiki 优化：`wiki-source-governance-20260623`
- 问答优化：`qa-quality-20260623`

## 文档维护规则

- `README.md`：面向使用者，只保留系统介绍、启动、迁移和维护入口。
- `docs/ROADMAP.md`：记录方向、优先级、待办池和暂缓事项。
- `docs/CHANGELOG.md`：记录稳定版本、提交号、tag、验证和回退方式。
- `docs/WORKFLOW.md`：记录协作方式和版本管理规则。

## 回退规则

查看已有稳定版本：

```powershell
git tag --list
```

临时查看某个版本：

```powershell
git checkout <tag-name>
```

从某个版本重新开分支：

```powershell
git checkout -b restore-from-<tag-name> <tag-name>
```

不建议在未确认影响前直接强制回退 `master`。

## 分享给其他人前检查

- `README.md` 启动说明是否清晰。
- `docs/CHANGELOG.md` 是否记录最新稳定 tag。
- `git status --short` 是否干净。
- `git push origin master` 是否完成。
- 最新稳定 tag 是否已推送到 GitHub。
