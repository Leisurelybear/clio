fix(projects): write project.yaml before project.json on create/add

project/create wrote project.json first; if project.yaml then failed the
request returned 500 but the dir already looked like a project, so every
retry got 409 and project/add later registered it without a project.yaml.
Both handlers now create project.yaml first and only then project.json
(the yaml helper is idempotent). Extends the yaml-failure tests.

Refs: D-11
