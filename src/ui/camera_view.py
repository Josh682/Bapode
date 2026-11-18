"""Camera Preview Widget for PySide6 GUI.

Renders live 5 FPS annotated video frames with smooth aspect ratio scaling.
"""

from __future__ import annotations

from ui.qt import QtCore, QtGui, QtWidgets
from ui.theme import ColorTokens


class CameraPreviewWidget(QtWidgets.QFrame):
    """Smooth, aspect-ratio preserving camera viewport widget."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            CameraPreviewWidget {{
                background-color: {ColorTokens.BG_CANVAS};
                border: 1px solid {ColorTokens.BORDER_STRONG};
                border-radius: 12px;
            }}
            """
        )
        self.setMinimumSize(480, 270)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.video_label = QtWidgets.QLabel()
        self.video_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet(
            f"background: transparent; color: {ColorTokens.TEXT_SECONDARY}; font-size: 14px;"
        )
        self.video_label.setText("📷 Menunggu Feed Webcam...")
        layout.addWidget(self.video_label)

        self._current_pixmap: QtGui.QPixmap | None = None

    def update_frame(self, image: QtGui.QImage) -> None:
        """Receive QImage from VisionTrackerWorker and render with aspect ratio scaling."""
        if image.isNull():
            return

        pixmap = QtGui.QPixmap.fromImage(image)
        self._current_pixmap = pixmap
        self._render_scaled()

    def _render_scaled(self) -> None:
        """Scale current pixmap to fill label while preserving 16:9 ratio."""
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        lbl_size = self.video_label.size()
        if lbl_size.width() <= 10 or lbl_size.height() <= 10:
            return

        scaled = self._current_pixmap.scaled(
            lbl_size,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        """Re-render on resize to maintain crisp resolution."""
        super().resizeEvent(event)
        self._render_scaled()
