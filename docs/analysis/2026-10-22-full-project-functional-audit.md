# 2026-10-22 全域功能审计与增量修复记录

> 状态：已完成（自动化审计范围）。外部服务、真实媒体和桌面窗口仍保留实机验证项。
>
> 范围：CLI、配置、媒体处理、AI/ASR、任务中心、HTTP/Web UI、桌面壳、导出、日志/通知、打包与发布文档。
>
> 边界：真实 Gemini/DeepSeek/阿里云 API、真实剪映导入、桌面窗口交互和真实长视频处理未在本机自动审计中实机验证；相关结论仅覆盖代码路径、单元测试和错误分支。

## 1. 审计方法

- 以仓库说明、配置示例、CLI 帮助、路由注册和模块入口建立功能清单。
- 先跑基线测试与静态检查，再按功能域阅读实现和测试，优先检查跨层契约、失败路径和配置/产物路径一致性。
- 每个确认缺陷采用最小修复并补回归测试；每个独立修复单独提交。
- 大范围重构、需要外部服务或需要产品决策的项只记录到优化 backlog，不混入小修复。

## 2. 环境与基线

| 项目 | 结果 |
|---|---|
| 仓库 | `G:\Coding_Project\IdeaProjects\vlog-video-analysis` |
| Python | 系统 `3.10.6`；项目 `.venv` `3.11.9` |
| 后端测试 | `.venv`：`2079 passed, 12 skipped` |
| 后端覆盖率 | `81.05%`（branch coverage） |
| 前端测试 | `54` 个测试文件，`547 passed` |
| Ruff check | 通过 |
| Ruff format check | 通过（234 files） |
| mypy subset | 通过（39 source files） |
| 工作区初始状态 | clean |

基线命令：

```powershell
.\.venv\Scripts\python.exe -m pytest clio/tests -q
npm test
ruff check clio main.py
ruff format --check clio main.py
mypy --check-untyped-defs --show-error-codes
```

## 3. 功能矩阵

状态说明：`已核查` 表示实现与相邻测试均已检查；`修复中` / `已修复` 指本轮发现的问题；`实机待验` 表示自动测试无法覆盖外部软件或服务。

| 功能域 | 主要入口 | 审查状态 | 备注 |
|---|---|---|---|
| CLI 参数与项目选择 | `clio/main.py` | 已核查 | token、export、显式项目路径、force/refine 参数已修复并冒烟 |
| 配置加载/校验/迁移 | `clio/config/*`、配置路由 | 已核查 | provider 删除保护、类型校验、损坏注册表降级已覆盖 |
| 压缩与媒体身份 | `compress.py`、`identity.py`、`vmeta.py` | 已核查 | ffmpeg/ffprobe 调用与 sidecar 契约 |
| 分析/口播/规划/refine | `tasks/*`、`pipeline.py`、`prompts.py` | 已核查 | AI 外部响应按 mock/错误分支核查；真实调用待实机验证 |
| ASR（本地/阿里云） | `asr/*`、`transcribe.py` | 已核查 | 真实模型和云端上传待实机验证 |
| 裁剪/导出 | `cut.py`、`export/jianying.py` | 已核查 | 剪映实际导入为实机待验 |
| 任务中心 | `task_center/*`、`routes/tasks.py` | 已核查 | 生命周期、SQLite、执行器、SSE；任务锁清理记入 O-04 |
| HTTP API / Web UI | `ui/server.py`、`ui/routes/*` | 已核查 | 认证、Range、静态文件、元数据异常路径已覆盖 |
| 桌面壳 | `desktop/*` | 已核查 | 单实例与关闭策略有测试；pywebview 实机待验 |
| 日志与通知 | `log*.py`、通知路由/前端 | 已核查 | 历史日志与 SSE 分离 |
| 打包/发布 | `pyproject.toml`、`packaging/*`、CI | 已核查 | wheel 首次运行资源已实测 |
| 用户文档 | `README*`、`docs/cli-reference.md`、`ROADMAP.md` | 已核查 | CLI、配置示例与测试基线已同步 |

## 4. 确认问题与处置

