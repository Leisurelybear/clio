# Clio — Config System

> Series: `02` of `00-overview.md`. Covers `clio/config/*`.

## 1. Responsibilities

- Load two YAML layers — **global** `config.yaml` and **per-project** `project.yaml` — into
  typed dataclasses, and expose a **merged runtime view** `AppConfig` whose read-only property
  accessors transparently pick the correct layer per field.
- Auto-migrate legacy V1 config → V2 split, auto-upgrade missing dataclass fields, and
  auto-generate a missing `config.yaml` from the bundled `config.example.yaml`.
- Validate numeric bounds, provider/task compatibility, relative-subdir safety, and cross-field
  rules; reject or warn per severity.
- Keep API keys out of YAML: `.env` at the config base directory is loaded into the process
  environ at startup; providers resolve keys from `api_key_env` names.

## 2. Module Map

| File | Responsibility |
|---|---|
| `models.py` | All dataclasses: `GlobalConfig`/`ProjectConfig` and their sub-configs; read-only `Combined*` view dataclasses; `AppConfig` (`:422`); deprecated merged aliases (`AIConfig`, `PathsConfig`, …) |
| `loader.py` | `load_global_config` (`:525`), `load_project_config` (`:574`), `load_config` (`:636`), `apply_run_paths` (`:661`); `.env` loader (`:89`), `_ensure_global_config` (`:117`), `_migrate_v1_to_v2` (`:421`), `_upgrade_config_file` (`:175`), `deep_merge` (`:136`), context-file loader `_load_context` (`:273`) |
| `validators.py` | `_validate_config` (`:107`) + `validate_global_config` (`:183`); `_filter_dc` (unknown-field pruning); numeric `_require_*` helpers; provider/task semantics |
| `parsers.py` | `_parse_providers`/`_parse_tasks`; `_infer_provider_capabilities` (default tags by provider type) |
| `descriptions.py` | UI field descriptions / config schema metadata consumed by the Settings editor |
| `enums.py` | `WhisperModelSize`, `WhisperLang`, `WhisperDevice` |

## 3. Config Models & Ownership

Layer ownership (which file owns which section):

**Global-only** (`config.yaml`): `proxy`, `server` (token + Task-Center retention →
`ServerConfig.__post_init__` validates non-negative ints), `naming`, `paths` (ffmpeg/ffprobe/
logs_dir), `ai.providers`, `ai.debug_print_prompt`/`provider_ttl_min`, `compress` (codec/fps/
crf/remove_audio), `whisper.cache_dir`/`hf_endpoint`.

**Project-only** (`project.yaml`): `paths.output_dir`, `ai.tasks`/`ai.context`
(+ legacy `context_file`, root-confinement-checked), `compress.target_size_mb`/`max_width`,
`analyze`, `script`, `plan`, `whisper` (enabled/model/language/device/max_segments/transcripts_subdir/
`engine`), `export`, `preview.subtitles`.

Unknown-section tolerance: `_filter_global_only` keeps unknown global sections conservatively;
`_filter_project_only` drops unknowns.

## 4. AppConfig — Merged Runtime View

`AppConfig(global_cfg, project_cfg, project_dir)` (`models.py:422`):

- Exposes `global_cfg` / `project_cfg` (writable layer accessors, invalidate cached `Combined*`
  views on setter).
- Property group 1 — **split sections** (lazily built `Combined*` views, cached): `paths`,
  `ai`, `compress`, `whisper` with per-field overrides to the correct layer and project-default
  fallbacks when `project_cfg` is None.
- Property group 2 — **project-only** (`analyze`, `script`, `plan`, `export`, `preview`): returns
  the project value or a lazily-created per-instance default `_default_project_cfg` (never the
  module-level `_EMPTY_PROJECT` singleton — P2-P10). Each has a setter that materializes
  `project_cfg` on demand so `config.analyze.X = Y` works.
- Property group 3 — **global-only** (`proxy`, `server`, `naming`): delegate directly.
- Computed paths (`compressed_dir`, `texts_dir`, `scripts_dir`, `plans_dir`, `transcripts_dir`,
  `summary_csv`) resolve the configured subdir **inside** `output_dir` and re-check root
  containment via `validate_within_root` (`_resolved_output_subdir`, `models.py:631`).

