# Optimization Plan - Active Refactoring Items

> Extracted from AGENTS.md section 11. Reference document - load on demand when planning refactoring work.

## 11. Optimization Plan (2026-06-20 Code Review)

Based on external code review (`docs/analysis/2026-06-20-review-part1.md`), cross-referenced against actual project state.

### What both reviews got right (still actionable / already addressed)

| Finding | Action | Phase | Status |
|---------|--------|-------|--------|
| `make_handler` closure too large (432 lines) | Extract business logic to services | **U-001** | ✅ mostly done (routes/ + services/) |
| config.py 406 lines, 14 dataclasses | Split into `config/` package | **U-003** | ✅ done |
| File system as database | Repository layer (long-term) | Phase 3 | still future |
| Config cache not true LRU | Make `ConfigCache.get` refresh order on hit | **U-001** | ⚠️ still open: hits do not `move_to_end`; see 2026-11-21 review O07 |
| No domain models | `@dataclass VideoAnalysis/Segment/VoiceoverScript` | Phase 3 | still future |
| No token cost tracking | `ai/cost_tracker.py` | Phase 3 | ✅ done via `clio/ai/token_usage.py` |
| Pipeline cancel not covering analyze/scripts/plan/label | Add cancel_event to all loop steps | **U-005** | ✅ done |
| `RateLimiter` lock blocks parallel AI calls | Split acquire from sleep | **U-006** | ✅ done |
| Whisper download ctypes thread kill unsafe | Replace with chunked download | **U-007** | ✅ done |
| `/api/fs/dirs` no auth/restriction for LAN mode | Add root restriction + token | **U-008** | ✅ done |
| Whisper low-confidence segments silently dropped | Mark `low_confidence` flag | **U-009** | ✅ done |

### What reviews got wrong (already fixed)

| Claim | Actual fix | Commit |
|-------|-----------|--------|
| server.py 547-line God Object | Split into 13 routes + 2 services (A-001 ✅) | `0918da0` |
| Provider cache no lifecycle | Composite key + lock + `_clear_provider_cache` (C2/C4 ✅) | `71659aa` + `ef68308` |
| UI contains business logic | `project_service.py` + `file_service.py` exist | `0918da0` |
| VIDEO_EXTS duplicate (B-019) | Centralized in `_constants.py` | ✅ |
| `format_index` hardcoded `3` (B-020) | All calls use `config.naming.index_width` | ✅ |

### What reviews missed (real issues found during cross-check)

| Issue | Detail | Fix |
|-------|--------|-----|
| `server.py:524` hardcodes `config_path.parent / "projects.json"` instead of `_registry_path()` | Fragile duplicate path logic | **U-004** |
| `serve.ps1`/`serve.sh` has hardcoded project paths | Not distributable | Needs de-localization |
| ROADMAP.md 656 lines - completed features not archived | Maintenance burden | Periodic cleanup |
| AGENTS.md section 7 commit history overly long (100+ entries) | Should trim to ~30 | Periodic cleanup |
| `transcribe.py` low-confidence segs silently dropped | Information loss for downstream | ✅ **U-009** (`fcbf9f3`) |
| `server.py` 6% coverage + `fs.py` 12% coverage | Security-sensitive untested surface | ✅ **U-010** (`c0e88fc`) |
| ~~`videos.py:101` sidecar mapping - all split segments to first sidecar~~ | ~~All split segments share same text/script in UI~~ | ~~B-097~~ ✅ `05edab2` |

### Tracking

See `ROADMAP.md` for current active work and `docs/analysis/2026-11-21-clio-full-review.md` for the latest dated findings. This file is a compact review-derived reference, not the source of truth for task status.
