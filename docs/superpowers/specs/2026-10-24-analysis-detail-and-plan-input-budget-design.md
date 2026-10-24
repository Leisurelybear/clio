# 分析细节与 Plan 输入预算设计

> 日期: 2026-10-24
> 状态: 待审阅
> 范围: `clio/ai/gemini.py`、`clio/analyze.py`、`clio/analyze_windows.py`、`clio/tasks/plan.py`、`clio/prompts.py`、配置模型与示例
> 目标: 提高视频分析的画面细节，同时让 plan 输入量不随细节线性膨胀

## 1. 问题与证据

用户痛点：视频分析不够细，导致 plan AI 抓不到重点；但继续增加文字会让 prompt 更臃肿。

对 `D:\vlog_projects\France\2025-France-d1` 的实际数据检查（142 个素材、466.5 分钟）：

| 指标 | 实测 | 期望 |
|---|---|---|
| timeline 条目 | 579 条 | prompt 要求 5-10 秒一条 |
| 平均时间粒度 | 48.3 秒/条 | 5-10 秒/条 |
| 长素材 009（1602 秒） | 12 条 | 约 160-320 条 |
| 单次分析 completion | 200-570 tokens | 未被 `max_tokens` 截断，说明模型在主动概括 |
| plan prompt | 141,028 tokens | 与素材量无关的稳定预算 |
| 视觉 timeline 字符 | 约 49k | - |
| 内联转录字符 | 约 107k | 不应重复进入 plan |

Gemini 的 `video_analyze` prompt_tokens 几乎精确等于 `64 × 视频秒数 + 提示词`：

| 窗口时长 | prompt_tokens | 推算 |
|---|---|---|
| 900s | 60,190 | 900 × 64 + 约 0.6k |
| 663s | 42,436 | 663 × 64 + 约 0.6k |
| 439s | 28,114 | 439 × 64 + 约 0.6k |

结论：当前 Gemini 使用 **media_resolution=LOW**，每帧只保留 64 个视觉 token。画面本身被分析得“很粗”，这与 timeline 输出粗是两件独立的事。

## 2. 根因

1. **视觉采样密度不足**：`clio/ai/gemini.py` 只传 `max_output_tokens`，没有传 `media_resolution`，Gemini 按低分辨率理解视频。640p 的压缩片被进一步压成每帧 64 tokens，招牌、食物、动作等细节丢失。
2. **分析输出缺少密度契约与显著性字段**：`ANALYZE_PROMPT` 要求“5-10 秒一条、每条 2-3 句、最多 60 条”，在 15 分钟窗口下自相矛盾（90-180 条才够）。没有最低覆盖要求，也没有 `score`，模型自然退化成摘要。
3. **plan 输入没有投影层**：`clio/tasks/plan.py` 把完整 `timeline`（含 `transcript` / `transcript_segments`）原样塞进 `clips_json`，同时 `TRANSCRIPT_CONTEXT` 又发送一遍转录。142 个素材全部进入 plan，其中真正的视觉信息只占约 15%。

## 3. 目标与非目标

### 目标

- 让 Gemini 实际看到更多画面细节（提高每帧视觉 token）。
- 分析输出有可执行的密度契约：时间覆盖完整、每条短而具体、行数可预测。
- 分析结果新增显著性字段，让 plan 能排序而不是“猜重点”。
- plan 输入与完整 timeline 解耦：无论 timeline 多细，plan 输入按 `片段数 × 常数` 封顶。
- 旧分析 JSON 继续可用，不要求一次性迁移。

### 非目标

- 不改变“一天 = 一条 vlog”的产品模型，不新增多日自动分组。
- 不替换 Gemini，不新增视频 provider。
- 不做逐帧视觉理解或本地 CV 场景检测（可作为后续独立项目）。
- 不改变 cut / export / preview 的现有行为。

## 4. 总体设计

三个独立杠杆，分别控制“看多细”“写多细”“喂多少”：

