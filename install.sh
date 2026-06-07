#!/usr/bin/env bash
# ============================================
# MH-DeepSeek Agent V4 — Linux/Termux/macOS 一键安装
# 用法: bash install.sh
# ============================================

set -e

# ── 颜色 ──────────────────────────────────────
RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'
BLUE='\033[34m'; CYAN='\033[36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

step()   { echo -e "\n${BOLD}[$1]${RESET} $2"; }
ok()     { echo -e "  ${GREEN}✅ $1${RESET}"; }
warn()   { echo -e "  ${YELLOW}⚠️  $1${RESET}"; }
info()   { echo -e "  ${DIM}ℹ️  $1${RESET}"; }
header() { echo -e "${BOLD}${BLUE}$1${RESET}"; }

# ── 平台检测 ──────────────────────────────────
if [ -n "$ANDROID_ROOT" ] || [ -d /data/data/com.termux ]; then
    PLATFORM="android"
elif [ "$(uname)" = "Darwin" ]; then
    PLATFORM="macos"
elif [ "$(uname)" = "Linux" ]; then
    PLATFORM="linux"
else
    echo -e "${RED}不支持的操作系统${RESET}"
    exit 1
fi

echo ""
header "============================================"
echo -e "  MH-DeepSeek Agent V4 — ${PLATFORM} 安装"
header "============================================"
echo ""

# ════════════════════════════════════════════════
# 1. 检查 Python
# ════════════════════════════════════════════════
step "1/5" "检查 Python 环境..."

PY_CMD=""
for cmd in python3 python; do
    if command -v $cmd &>/dev/null; then
        PY_CMD=$cmd
        break
    fi
done

if [ -z "$PY_CMD" ]; then
    echo -e "  ${RED}✗ Python 未安装！${RESET}"
    echo ""
    echo "  请先安装 Python 3.10+:"
    case "$PLATFORM" in
        android) echo "    pkg install python" ;;
        linux)   echo "    sudo apt install python3 python3-pip python3-venv" ;;
        macos)   echo "    brew install python@3.12" ;;
    esac
    exit 1
fi

PY_VER=$($PY_CMD --version 2>&1)
ok "$PY_VER"

# 确保 pip 可用
PIP_CMD=""
for cmd in pip3 pip; do
    if command -v $cmd &>/dev/null; then
        PIP_CMD=$cmd
        break
    fi
done
if [ -z "$PIP_CMD" ]; then
    $PY_CMD -m ensurepip --upgrade 2>/dev/null || true
    PIP_CMD="$PY_CMD -m pip"
fi
ok "pip: $PIP_CMD"

# ════════════════════════════════════════════════
# 2. 系统依赖
# ════════════════════════════════════════════════
step "2/5" "安装系统级依赖..."

case "$PLATFORM" in
    android)
        if command -v pkg &>/dev/null; then
            pkg update -y 2>/dev/null || true
            for pkg_name in binutils libsndfile python; do
                pkg install -y "$pkg_name" 2>/dev/null && ok "$pkg_name" || warn "$pkg_name 跳过"
            done
        fi
        ;;
    linux)
        if command -v apt &>/dev/null; then
            sudo apt-get update -qq 2>/dev/null || true
            sudo apt-get install -y -qq python3-pip python3-venv libsndfile1 2>/dev/null && \
                ok "系统依赖" || warn "部分系统包安装失败"
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm python-pip libsndfile 2>/dev/null || true
        fi
        ;;
    macos)
        if command -v brew &>/dev/null; then
            brew install libsndfile 2>/dev/null || true
        fi
        ;;
esac

# ════════════════════════════════════════════════
# 3. 升级 pip
# ════════════════════════════════════════════════
step "3/5" "升级 pip..."
$PIP_CMD install --upgrade pip setuptools wheel -q 2>/dev/null && \
    ok "pip 已升级" || warn "pip 升级跳过"

# ════════════════════════════════════════════════
# 4. Python 依赖
# ════════════════════════════════════════════════
step "4/5" "安装 Python 依赖..."

# 核心
CORE="flask flask-cors requests pycryptodome cryptography beautifulsoup4 psutil dnspython Pillow colorama"
# 网页抓取（Scrapling — 隐身模式反反爬）
SCRAPLING="scrapling>=0.2.0"

echo -e "  ${CYAN}安装核心依赖...${RESET}"
$PIP_CMD install $CORE -q 2>/dev/null
if [ $? -eq 0 ]; then
    ok "核心依赖已安装"
else
    echo -e "  ${RED}✗ 核心依赖安装失败！请检查网络后重试。${RESET}"
    exit 1
fi

echo -e "  ${CYAN}安装 Scrapling（网页抓取引擎）...${RESET}"
$PIP_CMD install "$SCRAPLING" -q 2>/dev/null && \
    ok "Scrapling" || warn "Scrapling 安装失败，网页浏览将降级到 requests"

# ════════════════════════════════════════════════
# 5. 初始化
# ════════════════════════════════════════════════
step "5/5" "初始化配置..."

# 密钥目录
KEY_DIR="${HOME}/.config/mh-agent/keys"
mkdir -p "$KEY_DIR"
ok "密钥目录: $KEY_DIR"

# 环境变量提示
ENV_FILE="$HOME/.bashrc"
if [ -f "$HOME/.zshrc" ]; then ENV_FILE="$HOME/.zshrc"; fi

if ! grep -q "DEEPSEEK_API_KEY" "$ENV_FILE" 2>/dev/null; then
    echo "" >> "$ENV_FILE"
    echo "# MH-DeepSeek Agent API Keys（可选，也可首次启动时输入）" >> "$ENV_FILE"
    echo "# export DEEPSEEK_API_KEY=\"sk-your-key-here\"" >> "$ENV_FILE"
    echo "# export BOCHA_SEARCH_API_KEY=\"sk-your-key-here\"" >> "$ENV_FILE"
    ok "环境变量模板已写入 $ENV_FILE"
else
    ok "环境变量已存在"
fi

# ════════════════════════════════════════════════
# 验证
# ════════════════════════════════════════════════
echo ""
header "============================================"
echo -e "  ${BOLD}验证安装...${RESET}"
header "============================================"

$PY_CMD -c "import flask, flask_cors, requests, Crypto, bs4, psutil, dns, cryptography, PIL; print('  OK')" 2>/dev/null && \
    ok "全部核心模块加载正常" || warn "部分模块加载异常"

$PY_CMD -c "import scrapling; print('  OK')" 2>/dev/null && \
    ok "Scrapling 可用" || info "Scrapling 未安装（网页浏览将降级）"

# ── 创建启动脚本 ──────────────────────────
rm -f start.bat
cat > start.sh << 'SCRIPT_EOF'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
echo "============================================"
echo "  MH-DeepSeek Agent V4"
echo "  Visit: http://localhost:9090"
echo "============================================"
python3 main.py 2>/dev/null || python main.py
SCRIPT_EOF
chmod +x start.sh
ok "start.sh 已创建"

echo ""
header "============================================"
echo -e "  ${GREEN}${BOLD}✅ 安装完成！${RESET}"
header "============================================"
echo ""
echo -e "  启动: ${CYAN}bash start.sh${RESET}"
echo -e "  或:   ${CYAN}$PY_CMD main.py${RESET}"
echo -e "  访问: ${CYAN}http://localhost:9090${RESET}"
echo ""
echo -e "  📖 ${BOLD}首次启动会提示输入 API Key${RESET}"
echo -e "     前往 https://platform.deepseek.com 获取"
echo ""
