from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTENSIONS
from clio.config.models import AppConfig
from clio.gpmf import load_telemetry_summary
from clio.identity import MediaIdentity, _extract_original_stem, load_identity, resolve_identity
from clio.tasks._helpers import (
    MediaSelection,
    _filename_capture_time,
    _matches_selected_artifact,
    _matches_selected_stem,
    _parse_capture_time_text,
    _probe_creation_time_text,
    _project_selection_stems,
    _selection_from_files,
)
from clio.utils import resolve_binary

_SCOPE_ID_RE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|legacy-day[1-9]\d{0,5}|unknown)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LEGACY_DAY_RE = re.compile(r"^day[1-9]\d{0,5}$", re.IGNORECASE)
_LEGACY_SCOPE_RE = re.compile(r"^legacy-(day[1-9]\d{0,5})$", re.IGNORECASE)
_CAPTURE_TIME_KEYS = ("capture_time", "recorded_at", "creation_time", "date_time")
_MANIFEST_NAME = "scope_manifest.json"
_MODES = {"greenfield", "legacy_adoption"}


@dataclass
class ScopeOption:
    """One server-discovered scope shown to the Run UI."""

    id: str
    kind: str
    capture_date: str | None = None
    capture_date_source: str = "unknown"
    capture_date_basis: str = "unknown"
    legacy_day_label: str | None = None
    clip_count: int = 0
    analyzed_clip_count: int = 0
    duplicate_artifact_count: int = 0
    planning_allowed: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class ResolvedScope:
    """Canonical scope plus the deduplicated artifacts that belong to it."""

    option: ScopeOption
    identity_keys: list[str] = field(default_factory=list)
    analyses: list[Path] = field(default_factory=list)
    ignored_duplicates: list[Path] = field(default_factory=list)
    alias: str | None = None


@dataclass
class ResolvedRunScope:
    """Result consumed by preview, execution, export, and completion."""

    mode: str
    selected: list[str]
    options: list[ScopeOption]
    scopes: list[ResolvedScope]
    analyses: list[Path]
    all_deduplicated_analyses: list[Path] = field(default_factory=list)
    ignored_duplicates: list[Path] = field(default_factory=list)
    orphaned_artifacts: list[Path] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    input_fingerprint: str = ""
    auto_selected: bool = False
    project_mode: str = "greenfield"


@dataclass
class _Artifact:
    path: Path
    data: dict[str, Any]
    identity: MediaIdentity | None
    identity_key: str
    stem_key: str
    compressed_stem: str
    usable: bool
    legacy_labels: set[str]


@dataclass
class _IdentityGroup:
    key: str
    artifacts: list[_Artifact] = field(default_factory=list)
    ignored: list[Path] = field(default_factory=list)
    selected: _Artifact | None = None
    legacy_labels: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _CaptureResolution:
    date: str | None = None
    source: str = "unknown"
    basis: str = "unknown"
    scope_id: str | None = None
    legacy_alias: str | None = None


def _normalized_path(value: str | Path) -> str:
    text = str(value).strip()
    if not text:
        return ""
    try:
        return Path(text).resolve().as_posix().casefold()
    except (OSError, ValueError):
        return text.replace("\\", "/").casefold()


def _rounded_offset(value: Any) -> float:
    try:
        return round(float(value or 0.0), 3)
    except (TypeError, ValueError):
        return 0.0


def _segment_suffix(identity: MediaIdentity | None) -> str:
    if identity is None:
        return ""
    if identity.segment_index is not None:
        return f"#seg={identity.segment_index}"
    offset = _rounded_offset(identity.segment_offset_sec)
    if offset:
        return f"#offset={offset:.3f}"
    return ""


def _source_stem(identity: MediaIdentity | None, fallback_stem: str) -> str:
    if identity is not None:
        if identity.original_stem.strip():
            return identity.original_stem.strip()
        if identity.compressed_stem.strip():
            return _extract_original_stem(identity.compressed_stem.strip())
    return Path(fallback_stem).stem.strip()


def _key_parts(identity: MediaIdentity | None, fallback_stem: str) -> tuple[str, str]:
    """Return ``(primary_key, stem_key)`` without using numeric artifact indexes."""
    suffix = _segment_suffix(identity)
    stem = _source_stem(identity, fallback_stem).casefold()
    if identity is not None:
        original_path = _normalized_path(identity.original_path)
        if original_path:
            return f"path:{original_path}{suffix}", f"stem:{stem}{suffix}"
    if not stem:
        stem = Path(fallback_stem).stem.strip().casefold() or fallback_stem.casefold()
    return f"stem:{stem}{suffix}", f"stem:{stem}{suffix}"


