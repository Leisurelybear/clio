build: move pywebview to an optional desktop extra

pywebview was a core dependency, so every CLI or server install pulled
pythonnet on Windows or pyobjc on macOS, which is large and often fails to
build. It now lives in the "desktop" extra (pip install "clio[desktop]");
requirements.txt and the desktop build scripts still install it.

Refs: C-30
