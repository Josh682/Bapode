@echo off
REM Bapode v3.0 - Application Launcher (Windows)
REM Windows counterpart of run.sh (run.sh remains the macOS/Linux launcher).

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
    set "PYTHON_EXEC=python"
)

set "PYTHONPATH=%SCRIPT_DIR%src;%PYTHONPATH%"
"%PYTHON_EXEC%" "%SCRIPT_DIR%src\main.py" %*
exit /b %ERRORLEVEL%
