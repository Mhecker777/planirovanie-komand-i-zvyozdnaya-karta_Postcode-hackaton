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

REM ---------- 2. Виртуальное окружение ----------
set VENV_DIR=.venv
set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe

set VENV_OK=0
if exist "!VENV_PYTHON!" (
    set VENV_PY_NUM=
    for /f "delims=" %%v in ('"!VENV_PYTHON!" -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])" 2^>nul') do set VENV_PY_NUM=%%v
    if "!VENV_PY_NUM!"=="!PY_NUM!" set VENV_OK=1
)

if "!VENV_OK!"=="0" (
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
    echo [INFO] Создаю окружение .venv...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать окружение.
        pause
        exit /b 1
    )
    echo [OK] Окружение создано.
) else (
    echo [OK] Окружение уже есть.
)

REM ---------- 3. Установка зависимостей ----------
set REQ_FILE=requirements.txt
if not exist "%REQ_FILE%" (
    echo [ОШИБКА] Не найден requirements.txt.
    pause
    exit /b 1
)

REM Проверяем, стоит ли streamlit в venv. Если нет — ставим всё.
"!VENV_PYTHON!" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
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
    echo.
    echo [OK] Библиотеки установлены.
) else (
    echo [OK] Библиотеки уже установлены.
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