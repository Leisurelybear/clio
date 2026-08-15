from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPT_OVERRIDE_DIR = Path("templates") / "prompts"
PROMPT_SUFFIXES = (".md", ".txt", "")
_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
_PROMPT_FILE_CACHE: dict[Path, tuple[int, int, str]] = {}


@dataclass(frozen=True)
class PromptOverride:
    path: Path
    content: str


def prompt_override_candidates(name: str, base_dir: Path) -> list[Path]:
    aliases = [name, name.lower()]
    return [base_dir / PROMPT_OVERRIDE_DIR / f"{alias}{suffix}" for alias in aliases for suffix in PROMPT_SUFFIXES]


def prompt_override_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / PROMPT_OVERRIDE_DIR


def _read_prompt_file(path: Path) -> str:
    resolved = path.resolve()
    stat = path.stat()
    cached = _PROMPT_FILE_CACHE.get(resolved)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    text = path.read_text(encoding="utf-8").strip()
    _PROMPT_FILE_CACHE[resolved] = (stat.st_mtime_ns, stat.st_size, text)
    return text


def find_prompt_override(name: str, project_dir: str | Path | None = None) -> PromptOverride | None:
    search_roots: list[Path] = []
    if project_dir:
        search_roots.append(Path(project_dir))
    search_roots.append(Path(__file__).resolve().parent.parent)

    seen: set[Path] = set()
    for root in search_roots:
        for candidate in prompt_override_candidates(name, root):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if candidate.is_file():
                text = _read_prompt_file(candidate)
                if text:
                    return PromptOverride(path=candidate, content=text)
    return None


def load_prompt(name: str, default: str, project_dir: str | Path | None = None) -> str:
    """Load a prompt override from templates/prompts before falling back to code defaults."""
    override = find_prompt_override(name, project_dir)
    if override:
        return override.content
    return default


def render_prompt_template(name: str, template: str, **values: object) -> str:
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    unknown = sorted(placeholders - values.keys())
    if unknown:
        raise ValueError(f"Prompt {name} contains unknown placeholder(s): {', '.join(unknown)}")

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return str(values[key])

    return _PLACEHOLDER_RE.sub(replace, template).replace("{{", "{").replace("}}", "}")


def render_prompt(name: str, default: str, project_dir: str | Path | None = None, **values: object) -> str:
    return render_prompt_template(name, load_prompt(name, default, project_dir), **values)


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
- 口播内容必须贴合素材的 timeline 细节：写进画面里真实出现的具体地点、动作、景物或事件，
  禁止写与素材分析无关的通用句子
- 结构：开头（在哪 / 在做什么）→ 过程（具体画面细节）→ 感受（个人体验、推荐或吐槽）
  → 过渡（自然衔到下一段）
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
- 每个 segment 的 index 字段必须精确匹配素材列表中某个素材的 index 值，不要自行编造序号。
  index 是引用素材的键，不是输出序号。
- 每个 segment 的 use_timeline 必须从该素材 timeline 的真实时间范围内选取（如 "00:10-00:45"），
  不得越界或编造时间轴。
- 控制总时长：total_estimated_sec 应接近 {target_duration_sec} 秒。
  素材超过预算时，优先裁掉叙事价值最低的片段，而不是全部保留。
- 每个 segment 的 reason 要写清楚两件事：放在这里的**叙事作用** + 选择它的**画面依据**
  （引用该素材的亮点或具体内容）。
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


TRANSCRIPT_CONTEXT = """
## 语音转录参考
以下是各素材的语音转录片段（有语音才列出）。可参考口播内容优化剪辑顺序和时间安排：
- transcript_segments 里每段含 start/end（秒）、text（识别文字）、avg_logprob（平均对数概率，越大越可信）
- avg_logprob 较低或标了 low_confidence 的片段识别置信度低，仅作参考，不要据此推导画面内容
- 转录只能辅助排序与衔接判断，选片和内容判断仍以画面分析（timeline/highlights）为准，不要过度依赖转录

{transcripts_json}
"""

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


PROMPT_DEFAULTS = {
    "ANALYZE_PROMPT": ANALYZE_PROMPT,
    "SCRIPT_PROMPT": SCRIPT_PROMPT,
    "PLAN_PROMPT": PLAN_PROMPT,
    "REFINE_TEXT_PROMPT": REFINE_TEXT_PROMPT,
    "REFINE_TEXT_FIX_PROMPT": REFINE_TEXT_FIX_PROMPT,
    "REFINE_SCRIPT_PROMPT": REFINE_SCRIPT_PROMPT,
    "REFINE_SCRIPT_FIX_PROMPT": REFINE_SCRIPT_FIX_PROMPT,
}
