"""GAP-P2-14: setup.ps1 must not download floating get-pip without hash pin."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_setup_ps1_pins_get_pip_sha256():
    text = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    assert "bootstrap.pypa.io/get-pip.py" not in text
    assert "getPipSha256" in text
    assert "0f8bb2652c0b0965f268312f49ec21e772d421d381af4324beea66b8acf2635c" in text
    assert "Get-FileHash" in text
    assert "ensurepip" in text


def test_setup_scripts_use_verified_ffmpeg_fallback():
    ps1 = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert "clio.ffmpeg_install --mode download" in ps1
    assert "clio.ffmpeg_install --mode download" in sh
    assert "ffmpeg.org/download.html" in ps1
    assert "ffmpeg.org/download.html" in sh
    assert "Microsoft\\WinGet\\Links" in ps1
    assert "winget 未提供可用的 ffmpeg/ffprobe" in ps1
    assert "brew install ffmpeg || true" in sh
