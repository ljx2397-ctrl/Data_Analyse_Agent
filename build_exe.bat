@echo off
setlocal
title Build DataAnalysisAgent exe (PyInstaller)

rem ============================================================
rem  PyInstaller one-dir build for Data Analysis Agent.
rem  Output: release\DataAnalysisAgent\DataAnalysisAgent.exe
rem  --onedir avoids onefile extraction delay on every launch
rem  (onefile unpacks 195MB to temp on each run = 10-20s).
rem  onedir starts in ~2-3s. Distribute as a zip.
rem  Keeps existing dev startup (start-dev.ps1).
rem  Pure ASCII to avoid cmd.exe GBK parsing issues.
rem ============================================================

cd /d "%~dp0"

echo.
echo  ---------------------------------------------
echo   Building DataAnalysisAgent (onedir mode) ...
echo   Output: release\DataAnalysisAgent\DataAnalysisAgent.exe
echo   onedir = fast startup, no extraction delay
echo  ---------------------------------------------
echo.

rem 0. Rebuild frontend to ensure dist is up-to-date
echo [INFO] Building frontend (npm run build)...
cd frontend
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    pause
    exit /b 1
)
cd ..
if not exist "frontend\dist\index.html" (
    echo [ERROR] frontend\dist\index.html not found after build.
    pause
    exit /b 1
)

rem 1. Use a project-local venv that inherits system packages
rem    via --system-site-packages. This avoids sandbox permission issues when
rem    installing PyInstaller into the global Python site-packages.
set "PY=.venv_build\Scripts\python.exe"
if not exist "%PY%" (
    echo [INFO] Creating build venv .venv_build with --system-site-packages...
    python -m venv --system-site-packages .venv_build
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        pause
        exit /b 1
    )
)

rem 2. Install PyInstaller into the venv if missing
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller into .venv_build...
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed.
        pause
        exit /b 1
    )
)

rem 3. Clean previous build artifacts
if exist release\ rd /s /q release
if exist build\ rd /s /q build

rem 4. Run PyInstaller (via venv python)
echo [INFO] Running PyInstaller...
rem Prevent .pyc writes into system Python dirs (sandbox may block them)
set "PYTHONDONTWRITEBYTECODE=1"
"%PY%" -m PyInstaller --onedir --name DataAnalysisAgent ^
  --add-data "%~dp0frontend\dist;frontend\dist" ^
  --collect-all langgraph ^
  --collect-all langchain ^
  --collect-all langchain_openai ^
  --collect-all langchain_core ^
  --collect-all matplotlib ^
  --collect-all openai ^
  --collect-all sklearn ^
  --exclude-module torch ^
  --exclude-module torchvision ^
  --exclude-module torchaudio ^
  --exclude-module tensorflow ^
  --exclude-module transformers ^
  --hidden-import main ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.protocols ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  --distpath release ^
  --workpath build\work ^
  --specpath build ^
  --noconfirm ^
  backend\app_entry.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo  ---------------------------------------------
echo   [OK] Build succeeded!
echo   Folder: release\DataAnalysisAgent\
echo   Run:    release\DataAnalysisAgent\DataAnalysisAgent.exe
echo.
echo   Distribute: zip the DataAnalysisAgent folder
echo   and send to others. Unzip + double-click
echo   the exe. No Python/Node needed. storage\
echo   is created next to the exe on first run.
echo   Configure LLM API Key in the web UI.
echo  ---------------------------------------------
echo.
pause
endlocal
