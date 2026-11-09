"""Operating System Abstraction Bridge for Bapode.

Reference: docs/DESIGN_SYSTEM.md & docs/ARCHITECTURE.md
Implements:
1. Screen-Sharing Protection:
   - macOS: NSWindowSharingNone (0) via PyObjC / AppKit
   - Windows: SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE = 0x11)
   - Linux / CI: Safe no-op fallback
2. OS Sleep/Wake Notification Listeners:
   - macOS: NSWorkspaceWillSleepNotification / NSWorkspaceDidWakeNotification
   - Windows: WM_POWERBROADCAST (PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC)
3. Fullscreen Meeting / Presentation Detector (PAUSED_MEETING_AUTO).
"""

import ctypes
import sys
from collections.abc import Callable
from typing import Any

from PySide6 import QtCore, QtWidgets

# Win32 power-management message constants (winuser.h)
_WM_POWERBROADCAST = 0x0218
_PBT_APMSUSPEND = 0x0004
_PBT_APMRESUMESUSPEND = 0x0007
_PBT_APMRESUMEAUTOMATIC = 0x0012


def set_screen_share_protected(widget: QtWidgets.QWidget) -> bool:
    """Make the widget invisible to screen recording/sharing while remaining visible to the user.

    - macOS: Sets NSWindowSharingNone
    - Windows: Sets WDA_EXCLUDEFROMCAPTURE (0x00000011)
    """
    if sys.platform == "darwin":
        # Guard against offscreen platform during headless testing / CI
        from PySide6 import QtGui

        if QtGui.QGuiApplication.platformName() != "cocoa":
            return False

        try:
            # PyObjC AppKit integration
            import objc
            from AppKit import (
                NSStatusWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
                NSWindowCollectionBehaviorMoveToActiveSpace,
            )

            win_id = int(widget.winId())
            if win_id <= 0x1000:
                return False

            ns_view = objc.objc_object(c_void_p=win_id)
            ns_window = ns_view.window()
            if ns_window is not None:
                # NSWindowSharingNone = 0
                ns_window.setSharingType_(0)
                # CRITICAL: Do NOT hide overlay when another app is focused/active
                if hasattr(ns_window, "setHidesOnDeactivate_"):
                    ns_window.setHidesOnDeactivate_(False)
                # Float above normal application windows
                ns_window.setLevel_(NSStatusWindowLevel)
                # Ensure overlay stays visible across virtual desktops and fullscreen apps
                behavior = (
                    NSWindowCollectionBehaviorCanJoinAllSpaces
                    | NSWindowCollectionBehaviorMoveToActiveSpace
                    | NSWindowCollectionBehaviorFullScreenAuxiliary
                    | NSWindowCollectionBehaviorIgnoresCycle
                )
                ns_window.setCollectionBehavior_(behavior)
                return True
        except Exception:
            return False

    elif sys.platform == "win32":
        try:
            # Win32 SetWindowDisplayAffinity
            # WDA_EXCLUDEFROMCAPTURE = 0x00000011
            wda_exclude = 0x00000011
            hwnd = int(widget.winId())
            user32 = ctypes.windll.user32
            result = user32.SetWindowDisplayAffinity(hwnd, wda_exclude)
            return bool(result != 0)
        except Exception:
            return False

    return False


def show_window_non_activating(widget: QtWidgets.QWidget) -> None:
    """Display window without stealing focus or triggering macOS Space switching."""
    widget.show()
    if sys.platform == "darwin":
        from PySide6 import QtGui

        if QtGui.QGuiApplication.platformName() == "cocoa":
            try:
                import objc

                win_id = int(widget.winId())
                if win_id > 0x1000:
                    ns_view = objc.objc_object(c_void_p=win_id)
                    ns_window = ns_view.window()
                    if ns_window is not None:
                        ns_window.orderFrontRegardless()
            except Exception:
                pass


def set_application_activation_policy(is_accessory: bool = True) -> None:
    """Set macOS application activation policy.

    is_accessory=True: App runs as background utility without Dock icon
                       and never causes Space switches when showing notifications.
    is_accessory=False: App runs as regular foreground app.
    """
    if sys.platform == "darwin":
        from PySide6 import QtGui

        if QtGui.QGuiApplication.platformName() == "cocoa":
            try:
                from AppKit import (
                    NSApp,
                    NSApplicationActivationPolicyAccessory,
                    NSApplicationActivationPolicyRegular,
                )

                policy = (
                    NSApplicationActivationPolicyAccessory
                    if is_accessory
                    else NSApplicationActivationPolicyRegular
                )
                NSApp.setActivationPolicy_(policy)
            except Exception:
                pass


