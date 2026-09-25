"""Winston-Lutz desktop app: pick a folder of RI images, analyze, and review."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QPoint, QPointF, QSettings, QSize, QThread, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSlider,
    QSplitter,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import SimpleITK as sitk

from .analysis import classify_rt_image, load_ri_for_display
from .image_viewer import ImageViewer, sitk_to_array
from .models import WinstonLutzItem
from .pipeline import (
    analyze_folder,
    find_html_report,
    infer_folder_bb_methods,
    list_ri_files,
    load_existing_results,
    write_html_reports,
)

logger = logging.getLogger(__name__)


def app_icon() -> QIcon:
    """Red bull's-eye icon for the window and taskbar."""
    icon_file = Path(__file__).resolve().parent / "icons" / "app.png"
    if icon_file.is_file():
        return QIcon(str(icon_file))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        cx = cy = size / 2.0
        rings = (
            (0.46, QColor(176, 16, 16)),
            (0.34, QColor(255, 255, 255)),
            (0.22, QColor(220, 28, 28)),
            (0.12, QColor(255, 255, 255)),
            (0.055, QColor(168, 0, 0)),
        )
        painter.setPen(Qt.NoPen)
        for radius, color in rings:
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), size * radius, size * radius)
        painter.end()
        icon.addPixmap(pm)
    return icon


def machine_name(folder: Path | None) -> str:
    """Machine folder name, skipping a Data parent. e.g. Edge."""
    if folder is None:
        return ""
    folder = Path(folder)
    parent = folder.parent
    name = parent.name
    if name.lower() == "data":
        name = parent.parent.name
    if not name or name in (".", ""):
        return ""
    return name


def case_display_name(folder: Path | None) -> str:
    """Machine/case label, e.g. Edge/26-09-25_06-15-37."""
    if folder is None:
        return ""
    case = Path(folder).name
    machine = machine_name(folder)
    if not machine:
        return case
    return f"{machine}/{case}"


