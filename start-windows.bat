@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Simple KTV - Windows Launcher
pushd "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot open the application directory.
    pause
    exit /b 1
)
if not exist "%~dp0start.ps1" (
    echo [ERROR] start.ps1 is missing. Extract the complete project ZIP first.
    popd
    pause
    exit /b 1
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found.
    popd
    pause
    exit /b 1
)
echo Starting Simple KTV. Docker Desktop must be installed and running.
echo The first start may download and build the container image.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "KTV_EXIT=%ERRORLEVEL%"
popd
if not "%KTV_EXIT%"=="0" (
    echo.
    echo [ERROR] Startup failed with exit code %KTV_EXIT%.
    echo Check Docker Desktop, WSL2 and your NVIDIA driver.
    pause
)
exit /b %KTV_EXIT%
