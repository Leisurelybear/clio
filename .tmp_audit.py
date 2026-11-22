import json, io, sys, collections
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
texts = Path(r"D:\vlog_projects\202610_Italy\output\texts")
files = sorted(texts.glob("*.json"))
print("json_total", len(files))
src = collections.Counter()
labels = collections.Counter()
no_label = 0
no_mi = 0
idx = collections.Counter()
for f in files:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print("bad", f.name, e)
        continue
    s = d.get("source_file") or d.get("compressed_file") or ""
    src[s] += 1
    lab = d.get("day_label") or d.get("day") or d.get("dayLabel")
    if lab is None:
        no_label += 1
        labels["<missing>"] += 1
    else:
        labels[str(lab)] += 1
    mi = d.get("media_identity")
    if not mi:
        no_mi += 1
    else:
        idx[str(mi.get("index"))] += 1
print("unique_source_file", len(src))
print("dup_pairs(2x)", sum(1 for v in src.values() if v == 2))
print("max_per_source", max(src.values()))
print("no_label", no_label, "no_media_identity", no_mi)
print("label_dist", dict(sorted(labels.items())))