class SummaryBanner(QFrame):
    """Readable case header above the results table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("summaryBanner")
        self.setStyleSheet(
            """
            QFrame#summaryBanner {
                background: #ffffff;
                border: 1px solid #94a3b8;
            }
            QLabel#summaryTitle {
                color: #0f172a;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#summaryCaption {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#summaryValue {
                color: #0f172a;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#summaryHint {
                color: #334155;
                font-size: 12px;
            }
            """
        )
        self.title = QLabel("No folder selected")
        self.title.setObjectName("summaryTitle")
        self.title.setWordWrap(True)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.result = QLabel("")
        self.result.setObjectName("summaryValue")
        self.result.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        result_font = QFont()
        result_font.setPointSize(16)
        result_font.setBold(True)
        self.result.setFont(result_font)
        self.hint = QLabel("Open a folder that contains RI.*.dcm files.")
        self.hint.setObjectName("summaryHint")
        self.hint.setWordWrap(True)

        self._fields = {}
        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        for col, key in enumerate(("Images", "Max d", "Tolerance", "Operator")):
            cap = QLabel(key)
            cap.setObjectName("summaryCaption")
            val = QLabel("—")
            val.setObjectName("summaryValue")
            grid.addWidget(cap, 0, col)
            grid.addWidget(val, 1, col)
            self._fields[key] = val

        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.result, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addLayout(grid)
        layout.addWidget(self.hint)

    def set_message(self, text: str, title: str = "Folder loaded"):
        self.title.setText(title)
        self.result.clear()
        self.result.setStyleSheet("")
        for val in self._fields.values():
            val.setText("—")
        self.hint.setText(text)
        self.hint.show()

    def set_results(self, case: str, n_images: int, max_d: float, tol: float, passed: bool, operator: str):
        self.title.setText(case or "Winston-Lutz")
        if passed:
            self.result.setText("PASS")
            self.result.setStyleSheet("color: #047844; font-size: 18px; font-weight: 800;")
        else:
            self.result.setText("FAIL")
            self.result.setStyleSheet("color: #b91c1c; font-size: 18px; font-weight: 800;")
        self._fields["Images"].setText(str(n_images))
        self._fields["Max d"].setText(f"{max_d:.2f} mm")
        self._fields["Tolerance"].setText(f"{tol:.1f} mm")
        self._fields["Operator"].setText(operator.strip() or "—")
        self.hint.hide()

PASS_FG = QColor(4, 120, 50)
PASS_BG = QColor(220, 247, 228)
FAIL_FG = QColor(185, 28, 28)
FAIL_BG = QColor(254, 226, 226)


def _toolbar_icon(name: str, size: int = 22) -> QIcon:
    """Simple painted toolbar icons so the app does not need image files."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(31, 58, 95)
    p.setPen(QPen(c, 1.8))
    p.setBrush(c)
    s = size
    if name == "folder":
        p.setBrush(QColor(232, 176, 54))
        p.setPen(QPen(QColor(166, 117, 20), 1.2))
        p.drawRoundedRect(2, 8, s - 4, s - 11, 2, 2)
        p.drawRoundedRect(2, 5, 9, 6, 2, 2)
    elif name == "run":
        p.setBrush(QColor(22, 163, 74))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([QPoint(5, 4), QPoint(s - 4, s // 2), QPoint(5, s - 4)]))
    elif name == "report":
        p.setBrush(QColor(248, 250, 252))
        p.setPen(QPen(c, 1.4))
        p.drawRoundedRect(5, 3, s - 9, s - 6, 2, 2)
        p.drawLine(8, 8, s - 7, 8)
        p.drawLine(8, 12, s - 7, 12)
        p.drawLine(8, 16, s - 9, 16)
    elif name == "zoom_in":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(3, 3, 12, 12)
        p.drawLine(14, 14, s - 3, s - 3)
        p.drawLine(6, 9, 12, 9)
        p.drawLine(9, 6, 9, 12)
    elif name == "zoom_out":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(3, 3, 12, 12)
        p.drawLine(14, 14, s - 3, s - 3)
        p.drawLine(6, 9, 12, 9)
    elif name == "zoom_fit":
        p.setBrush(Qt.NoBrush)
        p.drawRect(4, 4, s - 8, s - 8)
        p.drawLine(4, 4, 8, 8)
        p.drawLine(s - 4, 4, s - 8, 8)
        p.drawLine(4, s - 4, 8, s - 8)
        p.drawLine(s - 4, s - 4, s - 8, s - 8)
    elif name == "pan":
        p.setBrush(c)
        mid = s // 2
        p.drawPolygon(QPolygon([QPoint(mid, 2), QPoint(mid - 4, 8), QPoint(mid + 4, 8)]))
        p.drawPolygon(QPolygon([QPoint(mid, s - 2), QPoint(mid - 4, s - 8), QPoint(mid + 4, s - 8)]))
        p.drawPolygon(QPolygon([QPoint(2, mid), QPoint(8, mid - 4), QPoint(8, mid + 4)]))
        p.drawPolygon(QPolygon([QPoint(s - 2, mid), QPoint(s - 8, mid - 4), QPoint(s - 8, mid + 4)]))
        p.drawLine(mid, 7, mid, s - 7)
        p.drawLine(7, mid, s - 7, mid)
    elif name == "wheel":
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(4, 3, 14, 14)
        p.drawEllipse(8, 7, 6, 6)
        p.drawLine(11, 17, 11, s - 2)
    elif name == "prev":
        p.drawPolygon(QPolygon([QPoint(6, s // 2), QPoint(s - 5, 5), QPoint(s - 5, s - 5)]))
    elif name == "next":
        p.drawPolygon(QPolygon([QPoint(s - 6, s // 2), QPoint(5, 5), QPoint(5, s - 5)]))
    elif name == "help":
        p.setBrush(QColor(37, 99, 235))
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, s - 4, s - 4)
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(Qt.NoBrush)
        p.drawArc(7, 5, 8, 8, 40 * 16, 200 * 16)
        p.drawPoint(11, s - 6)
    p.end()
    return QIcon(pm)


class AnalyzeWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, folder: Path, mv_method: str, kv_method: str, preprocess: bool):
        super().__init__()
        self.folder = folder
        self.mv_method = mv_method
        self.kv_method = kv_method
        self.preprocess = preprocess

    def run(self):
        try:
            self.progress.emit(f"Analyzing {self.folder} ...")
            items = analyze_folder(
                self.folder,
                mv_method=self.mv_method,
                kv_method=self.kv_method,
                preprocess=self.preprocess,
            )
            self.finished_ok.emit(items)
        except Exception as exc:
            logger.exception("analysis failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Winston-Lutz")
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)

        self.folder: Path | None = None
        self.files: list[Path] = []
        self.items: list[WinstonLutzItem] = []
        self.worker: AnalyzeWorker | None = None
        self.tol_mm = 1.0
        self._current_modality: str | None = None

        self.settings = QSettings("MachineQA", "WinstonLutz")
        self.viewer = ImageViewer(self)
        self.viewer.status_changed.connect(self.statusBar().showMessage)
        self.viewer.window_level_changed.connect(self._on_viewer_wl)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "Type", "Gantry", "Table", "Coll", "BB − FC (mm)", "d (mm)", "Result"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.setStyleSheet(
            """
            QTableWidget {
                background: #ffffff;
                alternate-background-color: #f4f6f8;
                gridline-color: #e5e7eb;
                border: 1px solid #d0d5dd;
                outline: none;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #111827;
            }
            QHeaderView::section {
                background: #1f3a5f;
                color: #ffffff;
                font-weight: 600;
                padding: 8px 6px;
                border: none;
                border-right: 1px solid #34547a;
            }
            """
        )

        self.summary = SummaryBanner()

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.summary)
        left_layout.addWidget(self.table, 1)
        left_layout.addWidget(self.progress)

        self.viewer_hint = QLabel(
            "Red cross = field center.  Green cross = BB.  Shift+drag or right-drag changes window/level."
        )
        self.viewer_hint.setWordWrap(True)
        self.viewer_hint.setAlignment(Qt.AlignCenter)
        self.viewer_hint.setStyleSheet(
            "QLabel { color: #334155; padding: 8px 10px; background: #f8fafc; border-top: 1px solid #cbd5e1; }"
        )
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.viewer, 1)
        right_layout.addWidget(self.viewer_hint)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self._split_initialized = False

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self._build_toolbar())
        central_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._split_initialized:
            self._split_initialized = True
            half = max(self.splitter.width() // 2, 1)
            self.splitter.setSizes([half, half])

    def _make_action(self, text, icon_name, slot, shortcut=None, checkable=False, checked=False):
        act = QAction(_toolbar_icon(icon_name), text, self)
        if shortcut:
            act.setShortcut(shortcut)
        if checkable:
            act.setCheckable(True)
            act.setChecked(checked)
            act.toggled.connect(slot)
        else:
            act.triggered.connect(slot)
        self.addAction(act)
        return act

    def _tool_button(self, action: QAction) -> QToolButton:
        btn = QToolButton()
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setIconSize(QSize(20, 20))
        btn.setAutoRaise(True)
        return btn

    def _toolbar_row(self, *widgets) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        for widget in widgets:
            layout.addWidget(widget, 0)
        layout.addStretch(1)
        return row

    def _build_toolbar(self) -> QWidget:
        open_act = self._make_action("Open Folder", "folder", self.open_folder, "Ctrl+O")
        self.run_act = self._make_action("Run Analysis", "run", self.run_analysis, "Ctrl+R")
        self.run_act.setEnabled(False)
        self.report_act = self._make_action("View Report", "report", self.view_report)
        self.report_act.setEnabled(False)
        help_act = self._make_action("Help", "help", self._show_help)

        self.mv_method = QComboBox()
        self.mv_method.addItems(["ConnectedComponent", "LoG", "OtsuThreshold"])
        self.kv_method = QComboBox()
        self.kv_method.addItems(["ConnectedComponent", "OtsuThreshold", "LoG"])
        self.tol_spin = QDoubleSpinBox()
        self.tol_spin.setRange(0.1, 5.0)
        self.tol_spin.setSingleStep(0.1)
        self.tol_spin.setValue(1.0)
        self.tol_spin.valueChanged.connect(self._tol_changed)

        zoom_in = self._make_action("Zoom In", "zoom_in", self.viewer.zoom_in)
        zoom_out = self._make_action("Zoom Out", "zoom_out", self.viewer.zoom_out)
        zoom_fit = self._make_action("Fit", "zoom_fit", self.viewer.fit_image)
        self.pan_act = self._make_action(
            "Pan", "pan", self.viewer.enable_panning, checkable=True, checked=True
        )
        self.zoom_act = self._make_action(
            "Wheel Zoom", "wheel", self.viewer.enable_zooming, checkable=True, checked=True
        )
        prev_act = self._make_action("Prev", "prev", lambda: self._step_image(-1), Qt.Key_Left)
        next_act = self._make_action("Next", "next", lambda: self._step_image(1), Qt.Key_Right)

        self.overlay_box = QCheckBox("FC/BB overlays")
        self.overlay_box.setChecked(True)
        self.overlay_box.toggled.connect(self.viewer.set_show_markers)
        self.view_style = QComboBox()
        self.view_style.addItem("Report crop", "rescale")
        self.view_style.addItem("Report LoG", "log")
        self.view_style.addItem("Full image", "full")
        saved_style = self.settings.value("view_style", "log")
        idx = self.view_style.findData(saved_style)
        if idx < 0:
            idx = self.view_style.findData("log")
        self.view_style.setCurrentIndex(idx if idx >= 0 else 1)
        self.view_style.currentIndexChanged.connect(self._view_style_changed)
        self.window_slider = QSlider(Qt.Horizontal)
        self.window_slider.setMinimum(1)
        self.window_slider.setMaximum(4000)
        self.window_slider.setValue(1000)
        self.window_slider.setFixedWidth(140)
        self.window_slider.valueChanged.connect(self._wl_slider_changed)
        self.level_slider = QSlider(Qt.Horizontal)
        self.level_slider.setMinimum(-2000)
        self.level_slider.setMaximum(4000)
        self.level_slider.setValue(500)
        self.level_slider.setFixedWidth(140)
        self.level_slider.valueChanged.connect(self._wl_slider_changed)

        panel = QWidget()
        panel.setStyleSheet(
            "QWidget { background: #f3f4f6; }"
            "QToolButton { padding: 4px 8px; }"
            "QComboBox, QDoubleSpinBox { min-height: 26px; }"
        )
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(
            self._toolbar_row(
                self._tool_button(open_act),
                self._tool_button(self.run_act),
                self._tool_button(self.report_act),
                QLabel(" MV BB Detection "),
                self.mv_method,
                QLabel(" kV BB Detection "),
                self.kv_method,
                QLabel(" Tol (mm) "),
                self.tol_spin,
                self._tool_button(help_act),
            )
        )
        vbox.addWidget(
            self._toolbar_row(
                self._tool_button(zoom_in),
                self._tool_button(zoom_out),
                self._tool_button(zoom_fit),
                self._tool_button(self.pan_act),
                self._tool_button(self.zoom_act),
                self._tool_button(prev_act),
                self._tool_button(next_act),
                self.overlay_box,
                QLabel(" View "),
                self.view_style,
            )
        )
        vbox.addWidget(
            self._toolbar_row(
                QLabel(" Window "),
                self.window_slider,
                QLabel(" Level "),
                self.level_slider,
            )
        )
        return panel

    def _last_dir(self) -> str:
        return self.settings.value("last_directory", str(Path.cwd()))

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select folder with RI DICOM files", self._last_dir())
        if path:
            self.load_folder(Path(path))

    def load_folder(self, folder: str | Path) -> bool:
        folder = Path(folder)
        files = list_ri_files(folder)
        if not files:
            QMessageBox.warning(self, "No RI files", f"No RI.*.dcm files in:\n{folder}")
            return False
        self.settings.setValue("last_directory", str(folder))
        self.folder = folder
        self.files = files
        self.items = load_existing_results(folder)
        self.run_act.setEnabled(True)
        self.setWindowTitle(f"Winston-Lutz — {case_display_name(folder)}")
        self._apply_bb_methods(*infer_folder_bb_methods(folder, self.items))
        self._fill_table()
        self._update_report_button()
        extra = f"  ({len(self.items)} existing result.txt)" if self.items else ""
        self.statusBar().showMessage(f"Loaded {len(files)} RI images{extra}")
        return True

    def _apply_bb_methods(self, mv_method: str, kv_method: str):
        if mv_method:
            idx = self.mv_method.findText(mv_method)
            if idx >= 0:
                self.mv_method.setCurrentIndex(idx)
        if kv_method:
            idx = self.kv_method.findText(kv_method)
            if idx >= 0:
                self.kv_method.setCurrentIndex(idx)

    def view_report(self):
        if self.folder is None:
            return
        path = find_html_report(self.folder)
        if path is None and self.items:
            path = write_html_reports(self.folder, self.items, self.tol_mm)
            self._update_report_button()
        if path is None:
            QMessageBox.information(
                self,
                "No report",
                "No report.html yet. Run Analysis first, or open a case folder that already has a report.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _update_report_button(self):
        has_report = self.folder is not None and find_html_report(self.folder) is not None
        can_build = bool(self.folder and self.items)
        self.report_act.setEnabled(has_report or can_build)

    def run_analysis(self):
        if not self.folder:
            return
        self.run_act.setEnabled(False)
        self.progress.show()
        self.worker = AnalyzeWorker(
            self.folder,
            self.mv_method.currentText(),
            self.kv_method.currentText(),
            False,
        )
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.finished_ok.connect(self._analysis_done)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.start()

    def _analysis_done(self, items: list):
        self.progress.hide()
        self.run_act.setEnabled(True)
        self.items = items
        if self.folder and items:
            write_html_reports(self.folder, items, self.tol_mm)
        self._fill_table()
        self._update_report_button()
        self.statusBar().showMessage(f"Analysis finished: {len(items)} images")

    def _analysis_failed(self, message: str):
        self.progress.hide()
        self.run_act.setEnabled(True)
        QMessageBox.critical(self, "Analysis failed", message)

    def _tol_changed(self, value: float):
        self.tol_mm = value
        self._fill_table(reload_image=False)

    def _fill_table(self, reload_image: bool = True):
        current = self._current_path()
        by_path = {Path(i.DCM).resolve(): i for i in self.items}
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.files))
        max_d = 0.0
        n_fail = 0
        for row, path in enumerate(self.files):
            item = by_path.get(path.resolve())
            values = [str(row + 1), "", "", "", "", "", "", ""]
            passed = None
            if item:
                d = item.calc_norm_of_bb_offset_from_field_center()
                max_d = max(max_d, d)
                passed = d <= self.tol_mm
                if not passed:
                    n_fail += 1
                off = item.bb_offset_from_field_center
                values = [
                    str(row + 1),
                    "MV" if item.MV else "kV",
                    f"{item.gantry:.1f}",
                    f"{item.table:.1f}",
                    f"{item.collimator:.1f}",
                    f"{off[0]:.2f}, {off[1]:.2f}",
                    f"{d:.2f}",
                    "Pass" if passed else "Fail",
                ]
            result_font = QFont(self.table.font())
            result_font.setBold(True)
            alignments = [
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
                Qt.AlignCenter,
            ]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setToolTip(path.name)
                cell.setTextAlignment(alignments[col] | Qt.AlignVCenter)
                if passed is not None and col in (6, 7):
                    cell.setFont(result_font)
                    if passed:
                        cell.setForeground(PASS_FG)
                        cell.setBackground(PASS_BG)
                    else:
                        cell.setForeground(FAIL_FG)
                        cell.setBackground(FAIL_BG)
                self.table.setItem(row, col, cell)
        select = 0
        if current is not None:
            for i, path in enumerate(self.files):
                if path.resolve() == current.resolve():
                    select = i
                    break
        if self.files:
            self.table.selectRow(select)
        self.table.blockSignals(False)
        if reload_image:
            self._on_row_selected()
        label = case_display_name(self.folder)
        if self.items:
            self.summary.set_results(
                case=label,
                n_images=len(self.items),
                max_d=max_d,
                tol=self.tol_mm,
                passed=n_fail == 0,
                operator=self.items[0].user,
            )
        elif self.files:
            self.summary.set_message(
                f"{len(self.files)} RI images loaded. Run Analysis, or open a folder that already has result.txt.",
                title=label or "Folder loaded",
            )

    def _current_path(self) -> Path | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows:
            idx = rows[0].row()
            if 0 <= idx < len(self.files):
                return self.files[idx]
        return None

    def _on_row_selected(self):
        path = self._current_path()
        if path is not None:
            self._show_file(path)

    def _step_image(self, delta: int):
        if not self.files:
            return
        row = 0
        current = self._current_path()
        if current is not None:
            row = self.files.index(current)
        self.table.selectRow((row + delta) % len(self.files))

    def _modality_for_path(self, path: Path) -> str:
        item = next((i for i in self.items if Path(i.DCM).resolve() == path.resolve()), None)
        if item is not None:
            return "MV" if item.MV else "kV"
        kind = classify_rt_image(path)
        return kind if kind in ("MV", "kV") else "MV"

    def _wl_key(self, kind: str, modality: str) -> str:
        machine = machine_name(self.folder) or "unknown"
        return f"{kind}_{machine}_{modality}"

    def _saved_window_level(self, modality: str, vmin: float, vmax: float) -> tuple[float, float]:
        window = self.settings.value(self._wl_key("window", modality), None)
        level = self.settings.value(self._wl_key("level", modality), None)
        if window is None or level is None:
            return max(vmax - vmin, 1.0), (vmin + vmax) / 2.0
        try:
            return max(float(window), 1.0), float(level)
        except (TypeError, ValueError):
            return max(vmax - vmin, 1.0), (vmin + vmax) / 2.0

    def _store_window_level(self):
        if not self._current_modality:
            return
        self.settings.setValue(self._wl_key("window", self._current_modality), int(self.window_slider.value()))
        self.settings.setValue(self._wl_key("level", self._current_modality), int(self.level_slider.value()))

    def _view_style_changed(self):
        self.settings.setValue("view_style", self.view_style.currentData() or "log")
        path = self._current_path()
        if path is not None:
            self._show_file(path)

    def _show_file(self, path: Path):
        try:
            style = self.view_style.currentData() or "log"
            image = load_ri_for_display(path, style)
            arr, origin, spacing = sitk_to_array(image)
            vmin, vmax = float(arr.min()), float(arr.max())
            modality = self._modality_for_path(path)
            self._current_modality = modality
            window, level = self._saved_window_level(modality, vmin, vmax)
            self.window_slider.blockSignals(True)
            self.level_slider.blockSignals(True)
            self.window_slider.setMaximum(max(int(max(vmax - vmin, window) * 2), 10))
            self.window_slider.setValue(int(window))
            self.level_slider.setMinimum(int(vmin - max(window, vmax - vmin)))
            self.level_slider.setMaximum(int(vmax + max(window, vmax - vmin)))
            self.level_slider.setValue(int(level))
            self.window_slider.blockSignals(False)
            self.level_slider.blockSignals(False)
            self.viewer.set_image(arr, origin, spacing, window, level)
            item = next((i for i in self.items if Path(i.DCM).resolve() == path.resolve()), None)
            markers = []
            if item and item.sid_mm:
                scale = 1000.0 / item.sid_mm
                fc = [item.field_center[0] / scale, item.field_center[1] / scale]
                bb = [item.bb_center[0] / scale, item.bb_center[1] / scale]
                markers.append((fc[0], fc[1], QColor(255, 0, 0)))
                markers.append((bb[0], bb[1], QColor(0, 220, 0)))
            self.viewer.set_markers(markers)
            self.statusBar().showMessage(path.name)
        except Exception as exc:
            logger.exception("failed to load %s", path)
            self.statusBar().showMessage(f"Could not load {path.name}: {exc}")

    def _wl_slider_changed(self):
        self.viewer.set_window_level(self.window_slider.value(), self.level_slider.value())
        self._store_window_level()
        self.statusBar().showMessage(
            f"Window: {self.window_slider.value()}, Level: {self.level_slider.value()}"
        )

    def _on_viewer_wl(self, window: float, level: float):
        self.window_slider.blockSignals(True)
        self.level_slider.blockSignals(True)
        self.window_slider.setValue(int(window))
        self.level_slider.setValue(int(level))
        self.window_slider.blockSignals(False)
        self.level_slider.blockSignals(False)
        self._store_window_level()

    def _show_help(self):
        QMessageBox.information(
            self,
            "Winston-Lutz viewer",
            "Open Folder: choose a directory that contains RI.*.dcm files.\n"
            "Run Analysis: detect field and BB centers (writes {file}_out).\n"
            "View Report: open report.html in the browser.\n\n"
            "Pan: drag with the left mouse button.\n"
            "Zoom: mouse wheel, or Zoom In/Out.\n"
            "Window/Level: sliders, or Shift+drag / right-drag on the image.\n\n"
            "View:\n"
            "  Report crop = 50 mm center crop, min-max to 8-bit (result.png).\n"
            "  Report LoG = same crop, then Laplacian-of-Gaussian (C++ result.png).\n"
            "  Full image = entire DICOM, min-max to 8-bit.\n\n"
            "Red cross = radiation field center.\n"
            "Green cross = BB center.",
        )


def run_app(argv: list[str] | None = None, folder: str | Path | None = None) -> int:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = list(argv if argv is not None else sys.argv)
    app = QApplication.instance() or QApplication(args)
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MachineQA.WinstonLutz")
    except Exception:
        pass
    font = app.font()
    font.setPointSize(max(font.pointSize() + 3, 13))
    app.setFont(font)
    icon = app_icon()
    app.setWindowIcon(icon)
    win = MainWindow()
    win.setWindowIcon(icon)
    if folder:
        win.load_folder(folder)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(run_app())
