@echo off
REM Bapode v3.0 - Application Launcher (Windows)
REM Windows counterpart of run.sh

setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Detect Python interpreter
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    set "PYTHON_EXEC=%SCRIPT_DIR%.venv\Scripts\python.exe"
) else if exist "%SCRIPT_DIR%.venv312\Scripts\python.exe" (
    set "PYTHON_EXEC=%SCRIPT_DIR%.venv312\Scripts\python.exe"
) else if exist "%SCRIPT_DIR%venv\Scripts\python.exe" (
    set "PYTHON_EXEC=%SCRIPT_DIR%venv\Scripts\python.exe"
) else (
    if exist "%SCRIPT_DIR%setup_windows.bat" (
        echo [!] Virtual environment belum disiapkan. Menjalankan setup...
        call "%SCRIPT_DIR%setup_windows.bat"
        if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
            set "PYTHON_EXEC=%SCRIPT_DIR%.venv\Scripts\python.exe"
        ) else (
            set "PYTHON_EXEC=python"
        )
    ) else (
        set "PYTHON_EXEC=python"
    )
)

set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
set "CMD_ARGS=%*"
if "%~1"=="" set "CMD_ARGS=--show-dashboard"
"%PYTHON_EXEC%" "%SCRIPT_DIR%src\main.py" %CMD_ARGS%
exit /b %ERRORLEVEL%