`apply_run_paths` (`loader.py:661`) deep-copies and overrides `output_dir` (CLI `-o`).

## 5. Loading Pipeline & Migration

```text
load_config(config_path, project_dir)
  ├─ _load_dotenv(base)                  .env → os.environ (no override of existing env)
  ├─ _ensure_global_config(file)         create config.yaml from bundled example if missing
  ├─ _migrate_if_needed(file)            V1 task/flag → V2 split (backup + atomic replace)
  ├─ _upgrade_config_file(file, …)       inject missing dataclass fields (+ ./project.yaml)
  ├─ parse into GlobalConfig             validate_global_config
  ├─ load_project_config(project_dir)    project.yaml (auto-upgrade + engine alias migration)
  └─ AppConfig(global, project, cm)      _validate_config (cross-layer rules)
```

Key migration details:

- **V1→V2 split** (`_migrate_v1_to_v2`): backs up `config.yaml.bak`, writes global-only
  `config.yaml` + `config_version: V2`, extracts project fields into `project.yaml`, and migrates
  every registered `projects.json` project similarly. Atomic tmp+rename writes with
  post-write-verify.
- **Field auto-upgrade** (`_upgrade_config_file`): fills missing per-section dataclass fields
  from defaults; provider `capabilities` inferred from `type` when absent; provider/task maps
  upgraded field-by-field. Backs up `.bak` first, validates reparse before replacing.
- **Deprecated `whisper.engine=cloud` alias** (`_resolve_engine`, `loader.py:43`): rewrites to the
  resolved `cloud_provider` value beside a migration notice.
- **Context file** (`_load_context`): inline `ai.context` wins; else `ai.context_file` resolved
  under project dir (preferred) then config base, rejected if it escapes the root or uses
  symlinks (GAP-P1-01).

## 6. Validation Rules (highlights)

- Finite/positive/bounds via `_require_*`; `NaN`/`INF` rejected (`_require_finite`).
- Cross-field: `window_overlap_sec < window_max_min*60`; `font_size >= min_font_size`;
  `export.output_subdir != "export"` → `NotImplementedError` (feature removed).
- Provider semantics: `type ∈ {gemini, openai, openai_compat}`; `video_analyze` must bind a
  gemini provider; missing provider, placeholder keys, and out-of-list models are warnings
  (model warning only when provider declares a non-empty `models` list — R-040 F-2 downgrade).
- Subdir safety: all artifact subdirs must be relative, no `..`/`.`/absolute/`~` segments
  (GAP-P1-03), enforced again at runtime in `_resolved_output_subdir`.

## 7. Cross-Module Dependencies

- → `clio/utils.py` (`write_text_atomic`, `validate_within_root`)
- Consumed by: every task/route — config is the single source of truth for output layout, AI
  binding, whisper engine, compression parameters, and plan/export/preview knobs.

## 8. Risks / Maintenance Notes

1. `AppConfig` caches `Combined*` views lazily and invalidates them only in setters. Mutating
  `config.global_cfg.compress.crf = 20` directly is safe (same instance), but a method that
  *replaces* `global_cfg` must go through the setter or stale views leak through.
2. Two files (`models.py` + `loader.py`) carry backward-compat merged aliases (`AIConfig`,
  `PathsConfig`, `CompressConfig`, …) that are "kept temporarily" — they compile but their
  exact removal target is undocumented; tighten before cleanup.
3. `deep_merge` (`loader.py:136`) exists but is now used only for validation of generated YAML —
  confirm the UI does not still rely on it before removing (P2-P10 history).
4. `.env` parsing is naive `partition("=")` — values with `=` and inline comments are split
  unexpectedly; multiline values unsupported. Fine for keys, surprising for anything else.
5. `_resolve_engine` prints a migration notice on **every** cloud-engine load; hard-coding it to
  `"local"` fallback (`or "local"`) silently downgrades a misconfigured `cloud_provider`.
6. `validate_global_config` and `_validate_config` duplicate several numeric checks; when
  changing a floor/ceiling, update both or the two load paths drift.