# start-dev.ps1
# 一键启动开发环境：后端 FastAPI (8000) + 前端 Vite (5173)
# 跨电脑兼容：自动检测 python/node/npm，自动安装前后端依赖，自动生成 .env
# 单终端双日志：前后端输出实时显示在当前窗口，Ctrl+C 统一退出
#
# 用法（在项目根目录执行）：
#   powershell -ExecutionPolicy Bypass -File start-dev.ps1
#   或简写：./start-dev.ps1

$ErrorActionPreference = "Stop"
$root     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$backend  = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "    [X] $m" -ForegroundColor Red }

# ── 1. 检测 python / node / npm（不硬编码路径，靠系统 PATH）──────────────
Write-Step "检测运行环境..."
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 python，请安装 Python 3.11+ 并加入系统 PATH"
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 node，请安装 Node.js 18+ 并加入系统 PATH"
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Err "未找到 npm，请安装 Node.js 18+ 并加入系统 PATH"
    exit 1
}
Write-Ok "python / node / npm 均可用"

# ── 2. 检查 / 生成 .env（API Key 可选，启动后可在网页配置）──────────────
Write-Step "检查 .env 配置..."
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile -Force
    Write-Ok "已从 .env.example 创建 .env"
}

# 读取 .env 中的 DASHSCOPE_API_KEY（可选）
$apiKey = $null
Get-Content $envFile -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*DASHSCOPE_API_KEY\s*=\s*(.*)$') {
        $apiKey = $matches[1].Trim().Trim('"').Trim("'")
    }
}
# 环境变量优先
if ($env:DASHSCOPE_API_KEY) { $apiKey = $env:DASHSCOPE_API_KEY }

# 注入到当前进程环境（若有），后端子进程可继承读到
if ($apiKey -and $apiKey -notin @("PUT_YOUR_KEY_HERE", "你的_api_key", "DASHSCOPE_API_KEY", "your_api_key_here")) {
    $env:DASHSCOPE_API_KEY = $apiKey
    Write-Ok "DASHSCOPE_API_KEY 已就绪"
} else {
    Write-Warn "未配置 DASHSCOPE_API_KEY，服务仍会正常启动"
    Write-Host "    可在前端页面右上角『🤖 大模型配置』中填写 API Key，" -ForegroundColor Yellow
    Write-Host "    或编辑 $envFile 填入后重启，" -ForegroundColor Yellow
    Write-Host "    获取地址：https://bailian.console.aliyun.com/" -ForegroundColor Yellow
}

# ── 3. 检查后端 Python 依赖 ────────────────────────────────────────────
Write-Step "检查后端依赖..."
$probe = "import fastapi,uvicorn,langgraph,langchain_openai,pandas,matplotlib,xlsxwriter,openpyxl,sklearn,redis,fakeredis"
& python -c $probe 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "缺失后端依赖，开始安装..."
    & python -m pip install -r (Join-Path $backend "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Write-Err "后端依赖安装失败"; exit 1 }
    Write-Ok "后端依赖安装完成"
} else {
    Write-Ok "后端依赖已就绪"
}

# ── 4. 检查前端依赖 ────────────────────────────────────────────────────
Write-Step "检查前端依赖..."
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Warn "缺失前端依赖，开始安装（首次较慢）..."
    Push-Location $frontend
    try {
        & npm install
        if ($LASTEXITCODE -ne 0) { Write-Err "前端依赖安装失败"; exit 1 }
    } finally { Pop-Location }
    Write-Ok "前端依赖安装完成"
} else {
    Write-Ok "前端依赖已就绪"
}

# ── 5. 启动前后端（单终端双日志）──────────────────────────────────────
Write-Step "启动服务..."
Write-Host ""
Write-Host "  后端 API:    http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  API 文档:    http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "  前端界面:    http://127.0.0.1:5173" -ForegroundColor Green
Write-Host ""
Write-Host "  按 Ctrl+C 退出前后端（会自动清理子进程）" -ForegroundColor Yellow
Write-Host "  ────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# -NoNewWindow：子进程继承当前控制台，输出实时显示在本窗口
$be = Start-Process -FilePath "python" `
    -ArgumentList "run.py" `
    -WorkingDirectory $backend `
    -NoNewWindow -PassThru

$fe = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c","npm","run","dev" `
    -WorkingDirectory $frontend `
    -NoNewWindow -PassThru

# ── 6. 自动打开浏览器（等前端就绪，最多 30 秒）─────────────────────
# 不想自动打开时，先执行：$env:AUTO_OPEN_BROWSER = "0"
$frontendUrl = "http://127.0.0.1:5173"
if ($env:AUTO_OPEN_BROWSER -ne "0") {
    Write-Step "等待前端就绪，将自动打开浏览器..."
    $deadline = (Get-Date).AddSeconds(30)
    $ready = $false
    while ((Get-Date) -lt $deadline -and -not $fe.HasExited) {
        try {
            $resp = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($ready) {
        Start-Process $frontendUrl
        Write-Ok "已在默认浏览器打开 $frontendUrl"
    } else {
        Write-Warn "前端未在 30 秒内就绪，请手动打开 $frontendUrl"
    }
} else {
    Write-Ok "已跳过自动打开浏览器（AUTO_OPEN_BROWSER=0）"
}

# 退出时清理整棵进程树（uvicorn --reload、vite 都会派生子进程）
function Stop-Tree($proc) {
    if (-not $proc -or $proc.HasExited) { return }
    # taskkill /T 杀进程树，/F 强制
    & taskkill /T /F /PID $proc.Id 2>$null | Out-Null
}

try {
    # 任一进程退出则结束（通常意味着某端崩溃）
    while (-not $be.HasExited -and -not $fe.HasExited) {
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host ""
    Write-Host "==> 正在停止服务..." -ForegroundColor Yellow
    Stop-Tree $be
    Stop-Tree $fe
    Write-Ok "已退出"
}
