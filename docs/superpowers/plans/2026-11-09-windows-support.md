# Windows-Dedicated Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Bapode v3.0 into a dedicated, production-ready Windows desktop posture monitoring utility with native Win32 focus handling, OS sleep/wake listeners, DirectShow webcam optimization, and one-click batch/PowerShell launchers.

**Architecture:** Replace macOS-specific PyObjC dependencies and shell scripts with Windows-native ctypes/Win32 APIs and PySide6 QAbstractNativeEventFilter for WM_POWERBROADCAST. Optimize OpenCV camera reader with cv2.CAP_DSHOW for rapid acquisition, normalize temporary paths with tempfile, and provide one-click Windows setup and launch scripts.

**Tech Stack:** Python 3.11+, PySide6 (Qt 6.6+), OpenCV (DirectShow), MediaPipe Tasks, ctypes/pywin32, SQLite WAL.

**Spec:** `docs/superpowers/specs/2026-11-09-windows-support-design.md`

## Global Constraints
- Python version floor: `>=3.11`
- Remove all macOS dependencies (`pyobjc-core`, `pyobjc-framework-Cocoa`)
- Retain 100% test passing rate (all 77+ tests must pass)
- Adhere to Universal Engineering Rules (immutability, KISS, DRY, YAGNI, Conventional Commits)
- Never hardcode Unix `/tmp` paths; use `tempfile.gettempdir()`
- All UI interactions must remain non-blocking via Qt signals across the 4-thread architecture

---

### Task 1: Update Dependencies and Package Definitions

**Files:**
- Modify: `pyproject.toml`
- Create: `requirements-windows.txt`

**Interfaces:**
- Produces: Clean, Windows-specific dependency list with `PySide6`, `opencv-python`, `mediapipe`, `numpy`, and `pywin32`.

- [ ] **Step 1: Write requirements-windows.txt**

Create `requirements-windows.txt` with explicit versions compatible with Windows 10/11:
```text
numpy>=1.26.0
PySide6>=6.6.0
opencv-python>=4.8.0
mediapipe>=0.10.0
pywin32>=306; sys_platform == 'win32'
pytest>=8.0.0
pytest-qt>=4.4.0
```

- [ ] **Step 2: Update pyproject.toml**

Edit `pyproject.toml` to remove macOS `pyobjc` requirements and add Windows-compatible dependencies:
```toml
dependencies = [
    "numpy>=1.26.0",
    "PySide6>=6.6.0",
    "opencv-python>=4.8.0",
    "mediapipe>=0.10.0",
    "pywin32>=306; sys_platform == 'win32'",
]
```

- [ ] **Step 3: Verify environment dependencies**

Run:
```powershell
.venv\Scripts\python -m pip check
```
Expected: No broken requirements found.

- [ ] **Step 4: Commit dependency updates**

```bash
git add pyproject.toml requirements-windows.txt
git commit -m "chore(deps): update dependencies for dedicated Windows support"
```

---

### Task 2: Cross-Platform Path Normalization

**Files:**
- Modify: `src/vision/detector.py:46-49`
- Modify: `scripts/smoke_test_webcam.py:16-19`

**Interfaces:**
- Consumes: `tempfile.gettempdir()`
- Produces: Safe temporary directory `bapode_matplotlib` in standard Windows temp folder instead of Unix `/tmp/matplotlib`.

- [ ] **Step 1: Update src/vision/detector.py**

Replace hardcoded `/tmp/matplotlib` in `src/vision/detector.py`:
```python
import tempfile
mpl_dir = os.path.join(tempfile.gettempdir(), "bapode_matplotlib")
os.environ.setdefault("MPLCONFIGDIR", mpl_dir)
```

- [ ] **Step 2: Update scripts/smoke_test_webcam.py**

Replace hardcoded `/tmp/matplotlib` in `scripts/smoke_test_webcam.py`:
```python
import tempfile
mpl_dir = os.path.join(tempfile.gettempdir(), "bapode_matplotlib")
os.environ.setdefault("MPLCONFIGDIR", mpl_dir)
```

- [ ] **Step 3: Run existing vision tests**

Run:
```powershell
.venv\Scripts\pytest tests/test_vision.py -v
```
Expected: All 15 tests pass.

- [ ] **Step 4: Commit path normalization**

```bash
git add src/vision/detector.py scripts/smoke_test_webcam.py
git commit -m "fix(vision): use system temp directory for font cache on Windows"
```

---

### Task 3: Windows DirectShow Camera Acquisition

**Files:**
- Modify: `src/camera/reader.py:60-75`
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: `sys.platform`, `cv2.CAP_DSHOW`
- Produces: Faster webcam initialization and frame acquisition on Windows without MSMF lag.

- [ ] **Step 1: Update CameraReader in src/camera/reader.py**

Update camera initialization in `CameraReader.run()` to prefer `cv2.CAP_DSHOW` on Windows:
```python
if not self.mock_mode:
    try:
        import cv2

        if sys.platform == "win32":
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.camera_index)
        else:
            cap = cv2.VideoCapture(self.camera_index)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        if not cap.isOpened():
            self.mock_mode = True
    except Exception:
        self.mock_mode = True
```

