# ============================================
# MH-DeepSeek Agent V4 — Windows 一键安装 (PowerShell)
# 用法: 右键 → 使用 PowerShell 运行，或 ./install.ps1
# 如果遇到执行策略问题:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# ============================================

$ErrorActionPreference = "Continue"

# ── 颜色函数 ──────────────────────────────────
function Write-Step { param([string]$N, [string]$M) Write-Host "`n[$N] $M" -ForegroundColor Yellow }
function Write-OK   { param([string]$M) Write-Host "  [OK] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) Write-Host "  [WARN] $M" -ForegroundColor Yellow }
function Write-Info { param([string]$M) Write-Host "  [INFO] $M" -ForegroundColor Gray }
function Write-Err  { param([string]$M) Write-Host "  [FAIL] $M" -ForegroundColor Red }

Clear-Host
Write-Host ""
Write-Host "============================================" -ForegroundColor Blue
Write-Host "  MH-DeepSeek Agent V4 — Windows 安装" -ForegroundColor Blue
Write-Host "============================================" -ForegroundColor Blue
Write-Host ""

# ════════════════════════════════════════════════
# 1. 检查 Python
# ════════════════════════════════════════════════
Write-Step "1/5" "检查 Python 环境..."

$pythonFound = $false
$pyCmd = $null

# 尝试查找 Python
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = (& $cmd --version 2>&1).ToString()
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and ($major -gt 3 -or $minor -ge 10)) {
                $pyCmd = $cmd
                $pythonFound = $true
                Write-OK "$ver ($pyCmd)"
                break
            } elseif ($major -ge 3) {
                Write-Warn "$ver 版本过低，需要 Python 3.10+"
            }
        }
    } catch {}
}

if (-not $pythonFound) {
    Write-Err "Python 3.10+ 未安装或不在 PATH 中"
    Write-Host ""
    Write-Host "  正在尝试通过 winget 自动安装 Python..." -ForegroundColor Cyan
    
    $wingetFound = $false
    try { $null = Get-Command winget -ErrorAction Stop; $wingetFound = $true } catch {}
    
    if ($wingetFound) {
        Write-Host "  执行: winget install Python.Python.3.12 --silent" -ForegroundColor Gray
        winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        Write-Host ""
        Write-Warn "Python 安装完成。请重新打开终端后再运行此脚本。"
        Write-Host "  如果 Python 仍不可用，请手动安装: https://www.python.org/downloads/" -ForegroundColor Yellow
    } else {
        Write-Host "  winget 不可用，请手动安装 Python:" -ForegroundColor Yellow
        Write-Host "  https://www.python.org/downloads/" -ForegroundColor Cyan
        Write-Host "  安装时务必勾选 'Add Python to PATH'" -ForegroundColor Yellow
    }
    Write-Host ""
    Read-Host "按回车退出"
    exit 1
}

# 定位 pip
$pipCmd = "$pyCmd -m pip"

# ════════════════════════════════════════════════
# 2. 升级 pip
# ════════════════════════════════════════════════
Write-Step "2/5" "升级 pip..."
try {
    & $pyCmd -m pip install --upgrade pip setuptools wheel -q 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-OK "pip 已升级" }
    else { Write-Warn "pip 升级跳过，继续使用当前版本" }
} catch { Write-Warn "pip 升级跳过" }

# ════════════════════════════════════════════════
# 3. 核心依赖
# ════════════════════════════════════════════════
Write-Step "3/5" "安装核心依赖..."

$corePkgs = @(
    "flask", "flask-cors", "requests",
    "pycryptodome", "cryptography",
    "beautifulsoup4", "psutil",
    "dnspython", "Pillow", "colorama"
)

try {
    & $pyCmd -m pip install $corePkgs -q 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "核心依赖已安装"
    } else {
        Write-Err "核心依赖安装失败！请检查网络后重试。"
        Read-Host "按回车退出"
        exit 1
    }
} catch {
    Write-Err "安装过程出错: $_"
    Read-Host "按回车退出"
    exit 1
}

# ════════════════════════════════════════════════
# 4. Scrapling 网页抓取引擎
# ════════════════════════════════════════════════
Write-Step "4/5" "安装 Scrapling（网页抓取引擎）..."

try {
    & $pyCmd -m pip install "scrapling>=0.2.0" -q 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Scrapling 已安装"
    } else {
        Write-Warn "Scrapling 安装失败，网页浏览将降级到 requests"
    }
} catch {
    Write-Warn "Scrapling 安装失败，网页浏览将降级到 requests"
}

# ════════════════════════════════════════════════
# 5. 初始化配置
# ════════════════════════════════════════════════
Write-Step "5/5" "初始化配置..."

# 密钥目录
$keyDir = "$env:USERPROFILE\.config\mh-agent\keys"
if (-not (Test-Path $keyDir)) {
    New-Item -ItemType Directory -Path $keyDir -Force | Out-Null
    Write-OK "密钥目录已创建: $keyDir"
} else {
    Write-OK "密钥目录已存在: $keyDir"
}

# 环境变量提示
$envVars = @"
:: MH-DeepSeek Agent API Keys
:: 将下面两行的 :: 去掉并填入你的 Key，然后把这个文件放到启动脚本同目录
:: set DEEPSEEK_API_KEY=sk-your-key-here
:: set BOCHA_SEARCH_API_KEY=sk-your-key-here
"@

$envFile = "set_env.bat"
if (-not (Test-Path $envFile)) {
    Set-Content -Path $envFile -Value $envVars -Encoding ASCII
    Write-OK "环境变量模板已创建: $envFile"
    Write-Info "如需预设 API Key，编辑此文件后运行: set_env.bat"
} else {
    Write-OK "环境变量模板已存在"
}

# ── 验证 ────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Blue
Write-Host "  验证安装..." -ForegroundColor Blue
Write-Host "============================================" -ForegroundColor Blue

$modules = @("flask", "requests", "Crypto", "bs4", "psutil", "dns", "cryptography", "PIL")
$allOk = $true
foreach ($mod in $modules) {
    try {
        $null = & $pyCmd -c "import $mod"
        Write-OK $mod
    } catch {
        Write-Warn "$mod 加载失败"
        $allOk = $false
    }
}

try {
    $null = & $pyCmd -c "import scrapling"
    Write-OK "scrapling"
} catch {
    Write-Info "scrapling (未安装 — 网页浏览将降级)"
}

# ── 启动脚本 ────────────────────────────────────
Write-Host ""
Write-Host "创建启动脚本 start.bat ..." -ForegroundColor Yellow
@"
@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 加载环境变量（如果有）
if exist set_env.bat call set_env.bat

echo ============================================
echo   MH-DeepSeek Agent V4
echo   Visit: http://localhost:9090
echo ============================================
python main.py
pause
"@ | Out-File -FilePath "start.bat" -Encoding ASCII -Force
Write-OK "start.bat 已创建"

# ── 完成 ────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  ✅ 安装完成！" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  启动方式:" -ForegroundColor Cyan
Write-Host "    双击 start.bat" -ForegroundColor White
Write-Host "    或在终端执行: python main.py" -ForegroundColor White
Write-Host ""
Write-Host "  访问地址: http://localhost:9090" -ForegroundColor Cyan
Write-Host ""
Write-Host "  📖 首次启动会提示输入 API Key" -ForegroundColor Yellow
Write-Host "    前往 https://platform.deepseek.com 获取" -ForegroundColor Yellow
Write-Host ""

Read-Host "按回车退出"
