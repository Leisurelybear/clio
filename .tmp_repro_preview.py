import json, sys
from pathlib import Path

sys.path.insert(0, r"G:\Coding_Project\IdeaProjects\vlog-video-analysis")
from clio.config.loader import load_config
from clio.ui.services.run_preview import build_run_preview

cfg = load_config("config.yaml", project_dir=Path(r"D:\vlog_projects\202610_Italy"))
steps = ["compress", "transcribe", "analyze", "consistency", "voiceover", "plan", "plan_voiceover", "label"]
preview = build_run_preview(cfg, steps, force=False, use_transcripts=True, day_label="day1", all_days=False)
print("input:", json.dumps(preview["input"], ensure_ascii=False))
print("totals:", json.dumps(preview["totals"], ensure_ascii=False))
print("next_stage:", preview.get("next_stage"))
print("blockers:", json.dumps(preview.get("blockers"), ensure_ascii=False))
for s in preview["steps"]:
    print(
        f"  step {s['name']:14} total={s['total']:4} run={s['will_run']:4} skip={s['will_skip']:4} warn={s.get('warnings')} blocking={s.get('blocking')} deferred={s.get('deferred')}"
    )
for st in preview["stages"]:
    print(
        f"STAGE {st['key']:20} status={st['status']:8} total={st['total']} run={st['will_run']} run_max={st['will_run_max']} skip={st['will_skip']} skip_min={st['will_skip_min']} blockers={st['blockers']}"
    )
