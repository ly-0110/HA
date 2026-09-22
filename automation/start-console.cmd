@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul || (echo [ERROR] uv is not installed or not on PATH.& pause & exit /b 1)
if not exist "web\dist\index.html" (echo [ERROR] Web interface is not built. Run npm install and npm run web:build once.& pause & exit /b 1)
uv run iot-exp-gui
if errorlevel 1 pause
