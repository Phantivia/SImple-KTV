@echo off
setlocal DisableDelayedExpansion
title SIMPLE / KTV - upload
set "KTV_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%KTV_POWERSHELL%" (
    echo [ERROR] Windows PowerShell 5.1 was not found.
    if not "%KTV_NO_PAUSE%"=="1" pause
    exit /b 1
)
"%KTV_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\upload-existing.ps1" %*
set "KTV_EXIT=%ERRORLEVEL%"
echo.
if not "%KTV_EXIT%"=="0" echo [ERROR] See the message above and the logs folder.
if not "%KTV_NO_PAUSE%"=="1" pause
exit /b %KTV_EXIT%
