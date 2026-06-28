@echo off
REM vanning-eval WebUI launcher (local only)
REM - Uses system Python 3.11+ (py launcher preferred, fallback to python)
REM - Fixed port 8502 (to avoid 8501 conflicts)
REM - Auto-installs dependencies on first run
chcp 65001 >nul

cd /d "%~dp0"

REM Prefer Python 3.11 (pyproject 指定 = pyright と揃える)、無ければ py -3 → python にフォールバック
set PY=
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 --version >nul 2>&1
    if not errorlevel 1 set PY=py -3.11
)
if not defined PY (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 --version >nul 2>&1
        if not errorlevel 1 set PY=py -3
    )
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set PY=python
)
if not defined PY (
    echo [ERROR] Python 3.11 not found.
    echo Install Python 3.11 from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/2] Checking dependencies...
%PY% -m pip install -e ".[viewer]" --quiet --disable-pip-version-check --no-warn-script-location
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo [2/2] Starting WebUI at http://localhost:8502
%PY% main.py --port 8502
pause