```text
原片 640p
  │  compress (fps 现状不变)
  ▼
compressed.mp4 ──► Gemini analyze
                   ├─ Layer 1 视觉密度: media_resolution = medium
                   ├─ Layer 2 输出密度: 8-12s/行, 短槽位 + score + key_moments
                   ▼
              texts/*.json (完整细节, 给口播/剪辑/预览)
                   │
                   ▼
          plan_input.build_plan_clips()
          ├─ 去掉内联 transcript
          ├─ 每素材 top-K timeline + key_moments
          └─ 素材数超限时两阶段筛选
                   ▼
              PLAN_PROMPT（有界输入）
```

原则：

- **完整细节只落盘，不直接进 plan**。
- **plan 只消费排序后的投影**。
- **所有预算显式化**：行数上限、每行字数、K 值、候选素材数。

## 5. Layer 1：视觉采样密度

### 5.1 新配置

项目级 `analyze.media_resolution`：

| 值 | 含义 | 每帧 token（Gemini 视频） | 适用 |
|---|---|---|---|
| `low` | 现状，兼容模式 | 64 | 省成本、快速预览 |
| `medium` | 默认 | 约 256 | 640p/1280p 分析 |
| `high` | 高细节 | 更高（以 A/B 实测为准） | 仅当压缩宽度 ≥1280 |

默认 `medium`。`low` 作为显式回退，保证用户可以恢复旧行为。

### 5.2 透传链路

```text
AnalyzeConfig.media_resolution
  └─ clio.analyze.analyze_video(..., media_resolution=...)
       └─ provider.analyze_video(..., media_resolution=...)
            └─ GeminiProvider._config_for_video()
                 └─ types.GenerateContentConfig(media_resolution=...)
```

- `VideoAIProvider.analyze_video` 增加可选关键字参数 `media_resolution: str | None = None`。
- `GeminiProvider` 不修改共享的 `_generation_config`；每次视频调用基于它复制一份并覆盖 `media_resolution`，保证文本任务不受影响。
- 值映射：`low/medium/high` → `types.MediaResolution.MEDIA_RESOLUTION_LOW/MEDIUM/HIGH`。
- 仅 Gemini 支持；其他 provider 不会被选为 `video_analyze`（现有 capability 过滤）。
- 如果模型不支持该参数，API 会返回错误。**不做静默降级**，错误信息提示用户设置 `analyze.media_resolution: low` 或更换模型。

### 5.3 成本与窗口

- `medium` 约 4 倍视觉 token。15 分钟窗口从约 58k 升到约 230k，仍在主流 Gemini 1M 上下文内。
- 推荐配置：`media_resolution: medium` + `window_max_min: 5`。单次窗口约 77k token，与现状同量级；总量仍约 4 倍，但每个窗口的注意力更集中，模型更容易按密度要求输出。
- **分辨率是上限**：640p + medium 只提高每帧 token，不能恢复压缩时丢掉的招牌/食物纹理。若目标是这些细节，建议同时把 `compress.max_width` 提到 960-1280、`compress.fps` 提到 2-5，并观察压缩文件大小。
- 不强制改默认窗口或压缩参数，避免一次升级同时改变切片、画质和费用；文档与示例给出推荐组合。
- 若 `compress.fps < 2` 且 `media_resolution != low`，分析日志给出一次警告：1 fps 限制了快速动作的时间细节，建议 2-5 fps（不改 compress 行为）。

## 6. Layer 2：分析输出契约

### 6.1 timeline 条目

保持 `start` / `end` / `description` 必填，新增可选字段：

```json
{
  "start": "00:12",
  "end": "00:21",
  "description": "人物在集市摊位前挑选奶酪，镜头缓慢横移，招牌写着 FROMAGE",
  "score": 4,
  "camera": "pan",
  "quality": "ok",
  "tags": ["food", "market", "signage"]
}
```

