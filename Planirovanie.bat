@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title PochtaTeh - Quarterly Planning

cd /d "%~dp0"

echo.
echo ============================================================
echo   PochtaTeh - Quarterly Planning (PI)
echo ============================================================
echo.

REM ---------- 1. Check Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/
    echo IMPORTANT: check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

set PY_NUM=
for /f "delims=" %%v in ('python -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set PY_NUM=%%v

if not defined PY_NUM (
    echo [ERROR] Could not detect Python version.
    pause
    exit /b 1
)

if !PY_NUM! LSS 310 (
    echo [ERROR] Python 3.10 or higher required.
    pause
    exit /b 1
)

echo [OK] Python found.

REM ---------- 2. Virtual environment (created ONCE) ----------
set VENV_DIR=.venv
set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe

if not exist "!VENV_PYTHON!" (
    echo [INFO] First launch: creating .venv...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    echo [OK] Environment created.
) else (
    echo [OK] Environment .venv already exists.
)

REM ---------- 3. Check and install dependencies ----------
set REQ_FILE=requirements.txt
if not exist "%REQ_FILE%" (
    echo [ERROR] requirements.txt not found.
    pause
    exit /b 1
)

set NEEDS_INSTALL=0

"!VENV_PYTHON!" -c "import streamlit, pandas, numpy, plotly, networkx, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Some libraries are missing or broken.
    set NEEDS_INSTALL=1
)

set REQ_HASH_FILE=%VENV_DIR%\.req_hash
set CURR_HASH=

for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "%REQ_FILE%" MD5 2^>nul ^| findstr /v ":"') do (
    if not defined CURR_HASH set CURR_HASH=%%h
)
set CURR_HASH=!CURR_HASH: =!

set SAVED_HASH=
if exist "%REQ_HASH_FILE%" (
    set /p SAVED_HASH=<"%REQ_HASH_FILE%"
)

if not "!CURR_HASH!"=="!SAVED_HASH!" (
    echo [INFO] requirements.txt changed - updating dependencies.
    set NEEDS_INSTALL=1
)

if "!NEEDS_INSTALL!"=="1" (
    echo.
    echo ============================================================
    echo   Installing libraries. This will take 1-3 minutes.
    echo   Please wait and do not close this window.
    echo ============================================================
    echo.
    "!VENV_PYTHON!" -m pip install --disable-pip-version-check -r "%REQ_FILE%"
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install dependencies.
        echo Check your internet connection.
        pause
        exit /b 1
    )
    echo !CURR_HASH!> "%REQ_HASH_FILE%"
    echo.
    echo [OK] Libraries installed.
) else (
    echo [OK] Libraries are up to date - no installation needed.
)

REM ---------- 4. Check dataset.xlsx ----------
set DATASET_FOUND=0
if exist "data\dataset.xlsx" set DATASET_FOUND=1
if exist "dataset.xlsx" set DATASET_FOUND=1
if exist "src\data\dataset.xlsx" set DATASET_FOUND=1

if "!DATASET_FOUND!"=="0" (
    echo.
    echo [WARNING] dataset.xlsx not found.
    echo Put it into "data\" folder or into project root.
    echo.
    pause
)

REM ---------- 5. Find app.py ----------
set APP_PATH=src\app.py
if not exist "!APP_PATH!" set APP_PATH=app.py
if not exist "!APP_PATH!" (
    echo [ERROR] app.py not found.
    pause
    exit /b 1
)

REM ---------- 6. Launch ----------
echo.
echo ============================================================
echo   Launching application...
echo   Browser will open automatically.
echo   To stop - Ctrl+C in this window.
echo ============================================================
echo.

"!VENV_PYTHON!" -m streamlit run "!APP_PATH!" --server.headless=false

echo.
echo Application stopped.
pause
endlocal