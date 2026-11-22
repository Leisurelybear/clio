from pathlib import Path

for rel in ["clio/locales/zh-CN.json", "clio/locales/en.json"]:
    path = Path(rel)
    lines = path.read_text(encoding="utf-8").splitlines()
    frag = next(line for line in lines if '"plan.readiness.duration_fragments"' in line)
    lines.remove(frag)
    anchor = next(i for i, line in enumerate(lines) if '"plan.readiness.duration_long"' in line)
    lines.insert(anchor, frag)
    lines = [line.lstrip() if '"task.analysis_rerun.confirmation_required"' in line else line for line in lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(rel, "ok")