| ID | 严重度 | 状态 | 问题 | 处置 |
|---|---|---|---|---|
| A-01 | 高 | 已修复 | `tokens` 未加载项目配置，`-p` 也不可用，统计会落到错误的 output 目录 | `tokens` 接入统一 IO 参数与项目配置加载 |
| A-02 | 中 | 已修复 | CLI `export` 把空 ffprobe 配置直接传入导出器，未自动发现 PATH 中的 ffprobe | 与 UI 一致使用 `resolve_binary` |
| A-03 | 中 | 已修复 | reindex 只剥离 `_segNN`，遗漏 `_partNN`、`_ptNN`、`_chunkNN`，可能拆散同一源视频分组 | 已复用统一分段后缀正则 |
| A-04 | 中 | 已修复 | UI 步骤检测与 texts 发现把自定义子目录截成 basename，`nested/deep` 等合法配置检测失败 | 已保留安全相对路径，并统一拒绝绝对/穿越路径 |
| A-05 | 中 | 已修复 | 默认 provider 删除仅在前端隐藏按钮，后端允许直接 DELETE 绕过 | 后端对 `gemini/openai/deepseek` 返回 409 |
| A-06 | 高 | 已修复 | 显式 `-p`/`-i` 指向不存在目录时静默回退 cwd，可能把产物写到错误位置 | 提前校验显式路径；错误返回 2，单文件命令仍允许文件 `-i` |
| A-07 | 高 | 已修复 | wheel 未携带首次运行所需的 `config.example.yaml` 与默认模板 | 新增 `share/clio` 资源布局、统一资源查找，并做 wheel 安装/首次生成实测 |
| A-08 | 中 | 已修复 | 自定义 `-c /other/config.yaml` 且未给 `-p` 时，强制 cwd 为项目目录，忽略同目录 project.yaml | 改为仅在显式/有效目录时覆盖 project_dir |
| A-09 | 中 | 已修复 | 选片、转录、规划仍只剥离 `_segNN`，其他 legacy 分段会错配或丢失转录 | 任务层统一复用 `SEGMENT_SUFFIX_RE`，移除重复死代码 |
| A-10 | 中 | 已修复 | UI 原始视频解析、运行预览和字幕查找仍只识别 `_segNN` | UI 层统一复用 `SEGMENT_SUFFIX_RE` |
| A-11 | 中 | 已修复 | 裁剪任务仍只识别 `_segNN`，legacy `_part/_pt/_chunk` 无法计算偏移、反解原始视频或记录正确状态 | 统一复用 `SEGMENT_SUFFIX_RE`，补充别名与大小写回归测试 |
| A-12 | 中 | 已修复 | 导出接口未校验格式，未知或非字符串格式会先创建异步任务再失败 | 在读取配置和提交任务前校验 `FORMAT_REGISTRY` |
| A-13 | 中 | 已修复 | 裁剪接口未校验 `output_dir` 类型，JSON 数字/对象会触发未捕获 `TypeError` | 仅接受 `null`、空字符串或字符串路径，否则返回 400 |
| A-14 | 中 | 已修复 | 侧栏标签只剥离 `_segNN`，natural/legacy 分段名仍显示伪后缀，且可能把自然 `_partNN` 当成分段 | 复用统一分段正则，并仅对可解析的 legacy 序号剥离显示后缀 |
| A-15 | 中 | 已修复 | 裁剪解析未优先使用 exact source stem，自然 `_partNN` 文件名会被错误归并到其他源视频 | 原始/压缩视频解析优先 exact match，再回退分段基名 |
| A-16 | 中 | 已修复 | 转录源视频解析同样未优先 exact stem，可能为自然 `_partNN` 视频选择错误源文件 | 与裁剪路径统一 exact-first 解析顺序 |
| A-17 | 中 | 已修复 | reindex 会把多个自然 `_partNN` 文件归并到同一源，产生错误分组 | 仅在同一精确源文件存在时才按分段后缀归并 |
| A-18 | 低 | 已修复 | provider 名称含 `-` 时前端生成非法环境变量名，API key 无法持久化或读取 | 环境变量名统一大写并把非字母数字转换为 `_` |
| A-19 | 中 | 已修复 | 删除自定义 `.env` key 后只更新文件，当前进程仍保留旧值，运行中的 provider 继续使用已删除配置 | 保存 `.env` 时同步清理进程环境中被移除的自定义 key |
| A-20 | 中 | 已修复 | `/api/run` 对未知 pipeline step 先登记任务再异步失败，任务中心产生无意义失败记录 | 白名单校验前置，未知步骤同步返回 400 |
| A-21 | 中 | 已修复 | 字幕样式并发保存失败时 waiter 被静默 resolve，前端仍显示已保存 | 失败传播到所有 waiter，队列恢复后可继续保存；补 Vitest 回归 |
| A-22 | 中 | 已修复 | 重跑接口拒绝项目子目录中的原片路径，合法的 `footage/foo.mp4` 无法重跑 | 允许安全的项目相对路径，继续拒绝绝对路径、盘符和 `..` 穿越 |
| A-23 | 低 | 已修复 | 选片结果合并按原字符串去重，Windows 下仅大小写不同的同一路径会重复入列 | 按大小写不敏感键去重，保留首次出现的路径拼写 |
| A-24 | 中 | 已修复 | 批量重链先用剥离 `NNN_` 前缀的 stem 匹配，自然文件名冲突时会误报歧义或选错目标 | 优先精确 basename，再回退剥离前缀的 stem |
| A-25 | 中 | 已修复 | `/api/fs/mkdir`、`/api/fs/reveal` 对非字符串字段调用路径方法，触发未捕获异常 | 标量字段类型校验前置，非法载荷返回 400 |
| A-26 | 中 | 已修复 | `/api/videos/relink` 未校验路径类型，非字符串载荷会抛出未捕获异常 | 校验 `path`/`old_path` 类型并捕获路径解析错误 |
| A-27 | 中 | 已修复 | 重链到项目内已选视频时仍会追加记录，产生重复视频条目 | 目标已存在时返回 409，不修改项目 |
| A-28 | 低 | 已修复 | `/api/env` 对非字符串 `content` 调用字符串方法，返回 500 | 保存前校验载荷类型并返回 400 |
| A-29 | 低 | 已修复 | `/api/refine` 对非字符串 `file`/`context` 调用字符串方法，返回 500 | 请求字段类型校验前置 |
| A-30 | 中 | 已修复 | `/api/rerun` 在类型/取值校验前登记任务，非法 `index`、`abspath` 等会造成无意义任务记录 | 提交任务前校验 `video`、`task`、`source`、`index`、`abspath` |
| A-31 | 中 | 已修复 | 项目创建、添加、移除、迁移和选择路由对非字符串字段处理不一致，可能触发 500 或误操作 | 统一类型校验，非法值返回 400 |
| A-32 | 低 | 已修复 | Provider 创建路由接受非字符串名称，后续配置写入行为异常 | 名称类型校验前置 |
| A-33 | 低 | 已修复 | Whisper 模型删除/绑定路由接受非字符串模型名 | 模型名类型校验前置 |
| A-34 | 中 | 已修复 | 文档推荐 `analyze --force`，但子命令解析器不接受该位置，已知可用写法被拒绝 | `--force` 支持全局和子命令两个位置 |
| A-35 | 中 | 已修复 | `refine` 不支持其他项目命令通用的 `-p/--project` 与 `-o/--output`，文档化用法不可执行 | 补齐项目选择与输出目录参数 |
| A-36 | 中 | 已修复 | 未注册的当前项目在列表回退分支复用循环变量 `p`，会 `UnboundLocalError` 或用错项目配置判断步骤 | 改用当前 `project_dir`，补嵌套子目录回归测试 |
| A-37 | 中 | 已修复 | `project.json` 根节点为非对象时，项目读取、更新和启动迁移触发 `AttributeError` | 统一安全读取，非对象按缺省元数据处理 |
| A-38 | 中 | 已修复 | `projects.json` 根节点为非对象时，项目列表/名称解析返回 500 | 统一走注册表校验并隔离，读取失败降级为空注册表 |
| A-39 | 中 | 已修复 | V1→V2 配置迁移读取非对象 `projects.json` 时中断 | 迁移读取增加结构校验并跳过无效条目 |
| A-40 | 低 | 已修复 | 选片任务匹配遇到非对象分析 JSON 时抛 `AttributeError`，拖垮流水线 | 非对象分析产物按不匹配处理 |

