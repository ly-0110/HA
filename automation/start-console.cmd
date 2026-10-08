@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "UV_CACHE_DIR=%CD%\.uv-cache"
set "console_python="
set "console_uv="

if defined IOT_EXP_PYTHON call :try_python "%IOT_EXP_PYTHON%"
if defined console_python goto launch
call :try_python "%CD%\.venv\Scripts\python.exe"
if defined console_python goto launch
call :try_python "%CD%\.tools\python\python.exe"
if defined console_python goto launch
where py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>nul
    if not errorlevel 1 (
      py -%%V console_bootstrap.py %*
      goto finish
    )
  )
)
for %%V in (313 312 311 310) do (
  call :try_python "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if defined console_python goto launch
  call :try_python "%ProgramFiles%\Python%%V\python.exe"
  if defined console_python goto launch
  call :try_python "%SystemDrive%\Python%%V\python.exe"
  if defined console_python goto launch
)
call :try_python python
if defined console_python goto launch
call :try_python python3
if defined console_python goto launch

if defined IOT_EXP_UV if exist "%IOT_EXP_UV%" set "console_uv=%IOT_EXP_UV%"
if not defined console_uv if exist "%CD%\.tools\uv\uv.exe" set "console_uv=%CD%\.tools\uv\uv.exe"
if not defined console_uv if exist "%CD%\.tools\uv.exe" set "console_uv=%CD%\.tools\uv.exe"
if not defined console_uv if exist "%USERPROFILE%\.local\bin\uv.exe" set "console_uv=%USERPROFILE%\.local\bin\uv.exe"
if not defined console_uv if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "console_uv=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined console_uv for %%V in (313 312 311 310) do if not defined console_uv if exist "%APPDATA%\Python\Python%%V\Scripts\uv.exe" set "console_uv=%APPDATA%\Python\Python%%V\Scripts\uv.exe"
if not defined console_uv for /f "delims=" %%U in ('where uv 2^>nul') do if not defined console_uv set "console_uv=%%U"
if defined console_uv (
  "%console_uv%" run --no-project --python ">=3.10,<3.14" python console_bootstrap.py %*
  goto finish
)
echo 未找到 Python 3.10–3.13。请安装 Python 后再次双击本文件，项目依赖会自动安装。
echo 已安装 Python 时，可设置 IOT_EXP_PYTHON 为 python.exe 的完整路径。
echo 下载地址：https://www.python.org/downloads/
pause
exit /b 1

:launch
"%console_python%" console_bootstrap.py %*
:finish
set "console_exit=%ERRORLEVEL%"
if not "%console_exit%"=="0" pause
exit /b %console_exit%

:try_python
"%~1" -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>nul
if not errorlevel 1 set "console_python=%~1"
exit /b 0
