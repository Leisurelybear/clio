# Prompt 质量升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全量重写 `clio/prompts.py` 的 8 个 AI prompt，提升视频分析、日 vlog 规划、口播文案与审阅修正的质量；升级 `video_analyze` legacy 默认模型。

**Architecture:** 所有 prompt 仍是 `clio/prompts.py` 中的常量，占位符与 JSON 输出契约保持不变（下游 UI / `analyze.py` 校验 / `plan_model.py` 强依赖）。主要工作是重写 prompt 文本本身，外加三处小代码改动：highlights dict 渲染适配（`_helpers.py`）、legacy 默认模型升级（`config/loader.py`）、测试同步。

**Tech Stack:** Python 3.11+、pytest、ruff

参考 spec: `docs/superpowers/specs/2026-08-15-prompt-quality-design.md`

---

### Task 1: 重写 ANALYZE_PROMPT（视频分析核心）

**Files:**
- Modify: `clio/prompts.py:86-112`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import ANALYZE_PROMPT


def test_analyze_prompt_requires_high_detail_timeline():
    assert "镜头运动" in ANALYZE_PROMPT
    assert "只描述实际可见" in ANALYZE_PROMPT
    assert "禁止编造" in ANALYZE_PROMPT


def test_analyze_prompt_requires_specific_summary_and_mood():
    assert "独有的具体内容" in ANALYZE_PROMPT
    assert "空泛" in ANALYZE_PROMPT


def test_analyze_prompt_highlights_support_object():
    assert '"start"' in ANALYZE_PROMPT
    assert '"reason"' in ANALYZE_PROMPT
    assert "纯字符串" in ANALYZE_PROMPT


def test_analyze_prompt_cover_timestamp_rules():
    assert "主体清晰" in ANALYZE_PROMPT
    assert "真实存在" in ANALYZE_PROMPT
