#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== Clio - 环境配置 ==="

python_bin="${PYTHON:-python3}"

echo "[0/5] 检查 Python 版本..."
if ! py_ver="$($python_bin --version 2>&1)"; then
    echo "错误：未检测到 Python3，请安装 Python 3.11 或更高版本。" >&2
    echo "下载地址：https://python.org/downloads/" >&2
    exit 1
fi

if [[ $py_ver =~ Python\ ([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if [[ $major -lt 3 || ( $major -eq 3 && $minor -lt 11 ) ]]; then
        echo "错误：需要 Python 3.11+，当前版本：$py_ver" >&2
        case "$(uname -s)" in
            Darwin) echo "建议：brew install python@3.12" >&2 ;;
            Linux) echo "建议：使用系统包管理器安装 Python 3.11+ 和 python3-venv。" >&2 ;;
        esac
        exit 1
    fi
    echo "Python $major.$minor 检查通过"
else
    echo "错误：无法解析 Python 版本：$py_ver" >&2
    exit 1
fi

echo "[1/5] 准备 Python 虚拟环境..."
if [[ -x ".venv/bin/python" ]]; then
    venv_ver="$(".venv/bin/python" --version 2>&1 || true)"
    if [[ $venv_ver =~ Python\ ([0-9]+)\.([0-9]+) ]]; then
        vmajor="${BASH_REMATCH[1]}"
        vminor="${BASH_REMATCH[2]}"
        if [[ $vmajor -lt 3 || ( $vmajor -eq 3 && $vminor -lt 11 ) ]]; then
            echo "虚拟环境 Python 版本过旧（$vmajor.$vminor），正在重建..."
            rm -rf ".venv"
        fi
    fi
fi

if [[ ! -x ".venv/bin/python" ]]; then
    if ! "$python_bin" -m venv .venv; then
        echo "创建虚拟环境失败。Debian/Ubuntu 可尝试：sudo apt install python3-venv" >&2
        exit 1
    fi
else
    echo "虚拟环境已存在，跳过创建"
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "正在通过 ensurepip 安装 pip..."
    .venv/bin/python -m ensurepip --upgrade --default-pip
fi

echo "[2/5] 安装 Python 依赖..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

read -r -p "是否安装 Whisper 语音转录依赖？(y/N) " whisper_answer
if [[ "$whisper_answer" == "y" || "$whisper_answer" == "Y" ]]; then
    echo "安装 faster-whisper 及推理依赖..."
    if ! .venv/bin/python -m pip install -r requirements-whisper.txt; then
        echo "Whisper 依赖安装失败，后续可运行 python main.py whisper install 单独安装。" >&2
    elif command -v nvidia-smi >/dev/null 2>&1; then
        echo "检测到 NVIDIA GPU，尝试安装 CUDA 运行时依赖..."
        cuda_args=()
        free_kb="$(df -k . | awk 'NR==2{print $4}' 2>/dev/null || echo 0)"
        if [[ "$free_kb" =~ ^[0-9]+$ && "$free_kb" -gt 0 && "$free_kb" -lt $((5 * 1024 * 1024)) ]]; then
            echo "磁盘剩余空间较少，使用无缓存模式安装 CUDA 依赖..."
            cuda_args=(--no-cache-dir)
        fi
        if ! .venv/bin/python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 "${cuda_args[@]}"; then
            echo "CUDA 运行时依赖安装失败，将继续使用 CPU 或现有环境。" >&2
        fi
    fi
else
    echo "跳过 Whisper 依赖安装，后续可运行 python main.py whisper install。"
fi

echo "[3/5] 检查 ffmpeg..."
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "ffmpeg 已就绪：$(command -v ffmpeg)"
else
    echo "未检测到完整 ffmpeg/ffprobe，尝试使用系统包管理器安装..."
    case "$(uname -s)" in
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                sudo apt-get update -qq && sudo apt-get install -y ffmpeg
            elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y ffmpeg
            elif command -v pacman >/dev/null 2>&1; then
                sudo pacman -S --noconfirm ffmpeg
            elif command -v zypper >/dev/null 2>&1; then
                sudo zypper install -y ffmpeg
            else
                echo "无法自动安装 ffmpeg，请手动安装：https://ffmpeg.org/download.html" >&2
            fi
            ;;
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                brew install ffmpeg
            else
                echo "请先安装 Homebrew，然后运行：brew install ffmpeg" >&2
            fi
            ;;
        *)
            echo "当前系统未适配自动安装 ffmpeg，请手动安装：https://ffmpeg.org/download.html" >&2
            ;;
    esac
fi

echo "[4/5] 准备本地配置文件..."
if [[ ! -f ".env" && -f ".env.example" ]]; then
    cp ".env.example" ".env"
    echo "已创建 .env，请填写 GEMINI_API_KEY / DEEPSEEK_API_KEY 等密钥。"
else
    echo ".env 已存在或缺少 .env.example，跳过"
fi

if [[ ! -f "config.yaml" && -f "config.example.yaml" ]]; then
    cp "config.example.yaml" "config.yaml"
    echo "已创建 config.yaml，请按需修改 paths / proxy / ai.providers。"
else
    echo "config.yaml 已存在或缺少 config.example.yaml，跳过"
fi

echo "[5/5] 配置 git hooks..."
# Entry-point hooks are tracked executable (0o755) so a fresh clone runs them;
# re-assert perms in case an older checkout or filesystem dropped the bit.
chmod +x .githooks/pre-commit .githooks/pre-push 2>/dev/null || true
hooks_path="$(git config core.hooksPath || true)"
if [[ "$hooks_path" != ".githooks" ]]; then
    git config core.hooksPath .githooks
    echo "git hooks 路径已设置为 .githooks"
else
    echo "git hooks 路径已是 .githooks"
fi

echo ""
echo "=== 环境配置完成 ==="
echo "请先编辑 .env 填入 API Key，然后运行："
echo "  source .venv/bin/activate"
echo "  python main.py check"
echo ""
echo "启动 Web UI："
echo "  python main.py serve --no-browser"
