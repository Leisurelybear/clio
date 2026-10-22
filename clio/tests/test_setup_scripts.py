"""Static regression checks for setup-script platform fallbacks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_setup_guards_missing_winget_before_static_fallback():
    text = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    guard = text.index("Get-Command winget")
    install = text.index("install --id Gyan.FFmpeg")
    fallback = text.index("clio.ffmpeg_install --mode download")

    assert guard < install < fallback


def test_unix_setup_does_not_download_on_unsupported_or_macos_platforms():
    text = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert text.count("ffmpeg_download_supported=0") >= 2
    assert 'if [[ "$ffmpeg_download_supported" -eq 1 ]]' in text
    assert "managed_install_root" in text
    assert '")" +' not in text
    assert 'managed_dir="${CLIO_FFMPEG_DIR:-$HOME/.local/share/clio/ffmpeg}"' in text
    assert 'export PATH="$managed_dir:$PATH"' in text
    assert 'brew_prefix="$(brew --prefix 2>/dev/null || true)"' in text
    assert 'export PATH="$brew_prefix/bin:$PATH"' in text
