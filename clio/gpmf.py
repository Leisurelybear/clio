"""GoPro GPMF telemetry helpers (R-024 MVP).

**GPS / GPMF is optional.** Most phone clips and many GoPro modes have no
telemetry. Callers must treat a missing/empty summary as a normal no-op —
never fail compress/analyze because GPMF is absent.

MVP does not parse full GPMF binary streams. It supports:
- sidecar JSON next to the original: ``<stem>.gpmf.json``
- cheap MP4 marker probe for the ASCII ``GPMF`` fourcc

See ``docs/superpowers/specs/2026-07-16-gpmf-telemetry-design.md``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clio.i18n import t


@dataclass
class TelemetrySummary:
    has_gpmf: bool
    source: str  # "sidecar" | "probe" | "none"
    duration_sec: float | None = None
    sample_count: int = 0
    speed_peaks: list[dict[str, Any]] = field(default_factory=list)
    elev_delta_m: float | None = None
    capture_time: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    notes: list[str] = field(default_factory=list)


_CAPTURE_TIME_KEYS = (
    "capture_time",
    "captureTime",
    "recorded_at",
    "recordedAt",
    "creation_time",
    "creationTime",
    "created_at",
    "createdAt",
    "date_time",
    "dateTime",
    "timestamp",
    "GPSDateTime",
    "GPSDateTimeUTC",
    "DateTimeOriginal",
)
_GPS_CONTAINER_KEYS = ("gps", "GPS", "gps_samples", "position", "location", "coordinates", "coordinate")
_LATITUDE_KEYS = ("latitude", "lat", "Latitude", "Lat", "GPSLatitude")
_LONGITUDE_KEYS = ("longitude", "lon", "lng", "Longitude", "Lon", "Lng", "GPSLongitude")


def _normalize_capture_time(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    seconds = number / 1000 if number >= 100_000_000_000 else number
    try:
        value = datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="seconds")
        return value.replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _capture_time_from_mapping(data: dict[str, Any]) -> str | None:
    for key in _CAPTURE_TIME_KEYS:
        capture_time = _normalize_capture_time(data.get(key))
        if capture_time:
            return capture_time
    return None


def _gps_mappings(data: dict[str, Any]) -> list[dict[str, Any]]:
    mappings = [data]
    for key in _GPS_CONTAINER_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            mappings.append(value)
        elif isinstance(value, list):
            mappings.extend(row for row in value if isinstance(row, dict))
    return mappings


def _coordinate(mapping: dict[str, Any], keys: tuple[str, ...], *, limit: float) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and -limit <= number <= limit:
            return number
    return None


def _extract_gps(data: dict[str, Any]) -> tuple[float | None, float | None]:
    for mapping in _gps_mappings(data):
        latitude = _coordinate(mapping, _LATITUDE_KEYS, limit=90)
        longitude = _coordinate(mapping, _LONGITUDE_KEYS, limit=180)
        if latitude is not None and longitude is not None:
            return latitude, longitude
    return None, None


def _fmt_tc(sec: float) -> str:
    s = max(0, int(sec))
    return f"{s // 60:02d}:{s % 60:02d}"


def summarize_from_sidecar(path: Path, *, top_peaks: int = 2) -> TelemetrySummary:
    """Load a user/export sidecar JSON into TelemetrySummary."""
    p = Path(path)
    if not p.is_file():
        return TelemetrySummary(has_gpmf=False, source="none")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TelemetrySummary(has_gpmf=False, source="none", notes=["sidecar unreadable"])

    if not isinstance(data, dict):
        return TelemetrySummary(has_gpmf=False, source="none", notes=["sidecar not an object"])

    speed_raw = data.get("speed") or data.get("speed_samples") or []
    speeds: list[dict[str, Any]] = []
    if isinstance(speed_raw, list):
        for row in speed_raw:
            if not isinstance(row, dict):
                continue
            raw_t = row.get("t_sec", row.get("t", 0))
            raw_v = row.get("value", row.get("v", 0))
            if raw_t is None or raw_v is None:
                continue
            try:
                t = float(raw_t)
                v = float(raw_v)
            except (TypeError, ValueError):
                continue
            speeds.append(
                {
                    "t_sec": t,
                    "value": v,
                    "unit": str(row.get("unit") or "km/h"),
                }
            )
    speeds.sort(key=lambda r: r["value"], reverse=True)
    peaks = speeds[: max(0, top_peaks)]

    elev = data.get("elevation_m") or data.get("elevation") or []
    elev_delta: float | None = None
    if isinstance(elev, list) and len(elev) >= 2:
        try:
            nums = [float(x) for x in elev]
            elev_delta = max(nums) - min(nums)
        except (TypeError, ValueError):
            elev_delta = None

    duration = data.get("duration_sec")
    try:
        duration_f = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_f = None

    capture_time = _capture_time_from_mapping(data)
    metadata = data.get("metadata")
    if capture_time is None and isinstance(metadata, dict):
        capture_time = _capture_time_from_mapping(metadata)
    latitude, longitude = _extract_gps(data)
    has = bool(peaks or elev_delta is not None or capture_time or latitude is not None or data.get("has_gpmf"))
    return TelemetrySummary(
        has_gpmf=has or bool(speeds),
        source="sidecar",
        duration_sec=duration_f,
        sample_count=len(speeds),
        speed_peaks=peaks,
        elev_delta_m=elev_delta,
        capture_time=capture_time,
        latitude=latitude,
        longitude=longitude,
        notes=[],
    )


def probe_gpmf_marker(video_path: Path, *, max_bytes: int = 256 * 1024) -> bool:
    """Return True if the file appears to contain a GPMF fourcc near the start."""
    p = Path(video_path)
    if not p.is_file():
        return False
    try:
        with p.open("rb") as f:
            chunk = f.read(max_bytes)
    except OSError:
        return False
    return b"GPMF" in chunk


def load_telemetry_summary(video_path: Path | None) -> TelemetrySummary:
    """Prefer ``<stem>.gpmf.json`` sidecar; else probe for GPMF marker only.

    Safe for any path: missing file, non-GoPro, or no GPS → ``has_gpmf=False``.
    """
    if video_path is None:
        return TelemetrySummary(has_gpmf=False, source="none")
    p = Path(video_path)

    sidecar = p.with_suffix(".gpmf.json")
    # also accept stem.gpmf.json when video is .MP4
    if not sidecar.is_file():
        alt = p.parent / f"{p.stem}.gpmf.json"
        sidecar = alt if alt.is_file() else sidecar

    if sidecar.is_file():
        return summarize_from_sidecar(sidecar)

    if not p.is_file():
        return TelemetrySummary(has_gpmf=False, source="none")
    if probe_gpmf_marker(p):
        return TelemetrySummary(
            has_gpmf=True,
            source="probe",
            notes=["GPMF marker present; provide a .gpmf.json sidecar for peak details"],
        )
    # Typical path: phone videos / GoPro without GPS track → silent empty
    return TelemetrySummary(has_gpmf=False, source="none")


def format_telemetry_for_prompt(summary: TelemetrySummary | None) -> str:
    """Render a short prompt block. **Always returns "" when GPS/GPMF is absent.**"""
    if not summary or not summary.has_gpmf:
        return ""
    lines = [t("gpmf.header")]
    if summary.source == "probe" and not summary.speed_peaks:
        lines.append(t("gpmf.probe_only"))
        for n in summary.notes:
            lines.append(f"- {n}")
        return "\n".join(lines) + "\n"

    if summary.capture_time:
        lines.append(t("gpmf.capture_time", {"value": summary.capture_time}))
    if summary.latitude is not None and summary.longitude is not None:
        lines.append(
            t(
                "gpmf.gps",
                {"latitude": f"{summary.latitude:.6f}", "longitude": f"{summary.longitude:.6f}"},
            )
        )
    if summary.speed_peaks:
        parts = []
        for peak in summary.speed_peaks:
            tc = _fmt_tc(float(peak.get("t_sec") or 0))
            val = peak.get("value")
            unit = peak.get("unit") or ""
            parts.append(t("gpmf.peak_value", {"timecode": tc, "value": val, "unit": unit}) if val is not None else tc)
        lines.append(t("gpmf.speed_peaks", {"peaks": t("common.list_separator").join(parts)}))
    if summary.elev_delta_m is not None:
        lines.append(t("gpmf.elevation", {"value": f"{summary.elev_delta_m:.0f}"}))
    if summary.duration_sec is not None:
        lines.append(t("gpmf.duration", {"duration": _fmt_tc(summary.duration_sec)}))
    lines.append(t("gpmf.highlight_hint"))
    return "\n".join(lines) + "\n"


def merge_telemetry_into_context(
    context_override: str | None,
    original_video: Path | None,
    *,
    use_gpmf: bool,
) -> str | None:
    """Optionally append GPMF prompt block to an existing context override.

    - ``use_gpmf=False`` → return ``context_override`` unchanged (no I/O).
    - No GPS/GPMF on the clip → return ``context_override`` unchanged.
    - Telemetry present → append formatted block after any existing override.
    """
    if not use_gpmf:
        return context_override
    block = format_telemetry_for_prompt(load_telemetry_summary(original_video))
    if not block:
        return context_override
    base = (context_override or "").strip()
    if base:
        return f"{base}\n\n{block}"
    return block
