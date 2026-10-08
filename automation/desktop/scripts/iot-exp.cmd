@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
"%~dp0runtime\windows-x64\python\python.exe" -I -B -X utf8 -m iot_exp.cli --resources "%~dp0." %*
exit /b %ERRORLEVEL%
