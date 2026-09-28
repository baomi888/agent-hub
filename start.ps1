# 集合式 Agent 一键启动脚本（PowerShell）
# 由 start.bat 调用，避免执行策略限制

$ErrorActionPreference = "Continue"

# ============ 路径与端口配置 ============
$ROOT = $PSScriptRoot
$FRONTEND = Join-Path $ROOT "frontend"
$BACKEND_PORT = 8000
$FRONTEND_PORT = 3000
$BACKEND_URL = "http://localhost:$BACKEND_PORT"
$FRONTEND_URL = "http://localhost:$FRONTEND_PORT"

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[X] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "   集合式 Agent  一键启动" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host ""

# ============ 1. 检查 Python ============
$pyCheck = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "未检测到 Python，请先安装 Python 3.10+"
    Read-Host "按回车键退出"
    exit 1
}
Write-Ok "Python: $pyCheck"

# ============ 2. 检查后端依赖，缺失则自动安装 ============
python -c "import uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step "后端依赖未安装，正在执行 pip install -r requirements.txt ..."
    Push-Location $ROOT
    python -m pip install -r requirements.txt
    $pipOk = $LASTEXITCODE
    Pop-Location
    if ($pipOk -ne 0) {
        Write-Err "依赖安装失败，请手动执行 pip install -r requirements.txt"
        Read-Host "按回车键退出"
        exit 1
    }
} else {
    Write-Ok "后端依赖已就绪"
}

# ============ 3. 检查前端依赖 ============
if (-not (Test-Path (Join-Path $FRONTEND "node_modules"))) {
    Write-Step "前端依赖未安装，正在执行 npm install ..."
    Push-Location $FRONTEND
    npm install
    $npmOk = $LASTEXITCODE
    Pop-Location
    if ($npmOk -ne 0) {
        Write-Err "前端依赖安装失败，请手动执行 npm install"
        Read-Host "按回车键退出"
        exit 1
    }
} else {
    Write-Ok "前端依赖已就绪"
}

# ============ 4. 检查端口占用 ============
$backendInUse = Get-NetTCPConnection -LocalPort $BACKEND_PORT -State Listen -ErrorAction SilentlyContinue
if ($backendInUse) { Write-Warn "端口 $BACKEND_PORT 已被占用，后端可能已在运行" }
$frontendInUse = Get-NetTCPConnection -LocalPort $FRONTEND_PORT -State Listen -ErrorAction SilentlyContinue
if ($frontendInUse) { Write-Warn "端口 $FRONTEND_PORT 已被占用，前端可能已在运行" }

# ============ 5. 启动后端（独立窗口） ============
Write-Host ""
Write-Step "启动后端服务  uvicorn main:app --port $BACKEND_PORT"
# chcp 65001 + PYTHONUTF8=1：强制新窗口用 UTF-8 代码页，避免 Python 中文日志在终端显示乱码
# 注意：set 必须用引号包住 "VAR=1"，否则 && 前的空格会存进变量值（PYTHONUTF8 变 "1 " 会导致 Python 启动失败）
# PYTHONDONTWRITEBYTECODE=1 避免在受限环境下写 .pyc 导致启动失败
$backendCmd = "chcp 65001 >nul && cd /d `"$ROOT`" && set `"PYTHONDONTWRITEBYTECODE=1`" && set `"PYTHONUTF8=1`" && set `"PYTHONIOENCODING=utf-8`" && python -m uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT --reload"
Start-Process cmd -ArgumentList "/k", $backendCmd -WindowStyle Normal

# ============ 6. 启动前端（独立窗口） ============
Write-Step "启动前端服务  next dev --port $FRONTEND_PORT"
$frontendCmd = "chcp 65001 >nul && cd /d `"$FRONTEND`" && npx next dev -p $FRONTEND_PORT"
Start-Process cmd -ArgumentList "/k", $frontendCmd -WindowStyle Normal

# ============ 7. 等待就绪并打开浏览器 ============
Write-Host ""
Write-Step "等待服务启动（约 8 秒）..."
Start-Sleep -Seconds 8

Write-Host ""
Write-Ok "后端: $BACKEND_URL/health"
Write-Ok "前端: $FRONTEND_URL"
Write-Host ""
Write-Step "正在打开浏览器 ..."
Start-Process $FRONTEND_URL

Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "  启动完成！两个服务窗口请勿关闭" -ForegroundColor Green
Write-Host "  关闭本窗口不影响服务运行" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Magenta
Write-Host ""
Read-Host "按回车键关闭此窗口（服务继续运行）"
