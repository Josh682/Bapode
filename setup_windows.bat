@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo  Bapode v3.0 - Setup Lingkungan Windows
echo ===================================================

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python tidak ditemukan di PATH sistem.
    echo Silakan install Python 3.11 atau lebih baru dari python.org.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [*] Membuat virtual environment (.venv)...
    python -m venv .venv
)

echo [*] Menginstall dependensi Windows...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-windows.txt

echo [*] Mengunduh model MediaPipe...
python scripts\download_models.py

echo.
echo ===================================================
echo  Setup Selesai! Jalankan aplikasi dengan run.bat
echo ===================================================
pause
