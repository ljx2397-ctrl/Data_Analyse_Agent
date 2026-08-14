@echo off
setlocal
title Data Analysis Agent - One-click Start

rem ============================================================
rem  One-click start for Data Analysis Agent (Vue + FastAPI)
rem  Works on any Windows PC: start-dev.ps1 auto-detects
rem  Python/Node.js, installs missing dependencies, generates
rem  .env, then starts backend (8000) + frontend (5173).
rem  LLM API Key is optional - configure it in the web UI
rem  (sidebar: LLM Config) after startup.
rem ============================================================

rem Make Chinese output from PowerShell display correctly
chcp 65001 >nul

rem Always run from the folder containing this file
cd /d "%~dp0"

if not exist "%~dp0start-dev.ps1" (
    echo.
    echo [ERROR] start-dev.ps1 was not found next to this file.
    echo Please run this file from the project root folder.
    echo.
    pause
    exit /b 1
)

echo.
echo  =============================================
echo   Data Analysis Agent - starting services...
echo   Frontend: http://127.0.0.1:5173
echo   Backend:  http://127.0.0.1:8000
echo   Browser will open automatically when ready.
echo   LLM API Key is optional - configure it in the web UI.
echo   Press Ctrl+C in this window to stop both.
echo  =============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1"

echo.
echo  =============================================
echo   Finished. You can close this window now.
echo  =============================================
pause
endlocal
