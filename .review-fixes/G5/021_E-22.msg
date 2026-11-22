fix(ui): add download icon and fix file_text icon name

icon() has no 'download' entry and the .env buttons asked for
'file-text' while the set defines 'file_text', so the model download and
"edit .env" buttons fell back to the generic file icon. Add 'download' and
use 'file_text'. A new test scans every literal icon('...') call in the UI
sources and fails on names missing from the icon set.

Refs: E-22
