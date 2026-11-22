from pathlib import Path

for rel in ["clio/locales/zh-CN.json", "clio/locales/en.json"]:
    raw = Path(rel).read_bytes()
    print(rel, "bom" if raw.startswith(b"\xef\xbb\xbf") else "no-bom", len(raw))
    text = raw.decode("utf-8")
    for line in text.splitlines():
        if "duration_fragments" in line:
            print("  ", line.strip())