```

注意：`ANALYZE_PROMPT` 不含 `{}` 占位符（video_analyze 的 `PROMPT_PLACEHOLDERS` 为空集），新增测试里的 `"start"` 子串断言要与 JSON 示例文本匹配——确保新 prompt 的 highlights 示例中同时出现 `"start"` 和 `"reason"` 两个键名。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL（断言不成立，因为当前 prompt 无这些内容）

- [ ] **Step 3: 重写 ANALYZE_PROMPT**

将 `clio/prompts.py` 中 `ANALYZE_PROMPT` 常量整体替换为：

```python
ANALYZE_PROMPT = """请仔细分析这段旅行 vlog 原始素材视频，用中文回复。

请严格按以下 JSON 格式输出（不要 markdown 代码块，只输出 JSON）：
{
  "title": "10字以内的简短标题，适合作为文件名，不要含特殊符号",
  "summary": "2-3句话简介，必须提炼这段素材独有的具体内容（去了哪、做了什么、有什么特别），禁止空泛套话",
  "location": "地点（若无法判断填未知）",
  "mood": "氛围/情绪关键词，附上判断依据，如：轻松（海边、微风、笑声）",
  "timeline": [
    {"start": "00:00", "end": "00:08", "description": "2-3句细节描述"},
    {"start": "00:08", "end": "00:15", "description": "..."}
  ],
  "highlights": [
    {"start": "00:08", "description": "亮点画面一句话", "reason": "为什么值得保留"}
  ],
  "suggested_use": "适合放在日 vlog 的哪个环节（开场/途中/美食/结尾等）",
  "cover_timestamp": "建议作为封面的画面时间点，如 00:08",
  "_confidence": 0.82
}

要求：
## timeline（重点）
- 分段要细：每段 5-30 秒，优先接近 5-10 秒粒度，宁可多分几段也不要一句话带过整段
- 每条 description 写 2-3 句，包含：
  1. 画面主体在做什么（人物动作 / 景物状态）
  2. 镜头运动方式（固定 / 跟拍 / 平移 / 变焦 / 手持晃动）
  3. 场景内容细节（建筑、店铺、招牌文字、自然环境、天气）
  4. 明显的人物互动或事件
- 只描述画面里**实际能看到**的内容，禁止编造看不到的细节
- 画面抖动、过暗、被遮挡的段落可以跳过或缩短，但要在 description 里注明（如「画面晃动」）

## highlights
- 每条 = 画面质量高 + 有叙事价值 + 能代表这段素材的特色
- 格式为对象 {"start", "description", "reason"}；也兼容纯字符串
- start 必须是 timeline 里真实存在的时间点

## summary / mood
- summary 写这段素材独有的具体内容，禁止「风景优美、令人难忘」类空话
- mood 给具体情绪关键词并说明画面依据

## cover_timestamp
- 选择主体清晰、光线好、构图稳定、有代表性的单个时间点
- 必须是 timeline 中真实存在的画面点，时间格式 MM:SS 或 HH:MM:SS

## _confidence
- 填 0 到 1 的小数，表示对地点、时间线、亮点判断的整体把握
- 对无法确认的信息（如看不清的招牌文字、不确定的地点）主动降低置信度，并在 description 里注明存疑
"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: upgrade ANALYZE_PROMPT to high-detail timeline"
```

---

### Task 2: 重写 PLAN_PROMPT

**Files:**
- Modify: `clio/prompts.py:138-173`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import PLAN_PROMPT


def test_plan_prompt_requires_precise_timeline_and_budget():
    assert "use_timeline" in PLAN_PROMPT
    assert "不越界" in PLAN_PROMPT or "越界" in PLAN_PROMPT
    assert "target_duration_sec" in PLAN_PROMPT or "时长预算" in PLAN_PROMPT


def test_plan_prompt_reason_needs_evidence():
    assert "叙事作用" in PLAN_PROMPT
    assert "画面依据" in PLAN_PROMPT or "亮点" in PLAN_PROMPT


def test_plan_prompt_voiceover_hint_specific():
    assert "空泛" in PLAN_PROMPT
    assert "voiceover_hint" in PLAN_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 PLAN_PROMPT**

将 `clio/prompts.py` 中 `PLAN_PROMPT` 常量整体替换为：

```python
PLAN_PROMPT = """你是旅行 vlog 剪辑策划。用户以「一天行程 = 一条 vlog」来剪辑。

以下是当天所有素材的摘要（JSON 数组），每个素材都有唯一的 index：
{clips_json}

目标：选出不超过 {max_clips} 个片段，总时长约 {target_duration_sec} 秒，排成有叙事感的顺序。

选片原则（按优先级）：
1. **画面质量优先**：优先选择光线充足、画面稳定、构图清晰的片段
2. **叙事价值**：优先保留有人物互动、风景变化、有趣事件的内容
3. **节奏控制**：开场选吸引眼球的画面，中间有起伏，结尾有收束感
4. **多样性**：避免连续选取同一场景/同一角度的片段
5. **时长适配**：每个片段建议 10-60 秒，过长或过短需要裁剪

**重要规则：**
- 每个 segment 的 index 字段必须精确匹配素材列表中某个素材的 index 值，不要自行编造序号。index 是引用素材的键，不是输出序号。
- 每个 segment 的 use_timeline 必须从该素材 timeline 的真实时间范围内选取（如 "00:10-00:45"），不得越界或编造时间轴。
- 控制总时长：total_estimated_sec 应接近 {target_duration_sec} 秒。素材超过预算时，优先裁掉叙事价值最低的片段，而不是全部保留。
- 每个 segment 的 reason 要写清楚两件事：放在这里的**叙事作用** + 选择它的**画面依据**（引用该素材的亮点或具体内容）。
- voiceover_hint 必须结合该片段实际画面内容给出口播方向，禁止空泛套话。