- `description`：一句话，**≤40 个中文字符**，包含主体动作 + 镜头 + 场景/画面文字 + 明显互动。
- `score`：1-5。5 = 封面级；4 = 强亮点；3 = 可用；2 = 填充；1 = 抖动/过暗/无信息。用于 plan 排序。
- `camera`：`fixed | handheld | pan | zoom | follow`。
- `quality`：`ok | shaky | dark | blocked`。
- `tags`：短标签数组，自由词但要求复用少量高频词（食物、街景、建筑、人物互动、交通、夜景等）。

### 6.2 密度契约

`ANALYZE_PROMPT` 改为按窗口时长给目标，而不是固定 60 条：

| 窗口时长 | 目标行数 | 最大行数 |
|---|---|---|
| ≤5 分钟 | 25-40 | 60 |
| 5-10 分钟 | 50-80 | 120 |
| 10-15 分钟 | 75-120 | 180 |

规则：

- 有动作/事件/镜头变化时 **8-12 秒一行**；连续静态画面最多 20 秒一行。
- 必须覆盖到窗口结尾；允许跳过抖动/遮挡段，但要在相邻条目注明。
- 不再要求“每条 2-3 句”；改为固定短句，避免散文膨胀。
- 达到最大行数时，优先保留 `score >= 4` 的条目并缩短静态段落，而不是整体概括。

### 6.3 key_moments

新增顶层字段，作为 plan 的第一优先级输入：

```json
"key_moments": [
  {
    "start": "00:12",
    "end": "00:21",
    "title": "集市挑奶酪",
    "why": "人物互动 + 招牌文字 + 稳定横移",
    "score": 4
  }
]
```

- 每素材 3-8 条，必须是 timeline 中真实存在的区间。
- `why` 写画面依据，不写空泛评价。
- `score` 与对应 timeline 行一致或更高。

### 6.4 校验与合并

- `_validate_analysis`：
  - `key_moments` 缺失时补 `[]`；只保留 dict 条目。
  - `score` 限制在 1-5；非法值丢弃该字段而不是伪造。
  - `tags` 只保留字符串；`camera` / `quality` 只保留白名单值。
  - 旧 JSON 没有这些字段时完全兼容。
- `merge_window_analyses`：
  - `key_moments` 拼接、按 `start` 排序，在 overlap 区内按时间接近 + 文本相似去重。
  - 同一条 timeline 重复时保留字段更完整、`score` 更高的一条；`tags` 取并集。
  - `shift_analysis_times` 已覆盖 `start` / `end`，无需额外逻辑。
- 覆盖率观测（不阻断）：
  - 分析完成后计算 `coverage = 已覆盖秒数 / 窗口秒数`、`max_gap`。
  - 日志输出 `[analyze] <name>: rows=N coverage=xx% max_gap=Ns`。
  - P0 只告警；P2 再考虑对稀疏窗口做一次“补全缺失区间”的重试。

## 7. Layer 3：Plan 输入投影

### 7.1 新模块

新增 `clio/plan_input.py`，唯一职责：把完整的 analysis clip 转成 plan 可消费的投影。`clio/tasks/plan.py` 在调用 `plan_daily_vlog` 前调用它，plan prompt 不再接触原始 timeline。

```python
def build_plan_clips(clips: list[dict], config: AppConfig) -> list[dict]: ...
```

### 7.2 投影字段

每个素材只保留：

- `index`、`title`、`summary`、`location`、`mood`、`suggested_use`、`duration_sec`
- `highlights`（最多 `plan.max_key_moments_per_clip` 条）
- `key_moments`（最多同上限；旧 JSON 从 highlights 派生）
- `timeline`：最多 `plan.max_timeline_rows_per_clip` 条，且**删除 `transcript` / `transcript_segments`**
- 既有定位字段：`source_stem`、`match_stem`、`segment_offset_sec`

`duration_sec` 取值顺序：最后一个 `analyze_windows.end_sec` → timeline 最大 `end` → `0`。该字段让模型能在选片时感知素材长度。

转录只通过现有 `TRANSCRIPT_CONTEXT` 进入 plan，并且受 `plan.use_transcripts` 控制。这样内联转录不再重复计入 107k 字符。

