# Third-Party Notices

Clio (this project) is distributed under the MIT License — see `LICENSE`.
This file lists the third-party software shipped in the desktop build and its
license attribution, reviewed from upstream package metadata and repositories.

Last reviewed: 2026-08-14.
Keep this table in sync with the declared dependencies in `pyproject.toml` /
`requirements*.txt`; `clio/tests/test_licenses.py` enforces coverage in CI.

## Runtime dependencies (shipped)

| Package | License |
| --- | --- |
| anyio | MIT |
| certifi | MPL-2.0 |
| google-genai | Apache-2.0 |
| h11 | MIT |
| httpcore | BSD-3-Clause |
| httpx | BSD-3-Clause |
| idna | BSD-3-Clause |
| PyYAML | MIT |
| pywebview | BSD-3-Clause |
| sniffio | MIT OR Apache-2.0 |
| socksio | MIT |
| typing-extensions | PSF-2.0 |

## Desktop platform backends (shipped per OS)

| Package | License | Platform |
| --- | --- | --- |
| pythonnet | MIT | Windows |
| pyobjc-core | MIT | macOS |
| pyobjc-framework-Cocoa | MIT | macOS |
| pyobjc-framework-WebKit | MIT | macOS |
| pyobjc-framework-Quartz | MIT | macOS |
| pyobjc-framework-Security | MIT | macOS |

## On-demand installs (NOT bundled)

Whisper transcription (`faster-whisper`, `ctranslate2`, `torch`, `transformers`,
PyAV, etc.) is installed on demand via `main.py whisper install` and is not part
of the desktop bundle; its licenses apply only when the user installs it.

## Frontend build tooling (dev-only, NOT shipped)

The UI ships as authored ES modules with no bundled frontend runtime. Node
packages used to build/test the frontend (esbuild, vite, vitest, postcss, …)
are development dependencies only and are not distributed.

## Provenance

License identifiers above come from the corresponding PyPI metadata (`License`
field / `License ::` classifiers) and upstream repositories, recorded here for
auditability. When a package omits license metadata on PyPI, the identifier was
taken from its upstream source repository.