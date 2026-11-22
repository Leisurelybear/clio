from pathlib import Path

path = Path("clio/plan_readiness.py")
text = path.read_text(encoding="utf-8")
old = "from collections.abc import Iterable, Mapping\n"
new = "from collections.abc import Iterable, Mapping, Sequence\n"
assert text.count(old) == 1
path.write_text(text.replace(old, new), encoding="utf-8")
print("ok")
