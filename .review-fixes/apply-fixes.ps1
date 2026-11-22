<#
  apply-fixes.ps1 - apply the code-review fix patches in order, one commit per fix.

  The patches were generated against your CURRENT WORKING TREE (including uncommitted
  and untracked files such as clio/plan_pacing.py), not against HEAD.

  Run from anywhere (PowerShell):
      powershell -ExecutionPolicy Bypass -File .review-fixes\apply-fixes.ps1 -DryRun   # check only, changes nothing
      powershell -ExecutionPolicy Bypass -File .review-fixes\apply-fixes.ps1           # apply + commit

  Before the real run, commit (or otherwise save) your own in-progress work, so that every
  fix commit contains only that fix. The real run refuses to start on a dirty tree.

  Options:
      -Branch <name>  branch to create/use (default: fix/code-review-2026-11-22)
      -DryRun         apply all patches in sequence on a scratch copy of the touched files
      -From <rel>     resume from a patch, e.g. -From "G2\005_B-07.patch"
      -RunHooks       run git hooks on each commit (not recommended: the pre-commit hook
                      reformats files, which can make later patches stop applying)
      -NoFormat       skip the final "ruff format" commit
#>
param(
    [string]$Branch = "fix/code-review-2026-11-22",
    [switch]$DryRun,
    [string]$From = "",
    [switch]$RunHooks,
    [switch]$NoFormat
)
# Native tools (git) write progress to stderr; don't let PowerShell turn that into errors.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
Set-Location $repo

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $out = & git -c core.quotepath=false @GitArgs 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $out | ForEach-Object { Write-Host "  $_" }
        throw "git $($GitArgs -join ' ') failed (exit $code)"
    }
    return $out
}

function Get-PatchFiles([string]$patchPath) {
    $files = @()
    foreach ($m in (Select-String -Path $patchPath -Pattern '^diff --git a/(\S+) b/(\S+)')) {
        $files += $m.Matches[0].Groups[2].Value
    }
    return $files | Sort-Object -Unique
}

$patches = @(Get-ChildItem -Path $here -Recurse -Filter *.patch |
    Sort-Object @{Expression = { $_.Directory.Name }}, Name)
if ($From) {
    $start = -1
    for ($i = 0; $i -lt $patches.Count; $i++) { if ($patches[$i].FullName.EndsWith($From)) { $start = $i; break } }
    if ($start -lt 0) { Write-Host "Patch '$From' not found" -ForegroundColor Red; exit 1 }
    $patches = $patches[$start..($patches.Count - 1)]
}
Write-Host "Found $($patches.Count) patches." -ForegroundColor Cyan

# ---------- dry run: scratch git repo seeded with the current versions of every touched file ----------
if ($DryRun) {
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ("clio-fixcheck-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $scratch | Out-Null
    $all = @()
    foreach ($p in $patches) { $all += Get-PatchFiles $p.FullName }
    $all = $all | Sort-Object -Unique
    foreach ($f in $all) {
        $src = Join-Path $repo $f
        if (Test-Path -LiteralPath $src) {
            $dst = Join-Path $scratch $f
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -LiteralPath $src -Destination $dst
        }
    }
    Push-Location $scratch
    & git init -q 2>&1 | Out-Null
    & git config core.autocrlf false
    $n = 0; $failed = $false
    foreach ($p in $patches) {
        $rel = $p.FullName.Substring($here.Length + 1)
        $out = & git apply --whitespace=nowarn -- "$($p.FullName)" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[FAIL] $rel" -ForegroundColor Red
            $out | ForEach-Object { Write-Host "  $_" }
            $failed = $true; break
        }
        $n++
    }
    Pop-Location
    Remove-Item -Recurse -Force -LiteralPath $scratch -ErrorAction SilentlyContinue
    if ($failed) { exit 2 }
    Write-Host "[ok] all $n patches apply in sequence on the current working tree." -ForegroundColor Green
    exit 0
}

# ---------- real run ----------
$dirty = @(& git status --porcelain --untracked-files=no)
$untrackedTouched = @()
foreach ($p in $patches) {
    foreach ($f in (Get-PatchFiles $p.FullName)) {
        & git ls-files --error-unmatch -- $f *> $null
        if ($LASTEXITCODE -ne 0 -and (Test-Path -LiteralPath (Join-Path $repo $f))) { $untrackedTouched += $f }
    }
}
$untrackedTouched = $untrackedTouched | Sort-Object -Unique
if ($dirty.Count -gt 0 -or $untrackedTouched.Count -gt 0) {
    Write-Host "Your working tree has uncommitted work the patches are based on." -ForegroundColor Yellow
    if ($dirty.Count -gt 0) { Write-Host "Modified tracked files: $($dirty.Count)" }
    if ($untrackedTouched.Count -gt 0) {
        Write-Host "Untracked files that patches modify:"
        $untrackedTouched | ForEach-Object { Write-Host "  $_" }
    }
    Write-Host "Commit that work first (e.g. git add -A; git commit -m 'wip: ...'), then rerun." -ForegroundColor Yellow
    exit 1
}
$current = (& git rev-parse --abbrev-ref HEAD).Trim()
if ($current -ne $Branch) {
    & git rev-parse --verify --quiet "refs/heads/$Branch" *> $null
    if ($LASTEXITCODE -eq 0) { Invoke-Git switch -q $Branch | Out-Null } else { Invoke-Git switch -q -c $Branch | Out-Null }
}

$done = 0
foreach ($p in $patches) {
    $rel = $p.FullName.Substring($here.Length + 1)
    $msg = [IO.Path]::ChangeExtension($p.FullName, ".msg")
    $out = & git apply --check --whitespace=nowarn -- "$($p.FullName)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $out | ForEach-Object { Write-Host "  $_" }
        Write-Host "[FAIL] $rel does not apply. Nothing was changed for it. Fix, then rerun with -From `"$rel`"" -ForegroundColor Red
        exit 2
    }
    try {
        Invoke-Git apply --whitespace=nowarn -- $p.FullName | Out-Null
        $files = @(Get-PatchFiles $p.FullName)
        Invoke-Git add -A -- @files | Out-Null
        $commitArgs = @("commit", "-q", "-F", $msg)
        if (-not $RunHooks) { $commitArgs += "--no-verify" }
        Invoke-Git @commitArgs | Out-Null
    } catch {
        Write-Host "[FAIL] $rel : $_" -ForegroundColor Red
        Write-Host "If its changes are staged, run: git commit -F `"$msg`"  then rerun with -From <next patch>"
        exit 3
    }
    Write-Host "[done] $rel" -ForegroundColor Green
    $done++
}
Write-Host "$done / $($patches.Count) patches committed." -ForegroundColor Cyan

if (-not $NoFormat) {
    $ruff = $null
    if (Test-Path ".venv\Scripts\ruff.exe") { $ruff = ".venv\Scripts\ruff.exe" }
    elseif (Get-Command ruff -ErrorAction SilentlyContinue) { $ruff = "ruff" }
    if ($ruff) {
        & $ruff format clio main.py | Out-Host
        $changed = @(& git status --porcelain --untracked-files=no)
        if ($changed.Count -gt 0) {
            Invoke-Git add -u | Out-Null
            Invoke-Git commit -q --no-verify -m "style: apply ruff format after review fixes" | Out-Null
            Write-Host "[done] ruff format commit" -ForegroundColor Green
        } else { Write-Host "ruff format: nothing to change." }
    } else { Write-Host "ruff not found; skipped the formatting commit." -ForegroundColor Yellow }
}
Write-Host "Next: .venv\Scripts\python -m pytest -q   and   npm test" -ForegroundColor Cyan
