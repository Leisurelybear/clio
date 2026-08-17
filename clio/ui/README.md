# 可视化编辑 UI

本地 web 工具，在浏览器里看视频、读 AI 输出、就地修改保存。

## 启动

```bash
.\.venv\Scripts\python.exe main.py serve
# 默认 http://127.0.0.1:8765/ ，自动打开浏览器
```

常用参数：

```bash
python main.py serve --port 9000        # 换端口
python main.py serve --no-browser       # 不开浏览器（远程机调试）
python main.py serve --host 0.0.0.0     # 暴露到局域网（注意安全）
```

按 `Ctrl+C` 退出。

## 文件与目录选择

所有“浏览”按钮共享同一套选择逻辑：

- 在 pywebview 桌面版中优先使用操作系统原生对话框；原生接口不可用时回退到 Tk 对话框。
- 在浏览器版中打开应用内文件浏览器，可切换 Windows 磁盘或受限目录、进入子目录、手输路径、刷新和新建文件夹。
- “添加视频”支持在同一窗口多选；配置中的 ffmpeg/ffprobe、模板、项目目录、输出目录、Whisper 缓存目录和剪裁/重新关联目录都使用同一入口。
- 浏览器版选择的是运行 Clio 的服务器文件系统，不是浏览器所在设备的本地文件系统；仍可直接在输入框粘贴绝对路径作为备用方式。
- 视频操作菜单中的“在文件管理器中显示”会打开所在目录并定位文件；项目目录和裁剪输出仍可直接打开目录。

## 界面布局

侧栏分两段：**项目**（跨视频的产物）放上面，**视频**（per-video 产物）放下面。
点 sidebar 的项目条目会切换右栏内容；点视频条目会切换右栏 + 播放器。
⚙ 设置也是项目级入口：点开后右侧渲染完整 config 编辑表单。
▶ 运行：支持多步骤流水线（压缩→分析→口播→vlog 剪辑规划→标号），实时进度 + ETA。
日志和统计也是项目级入口：日志面板实时读取服务日志，统计面板汇总 AI token 用量。**任务**入口是跨项目的统一任务管理中心，集中展示流水线、重跑、裁剪导出、Whisper 安装和波形生成的状态与执行事件。

```
┌────────────────────────────────────────────┐
│ 项目: E:\Videos\Franch2 [压缩|原视频] [重新加载] │
├──────────┬──────────────────┬──────────────┤
│ 项目      │  视频播放器       │  视频模式    │
│ 编排  1   │  ▶ 00:00 / 00:42 │ ┌ 分析 ┐     │
│ 设置  2   │                  │ │ 摘要 │     │
│ 运行  3   │                  │ │ 时间轴│     │
│ 日志  4   │                  │ └──────┘     │
│ 统计  5   │                  │ ┌ 口播 ┐     │
│ ────────  │                  │ ┌ 转录 ┐     │
│ 视频      │                  │   [保存]     │
│ [001]xxx  │                  │              │
│ [002]yyy  │                  │              │
└──────────┴──────────────────┴──────────────┘
```

点 📋 规划时，tab 栏隐藏，右栏整块渲染规划面板 + 保存按钮；播放器保持上一个视频：

```
┌──────────┬──────────────────┬──────────────┐
│ 📋 规划 ●│  视频播放器       │  规划模式    │
│ ⚙ 设置   │  (上一个视频)     │ ┌ 主题 ──┐    │
│ ▶ 运行   │                  │ │        │    │
│          │                  │ └────────┘    │
│ 视频      │                  │ ┌ 顺序 ──┐    │
│ [001]xxx  │                  │ │ seg 1  │    │
│ [002]yyy  │                  │ │ seg 2  │    │
│          │                  │ └────────┘    │
│          │                  │   [保存]     │
└──────────┴──────────────────┴──────────────┘
```

