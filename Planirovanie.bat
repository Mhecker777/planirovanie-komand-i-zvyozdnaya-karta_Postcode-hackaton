@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title ПочтаТех(Без Даты) - Квартальное планирование

cd /d "%~dp0"

echo.
echo ============================================================
echo   ПочтаТех(Без Даты) - Квартальное планирование (PI)
echo ============================================================
echo.

REM ---------- 1. Проверка Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден в системе.
    echo Установите Python 3.12 или 3.13 с https://www.python.org/downloads/
    echo ВАЖНО: при установке отметьте "Add Python to PATH".
    pause
    exit /b 1
)

set PY_NUM=
for /f "delims=" %%v in ('python -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set PY_NUM=%%v

if not defined PY_NUM (
    echo [ОШИБКА] Не удалось определить версию Python.
    pause
    exit /b 1
)

if !PY_NUM! LSS 310 (
    echo [ОШИБКА] Нужен Python 3.10 или выше.
    pause
    exit /b 1
)

echo [OK] Python найден.

REM ---------- 2. Виртуальное окружение (создаётся ОДИН РАЗ) ----------
set VENV_DIR=.venv
set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe

if not exist "!VENV_PYTHON!" (
    echo [INFO] Первый запуск: создаю окружение .venv...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать окружение.
        pause
        exit /b 1
    )
    echo [OK] Окружение создано.
) else (
    echo [OK] Окружение .venv уже существует.
)

REM ---------- 3. Проверка и установка зависимостей ----------
set REQ_FILE=requirements.txt
if not exist "%REQ_FILE%" (
    echo [ОШИБКА] Не найден requirements.txt.
    pause
    exit /b 1
)

set NEEDS_INSTALL=0

REM 3.1. Проверяем, что ВСЕ нужные модули импортируются.
REM      (а не только streamlit, как было раньше — из-за этого
REM      обновление pandas или потеря networkx не замечались).
"!VENV_PYTHON!" -c "import streamlit, pandas, numpy, plotly, networkx, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Не все библиотеки установлены или повреждены.
    set NEEDS_INSTALL=1
)

REM 3.2. Сверяем хеш requirements.txt — если файл менялся,
REM      зависимости нужно обновить даже при живых импортах.
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
    echo [INFO] requirements.txt изменился — обновляю зависимости.
    set NEEDS_INSTALL=1
)

REM 3.3. Устанавливаем ТОЛЬКО если что-то не сошлось.
if "!NEEDS_INSTALL!"=="1" (
    echo.
    echo ============================================================
    echo   Устанавливаю библиотеки. Это займёт 1-3 минуты.
    echo   Пожалуйста, подождите и не закрывайте окно.
    echo ============================================================
    echo.
    "!VENV_PYTHON!" -m pip install --disable-pip-version-check -r "%REQ_FILE%"
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] Не удалось установить зависимости.
        echo Проверьте интернет-соединение.
        pause
        exit /b 1
    )
    echo !CURR_HASH!> "%REQ_HASH_FILE%"
    echo.
    echo [OK] Библиотеки установлены.
) else (
    echo [OK] Библиотеки актуальны — установка не требуется.
)

REM ---------- 4. Проверка dataset.xlsx ----------
set DATASET_FOUND=0
if exist "data\dataset.xlsx" set DATASET_FOUND=1
if exist "dataset.xlsx" set DATASET_FOUND=1
if exist "src\data\dataset.xlsx" set DATASET_FOUND=1

if "!DATASET_FOUND!"=="0" (
    echo.
    echo [ВНИМАНИЕ] Не найден dataset.xlsx.
    echo Положите его в папку data\ или в корень проекта.
    echo.
    pause
)

REM ---------- 5. Поиск app.py ----------
set APP_PATH=src\app.py
if not exist "!APP_PATH!" set APP_PATH=app.py
if not exist "!APP_PATH!" (
    echo [ОШИБКА] Не найден app.py.
    pause
    exit /b 1
)

REM ---------- 6. Запуск ----------
echo.
echo ============================================================
echo   Запускаю приложение...
echo   Браузер откроется автоматически.
echo   Чтобы остановить - Ctrl+C в этом окне.
echo ============================================================
echo.

"!VENV_PYTHON!" -m streamlit run "!APP_PATH!" --server.headless=false

echo.
echo Приложение остановлено.
pause
endlocal