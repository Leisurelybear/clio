fix(projects): resolve ?project=<name> only within allowed projects

An explicit project_dir must be the serve root or registered, but
?project=<name> scanned every sibling of the serve root and returned any
dir whose project.json had that name, so later PUTs (texts/plan/config)
wrote into an unregistered directory. Name lookup now matches registered
projects and the serve root only (as /api/projects lists them), which
also drops the per-request read of every sibling project.json.

Refs: D-16