- [ ] **Step 2: Run camera and integration tests**

Run:
```powershell
.venv\Scripts\pytest tests/test_camera_view.py tests/test_integration.py -v
```
Expected: All tests pass.

- [ ] **Step 3: Commit camera optimization**

```bash
git add src/camera/reader.py
git commit -m "perf(camera): prioritize DirectShow backend for instant webcam capture on Windows"
```

---

### Task 4: Native Windows Platform Bridge (Focus & Power Events)

**Files:**
- Modify: `src/ui/platform_bridge.py`
- Modify: `tests/test_integration.py`

**Interfaces:**
- Consumes: Win32 `WM_POWERBROADCAST`, `SW_SHOWNOACTIVATE`, `SetWindowPos`
- Produces: Non-activating HUD overlay in Windows and automatic sleep/wake dispatching.

- [ ] **Step 1: Write tests for Windows power listener and non-activating window**

Add unit tests in `tests/test_integration.py` verifying `PlatformPowerListener` and `show_window_non_activating` on Windows:
```python
def test_windows_non_activating_show(qtbot):
    from PySide6 import QtWidgets
    from ui.platform_bridge import show_window_non_activating

    widget = QtWidgets.QWidget()
    qtbot.addWidget(widget)
    show_window_non_activating(widget)
    assert widget.isVisible()
```

- [ ] **Step 2: Implement Windows Native Platform Bridge in src/ui/platform_bridge.py**

1. Implement `WindowsPowerEventFilter(QtCore.QAbstractNativeEventFilter)`:
   - Listen for `WM_POWERBROADCAST = 0x0218`.
   - On `PBT_APMSUSPEND = 0x0004`: Emit `system_will_sleep`.
   - On `PBT_APMRESUMEAUTOMATIC = 0x0012` or `PBT_APMRESUMESUSPEND = 0x0007`: Emit `system_did_wake`.
2. In `PlatformPowerListener`:
   - If `sys.platform == "win32"`: Install `WindowsPowerEventFilter` on `QtCore.QCoreApplication.instance()`.
3. In `show_window_non_activating(widget)`:
   - On Windows, invoke `user32.ShowWindow(hwnd, 4)` (`SW_SHOWNOACTIVATE`) and `user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)` so the HUD overlay floats without stealing keyboard focus.
4. Clean up Qt wrapper in `src/ui/qt.py` to remove macOS-only subprocess calls.

- [ ] **Step 3: Run full integration and UI test suite**

Run:
```powershell
.venv\Scripts\pytest tests/test_integration.py -v
```
Expected: All tests pass.

- [ ] **Step 4: Commit Windows platform bridge**

```bash
git add src/ui/platform_bridge.py src/ui/qt.py tests/test_integration.py
git commit -m "feat(ui): implement Win32 non-activating overlay and WM_POWERBROADCAST sleep listeners"
```

---

### Task 5: Windows Launchers and Documentation

**Files:**
- Create: `setup_windows.bat`
- Create: `run.bat`
- Create: `run.ps1`
- Modify: `README.md`

**Interfaces:**
- Produces: Double-click desktop launchers for end users on Windows.

- [ ] **Step 1: Create setup_windows.bat**

Write `setup_windows.bat` to automate virtualenv creation, pip install, and model downloading:
```batch
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
```

- [ ] **Step 2: Create run.bat**

Write `run.bat` for seamless double-click launch:
```batch
@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] Virtual environment belum disiapkan. Menjalankan setup...
    call setup_windows.bat
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
".venv\Scripts\python.exe" "%~dp0src\main.py" %*
```

- [ ] **Step 3: Create run.ps1**

Write `run.ps1` for PowerShell users:
```powershell
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
```

- [ ] **Step 4: Update README.md with Windows quickstart**

Add Windows quickstart instructions at the top of `README.md`:
```markdown
### 🚀 Cara Menjalankan di Windows
1. Jalankan `setup_windows.bat` untuk menginstall dependensi dan mengunduh model MediaPipe.
2. Klik dua kali `run.bat` atau jalankan `.\run.ps1` di PowerShell untuk memulai Bapode.
```

- [ ] **Step 5: Commit launchers and documentation**

```bash
git add setup_windows.bat run.bat run.ps1 README.md
git commit -m "docs(win): add Windows one-click launchers and setup instructions"
```

---

### Task 6: Full Verification, Push, and Pull Request

**Files:**
- Entire repository

- [ ] **Step 1: Run complete test suite**

Run:
```powershell
.venv\Scripts\pytest tests/ -v
```
Expected: 100% tests pass.

- [ ] **Step 2: Run smoke test with mock camera**

Run:
```powershell
.venv\Scripts\python src/main.py --mock-camera --headless
```
Expected: Starts cleanly, processes synthetic frames, and terminates on SIGINT.

- [ ] **Step 3: Push branch feat/windows-support to origin**

Run:
```bash
git push -u origin feat/windows-support
```

- [ ] **Step 4: Open Pull Request to main**

Run:
```bash
gh pr create --base main --head feat/windows-support --title "feat: Windows-dedicated support with native Win32 APIs and one-click launchers" --body "..."
```