投影只用于 **prompt 输入**。完整 clips 必须保留在内存中，供范围校验和转录筛选使用（见 7.6）。

### 7.3 timeline 行选择算法

目标：少量行仍能代表整段素材，并优先保留亮点。

1. 解析每行 `start/end`；无法解析的行丢弃。
2. 按 `max_timeline_rows_per_clip` 把素材时长切成等长桶。
3. 每个桶优先选与 `key_moments` / `highlights` 重叠的行，其次选 `score` 最高、`quality=ok` 的行。
4. 若仍有空桶，用时间上最接近桶中心的行填充。
5. 去重后按 `start` 升序输出；不足 K 条不补。
6. 旧 JSON 没有 `score`：先选 highlights 覆盖行，再均匀采样；保证首尾各一行。

不变式：

- 输出行数 ≤ K。
- 输出按时间升序。
- 不包含任何转录字段。
- 同一输入必须产生同一投影（纯函数，便于测试和 lineage 计算）。

### 7.4 两阶段候选筛选（P1）

当 `len(clips) > plan.max_candidate_clips`（默认 60）时：

1. 第一阶段只发送“卡片”：`index/title/summary/location/duration/前 3 条 key_moments`。
2. 模型返回不超过 `max_candidate_clips` 的候选 index 列表。
3. 第二阶段只对候选调用现有 `plan_daily_vlog`，使用完整投影。
4. 第一阶段返回非法 index 时，按本地排序回退：`max(key_moment.score)` 降序、`duration_sec` 降序，取前 N。
5. 两阶段都使用 `vlog_plan` 任务绑定；token 统计分别记 `vlog_plan_shortlist` 和 `vlog_plan`，不新增任务绑定。
6. `plan.max_candidate_clips: 0` 关闭两阶段，保持单次规划。

新增 `PLAN_SHORTLIST_PROMPT` 常量，输出 JSON：

```json
{"keep": ["001", "007"], "reason": "..."}
```

### 7.5 校验与转录必须使用完整 clips

现有 `_validate_plan_ranges` 会检查 `use_timeline` 是否落在该素材的 timeline 区间内（`clio/analyze.py:445-475`）。如果直接拿投影后的 top-K 行去校验，模型选中的合理区间会落在被删掉的行之间，被误判为越界并丢弃。

因此 `plan_daily_vlog` 增加可选参数：

```python
def plan_daily_vlog(
    clips: list[dict],              # 投影后的 clips，用于 prompt
    ...,
    full_clips: list[dict] | None = None,  # 完整 clips，用于校验/转录
) -> dict: ...
```

- `full_clips is None` 时退回 `clips`，保持现有调用方行为。
- `_validate_plan_ranges` 和 `_select_transcript_segments` 使用 `full_clips`。
- `clips_json` 只序列化 `clips`（投影后）。
- plan 结果中的 `source_inputs` 继续来自完整 clips，保持溯源信息不丢失。
- `use_timeline` 的合法性以完整 timeline 为准；投影只影响模型“看到什么”，不影响“能选什么”。

### 7.6 预算与观测

- `plan.max_timeline_rows_per_clip`：默认 8，`0` 表示不截断（仅调试）。
- `plan.max_key_moments_per_clip`：默认 6，`0` 表示不限制。
- `plan.max_candidate_clips`：默认 60，`0` 关闭两阶段。
- 规划前日志输出：`[plan] clips=N rows=M chars=C`，便于对照 token 统计。
- 验收目标（France 项目）：
  - P0 投影后 plan prompt < 60k tokens；
  - 开启两阶段后 < 30k tokens；
  - 视觉 timeline 字符从约 49k 提升到 100k+，但 plan 输入不增长。

### 7.7 PLAN_PROMPT 必须解释新字段

只把 `score` / `key_moments` 塞进 JSON 而不告诉模型含义，等于没有排序。`PLAN_PROMPT` 增加：

