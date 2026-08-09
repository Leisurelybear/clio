# Clio Desktop — PyInstaller onedir build

Packages the desktop shell (`python -m clio.desktop`) as a standalone folder
(onedir) with no Python needed on the target machine.

Supported platforms:

| Platform      | Artifact          | Backend |
|---------------|-------------------|---------|
| Windows x64   | `dist/clio/clio.exe` | WebView2 (EdgeChromium) via pythonnet |
| macOS (Intel + Apple Silicon) | `dist/clio.app` (universal2) | Cocoa/WebKit via pyobjc |
| Windows ARM64 | use the x64 build  | runs under built-in x64 emulation |

Windows ARM64 has no native build: pythonnet publishes no `win_arm64` wheel, so
we ship the x64 artifact and let Windows on ARM translate it. This matches
Microsoft's guidance for Win32 apps.

## Requirements (build machine)

- Python 3.10+ (macOS universal2 builds need a **universal2** CPython, e.g. the
  python.org installer — a single-arch Homebrew python cannot emit universal2)
- Windows: `pip install pyinstaller pywebview pythonnet`
- macOS: `pip install pyinstaller pywebview pyobjc-core pyobjc-framework-Cocoa
  pyobjc-framework-WebKit pyobjc-framework-Quartz pyobjc-framework-Security`
- ffmpeg / ffprobe discoverable by the target machine (see below)

## Build (Windows)

```powershell
# one-click
.\packaging\build-desktop.ps1

# or manual
python -m PyInstaller packaging/clio.spec --noconfirm --clean
```

Output: `dist/clio/clio.exe` (onedir — keep the whole `dist/clio` folder
together; do not move the exe alone).

Launch:

```powershell
.\dist\clio\clio.exe          # uses ./config.yaml in the current directory, or
                             # falls back to the platform config dir when absent
                             # (Windows: %APPDATA%\Clio, macOS: ~/Library/Application
                             # Support/Clio, Linux: ~/.config/clio)
.\dist\clio\clio.exe -c .\project\config.yaml
```

## Build (macOS)

```bash
# one-click (auto-installs pyobjc; universal2 when the interpreter is)
bash packaging/build-desktop-macos.sh

# or manual (universal2 requires a universal2 CPython)
python3 -m pip install pyinstaller "pywebview" "pyobjc-core" \
  "pyobjc-framework-Cocoa" "pyobjc-framework-WebKit" \
  "pyobjc-framework-Quartz" "pyobjc-framework-Security"
python3 -m PyInstaller packaging/clio.spec --target-architecture universal2 --noconfirm --clean
```

Output: `dist/clio.app` (a `.app` bundle, universal2 when built with
`--target-architecture universal2`). Verify the architecture:

```bash
lipo -info dist/clio.app/Contents/MacOS/clio   # expect: Architectures ... universal2
```

Launch:

```bash
open dist/clio.app                             # uses ./config.yaml in the CWD
open dist/clio.app --args -c ./project/config.yaml
```

The macOS `.app` is **unsigned and not notarized**; first launch on another
machine will be blocked by Gatekeeper. Right-click → Open to allow once, or
`xattr -dr com.apple.quarantine dist/clio.app` after transfer. For distribution
outside your own machines, sign with a Developer ID cert and notarize
(`codesign --deep --force --options runtime`, then `notarytool submit`).

## CI release (GitHub Actions)

`.github/workflows/release.yml` builds both artifacts and publishes a GitHub
Release with both zips:

- `windows-latest` → `clio-<ver>-windows-x64.zip` (`dist/clio` onedir)
- `macos-26-intel` (universal2 CPython from python.org) →
  `clio-<ver>-macos-universal2.zip` (`dist/clio.app` + README)

```bash
git tag v0.1.0
git push origin v0.1.0
```

Or run it manually from the Actions tab (version defaults to the tag, or
`0.0.0-dev` for manual runs without a tag). Manual runs only upload build
artifacts; real GitHub Releases are created for `v*` tags.

On first launch, if no `config.yaml` is found in the target directory, the app
auto-creates one from the bundled `config.example.yaml` template instead of
crashing. Fill in your API keys / `.env` afterward and relaunch.

The app serves the UI on a random loopback port and opens a native window.
Closing the window stops the local server; closing during a run asks for
confirmation and sends a cancel request first.

## What is bundled

- `clio/**` Python package + `clio/ui/static` web UI assets
- `templates/` (trip_context.md, prompt overrides)
- `config.example.yaml` (first-launch template for auto-generated `config.yaml`)
- pywebview engine: WebView2 (EdgeChromium) via pythonnet on Windows; Cocoa
  WKWebView via pyobjc on macOS

## Caveats

### WebView2 Evergreen runtime (Windows only)

The Windows window is rendered by the **WebView2 Runtime** (Microsoft Edge
Chromium). Windows 11 ships it; Windows 10 needs the Evergreen runtime installed
<https://developer.microsoft.com/microsoft-edge/webview2/>. Without it the
window will fail to open. On macOS the app uses the system WKWebView, so no
extra runtime is needed.

### Unsigned exe — SmartScreen warning (Windows)

The Windows build is unsigned, so SmartScreen shows "Windows protected your PC"
on first run. Workarounds:

- **More info → Run anyway** (per machine)
- Unblock once: `Unblock-File .\dist\clio\clio.exe`
- Ship a code-signing certificate for production

### ffmpeg / ffprobe

The app shells out to `ffmpeg` / `ffprobe` (compression, waveform, cutting,
transcription audio extraction). These are **not** bundled — the target machine
must have them on `PATH`, or `config.yaml` `paths.ffmpeg` / `paths.ffprobe`
must point at them.

**Install on Windows** (any one option):

1. **winget** (Windows 10/11 package manager):
   ```powershell
   winget install Gyan.FFmpeg
   ```
2. **Chocolatey**:
   ```powershell
   choco install ffmpeg
   ```
3. **Manual** — download a release build (gyan.dev or BtbN), extract to
   `C:\ffmpeg`, then add `C:\ffmpeg\bin` to `PATH` (or set `paths.ffmpeg` /
   `paths.ffprobe` in `config.yaml`).

**Install on macOS**:

```bash
brew install ffmpeg          # or download the evermeet.cx / osxexperts build
```

In the app, the top banner shows "如何安装" when ffmpeg is missing; after
installing, click "重新检测" (or reload the window). `setup.ps1` also installs
ffmpeg for dev machines.

### Whisper transcription is not bundled

`faster-whisper` (torch + transformers + ctranslate2 ≈ 4 GB) is excluded from
the bundle. Transcription still works when running from source
(`python main.py whisper install`); in the packaged app the transcription step
reports that whisper is unavailable.

### API keys

Keys are read from environment variables / `.env` next to `config.yaml`. Never
bake keys into the build.

## Measured cold-start (2026-08-01, Windows 10, x64)

- Onedir size: ~123 MB (excl. whisper ML stack)
- Exe launch → loopback HTTP ready → SPA index 200: **~2.1 s**