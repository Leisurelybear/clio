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
