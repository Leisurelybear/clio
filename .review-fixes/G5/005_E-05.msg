fix(config): escape YAML keys rendered in the settings form

labelFromPath()/_sectionLabel() fell back to the raw YAML key and data-path,
data-desc-path and data-section-key interpolated it unescaped into
innerHTML, so a project.yaml key like "<img onerror=...>" ran script when
the settings page opened. Escape the non-catalog fallbacks and every
attribute built from a path; make getDeep/setDeep ignore __proto__,
constructor and prototype segments. Regression tests added.

Refs: E-05