## 5. 优化 backlog

> 只记录不影响当前正确性的结构性改进；不把大重构混入本轮小修复提交。

| ID | 优先级 | 项目 | 说明 |
|---|---|---|---|
| O-02 | P2 | CLI 文档自动化 | 从 argparse 生成命令/参数清单或加入文档一致性测试，减少手写漂移 |
| O-03 | P2 | 外部服务契约测试 | 为 Gemini/DeepSeek/DashScope 增加可选 live smoke suite，默认 CI 跳过 |
| O-04 | P2 | 任务锁生命周期 | `TaskManager._task_locks` 不清理已完成任务的锁，长期运行会随历史任务数量增长 |
| O-05 | P3 | 桌面关闭确认 | `fetch_active_tasks` 只检查前 50 条，极端历史量下可能漏掉活跃任务 |
| O-06 | P1 | Provider 非 ASCII 名称 | 后端接受中文等非 ASCII provider 名，但前端对纯非 ASCII 名称统一生成 `CUSTOM_PROVIDER_API_KEY`，多个 provider 会共用/覆盖 key；需产品决策是拒绝名称还是生成稳定编码 |
| O-07 | P2 | Gemini 文件轮询韧性 | `_wait_for_file` 每轮只有单次 `files.get()`，长任务遇到一次瞬时网络错误即失败；可为瞬时错误增加有界重试 |
| O-08 | P3 | Trip context 缓存淘汰 | `_trip_context_cache` 以 project+mtime 为 key，修改文件后旧 key 不会淘汰，长期切换项目会缓慢增长 |
| O-09 | P3 | CLI 文档同步（已完成：`9332b5d`） | 已将 `-p/--project` 设为首选入口，补充命令总览、`doctor`、`desktop`、`tokens`、`migrate`、`migrate-config`、`compare-models` 和 `serve --token` |
| O-10 | P3 | 测试基线漂移（已完成：`a423ee8`） | `README*` 与 `ROADMAP.md` 已回填 `2171 passed / 12 skipped`、`561` Vitest |
| O-11 | P3 | 配置示例字段清单（已完成：`34b0d1d`） | `config.example.yaml` 头部全局 `ai` 字段补上 `default_tasks` |
| O-12 | P3 | 死条件清理 | `_list_projects` 的 `version` 条件表达式两个分支相同，可在后续纯重构中简化 |

