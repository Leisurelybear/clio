# Clio 全项目代码与用户流程 Review

**审查日期：** 2026-08-16  
**代码基线：** `HEAD 43b4216`，并包含审查时工作树中已有的 14 个未提交文件改动（未由本审查创建）  
**原则：** 代码和运行结果是唯一 source of truth；旧文档、设计稿和 README 仅用于比较意图，不作为实现事实。  
**范围：** 161 个生产 Python/JavaScript 文件，约 32,034 行；CLI、配置、媒体处理、AI provider、pipeline、plan/cut/export、Whisper、HTTP API、Task Center、桌面宿主和全部前端模块。

## 1. 结论摘要

项目已经具备可用的本地单用户闭环：选择项目和视频 → ffmpeg 压缩 → Gemini/兼容 OpenAI provider 分析 → 口播/转录 → plan 编辑 → cut/JianYing 导出。核心模块有较完整的单元测试，当前本机 Python 测试和 Vitest 均通过。

仍有 3 类会影响生产可信度的问题：

1. 统一任务中心的生命周期并未覆盖所有后台操作，且冷启动重试、快完成订阅、列表快照等边界存在竞态。
2. pipeline 对“部分失败”和 transcribe 返回码没有统一语义，可能把没有产物的运行标成 succeeded。
3. 媒体 identity/index、导出文件写入和持久化数据的边界仍有错配、泄露或中断风险。

建议按以下顺序处理：先修复 P1 的任务重试、导出阻塞、失败状态、overwrite 类型和 index 前缀；再处理 P2 的竞态、清理、原子写和测试覆盖；最后推进架构和可观测性迭代。

