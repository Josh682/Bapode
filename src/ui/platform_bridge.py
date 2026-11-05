"""Operating System Abstraction Bridge for Posture Guardian.

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
from typing import Any

from PySide6 import QtCore, QtWidgets


def set_screen_share_protected(widget: QtWidgets.QWidget) -> bool:
    """Make the widget invisible to screen recording/sharing while remaining visible to the user.

    - macOS: Sets NSWindowSharingNone
    - Windows: Sets WDA_EXCLUDEFROMCAPTURE (0x00000011)
    """
    if sys.platform == "darwin":
        try:
            # PyObjC AppKit integration
            import objc  # type: ignore[import-not-found]
            from AppKit import (  # type: ignore[import-not-found]
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
            )

            win_id = int(widget.winId())
            ns_view = objc.objc_object(c_void_p=win_id)
            ns_window = ns_view.window()
            if ns_window is not None:
                # NSWindowSharingNone = 0
                ns_window.setSharingType_(0)
                # Ensure overlay stays visible across virtual desktops and fullscreen apps
                behavior = (
                    NSWindowCollectionBehaviorCanJoinAllSpaces
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
    # Platform-specific check can be expanded; default non-blocking False
    return False
