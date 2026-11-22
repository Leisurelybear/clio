import json
import subprocess
from pathlib import Path

for rel in ["clio/locales/zh-CN.json", "clio/locales/en.json"]:
    head_raw = subprocess.run(["git", "show", f"HEAD:{rel}"], capture_output=True, check=True).stdout
    head = json.loads(head_raw.decode("utf-8"))
    current = json.loads(Path(rel).read_text(encoding="utf-8"))

    assert set(head) <= set(current), rel
    new_keys = sorted(set(current) - set(head))

    ordered: dict[str, str] = {}
    pending = list(new_keys)
    for key in head:
        while pending and pending[0] < key:
            nk = pending.pop(0)
            ordered[nk] = current[nk]
        ordered[key] = current[key]
    for nk in pending:
        ordered[nk] = current[nk]

    assert ordered == current, rel
    Path(rel).write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(rel, "new keys:", len(new_keys))
