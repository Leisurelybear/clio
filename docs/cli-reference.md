# CLI Reference

> Complete documentation for all subcommands. For common commands see [README.md](../README.md).

## Global Parameters

| Parameter | Scope | Description |
|-----------|-------|-------------|
| `-c, --config <file>` | All | Config file path (default `config.yaml`) |
| `-p, --project <dir>` | Most subcommands | Project directory containing `project.yaml` (preferred) |
| `-o, --output <path>` | Most subcommands | Output directory (default `output/<media folder name>`) |
| `--force` | Most subcommands | Ignore existing output / optional readiness warnings; accepted before or after the subcommand |
| `-i, --input <path>` | Compatibility | Legacy input path for single-file commands, `refine`, and `compare-models`; hidden from `--help` |

## Command Overview

| Command | Purpose |
|---|---|
| `compress` | Compress source videos without invoking AI |
| `analyze` | Compress when needed, then analyze video content |
| `scripts` | Generate voiceover scripts from analysis JSON |
| `plan` | Generate one-day or multi-day editing plans |
| `run` | Run the full analyze → scripts → plan → label pipeline |
| `cut` | Cut plan segments with ffmpeg |
| `export` | Export a plan to an editor draft |
| `refine` | Review or target-fix existing texts/scripts |
| `transcribe` | Generate Whisper transcripts |
| `check` / `doctor` | Quick readiness check / full environment diagnosis |
| `label` | Burn index labels into compressed videos |
| `verify` / `reindex` | Validate or rebuild media sidecars |
| `tokens` | Show project token usage as JSON |
| `compare-models` | Analyze one video with several providers/models |
| `migrate` / `migrate-config` | Migrate legacy projects / fill missing provider fields |
| `serve` / `desktop` | Start the local web UI / desktop window |

---

## `check` — Environment Check

Verify virtual environment, ffmpeg / ffprobe, media directory, and API keys for each AI task are ready.
No network requests are made.

```bash
python main.py check
python main.py check -p "E:/Videos/云南"     # Also verify the specified project directory
```

Outputs `[OK] xxx`, `[WARN] xxx`, or `[FAIL] xxx`, plus how many video files were found.

---

## `doctor` — Full Environment Diagnosis

Checks the virtual environment, Python version, ffmpeg/ffprobe, config files, provider keys, task bindings, disk paths, and common runtime risks. Unlike `check`, it includes diagnostic guidance for failures.

```bash
python main.py doctor
python main.py doctor -p "E:/Videos/云南"
```

Exit code is `0` when no blocking problem is found, otherwise non-zero.

---

## `compress` — Compress Only

Compress videos in the media folder with ffmpeg to 640p / 5MB / audio-stripped mp4 for later use.
Does **not** invoke AI. Suitable for batch compressing first, then manual review later.

Compresses each original to one file under `compressed/` as `<index>_<original stem>.mp4`.
Physical `_segNN` split is removed; long-clip AI coverage uses analyze windows (`analyze.window_max_min`).

```bash
python main.py compress -p "E:/Videos/云南"

# Process only a single video file
python main.py compress -i "E:/Videos/云南/GL010683.mp4"
```

Output to `output/<media folder name>/compressed/`, naming format `<index>_<original filename>.mp4` (e.g. `001_GL010683.mp4`).
Legacy split projects may still show grouped `_segNN` rows; new projects list one row per original/compressed file.
Existing compressed files are automatically skipped (when `skip_existing: true`).

---

## `analyze` — Compress + AI Analysis (Most Common)

First compress (if not yet compressed), then call the provider configured in `ai.tasks.video_analyze` (default Gemini) for content analysis on each compressed video.

**Long clips (new path):** no physical `_segNN` files. Duration is split into logical windows
(`analyze.window_max_min`, default 15 min, with `window_overlap_sec` overlap). Each window is a
temp ffmpeg slice under `output/.analyze_windows/<compressed_stem>/`, analyzed separately, then
merged into **one** texts JSON with **absolute** timeline times. Any window failure fails the whole
clip (no partial texts). Oversized slices are re-encoded/shrunk to stay under Gemini’s ~200MB upload limit.

