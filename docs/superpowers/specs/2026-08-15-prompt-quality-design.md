# Prompt 质量升级设计

> 日期: 2026-08-15
> 状态: 待审阅
> 范围: `clio/prompts.py` 全部 8 个 prompt + `video_analyze` 默认模型升级

## 1. 背景

流水线已稳定，当前 AI 任务 prompt 质量是产出瓶颈。用户反馈痛点：

- **ANALYZE_PROMPT**: timeline 太粗（缺少画面细节）；highlights / cover_timestamp 不准；summary/mood 千篇一律
- **PLAN_PROMPT**: 选片理由、时间轴、口播方向不够具体
- **SCRIPT_PROMPT**: 口播文案与素材 timeline 细节贴合度不足
- **REFINE 系列**: 需要更保守（只改确凿错误，不误伤合理部分）
- 其他任务也需要高质量 prompt

用户已确认：

- 「视频转文本」= Gemini 视频分析（ANALYZE_PROMPT），不是 Whisper 转录
- timeline 细度：**高细度**（条目更多、描述更详尽）
- 模型：接受 `video_analyze` 默认模型从 `gemini-2.5-flash` 升级到 `gemini-3-flash`
- 实施位置：直接更新 `clio/prompts.py` 常量（不引入模板文件作为默认）
- 范围：全部 8 个 prompt 一次性打磨
- refine 系列：保守不误伤；script：加强 timeline 细节贴合

## 2. 数据契约约束（不可破坏）

下游强依赖以下 JSON 字段结构与占位符，改动时必须保持：

### 占位符（render 依赖）

| Prompt | 占位符 |
|---|---|
| `ANALYZE_PROMPT` | 无（直接使用，`_wrap_with_context` 附加背景） |
| `SCRIPT_PROMPT` | `{index} {template} {title} {summary} {location} {timeline_text} {target_words}` |
| `PLAN_PROMPT` | `{clips_json} {max_clips} {target_duration_sec} {example_index}` |
| `REFINE_TEXT_PROMPT` | `{existing_json}` |
| `REFINE_TEXT_FIX_PROMPT` | `{fix_instruction} {existing_json}` |
| `REFINE_SCRIPT_PROMPT` | `{analysis_json} {existing_json}` |
| `REFINE_SCRIPT_FIX_PROMPT` | `{fix_instruction} {analysis_json} {existing_json}` |
| `TRANSCRIPT_CONTEXT` | `{transcripts_json}` |

### JSON 输出字段（`analyze.py` 校验 + UI 渲染依赖）

- `analyze_video` 输出: `title, summary, location, mood, timeline[], highlights[], suggested_use, cover_timestamp, _confidence`
- `timeline[]` 条目: `{start, end, description}`（额外字段如 `transcript` 已支持）
- `generate_voiceover` 输出: `index, title, voiceover, duration_hint_sec, edit_tip, _confidence`
- `plan_daily_vlog` 输出: `day_title, theme, total_estimated_sec, sequence[], opening_tip, ending_tip, _confidence`
- `sequence[]` 条目: `{index, title, reason, use_timeline, voiceover_hint, subtitle}`（`plan_model.py` 的 `_SEGMENT_KNOWN`）
- refine 输出: 保持原字段 + `_changelog[]`

## 3. 具体改动

### 3.1 `ANALYZE_PROMPT`（核心）

重写要点：

- **timeline 高细度**: 条目每段 5-30 秒（鼓励接近 5-10 秒粒度），`description` 2-3 句，包含:
  - 画面主体在做什么（人物动作 / 景物状态）
  - 镜头运动（固定 / 跟拍 / 平移 / 变焦）
  - 场景内容细节（建筑、店铺、招牌文字、自然环境）
  - 明显的人物互动或事件
  - 只描述**实际可见**内容，禁止编造