请输出 JSON（不要 markdown 代码块）：
{{
  "day_title": "这一天 vlog 的标题",
  "theme": "主题一句话",
  "total_estimated_sec": 180,
  "sequence": [
    {{
      "index": "{example_index}",
      "title": "...",
      "reason": "叙事作用 + 画面依据",
      "use_timeline": "00:10-00:45",
      "voiceover_hint": "结合画面内容的口播方向"
    }}
  ],
  "opening_tip": "开场建议",
  "ending_tip": "结尾建议",
  "_confidence": 0.82
}}
"""
```

注意：`{{ }}` 转义保持现有格式（`format_prompt_template` 用 `str.format` 且 `PROMPT_PLACEHOLDERS["vlog_plan"]` 要求 `clips_json/max_clips/target_duration_sec/example_index` 四个占位符都在，且不得出现额外占位符——所以 JSON 示例中的 `{...}` 必须用双花括号）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: 验证占位符契约**

Run: `python -c "from clio.prompt_overrides import validate_prompt_template; from clio.prompts import PLAN_PROMPT; validate_prompt_template('vlog_plan', PLAN_PROMPT, {'clips_json','max_clips','target_duration_sec','example_index'}); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: upgrade PLAN_PROMPT with budget and timeline rules"
```

---

### Task 3: 重写 SCRIPT_PROMPT

**Files:**
- Modify: `clio/prompts.py:114-136`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import SCRIPT_PROMPT


def test_script_prompt_binds_to_timeline_details():
    assert "timeline_text" in SCRIPT_PROMPT
    assert "具体地点" in SCRIPT_PROMPT or "具体" in SCRIPT_PROMPT
    assert "禁止" in SCRIPT_PROMPT


def test_script_prompt_duration_matches_timeline():
    assert "duration_hint_sec" in SCRIPT_PROMPT
    assert "匹配" in SCRIPT_PROMPT or "时长" in SCRIPT_PROMPT


def test_script_prompt_has_clear_structure():
    assert "开头" in SCRIPT_PROMPT
    assert "过程" in SCRIPT_PROMPT
    assert "感受" in SCRIPT_PROMPT
    assert "过渡" in SCRIPT_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 SCRIPT_PROMPT**

将 `clio/prompts.py` 中 `SCRIPT_PROMPT` 常量整体替换为：

```python
SCRIPT_PROMPT = """你是旅行 vlog 口播文案写手。根据以下素材分析结果和口播模板，为编号 {index} 的片段写口播文案。

## 口播模板
{template}

## 素材信息
编号: {index}
标题: {title}
简介: {summary}
地点: {location}
时间轴:
{timeline_text}

## 写作要求
- 口播内容必须贴合素材的 timeline 细节：写进画面里真实出现的具体地点、动作、景物或事件，禁止写与素材分析无关的通用句子
- 结构：开头（在哪 / 在做什么）→ 过程（具体画面细节）→ 感受（个人体验、推荐或吐槽）→ 过渡（自然衔到下一段）
- 第一人称，自然口语化，像跟朋友分享旅行
- duration_hint_sec 必须与画面时长匹配（约等于所用 timeline 片段的时长），不要脱离实际

请输出 JSON（不要 markdown 代码块）：
{{
  "index": "{index}",
  "title": "{title}",
  "voiceover": "口播正文，约{target_words}字，第一人称，自然口语化",
  "duration_hint_sec": 20,
  "edit_tip": "给剪辑师的一句建议（选哪个时间段、要不要加速等）",
  "_confidence": 0.82
}}
"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: 验证占位符契约**

Run: `python -c "from clio.prompt_overrides import validate_prompt_template; from clio.prompts import SCRIPT_PROMPT; validate_prompt_template('voiceover', SCRIPT_PROMPT, {'index','title','summary','location','timeline_text','template','target_words'}); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: upgrade SCRIPT_PROMPT to bind to timeline details"
```

---

### Task 4: 保守化 REFINE_TEXT_PROMPT / REFINE_SCRIPT_PROMPT

**Files:**
- Modify: `clio/prompts.py:176-207`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import REFINE_SCRIPT_PROMPT, REFINE_TEXT_PROMPT


