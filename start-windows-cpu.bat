@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0start-windows.bat" -CPU %*
exit /b %ERRORLEVEL%