**Legacy `_seg*` projects:** still analyzed one call per segment file. Optional whole-clip hard skip
via `analyze.max_analyze_duration_min` applies only to those legacy segments (`0` = unlimited; new
default for fresh configs).

```bash
python main.py analyze -p "E:/Videos/云南"
python main.py analyze --force         # Force re-analysis (overrides skip_existing)

# Analyze a single video
python main.py analyze -i "E:/Videos/云南/GL010683.mp4"
```

Output trilogy:
- `output/<media folder name>/texts/<index>_<title>.json` — Structured analysis (machine-readable; may include `analyze_windows` debug metadata)
- `output/<media folder name>/texts/<index>_<title>.txt` — Human-readable version (with timeline)
- `output/<media folder name>/summary.csv` — Full media overview table (one row per entry)
- `output/<media folder name>/covers/<index>_<title>.jpg` — Optional cover frame when analysis returns `cover_timestamp`

If a matching transcript already exists, timeline entries are enriched with `transcript` and `transcript_segments`. If transcription runs after analysis, the transcribe step updates the matching analysis JSON/TXT before planning.

> Re-running will **automatically skip** already generated `.json` / `.txt` (when `analyze.skip_existing: true`).
> Add `--force` to rerun everything.

---

## `scripts` — Generate Voiceover Scripts

For each analysis in `texts/`, call the `ai.tasks.voiceover` provider (default DeepSeek) to generate voiceover scripts according to the `templates/vlog_template.md` template.

```bash
python main.py scripts

# Generate voiceover for a single analysis
python main.py scripts -i output/Franch/texts/001_机场轻轨清晨.json
```

Output to `output/<media folder name>/scripts/<index>_<title>_voiceover.{json,md}`.
`.md` is a finished draft that can be pasted directly into JianYing (CapCut).

---

## `plan` — Vlog Editing Plan

Feed all `texts/` summaries for a day to the `ai.tasks.vlog_plan` provider, letting it select the most narratively compelling segments and arrange them into an editing order.

```bash
python main.py plan --day day1
python main.py plan --day "Day2_卢瓦尔河谷"
python main.py plan --all-days
```

The `--day` label appears in the output filename and vlog title. Output to `output/<media folder name>/plans/<day>_plan.{json,md}`.
`--all-days` scans `texts/*.json` for `day_label`, `day`, or `dayLabel`; files without a day field are grouped into `day1`. It generates one `<day>_plan.{json,md}` per day plus `plans/trip_plan.json`.

---

## `label` — Burn Index Numbers

Burn index numbers (`001` / `002` / ...) onto the upper-left corner of compressed videos, making it easy to match plan segments in JianYing (CapCut).

```bash
python main.py label
```

Output to `output/<media folder name>/labeled/<index>_<title>_labeled.mp4`.

---

## `reindex` — Rebuild .vmeta / .vindex Sidecars

Rebuild `.vmeta` (compressed→original mapping) and `.vindex` (original→compressed mapping) sidecar files from existing compressed videos. Useful after merging split temp files or when upgrading old projects.

```bash
python main.py reindex
python main.py reindex -p "E:/Videos/Franch3"
```

The UI automatically reindexes when opening a project for the first time.

---

## `verify` — Check .vmeta / .vindex Integrity

Verify existing `.vindex` and `.vmeta` sidecars against source videos and compressed segments.
This reports missing sources, stale source metadata, missing segments, missing `.vmeta` files, and compressed-file hash mismatches.

```bash
python main.py verify
```

Exit code is `0` when every indexed source is OK, otherwise `1`. If verification reports stale or missing sidecars, run `python main.py reindex`; if compressed-file hashes mismatch, rerun compression for the affected source.

---

## `cut` — Cut Video Segments per Plan

Read `plans/<day>_plan.json`, and use ffmpeg to cut independent segments from the corresponding compressed video (or original) based on the time ranges specified in `sequence[].use_timeline`.