def test_refine_text_prompt_is_conservative():
    assert "只改" in REFINE_TEXT_PROMPT
    assert "确凿" in REFINE_TEXT_PROMPT
    assert "不重写" in REFINE_TEXT_PROMPT or "禁止改写" in REFINE_TEXT_PROMPT


def test_refine_text_prompt_checks_cross_field_consistency():
    assert "一致性" in REFINE_TEXT_PROMPT
    assert "location" in REFINE_TEXT_PROMPT
    assert "cover_timestamp" in REFINE_TEXT_PROMPT


def test_refine_script_prompt_is_conservative():
    assert "只改" in REFINE_SCRIPT_PROMPT
    assert "确凿" in REFINE_SCRIPT_PROMPT


def test_refine_script_prompt_keeps_reference():
    assert "analysis_json" in REFINE_SCRIPT_PROMPT or "素材分析" in REFINE_SCRIPT_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 REFINE_TEXT_PROMPT**

将 `clio/prompts.py` 中 `REFINE_TEXT_PROMPT` 常量整体替换为：

```python
REFINE_TEXT_PROMPT = """请审阅下面这段 vlog 素材的 AI 分析结果。
**严格依据开头的「背景与规范」修正其中的错误**，保持保守原则。

## 只改确凿错误
仅修正以下类型的问题，其余内容一律原样保留：
1. 与画面可证事实明显矛盾的描述
2. 跨字段一致性冲突，例如：
   - location 与 timeline 中描述的地点互相矛盾
   - highlights 内容与 timeline 描述不符
   - cover_timestamp 不在 timeline 的时间范围内
3. 与「背景与规范」明显冲突的常识错误、命名不一致

## 禁止
- 禁止改写风格、扩写、润色——这是审阅修正，不是重写
- 禁止新增或删除字段
- 禁止为了「看起来更好」而改动没有确凿错误的字段
- 明显没问题的字段（如 id、source_file、index）原样输出

## 输出
- 保持原有 JSON 字段结构
- 修正时优先尊重画面里**实际能看到**的线索（标题、招牌、车型、建筑等），其次是背景与规范
- 修正后请在末尾加一个 `_changelog` 字段（数组），简要列出改了哪些字段和原因，便于审计

待审阅的 JSON：
{existing_json}
"""
```

- [ ] **Step 4: 重写 REFINE_SCRIPT_PROMPT**

将 `clio/prompts.py` 中 `REFINE_SCRIPT_PROMPT` 常量整体替换为：

