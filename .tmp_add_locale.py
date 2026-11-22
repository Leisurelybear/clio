import json
from pathlib import Path

updates = {
    "clio/locales/zh-CN.json": {
        "plan.readiness.duration_fragments": "第 {positions} 段是 {min} 秒以下的叙事碎片（共 {count} 段），相邻碎片应合并或延长为完整段落",
    },
    "clio/locales/en.json": {
        "plan.readiness.duration_fragments": "Segments {positions} are narrative fragments under {min}s ({count} fragments); merge or extend adjacent fragments into complete beats",
    },
}

for rel, mapping in updates.items():
    path = Path(rel)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    for key, value in mapping.items():
        assert key not in data, key
        data[key] = value
    ordered = dict(sorted(data.items()))
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(rel, len(ordered))
