fix(projects): accept dict registry entries when resuming last project

resolve_last_project_config's legacy name lookup did Path(entry) over
reg["projects"], but the registry also stores {"project_dir": ...} dicts.
When last_project's dir had moved, Path(dict) raised TypeError, which the
except tuple does not cover, so `clio serve` crashed at startup.
It now iterates _registry_project_paths(reg) and skips (with a warning)
non-object project.json files. Adds a startup-resolution test.

Refs: D-15
