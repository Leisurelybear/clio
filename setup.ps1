# Clio 一键配置脚本（PowerShell）
# 用法: .\setup.ps1

$ErrorActionPreference = "Stop"
if ($env:CLIO_SETUP_DRYRUN -eq "1") {
    Write-Host "[dry-run] 脚本语法与结构检查通过"
    exit 0
}
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== Clio - 环境配置 ===" -ForegroundColor Cyan

# ---- Python 版本检查（项目要求 3.11+）----
$pyCmdStr = "python"
$pyOk = $false
$major = $null; $minor = $null

# 先看 python 命令是否存在（避免 CommandNotFoundException）
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyVer = python --version 2>&1
    if ($LASTEXITCODE -eq 0 -and $pyVer -match 'Python (\d+)\.(\d+)') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -ge 3 -and ($major -gt 3 -or $minor -ge 11)) {
            Write-Host "     Python $major.$minor - 版本检查通过" -ForegroundColor Green
            $pyOk = $true
        } else {
            Write-Host "     当前系统默认 Python 为 $major.$minor（需 3.11+），尝试 Python Launcher..." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "     系统默认 python 不可用，尝试 Python Launcher..." -ForegroundColor Yellow
}

# 如果默认 python 不达标或不可用，用 Python Launcher 找高版本
if (-not $pyOk) {
    $found = $false
    # py 也不是一定存在，先确认
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($tryVer in @("3.13", "3.12", "3.11")) {
            $v = & py "-$tryVer" --version 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -match "Python $tryVer") {
                Write-Host "     找到 Python $tryVer（通过 Python Launcher），将使用 py -$tryVer" -ForegroundColor Green
                $pyCmdStr = "py -$tryVer"
                $found = $true
                break
            }
        }
    }
    # 还是没有，直接扫常见安装目录
    if (-not $found) {
        Write-Host "     尝试在常见安装目录中查找..." -ForegroundColor Yellow
        $searchDirs = @(
            "$env:LOCALAPPDATA\Programs\Python",
            "C:\Program Files\Python",
            "C:\Python",
            "$env:ProgramFiles\Python"
        )
        foreach ($dir in $searchDirs) {
            if (Test-Path $dir) {
                $exes = Get-ChildItem "$dir\Python3*\python.exe" -ErrorAction SilentlyContinue
                foreach ($exe in $exes) {
                    $v = & $exe.FullName --version 2>&1
                    if ($v -match 'Python (\d+)\.(\d+)') {
                        $mv = [int]$Matches[1]; $nv = [int]$Matches[2]
                        if ($mv -ge 3 -and ($mv -gt 3 -or $nv -ge 11)) {
                            Write-Host "     找到 Python $mv.$nv（位于 $($exe.FullName)）" -ForegroundColor Green
                            $pyCmdStr = "& `"$($exe.FullName)`""
                            $found = $true
                            break
                        }
                    }
                }
            }
            if ($found) { break }
        }
    }

    if (-not $found) {
        Write-Host "错误：未检测到 Python 3.11+" -ForegroundColor Red
        Write-Host "      下载: https://python.org/downloads/" -ForegroundColor Cyan
        Write-Host "      推荐安装包（64 位）:" -ForegroundColor Cyan
        Write-Host "      https://python.org/ftp/python/3.12.5/python-3.12.5-amd64.exe" -ForegroundColor Cyan
        Write-Host "      （注意：Microsoft Store 版可能缺少 ensurepip，请用官网安装包）" -ForegroundColor Yellow
        $choice = Read-Host "      是否在浏览器中打开下载页面? (Y/n)"
        if ($choice -ne 'n' -and $choice -ne 'N') {
            Start-Process "https://python.org/downloads/"
        }
        exit 1
    }
}

# 1. Python 虚拟环境
if (Test-Path ".venv\Scripts\python.exe") {
    $venvVer = & ".venv\Scripts\python.exe" --version 2>&1
    if ($venvVer -match 'Python (\d+)\.(\d+)') {
        $vmajor = [int]$Matches[1]; $vminor = [int]$Matches[2]
        if ($vmajor -lt 3 -or ($vmajor -eq 3 -and $vminor -lt 11)) {
            Write-Host "     虚拟环境 Python 版本过旧 ($vmajor.$vminor)，正在重建..." -ForegroundColor Yellow
            Remove-Item ".venv" -Recurse -Force
        }
    }
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/4] 创建 Python 虚拟环境..."
    Invoke-Expression "$pyCmdStr -m venv .venv"
} else {
    Write-Host "[1/4] 虚拟环境已存在，跳过"
}

# 确保虚拟环境中有 pip（某些 Windows Python 安装不会自带）
& ".venv\Scripts\python.exe" -c "import pip" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "     正在通过 ensurepip 安装 pip..." -ForegroundColor Yellow
    & ".venv\Scripts\python.exe" -m ensurepip --upgrade --default-pip
    if ($LASTEXITCODE -ne 0) {
        Write-Host "     ensurepip 不可用，下载 get-pip.py..." -ForegroundColor Yellow
        $getPip = "$env:TEMP\get-pip.py"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing -TimeoutSec 30
            & ".venv\Scripts\python.exe" $getPip
        } catch {
            Write-Host "     无法安装 pip，请检查网络或手动安装: python -m ensurepip --upgrade" -ForegroundColor Red
            exit 1
        } finally {
            if (Test-Path $getPip) { Remove-Item $getPip -Force }
        }
    }
}

Write-Host "[2/4] 安装 Python 依赖..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Host "     警告: pip 升级失败，继续安装依赖..." -ForegroundColor Yellow }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "     [错误] 依赖安装失败，请检查网络或代理设置" -ForegroundColor Red; exit 1 }

# Whisper 语音转录（可选加速）
$whisperAnswer = Read-Host "是否安装 Whisper 语音转录? (y/N, 默认 N)"
if ($whisperAnswer -eq 'y' -or $whisperAnswer -eq 'Y') {
    Write-Host "[2b] 安装 faster-whisper 及推理依赖..." -ForegroundColor Yellow
    .\.venv\Scripts\python.exe -m pip install -r requirements-whisper.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "     Whisper 依赖安装失败，跳过后续步骤" -ForegroundColor Red
        Write-Host "     可后续运行 'python -m clio whisper install' 单独安装" -ForegroundColor DarkYellow
    } else {
        # 检测 CUDA 显卡（nvidia-smi 可能在非标准路径）
        try {
            $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
            if (-not $nvidiaSmi) {
                $nvidiaSmi = Get-ChildItem "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe" -ErrorAction SilentlyContinue
                if ($nvidiaSmi) { $nvidiaSmi = $nvidiaSmi.FullName }
            }
            if ($nvidiaSmi) {
                Write-Host "     检测到 NVIDIA GPU，安装 CUDA 运行时加速..." -ForegroundColor Green
                # CUDA 包 ~3GB，检查磁盘空间
                $cudaArgs = @()
                $drive = (Get-PSDrive -Name ($pwd.Drive.Name) -ErrorAction SilentlyContinue)
                if ($drive -and $drive.Free -lt 5GB) {
                    Write-Host "     C 盘仅剩 $([math]::Round($drive.Free/1GB, 1))GB，使用无缓存模式安装..." -ForegroundColor Yellow
                    $cudaArgs = @("--no-cache-dir")
                }
                .\.venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 @cudaArgs
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "     CUDA 运行时安装失败，将使用 CPU 运行（速度较慢）" -ForegroundColor DarkYellow
                    if ($LASTEXITCODE -eq 28) {
                        Write-Host "     原因：磁盘空间不足，请清理磁盘后重试，或跳过 CUDA 直接使用 CPU" -ForegroundColor DarkYellow
                    }
                }
            }
        } catch {
            Write-Host "     未检测到 NVIDIA GPU，将使用 CPU 运行（速度较慢）" -ForegroundColor DarkYellow
        }
    }
} else {
    Write-Host "     跳过 Whisper 安装，后续可运行 'python -m clio whisper install' 单独安装" -ForegroundColor DarkYellow
}

# 2. ffmpeg
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    # 检查常见安装路径
    $ffmpegCandidates = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\*\ffmpeg.exe",
        "C:\ProgramData\chocolatey\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe",
        "$env:USERPROFILE\scoop\apps\ffmpeg\current\bin\ffmpeg.exe",
        "C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "C:\Tools\ffmpeg\bin\ffmpeg.exe"
    )
    $foundFfmpeg = $null
    foreach ($p in $ffmpegCandidates) {
        $foundFfmpeg = Resolve-Path $p -ErrorAction SilentlyContinue
        if ($foundFfmpeg) { break }
    }
    if ($foundFfmpeg) {
        Write-Host "[3/4] 检测到 ffmpeg: $($foundFfmpeg.Path)"
        # 将 bin 目录临时加入 PATH
        $env:PATH = "$(Split-Path $foundFfmpeg.Path);$env:PATH"
    } else {
        Write-Host "[3/4] 未检测到 ffmpeg，尝试通过 winget 安装..."
        winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "     winget 安装失败，请手动下载安装:" -ForegroundColor Yellow
            Write-Host "     https://ffmpeg.org/download.html#build-windows" -ForegroundColor Cyan
            Write-Host "     下载后请将 bin 目录加入 PATH 环境变量，或设置 FFMPEG_HOME" -ForegroundColor DarkYellow
        }
    }
} else {
    Write-Host "[3/4] ffmpeg 已就绪: $($ffmpeg.Source)"
}

# 3. .env
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[4/4] 已创建 .env，请编辑并填入 GEMINI_API_KEY"
} else {
    Write-Host "[4/4] .env 已存在"
}
# Restrict .env to the current user only (other local users must not read keys).
try {
    icacls ".env" /inheritance:r /grant:r "$env:USERNAME:F" | Out-Null
} catch {
    Write-Host "[*] 警告: 未能限制 .env 访问权限: $_" -ForegroundColor DarkYellow
}

# 4. config.yaml
if (-not (Test-Path "config.yaml") -and (Test-Path "config.example.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
    Write-Host "[*] 已创建 config.yaml，请按需修改 paths / proxy"
}

# 5. Git hooks
$hooksPath = git config core.hooksPath
if ($hooksPath -ne ".githooks") {
    Write-Host "[5/5] 设置 git hooks 路径为 .githooks..."
    git config core.hooksPath .githooks
} else {
    Write-Host "[5/5] git hooks 路径已配置为 .githooks"
}

Write-Host ""
Write-Host "=== 环境检查 ===" -ForegroundColor Cyan
Write-Host "  注意: 请先编辑 .env 填入 API Key，再运行环境检查" -ForegroundColor DarkYellow
Write-Host "  .\.venv\Scripts\python.exe -m clio check" -ForegroundColor Cyan