## 6. 修复与提交记录

| Commit | 内容 | 验证 |
|---|---|---|
| `2d78fc7` | 初始化审计文档与功能矩阵 | 文档检查 |
| `b047d80` | `fix(cli): read token stats from project output` | `test_main.py` 14 passed；Ruff 通过 |
| `337626f` | `fix(cli): resolve ffprobe for export` | `test_main.py` 15 passed；Ruff 通过 |
| `a43476a` | `fix(reindex): recognize all legacy split suffixes` | `test_reindex.py` 16 passed；Ruff 通过 |
| `c95ea69` | `fix(ui): detect nested artifact subdirs` | `test_file_service.py` + `test_project_service.py` 136 passed；Ruff 通过 |
| `89cc29c` | `fix(config): protect built-in providers from deletion` | `test_routes_config.py` 47 passed；Ruff 通过 |
| `be627a7` | `fix(cli): reject invalid explicit project paths` | `test_main.py` 19 passed；Ruff 通过 |
| `0b241ba` | `fix(cli): honor sibling project config` | `test_main.py` 20 passed；Ruff 通过 |
| `37bcd3e` | `fix(packaging): bundle first-run config and templates` | 138 related tests passed；wheel 安装及首次配置生成实测通过 |
| `540d056` | `fix(tasks): strip all legacy split suffixes` | 65 related tests passed；Ruff 通过 |
| `dd8b9e2` | `fix(ui): match all legacy split suffixes` | 154 related tests passed；Ruff 通过 |
| `0363b23` | `fix(cut): recognize all legacy split suffixes` | `test_tasks_cut.py` 44 passed；Ruff 通过 |
| `3aa8b81` | `fix(export): reject unknown formats before submission` | `test_export*.py` 41 passed；Ruff 通过 |
| `a3ec111` | `fix(cut): validate output directory type` | `test_routes_plan.py` 25 passed；Ruff 通过 |
| `ba8d6ab` | `fix(cut): prefer exact original stems` | 相关后端测试通过；Ruff 通过 |
| `db388e2` | `fix(transcribe): prefer exact source stems` | 相关后端测试通过；Ruff 通过 |
| `701fb3c` | `fix(reindex): group natural part names by exact source` | 相关后端测试通过；Ruff 通过 |
| `fcbfb6f` | `fix(ui): strip legacy segment suffixes in sidebar labels` | 相关后端测试通过；Ruff 通过 |
| `927333d` | `fix(ui): normalize provider API key env names` | 相关前端测试通过；Ruff 通过 |
| `712eb31` | `fix(env): clear removed custom keys from process` | 相关后端测试通过；Ruff 通过 |
| `ca5713b` | `fix(run): reject unknown pipeline steps` | `test_routes_run.py` 55 passed；Ruff 通过 |
| `ee96a3f` | `fix(ui): surface subtitle save failures` | `subtitle-settings.test.js` 17 passed |
| `e8fdcef` | `fix(rerun): allow nested original video paths` | `test_routes_run.py` 55 passed；Ruff 通过 |
| `be780d2` | `docs(audit): record completed fixes and remaining risks` | 文档检查 |
| `11424e1` | `fix(ui): dedupe selected video paths case-insensitively` | `video-selection.test.js` 8 passed |
| `2ebb9a3` | `fix(ui): prefer exact names in batch relink matching` | `offline-media.test.js` 9 passed |
| `af3c550` | `fix(ui): reject non-string filesystem payloads` | `test_fs.py` 48 passed；Ruff 通过 |
| `409f021` | `fix(ui): validate video relink path types` | `test_routes_videos.py` 60 passed；Ruff 通过 |
| `9982060` | `fix(ui): reject relinking to an existing project video` | `test_routes_videos.py` 60 passed；Ruff 通过 |
| `32c18d7` | `fix(env): validate dotenv save payload type` | `test_routes_env.py` 11 passed, 1 skipped；Ruff 通过 |
| `c1b7733` | `fix(refine): validate request field types` | `test_refine_routes.py` 25 passed；Ruff 通过 |
| `e9c0dde` | `fix(rerun): validate rerun payload types` | `test_routes_run.py` 60 passed；Ruff 通过 |
| `ba29f39` | `fix(projects): validate project route payload types` | `test_routes_projects.py` 26 passed；Ruff 通过 |
| `74d7c1f` | `fix(config): validate provider name payload type` | `test_routes_config.py` 48 passed；Ruff 通过 |
| `ed941bd` | `fix(whisper): validate model route payload types` | `test_routes_transcripts.py` 63 passed；Ruff 通过 |
| `f38644f` | `fix(cli): accept force after subcommands` | `test_main.py` 22 passed；`analyze --force` 与 `--force analyze` 冒烟通过 |
| `197bfed` | `fix(cli): support project selection for refine` | `test_main.py` 22 passed；Ruff 通过 |
| `9332b5d` | `docs(cli): align command reference with current CLI` | 对照 argparse 帮助核对 |
| `30f2ff9` | `docs(audit): record route and CLI validation fixes` | 文档检查 |
| `9477b56` | `fix(projects): use current directory for fallback steps` | `test_project_service.py` 67 passed |
| `65ac069` | `fix(projects): tolerate non-object project metadata` | 相关项目/服务/启动测试 230 passed, 1 skipped |
| `e01218d` | `fix(projects): tolerate malformed project registry` | 项目服务/路由测试 100 passed |
| `34b0d1d` | `docs(config): document default task bindings in example` | 文档检查 |
| `38be846` | `fix(tasks): ignore non-object analysis artifacts` | `test_helpers.py` 33 passed |
| `e1bcb1a` | `fix(config): tolerate malformed registry during migration` | `test_config_v2.py` 44 passed |
| `a423ee8` | `docs: refresh test suite baselines` | 文档检查 |

