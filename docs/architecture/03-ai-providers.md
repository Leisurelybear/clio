# Clio — AI Providers, Prompts & Token Usage

> Series: `03` of `00-overview.md`. Covers `clio/ai/*`, `clio/analyze.py`, `clio/prompts.py`,
> `clio/ratelimit.py`.

## 1. Responsibilities

- Abstract every AI interaction behind capability-tagged providers: **video-capable**
  (Gemini multimodal) for clip analysis, **text-only** (OpenAI-compatible: DeepSeek/Tongyi/
  Moonshot/OpenAI) for voiceover, plan, and refine.
- Cache live provider instances (HTTP clients) with a TTL, resolve per-task provider+model from
  `config.ai.tasks`, and record token usage per project.
- Own all prompt templates and their shared trip-context wrapping, plus strict validation of
  AI-returned JSON to keep the rest of the pipeline safe.

## 2. Module Map

| File | Responsibility |
|---|---|
| `ai/base.py` | `TaskName` enum (`:11`), `ProviderCapability` (`:18`), `TokenUsage`/`AIResponse` carriers (`:24`/`:31`), `TextAIProvider`/`VideoAIProvider` protocols (`:38`/`:49`), `provider_supports_video` (`:62`) |
| `ai/gemini.py` | `GeminiProvider` (`:26`): google-genai File API upload, `_wait_for_file` polling (`:96`, cancel-aware), `_is_retryable` classification (`:50`), rate limiter, proxy, `max_output_tokens` |
| `ai/openai_compat.py` | `OpenAICompatProvider` (`:13`): raw httpx `/chat/completions`; `analyze_video` raises `NotImplementedError` (`:97`) |
| `ai/factory.py` | `_CachedEntry`/`_provider_cache` (`:21`), `get_task_provider` (`:97`), `get_video_provider` (`:103`, validates video capability), `_clear_provider_cache` (`:77`) |
| `ai/token_usage.py` | `TokenUsageStore` ABC (`:19`), `FileTokenUsageStore` (`:60`) — per-project `.token_usage.json` stats + 500-entry history |
| `analyze.py` | Task-level AI functions: `analyze_video` (`:481`), `generate_voiceover` (`:517`), `plan_daily_vlog` (`:574`), `refine_text` (`:675`), `refine_script` (`:726`); context wrapping, response validation, refinement merge |
| `prompts.py` | All prompt constants; `find_prompt_override` (`:39`) reads `templates/prompts/<name>.md|txt` |
| `ratelimit.py` | Token-bucket rate limiter (`requests_per_minute`) shared with subprocess/API calls |

## 3. Provider Contract & Lifecycle

`get_task_provider(config, task)` / `get_video_provider(config, task)`:

1. Resolve the merged `config.ai.tasks[task]` view → provider name + model. The merge applies
   project `ai.tasks` over global `ai.default_tasks`; missing `refine_text` follows the effective
   `video_analyze` binding unless explicitly configured.
2. Look up `config.ai.providers[name]`; `get_video_provider` additionally rejects non-video
   providers (`provider_supports_video`) so a text-bound clip analysis fails fast.
3. `_build_provider` constructs (or reuses from `_provider_cache`) the provider instance. The
   cache key covers all settings that change wire behavior: provider type, api_key, base_url,
   poll interval, retries, rate-limit, timeout, max_tokens, proxy config (including proxy URL).
   A task-level `ai.tasks.<name>.timeout_sec` overrides the provider timeout for that task only;
   the factory builds a separate cached provider instead of mutating the shared config.
4. Returns `(provider, model)`.

TTL lifecycle (`factory.py`): entries older than `config.ai.provider_ttl_min` (0 = infinite) are
retired before replacement. Active AI calls hold a reference, so an expired provider is closed only
after its last call finishes; idle entries close immediately. Concurrent callers get a fresh instance
instead of a half-closed one (P2-P05). `_clear_provider_cache()` (used at config/`.env` write and
`before_stop`) force-closes everything so rotated API keys apply immediately.

### Completion response contract

Every `generate_text()` / `analyze_video()` completion must return the frozen `AIResponse` carrier:

- `text`: always a string.
- `token_usage`: a validated `TokenUsage` value or `None` when the upstream API did not report it.
- `finish_reason`: a non-empty provider reason or `None`.
- `block_reason`: a non-empty provider reason or `None`; a blocked response always has empty `text`.

Providers do not raise provider-specific errors for a completed-but-blocked generation. Gemini maps
its prompt feedback and OpenAI-compatible providers map `content_filter` / `content_filtered` into
`block_reason`. `_call_ai()` and the provider connection test convert that shared signal into a
localized failure after token accounting. `clio/tests/test_ai_response_contract.py` exercises the
shared shape and the normal/blocked paths for every bundled provider.