## 2. 验证快照

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -m pytest clio/tests/ -q` | **1821 passed, 12 skipped** | 1833 collected，本机 Python 3.10.6 |
| Vitest | **43 files passed, 468 tests passed** | Node 前端测试 |
| `ruff check clio main.py` | **PASS** | 默认 lint |
| 配置化 mypy 门禁 | **PASS** | 29 个纳入门禁的 source files |
| 全量 mypy | **86 errors / 22 files** | 不含 tests，存在较大类型债务 |
| coverage | 未运行 | 本机未安装 pytest-cov；CI 会安装 |
| pip-audit | 未运行 | 本机未安装 pip-audit；CI 会运行 |

项目声明 Python `>=3.11`，但本次本地验证使用 3.10.6；不能据此替代 3.11/3.12 及 Windows/macOS CI 矩阵。当前工作树存在外部未提交修改，复现问题时应记录完整 `git diff`。

## 3. 代码地图与用户流程

### 3.1 主要入口

- CLI：`main.py` → `clio/pipeline.py`、`clio/tasks/*`。
- Web：`clio/ui/server.py` 注册 `clio/ui/routes/*`，静态 ES modules 位于 `clio/ui/static/src/`。
- AI：`clio/ai/factory.py` 按配置选择 `gemini` 或 OpenAI-compatible provider。
- 状态：旧的 processing/progress 文件状态与新的 `clio/task_center/*` 并存。
- 导出：`/api/cut` 使用 Task Center；`/api/export` 仍直接调用 `clio/export/jianying.py`。

### 3.2 正常用户路径

1. `serve` 启动，读取 global/project YAML，恢复最近项目，扫描并建立媒体 identity/index。
2. 前端加载 `/api/project`、`/api/videos`，用户切换压缩/原视频并选择文件。
3. “运行”调用 `/api/run/preview` 查看待处理项，再调用 `/api/run/start` 创建 pipeline task。
4. pipeline 执行 compress、analyze、voiceover、transcribe/plan/label 等步骤；前端通过 Task Center SSE 和兼容旧 SSE 显示进度。
5. 用户在分析、口播、转录和 plan 面板修订产物，调用 `/api/plan/readiness`。
6. `/api/cut` 产生剪辑文件；`/api/export` 生成 JianYing `draft_content.json` 并可复制到目标目录。
7. 任务中心提供历史、取消、重试和实时事件；Whisper install、waveform、rerun 等功能也复用任务事件。

### 3.3 关键异常路径

- 服务重启后数据库仍有 failed/interrupted task。
- AI/Whisper/ffmpeg 单个文件失败，但其它文件继续完成。
- 浏览器在 POST 返回 task_id 后才建立 SSE 订阅。
- 多个浏览器标签或两个首次请求同时触发 handler 注册。
- 导出中途进程退出、目标文件被 JianYing 同时读取。
- 用户输入字符串形式的布尔值、未规范化 index 或带敏感上下文的 prompt。

## 4. 问题清单（按优先级）

严重级别定义：P0=数据破坏/远程高风险；P1=主要功能不可用或结果不可信；P2=明显错误、竞态或运维风险；P3=体验、维护性或文档问题。

### P1 — 建议下一迭代必须修复

#### R-2026-01：Task Center 冷启动后 retry 找不到 handler

**证据：** `clio/ui/server.py:314-323` 的 manager 懒初始化；`clio/ui/routes/run.py:56-72`、`plan.py:65-73`、`whisper_download.py:247-255`、`waveform.py:267-275`、`tasks.py:173-181` 只在旧路由首次访问时注册 handler；`clio/task_center/executor.py:55-77` 的 `registry.require()` 对未注册 kind 抛错。

**触发：** 重启服务 → 只打开任务中心 → 对数据库中 pipeline/cut/whisper/waveform/rerun 任务点击 retry。

**影响：** 任务历史可见但无法恢复，直接 409；违反统一任务中心的核心承诺。

**修复标准：** 启动时一次性注册所有内建 handler，或提供线程安全、幂等的 `register_builtin_task_handlers()`；在全新进程只调用 retry API 的集成测试中，各 kind 均能执行；重复注册不得抛错。

#### R-2026-02：`/api/export` 仍为同步 HTTP 操作，且 job_lock 实际未保护导出

**证据：** `clio/ui/routes/export.py:49-115` 在请求线程直接调用 `export_plan`；`clio/ui/static/src/editor-plan.js:777-809` 同步等待；`_ServerState.job_thread` 未由 export 设置。

**触发：** 大计划或大量素材导出，期间刷新页面、浏览器断开，或并发发送两个 export 请求。

**影响：** 请求超时后失去状态；任务中心无历史/取消/恢复；两个导出可能并行写同一目录。

**修复标准：** 迁移到 `TaskKind.CUT_EXPORT` 或新增 `EXPORT`；返回 `task_id`，统一 progress/cancel/retry/history；按 project+day+format 使用任务级并发 key；旧 API 只做兼容适配。

#### R-2026-03：pipeline 局部失败被标记为 succeeded

**证据：** `clio/pipeline.py:103-133` 忽略每个 step 返回值，只要没有异常就 `tracker.done()`；`clio/tasks/analyze.py:457-535` 仅在全部失败时 raise；`clio/tasks/scripts.py:161-249` 失败只打印 warning；`clio/tasks/transcribe.py:184-195,343-349` 返回 `1` 但 pipeline 不检查。

**触发：** 多视频中部分 AI 请求失败，或 Whisper 未安装/无音轨/转录错误。

**影响：** Task Center 显示绿色 succeeded，但可能没有 analysis、voiceover 或 transcript 产物，用户会继续导出错误计划。

**修复标准：** 每一步返回统一结构（processed/skipped/failed/cancelled/counts）；pipeline 汇总失败并写入 `result_summary`；全失败为 failed，部分失败使用 `succeeded_with_warnings` 或明确 warning/error_count；CLI exit code 与策略一致；UI 能显示缺失产物。

#### R-2026-04：`overwrite` 接受任意 truthy 值，字符串 `"false"` 会覆盖文件

**证据：** `clio/ui/routes/run.py:427-450,496-499` 使用 `bool(obj.get("overwrite", False))`，未验证 JSON 类型。

**触发：** `POST /api/run/start` 或 preview 发送 `{"overwrite":"false"}`。

**影响：** 非预期覆盖已有分析/口播/转录文件，属于潜在破坏性行为。

**修复标准：** 仅接受 JSON boolean；其它类型返回 400；对 `files`、`context_override`、`task_prompts` 做 schema 校验；覆盖前在响应中明确列出将被替换的 artifacts，并保留可恢复备份。

#### R-2026-05：ArtifactIndex 使用无边界前缀匹配，`1` 会匹配 `10`

**证据：** `clio/index.py:216-229` 使用 `existing_key.startswith(idx.lower())`。

**复现：** 同一目录有 `10_ten.mp4`、`1_one.mp4` 和 `1_analysis.json`；实测 `10_ten` 关联到 `1_analysis.json`，`1_one` 反而未关联。

**影响：** 分析/口播/封面可能归到错误视频；默认三位补零降低概率，但配置允许自定义 `naming.index_width`，旧文件和手工编辑计划仍会触发。

**修复标准：** 使用 `expand_index_keys()` / `index_in_set()` 做 token 边界匹配（`idx` 或 `idx_`）；歧义时返回 ambiguous 并阻止自动选择；补充 1/10、001/010、自定义宽度回归测试。

### P2 — 建议近期修复

#### R-2026-06：任务输入、prompt、运行上下文和结果绝对路径持久化过多

**证据：** `clio/task_center/models.py:126-156`、`store.py:38-40,346-347` 完整写入 `input_data`/`result_summary`；`run.py:440-462` 保存 `context_override`、`task_prompts`；pipeline/cut/whisper 结果包含 output、progress 的本地绝对路径。

**影响：** SQLite、任务列表、详情和 SSE 可能暴露完整 prompt、项目路径及上下文；若服务绑定 LAN 或日志备份外泄，风险扩大。

**修复标准：** 将输入拆成可重试最小白名单和私有运行上下文；禁止完整 prompt、API key、context 持久化；API 仅返回 artifact id/basename 或脱敏相对路径；增加 SQLite 和 HTTP 响应的 secret/path redaction 测试。

#### R-2026-07：列表 snapshot 与 SSE cursor 不是同一事务，可能漏事件

**证据：** `clio/ui/routes/tasks.py:83-97` 依次调用 `list()`、`count()`、`latest_event_seq()`；`store.py:118-155,231-234` 分开连接/查询；前端 `task-center.js:119-125` 以 `latest_seq` 初始化 cursor。

**竞态：** 新任务在 list 与 latest_seq 之间提交；它不在 snapshot，但 cursor 已跳过其事件，页面直到刷新都看不到。

**修复标准：** 在同一 SQLite read transaction 返回 `snapshot_seq`、列表和 count；SSE 从该 seq 之后读取；并发 race 测试必须稳定复现并证明不丢任务。

#### R-2026-08：终态事件可能先于专属 listener，部分前端曾永久等待

**证据：** `clio/ui/static/src/task-center.js:160-191` 先由全局 EventSource 消费事件，业务模块随后调用 `subscribeTaskEvents()`。当前工作树已在 `runner.js`、`sidebar-rerun.js`、`editor-config.js`、`waveform.js` 增加 `fetchTask()` 补偿，但通用订阅 API 仍没有统一快照优先语义。

**影响：** 快速失败/完成的 rerun、Whisper install、waveform 可能漏终态；不同模块各自补偿，易出现回归。

**修复标准：** 提供统一 `subscribeTask(taskId, cb)`：先 GET 快照，再注册/重放事件；回调幂等、按 seq 去重；覆盖“订阅前已完成、重连、取消”Vitest。

#### R-2026-09：详情页事件时间线不随 SSE 追加

**证据：** `task-center.js:166-181` 收到事件时只更新 `state.taskDetail.task`，没有把 `payload.event` 追加到 `state.taskDetail.events`；`_detailHtml()` 只渲染已有数组。

**影响：** 用户打开详情后看不到实时执行步骤，只能重新选择或刷新。

**修复标准：** 按 seq 去重追加事件、限制数量并实时重绘；终态后保留最后事件；增加 UI 测试。

#### R-2026-10：TaskStore cleanup 定义但从未进入生命周期

**证据：** `clio/task_center/store.py:251-290` 有 retention/数量限制 cleanup；`server.py:314-323,726-731` 初始化和关闭路径没有调用，亦没有 scheduler。

**影响：** 长期运行的 SQLite 无限增长，事件查询、备份和恢复成本持续增加。

**修复标准：** manager 启动后执行一次，随后按小时/任务完成低频执行；只清理终态任务并级联 events；提供保留天数和数量配置、指标和测试。

#### R-2026-11：首次 handler 注册存在并发竞态

**证据：** 多个旧路由采用“先 `kinds()` 判断、再 `register()`”；`ThreadingHTTPServer` 可并发处理首次请求。

**触发：** 两个标签同时首次访问 pipeline/plan/whisper/waveform API。

**影响：** 两个线程都看到未注册，第二个 `register` 抛 `TaskHandlerAlreadyRegisteredError`，请求失败。

**修复标准：** 所有内建 handler 启动注册；registry.register 幂等或使用锁保护 check-and-register；并发首次请求测试。

#### R-2026-12：JianYing legacy segment offset 未规范化 index

**证据：** `clio/export/jianying.py:92-111,243-277` 的 `_build_index_to_source()` 使用规范化 key，但 `_build_index_to_offset()` 仅保存 `offsets[identity.index]`；`plan_readiness.py:105-120` 会接受 `001`/`1` 等等价 index。

**影响：** 计划写成 `1`、素材 identity 为 `001` 时 source 能匹配而 offset 查找失败，导出 timerange 少加 segment offset，剪辑时间偏移。

**修复标准：** offset map 对 `expand_index_keys(identity.index)` 写入所有等价 key；增加 legacy split + unpadded plan index 的导出断言。

#### R-2026-13：JianYing draft 与复制结果非 atomic

**证据：** `clio/export/jianying.py:408-413` 直接 `write_text()`；`clio/ui/routes/export.py:40-46` 直接 `write_bytes()`。

**影响：** 进程中断或 JianYing 同时读取会留下半写 JSON；下次打开得到损坏草稿。

**修复标准：** 使用项目统一 `write_text_atomic`/临时文件 + `replace`；复制到目标目录也采用临时文件、flush/fsync 后 replace；失败保留旧版本。

#### R-2026-14：新增统一事件流的行为测试不足

**证据：** 当前 Vitest 主要覆盖 `statusLabel`、`kindLabel`、`statusGroup`、`filterTasks`；缺少 EventSource ingestion、cursor reconnect、listener isolation、终态 UI、取消和详情时间线测试。

**影响：** 任务中心跨页面回归难以发现，当前工作树的多个补丁只能靠人工验证。

**修复标准：** 为每个消费者建立 fake EventSource 生命周期测试；覆盖快完成、断线重连、重复 seq、跨项目过滤、cancel/retry 终态。

### P3 — 维护性与体验问题

#### R-2026-15：API 未返回 `updated_at`，排序语义依赖替代字段

**证据：** `clio/task_center/schema.py:28` 有 `updated_at`，但 `TaskRecord.to_dict()`（`models.py:126-156`）未返回；前端只能按当前工作树的 `heartbeat_at/finished_at/started_at/created_at` 排序。

**影响：** 任务进度更新不一定移动到最新位置；字段优先级与后端更新时间语义不一致。

**修复标准：** API 返回 ISO/epoch `updated_at`；前端统一按数值时间排序；兼容旧记录并补充排序测试。

#### R-2026-16：全量类型债务掩盖真实错误

**证据：** 全量 mypy 86 errors/22 files，重点包含 `task_center/store.py`、`ui/routes/tasks.py`、`run.py`、`whisper_download.py`、`server.py`，以及 `index.py` 的 union API、desktop、transcribe 等。

**影响：** 协议漂移（如 handler 属性、provider 返回值）在运行时才暴露，降低重构安全性。

**修复标准：** 先清理 Task Center 和 route protocol，再处理 ArtifactIndex union、desktop、media modules；每批纳入 CI scope，禁止新增 error。

#### R-2026-17：前端超大模块和异步状态管理增加回归成本

**证据：** `editor-config.js` 1795 行、`editor-plan.js` 918 行、`runner.js` 731 行、`sidebar-data.js` 676 行。

**建议：** 按 API client、view model、renderers、event adapters 拆分；所有可取消请求使用 AbortController/latest-wins；统一 toast、empty/error/loading 状态和无障碍属性。

#### R-2026-18：文档、包名和支持矩阵不一致

**证据：** `pyproject.toml` 要求 Python >=3.11，`packaging/README-desktop.md` 写 3.10+；README 测试说明只写 Ubuntu/Windows，而 CI 含 macOS；`package.json` 和多个 README URL 仍是旧 `vlog-editing-helper` 名称。

**影响：** 用户按文档安装可能得到不支持的解释器或错误仓库链接；发布和问题报告难以复现。

**修复标准：** 统一 pyproject、setup 脚本、packaging README、README/README.en、package.json、CI 和 AGENTS；在文档中展示 Python/Node/ffmpeg 版本矩阵。

## 5. 安全与可靠性专项观察

1. API 已支持 Bearer token、Host/Origin 检查和默认 loopback；显式 LAN host 会生成 token，这是正确方向。
2. 但 EventSource 和媒体 URL 仍把 token 放入 query（`task-center.js:171` 等）。URL 可能进入浏览器历史、代理、访问日志和诊断系统；长期应改为 cookie/session 或支持带 Authorization 的 fetch-stream。
3. 所有变更 API 都依赖路由鉴权，但应继续为 JSON Content-Type、Content-Length、路径 basename 和项目目录边界保持负面测试；不要把“本地单用户”当作任意路径安全边界。
4. 配置 `_load_context()` 已尝试限制 context 文件根目录；新增 project/path 功能时应复用该 canonical resolve 和 safe-basename 规则，避免 symlink/大小写/相对路径绕过。

## 6. 推荐修复路线与验收门槛

### Sprint A：可信状态（P1）

- 统一内建 handler registry，冷启动 retry 和并发注册测试。
- pipeline step result/partial failure 语义和 UI 状态。
- export 纳入 Task Center，加入任务级幂等键和原子产物写入。
- 严格 JSON schema，尤其 boolean、列表元素、prompt/context 长度。
- ArtifactIndex token 边界和 ambiguous 阻断。

**门槛：** 新进程启动后不访问其它页面即可 retry；两次相同提交最多一个任务；所有失败策略在 CLI/API/UI 三层一致；错误输入均 4xx 且不改文件。

### Sprint B：一致性与恢复（P2）

- snapshot/cursor 单事务；SSE 使用 `Last-Event-ID`（当前工作树已开始支持）并统一订阅快照优先。
- 详情时间线实时 append、seq 去重、断线重连。
- cleanup scheduler、SQLite 指标和备份策略。
- JianYing offset normalization、atomic write。
- 为任务生命周期、导出、媒体 index 增加集成测试。

**门槛：** 并发 race 测试 1000 次无丢事件；断线后只补发缺失 seq；任何终态都能关闭 loading；损坏写入不会替换上一份有效草稿。

### Sprint C：可维护性与产品化（P3/未来）

- 清理全量 mypy 债务并把 scope 逐批扩大。
- 前端模块化、请求取消、统一错误/空状态和 accessibility。
- API event schema/version、server-side condition wakeup，减少每 250ms 轮询 SQLite。
- canonical project identity（Windows 大小写、别名路径）和跨标签页状态同步。
- 建立端到端矩阵：输入扫描、压缩、Gemini、兼容 provider、Whisper、plan readiness、cut、JianYing、重启恢复。

## 7. 交付与回归清单

每个修复应包含：

- 文件/函数级证据与最小复现；
- 单元测试 + 至少一个真实 HTTP/浏览器生命周期测试；
- 数据兼容说明（旧 task DB、旧 index、旧 plan）；
- 失败、取消、重启、并发、重复提交五类验收；
- 更新英文 commit、README/AGENTS/配置示例（若接口或配置变化）；
- 在 Python 3.11/3.12、Windows/macOS/Linux 和 Node CI 上运行；
- 明确是否改变用户可见状态、退出码、产物路径或隐私边界。

本次 review 不修改业务代码、不提交、不推送；文档本身是唯一新增交付物。 