```bash
# Default output to output/cuts/day1/
python main.py cut --day day1

# Specify output directory
python main.py cut --day day1 --out-dir "E:/剪辑素材/第一天"

# Cut from original (instead of compressed)
python main.py cut --day day1 --source original

# Re-encode (default -c copy finishes in seconds; with --reencode uses h264 for precise cutting)
python main.py cut --day day1 --reencode

# Ignore readiness warnings only (errors still block)
python main.py cut --day day1 --force
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--day` | `day1` | Plan label (corresponds to `plans/<day>_plan.json`) |
| `--out-dir` | `output/cuts/<day>/` | Output directory, can be user-specified |
| `--source` | `compressed` | Video source: `compressed` or `original` |
| `--reencode` | — | h264 re-encoding (default `-c copy` for fast cut) |
| `--force` | — | Proceed despite plan **warnings** (empty title, offline media, etc.). **Errors** (bad timeline, empty sequence, missing index when known) still fail |

Before cutting, Clio runs the same readiness checks as the Web UI (`clio/plan_readiness.py`). Fix errors in the plan editor or re-run `plan`; use `--force` only when warnings are acceptable.

Outputs each segment as `<index>_<title>_seg_<number>.mp4`; if a corresponding `texts JSON` exists, it is also copied to the same directory.
After completion, generates `manifest.md` (markdown table with info for each segment).

---

## `export` — Export Plan to Editor Draft

Export `plans/<day>_plan.json` to a JianYing-oriented draft folder under `output/export/`.

```bash
python main.py export --day day1 --format jianying
python main.py export --day day1 --out-dir "E:/drafts/day1" --force
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--day` | `day1` | Plan label |
| `--format` | `jianying` | Export format (currently only `jianying`) |
| `--out-dir` | `output/export/<day>_<format>/` | Draft output directory |
| `--force` | — | Same as `cut --force`: ignore readiness **warnings** only |

---

## `run` — One-Click Full Pipeline

Runs `analyze` → `scripts` → `plan` → `label` sequentially; any step can be re-run independently.

```bash
python main.py run -p "E:/Videos/云南" --day day1
```

---

## `refine` — Review & Fix Existing Output

### 1. Review Mode (default)

AI automatically reviews and corrects errors (misidentified locations, naming inconsistencies, etc.) based on `ai.context` / `ai.context_file`.

```bash
# Default: review all files in texts/ and scripts/
python main.py refine

# Select the project explicitly
python main.py refine -p "E:/Videos/云南" --target texts

# Review only texts/
python main.py refine --target texts

# Fix a single file
python main.py refine -i output/Franch/texts/001_机场轻轨清晨.json

# Fix all json files in a folder
python main.py refine -i output/Franch/texts/
```

### 2. Targeted Fix Mode (`--fix`)

You provide a specific edit instruction, and AI **only modifies** the fields mentioned in that instruction, leaving everything else untouched.
Must be used with `-i` to specify a single file, to avoid unintended changes.

```bash
# Correct the location field from misidentification to the correct place name
python main.py refine -i output/Franch/texts/001_机场轻轨清晨.json `
    --fix "把 location 从曼谷素万那普机场改成巴黎戴高乐机场"

# Fix a specific error in the voiceover text
python main.py refine -i output/Franch/scripts/001_机场轻轨清晨_voiceover.json `
    --fix "把 voiceover 第一句'曼谷的早晨'改成'巴黎的早晨'"
```

The first entry in the `_changelog` field will read "modified XXX per user instruction" for audit purposes.
Both modes call the `video_analyze` task's AI (default gemini) to review texts,
and the `voiceover` task's AI (default deepseek) to review scripts; results overwrite the original files directly.

### 3. Temporary Context Mode (`--context / -C`)

Append a temporary context instruction, attached after `ai.context` with higher priority.
Suitable for temporarily correcting a common error without modifying `config.yaml`:

```bash
python main.py refine --context "特别注意：所有素材均在法国巴黎拍摄，不要误判为其他城市"

# Can also be combined with --fix
python main.py refine -i output/Franch/scripts/001_机场轻轨清晨_voiceover.json `
    --fix "把 location 改成巴黎戴高乐机场" `
    --context "用户刚从泰国回来，AI 请勿混淆曼谷和巴黎"
```

---

## `compare-models` — Compare Video Models

Analyze the same video with several provider/model pairs and write a comparison report. Useful for evaluating model quality or cost before changing a project binding.

```bash
python main.py compare-models -i "output/compressed/001_GL010685.mp4" \
  --models gemini:gemini-2.5-flash gemini:gemini-2.5-flash-lite
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-i, --input` | required | Video file to analyze |
| `--models` | required | One or more `provider:model` values; comma-separated values also work |
| `--out-dir` | `output/model_compare/` | Comparison report directory |
| `--context / -C` | — | Temporary trip context appended for this run only |

---

## `migrate` / `migrate-config` — Project Migration

`migrate` upgrades legacy projects to the `project_dir + videos.json` layout. `migrate-config` scans existing `project.yaml` files and fills missing provider fields.

```bash
# Migrate one legacy project, or scan the project registry when --from is omitted
python main.py migrate --from "E:/Videos/旧项目"

# Scan a projects root for project.yaml files
python main.py migrate-config --projects-root "E:/Videos"
```

---

## Transcription — `transcribe` / `whisper`

Use faster-whisper for offline speech recognition (ASR) on videos, generating timestamped text transcripts. First run `whisper install` to install dependencies, then run `transcribe`.

```bash
# Install faster-whisper (includes CUDA detection and model pre-download)
python main.py whisper install

# Check faster-whisper / CUDA / model cache status
python main.py whisper check

# Transcribe compressed videos
python main.py transcribe

# Ignore existing transcripts, regenerate all
python main.py transcribe --force
```

> Note: transcript data is attached to matching analysis timeline entries and injected into the `plan` prompt, so the AI can reference actual voiceover content to optimize editing arrangement.

---

## `serve` — Start Local Web UI (Visual Editor)

Start a visual editor in the browser for watching videos + editing AI output (texts / scripts / plan), saving directly back to JSON.

```bash
python main.py serve                    # Default http://127.0.0.1:8765/, auto-open browser
python main.py serve --port 9000        # Change port
python main.py serve --no-browser       # Don't auto-open browser (for remote machine debugging)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--host` | `127.0.0.1` | Listen address; change to `0.0.0.0` to expose to LAN (be mindful of security) |
| `--port` | `8765` | Port |
| `--no-browser` | — | If set, does not auto-open browser |
| `--token` | `server.api_token` | API token override; non-localhost hosts auto-generate one when empty |

Page after startup: left project/video sidebar, center video player (Range requests), right editor (texts / voiceover / plan / config / run / logs). Plan tab uses a collapsed sequence list (expand a row to edit); insert picks from existing project videos; `use_timeline` is edited as text or via the **选区** dual-handle source-video modal. Plan preview bar is a **global scrub timeline** (Σ `use_timeline`) with progress fill + composite clock; waveform is **stitched multi-source peaks** on that axis (R-031a/a2). Media is still source-video seek hop (cut/concat media is roadmap R-031b). `Ctrl+S` saves. Details: `clio/ui/README.md`.

### Desktop Window

```bash
python main.py desktop
```

Starts the same local UI inside a pywebview window with native file/folder dialogs, single-instance focus behavior, and close confirmation while tasks are active. Use the global `-c` option to point at a different config file.

### Token Usage

```bash
python main.py tokens
python main.py tokens -p "E:/Videos/云南"
```

Prints the selected project's AI token/cost statistics as JSON.

### External Trigger

Automation tools such as NAS download tasks or Syncthing hooks can trigger the same pipeline start behavior through the authenticated webhook endpoint:

```bash
curl -X POST "http://127.0.0.1:8765/api/webhook/trigger" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api-token-if-configured>" \
  -d '{"steps":["compress","analyze"],"day_label":"day1"}'
```