## 4. Gemini Provider (`gemini.py`)

Notable behaviors:

- **Upload limit**: `video_upload_max_mb` (default 200 MB) rejects oversized files before
  opening an upload stream; callers should compress larger media first.
- **File API flow**: `analyze_video` uploads the media file, `_wait_for_file` polls until
  `PROCESSING`→`ACTIVE` (interval from `poll_interval_sec`, cancel-aware), then calls
  `generate_content` with the model + prompt.
- **Retry policy**: `_is_retryable` distinguishes transient (429/5xx/network) from permanent
  4xx errors; retries bounded by `retry_attempts`.
- **Rate limiting / proxy**: token bucket via `requests_per_minute`; HTTP(S)_PROXY support for
  reachability.
- **Output budget**: `max_output_tokens` caps responses; when a video is longer than practical
  limits the caller sliced it upstream (`clio/tasks/analyze.py` logical windows).
- **Completion metadata**: candidate `finish_reason` and prompt-feedback `block_reason` are
  normalized into the shared response contract.

## 5. OpenAI-Compatible Provider (`openai_compat.py`)

- One `httpx.Client` per provider; `chat(messages, model)` posts to
  `{base_url}/chat/completions` with per-task `temperature` and optional `max_tokens`.
- `analyze_video` is intentionally `NotImplementedError` — matching `capabilities: [text]`.
- Token accounting is extracted from the `usage` payload; `content_filter` / `content_filtered`
  completions become shared blocked responses.

## 6. Task Functions & JSON Validation (`analyze.py`)

Each task follows the same skeleton — wrap prompt with context → call AI → `extract_json()` →
strict per-task validator → conservative merge for refine:

| Task | Entry | Output validator |
|---|---|---|
| Clip analysis | `analyze_video` (`:481`) | `_validate_analysis` (`:126`) — index/title/timeline coercion, confidence |
| Voiceover | `generate_voiceover` (`:517`) | `_validate_voiceover` (`:171`) |
| Day plan | `plan_daily_vlog` (`:574`, transcript-enriched when `use_transcripts`) | `_validate_plan` (`:200`) + `_validate_plan_ranges` (`:427`) — clip limits, range grounding, duration |
| Refine text/script | `refine_text` (`:675`) / `refine_script` (`:726`) | `_merge_refinement_result` (`:235`) — immutable fields preserved, only `_changelog` appended |

Context wrapping (`_wrap_with_context`, `:270`):

```text
context_override (transient, CLI --context / UI textarea)
  → config.ai.context (inline project context)
  → templates/trip_context.md (package default, cached per project+mtime via _read_trip_context:51)
```

`_call_ai` (`:284`) records elapsed time + token usage when a `TokenUsageStore` is passed,
rejects shared blocked responses, and warns when `finish_reason == "length"` (truncated JSON risk).
AI JSON is parsed with `extract_json()` (first `json.loads`, then regex `{…}`).

## 7. Token Accounting (`token_usage.py`)

- `FileTokenUsageStore(str(output_dir))` aggregates per provider/model/task totals into
  `.token_usage.json` with a bounded 500-entry history ring.
- Read via CLI `tokens` and `GET /api/token-usage`; written during AI calls by task modules
  (`clio/tasks/analyze.py:418` creates the store per run).

## 8. Cross-Module Dependencies

- → `clio/config` (`AppConfig`, `ProviderConfig`, `TaskConfig`)
- → `clio/utils` (`extract_json`)
- Consumed by: `clio/tasks/*` (analyze/scripts/plan/refine/compare_models), UI routes
  (`/api/ai/test`, `/api/token-usage`), CLI.

## 9. Risks / Maintenance Notes

1. Provider cache key is **not** derived from TTL value itself; if TTL semantics change, cached
   entries from a prior config may outlive the intend shipped with a new `provider_ttl_min`.
2. `refine_text` reuses `video_analyze`'s provider by default (Gemini) — the config knob
   (`ai.tasks.refine_text` → cheap text model) is documented in AGENTS.md §4.6 but easy to miss,
   making refine unexpectedly expensive on big clips.
3. Prompts are large constants in `prompts.py` and partially overridable via
   `templates/prompts/`; the override loader (`find_prompt_override`) and the base constants can
   drift — cache keys must include the effective (overridden) prompt text (guarded in R-044).
4. `provider.supports-video` gating exists at lookup time only; a provider mis-declared as
   `[text]` then bound to `video_analyze` fails fast (good), but the reverse (declared `[video]`
   with no `analyze_video` impl) would break at call time at the provider boundary.
5. `openai_compat.analyze_video` raising `NotImplementedError` is contractually correct but an
   un-caught path would crash a run — UI dropdowns must stay capability-filtered.