```python
REFINE_SCRIPT_PROMPT = """请审阅下面这段 vlog 口播文案。
**严格依据开头的「背景与规范」修正其中的错误**，保持保守原则。

## 只改确凿错误
仅修正以下类型的问题，其余内容一律原样保留：
1. 地名误用、景点混淆
2. 时序 / 编号错误
3. 与对应素材分析明显矛盾的描述
4. 与「背景与规范」明显冲突的风格问题

## 禁止
- 禁止为了「更有文采」而重写口播、改语气、扩写或润色——这是审阅修正，不是重写
- 禁止新增或删除字段
- 没有确凿错误的字段原样输出

## 参考
如果对应的素材分析里有更准确的信息（如 location），请以素材分析为准。

## 对应素材分析（参考）
{analysis_json}

## 输出
- 保持原有 JSON 字段结构
- 修正后请在末尾加一个 `_changelog` 字段（数组），简要列出改了哪些字段和原因

## 待审阅的口播 JSON
{existing_json}
"""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 6: 验证占位符契约**

Run: `python -c "from clio.prompt_overrides import validate_prompt_template; from clio.prompts import REFINE_TEXT_PROMPT, REFINE_SCRIPT_PROMPT; validate_prompt_template('refine_text', REFINE_TEXT_PROMPT, {'existing_json'}); validate_prompt_template('refine_script', REFINE_SCRIPT_PROMPT, {'analysis_json','existing_json'}); print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: make refine prompts conservative with cross-field checks"
```

---

### Task 5: 微调 REFINE_TEXT_FIX_PROMPT / REFINE_SCRIPT_FIX_PROMPT

**Files:**
- Modify: `clio/prompts.py:210-251`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import REFINE_SCRIPT_FIX_PROMPT, REFINE_TEXT_FIX_PROMPT


def test_refine_fix_prompts_touch_only_mentioned_fields():
    assert "逐字节" in REFINE_TEXT_FIX_PROMPT
    assert "明确提到" in REFINE_TEXT_FIX_PROMPT
    assert "逐字节" in REFINE_SCRIPT_FIX_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 REFINE_TEXT_FIX_PROMPT**

将 `clio/prompts.py` 中 `REFINE_TEXT_FIX_PROMPT` 常量整体替换为：

```python
REFINE_TEXT_FIX_PROMPT = """用户对下面这段 vlog 素材分析给出了**具体修改意见**，请严格按意见修正。

## 用户修改意见
{fix_instruction}

## 要求
- 保持原有 JSON 字段结构，不要新增/删除与意见无关的字段
- **只改用户意见里明确提到的字段，其余字段逐字节原样保留**
- 修正后请在末尾加一个 `_changelog` 字段（数组），**第一条写"按用户意见修改了 XXX"**，不要自己加额外解释

## 待修正的 JSON
{existing_json}
"""
```

- [ ] **Step 4: 重写 REFINE_SCRIPT_FIX_PROMPT**

将 `clio/prompts.py` 中 `REFINE_SCRIPT_FIX_PROMPT` 常量整体替换为：

```python
REFINE_SCRIPT_FIX_PROMPT = """用户对下面这段 vlog 口播文案给出了**具体修改意见**，请严格按意见修正。

## 用户修改意见
{fix_instruction}

## 对应的素材分析（参考）
{analysis_json}

## 要求
- 保持原有 JSON 字段结构
- **只改用户意见里明确提到的字段，其余字段逐字节原样保留**
- 修正后请在末尾加一个 `_changelog` 字段（数组），**第一条写"按用户意见修改了 XXX"**

## 待修正的口播 JSON
{existing_json}
"""
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 6: 验证占位符契约**

Run: `python -c "from clio.prompt_overrides import validate_prompt_template; from clio.prompts import REFINE_TEXT_FIX_PROMPT, REFINE_SCRIPT_FIX_PROMPT; validate_prompt_template('refine_text_fix', REFINE_TEXT_FIX_PROMPT, {'fix_instruction','existing_json'}); validate_prompt_template('refine_script_fix', REFINE_SCRIPT_FIX_PROMPT, {'fix_instruction','analysis_json','existing_json'}); print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: enforce byte-preservation in targeted refine prompts"
```

---

### Task 6: 更新 TRANSCRIPT_CONTEXT

**Files:**
- Modify: `clio/prompts.py:227-231`
- Test: `clio/tests/test_prompts.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_prompts.py` 末尾追加：

