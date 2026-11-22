fix(config): treat empty YAML sections as empty mappings

A section written without content (proxy:, ai:, paths:, whisper:, or an
empty provider) loads as None, and raw.get("proxy", {}) returned None, so
_filter_dc or .get() raised AttributeError and the user saw a traceback.
A shared _section() helper now maps None to {} and rejects other non-mapping
values with a ValueError naming the field; the loader and parsers use it.

Refs: C-08
