"""Camera Capture Worker (Thread 3) for Bapode.

Reference: docs/specs/01-vision-and-metrics.md & docs/ARCHITECTURE.md
Implements:
1. Thread-safe single-reader camera thread running at 5 FPS (dt ~ 200 ms).
2. Monotonic timestamping at the exact moment of frame acquisition.
3. Overwriting queue of size 1 (queue.Queue(maxsize=1)) to prevent frame lag.
4. Synthetic / Mock camera mode for testing without a physical webcam.
"""

import contextlib
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class CameraFrame:
    """Frame captured from camera with high-resolution monotonic timestamp."""

    image: np.ndarray
    timestamp_s: float
    frame_index: int


class CameraReader(threading.Thread):
    """Thread 3: Single-reader camera capture worker at constant 5 FPS."""

    def __init__(
        self,
        camera_index: int = 0,
        target_fps: float = 5.0,
        mock_mode: bool = False,
        frame_generator: Callable[[int, float], np.ndarray] | None = None,
        frame_width: int = 1280,
        frame_height: int = 720,
    ) -> None:
        super().__init__(name="CameraReaderThread", daemon=True)
        self.camera_index = camera_index
        self.target_fps = target_fps
        self.frame_interval_s = 1.0 / target_fps
        self.mock_mode = mock_mode
        self.frame_generator = frame_generator
        self.frame_width = frame_width
        self.frame_height = frame_height

        self.latest_frame_queue: queue.Queue[CameraFrame] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._frame_counter = 0

    def stop(self) -> None:
        """Signal thread to stop and wait for termination."""
        self._stop_event.set()

    def run(self) -> None:
        """Main loop: acquire frames at 5 FPS and push to single-element queue."""
        cap: Any = None
        if not self.mock_mode:
            try:
                import cv2

                if sys.platform == "win32":
                if sys.platform == "win32":
                    # Windows: force DirectShow backend to avoid MSMF lag/freeze
                    cap = self._open_windows_capture(cv2)
                else:
                    cap = cv2.VideoCapture(self.camera_index)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
                    cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                if not cap.isOpened():
                    # Fallback to mock mode if camera cannot be opened
                    self.mock_mode = True
            except Exception:
                self.mock_mode = True

        try:
            while not self._stop_event.is_set():
                cycle_start = time.monotonic()
                image: np.ndarray | None = None

                if self.mock_mode or cap is None:
                    image = self._generate_mock_frame(cycle_start)
                else:
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        image = frame
                    else:
                        image = self._generate_mock_frame(cycle_start)

                if image is not None:
                    self._frame_counter += 1
                    camera_frame = CameraFrame(
                        image=image,
                        timestamp_s=cycle_start,
                        frame_index=self._frame_counter,
                    )
                    # Push to queue with latest-frame overwrite strategy
                    self._push_latest_frame(camera_frame)

                # Maintain steady 5 FPS (200 ms interval)
                elapsed = time.monotonic() - cycle_start
                sleep_time = max(0.001, self.frame_interval_s - elapsed)
                self._stop_event.wait(sleep_time)
        finally:
            if cap is not None:
                with contextlib.suppress(Exception):
                    cap.release()

    def _open_windows_capture(self, cv2: Any) -> Any:
        """Open the camera on Windows using the DirectShow backend (cv2.CAP_DSHOW).

        The default Media Foundation (MSMF) backend frequently takes several seconds
        to open, buffers stale frames, or freezes on many UVC webcams. DirectShow
        opens quickly and always returns the most recent frame. If DirectShow cannot
        open the device, fall back to MSMF so the camera remains usable.
        """
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            with contextlib.suppress(Exception):
                cap.release()
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_MSMF)

        if cap.isOpened():
            # FOURCC must be set before resolution under DirectShow. MJPG lets most
            # webcams deliver 1280x720 over USB 2.0; unsupported devices ignore it.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
            cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            # Keep driver-side buffering minimal so 5 FPS reads stay fresh
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _push_latest_frame(self, frame: CameraFrame) -> None:
        """Push frame into queue, evicting any old frame if queue is full."""
        try:
            self.latest_frame_queue.put_nowait(frame)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.latest_frame_queue.get_nowait()
            with contextlib.suppress(queue.Full):
                self.latest_frame_queue.put_nowait(frame)

    def _generate_mock_frame(self, timestamp: float) -> np.ndarray:
        """Generate synthetic mock image when camera is unavailable or in mock mode."""
        if self.frame_generator is not None:
            return self.frame_generator(self._frame_counter, timestamp)
        # Default blank neutral gray image with shape (H, W, 3)
        return np.full(
            (self.frame_height, self.frame_width, 3),
            fill_value=128,
            dtype=np.uint8,
        )