- `key_moments` 是该素材最值得使用的区间，优先从其 `use_timeline` 附近选片。
- `timeline[].score`：5 = 封面级，4 = 强亮点，3 = 可用，2 = 填充，1 = 不可用；优先选 4-5，避免 1-2。
- `quality` 非 `ok` 的行不选，除非没有替代。
- `duration_sec` 用于判断素材是否值得整段使用。
- 保留现有 `use_timeline` 必须落在素材时间轴内的规则；该规则仍由完整 clips 校验。

占位符不变：`{clips_json} {max_clips} {target_duration_sec} {example_index}`。

## 8. 配置变更

| 字段 | 层 | 默认 | 校验 | UI |
|---|---|---|---|---|
| `analyze.media_resolution` | 项目 | `medium` | 枚举 `low/medium/high` | select |
| `plan.max_timeline_rows_per_clip` | 项目 | `8` | `>= 0` | number |
| `plan.max_key_moments_per_clip` | 项目 | `6` | `>= 0` | number |
| `plan.max_candidate_clips` | 项目 | `60` | `>= 0` | number |

同步修改：

- `clio/config/models.py`：`AnalyzeConfig` / `PlanConfig`。
- `clio/config/validators.py`：范围与枚举校验。
- `clio/config/schema.py`：`_FIELD_OVERRIDES` 增加 select/分组。
- `clio/config/descriptions.py`：中文说明。
- `config.example.yaml`：全局说明中补 `analyze.media_resolution` 的推荐组合。
- `docs/project.example.yaml`：新增四个字段并注释推荐值。
- `clio/locales/{zh-CN,en}.json`：新增分析覆盖率、plan 预算、media_resolution 错误提示等 `t()` key。

## 9. 缓存与 lineage

`clio/tasks/analyze.py::_analysis_lineage_fingerprint` 增加：

- `analyze.media_resolution`
- `analyze.window_max_min`
- `analyze.window_overlap_sec`

原因：改变视觉分辨率或窗口切法会改变分析结果，必须让 `skip_existing` 失效，否则用户升级后仍看到旧的粗分析。

`clio/tasks/plan.py::_plan_lineage_fingerprint` 改为基于**投影后**的 clips，并加入：

- `plan.max_timeline_rows_per_clip`
- `plan.max_key_moments_per_clip`
- `plan.max_candidate_clips`
- 两阶段 shortlist（若启用）

这样只改完整 timeline 中未被 plan 使用的行，不会无意义地使 plan 缓存失效；但 key_moments / score / 投影参数变化会正确失效。

## 10. 兼容与迁移

- 旧 `texts/*.json` 没有 `key_moments` / `score` / `tags`：
  - `_validate_analysis` 补默认值；
  - `build_plan_clips` 回退到 highlights + 均匀采样；
  - 不需要批量迁移脚本。
- 已有 plan 缓存因 lineage 变化会重新生成一次，这是预期行为。
- `media_resolution` 首次升级后，分析缓存也会因 lineage 变化重新运行一次；想保留旧结果可显式设 `low`。
- Prompt override 用户：`templates/prompts/video_analyze.md` 不受影响；回退逻辑继续优先使用 override。

## 11. 测试

### 单元测试

- 配置：新字段解析、默认值、非法枚举/负数拒绝；`config.example.yaml` 与 `project.example.yaml` 字段存在。
- Gemini：mock client 断言视频调用携带 `MEDIA_RESOLUTION_MEDIUM`，文本调用不带；`low/high` 映射正确；未知值报错。
- lineage：修改 `media_resolution` / `window_max_min` 后分析 lineage 变化；修改投影参数后 plan lineage 变化。
- `_validate_analysis`：保留合法 `score/tags/key_moments`，清理非法值；旧 JSON 不报错。
- `merge_window_analyses`：key_moments 去重、score 取高、tags 并集；overlap 行保留更完整条目。
- `build_plan_clips`：
  - 删除内联转录；
  - 行数 ≤ K；
  - 时间升序；
  - 亮点优先；
  - 旧 JSON 回退；
  - 空 timeline / 非法时间不崩溃；
  - 输出稳定可复现。
