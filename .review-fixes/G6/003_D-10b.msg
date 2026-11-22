fix(config): validate project.yaml in memory before writing it

PUT /api/config/project wrote the candidate to the live project.yaml and
then validated it, so a running pipeline or the CLI could read an invalid
config in that window. Use config.validate_project_yaml (added in G3) to
check the candidate first and only write it when it passes.

Refs: D-10b