The JSON body is the same as `POST /api/run/start`, including `steps`, `files`, `overwrite`, `day_label`, `use_transcripts`, `context_override`, and `task_prompts`.

---

## Output Directory Structure

```
output/
├── compressed/          # Compressed videos (for AI use, audio stripped)
├── texts/
│   ├── 001_丽江古城.txt    # Human-readable: summary + timeline
│   └── 001_丽江古城.json   # Machine-readable, used by subsequent steps
├── scripts/
│   ├── 001_丽江古城_voiceover.md   # Voiceover script (ready for JianYing/CapCut)
│   └── 001_丽江古城_voiceover.json
├── labeled/             # Preview videos with index numbers burned in
├── plans/
│   ├── day1_plan.md     # Recommended editing order and timeline
│   └── day1_plan.json
├── cuts/
│   └── day1/            # Cut segments + manifest.md
└── summary.csv          # Full media overview table
```

---

## Typical Workflow

```
Raw Footage (input_dir)
    │
    ▼ compress (1 original → 1 file; no physical _seg)
640p Compressed Videos (compressed/)
    │
    ▼ analyze (Gemini; long clips use logical windows)
Text Analysis (texts/)
    │
    ├──► scripts ──► Voiceover script (one per media file)
    │
    ├──► plan ────► Daily vlog editing plan (which segments to pick, in what order)
    │
    ├──► label ───► Preview videos with index numbers (for reference in JianYing/CapCut)
    │
    ├──► cut ─────► Cut segments per plan + manifest.md
    │
    ▼
User in JianYing (CapCut): select segments per plan → add effects → paste voiceover script
```

---

## Configuration Reference

### Compression Parameters (`compress`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `target_size_mb` | 5 | Target file size (MB); bitrate is auto-calculated from video duration |
| `max_width` | 640 | Maximum width |
| `fps` | 15 | Frame rate |
| `remove_audio` | true | Strip audio |

### AI Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| (see `ai` section in `config.example.yaml`) | — | Each task can independently specify provider and model |

### Project-Specific Configuration (`project.yaml`)

Each project directory can contain a `project.yaml` that only needs to specify fields **different from** the global `config.yaml`:

```yaml
# Example: Paris project using dedicated AI context and compression parameters
ai:
  context_file: ./trip_context_paris.md
compress:
  fps: 1
  target_size_mb: 5
```

Auto deep-merged on top of the global configuration at load time:
- Nested dicts are merged recursively (e.g. `ai.tasks`), not overwritten entirely
- Uncovered fields inherit from the global `config.yaml`
- `ai.context_file` relative paths are resolved relative to the project directory first
- UI settings tab reads/writes the current project's `project.yaml` via `?project=X`
- CLI selects the project directory with `-p/--project`; `-i/--input` remains for single-file and compatibility uses

### Voiceover Template

Edit `templates/vlog_template.md` to adjust voiceover style (first person, word count, structure, etc.).

### Prompt Overrides

Create Markdown files in `templates/prompts/` to override built-in AI prompts without editing Python code. The priority order is:

1. Runtime `task_prompts` from the Run panel / API
2. `templates/prompts/{name}.md`
3. Built-in constants in `clio/prompts.py`

Supported files and required placeholders:

| File | Required placeholders |
|---|---|
| `video_analyze.md` | none |
| `voiceover.md` | `{index}`, `{title}`, `{summary}`, `{location}`, `{timeline_text}`, `{template}`, `{target_words}` |
| `vlog_plan.md` | `{clips_json}`, `{max_clips}`, `{target_duration_sec}`, `{example_index}` |
| `refine_text.md` | `{existing_json}` |
| `refine_text_fix.md` | `{fix_instruction}`, `{existing_json}` |
| `refine_script.md` | `{analysis_json}`, `{existing_json}` |
| `refine_script_fix.md` | `{fix_instruction}`, `{analysis_json}`, `{existing_json}` |
| `transcript_context.md` | `{transcripts_json}` |

Missing or unknown placeholders fail before the AI request. Use `{{` and `}}` for literal JSON braces inside custom prompt files.