def _legacy_label(data: dict[str, Any]) -> str | None:
    raw = data.get("day_label") or data.get("day") or data.get("dayLabel")
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    return text if _LEGACY_DAY_RE.fullmatch(text) else None


def _artifact_legacy_labels(data: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    label = _legacy_label(data)
    if label:
        labels.add(label)
    persisted = data.get("legacy_day_label")
    if isinstance(persisted, str) and _LEGACY_DAY_RE.fullmatch(persisted.strip()):
        labels.add(persisted.strip().lower())
    scope_id = data.get("scope_id")
    if isinstance(scope_id, str):
        match = _LEGACY_SCOPE_RE.fullmatch(scope_id.strip())
        if match:
            labels.add(match.group(1).lower())
    return labels


def _manifest_mode(config: AppConfig) -> str | None:
    path = config.paths.output_dir / _MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    return str(mode) if mode in _MODES else None


def _has_legacy_evidence(config: AppConfig, artifacts: list[_Artifact], manifest_mode: str | None) -> bool:
    if manifest_mode == "legacy_adoption":
        return True
    if any(artifact.legacy_labels for artifact in artifacts):
        return True
    if config.plans_dir.is_dir() and any(path.is_file() for path in config.plans_dir.glob("*_plan.json")):
        return True
    if config.texts_dir.is_dir():
        consistency = list(config.texts_dir.glob("*_consistency.json")) + list(
            config.texts_dir.glob("*_entity_consistency.json")
        )
        if any(path.is_file() for path in consistency):
            return True
    return config.scripts_dir.is_dir() and any(path.is_file() for path in config.scripts_dir.glob("*_voiceover.json"))


def _project_mode(config: AppConfig, artifacts: list[_Artifact]) -> str:
    manifest_mode = _manifest_mode(config)
    if manifest_mode is not None:
        return manifest_mode
    if _has_legacy_evidence(config, artifacts, manifest_mode):
        return "legacy_adoption"
    # Canonical metadata recovered after an interrupted first write remains greenfield.
    return "greenfield"


def _compressed_files(config: AppConfig, selection: MediaSelection | None) -> dict[str, Path]:
    directory = config.compressed_dir
    if not directory.is_dir():
        return {}
    # An empty selection file means the caller has not selected any source
    # paths; keep legacy artifact discovery usable in that state. A non-empty
    # selection remains authoritative.
    if selection is not None and not len(selection):
        selection = None
    files: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if selection is not None and not _matches_selected_stem(path, selection, artifact=True):
            continue
        files[path.stem.casefold()] = path
    return files


def _artifact_from_path(path: Path, compressed_stems: set[str]) -> _Artifact:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        loaded = {}
    raw = loaded if isinstance(loaded, dict) else {}
    identity = load_identity(raw)
    if identity is not None:
        identity_key, stem_key = _key_parts(identity, path.stem)
    else:
        source = str(raw.get("source_file") or raw.get("compressed_file") or "").strip()
        stem = Path(source).stem if source else path.stem
        if "_" in stem and stem.split("_", 1)[0].isdigit():
            stem = _extract_original_stem(stem)
        stem_key = f"stem:{stem.casefold()}" if stem else f"artifact:{path.stem.casefold()}"
        identity_key = stem_key
    compressed_stem = ""
    if identity is not None:
        compressed_stem = identity.compressed_stem.strip()
    if not compressed_stem:
        compressed_name = str(raw.get("compressed_file") or "").strip()
        if compressed_name:
            compressed_stem = Path(compressed_name).stem
    if not compressed_stem:
        # Legacy artifacts usually use their compressed stem as the filename.
        compressed_stem = path.stem
    usable = bool(compressed_stem) and compressed_stem.casefold() in compressed_stems
    return _Artifact(
        path=path,
        data=raw,
        identity=identity,
        identity_key=identity_key,
        stem_key=stem_key,
        compressed_stem=compressed_stem,
        usable=usable,
        legacy_labels=_artifact_legacy_labels(raw),
    )


def _artifact_from_compressed(path: Path, config: AppConfig) -> _Artifact:
    index = path.stem.split("_", 1)[0]
    identity = resolve_identity(path, index, config.project_dir)
    identity_key, stem_key = _key_parts(identity, path.stem)
    return _Artifact(
        path=path,
        data={},
        identity=identity,
        identity_key=identity_key,
        stem_key=stem_key,
        compressed_stem=path.stem,
        usable=True,
        legacy_labels=set(),
    )


def _schema_version(data: dict[str, Any]) -> tuple[int, ...]:
    raw = data.get("schema_version", data.get("version", 0))
    if isinstance(raw, int):
        return (raw,)
    if isinstance(raw, float):
        return (int(raw),)
    if isinstance(raw, str):
        try:
            return tuple(int(part) for part in raw.split("."))
        except ValueError:
            return (0,)
    return (0,)


def _has_structured_lineage(artifact: _Artifact) -> bool:
    """Return whether the artifact carries the structured analysis lineage marker."""
    components = artifact.data.get("_lineage_components")
    lineage = artifact.data.get("_lineage")
    return isinstance(components, dict) and bool(components) and isinstance(lineage, str) and bool(lineage.strip())


def _select_artifact(artifacts: list[_Artifact]) -> _Artifact:
    usable = [artifact for artifact in artifacts if artifact.usable]
    candidates = usable or artifacts

    def priority(artifact: _Artifact) -> tuple[int, int, int, int, tuple[int, ...], float, str]:
        current_lineage = int(_has_structured_lineage(artifact))
        scope_id = artifact.data.get("scope_id")
        raw_date = artifact.data.get("capture_date")
        persisted = int(
            (isinstance(scope_id, str) and bool(_SCOPE_ID_RE.fullmatch(scope_id.strip())))
            or (isinstance(raw_date, str) and bool(_DATE_RE.fullmatch(raw_date.strip())))
        )
        legacy_label = int(bool(artifact.legacy_labels))
        media_identity = int(artifact.identity is not None)
        try:
            mtime = artifact.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (
            current_lineage,
            persisted,
            legacy_label,
            media_identity,
            _schema_version(artifact.data),
            mtime,
            artifact.path.name,
        )

    return max(candidates, key=priority)


def _date_from_text(value: Any) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value.strip():
        return None, "unknown"
    parsed = _parse_capture_time_text(value)
    if parsed is not None:
        return parsed.date().isoformat(), "machine_local"
    match = re.search(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)", value)
    if match:
        try:
            return (
                datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date().isoformat(),
                "machine_local",
            )
        except ValueError:
            return None, "unknown"
    return None, "unknown"


def _valid_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _DATE_RE.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _resolve_capture(artifact: _Artifact, config: AppConfig) -> _CaptureResolution:
    """Resolve capture identity in the order persisted metadata -> media -> label."""
    data = artifact.data
    raw_scope = data.get("scope_id")
    if isinstance(raw_scope, str):
        scope_id = raw_scope.strip()
        if _DATE_RE.fullmatch(scope_id) and _valid_date(scope_id):
            return _CaptureResolution(
                date=scope_id,
                source=str(data.get("capture_date_source") or "analysis_artifact"),
                basis=str(data.get("capture_date_basis") or "machine_local"),
                scope_id=scope_id,
            )
        legacy_match = _LEGACY_SCOPE_RE.fullmatch(scope_id)
        if legacy_match:
            alias = str(data.get("legacy_day_label") or legacy_match.group(1)).strip().lower()
            return _CaptureResolution(
                source="legacy_label",
                basis="unknown",
                scope_id=f"legacy-{alias}",
                legacy_alias=alias,
            )
        if scope_id == "unknown":
            return _CaptureResolution(
                source=str(data.get("capture_date_source") or "analysis_artifact"),
                basis=str(data.get("capture_date_basis") or "unknown"),
                scope_id="unknown",
            )

    raw_date = _valid_date(data.get("capture_date"))
    if raw_date:
        return _CaptureResolution(
            date=raw_date,
            source=str(data.get("capture_date_source") or "analysis_artifact"),
            basis=str(data.get("capture_date_basis") or "machine_local"),
            scope_id=raw_date,
        )

    metadata_value = data.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    for key in _CAPTURE_TIME_KEYS:
        for values in (data, metadata):
            date_text, basis = _date_from_text(values.get(key))
            if date_text:
                return _CaptureResolution(
                    date=date_text,
                    source=str(data.get("capture_date_source") or "analysis_artifact"),
                    basis=basis,
                    scope_id=date_text,
                )

    original_path = (
        Path(artifact.identity.original_path)
        if artifact.identity is not None and artifact.identity.original_path
        else None
    )
    if original_path is not None and original_path.is_file():
        telemetry = load_telemetry_summary(original_path)
        date_text, basis = _date_from_text(telemetry.capture_time)
        if date_text:
            return _CaptureResolution(date=date_text, source="gpmf", basis=basis, scope_id=date_text)

    probe_path = original_path if original_path is not None and original_path.is_file() else None
    if probe_path is None and artifact.identity is not None and artifact.identity.compressed_path:
        compressed_path = Path(artifact.identity.compressed_path)
        if compressed_path.is_file():
            probe_path = compressed_path
    if probe_path is None:
        for stem, path in _compressed_files(config, None).items():
            if stem == artifact.compressed_stem.casefold():
                probe_path = path
                break
    if probe_path is not None:
        try:
            ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
            raw_creation_time = _probe_creation_time_text(probe_path, ffprobe)
        except (OSError, RuntimeError):
            raw_creation_time = ""
        date_text, basis = _date_from_text(raw_creation_time)
        if date_text:
            return _CaptureResolution(date=date_text, source="container", basis=basis, scope_id=date_text)

    original_stem = _source_stem(artifact.identity, artifact.path.stem)
    captured = _filename_capture_time(original_stem)
    if captured is not None:
        date_text = captured.date().isoformat()
        return _CaptureResolution(date=date_text, source="filename", basis="machine_local", scope_id=date_text)
    date_text, basis = _date_from_text(original_stem)
    if date_text:
        return _CaptureResolution(date=date_text, source="filename", basis=basis, scope_id=date_text)

    return _CaptureResolution()


def _scope_for_group(
    group: _IdentityGroup,
    config: AppConfig,
    project_mode: str,
) -> tuple[str, str, str, str, str | None, list[str]]:
    """Return ``(scope_id, kind, source, basis, legacy_alias, warnings)``."""
    assert group.selected is not None
    resolution = _resolve_capture(group.selected, config)
    aliases = sorted(group.legacy_labels)
    alias = aliases[0] if len(aliases) == 1 else None
    warnings: list[str] = []
    if len(aliases) > 1:
        warnings.append("Conflicting legacy labels were found for one media identity.")

    if resolution.date:
        return resolution.date, "capture_date", resolution.source, resolution.basis, alias, warnings
    if resolution.legacy_alias and project_mode == "legacy_adoption":
        return (
            f"legacy-{resolution.legacy_alias}",
            "legacy_day",
            "legacy_label",
            "unknown",
            resolution.legacy_alias,
            warnings,
        )
    if resolution.scope_id == "unknown" and not aliases:
        warnings.append("Capture date could not be determined; refine the media date before planning.")
        return "unknown", "unknown", resolution.source, resolution.basis, None, warnings
    if project_mode == "legacy_adoption" and len(aliases) == 1:
        scoped_alias = aliases[0]
        warnings.append(f"Capture date could not be determined; using the unambiguous legacy {scoped_alias} label.")
        return f"legacy-{scoped_alias}", "legacy_day", "legacy_label", "unknown", scoped_alias, warnings

    warnings.append("Capture date could not be determined; refine the media date before planning.")
    return "unknown", "unknown", resolution.source, resolution.basis, alias, warnings


def _merge_compatible_groups(groups_by_key: dict[str, _IdentityGroup]) -> dict[str, _IdentityGroup]:
    """Union pathless artifacts with a unique path-backed group of the same source."""
    path_groups = [group for group in groups_by_key.values() if group.key.startswith("path:")]
    if not path_groups:
        return groups_by_key

    by_stem: dict[str, list[_IdentityGroup]] = {}
    for group in path_groups:
        stem_keys = {artifact.stem_key for artifact in group.artifacts if artifact.stem_key}
        if len(stem_keys) == 1:
            by_stem.setdefault(next(iter(stem_keys)), []).append(group)

    merged = dict(groups_by_key)
    for key, group in list(merged.items()):
        if not key.startswith("stem:"):
            continue
        candidates = by_stem.get(key, [])
        if len(candidates) != 1:
            continue
        target = candidates[0]
        target.artifacts.extend(group.artifacts)
        merged.pop(key, None)
    return merged


def _texts_dirs(texts_dir: Path) -> list[Path]:
    """Find analysis directories using the same fallback layout as run preview."""
    if texts_dir.is_dir():
        return [texts_dir]
    output_dir = texts_dir.parent
    if not output_dir.is_dir():
        return []
    return sorted(path for path in output_dir.rglob(texts_dir.name) if path.is_dir())


def _build_groups(
    config: AppConfig,
    *,
    files: list[str] | None,
    compressed: dict[str, Path],
) -> tuple[list[_IdentityGroup], list[Path], list[Path], list[_Artifact], list[Path], list[Path]]:
    """Return active groups plus deduplicated, ignored, orphan, and mode diagnostics."""
    selection = _selection_from_files(files) if files is not None else _project_selection_stems(config)
    if selection is not None and not len(selection):
        selection = None
    compressed_stems = set(compressed)
    artifacts: list[_Artifact] = []
    for texts_dir in _texts_dirs(config.texts_dir):
        for path in sorted(texts_dir.glob("*.json")):
            if selection is not None and not _matches_selected_artifact(path, selection):
                continue
            artifacts.append(_artifact_from_path(path, compressed_stems))

    groups_by_key: dict[str, _IdentityGroup] = {}
    for artifact in artifacts:
        group = groups_by_key.setdefault(artifact.identity_key, _IdentityGroup(key=artifact.identity_key))
        group.artifacts.append(artifact)

    # A greenfield project may have compressed media but no analysis artifacts
    # yet. Keep one current identity row per compressed file for scope preview.
    represented_compressed = {
        artifact.compressed_stem.casefold() for artifact in artifacts if artifact.usable and artifact.compressed_stem
    }
    for compressed_stem, path in compressed.items():
        if compressed_stem in represented_compressed:
            continue
        artifact = _artifact_from_compressed(path, config)
        group = groups_by_key.setdefault(artifact.identity_key, _IdentityGroup(key=artifact.identity_key))
        group.artifacts.append(artifact)

    groups_by_key = _merge_compatible_groups(groups_by_key)
    all_artifacts = [artifact for group in groups_by_key.values() for artifact in group.artifacts]

    groups: list[_IdentityGroup] = []
    all_analyses: list[Path] = []
    all_deduplicated_analyses: list[Path] = []
    ignored: list[Path] = []
    orphaned: list[Path] = []
    for key in sorted(groups_by_key):
        group = groups_by_key[key]
        selected = _select_artifact(group.artifacts)
        group.selected = selected
        group.legacy_labels = {label for artifact in group.artifacts for label in artifact.legacy_labels}
        group.ignored = [
            artifact.path
            for artifact in group.artifacts
            if artifact is not selected and artifact.path.suffix.lower() == ".json"
        ]
        if selected.path.suffix.lower() == ".json":
            all_deduplicated_analyses.append(selected.path)
            if selected.usable:
                all_analyses.append(selected.path)

        if not selected.usable:
            # No current media exists for this source group. Keep the analyses as
            # orphan diagnostics; they must not create a plan scope or clip count.
            orphaned.extend(
                artifact.path
                for artifact in group.artifacts
                if artifact.path.suffix.lower() == ".json" and artifact.data
            )
            ignored.extend(group.ignored)
            continue

        ignored.extend(group.ignored)
        groups.append(group)
    return (
        groups,
        sorted(set(all_analyses)),
        sorted(set(all_deduplicated_analyses)),
        all_artifacts,
        sorted(set(ignored)),
        sorted(set(orphaned)),
    )


def _sort_options(options: Iterable[ScopeOption]) -> list[ScopeOption]:
    def key(option: ScopeOption) -> tuple[int, str]:
        if option.kind == "capture_date":
            return (0, option.id)
        if option.kind == "legacy_day":
            return (1, option.id)
        return (2, option.id)

    return sorted(options, key=key)


def input_fingerprint(
    *,
    steps: Iterable[str] | None,
    project_mode: str,
    selected: list[str],
    identity_keys: Iterable[str],
    manifest_revision: str = "",
) -> str:
    payload = {
        "steps": sorted({str(step) for step in steps or ()}),
        "mode": project_mode,
        "selected": list(selected),
        "identities": sorted(identity_keys),
        "manifest_revision": manifest_revision,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_revision(config: AppConfig) -> str:
    path = config.paths.output_dir / _MANIFEST_NAME
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def resolve_run_scope(
    config: AppConfig,
    *,
    scope_id: str | None = None,
    legacy_day_label: str | None = None,
    all_days: bool = False,
    files: list[str] | None = None,
    steps: Iterable[str] | None = None,
) -> ResolvedRunScope:
    """Resolve canonical scopes and one current analysis artifact per media identity.

    The resolver never deletes or rewrites artifacts. Ignored duplicates and
    orphaned analyses are returned as diagnostics for a later cleanup action.
    """
    selection = _selection_from_files(files) if files is not None else None
    compressed = _compressed_files(config, selection or _project_selection_stems(config))
    (
        groups,
        all_analyses,
        all_deduplicated_analyses,
        all_artifacts,
        ignored,
        orphaned,
    ) = _build_groups(config, files=files, compressed=compressed)
    project_mode = _project_mode(config, all_artifacts)

    scoped: list[ResolvedScope] = []
    options_by_id: dict[str, ScopeOption] = {}
    for group in groups:
        assert group.selected is not None
        artifact = group.selected
        scope_id_value, kind, source, basis, alias, warnings = _scope_for_group(group, config, project_mode)
        option = options_by_id.get(scope_id_value)
        if option is None:
            option = ScopeOption(
                id=scope_id_value,
                kind=kind,
                capture_date=scope_id_value if kind == "capture_date" else None,
                capture_date_source=source,
                capture_date_basis=basis,
                legacy_day_label=alias,
                planning_allowed=kind != "unknown",
                warnings=list(warnings),
            )
            options_by_id[scope_id_value] = option
        option.clip_count += 1
        is_analysis = artifact.path.suffix.lower() == ".json" and bool(artifact.data)
        if is_analysis:
            option.analyzed_clip_count += 1
        option.planning_allowed = option.planning_allowed and is_analysis
        option.duplicate_artifact_count += len(group.ignored)
        if alias and option.legacy_day_label is None:
            option.legacy_day_label = alias
        for warning in warnings:
            if warning not in option.warnings:
                option.warnings.append(warning)
        scoped.append(
            ResolvedScope(
                option=option,
                identity_keys=[group.key],
                analyses=[artifact.path] if is_analysis else [],
                ignored_duplicates=list(group.ignored),
                alias=alias,
            )
        )

    options = _sort_options(options_by_id.values())
    warnings = [warning for option in options for warning in option.warnings]
    requested = scope_id or (f"legacy-{legacy_day_label.strip().lower()}" if legacy_day_label else None)
    available = {option.id for option in options}
    auto_selected = False
    if files is not None:
        selected = [option.id for option in options]
        mode = "selection"
    elif all_days:
        selected = [option.id for option in options if option.id != "unknown"]
        mode = "all_known"
    elif requested:
        if requested in available:
            selected = [requested]
        else:
            selected = []
            warnings.append(f"Requested scope {requested!r} was not found.")
        mode = "single"
    else:
        selected = [options[0].id] if options else []
        auto_selected = bool(options)
        mode = "single"

    included = [scope for scope in scoped if scope.option.id in selected]
    # ``analyses`` exposes active deduplicated identities. Discovery callers that
    # also need orphan diagnostics use ``all_deduplicated_analyses``.
    identity_keys = [key for scope in included for key in scope.identity_keys]
    fingerprint = input_fingerprint(
        steps=steps,
        project_mode=project_mode,
        selected=selected,
        identity_keys=identity_keys,
        manifest_revision=_manifest_revision(config),
    )
    return ResolvedRunScope(
        mode=mode,
        selected=selected,
        options=options,
        scopes=included,
        analyses=all_analyses,
        all_deduplicated_analyses=all_deduplicated_analyses,
        ignored_duplicates=ignored,
        orphaned_artifacts=orphaned,
        diagnostics=[f"ignored duplicate analysis artifact: {path.name}" for path in ignored]
        + [f"orphaned analysis artifact without current compressed media: {path.name}" for path in orphaned],
        warnings=warnings,
        input_fingerprint=fingerprint,
        auto_selected=auto_selected,
        project_mode=project_mode,
    )
