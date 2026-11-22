import sys, io, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
from clio.config.loader import load_config

cfg = load_config("config.yaml", project_dir=Path(r"D:\vlog_projects\202610_Italy"))
print("providers:", list(cfg.ai.providers.keys()))
for name, t in cfg.ai.tasks.items():
    print("task", name, "->", t.provider, t.model)
print("---- provider base urls ----")
for name, p in cfg.ai.providers.items():
    print(name, p.type, p.base_url)