For prompt experiments, prefer creating an override file instead of editing `clio/prompts.py`; deleting the override restores the built-in prompt.

### AI Provider Configuration

Each step can independently configure provider and model (`config.yaml` → `ai`):

```yaml
ai:
  providers:
    gemini:
      type: gemini
      api_key_env: GEMINI_API_KEY
    openai:
      type: openai
      api_key_env: OPENAI_API_KEY
      base_url: https://api.openai.com/v1
      timeout_sec: 120
    deepseek:
      type: openai              # OpenAI compatible API
      api_key_env: DEEPSEEK_API_KEY
      base_url: https://api.deepseek.com/v1
      timeout_sec: 120

  tasks:
    video_analyze:              # Video understanding (must support video, e.g. gemini)
      provider: gemini
      model: gemini-2.5-flash
    voiceover:                  # Voiceover script
      provider: deepseek
      model: deepseek-chat
    vlog_plan:                  # Daily vlog plan
      provider: openai
      model: gpt-4o-mini
```

| Task | Description | Supported Providers |
|------|-------------|-------------------|
| `video_analyze` | Watch video, output timeline | `gemini` |
| `voiceover` | Generate voiceover script | `gemini` / `openai` / any OpenAI compatible |
| `vlog_plan` | Recommend editing order | same as above |
| `refine_text` | Review and fix existing analysis/voiceover (`refine` command) | same as above (text-only) |

> `refine_text` falls back to `video_analyze`'s provider by default. Both texts and scripts
> review share this single task (both are text-only); declaring it explicitly in `ai.tasks` allows switching to a cheaper model.

For OpenAI-compatible providers, `timeout_sec` controls the HTTP client timeout. Increase it for slow third-party gateways or local model servers; decrease it when you want failures to surface faster.

### Prompt Overrides

Default prompt constants live in `clio/prompts.py`. To override them without editing code, create files under either project-level `<input_dir>/templates/prompts/` or repo-level `templates/prompts/`.
Project-level files take priority.

Supported names are the prompt constant names, with `.md`, `.txt`, or no suffix. Lowercase names also work:

```text
templates/prompts/ANALYZE_PROMPT.md
templates/prompts/SCRIPT_PROMPT.md
templates/prompts/PLAN_PROMPT.md
templates/prompts/REFINE_TEXT_PROMPT.md
templates/prompts/REFINE_TEXT_FIX_PROMPT.md
templates/prompts/REFINE_SCRIPT_PROMPT.md
templates/prompts/REFINE_SCRIPT_FIX_PROMPT.md
```

Keep the same `{placeholder}` names as the built-in prompt when overriding formatted prompts such as script, plan, or refine prompts.

Generated analysis, voiceover, and plan JSON may include `_confidence` as a 0-1 self-assessed confidence score. Missing values are treated as `0.0` so older outputs remain valid.

The web backend also exposes authenticated prompt management APIs:

```text
GET /api/prompts
PUT /api/prompts/{PROMPT_NAME}
DELETE /api/prompts/{PROMPT_NAME}
```

`PUT` accepts `{"content":"..."}` and writes a project-level override under `<input_dir>/templates/prompts/`.
`DELETE` removes project-level overrides for the prompt; repo-level overrides, if present, still apply.

### AI Trip Context

Add `ai.context` or `ai.context_file` in `config.yaml`; the content is automatically injected as a **preamble** before all AI prompts:

```yaml
ai:
  context: "所有素材均拍摄于 2024 年 7 月法国巴黎，不要误判为其他城市。"
  # Or use a file for longer text:
  # context_file: ./templates/trip_context.md
```

Template and examples are in `templates/trip_context.md`. It is recommended to include at least:
- Travel time / location
- Naming conventions (Chinese title vs. original foreign text)
- Easily misidentified edge cases (e.g. airports, metro)
- Output language and style

### Dependencies

- Python 3.11+
- ffmpeg / ffprobe
- Gemini API Key
- SOCKS5 proxy (for mainland China environments)
