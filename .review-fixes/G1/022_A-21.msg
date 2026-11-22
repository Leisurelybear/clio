refactor: drop unused prompt renderer and physical window slicing

render_prompt_template/render_prompt were only used by tests (production
uses prompt_overrides.format_prompt_template) and un-escaped "}}" after
substitution, corrupting inserted JSON. slice_window_video and
cleanup_analyze_windows_dir belonged to the deprecated physical split and
re-ran ffmpeg without cancel support on any TypeError. Remove both groups,
their tests and the imports they alone needed.

Refs: A-21
