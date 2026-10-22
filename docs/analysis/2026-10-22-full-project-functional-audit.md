# 2026-10-22 全域功能审计与增量修复记录

> 状态：进行中。本文档随审计、修复和验证实时更新。
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
| CLI 参数与项目选择 | `clio/main.py` | 已核查 | 发现 token、export、显式项目路径问题 |
| 配置加载/校验/迁移 | `clio/config/*`、配置路由 | 已核查 | 默认 provider 删除保护待后端核验 |
| 压缩与媒体身份 | `compress.py`、`identity.py`、`vmeta.py` | 已核查 | ffmpeg/ffprobe 调用与 sidecar 契约 |
| 分析/口播/规划/refine | `tasks/*`、`pipeline.py`、`prompts.py` | 待深入 | AI 外部响应仅按 mock/错误分支核查 |
| ASR（本地/阿里云） | `asr/*`、`transcribe.py` | 待深入 | 真实模型和云端上传为实机待验 |
| 裁剪/导出 | `cut.py`、`export/jianying.py` | 已核查 | 剪映实际导入为实机待验 |
| 任务中心 | `task_center/*`、`routes/tasks.py` | 部分核查 | 生命周期、SQLite、执行器、SSE |
| HTTP API / Web UI | `ui/server.py`、`ui/routes/*` | 部分核查 | 认证、Range、静态文件与并发路径待继续 |
| 桌面壳 | `desktop/*` | 待深入 | 单实例、窗口关闭策略、pywebview 实机待验 |
| 日志与通知 | `log*.py`、通知路由/前端 | 已核查 | 历史日志与 SSE 分离 |
| 打包/发布 | `pyproject.toml`、`packaging/*`、CI | 待深入 | wheel 首次运行资源是重点风险 |
| 用户文档 | `README*`、`docs/cli-reference.md`、`ROADMAP.md` | 已核查 | 发现 CLI 与测试基线漂移 |

## 4. 确认问题与处置

| ID | 严重度 | 状态 | 问题 | 处置 |
|---|---|---|---|---|
| A-01 | 高 | 已修复 | `tokens` 未加载项目配置，`-p` 也不可用，统计会落到错误的 output 目录 | `tokens` 接入统一 IO 参数与项目配置加载 |
| A-02 | 中 | 已修复 | CLI `export` 把空 ffprobe 配置直接传入导出器，未自动发现 PATH 中的 ffprobe | 与 UI 一致使用 `resolve_binary` |
| A-03 | 中 | 已修复 | reindex 只剥离 `_segNN`，遗漏 `_partNN`、`_ptNN`、`_chunkNN`，可能拆散同一源视频分组 | 已复用统一分段后缀正则 |
| A-04 | 中 | 已修复 | UI 步骤检测与 texts 发现把自定义子目录截成 basename，`nested/deep` 等合法配置检测失败 | 已保留安全相对路径，并统一拒绝绝对/穿越路径 |
| A-05 | 中 | 已修复 | 默认 provider 删除仅在前端隐藏按钮，后端允许直接 DELETE 绕过 | 后端对 `gemini/openai/deepseek` 返回 409 |
| A-06 | 高 | 待验证 | 显式 `-p`/`-i` 指向不存在目录时静默回退 cwd，可能把产物写到错误位置 | 核查单文件 `-i` 后改为显式报错 |
| A-07 | 高 | 待验证 | wheel 可能未携带首次运行所需的 `config.example.yaml` 与默认模板 | 构建 wheel 实测；大改则进入优化 backlog |

## 5. 优化 backlog

> 只记录不影响当前正确性的结构性改进；不把大重构混入本轮小修复提交。

| ID | 优先级 | 项目 | 说明 |
|---|---|---|---|
| O-01 | P1 | 安装包资源布局 | 统一首次运行配置/模板的包内查找与 wheel 资源声明，并加入打包冒烟测试 |
| O-02 | P2 | CLI 文档自动化 | 从 argparse 生成命令/参数清单或加入文档一致性测试，减少手写漂移 |
| O-03 | P2 | 外部服务契约测试 | 为 Gemini/DeepSeek/DashScope 增加可选 live smoke suite，默认 CI 跳过 |

## 6. 修复与提交记录

| Commit | 内容 | 验证 |
|---|---|---|
| `2d78fc7` | 初始化审计文档与功能矩阵 | 文档检查 |
| `b047d80` | `fix(cli): read token stats from project output` | `test_main.py` 14 passed；Ruff 通过 |
| `337626f` | `fix(cli): resolve ffprobe for export` | `test_main.py` 15 passed；Ruff 通过 |
| `a43476a` | `fix(reindex): recognize all legacy split suffixes` | `test_reindex.py` 16 passed；Ruff 通过 |
| `c95ea69` | `fix(ui): detect nested artifact subdirs` | `test_file_service.py` + `test_project_service.py` 136 passed；Ruff 通过 |
| 待填 | `fix(config): protect built-in providers from deletion` | `test_routes_config.py` 47 passed；Ruff 通过 |

## 7. 未实机验证项

- Gemini File API 上传、轮询、超时和真实 2.5 Flash 响应解析。
- DeepSeek/OpenAI-compatible 文本任务真实调用、限流和错误体。
- 阿里云 DashScope ASR + OSS 上传全链路。
- 本地 faster-whisper 模型下载、GPU/CPU 设备切换和长音频转录。
- 剪映/CapCut 对导出草稿的实际打开与导入。
- pywebview 窗口生命周期、单实例聚焦和系统文件对话框。
- 真实 GoPro 4K 素材的压缩质量、时长、旋转和音轨边界。

## 8. 收尾验证

待全部修复完成后回填最终测试、覆盖率、Ruff、mypy 与工作区状态。
