# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the Clio desktop shell (R-032c).

Build (any of):
    pyinstaller packaging/clio.spec
    pyinstaller packaging/clio.spec --target-architecture universal2   # macOS only

Output:
    Windows: dist/clio/clio.exe (onedir)
    macOS:   dist/clio/clio       (onedir; universal2 binary when requested)

Platform-specific hidden imports are selected at spec-evaluation time from
sys.platform so the same spec drives the Windows (WinForms + pythonnet /
WebView2) and macOS (Cocoa + pyobjc) builds.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent.resolve()

IS_MAC = sys.platform == "darwin"

_PLATFORM_HIDDENIMPORTS = [
    # pywebview picks its platform backend lazily at runtime.
    "webview.platforms.winforms",
    # pythonnet bridge (Windows only).
    "clr",
    "pythonnet",
]
if IS_MAC:
    _PLATFORM_HIDDENIMPORTS = [
        "webview.platforms.cocoa",
        # pyobjc frameworks consumed by webview/platforms/cocoa.py.
        "AppKit",
        "Foundation",
        "WebKit",
        "objc",
        "PyObjCTools",
    ]

a = Analysis(
    [str(ROOT / "clio" / "desktop" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "clio" / "ui" / "static"), "clio/ui/static"),
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "config.example.yaml"), "clio/config"),
        # License + third-party attribution ship inside the bundle (GAP-P2-15).
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY.md"), "."),
    ],
    hiddenimports=[
        # Lazy `from tkinter import ...` inside dialogs.py / app.py.
        "tkinter",
        *_PLATFORM_HIDDENIMPORTS,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "desktop_rthook.py")],
    excludes=[
        # Whisper is an optional feature installed on demand via `whisper install`
        # (a pip subprocess). Bundling its ML stack (torch + transformers +
        # ctranslate2 + tokenizers + PyAV) would balloon the onedir to ~4 GB.
        "faster_whisper",
        "ctranslate2",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tokenizers",
        "accelerate",
        "av",
        "soundfile",
        "nvidia",
        "triton",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

# console=False: windowed app on both platforms (desktop_rthook keeps
# sys.stdout/stderr usable via os.devnull).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="clio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="clio",
)

# macOS ships GUI apps as a .app bundle (needed for window focus, menus, Dock).
if IS_MAC:
    app = BUNDLE(
        coll,
        name="clio.app",
        bundle_identifier="com.clio.desktop",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleName": "Clio",
            "CFBundleDisplayName": "Clio",
        },
    )
