# R-041 统一任务管理中心实施计划

> 本计划在设计确认后执行。每个任务保持一个独立 commit；先写行为测试，再迁移生产入口。

**Goal:** 建立跨项目、可持久化、可订阅的统一任务中心，并逐步接管现有后台执行生命周期。

**Architecture:** `clio/task_center/` 提供状态机、SQLite store、manager、executor registry 和 reporter；现有后台功能通过 adapter 分批迁移。`.processing.json` 保持产物状态职责，旧进度文件在兼容期作为投影。

**Reference:** `docs/superpowers/specs/2026-08-15-r041-unified-task-center-design.md`

## Task 1: 定义任务领域模型与状态机

**Files:**

- Create: `clio/task_center/models.py`
- Create: `clio/task_center/state_machine.py`
- Test: `clio/tests/test_task_center_models.py`

- [ ] 定义 `TaskStatus`、`TaskKind`、`TaskRecord`、`TaskEvent` 和列表过滤类型。
- [ ] 明确允许的状态迁移，并拒绝终态回到运行态。
- [ ] 统一进度归一化、时间字段和错误字段校验。
- [ ] 证明重试创建新任务而不是复活旧任务。

Commit: `feat(tasks): define unified task lifecycle`

## Task 2: 实现 SQLite TaskStore

**Files:**

- Create: `clio/task_center/store.py`
- Create: `clio/task_center/schema.py`
- Test: `clio/tests/test_task_center_store.py`

- [ ] 创建 tasks/events 表、索引与 schema version。
- [ ] 启用 WAL、busy timeout、外键和短事务。
- [ ] 实现 create/get/list/update/append_event 与 cursor 查询。
- [ ] 实现终态历史分页、保留策略和级联清理。
- [ ] 测试并发写、数据库重开、损坏/迁移错误的明确失败行为。

Commit: `feat(tasks): persist task history in sqlite`

## Task 3: 实现 TaskManager、reporter 与执行注册表

**Files:**

- Create: `clio/task_center/manager.py`
- Create: `clio/task_center/reporter.py`
- Create: `clio/task_center/executor.py`
- Test: `clio/tests/test_task_center_manager.py`

- [ ] 实现 submit/start/progress/log/succeed/fail/request_cancel。
- [ ] 让 handler 声明并发 key、可取消性和恢复策略。
- [ ] 维护运行时 cancel event，但所有可查询状态写入 store。
- [ ] 启动时把遗留活动任务原子标记为 interrupted。
- [ ] 测试重复提交、并发上限、取消竞态、worker 异常和关闭恢复。

Commit: `feat(tasks): add task manager and executor registry`

## Task 4: 暴露统一 tasks API 与 SSE

**Files:**

- Create: `clio/ui/routes/tasks.py`
- Modify: `clio/ui/server.py`
- Modify: `clio/ui/handler_protocol.py`
- Test: `clio/tests/test_routes_tasks.py`
- Test: `clio/tests/test_server_routes.py`

- [ ] 添加列表、详情、取消和重试接口。
- [ ] 添加带 `seq` cursor 的 SSE，支持 heartbeat、断线续传和终态更新。
- [ ] 响应中屏蔽内部路径和未获准的输入参数。
- [ ] 在 server 生命周期中初始化和关闭 TaskManager。

Commit: `feat(api): expose unified task endpoints`

## Task 5: 迁移 pipeline run 与单视频 rerun

**Files:**

- Modify: `clio/ui/routes/run.py`
- Modify: `clio/pipeline.py`
- Modify: `clio/progress.py`
- Modify: `clio/ui/server.py`
- Test: `clio/tests/test_routes_run.py`
- Test: `clio/tests/test_progress.py`
- Test: `clio/tests/test_pipeline.py`

- [ ] Run/rerun 路由只提交任务，不直接创建 `run_thread`。
- [ ] 用 `TaskReporter` 贯穿现有 `tracker` 契约，并保持 `.progress.json` 兼容投影。
- [ ] 保留 `files`、`overwrite`、`cancel_event`、`context_override`、`task_prompts` 全链路行为。
- [ ] 原接口返回 `task_id`；旧 status/stream/cancel 从统一任务映射结果。
- [ ] 测试 pipeline/rerun 并发冲突、取消、异常和 stale 恢复。

Commit: `refactor(run): route pipeline execution through task center`

## Task 6: 迁移剪辑导出与 Whisper 安装

**Files:**

- Modify: `clio/ui/routes/plan.py`
- Modify: `clio/ui/routes/whisper_download.py`
- Modify: related task modules and tests

- [ ] 删除路由自持有的 `job_thread` 和 `_PROJECT_TASKS` 生命周期。
- [ ] 剪辑按片段报告子项进度，并支持协作式取消。
- [ ] Whisper 依赖/模型下载报告阶段、百分比、速度/等待消息和错误。
- [ ] 旧 Whisper status/cancel 接口继续作为兼容适配层。

Commit: `refactor(tasks): centralize cut and whisper jobs`

## Task 7: 纳管波形后台任务

**Files:**

- Modify: `clio/tasks/waveform.py`
- Modify: `clio/ui/routes/waveform.py`
- Test: `clio/tests/test_tasks_waveform.py`
- Test: `clio/tests/test_routes_waveform.py`

- [ ] 保留内容缓存 key、跨进程锁和错误冷却语义。
- [ ] 用任务中心管理进程内 worker、全局并发上限和可观察状态。
- [ ] 标记为 `background`，默认不显示在主任务列表。
- [ ] 验证并发 GET 不重复启动、重启后 orphan lock 可恢复。

Commit: `refactor(waveform): expose generation as managed tasks`

## Task 8: 建立全局任务中心 UI

**Files:**

- Create: `clio/ui/static/src/task-center.js`
- Create: `clio/ui/static/src/task-center-view.js`
- Modify: sidebar/header navigation modules and styles
- Test: `clio/ui/static/src/__tests__/task-center.test.js`

- [ ] 添加全局入口、运行中徽标和跨项目列表。
- [ ] 添加状态/类型/项目筛选、分页与空状态。
- [ ] 添加任务详情时间线、阶段、子项、日志、错误、取消和重试。
- [ ] 使用唯一的全局 SSE 连接；页面与项目切换不销毁连接。
- [ ] 原功能入口显示轻量进度和“在任务中心查看”链接。

Commit: `feat(ui): add unified task center`

## Task 9: 切换前端旧状态源并清理兼容层

**Files:**

- Modify: `clio/ui/static/src/runner.js`
- Modify: rerun, plan, whisper and waveform frontend modules
- Modify: relevant backend routes only after all consumers migrate
- Test: Python and Vitest regression suites

- [ ] 所有前端任务状态改由 task store/SSE 驱动。
- [ ] 保留一版旧 API 兼容并增加 deprecation 说明。
- [ ] 评估 `.progress.json` / `.whisper_install.json` 的外部消费者，再决定删除或长期只读投影。
- [ ] 更新 README、CLI reference、AGENTS directory tree 与路线图状态。
- [ ] 运行 pytest、Vitest、ruff 和 mypy gate。

Commit: `refactor(tasks): retire fragmented task status flows`

## 实施顺序与发布边界

- 第一可交付版本：Task 1–5。先统一最重要的 pipeline/rerun，API 已可用，但 UI 仍兼容旧面板。
- 第二可交付版本：Task 6–8。覆盖其余用户可感知后台任务并发布任务中心 UI。
- 清理版本：Task 9。只有在兼容审计完成后才删除旧状态路径。
- R-042 通知中心应在 Task 4 的事件契约稳定后启动，可与 Task 8 后半段并行设计，但单独提交和发布。
