feat(config): validate candidate project.yaml without writing it

Callers could only validate project settings by writing project.yaml first
and rolling back on failure. load_config and load_project_config now accept
project_yaml_path to read a candidate file instead, and the new
validate_project_yaml(text, project_dir, *, config_path) runs the same checks
on candidate text and returns the AppConfig. The real file is never touched.

Refs: D-10
