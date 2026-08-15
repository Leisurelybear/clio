# R-041 统一任务管理中心设计

**Status:** Draft，待用户确认后实施。

## 目标

把目前分散在 Run、单视频重跑、剪辑导出、Whisper 安装和波形生成中的线程、取消、进度、错误与日志生命周期，收敛到一个统一任务管理中心。用户可以跨项目查看正在运行和历史任务，进入任务详情查看执行过程，并在任务支持时取消或重试。

## 当前问题

现有后台执行由多套互不兼容的机制管理：

- Pipeline / rerun 使用 `_ServerState.run_thread`、`cancel_event` 和 `.progress.json`。
- 剪辑导出使用 `_ServerState.job_thread` 与 `job_cancel_event`，没有统一的持久化进度。
- Whisper 安装使用模块级 `_PROJECT_TASKS` 与 `.whisper_install.json`。
- 波形生成使用自己的线程、信号量、锁文件和错误冷却文件。
- `.processing.json` 保存每个素材各步骤的产物状态，但不表示一次任务的完整生命周期。
- 前端分别轮询或订阅不同接口，完成、失败和中断提示散落在各功能模块。

结果是：任务无法统一发现，状态含义不一致，服务重启后的中断恢复逻辑重复，新增后台任务也需要重新实现线程、取消、进度和 UI。

## 核心决策

### 1. 任务中心是执行生命周期的唯一事实来源

新增 `clio/task_center/` 包，负责：

- 创建任务与生成稳定 `task_id`
- 排队、启动和并发策略
- 状态迁移与心跳
- 进度、阶段、日志和错误事件
- 协作式取消
- 服务重启后的 stale task 恢复
- 查询、订阅、历史保留和清理

路由不再直接创建或持有业务线程；它们只校验请求、提交任务并返回 `task_id`。

### 2. 状态模型统一且不可逆跳转