点 ⚙ 设置时，右栏渲染完整 config 嵌套表单（paths / ai / compress / analyze / script / plan 等全部字段），每个配置分区包裹在可折叠的卡片中，左侧带 accent 色条标识层级。
- 编辑**全局 config.yaml**（全局 tab）：保存后需重启服务生效。
- 编辑**项目 project.yaml**（项目 tab）：保存后立即生效。
- 编辑**项目专属 project.yaml**（通过 `?project=X` 切换）：保存后立即生效，下次流水线运行自动加载新配置。
校验失败（如 provider 拼写错误）时弹出错误红字，不写文件。

Provider registry also has focused backend endpoints for integrations and future UI refactors:

- `GET /api/providers` returns global `ai.providers`.
- `POST /api/providers` creates a provider from a JSON body containing `name`, `type`, `api_key_env`, `base_url`, `models`, and `capabilities`.
- `PUT /api/providers/{name}` creates or updates one provider.
- `DELETE /api/providers/{name}` removes one provider from global `config.yaml`.

These endpoints require the same API token as other sensitive config routes and invalidate the server config cache after writes.

## 侧边栏 (Sidebar)

顶部与编辑器导航区域保持固定紧凑：

- 全局顶部的 **当前项目** 选择器负责切换项目、创建项目和打开当前项目目录。项目名称作为主信息显示，完整路径只在菜单底部和 tooltip 中作为辅助信息；没有项目时“打开项目目录”会禁用。
- 右侧编辑器顶部是一行 **编排 / 设置 / 运行 / 日志 / 统计 / 任务** 工作区导航，使用图标 + 中文名称显示当前入口。数字快捷键仍可用（`Ctrl+1` 到 `Ctrl+6`），但不再占用导航空间；旁边的折叠按钮为 `Ctrl+\`。
- 左侧栏折叠按钮为 `Ctrl+B`。收起后把手顶部会出现 `›` / `‹` 箭头，点击可展开。

### 统一任务中心

任务中心从 `GET /api/tasks` 加载跨项目任务（包含后台维护任务），并通过
`GET /api/tasks/stream?after=<seq>` 持续接收全局事件。列表支持按状态、类型和项目筛选；详情显示阶段进度、错误和事件时间线。对可取消任务可直接发起取消，对失败或中断任务可创建重试任务。任务记录由服务端 SQLite 持久化，页面或项目切换不会丢失历史；运行面板仍保留轻量实时进度，并提供“在任务中心查看”入口。

### 通知中心

顶部铃铛打开全局通知收件箱。任务完成、失败、中断、取消，以及任务中的警告/错误事件会由后端直接写入 Task Center SQLite；现有状态栏消息、toast 和运行环境 warning 也通过 `POST /api/notifications` 注册。收件箱通过 `GET /api/notifications/stream?after=<seq>` 实时更新，支持全部/未读/警告错误筛选、服务端分页、单条已读、全部已读和跳转到关联任务。切换筛选不会关闭面板；通知独立于当前项目和页面，关闭提示或切换项目不会丢失。

视频列表上方是**搜索 + 状态筛选**：

- 搜索框按 `index / 文件名(去序号前缀) / 标题` 不区分大小写子串过滤。
- 状态 chips（`缺分析 / 缺口播 / 缺转录 / 离线`；原视频视图另有 `未压缩`）点击即只显示缺少该阶段的视频；`全部` 取消。
- 列表标题显示 `(可见数/总数)`。

视频行用 **4 个小圆点**（压缩/分析/口播/转录）替代文字角标，圆点 = 该制品文件（`text_json`/`script_json`/`transcript_file`，原视图压缩另看 `match`）已生成。

列表底部的**阶段计数条**按每个视频自己的制品字段精确统计（替代旧的按目录扫描的 `流水线` 列表）：

- 每格显示 `完成数/总数`，点击 = 只看该阶段**已完成**的视频；
- 想筛"缺少"用上面的 chips（两者语义互补，高亮互斥）；
- 测试全绿后底部清零（0 完成）的格子不可点。

切换 `压缩 / 原视频` 源时会自动清空搜索和已选阶段筛选。

## 数据来源

UI 只读 / 写 `config.yaml` 里 `paths.output_dir` 下的文件：

| 入口 | 路径 | 文件 | 字段 |
| --- | --- | --- | --- |
| 分析 (texts) | sidebar → 视频 → tab「分析」 | `output/texts*/*.json` | `title`, `location`, `mood`, `summary`, `timeline[]`，可含同期声 `transcript` |
| 口播 (scripts) | sidebar → 视频 → tab「口播」 | `output/scripts/*_voiceover.json` | `title`, `voiceover`, `edit_tip`, `duration_hint_sec` |
| 转录 (transcript) | sidebar → 视频 → tab「转录」 | `output/transcripts/*.json` | `segments[]`，支持手动添加、编辑、删除 |
| 规划 (plan) | sidebar → 📋 规划 | `output/plans/day<N>_plan.json` | `day_title`, `theme`, `opening_tip`, `ending_tip`, `sequence[]`（可编辑，见下） |
| 设置 (config) | sidebar → ⚙ 设置 → 项目 tab | `project.yaml` | 项目级字段，嵌套表单渲染 |
| 设置 (config) | sidebar → ⚙ 设置 → 全局 tab | `config.yaml`（global-only 字段） | 保存后需重启服务 |
| 设置 (config) | sidebar → ⚙ 设置 → 合并视图 tab | 合并后配置 | 只读查看全局+项目字段来源 |
| 日志 (logs) | sidebar → **日志** | 内存会话缓冲（`session_log`）+ 磁盘 `logs/YYYY-MM-DD-HH.log` | 见下方「会话日志」 |
| 统计 (tokens) | sidebar → 统计 | `output/token_usage.json` | 总 token、按模型、按任务、最近 100 条历史 |
| 多项目 | sidebar 顶部选择器 / URL `?project=name` | 自动发现 `project.json` | 支持新建、打开、切换 |

`texts*` 通配同时匹配 `texts/` 和 `texts - 巴黎/` 之类的目录。

## 会话日志 (R-027)

侧栏 **日志** 读服务端内存会话缓冲（`GET /api/logs?offset=`），与磁盘 `logs/YYYY-MM-DD-HH.log` 同源写入（`print` / pipeline 进度经 Tee 进入缓冲）。

**已有能力：**

| 能力 | 说明 |
| --- | --- |
| 自动刷新 | 每 2s 增量拉取；自动滚动可关 |
| 清空 | `POST /api/logs/clear` 清空服务端缓冲；本地过滤条件保留 |
| 关键字过滤 | 工具栏搜索框，不区分大小写子串（客户端） |
| 等级 chips | 全部 / 信息 / 警告 / 错误 |
| 等级徽章 | 每行推断 severity 并着色 |
| 匹配计数 | `显示 N / M` |

**等级推断**（纯客户端 `logs-filter.js`，非结构化 JSON 日志）：

1. 显式 `[ERROR]` / `[WARN]` / `[INFO]` / `[DEBUG]`
2. Traceback / ✗ / 失败 / Exception / `HTTP 5xx` → 错误
3. ⚠ / 跳过 / skip → 警告
4. 其它 → 信息

**已排期后续（见 `ROADMAP.md` R-027）：**

| ID | 能力 | 说明 |
| --- | --- | --- |
| R-027d | **时间展示 + 按时间筛选** | 行首/列显示 `HH:MM:SS`；快捷「最近 5/15/60 分钟」与 from–to；与关键字/等级 AND |
| R-027e | **加载磁盘历史日志** | 列 `logs/YYYY-MM-DD-HH.log`，选择小时文件或时间范围读入面板（限大小）；历史模式暂停 live tail |

**其它可选（未排期）：** 复制/导出过滤结果；手动暂停刷新；步骤 chips；≥ 等级；打开 `logs/` 目录；跨项目分桶；R-027c 服务端 `?q=`。

## 视频源切换 (Source Toggle)

header 右侧的 **`压缩` / `原视频`** 切换按钮决定侧栏列的是哪一边：

- **压缩**：列 `output/compressed/` 下的 640p 视频（默认）。适合看 AI 标注的时间码在压缩版上对不对。
- **原视频**：列项目 `videos.json` 中选中的 4K 原始素材（可来自任意磁盘路径）。适合看真实细节 / 选镜头。

每个视频条目都带一个 match 角标，标出对应的另一边文件名：

```
[001] GL010695  → 压: 001_GL010695.mp4     ← 压缩视图，对应原视频
[002] GL010741  → 压: 002_GL010741.mp4
```
```
[001] GL010695.MP4  → 原: GL010695.MP4      ← 原视频视图，对应压缩版
[002] GL010741.MP4  → 原: GL010741.MP4
```

**匹配规则**（大小写不敏感）：

- 压缩 → 原：剥掉 `001_` 之类的前缀，在 `videos.json` / `.vmeta.source_path` 里找同 stem 的文件
- 原 → 压：在 `output/compressed/` 里找 `*_<原 stem>.mp4`（必须带 `_<index>_` 前缀）

**边角情况**：

- 某一边没找到对应 → 角标显示 `无对应` 且整行变暗；点进去 `texts` / `口播` tab 会显示"没有对应 JSON"
- 在 `texts` / `口播` tab 有未保存改动时切换源 → 弹确认框，避免丢改动
- `规划 (plan)` tab 不受源影响，按 `sequence[].index` 在当前视图里找对应视频并跳转
- **在规划视图（sidebar 📋 规划 激活）下点 header 的源切换**：仅刷新视频列表 + 清空播放器，**不会**把视图切回视频模式（规划 vs 视频是两个独立工作区）。要回到视频模式，点 sidebar 的某个视频条目即可

## 规划结构编辑与导出检查 (R-026 + R-030)

进入 sidebar **📋 规划** 后，可在不重跑 AI 的情况下改结构。

**列表密度 (R-030):** 默认折叠为一行（序号 · 标题 · 时间轴 · 视频 index · ▸）；**同时最多展开一段**。点行头 = 展开 + 跳预览；点 chevron 仅展开/收起。结构按钮（↑↓ / 插入 / 删除）与字段编辑在展开面板内。预览切到某段时会自动展开该段（编辑另一段输入框时暂缓，blur 后对齐）。

| 操作 | 说明 |
| --- | --- |
| 拖拽 / ↑↓ | 调整 `sequence[]` 顺序（↑↓ 在展开面板） |
| 删除 | 删除某一段（确认后；展开面板内） |
| +插入 / 末尾插入 | 从项目已有视频列表选择后插入新段（可搜索 index / 标题 / 文件名） |
| 选区 | 弹窗内独立源视频 + 双端拖拽选 `use_timeline`（plan 时间基，含 `offset_sec`）；主成片预览不被改写 |
| 标题 / 时间轴 / 理由 / 口播提示 | 展开后改字段；时间轴手写 `MM:SS-MM:SS`；理由/口播为可纵向拖高的 textarea；`Ctrl+S` 保存（后端校验非法时间轴） |
| 就绪检查面板 | `POST /api/plan/readiness`：error 阻塞裁剪/导出；warning 可确认后 `force` 继续；点击带 `segment_index` 的 issue 会展开并滚到该段 |
| 未保存改动 | 裁剪/导出前会提示先保存（不静默写盘） |

CLI 对齐：`python main.py cut|export ... --force` 仅忽略 **warning**，**error** 仍阻止。

> 不做「单段 AI regenerate」：需要重写文案时改字段或整日重跑 `plan`。

## 规划预览播放

在播放器下方有规划预览条；进入「编排」且有 `sequence` 后，预览条为**成片全局时间轴**（R-031a）：

- 总时长 = Σ 各段 `use_timeline` 时长；**拖动**进度条跳到任意成片秒（主交互）
- 进度条为经典形态：**已播填充** + 带顶帽的 playhead；段界细线（非整块高亮）
- 分段色块仍保留；**点击色块**跳到该段起点；播放/上一段/下一段沿全局轴连续预览
- 播放器时钟显示 **`成片 mm:ss / 总 mm:ss`**
- **波形**按 plan 把各源视频 peaks 按 `use_timeline` 切片拼接（成片轴）；拖波形同样 `seekToGlobal`。预览切段不重载单片波形
- 当前媒体仍是**源视频 seek hop**（按段加载对应源片并 seek；`offset_sec` 参与预览 seek）；Cut/合成片优先见 ROADMAP **R-031b**
- 当前定位的 segment 会在规划列表（展开）和预览条里高亮（不必先点「预览播放」）
- 再次点击播放按钮可暂停；播完所有 segment 后自动停止
- 切换到视频 / 运行 / 设置 tab 或切换源时自动停止连续预览，并恢复单片波形

## 播放速度

播放器下方的时间栏右侧有速度选择器，支持：
`0.5x` / `0.75x` / `1x` / `1.25x` / `1.5x` / `2x`

预览播放时也可调整速度，适合快速浏览编排效果。

## 快捷键

- `Ctrl+S` — 保存当前 tab 的修改
- `Ctrl+1` ~ `Ctrl+6` — 切换项目区入口：编排 / 设置 / 运行 / 日志 / 统计 / 任务
- `Ctrl+B` — 折叠 / 展开左侧栏
- `Ctrl+\` — 折叠 / 展开右侧编辑栏
- `Escape` — 关闭打开中的 modal；编辑转录文本时结束编辑
- 点 timeline / plan 的 segment — 视频跳到对应时间

## 安全

- 默认仅监听 `127.0.0.1`，不暴露到局域网
- 使用 `--host 0.0.0.0` 暴露到局域网时，服务会自动生成访问 token；终端会打印带 `?token=` 的 Token URL，前端也会把 token 写入 `Authorization: Bearer ...`
- 也可以通过 `--token <value>` 显式指定 token；远程访问时不要把 token URL 发给不可信设备
- 目录/文件浏览接口 `/api/fs/dirs`、`/api/fs/entries` 和定位接口 `/api/fs/reveal` 复用路径白名单：非 Windows 只允许用户主目录，Windows 允许带盘符的本地路径；敏感服务启用 Token 鉴权
- 所有文件 IO 沙盒在 `output_dir` 内：basename 不允许 `/` `\` `..`
- 写入采用 atomic rename (写 `.tmp` 然后 `os.replace`)，不会留下半截文件
- 首次覆盖某个文件时自动创建 `*.bak` 备份（已存在则不覆盖）

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 启动报 `Address already in use` | 换端口：`--port 9000`，或杀掉占用进程 |
| 切到原视频视图后列表全空 | 项目尚无 `videos.json` 或列表为空。点击侧栏「添加视频」勾选素材，或对旧项目运行 `python main.py migrate` |
| 浏览器打开空白 | 看终端输出 + `logs/YYYY-MM-DD-HH.log` |
| `texts` tab 一直说"没有 JSON" | 视频列表里该行 `texts` 状态是 `·` 灰色；说明 `output/texts*` 下没匹配文件 |
| 保存后 clip 看到旧内容 | 按浏览器 `Ctrl+Shift+R` 强刷；服务器 `/api/videos` 走的是缓存头 `no-store`，但浏览器可能缓了 JSON |
| 设置 tab 显示"配置数据不可用" | 当前项目没有 `project.yaml` 且全局 `config.yaml` 读取失败；检查 config.yaml 是否存在并格式正确 |
| 切换项目后 AI 行为没变 | 检查项目目录下是否有 `project.yaml`；没有则使用全局 config.yaml 的 AI 配置 |

## Prompt Management

Settings includes a `Prompts` sub-tab for editing AI prompt templates from the browser.

- The list shows every built-in prompt and whether the current project has an override.
- Saving writes a project-level file under `<project_dir>/templates/prompts/{PROMPT_NAME}.md`.
- Restore deletes project-level overrides for that prompt; repo-level overrides, if present, still apply.
- The next AI call uses the updated prompt automatically.

## Run Panel Input Directory

The Run panel has a per-run input directory field with a browse button.

- The value is sent only with the current run request and does not modify `project.yaml`.
- If no files are selected in the sidebar, the selected steps process all videos in that directory.
- If sidebar file selection is active, only the selected filenames are passed to the run request.

After a run finishes while the Run panel is still open, the UI switches to the most relevant result view:

- `plan` opens the generated plan for the selected day.
- `voiceover`, `transcribe`, and `analyze` open the compressed video detail view on the matching tab.
- `compress` and `label` open the compressed video list.
