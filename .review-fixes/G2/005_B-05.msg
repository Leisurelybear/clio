fix(migrate): keep migration target inside the projects folder

The new project folder was built from the name in project.yaml, so a
name like ../../x or an absolute path wrote project files outside
projects/ and registered that location; a non-string name raised
TypeError. The name is now coerced to str, sanitized with safe_basename
when it is not a single path component, and the target is checked with
validate_within_root.

Refs: B-05
