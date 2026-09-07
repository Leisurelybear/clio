# Clio — ASR & Transcription

> Series: `04` of `00-overview.md`. Covers `clio/asr/*`, `clio/transcribe.py`,
> `clio/whisper_cli.py`, `clio/whisper_cache.py`, `clio/ui/routes/whisper_*.py`.

## 1. Responsibilities

- Provide transcription through a **pluggable engine registry** (`whisper.engine`):
  `local` = faster-whisper, `aliyun` = DashScope `paraformer-v2` cloud ASR.
- Normalize every engine's output to the same `TranscriptSegment` contract (`_transcript.json`),
  so downstream consumers (plan enrichment, transcript review UI) are engine-agnostic.
- Manage the fragile local stack: model download/cache, CUDA detection, env snapshot/restore,
  pip install mirrors, and a whispering CLI (`python main.py whisper install/check`).

## 2. Module Map

| File | Responsibility |
|---|---|
| `asr/base.py` | `TranscriptSegment` (`:11`, frozen, `to_dict`), `ProviderCapabilities` (`:31`), `TranscriptionProvider` protocol (`:41`) |
| `asr/factory.py` | `register_provider` decorator (`:10`), `list_providers` (`:18`), `build_provider(engine_id, config)` (`:22`) — decorator-based registry |
| `asr/local.py` | `LocalWhisperProvider` (`:12`) — adapter delegating to `transcribe._transcribe_local_whisper` |
| `asr/aliyun.py` | `AliyunASRProvider` (`:20`) — DashScope async task submit + poll + result parse (ms→s) |
| `asr/upload.py` | `dashscope_upload` (`:22`) — OSS multipart upload with progress callback |
| `transcribe.py` | `check_whisper` (`:13`), `check_cublas` (`:24`, Windows cuBLAS DLL probe), `pip_mirror_for_config` (`:75`), `_get_model` (`:162`, cached `WhisperModel`), `transcribe_audio` (`:248`, central entry), `_transcribe_local_whisper` (`:268`) |
| `whisper_cli.py` | `run_whisper_install` (`:28`), `_verify_install` (`:144`), `run_whisper_check` (`:179`) |
| `whisper_cache.py` | Model cache download management used by CLI + UI |
| `ui/routes/whisper_download.py` | Managed `TaskKind.WHISPER_INSTALL` pip-install flow + progress page |
| `ui/routes/whisper_check.py` / `whisper_models.py` | Deps check / model list & delete endpoints |

## 3. Provider Registry & Engine Selection

Registration happens at import time in `clio/asr/__init__.py` (imports `local`, `aliyun`,
`upload`), which decorates each class with `@register_provider` keyed by
`capabilities.id` (`local`, `aliyun`).

Engine selection (`ui/routes/whisper_check.py` + `transcribe.py`):
`config.whisper.engine` (default `local`; loader migrates deprecated `cloud`/`cloud_provider`
aliases) → `build_provider(engine_id, config)` → `capabilities.supported_languages` check against
the desired `language` → `provider.transcribe(audio_path, language, progress_callback,
cancel_event)`.

`ProviderCapabilities` drives UI behavior: `requires_public_url` (cloud must upload first),
`max_audio_mb`, `supported_languages` (`["*"]` = any). Unknown engine ids raise a
`RuntimeError` listing available engines.

## 4. Local Engine (faster-whisper)

`transcribe_audio` (`transcribe.py:248`) flow:

```text
config.whisper.engine == "local"
  → _transcribe_local_whisper (transcribe.py:268)
    → _get_model (transcribe.py:162): cached WhisperModel; env snapshot/restore for
        HF_ENDPOINT (hf-mirror) + proxy; compute-type fallback chain CUDA → CPU on
        cublas errors (clears cache, retries once)
    → faster-whisper segment generator: VAD filter, beam 5, word timestamps off
    → each segment → TranscriptSegment; low_confidence when
        avg_logprob < -0.8 or no_speech_prob > 0.1 (transcribe.py:303)
```

Environment machinery:

- `check_whisper` / `check_cublas`: import + Windows DLL probes to decide whether transcription
  can run without a reinstall.
- `pip_mirror_for_config`: map configured mirrors (e.g. hf-mirror/aliyun) to pip index flags used
  by `run_whisper_install`.
- `run_whisper_install`: pip-installs `faster-whisper` (+ pinned CUDA DLLs on Windows), then
  pre-downloads the model via `snapshot_download` into `whisper.cache_dir` (default
  `<program>/models/`).

## 5. Cloud Engine (Aliyun DashScope)

`AliyunASRProvider.transcribe` (aliyun.py:33):

```text
1. dashscope_upload (OSSUpload): multipart upload, progress 0→5        (asr/upload.py:22)
2. submit async task (paraformer-v2, file_url, language)
3. poll every 3s up to 30min: progress 5→95, cancel-aware, network retries
4. fetch transcription_url result, parse segments, ms → seconds
```

Keys come from `.env` (`DASHSCOPE_API_KEY` / `ALIYUN_ACCESS_KEY_ID`/`SECRET` for the OSS
upload) — never in `config.yaml` or task payloads. The output is converted to the same
`TranscriptSegment` list, so the batch writer in `clio/tasks/transcribe.py` stores an identical
`_transcript.json`.

## 6. Transcript Output Contract (`_transcript.json`)

Per segment (`TranscriptSegment.to_dict`):

```json
{ "start": 12.5, "end": 18.2, "text": "…", "avg_logprob": -0.34, "low_confidence": true }
```

Files are `<stem>_transcript.json` under `whisper.transcripts_subdir`; the batch task
(`clio/tasks/transcribe.py`) splits the audio, runs segments through the chosen engine, merges
with `max_segments_per_clip`, and marks errors. Plan generation reads these via
`plan_daily_vlog(use_transcripts=True)` to ground voiceover timeline choices.

## 7. Cross-Module Dependencies

- → `clio/config` (`whisper` section: engine/model/language/device/cache_dir/hf_endpoint)
- → `clio/utils` (subprocess wrappers, ffmpeg audio extraction)
- → `clio/task_center` (whisper-install runs as a managed task; transcribe runs inside
  PIPELINE/RERUN tasks)
- → `clio/ai/*` `ratelimit` for cloud engine rate shaping (shared token bucket)

## 8. Risks / Maintenance Notes

1. The local stack is the **fragile path**: cuBLAS/cuda DLL discovery, env mutation
   (os.environ snapshot/restore around model load), and pip-mirror permutations are hard to test
   in CI — the cloud engine (R-047 Phase 2) is the productized alternative and the primary
   desktop fallback.
2. `_get_model` mutates `os.environ` during load and restores after; a concurrent thread loading
   models at the same moment can observe the interpolated `HF_ENDPOINT`. The `_MODEL_LOCK`
   serializes loads, but strictly only within the process.
3. `requires_public_url` and `max_audio_mb` are capabilities that the batch transcribe task must
   honor (split/upload size gating); enforce them in the task, not just the UI.
4. Aliyun's progress mapping (0→5 upload, 5→95 poll, 95→100 fetch) is engine-specific shorthands;
   a second cloud vendor (Baidu) must implement the same `TranscriptionProvider` protocol but will
   carry its own progress semantics.