- **highlights 更准**: 每条标准 = 画面质量高 + 有叙事价值 + 能代表该段特色。
  格式改为**对象**: `{"start": "00:08", "description": "...", "reason": "..."}`，同时声明兼容纯字符串。
- **cover_timestamp 更准**: 主体清晰、光线好、构图稳定、有代表性；必须是 timeline 真实存在的点。
- **summary / mood 去套路**: 提炼这段素材独有的具体内容（去了哪、做了什么、什么特别），禁止「风景优美、令人难忘」类空话；mood 给具体情绪 + 依据。
- **`_confidence` 细化**: 分开评估地点判断 / 时间线准确性 / 亮点判断。

### 3.2 `PLAN_PROMPT`

- 每个 segment 强制精确 `use_timeline`（取自素材 timeline，不越界）。
- `reason` = 叙事作用 + 画面依据（引用具体亮点/内容）。
- 明确**总时长预算**：`total_estimated_sec` 与 `target_duration_sec` 接近，超预算先裁叙事价值低的片段。
- `voiceover_hint` 必须结合该片段实际内容，禁止空泛套话。
- 保留「index 必须匹配素材列表」强调（现有 `plan_daily_vlog` 有过滤后处理）。

### 3.3 `SCRIPT_PROMPT`

- 口播必须贴合 timeline 细节：写进具体地点、动作、景物，禁止与素材无关的通用句。
- `duration_hint_sec` 与 `use_timeline`/timeline 时长匹配。
- 结构：开头（在哪/在做什么）→ 过程（具体细节）→ 感受 → 过渡。

### 3.4 `REFINE_TEXT_PROMPT` / `REFINE_SCRIPT_PROMPT`（保守化）

- 只改**确凿错误**：与画面可证事实矛盾、跨字段冲突、与背景规范冲突。
- 加跨字段一致性检查：
  - `location` ↔ `timeline` 描述矛盾
  - `highlights` ↔ `timeline` 内容一致
  - `cover_timestamp` 必须在 timeline 范围内
- 严禁改写风格 / 扩写 / 润色——是审阅修正，不是重写。
- 保持原有「明显没问题的字段原样输出」+ `_changelog` 要求。

### 3.5 `REFINE_TEXT_FIX_PROMPT` / `REFINE_SCRIPT_FIX_PROMPT`

- 微调：明确「只动意见提到的字段，其余逐字节原样保留」。
- 保持 `_changelog` 首条固定「按用户意见修改了 XXX」。

### 3.6 `TRANSCRIPT_CONTEXT`

- 说明转录片段用途（结合口播优化排序），`avg_logprob` 低 = 低置信，参考但不过度依赖。

### 3.7 模型默认升级

- `clio/config/loader.py:508` 默认 `gemini-2.5-flash` → `gemini-3-flash`
- `docs/project.example.yaml` 的 `video_analyze` 示例同步
- 受影响测试更新（`test_ai.py`、`conftest.py`、`test_compare_models.py` 等断言默认模型的地方）
- 用户本地真实 `project.yaml` 若显式绑定旧模型，需手动改（文档提示）

### 3.8 highlights 渲染兼容（最小适配）

`_helpers.py:156,184` 的 `f"- {h}"` 在 `h` 为 dict 时显示不优雅。改为：
- dict 时渲染 `[start] description`（或仅有 description）
- 字符串时保持原样

## 4. 验证方案

1. 更新受影响的测试：
   - `test_prompts.py` — prompt 占位符渲染仍通过
   - `test_ai.py` / `test_compare_models.py` / `conftest.py` — 默认模型断言更新
   - 新增/更新：highlights dict 渲染测试
2. `python -m pytest clio/tests/ -v` 全绿
3. `ruff check clio main.py` 通过
4. 文档同步：`templates/prompts/README.md`（提示 highlights 支持对象格式）、`config/descriptions.py`（如模型名引用）

## 5. 不在范围内

- Whisper 转录 prompt/热词（用户明确不在此次）
- prompt override 机制本身
- 其他流水线行为改动
