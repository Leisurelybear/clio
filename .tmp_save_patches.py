import subprocess
from pathlib import Path

for rel, patch in [
    ("clio/locales/zh-CN.json", ".tmp_zh.patch"),
    ("clio/locales/en.json", ".tmp_en.patch"),
]:
    diff = subprocess.run(["git", "diff", "--", rel], capture_output=True, check=True).stdout
    Path(patch).write_bytes(diff)
    print(patch, len(diff))
