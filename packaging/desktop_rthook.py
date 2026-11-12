"""PyInstaller runtime hook: keep sys.stdout/stderr usable in windowed builds.

With ``console=False`` (windowed) on Windows, sys.stdout/sys.stderr are None,
which would crash bare ``print()`` calls in the HTTP handler and pipeline.
Redirect to os.devnull so the app never dies on logging writes.
"""

import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - must stay open for process lifetime
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115 - must stay open for process lifetime
