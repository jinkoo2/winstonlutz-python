"""2D medical image viewer (pan / zoom / window-level), similar to vtk_image_labeler_2d."""

from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

import SimpleITK as sitk


def sitk_to_array(image: sitk.Image) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    if image.GetDimension() == 3 and image.GetSize()[2] == 1:
        image = image[:, :, 0]
    arr = sitk.GetArrayFromImage(image).astype(np.float32)
    if arr.ndim == 3:
        arr = arr[0]
    origin = (float(image.GetOrigin()[0]), float(image.GetOrigin()[1]))
    spacing = (float(image.GetSpacing()[0]), float(image.GetSpacing()[1]))
    return arr, origin, spacing


def apply_window_level(arr: np.ndarray, window: float, level: float) -> np.ndarray:
    if window <= 0:
        window = 1.0
    lo = level - window / 2.0
    hi = level + window / 2.0
    scaled = (arr - lo) / (hi - lo)
    return np.clip(scaled * 255.0, 0, 255).astype(np.uint8)


class ImageViewer(QGraphicsView):
    status_changed = pyqtSignal(str)
    window_level_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(80, 80, 80))

        self._arr: np.ndarray | None = None
        self._origin = (0.0, 0.0)
        self._spacing = (1.0, 1.0)
        self._window = 1000.0
        self._level = 500.0
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._marker_items: list[QGraphicsLineItem] = []
        self._markers: list[tuple[float, float, QColor]] = []
        self._show_markers = True
        self._panning = True
        self._zooming = True
        self._last_pos = None
        self._wl_drag = False

    def set_image(self, arr: np.ndarray, origin, spacing, window=None, level=None):
        self._arr = arr
        self._origin = (float(origin[0]), float(origin[1]))
        self._spacing = (float(spacing[0]), float(spacing[1]))
        vmin = float(np.min(arr))
        vmax = float(np.max(arr))
        if window is None:
            window = max(vmax - vmin, 1.0)
        if level is None:
            level = (vmin + vmax) / 2.0
        self._window = window
        self._level = level
        self._refresh_pixmap()
        self.fit_image()

    def set_markers(self, markers: list[tuple[float, float, QColor]]):
        """markers are physical (x, y) in the image coordinate frame."""
        self._markers = markers
        self._draw_markers()

    def set_show_markers(self, visible: bool):
        self._show_markers = bool(visible)
        self._draw_markers()

    def clear(self):
        self._arr = None
        self._markers = []
        self._clear_markers()
        self.scene().clear()
        self._pixmap_item = None

    def set_window_level(self, window: float, level: float):
        self._window = max(float(window), 1.0)
        self._level = float(level)
        self._refresh_pixmap()

    def window_level(self) -> tuple[float, float]:
        return self._window, self._level

    def intensity_range(self) -> tuple[float, float]:
        if self._arr is None:
            return 0.0, 1.0
        return float(np.min(self._arr)), float(np.max(self._arr))

    def enable_panning(self, enabled: bool):
        self._panning = enabled
        self.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)

    def enable_zooming(self, enabled: bool):
        self._zooming = enabled

    def zoom_in(self):
        self.scale(1.2, 1.2)

    def zoom_out(self):
        self.scale(0.8, 0.8)

    def fit_image(self):
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    def _refresh_pixmap(self):
        if self._arr is None:
            return
        gray = np.ascontiguousarray(apply_window_level(self._arr, self._window, self._level))
        h, w = gray.shape
        qimg = QImage(gray.data, w, h, gray.strides[0], QImage.Format_Grayscale8).copy()
        pix = QPixmap.fromImage(qimg)
        if self._pixmap_item is None:
            self.scene().clear()
            self._marker_items = []
            self._pixmap_item = self.scene().addPixmap(pix)
        else:
            self._pixmap_item.setPixmap(pix)
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
        self._draw_markers()

    def _clear_markers(self):
        for item in self._marker_items:
            self.scene().removeItem(item)
        self._marker_items = []

    def _draw_markers(self):
        self._clear_markers()
        if not self._show_markers or self._arr is None:
            return
        arm = 14
        for x_mm, y_mm, color in self._markers:
            col = (x_mm - self._origin[0]) / self._spacing[0]
            row = (y_mm - self._origin[1]) / self._spacing[1]
            pen = QPen(color, 0)
            h = self.scene().addLine(col - arm, row, col + arm, row, pen)
            v = self.scene().addLine(col, row - arm, col, row + arm, pen)
            self._marker_items.extend([h, v])

    def wheelEvent(self, event):
        if not self._zooming or self._arr is None:
            return
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def mousePressEvent(self, event):
        self._last_pos = event.pos()
        if event.button() == Qt.RightButton or (
            event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier
        ):
            self._wl_drag = True
            self.setCursor(Qt.SizeVerCursor)
        elif event.button() == Qt.LeftButton and self._panning:
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._emit_status(event.pos())
        if self._last_pos is not None and self._wl_drag and self._arr is not None:
            delta = event.pos() - self._last_pos
            vmin, vmax = self.intensity_range()
            span = max(vmax - vmin, 1.0)
            self._window = max(self._window + delta.x() * span / 400.0, 1.0)
            self._level = self._level - delta.y() * span / 400.0
            self._last_pos = event.pos()
            self._refresh_pixmap()
            self.window_level_changed.emit(self._window, self._level)
            self.status_changed.emit(f"Window: {self._window:.1f}, Level: {self._level:.1f}")
            return
        if (
            self._last_pos is not None
            and event.buttons() & Qt.LeftButton
            and self._panning
            and not self._wl_drag
        ):
            delta = event.pos() - self._last_pos
            self._last_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._last_pos = None
        self._wl_drag = False
        self.setCursor(Qt.OpenHandCursor if self._panning else Qt.ArrowCursor)
        super().mouseReleaseEvent(event)

    def _emit_status(self, view_pos):
        if self._arr is None:
            return
        scene_pos = self.mapToScene(view_pos)
        col = int(scene_pos.x())
        row = int(scene_pos.y())
        h, w = self._arr.shape
        if 0 <= col < w and 0 <= row < h:
            value = float(self._arr[row, col])
            x = self._origin[0] + col * self._spacing[0]
            y = self._origin[1] + row * self._spacing[1]
            self.status_changed.emit(
                f"World: ({x:.2f}, {y:.2f})  Index: ({col}, {row})  Value: {value:.2f}"
            )
        else:
            self.status_changed.emit(f"World: outside  View: ({scene_pos.x():.1f}, {scene_pos.y():.1f})")