顶层状态：

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelling -> cancelled
queued -------------------------> cancelled
queued/running -- app restart --> interrupted
```

`interrupted` 明确表示进程退出导致的非正常终止，不伪装成 `idle` 或 `failed`。终态任务不得再次变回 `running`；重试会创建新任务，并通过 `retry_of` 关联原任务。

### 3. 统一任务记录与事件流

`TaskRecord` 至少包含：

- `id`, `kind`, `status`, `title`
- `project_id`, `project_name`；本地路径只在必要时保存在后端，不进入普通列表响应
- `parent_id`, `retry_of`
- `created_at`, `started_at`, `finished_at`, `heartbeat_at`
- `phase`, `current`, `total`, `progress_pct`, `message`
- `cancellable`, `cancel_requested`
- `error_code`, `error_message`
- 经过白名单过滤的 `input_summary` 与 `result_summary`，禁止记录 API key、完整 prompt 或其他秘密

每次状态、进度和日志变化追加 `TaskEvent`，带单调递增 `seq`。详情页以事件流还原执行过程，SSE 也从同一事件源发送增量更新。

### 4. 使用 SQLite 持久化

任务历史与事件写入全局配置目录旁的 `task-center.sqlite3`，使用 Python 标准库 `sqlite3`，不增加依赖。选择 SQLite 的原因：

- ThreadingHTTPServer 下并发写入比单个 JSON 文件可靠。
- 支持跨项目列表、过滤、分页与未完成任务恢复。
- 任务事件可追加，不需要反复重写越来越大的历史文件。
- 后续通知中心可以通过 task event 游标消费事件。

数据库启用 WAL、busy timeout 和短事务。数据库路径由服务启动时的 `config_path` 派生，测试必须注入临时路径。

### 5. `.processing.json` 继续保留

`.processing.json` 是“素材 × pipeline step”的产物/就绪状态，不等同于某次执行任务。它继续由各 task module 写入，任务详情可以读取并展示，但任务中心不以它判断顶层任务是否完成。

迁移期间 `.progress.json` 和 `.whisper_install.json` 作为兼容投影继续写入；所有前端迁移完成后再单独决定是否删除。不能在第一阶段同时移除旧协议。

### 6. 顶层任务与子过程的边界

首批纳管的用户可感知任务：

| `kind` | 当前来源 | 展示方式 |
| --- | --- | --- |
| `pipeline` | Run panel | 顶层任务，pipeline step 为阶段，素材为子项 |
| `rerun` | 单视频重跑 | 顶层任务，记录视频与步骤 |
| `cut_export` | Plan 剪辑导出 | 顶层任务，片段为子项 |
| `whisper_install` | Whisper 依赖/模型安装 | 顶层任务，下载阶段与字节进度 |
| `waveform` | 播放器波形生成 | 默认归入“后台维护”筛选，避免污染主列表 |

AI 并发 worker、单个 ffmpeg 子进程等只作为父任务的阶段/子项，不再生成大量顶层任务。未来 ffmpeg 安装、导出和其他耗时操作必须通过同一提交接口接入。

### 7. 并发和取消策略由任务类型声明

任务 handler 注册时声明：

- `concurrency_key`：例如同一项目仅允许一个 pipeline/rerun；波形全局最多两个。
- `cancellable`：是否支持取消。
- `recover_policy`：重启后标记 interrupted，或在具备幂等依据时允许重新排队。
- `retention`：普通任务历史保留数量/天数；事件按所属任务级联清理。

第一版不实现进程级强制终止。取消继续使用 `threading.Event` 协作传递，并在 UI 中区分“正在取消”和“已取消”。

## 后端接口

新增统一接口：

- `GET /api/tasks`：跨项目分页列表，可按项目、类型和状态过滤。
- `GET /api/tasks/{task_id}`：任务快照与最近事件。
- `GET /api/tasks/stream?after=<seq>`：全局 SSE 增量事件，支持断线续传。
- `POST /api/tasks/{task_id}/cancel`：请求取消。
- `POST /api/tasks/{task_id}/retry`：按白名单参数创建重试任务。

现有 `/api/run/*`、`/api/rerun`、Whisper 和 waveform 接口先保留，由适配层提交/查询统一任务，待前端完全迁移后再弃用。提交接口应返回 `task_id`，让旧 UI 与任务中心可以指向同一个任务。

## 前端体验

新增全局“任务”入口，不依赖当前项目或编辑器实体：

- 入口徽标显示运行中数量；失败任务仅显示待处理标记，不与通知未读数混用。
- 默认列表按更新时间倒序，支持“进行中 / 失败 / 已完成 / 后台维护”和项目筛选。
- 任务详情展示状态时间线、阶段进度、素材子项、最近日志、错误和可用操作。
- 从 Run、重跑、剪辑、Whisper 等原入口启动后，原位置保留轻量进度，同时提供“在任务中心查看”。
- 切换项目或编辑器页面不会中断 SSE，也不会丢失终态处理。

任务中心不承担通知已读语义。通知中心（R-042）后续订阅任务终态和需用户处理事件，并提供收件箱、已读和深链接。

## 启动与关闭

- 服务启动时把数据库中残留的 `queued/running/cancelling` 标记为 `interrupted`，写入原因事件。
- 桌面关闭沿用现有 shutdown 流程：先向可取消任务发出取消请求，在超时内等待，然后记录剩余任务为 interrupted。
- CLI 使用同一个 `TaskReporter` 协议写任务状态；第一阶段只保证可观察，跨进程从 UI 取消 CLI 不在范围内。

## 非目标

- 分布式队列、多机器 worker 或云端同步
- 多用户权限与远程任务调度
- 第一版支持暂停/继续或任意优先级抢占
- 把 `.processing.json`、session log 或产物索引全部塞进任务数据库
- 在任务中心中保存 API key、完整 AI prompt 或大段模型响应
- 第一版发送邮件、Webhook 或系统推送（属于 R-042）

## 验收标准

- 所有用户可感知后台任务都获得 `task_id`，且可以在统一列表中找到。
- 状态、进度、日志、取消和错误使用同一数据模型及接口。
- 切换项目/页面不丢失任务更新；SSE 重连不会重复或漏掉事件。
- 服务重启后旧运行任务显示为 `interrupted`，不显示为仍在运行或空闲。
- 任务历史不泄漏秘密，列表支持分页和清理。
- 旧 Run、重跑、剪辑、Whisper 与 waveform 行为在迁移期保持兼容。
- Python 与前端测试覆盖状态机、持久化、并发、取消、恢复、SSE 和主要 UI 流程。

## 待确认的产品选择

1. 默认历史保留建议为 30 天且最多 1000 个任务，二者任一达到即清理最旧终态任务。
2. 波形任务默认隐藏在“后台维护”筛选中，但仍属于统一任务中心。
3. 任务中心采用全局入口，可跨项目查看；项目筛选默认跟随当前项目，但允许切换为“全部项目”。
