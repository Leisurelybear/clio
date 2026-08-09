"""Planning task - generate daily vlog editing plan."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import plan_daily_vlog
from clio.config import AppConfig
from clio.identity import legacy_segment_offset_sec, load_identity
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.prompt_overrides import resolve_prompt_template
from clio.prompts import PLAN_PROMPT
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_artifact, _selected_stems
from clio.utils import format_index, safe_basename, write_json_atomic, write_text_atomic


def _analysis_day_label(data: dict) -> str:
    raw = data.get("day_label") or data.get("day") or data.get("dayLabel") or "day1"
    label = str(raw).strip()
    return label or "day1"


def _source_inputs_from_clips(clips: list[dict]) -> list[dict[str, str]]:
    """Build authoritative input-pool provenance for a generated plan."""
    return [
        {
            "index": str(c.get("index") or ""),
            "source_stem": str(c.get("source_stem") or ""),
        }
        for c in clips
    ]


def _plan_lineage_fingerprint(config: AppConfig, clips: list[dict], task_prompts: dict[str, str] | None = None) -> str:
    """Fingerprint everything that can change a cached plan result.

    Changing the vlog_plan prompt, its provider/model, the clip inputs, or the
    transcripts toggle invalidates the skip_existing cache so users never
    silently keep a plan generated under an older lineage.
    """
    try:
        task = config.ai.tasks.get("vlog_plan")
        provider = getattr(task, "provider", "") if task else ""
        model = getattr(task, "model", "") if task else ""
    except Exception:
        provider, model = "", ""
    try:
        prompt = resolve_prompt_template("vlog_plan", PLAN_PROMPT, config, task_prompts=task_prompts)
    except Exception:
        prompt = PLAN_PROMPT
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "use_transcripts": getattr(config.plan, "use_transcripts", False),
            "max_tokens": getattr(config.ai, "max_tokens", None),
            "clips": [
                {
                    "index": c.get("index", ""),
                    "source_stem": c.get("source_stem", ""),
                    "title": c.get("title", ""),
                }
                for c in clips
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _discover_day_labels(config: AppConfig) -> list[str]:
    labels: set[str] = set()
    for json_file in sorted(config.texts_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        labels.add(_analysis_day_label(data))
    return sorted(labels)


def run_plan_vlog(
    config: AppConfig,
    day_label: str = "day1",
    tracker: ProgressTracker | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    filter_by_day: bool = False,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    safe_day = safe_basename(day_label, max_len=60)

    selected = _selected_stems(files) if files is not None else None
    if selected is not None:
        print(f"[规划] 按选片过滤素材（{len(selected)} 个 stem）")

    out_json = config.plans_dir / f"{safe_day}_plan.json"
    out_md = config.plans_dir / f"{safe_day}_plan.md"

    clips = []
    for json_file in sorted(config.texts_dir.glob("*.json")):
        if cancel_event and cancel_event.is_set():
            print("[取消] plan 步骤被用户终止")
            return
        if selected is not None and not _matches_selected_artifact(json_file, selected):
            continue
        data = json.loads(json_file.read_text(encoding="utf-8"))
        if filter_by_day and _analysis_day_label(data) != day_label:
            continue
        raw_idx = data.get("index")
        if raw_idx is None:
            raw_idx = json_file.stem[:3]
        try:
            idx = int(raw_idx)
        except (ValueError, TypeError):
            print(f"  [跳过] 无效 index '{raw_idx}' 在 {json_file.name}")
            continue
        identity = load_identity(data)
        if identity is not None:
            source_stem = identity.original_stem
            match_stem = identity.compressed_stem
            segment_offset = legacy_segment_offset_sec(identity)
        else:
            source_stem = Path(data.get("source_file", "")).stem or json_file.stem
            match_stem = source_stem
            segment_offset = 0.0
        clips.append(
            {
                "index": format_index(idx, config.naming.index_width),
                "title": data.get("title", ""),
                "summary": data.get("summary", ""),
                "location": data.get("location", ""),
                "timeline": data.get("timeline", []),
                "highlights": data.get("highlights", []),
                "suggested_use": data.get("suggested_use", ""),
                "source_stem": source_stem,
                "match_stem": match_stem,
                "segment_offset_sec": segment_offset,
            }
        )

    if not clips:
        print("[规划] 无可用素材（选片过滤后为空或尚未 analyze）")
        return None

    # files= means a selection-scoped plan; never short-circuit with a prior full plan (I1).
    if files is None and not overwrite and config.analyze.skip_existing and out_json.exists() and out_md.exists():
        try:
            existing = json.loads(out_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"  [重新规划] {day_label} (已有规划文件损坏)")
        else:
            stored = existing.get("_lineage")
            current = _plan_lineage_fingerprint(config, clips, task_prompts)
            if stored == current:
                print(f"[跳过] {day_label} 计划 (已存在)")
                return existing
            print(f"  [重新规划] {day_label} (缓存血缘变化)")
            if stored is None:
                # Legacy cache without lineage: accept it (best effort) by stamping
                # a fresh marker so only genuinely-new inputs invalidate it.
                existing["_lineage"] = current
                write_json_atomic(out_json, existing)
                print(f"[跳过] {day_label} 计划 (已存在)")
                return existing
            # Mismatched/newer lineage: recompute below.

    transcripts_map: dict[str, dict] = {}
    trans_dir = config.transcripts_dir
    if trans_dir.is_dir() and config.whisper.enabled and config.plan.use_transcripts:
        for tf in sorted(trans_dir.glob("*_transcript.json")):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                identity = load_identity(data)
                if identity is not None:
                    stem = identity.compressed_stem
                else:
                    stem = data.get("source_stem", "")
                    if "_" in stem:
                        stem = stem.split("_", 1)[1]
                    stem = re.sub(r"_seg\d+$", "", stem)
                if stem:
                    transcripts_map[stem.lower()] = data
            except (json.JSONDecodeError, KeyError):
                continue
    if config.plan.use_transcripts and not transcripts_map:
        print("[警告] use_transcripts=true 但未找到任何 transcript 数据，规划将不使用语音信息")
        print("       请先运行 transcript 步骤，或设置 use_transcripts: false 消除此警告")

    if tracker:
        tracker.update(phase="plan", total=1, current=0, message=f"生成 {day_label} 规划...")
    with timed(f"run_plan_vlog {day_label}（{len(clips)} 条）"):
        print(f"[规划] {day_label}，共 {len(clips)} 条素材")
        plan = plan_daily_vlog(
            clips,
            config,
            day_label,
            transcripts_map=transcripts_map,
            use_transcripts=config.plan.use_transcripts,
            token_store=token_store,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    from clio.plan_model import Plan

    plan_obj = Plan.from_dict(plan)
    plan = plan_obj.to_dict()
    plan["source_inputs"] = _source_inputs_from_clips(clips)
    if config.plan.use_transcripts:
        plan["_transcripts_missing"] = not transcripts_map
    plan = add_schema_version(plan)
    plan["_lineage"] = _plan_lineage_fingerprint(config, clips, task_prompts)
    write_json_atomic(out_json, plan)
    if tracker:
        tracker.log(f"规划 {day_label} ✓")

    lines = [
        f"# {plan.get('day_title', day_label)}",
        "",
        f"**主题**: {plan.get('theme', '')}",
        f"**预估总时长**: {plan.get('total_estimated_sec', '')} 秒",
        "",
    ]
    source_inputs = plan.get("source_inputs") or []
    if source_inputs:
        lines.append("## 规划素材")
        lines.append("")
        for entry in source_inputs:
            idx = entry.get("index", "?")
            stem = entry.get("source_stem", "")
            lines.append(f"- `{idx}` {stem}")
        lines.append("")
    lines.append("## 推荐剪辑顺序")
    for item in plan.get("sequence", []):
        lines.extend(
            [
                f"### {item.get('index', '?')} {item.get('title', '')}",
                f"- **理由**: {item.get('reason', '')}",
                f"- **使用片段**: {item.get('use_timeline', '')}",
                f"- **口播方向**: {item.get('voiceover_hint', '')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 开场建议",
            plan.get("opening_tip", ""),
            "",
            "## 结尾建议",
            plan.get("ending_tip", ""),
        ]
    )
    write_text_atomic(out_md, "\n".join(lines))
    print(f"  -> {out_md.name}")

    state = ProcessingState(config.paths.output_dir)
    for clip in clips:
        source_stem = clip.get("source_stem", "")
        if source_stem:
            state.mark(source_stem, "plan", "done")
    return plan


def run_plan_all_days(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    cancel_event: threading.Event | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    labels = _discover_day_labels(config)
    if not labels:
        print("没有可用的分析结果，请先运行 analyze")
        return None

    summary: dict[str, Any] = {"days": []}
    for day_label in labels:
        if cancel_event and cancel_event.is_set():
            print("[取消] all-days plan 被用户终止")
            break
        plan = run_plan_vlog(
            config,
            day_label=day_label,
            tracker=tracker,
            cancel_event=cancel_event,
            overwrite=overwrite,
            context_override=context_override,
            filter_by_day=True,
            task_prompts=task_prompts,
        )
        if plan is None:
            continue
        sequence = plan.get("sequence", [])
        summary["days"].append(
            {
                "day_label": day_label,
                "day_title": plan.get("day_title", day_label),
                "theme": plan.get("theme", ""),
                "clip_count": len(sequence) if isinstance(sequence, list) else 0,
                "total_estimated_sec": plan.get("total_estimated_sec", 0),
                "plan_file": f"{day_label}_plan.json",
            }
        )

    if not summary["days"]:
        return None
    add_schema_version(summary)
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    out_json = config.plans_dir / "trip_plan.json"
    write_json_atomic(out_json, summary)
    print(f"  -> {out_json.name}")
    return summary