## 7. 未实机验证项

- Gemini File API 上传、轮询、超时和真实 2.5 Flash 响应解析。
- DeepSeek/OpenAI-compatible 文本任务真实调用、限流和错误体。
- 阿里云 DashScope ASR + OSS 上传全链路。
- 本地 faster-whisper 模型下载、GPU/CPU 设备切换和长音频转录。
- 剪映/CapCut 对导出草稿的实际打开与导入。
- pywebview 窗口生命周期、单实例聚焦和系统文件对话框。
- 真实 GoPro 4K 素材的压缩质量、时长、旋转和音轨边界。

## 8. 收尾验证

最终自动化验证（2026-10-22，项目 `.venv` Python 3.11.9）：

| 项目 | 结果 |
|---|---|
| pytest | `2171 passed, 12 skipped`（2183 collected，68.37s） |
| 后端覆盖率 | `81.68%`（branch coverage，16022 statements） |
| Vitest | `561 passed`，`54` 个测试文件 |
| Ruff check | 通过 |
| Ruff format check | 通过（236 files） |
| mypy subset | 通过（39 source files） |
| CLI 冒烟 | `check` 与 `doctor` 正常退出，输出符合文档 |
| 工作区 | 所有修复均已本地提交；用户已授权推送 |

真实 Gemini/DeepSeek/阿里云调用、真实剪映导入、pywebview 窗口交互和真实长视频处理仍属于实机验证范围；对应风险已列入第 7 节。
