# packaging/build-desktop.ps1
# Build the Clio desktop shell as a PyInstaller onedir (R-032c).
# Requires: pip install pyinstaller pywebview pythonnet
param(
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $extra = @()
    if (-not $NoClean) {
        $extra += "--clean"
    }
    python -m PyInstaller $extra packaging/clio.spec --noconfirm
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
    # Ship the desktop README (WebView2 / SmartScreen / ffmpeg notes) plus the
    # license and third-party notices next to the exe (GAP-P2-15).
    Copy-Item "$root\packaging\README-desktop.md" (Join-Path $root "dist\clio\README-desktop.md") -Force
    Copy-Item "$root\LICENSE" (Join-Path $root "dist\clio\LICENSE") -Force
    Copy-Item "$root\THIRD_PARTY.md" (Join-Path $root "dist\clio\THIRD_PARTY.md") -Force
    Write-Host ""
    Write-Host "Built: dist/clio/clio.exe" -ForegroundColor Green
    Write-Host "Smoke test: & .\dist\clio\clio.exe"
} finally {
    Pop-Location
}
