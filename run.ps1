# Bapode v3.0 — PowerShell Application Launcher
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

$PythonExec = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExec)) {
    Write-Host "[!] Virtual environment tidak ditemukan. Menjalankan setup_windows.bat..." -ForegroundColor Yellow
    cmd.exe /c setup_windows.bat
}

$env:PYTHONPATH = "$ScriptDir\src;" + $env:PYTHONPATH
& $PythonExec "$ScriptDir\src\main.py" @args