```python
from clio.prompts import TRANSCRIPT_CONTEXT


def test_transcript_context_explains_purpose_and_confidence():
    assert "优化" in TRANSCRIPT_CONTEXT
    assert "avg_logprob" in TRANSCRIPT_CONTEXT
    assert "低置信" in TRANSCRIPT_CONTEXT
    assert "不过度依赖" in TRANSCRIPT_CONTEXT or "参考" in TRANSCRIPT_CONTEXT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 重写 TRANSCRIPT_CONTEXT**

将 `clio/prompts.py` 中 `TRANSCRIPT_CONTEXT` 常量整体替换为：

```python
TRANSCRIPT_CONTEXT = """
## 语音转录参考
以下是各素材的语音转录片段（有语音才列出）。可参考口播内容优化剪辑顺序和时间安排：
- transcript_segments 里每段含 start/end（秒）、text（识别文字）、avg_logprob（平均对数概率，越大越可信）
- avg_logprob 较低或标了 low_confidence 的片段识别置信度低，仅作参考，不要据此推导画面内容
- 转录只能辅助排序与衔接判断，选片和内容判断仍以画面分析（timeline/highlights）为准，不要过度依赖转录

{transcripts_json}
"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: 验证占位符契约**

Run: `python -c "from clio.prompt_overrides import validate_prompt_template; from clio.prompts import TRANSCRIPT_CONTEXT; validate_prompt_template('transcript_context', TRANSCRIPT_CONTEXT, {'transcripts_json'}); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add clio/prompts.py clio/tests/test_prompts.py
git commit -m "feat: clarify transcript context usage and confidence"
```

---

### Task 7: highlights dict 渲染兼容（_helpers.py）

**Files:**
- Modify: `clio/tasks/_helpers.py:155-156, 183-184`
- Test: `clio/tests/test_helpers.py`

- [ ] **Step 1: 写失败测试**

在 `clio/tests/test_helpers.py` 的 `TestWriteTextFile` 类中添加：

```python
    def test_writes_dict_highlight(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "My Video",
            "summary": "A nice clip",
            "location": "Paris",
            "mood": "happy",
            "suggested_use": "opening",
            "timeline": [{"start": "0:00", "end": "1:30", "description": "intro"}],
            "highlights": [{"start": "00:08", "description": "sunset shot", "reason": "clear"}],
        }
        _write_text_file(out, analysis, tmp_path / "source.mp4", tmp_path / "compressed.mp4")
        text = out.read_text(encoding="utf-8")
        assert "[00:08] sunset shot" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest clio/tests/test_helpers.py::TestWriteTextFile::test_writes_dict_highlight -v`
Expected: FAIL（当前输出是 `- {'start': '00:08', ...}`）

- [ ] **Step 3: 添加渲染辅助函数**

在 `clio/tasks/_helpers.py` 中（`_write_text_file` 定义之前）添加：

```python
def _format_highlight(highlight: object) -> str:
    """Render a highlight entry; supports both string and dict formats.

    Dict form uses start/description/reason, falls back to str() when unusable.
    """
    if not isinstance(highlight, dict):
        return str(highlight)
    start = highlight.get("start") or highlight.get("timestamp") or highlight.get("time")
    description = highlight.get("description") or highlight.get("text") or highlight.get("reason")
    if start and description:
        return f"[{start}] {description}"
    if description:
        return str(description)
    return str(highlight)
```

- [ ] **Step 4: 替换两处渲染**

`clio/tasks/_helpers.py:155-156`（`_write_text_file` 内）与 `:183-184`（`_rewrite_text_file` 内）都改为：

```python
    for h in analysis.get("highlights", []):
        lines.append(f"- {_format_highlight(h)}")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest clio/tests/test_helpers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add clio/tasks/_helpers.py clio/tests/test_helpers.py
git commit -m "feat: render dict-format highlights in text files"
```

---

### Task 8: 升级 legacy video_analyze 默认模型

> **审查修正**：`_legacy_ai_config`（含 `video_model` 默认值）是**死代码**——全仓库无调用点，`_migrate_v1_to_v2` 直接操作 raw YAML，不经过它。默认值改动不产生运行时效果。最终处理：删除该死代码，默认模型升级仅通过 `docs/project.example.yaml` 示例生效。

**Files:**
- Modify: `docs/project.example.yaml:26`
- Refactor: 删除 `clio/config/loader.py` 的 `_legacy_ai_config`（commit 55c4109）
- Test: `clio/tests/test_config.py`（确认无断言该默认值）

- [x] **Step 1: 删除死代码 `_legacy_ai_config`**

`clio/config/loader.py` 的 `_legacy_ai_config` 定义、`clio/config/__init__.py` 的导入与导出、`clio/config/models.py:350` 注释引用一并移除。

- [x] **Step 2: 修改 project.example.yaml**

将 `docs/project.example.yaml` 的 `video_analyze` 任务：

```yaml
    video_analyze:
      provider: gemini
      model: gemini-2.5-flash
```

改为：

```yaml
    video_analyze:
      provider: gemini
      model: gemini-3-flash
```

- [x] **Step 3: 运行全量配置测试**

Run: `python -m pytest clio/tests/test_config.py clio/tests/test_config_v2.py clio/tests/test_ai.py -v`
Expected: PASS（无测试断言旧默认值；若发现 fixture 显式绑定则保留，因为它们定义的是显式值）

- [x] **Step 4: Commit**

```bash
git add clio/config/loader.py docs/project.example.yaml
git commit -m "feat: default video_analyze to gemini-3-flash"
```

---

### Task 9: 文档同步

**Files:**
- Modify: `templates/prompts/README.md`

- [ ] **Step 1: 更新 README**

在 `templates/prompts/README.md` 末尾追加说明：

```markdown
## Highlights 格式

`ANALYZE_PROMPT` 的 `highlights` 支持两种格式：

- 字符串：`"sunset shot"`
- 对象：`{"start": "00:08", "description": "sunset shot", "reason": "clear"}`

`.txt` 输出会渲染为 `[start] description`。覆盖该 prompt 时可自由选择格式，代码均兼容。
```

- [ ] **Step 2: Commit**

```bash
git add templates/prompts/README.md
git commit -m "docs: document highlights dict format in prompt overrides"
```

---

### Task 10: 全量验证

- [ ] **Step 1: 运行全部后端测试**

Run: `python -m pytest clio/tests/ -v`
Expected: PASS（全绿）

- [ ] **Step 2: ruff 检查与格式化**

Run: `ruff format clio main.py; if ($?) { ruff check clio main.py }`
Expected: 无错误（如有格式化差异先 `ruff format` 再提交）

- [ ] **Step 3: 前端测试**

Run: `npm test`
Expected: PASS（若 Node 环境可用；prompt 改动不涉及前端，如失败需确认是否环境问题）

- [ ] **Step 4: 最终确认占位符契约（全量）**

> **审查修正**：原命令误含 `ANALYZE_PROMPT`（含字面 JSON 花括号，占位符集为空，不经 `str.format`，不能过 `validate_prompt_template`）；且 `validate_prompt_template` 第一参数应为**任务名**（`voiceover` 等）而非常量名。此验证现已固化为 `clio/tests/test_prompts.py` 的契约测试，此处仅作手动兜底：

Run: `python -c "from clio.prompts import PROMPT_DEFAULTS; from clio.prompt_overrides import validate_prompt_template; P={'SCRIPT_PROMPT':'voiceover','PLAN_PROMPT':'vlog_plan','REFINE_TEXT_PROMPT':'refine_text','REFINE_TEXT_FIX_PROMPT':'refine_text_fix','REFINE_SCRIPT_PROMPT':'refine_script','REFINE_SCRIPT_FIX_PROMPT':'refine_script_fix'}; [validate_prompt_template(task, PROMPT_DEFAULTS[name]) for name, task in P.items()]; print('all OK')"`
Expected: `all OK`

- [ ] **Step 5: 检查 git 状态干净**

Run: `git status`
Expected: 无未提交改动

---

## Self-Review 结果

**Spec coverage:**
- 3.1 ANALYZE_PROMPT → Task 1 ✓
- 3.2 PLAN_PROMPT → Task 2 ✓
- 3.3 SCRIPT_PROMPT → Task 3 ✓
- 3.4 REFINE_TEXT/REFINE_SCRIPT → Task 4 ✓
- 3.5 REFINE_FIX 两个 → Task 5 ✓
- 3.6 TRANSCRIPT_CONTEXT → Task 6 ✓
- 3.7 模型升级 → Task 8 ✓
- 3.8 highlights 渲染兼容 → Task 7 ✓
- 4 验证方案 → Task 9（文档）+ Task 10（全量验证）✓

**Placeholder scan:** 所有 prompt 重写都保留了原有占位符并经过 `validate_prompt_template` 验证步骤；ANALYZE_PROMPT 无占位符（video_analyze placeholders 为空集）。

**Type consistency:** `_format_highlight` 在 Task 7 定义并立即被两处使用；测试引用与实现签名一致（接受 object → str）。