class _WindowsPowerEventFilter(QtCore.QAbstractNativeEventFilter):
    """Windows-only native event filter translating WM_POWERBROADCAST to callbacks.

    Windows sends PBT_APMRESUMEAUTOMATIC on every resume and additionally
    PBT_APMRESUMESUSPEND when the user is present; wake is reported only once
    per suspend cycle.
    """

    def __init__(self, on_sleep: Callable[[], None], on_wake: Callable[[], None]) -> None:
        super().__init__()
        self._on_sleep = on_sleep
        self._on_wake = on_wake
        self._suspended = False

    def nativeEventFilter(self, eventType: Any, message: Any) -> Any:  # noqa: N802, N803
        """Inspect raw Win32 MSG structures; never consume the event."""
        try:
            if bytes(eventType) != b"windows_generic_MSG":
                return False, 0
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_POWERBROADCAST:
                if msg.wParam == _PBT_APMSUSPEND and not self._suspended:
                    self._suspended = True
                    self._on_sleep()
                elif (
                    msg.wParam in (_PBT_APMRESUMEAUTOMATIC, _PBT_APMRESUMESUSPEND)
                    and self._suspended
                ):
                    self._suspended = False
                    self._on_wake()
        except Exception:
            pass
        return False, 0


class PlatformPowerListener(QtCore.QObject):
    """Listens for OS Sleep and Wake power notifications across platforms."""

    system_will_sleep = QtCore.Signal()
    system_did_wake = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._registered = False
        self._init_platform_listener()

    def _init_platform_listener(self) -> None:
        """Register OS-specific sleep/wake handlers."""
        if sys.platform == "darwin":
            try:
                from AppKit import NSWorkspace  # pyright: ignore[reportMissingImports]

                nc = NSWorkspace.sharedWorkspace().notificationCenter()
                nc.addObserver_selector_name_object_(
                    self,
                    "onMacSleep:",
                    "NSWorkspaceWillSleepNotification",
                    None,
                )
                nc.addObserver_selector_name_object_(
                    self,
                    "onMacWake:",
                    "NSWorkspaceDidWakeNotification",
                    None,
                )
                self._registered = True
            except Exception:
                pass
        elif sys.platform == "win32":
            self._init_windows_listener()

    def _init_windows_listener(self) -> None:
        """Register a WM_POWERBROADCAST native event filter (Windows only).

        WM_POWERBROADCAST is delivered to top-level windows only (not message-only
        windows), so a hidden, never-shown native QWindow is created as receiver.
        Skipped under QCoreApplication (headless) and non-native QPA platforms
        such as "offscreen" used in tests / CI.
        """
        from PySide6 import QtGui

        app = QtCore.QCoreApplication.instance()
        if not isinstance(app, QtGui.QGuiApplication):
            return
        if QtGui.QGuiApplication.platformName() != "windows":
            return

        try:
            self._win_power_filter = _WindowsPowerEventFilter(
                on_sleep=self.system_will_sleep.emit,
                on_wake=self.system_did_wake.emit,
            )
            app.installNativeEventFilter(self._win_power_filter)

            self._win_power_window = QtGui.QWindow()
            self._win_power_window.setTitle("BapodePowerListener")
            self._win_power_window.create()  # Allocates HWND without showing it
            self._registered = True
        except Exception:
            pass

    def onMacSleep_(self, notification: Any) -> None:  # noqa: N802
        """PyObjC selector for macOS sleep."""
        self.system_will_sleep.emit()

    def onMacWake_(self, notification: Any) -> None:  # noqa: N802
        """PyObjC selector for macOS wake."""
        self.system_did_wake.emit()

    def simulate_sleep(self) -> None:
        """Test helper to simulate system entering sleep."""
        self.system_will_sleep.emit()

    def simulate_wake(self) -> None:
        """Test helper to simulate system waking."""
        self.system_did_wake.emit()


def is_meeting_app_fullscreen() -> bool:
    """Detect if common meeting apps (Zoom, Teams, Meet) are active in fullscreen."""
    # Heuristic detection; falls back cleanly to False
    _meeting_process_names = {
        "zoom.us",
        "teams",
        "ms-teams",
        "webex",
        "slack",
        "google meet",
    }
    if sys.platform == "win32":
        return _is_windows_meeting_fullscreen(_meeting_process_names)
    # Platform-specific check can be expanded; default non-blocking False
    return False


def _is_windows_meeting_fullscreen(meeting_names: set[str]) -> bool:
    """Check whether the foreground Win32 window is a fullscreen meeting app."""
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        win_rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(win_rect)):
            return False

        # MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromWindow(hwnd, 2)
        if not hmon:
            return False

        class _MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mi = _MonitorInfo()
        mi.cbSize = ctypes.sizeof(_MonitorInfo)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return False

        mon = mi.rcMonitor
        covers_monitor = (
            win_rect.left <= mon.left
            and win_rect.top <= mon.top
            and win_rect.right >= mon.right
            and win_rect.bottom >= mon.bottom
        )
        if not covers_monitor:
            return False

        # Match executable name or window title against meeting keywords
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe_name = ""
        if pid.value:
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            hproc = kernel32.OpenProcess(0x1000, False, pid.value)
            if hproc:
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(512)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                        exe_name = buf.value.rsplit("\\", 1)[-1].lower()
                        if exe_name.endswith(".exe"):
                            exe_name = exe_name[:-4]
                finally:
                    kernel32.CloseHandle(hproc)

        title_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, 256)
        title = title_buf.value.lower()

        tokens = set(meeting_names) | {"zoom", "ciscocollabhost"}
        return any(tok in exe_name or tok in title for tok in tokens)
    except Exception:
        return False
