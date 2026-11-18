# Bapode Windows-Dedicated Support Design Specification

**Date:** 2026-11-09  
**Branch:** `feat/windows-support`  
**Status:** Approved  

---

## 1. Executive Summary
Bapode v3.0 is a non-intrusive desktop posture monitoring utility originally developed with macOS-centric assumptions (such as PyObjC dependencies, Bash launcher, and macOS sleep notifications). 
This specification outlines the conversion of Bapode into a dedicated, robust, native **Windows** application with zero macOS bloat, full Win32 API integration, optimized UVC DirectShow camera capture, native OS sleep/wake hooks, and one-click batch/PowerShell launchers.

---

## 2. Technical Requirements & Decisions

### 2.1 Dependencies & Packaging
- **File:** `pyproject.toml`, `requirements-windows.txt`
- **Actions:**
  - Remove all macOS dependencies (`pyobjc-core`, `pyobjc-framework-Cocoa`).
  - Add required runtime dependencies:
    - `PySide6>=6.6.0`
    - `opencv-python>=4.8.0`
    - `mediapipe>=0.10.0`
    - `numpy>=1.26.0`
    - `pywin32>=306; sys_platform == 'win32'`
  - Add development dependencies:
    - `pytest>=8.0.0`
    - `pytest-qt>=4.4.0`

### 2.2 Platform Bridge (`src/ui/platform_bridge.py`)
- **Screen-Sharing Protection:**
  - Call `user32.SetWindowDisplayAffinity(hwnd, 0x00000011)` (`WDA_EXCLUDEFROMCAPTURE`) on Windows 10/11.
- **Non-Activating HUD Overlay Display:**
  - In addition to Qt flags (`FramelessWindowHint`, `WindowStaysOnTopHint`, `Tool`, `WindowTransparentForInput`, `WindowDoesNotAcceptFocus`, `WA_ShowWithoutActivating`), invoke Win32 `user32.ShowWindow(hwnd, 4)` (`SW_SHOWNOACTIVATE`) and `user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)` (`HWND_TOPMOST`, `SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW`).
  - This ensures Windows never shifts keyboard or focus context away from user IDEs, editors, or browsers.
- **OS Sleep & Wake Listeners (`PlatformPowerListener`):**
  - Implement a Windows native event filter subclassing `QtCore.QAbstractNativeEventFilter`.
  - Listen for `WM_POWERBROADCAST` (`0x0218`):
    - `PBT_APMSUSPEND` (`0x0004`): Emit `system_will_sleep`.
    - `PBT_APMRESUMEAUTOMATIC` (`0x0012`) / `PBT_APMRESUMESUSPEND` (`0x0007`): Emit `system_did_wake`.
  - When installed on `QCoreApplication`, automatically routes sleep and wake events into `VisionTrackerWorker.on_os_sleep` and `on_os_wake`.
- **Fullscreen Meeting Detector:**
  - On Windows, inspect foreground window process names against `{"teams.exe", "ms-teams.exe", "zoom.exe", "webex.exe", "slack.exe", "chrome.exe", "msedge.exe"}` and check if the window bounds match primary screen resolution.

### 2.3 Camera Reader Optimization (`src/camera/reader.py`)
- On Windows (`sys.platform == "win32"`), use `cv2.CAP_DSHOW` (DirectShow) backend for `cv2.VideoCapture` to avoid 3-5 second device negotiation delays and black frame drops caused by default MSMF.
- Fallback gracefully to default backend if DirectShow is unavailable.

### 2.4 Temporary Directory & Path Normalization (`src/vision/detector.py`, `scripts/smoke_test_webcam.py`)
- Replace hardcoded `os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")` with:
  ```python
  import tempfile
  mpl_dir = os.path.join(tempfile.gettempdir(), "bapode_matplotlib")
  os.environ.setdefault("MPLCONFIGDIR", mpl_dir)
  ```

### 2.5 Windows Launchers & Automation
- **`setup_windows.bat`:**
  - Checks Python installation (>= 3.11).
  - Creates virtual environment `.venv` using `python -m venv .venv`.
  - Upgrades pip and installs `requirements-windows.txt`.
  - Automatically runs `scripts/download_models.py` to fetch MediaPipe `.task` models.
- **`run.bat`:**
  - One-click launcher script.
  - Automatically activates `.venv` and executes `python src\main.py %*`.
- **`run.ps1`:**
  - Modern PowerShell equivalent launcher script.

---

## 3. Verification & Acceptance Criteria
1. All 77 existing tests pass under `pytest tests/ -v`.
2. Windows power listener test in `tests/test_integration.py` verifies both simulation and native event emission.
3. Smoke test script runs without errors on Windows.
4. Git branch `feat/windows-support` is cleanly committed, pushed to `origin`, and Pull Request is opened with full documentation.