- 两阶段：候选超限时触发；非法返回回退本地排序；`0` 关闭。
- 完整 clips 校验：投影只保留 8 行时，`use_timeline` 覆盖被删行所在区间仍应通过 `_validate_plan_ranges`；转录筛选不受投影影响。
- Prompt：`ANALYZE_PROMPT` 包含密度表、`key_moments`、`score`；`PLAN_SHORTLIST_PROMPT` 在 `PROMPT_DEFAULTS` 与 `clio/prompt_overrides.py` 的允许占位符表中登记。

### 集成测试

- 捕获 `plan_daily_vlog` 的 prompt：`clips_json` 不包含 `transcript_segments`；`TRANSCRIPT_CONTEXT` 在 `use_transcripts=true` 时仍存在。
- France 数据的离线投影脚本：输出 clips 数、行数、字符数，作为预算回归基线（不放入默认测试，避免依赖本地项目）。

### 前端

配置表单由 schema 自动生成，只需确认 select 控件渲染；若新增测试，放在 Vitest 的 config 渲染用例中。

## 12. 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0a | `media_resolution` 配置、透传、lineage、A/B 脚本 | 同一 15 分钟素材 medium 的 timeline 细节明显增加，token 约 4 倍 |
| P0b | 分析 prompt 密度契约 + score/tags/key_moments + 校验/合并 | 新分析 avg gap ≤20s，description ≤40 字，key_moments 3-8 条 |
| P0c | `plan_input.build_plan_clips` + 去内联转录 + plan lineage | France 项目 plan prompt <60k tokens |
| P1 | 两阶段候选筛选 | 142 素材 plan prompt <30k tokens，选片质量不下降 |
| P2 | 稀疏窗口补全重试、按场景切片、compress fps 推荐 | 可选，单独评估 |

## 13. 风险

- **成本上升**：medium 约 4 倍视觉 token。默认值和示例必须写清楚；提供 `low` 回退。
- **模型不遵守密度**：即使 medium，模型仍可能少写。用覆盖率日志监控；P2 再加重试。
- **640p 上限**：high 分辨率可能只增加 token 不增加真实细节，因此默认 medium，high 只建议配合 `max_width >= 1280`。
- **输出被截断**：15 分钟窗口按新密度约 3-5k completion tokens，provider `max_tokens` 默认 0（不限）；若用户设置了较小的 `max_tokens`，日志已有截断告警。
- **旧 prompt override**：用户自定义 prompt 不会自动获得新契约；文档说明需要同步更新 override。
- **两阶段多一次调用**：仅在大素材集触发；失败有确定性回退。

## 14. 验收标准

1. 对同一素材，`medium` 的 `video_analyze` prompt_tokens 约为 `low` 的 3.5-4.5 倍，且 timeline 平均粒度 ≤20 秒。
2. 新分析 JSON 每条 timeline 有可解析的 `score`；`key_moments` 在 3-8 条之间（模型遵守时）。
3. France 项目（142 素材）plan 输入的 `transcript_segments` 出现次数为 0。
4. P0 后 plan prompt_tokens <60k；P1 后 <30k。
5. 旧分析 JSON 不经过迁移即可生成 plan。
6. 修改 `media_resolution` 或窗口参数会使分析缓存失效；修改未被 plan 使用的 timeline 行不会使 plan 缓存失效。
7. 全量 pytest、前端 Vitest、ruff、mypy 子集通过。

## 15. 待确认

- 本设计默认 `media_resolution: medium`，升级后分析缓存会重跑一次。若更看重一次性升级成本，可改为默认 `low`、由用户在设置页主动开启。
- 两阶段候选筛选本设计归入 P1；若 100+ 素材一次规划是常态，建议提升到 P0。
- `description` 40 字上限目前按中文定义；英文场景是否放宽到约 80 字符，待确认。
