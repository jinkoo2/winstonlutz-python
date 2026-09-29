"""Winston-Lutz desktop app: pick a folder of RI images, analyze, and review."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QPoint, QPointF, QSettings, QSize, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QLabel,
    QListView,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
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

from .analysis import (
    AnalysisParams,
    RiSetup,
    analysis_params_from_machine,
    angle_sort_key,
    classify_rt_image,
    display_angle,
    gtc_beam_name,
    load_ri_for_display,
    read_ri_setup,
)
from .app_settings import (
    MACHINES_KEY,
    find_machine_for_folder,
    get_institution,
    get_machines,
    is_simple_run_mode,
    load_gui_settings,
    save_gui_settings,
    simple_machine_name,
)
from .identity import (
    USER_ID_NONE,
    USER_ID_OIDC,
    clear_current_user,
    current_user_email,
    current_user_label,
    current_user_profile,
    get_user_id_method,
    session_operator,
    user_needs_email,
)
from .image_viewer import ImageViewer, sitk_to_array
from .logutil import configure_logging
from .models import WinstonLutzItem
from .pipeline import (
    analyze_folder,
    case_has_html_report,
    case_open_status,
    find_html_report,
    infer_folder_bb_methods,
    list_case_candidates,
    list_ri_files,
    load_existing_results,
    write_html_reports,
)
from .rtplan import (
    PlanBeam,
    beam_expects_image,
    find_machine_rtplan,
    list_plan_beams,
    machine_all_ri_required,
    machine_ignore_beams,
    match_ri_files_to_beams,
    missing_required_beams,
)
from .settings_dialog import SettingsDialog

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
    return simple_machine_name(folder)


def window_title(case: str = "") -> str:
    parts = ["Winston-Lutz"]
    institution = get_institution()
    if institution:
        parts.append(institution)
    user = current_user_label()
    if user:
        parts.append(user)
    if case:
        parts.append(case)
    return " — ".join(parts)


def _restore_layout(widget, settings: QSettings, key: str, splitter: QSplitter | None = None) -> None:
    geom = settings.value(f"{key}/geometry")
    if geom is not None:
        widget.restoreGeometry(geom)
    if splitter is not None:
        state = settings.value(f"{key}/splitter")
        if state is not None:
            splitter.restoreState(state)


def _save_layout(widget, settings: QSettings, key: str, splitter: QSplitter | None = None) -> None:
    settings.setValue(f"{key}/geometry", widget.saveGeometry())
    if splitter is not None:
        settings.setValue(f"{key}/splitter", splitter.saveState())
    settings.sync()


_STATUS_COLORS = {
    "new": QColor("#64748b"),
    "pass": QColor("#15803d"),
    "fail": QColor("#b91c1c"),
}


class _SortItem(QTableWidgetItem):
    """QTableWidget cell that sorts by a numeric/tuple key instead of display text."""

    def __init__(self, text: str, key, path: Path | None = None):
        super().__init__(text)
        self._key = key
        if path is not None:
            self.setData(Qt.UserRole, str(path))

    def __lt__(self, other):
        if isinstance(other, _SortItem):
            return self._key < other._key
        return super().__lt__(other)


def _type_rank(kind: str) -> int:
    if kind == "MV":
        return 0
    if kind == "kV":
        return 1
    return 2


class ScanCasesWorker(QThread):
    """List DATA_FOLDER case directories off the UI thread."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, machine: dict, parent=None):
        super().__init__(parent)
        self._machine = dict(machine)

    def run(self):
        try:
            self.finished_ok.emit(list_case_candidates(self._machine))
        except Exception as exc:
            logger.exception("case scan failed")
            self.failed.emit(str(exc))


class CaseListModel(QAbstractListModel):
    """Open Case list: 'all' is virtual; other filters load matching rows in batches.

    Status comes from report.html, not a per-folder RI / result.txt scan.
    """

    PAGE = 50
    MAX_SCAN = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidates: list[Path] = []
        self._cursor = 0
        self._rows: list[tuple[Path, str]] = []
        self._filter = "all"
        self._status_cache: dict[Path, str] = {}
        self._busy = False
        self._virtual = True

    def set_source(self, candidates: list[Path], _tol_mm: float, status_filter: str) -> None:
        self.beginResetModel()
        self._candidates = candidates
        self._cursor = 0
        self._rows = []
        self._filter = (status_filter or "all").strip().lower() or "all"
        self._status_cache = {}
        self._busy = False
        self._virtual = self._filter == "all"
        self.endResetModel()
        if not self._virtual:
            self.fetchMore(QModelIndex())

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        if self._virtual:
            return len(self._candidates)
        return len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        folder = self._folder_at(index.row()) if index.isValid() else None
        if folder is None:
            return None
        status = self._status_at(index.row(), compute=not self._virtual)
        if role == Qt.DisplayRole:
            if status:
                return f"{folder.name}    {status}"
            return folder.name
        if role == Qt.ForegroundRole:
            return QBrush(_STATUS_COLORS.get(status or "new", QColor("#334155")))
        if role == Qt.FontRole:
            font = QFont()
            font.setBold(bool(status) and status != "new")
            return font
        if role == Qt.UserRole:
            return str(folder)
        return None

    def case_at(self, row: int) -> Path | None:
        return self._folder_at(row)

    def row_for_name(self, name: str) -> int:
        if not name:
            return -1
        if self._virtual:
            for i, folder in enumerate(self._candidates):
                if folder.name == name:
                    return i
            return -1
        for i, (folder, _status) in enumerate(self._rows):
            if folder.name == name:
                return i
        return -1

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        if parent.isValid() or self._virtual:
            return False
        return self._cursor < len(self._candidates)

    def fetchMore(self, parent=QModelIndex()) -> None:
        if parent.isValid() or self._virtual or self._busy or not self.canFetchMore():
            return
        self._busy = True
        added: list[tuple[Path, str]] = []
        scanned = 0
        try:
            while self._cursor < len(self._candidates) and len(added) < self.PAGE and scanned < self.MAX_SCAN:
                folder = self._candidates[self._cursor]
                self._cursor += 1
                scanned += 1
                row = self._inspect(folder)
                if row is not None:
                    added.append(row)
        finally:
            self._busy = False
        if added:
            start = len(self._rows)
            self.beginInsertRows(QModelIndex(), start, start + len(added) - 1)
            self._rows.extend(added)
            self.endInsertRows()
        elif self.canFetchMore():
            QTimer.singleShot(0, lambda: self.fetchMore(QModelIndex()))

    def ensure_status_range(self, start: int, end: int) -> None:
        """Fill pass/fail for a visible slice without blocking the whole list."""
        if not self._virtual or not self._candidates:
            return
        start = max(0, start)
        end = min(len(self._candidates), max(start, end))
        if start >= end:
            return
        changed = False
        for i in range(start, end):
            folder = self._candidates[i]
            if folder in self._status_cache:
                continue
            self._status_cache[folder] = case_open_status(folder)
            changed = True
        if changed:
            self.dataChanged.emit(self.index(start, 0), self.index(end - 1, 0))

    def _folder_at(self, row: int) -> Path | None:
        if self._virtual:
            if 0 <= row < len(self._candidates):
                return self._candidates[row]
            return None
        if 0 <= row < len(self._rows):
            return self._rows[row][0]
        return None

    def _status_at(self, row: int, compute: bool = True) -> str:
        if not self._virtual:
            return self._rows[row][1]
        folder = self._folder_at(row)
        if folder is None:
            return "new"
        cached = self._status_cache.get(folder)
        if cached is not None or not compute:
            return cached or ""
        status = case_open_status(folder)
        self._status_cache[folder] = status
        return status

    def _inspect(self, folder: Path) -> tuple[Path, str] | None:
        wanted = self._filter
        if wanted == "new":
            if case_has_html_report(folder):
                return None
            return folder, "new"
        status = case_open_status(folder)
        if wanted in ("pass", "fail") and status != wanted:
            return None
        return folder, status


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
        self.hint = QLabel("Open Case: choose a folder that contains RI images.")
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
MISSING_FG = QColor(180, 83, 9)
MISSING_BG = QColor(255, 237, 213)
RUN_FG = QColor(71, 85, 105)
RUN_BG = QColor(241, 245, 249)
NA_FG = QColor(100, 116, 139)
NA_BG = QColor(241, 245, 249)

PLAN_TABLE_HEADERS = [
    "Beam",
    "Name",
    "Type",
    "Gantry",
    "Table",
    "Coll",
    "BB − FC (mm)",
    "d (mm)",
    "Result",
]
FILE_TABLE_HEADERS = [
    "#",
    "Name",
    "Type",
    "Gantry",
    "Table",
    "Coll",
    "BB − FC (mm)",
    "d (mm)",
    "Result",
]


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
    elif name == "settings":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(71, 85, 105))
        cx = cy = s / 2.0
        for i in range(6):
            p.save()
            p.translate(cx, cy)
            p.rotate(i * 60)
            p.drawRoundedRect(-2, int(-s * 0.46), 4, int(s * 0.28), 1, 1)
            p.restore()
        p.drawEllipse(QPointF(cx, cy), s * 0.28, s * 0.28)
        p.setBrush(QColor(243, 244, 246))
        p.drawEllipse(QPointF(cx, cy), s * 0.12, s * 0.12)
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


class OpenCaseDialog(QDialog):
    """Pick a machine, then a case; RI files for the selected case are listed."""

    def __init__(self, parent=None, last_machine: str = "", last_case: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Open Case")
        self.resize(720, 460)
        self.machines = [m for m in get_machines() if str(m.get("NAME") or "").strip()]
        self.selected_case: Path | None = None
        self._prefer_case = last_case

        self.machine_combo = QComboBox()
        for machine in self.machines:
            self.machine_combo.addItem(str(machine["NAME"]).strip(), machine)

        self.filter_combo = QComboBox()
        for status in ("all", "new", "fail", "pass"):
            self.filter_combo.addItem(status, status)

        self.case_model = CaseListModel(self)
        self.case_view = QListView()
        self.case_view.setModel(self.case_model)
        self.case_view.setUniformItemSizes(True)
        self.case_view.setSelectionMode(QListView.SingleSelection)
        self.file_list = QListWidget()
        self.case_view.selectionModel().currentChanged.connect(self._on_case_changed)
        self.case_view.doubleClicked.connect(self._accept_if_case)
        self.case_model.rowsInserted.connect(self._on_cases_shown)
        self.case_model.modelReset.connect(self._on_cases_shown)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_if_case)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Open")
        self.ok_button.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Machine"))
        top.addWidget(self.machine_combo, 1)

        lists = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Cases"))
        self._scan_status = QLabel("")
        self._scan_status.setStyleSheet("color: #64748b;")
        filter_row.addWidget(self._scan_status, 1)
        filter_row.addWidget(QLabel("Show"))
        filter_row.addWidget(self.filter_combo)
        left_layout.addLayout(filter_row)
        left_layout.addWidget(self.case_view, 1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("RI files"))
        right_layout.addWidget(self.file_list, 1)
        lists.addWidget(left)
        lists.addWidget(right)
        lists.setChildrenCollapsible(False)
        lists.setStretchFactor(0, 1)
        lists.setStretchFactor(1, 1)
        self.splitter = lists

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(lists, 1)
        layout.addWidget(buttons)

        self._settings = getattr(parent, "settings", None) if parent is not None else None
        if self._settings is None:
            self._settings = QSettings("MachineQA", "WinstonLutz")
        self._splitter_restored = False
        self._scan_worker: ScanCasesWorker | None = None
        self._scan_gen = 0
        self._pending_tol = 1.0
        self._pending_filter = "all"
        _restore_layout(self, self._settings, "open_case")

        last_filter = str(self._settings.value("open_case/filter", "all") or "all")
        filter_idx = self.filter_combo.findData(last_filter)
        self.filter_combo.setCurrentIndex(filter_idx if filter_idx >= 0 else 0)

        if last_machine:
            idx = self.machine_combo.findText(last_machine)
            if idx >= 0:
                self.machine_combo.setCurrentIndex(idx)
        self.case_view.verticalScrollBar().valueChanged.connect(lambda *_: self._fill_visible_status())
        self._reload_cases(ask_folder=True)
        self.machine_combo.currentIndexChanged.connect(lambda *_: self._reload_cases(ask_folder=True))
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._splitter_restored:
            self._splitter_restored = True
            _restore_layout(self, self._settings, "open_case", self.splitter)

    def done(self, result):
        self._scan_gen += 1
        self._disconnect_scan_worker()
        _save_layout(self, self._settings, "open_case", self.splitter)
        super().done(result)

    def _data_folder_path(self, machine: dict) -> Path | None:
        text = str(machine.get("DATA_FOLDER") or "").strip()
        if not text:
            return None
        path = Path(text).expanduser()
        return path if path.is_dir() else None

    def _persist_data_folder(self, machine: dict, folder: str) -> None:
        machine["DATA_FOLDER"] = folder
        data = load_gui_settings()
        name = str(machine.get("NAME") or "").strip()
        machines = [m for m in (data.get(MACHINES_KEY) or []) if isinstance(m, dict)]
        for entry in machines:
            if str(entry.get("NAME") or "").strip() == name:
                entry["DATA_FOLDER"] = folder
                break
        data[MACHINES_KEY] = machines
        save_gui_settings(data)

    def _ensure_data_folder(self, machine: dict) -> bool:
        if self._data_folder_path(machine) is not None:
            return True
        name = str(machine.get("NAME") or "this machine").strip()
        current = str(machine.get("DATA_FOLDER") or "").strip() or "(not set)"
        QMessageBox.warning(
            self,
            "Data folder",
            f"DATA_FOLDER for {name} is missing or does not exist:\n{current}\n\n"
            "Please choose the Data folder for this machine.",
        )
        start = str(machine.get("DATA_FOLDER") or "").strip()
        start_path = Path(start).expanduser() if start else Path()
        start_dir = ""
        if start_path.is_dir():
            start_dir = str(start_path)
        elif start_path.parent.is_dir():
            start_dir = str(start_path.parent)
        chosen = QFileDialog.getExistingDirectory(
            self, f"Select DATA_FOLDER for {name}", start_dir
        )
        if not chosen:
            return False
        self._persist_data_folder(machine, chosen)
        return True

    def _on_filter_changed(self, *_args):
        self._settings.setValue("open_case/filter", str(self.filter_combo.currentData() or "all"))
        self._reload_cases(ask_folder=False)

    def _disconnect_scan_worker(self) -> None:
        worker = self._scan_worker
        self._scan_worker = None
        if worker is None:
            return
        try:
            worker.finished_ok.disconnect()
            worker.failed.disconnect()
        except TypeError:
            pass

    def _reload_cases(self, ask_folder: bool = True):
        self.file_list.clear()
        self.selected_case = None
        self.ok_button.setEnabled(False)
        status_filter = str(self.filter_combo.currentData() or "all")
        machine = self.machine_combo.currentData()
        if not machine:
            self._scan_gen += 1
            self._disconnect_scan_worker()
            self.case_model.set_source([], 1.0, status_filter)
            self._scan_status.setText("")
            return
        if ask_folder and not self._ensure_data_folder(machine):
            self._scan_gen += 1
            self._disconnect_scan_worker()
            self.case_model.set_source([], 1.0, status_filter)
            self._scan_status.setText("")
            return
        tol = 1.0
        if machine.get("WL_pass_tolerance") not in (None, ""):
            tol = float(machine["WL_pass_tolerance"])
        self._scan_gen += 1
        gen = self._scan_gen
        self._pending_tol = tol
        self._pending_filter = status_filter
        self._disconnect_scan_worker()
        self.case_model.set_source([], tol, status_filter)
        self._scan_status.setText("Scanning cases…")
        worker = ScanCasesWorker(machine, self)
        worker.finished_ok.connect(lambda folders, g=gen: self._on_cases_scanned(folders, g))
        worker.failed.connect(lambda msg, g=gen: self._on_scan_failed(msg, g))
        self._scan_worker = worker
        worker.start()

    def _on_cases_scanned(self, folders, gen: int) -> None:
        if gen != self._scan_gen:
            return
        self._scan_worker = None
        n = len(folders) if isinstance(folders, list) else 0
        if self._pending_filter == "all":
            self._scan_status.setText(f"{n} cases")
        else:
            self._scan_status.setText(f"Filtering {n} cases…")
        self.case_model.set_source(folders if isinstance(folders, list) else [], self._pending_tol, self._pending_filter)
        self._refresh_scan_status()
        QTimer.singleShot(0, self._fill_visible_status)

    def _on_scan_failed(self, message: str, gen: int) -> None:
        if gen != self._scan_gen:
            return
        self._scan_worker = None
        self._scan_status.setText("Scan failed")
        logger.warning("Open Case scan failed: %s", message)

    def _on_cases_shown(self, *_args):
        self._refresh_scan_status()
        QTimer.singleShot(0, self._maybe_select_first)

    def _refresh_scan_status(self) -> None:
        shown = self.case_model.rowCount()
        if self._scan_worker is not None:
            return
        if self.case_model.canFetchMore():
            self._scan_status.setText(f"Showing {shown}…")
            return
        self._scan_status.setText(f"{shown} cases")

    def _fill_visible_status(self) -> None:
        if self._pending_filter != "all" or self._scan_worker is not None:
            return
        first = self.case_view.indexAt(QPoint(4, 4))
        start = first.row() if first.isValid() else 0
        viewport = self.case_view.viewport().height() or 400
        hint = max(24, self.case_view.sizeHintForRow(0) if self.case_model.rowCount() else 24)
        self.case_model.ensure_status_range(start, start + max(40, viewport // max(1, hint) + 8))

    def _maybe_select_first(self):
        if self.case_view.currentIndex().isValid():
            return
        if self._select_case_named(self._prefer_case):
            return
        if self.case_model.rowCount():
            self.case_view.setCurrentIndex(self.case_model.index(0, 0))

    def _select_case_named(self, name: str) -> bool:
        row = self.case_model.row_for_name(name)
        if row < 0:
            return False
        self.case_view.setCurrentIndex(self.case_model.index(row, 0))
        return True

    def _on_case_changed(self, current, _previous):
        self.file_list.clear()
        self.selected_case = None
        self.ok_button.setEnabled(False)
        if not current.isValid():
            return
        folder = self.case_model.case_at(current.row())
        if folder is None:
            return
        self.selected_case = folder
        self.ok_button.setEnabled(True)
        for dcm in list_ri_files(folder):
            self.file_list.addItem(dcm.name)

    def _accept_if_case(self, *_args):
        if self.selected_case is not None:
            self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(window_title())
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)

        self.folder: Path | None = None
        self.files: list[Path] = []
        self.items: list[WinstonLutzItem] = []
        self.setups: dict[Path, RiSetup] = {}
        self.analysis_params = AnalysisParams()
        self.plan_beams: list[PlanBeam] = []
        self.beam_ri: dict[int, Path] = {}
        self.unmatched_ri: list[Path] = []
        self.ignore_beams: set[int] = set()
        self.all_ri_required = False
        self._analysis_running = False
        self.worker: AnalyzeWorker | None = None
        self.tol_mm = 1.0
        self._current_modality: str | None = None

        self.settings = QSettings("MachineQA", "WinstonLutz")
        self.viewer = ImageViewer(self)
        self.viewer.status_changed.connect(self.statusBar().showMessage)
        self.viewer.window_level_changed.connect(self._on_viewer_wl)

        self.table = QTableWidget(0, len(FILE_TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(FILE_TABLE_HEADERS)
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
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.sortIndicatorChanged.connect(self._remember_sort)
        self._sort_col = 1
        self._sort_order = Qt.AscendingOrder
        self.table.setSortingEnabled(True)
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
        central_layout.addWidget(self._build_heading())
        central_layout.addWidget(self._build_toolbar())
        central_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")
        _restore_layout(self, self.settings, "main")
        self._apply_run_mode_ui()
        self._refresh_heading()
        QTimer.singleShot(0, self._after_shown)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._split_initialized:
            self._split_initialized = True
            state = self.settings.value("main/splitter")
            if state is not None:
                self.splitter.restoreState(state)
            else:
                half = max(self.splitter.width() // 2, 1)
                self.splitter.setSizes([half, half])

    def closeEvent(self, event):
        _save_layout(self, self.settings, "main", self.splitter)
        super().closeEvent(event)

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

    def _build_heading(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("appHeading")
        bar.setStyleSheet(
            """
            QWidget#appHeading { background: #1f3a5f; }
            QWidget#appHeading QLabel { color: #f8fafc; background: transparent; }
            QWidget#appHeading QPushButton {
                color: #f8fafc;
                background: #34547a;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QWidget#appHeading QPushButton:hover { background: #456894; }
            QWidget#appHeading QPushButton:disabled { color: #94a3b8; background: #2a4a6e; }
            """
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(28, 28))
        title = QLabel("Winston-Lutz")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.heading_institution = QLabel()
        self.heading_institution.setStyleSheet("color: #cbd5e1;")
        self.heading_user = QLabel()
        self.heading_user.setStyleSheet("font-weight: 600;")
        self.user_settings_btn = QPushButton("User settings")
        self.login_btn = QPushButton("Login")
        self.logout_btn = QPushButton("Logout")
        self.user_settings_btn.clicked.connect(lambda: self.open_user_settings())
        self.login_btn.clicked.connect(self._login_user)
        self.logout_btn.clicked.connect(self._logout_user)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(self.heading_institution)
        layout.addStretch(1)
        layout.addWidget(self.heading_user)
        layout.addWidget(self.user_settings_btn)
        layout.addWidget(self.login_btn)
        layout.addWidget(self.logout_btn)
        return bar

    def _refresh_heading(self) -> None:
        institution = get_institution()
        self.heading_institution.setText(institution)
        self.heading_institution.setVisible(bool(institution))
        method = get_user_id_method()
        profile = current_user_profile()
        label = current_user_label()
        email = current_user_email()
        if profile and label:
            extra = f"  <{email}>" if email else ""
            self.heading_user.setText(label + extra)
        elif method == USER_ID_OIDC:
            self.heading_user.setText("Not signed in")
        elif method == USER_ID_NONE:
            self.heading_user.setText("No user")
        else:
            self.heading_user.setText("")
        signed_in = profile is not None
        self.user_settings_btn.setEnabled(signed_in)
        self.login_btn.setVisible(method == USER_ID_OIDC and not signed_in)
        self.logout_btn.setVisible(method == USER_ID_OIDC and signed_in)

    def _scan_new_cases(self) -> None:
        from .case_notify import scan_and_notify_new_cases_async

        scan_and_notify_new_cases_async()

    def _after_shown(self) -> None:
        self._scan_new_cases()
        if user_needs_email():
            QMessageBox.information(
                self,
                "User settings",
                "This login has no email address. Enter one in User settings "
                "to receive QA case notifications.",
            )
            self.open_user_settings(prompt_email=True)

    def open_user_settings(self, prompt_email: bool = False):
        from .user_settings_dialog import UserSettingsDialog

        if current_user_profile() is None:
            QMessageBox.information(
                self,
                "User settings",
                "Sign in first, or set Identity to OSUser in Settings.",
            )
            return
        dlg = UserSettingsDialog(self, prompt_email=prompt_email or user_needs_email())
        dlg.exec_()
        self._refresh_heading()
        self._apply_run_mode_ui()

    def _login_user(self):
        from .oidc_dialog import OidcLoginDialog

        dlg = OidcLoginDialog(self, can_quit=False)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._refresh_heading()
        self._apply_run_mode_ui()
        if user_needs_email():
            self.open_user_settings(prompt_email=True)

    def _logout_user(self):
        if (
            QMessageBox.question(
                self,
                "Logout",
                "Sign out of this Winston-Lutz session?",
            )
            != QMessageBox.Yes
        ):
            return
        clear_current_user()
        self._refresh_heading()
        self._apply_run_mode_ui()

    def _build_toolbar(self) -> QWidget:
        open_act = self._make_action("Open Case", "folder", self.open_case, "Ctrl+O")
        self.run_act = self._make_action("Run Analysis", "run", self.run_analysis, "Ctrl+R")
        self.run_act.setEnabled(False)
        self.report_act = self._make_action("View Report", "report", self.view_report)
        self.report_act.setEnabled(False)
        settings_act = self._make_action("Settings", "settings", self.open_settings, "Ctrl+,")
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
                self._tool_button(settings_act),
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

    def _apply_run_mode_ui(self) -> None:
        self.setWindowTitle(
            window_title(case_display_name(self.folder) if self.folder else "")
        )
        self._update_report_button()
        if self.folder:
            return
        if is_simple_run_mode():
            self.summary.hint.setText(
                "Simple mode: Open Case picks a folder that contains RI.*.dcm files. "
                "The parent folder is the machine name."
            )
        else:
            self.summary.hint.setText(
                "Open Case: choose a machine and a case with RI images."
            )

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec_()
        if not dlg.did_save:
            return
        self._apply_run_mode_ui()
        self._refresh_heading()
        if self.folder:
            self.load_folder(self.folder)
        else:
            self.setWindowTitle(window_title())

    def open_case(self):
        self._scan_new_cases()
        if is_simple_run_mode():
            self._open_case_simple()
            return
        machines = [m for m in get_machines() if str(m.get("NAME") or "").strip()]
        if not machines:
            QMessageBox.warning(
                self,
                "Open Case",
                "No machines are defined. Open Settings and add at least one machine, "
                "or set RunMode to Simple.",
            )
            return
        last_machine = str(self.settings.value("last_machine", "") or "")
        last_case = Path(str(self.settings.value("last_directory", "") or "")).name
        dlg = OpenCaseDialog(self, last_machine=last_machine, last_case=last_case)
        if dlg.exec_() != QDialog.Accepted or dlg.selected_case is None:
            return
        self.settings.setValue("last_machine", dlg.machine_combo.currentText())
        self.load_folder(dlg.selected_case)

    def _open_case_simple(self):
        last = str(self.settings.value("last_directory", "") or "")
        start_path = Path(last).expanduser() if last else Path()
        start = ""
        if start_path.is_dir():
            parent = start_path.parent
            start = str(parent if parent.is_dir() else start_path)
        elif start_path.parent.is_dir():
            start = str(start_path.parent)
        chosen = QFileDialog.getExistingDirectory(
            self, "Select case folder (RI images)", start
        )
        if not chosen:
            return
        folder = Path(chosen)
        if not list_ri_files(folder):
            QMessageBox.warning(
                self,
                "Open Case",
                f"No RI.*.dcm files in:\n{folder}",
            )
            return
        machine = simple_machine_name(folder)
        if machine:
            self.settings.setValue("last_machine", machine)
        self.load_folder(folder)

    def load_folder(self, folder: str | Path) -> bool:
        folder = Path(folder)
        files = list_ri_files(folder)
        machine_cfg = None if is_simple_run_mode() else find_machine_for_folder(folder)
        self.analysis_params = analysis_params_from_machine(machine_cfg)
        self.all_ri_required = machine_all_ri_required(machine_cfg)
        self.ignore_beams = machine_ignore_beams(machine_cfg)
        self.plan_beams = []
        self.beam_ri = {}
        self.unmatched_ri = []
        self._analysis_running = False
        explicit_plan = str(
            (machine_cfg or {}).get("DICOM_PLAN_FILE")
            or (machine_cfg or {}).get("RTPLAN_FILE_PATH")
            or ""
        ).strip()
        plan_path = find_machine_rtplan(machine_cfg) if explicit_plan else None
        if plan_path is not None:
            try:
                self.plan_beams = list_plan_beams(plan_path)
            except Exception:
                logger.exception("could not read RT Plan %s", plan_path)
                self.plan_beams = []
        if self.plan_beams:
            self.beam_ri, self.unmatched_ri = match_ri_files_to_beams(files, self.plan_beams)
        if not files and not self.plan_beams:
            QMessageBox.warning(self, "No RI files", f"No RI.*.dcm files in:\n{folder}")
            return False
        self.settings.setValue("last_directory", str(folder))
        self.folder = folder
        self.files = files
        self.items = load_existing_results(folder)
        have_result = {Path(i.DCM).resolve() for i in self.items}
        self.setups = {}
        for path in files:
            if path.resolve() in have_result:
                continue
            try:
                self.setups[path.resolve()] = read_ri_setup(path, self.analysis_params)
            except Exception:
                logger.exception("could not read Type/Gantry/Table/Coll from %s", path.name)
        self.run_act.setEnabled(True)
        self.setWindowTitle(window_title(case_display_name(folder)))
        self._apply_bb_methods(*infer_folder_bb_methods(folder, self.items))
        if machine_cfg and machine_cfg.get("WL_pass_tolerance") not in (None, ""):
            self.tol_spin.setValue(float(machine_cfg["WL_pass_tolerance"]))
        self._configure_table(bool(self.plan_beams))
        self._sort_col = 0 if self.plan_beams else 2
        self._sort_order = Qt.AscendingOrder
        self._fill_table()
        self._update_report_button()
        extra = f"  ({len(self.items)} existing result.txt)" if self.items else ""
        if self.plan_beams:
            n_miss = len(
                missing_required_beams(self.plan_beams, self.beam_ri, self.ignore_beams)
            )
            self.statusBar().showMessage(
                f"Loaded {len(self.beam_ri)}/{len(self.plan_beams)} plan beams, {len(files)} RI images{extra}"
                + (f", {n_miss} missing" if n_miss else "")
            )
        else:
            self.statusBar().showMessage(f"Loaded {len(files)} RI images{extra}")
        return True

    def _configure_table(self, plan_mode: bool) -> None:
        headers = PLAN_TABLE_HEADERS if plan_mode else FILE_TABLE_HEADERS
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        for i in range(len(headers)):
            header.setSectionResizeMode(i, QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

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
        if self.folder is None or is_simple_run_mode():
            return
        path = find_html_report(self.folder)
        if path is None and self.items:
            path = write_html_reports(
                self.folder,
                self.items,
                self.tol_mm,
                case_passed=self._case_passed(
                    sum(
                        1
                        for i in self.items
                        if i.calc_norm_of_bb_offset_from_field_center() > self.tol_mm
                    )
                ),
            )
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
        if is_simple_run_mode():
            self.report_act.setEnabled(False)
            return
        has_report = self.folder is not None and find_html_report(self.folder) is not None
        can_build = bool(self.folder and self.items)
        self.report_act.setEnabled(has_report or can_build)

    def run_analysis(self):
        if not self.folder:
            return
        self.run_act.setEnabled(False)
        self._analysis_running = True
        self.progress.show()
        self._fill_table(reload_image=False)
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

    def _case_passed(self, n_fail: int) -> bool:
        if n_fail:
            return False
        if self.all_ri_required and missing_required_beams(
            self.plan_beams, self.beam_ri, self.ignore_beams
        ):
            return False
        return True

    def _analysis_done(self, items: list):
        self._analysis_running = False
        self.progress.hide()
        self.run_act.setEnabled(True)
        self.items = items
        if self.folder and items and not is_simple_run_mode():
            n_fail = sum(
                1
                for i in items
                if i.calc_norm_of_bb_offset_from_field_center() > self.tol_mm
            )
            write_html_reports(
                self.folder,
                items,
                self.tol_mm,
                case_passed=self._case_passed(n_fail),
            )
        self._fill_table()
        self._update_report_button()
        self.statusBar().showMessage(f"Analysis finished: {len(items)} images")

    def _analysis_failed(self, message: str):
        self._analysis_running = False
        self.progress.hide()
        self.run_act.setEnabled(True)
        self._fill_table(reload_image=False)
        QMessageBox.critical(self, "Analysis failed", message)

    def _tol_changed(self, value: float):
        self.tol_mm = value
        self._fill_table(reload_image=False)

    def _remember_sort(self, column: int, order):
        self._sort_col = column
        self._sort_order = order

    def _fill_table(self, reload_image: bool = True):
        current = self._current_path()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        if self.plan_beams:
            max_d, n_fail = self._fill_plan_table()
        else:
            max_d, n_fail = self._fill_file_table()
        self.table.setSortingEnabled(True)
        if 0 <= self._sort_col < self.table.columnCount():
            self.table.sortItems(self._sort_col, self._sort_order)
        self._select_row_for_path(current)
        self.table.blockSignals(False)
        if reload_image:
            self._on_row_selected()
        self._update_summary(max_d, n_fail)

    def _select_row_for_path(self, current: Path | None) -> None:
        select = 0
        if current is not None:
            current_res = current.resolve()
            for i in range(self.table.rowCount()):
                stored = self.table.item(i, 0)
                if stored and Path(str(stored.data(Qt.UserRole) or "")).resolve() == current_res:
                    select = i
                    break
        if self.table.rowCount():
            self.table.selectRow(select)

    def _paint_result_cell(self, cell: QTableWidgetItem, kind: str) -> None:
        cell.setFont(QFont(self.table.font().family(), self.table.font().pointSize(), QFont.Bold))
        if kind == "pass":
            cell.setForeground(PASS_FG)
            cell.setBackground(PASS_BG)
        elif kind == "fail":
            cell.setForeground(FAIL_FG)
            cell.setBackground(FAIL_BG)
        elif kind == "missing":
            cell.setForeground(MISSING_FG)
            cell.setBackground(MISSING_BG)
        elif kind == "running":
            cell.setForeground(RUN_FG)
            cell.setBackground(RUN_BG)
        elif kind == "na":
            cell.setForeground(NA_FG)
            cell.setBackground(NA_BG)

    def _fill_plan_table(self) -> tuple[float, int]:
        by_path = {Path(i.DCM).resolve(): i for i in self.items}
        self.table.setRowCount(len(self.plan_beams))
        max_d = 0.0
        n_fail = 0
        p = self.analysis_params
        for row, beam in enumerate(self.plan_beams):
            path = self.beam_ri.get(beam.number)
            item = by_path.get(path.resolve()) if path is not None else None
            d_val: float | None = None
            off_text = ""
            result_text = ""
            result_kind = ""
            passed = None
            if path is None:
                if beam.number in self.ignore_beams:
                    result_text = "NA"
                    result_kind = "na"
                else:
                    result_text = "Missing"
                    result_kind = "missing"
                    if self.all_ri_required and beam_expects_image(
                        beam, self.ignore_beams
                    ):
                        n_fail += 1
            elif self._analysis_running and item is None:
                result_text = "Running"
                result_kind = "running"
            elif item is not None:
                d_val = item.calc_norm_of_bb_offset_from_field_center()
                max_d = max(max_d, d_val)
                passed = d_val <= self.tol_mm
                if not passed:
                    n_fail += 1
                off = item.bb_offset_from_field_center
                off_text = f"{off[0]:.2f}, {off[1]:.2f}"
                result_text = "Pass" if passed else "Fail"
                result_kind = "pass" if passed else "fail"
            values = [
                str(beam.number),
                beam.name,
                beam.kind,
                f"{beam.gantry:.0f}",
                f"{beam.table:.0f}",
                f"{beam.collimator:.0f}",
                off_text,
                "" if d_val is None else f"{d_val:.2f}",
                result_text,
            ]
            type_rank = _type_rank(beam.kind)
            has_d = 0 if d_val is not None else 1
            d_sort = d_val if d_val is not None else 0.0
            result_rank = {"pass": 0, "fail": 1, "missing": 2, "na": 3, "running": 4}.get(
                result_kind, 5
            )
            g_sort = angle_sort_key(beam.gantry, p.nominal_gantry_angles)
            t_sort = angle_sort_key(beam.table, p.nominal_table_angles)
            c_sort = angle_sort_key(beam.collimator, p.nominal_collimator_angles)
            keys = [
                (beam.number,),
                (beam.name.lower(), beam.number),
                (type_rank, g_sort, t_sort, c_sort),
                (g_sort, type_rank, t_sort, c_sort),
                (t_sort, type_rank, g_sort, c_sort),
                (c_sort, type_rank, g_sort, t_sort),
                (has_d, d_sort),
                (has_d, d_sort),
                (result_rank, d_sort),
            ]
            if path is not None:
                tip = path.name
            elif beam.number in self.ignore_beams:
                tip = f"Ignored beam {beam.number} ({beam.name})"
            else:
                tip = f"No RI for beam {beam.number} ({beam.name})"
            for col, text in enumerate(values):
                cell = _SortItem(text, keys[col], path if col == 0 else None)
                cell.setToolTip(tip)
                cell.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                if col == 7 and result_kind in ("pass", "fail"):
                    self._paint_result_cell(cell, result_kind)
                if col == 8 and result_kind:
                    self._paint_result_cell(cell, result_kind)
                self.table.setItem(row, col, cell)
        return max_d, n_fail

    def _fill_file_table(self) -> tuple[float, int]:
        by_path = {Path(i.DCM).resolve(): i for i in self.items}
        self.table.setRowCount(len(self.files))
        max_d = 0.0
        n_fail = 0
        p = self.analysis_params
        for row, path in enumerate(self.files):
            item = by_path.get(path.resolve())
            setup = self.setups.get(path.resolve())
            values = [str(row + 1), "", "", "", "", "", "", "", ""]
            passed = None
            kind = ""
            gantry = table = coll = 0.0
            d_val: float | None = None
            result_kind = ""
            name = ""
            if item:
                d_val = item.calc_norm_of_bb_offset_from_field_center()
                max_d = max(max_d, d_val)
                passed = d_val <= self.tol_mm
                if not passed:
                    n_fail += 1
                off = item.bb_offset_from_field_center
                kind = "MV" if item.MV else "kV"
                gantry, table, coll = item.gantry, item.table, item.collimator
                name = gtc_beam_name(gantry, table, coll, p)
                values = [
                    str(row + 1),
                    name,
                    kind,
                    display_angle(gantry, p.nominal_gantry_angles),
                    display_angle(table, p.nominal_table_angles),
                    display_angle(coll, p.nominal_collimator_angles),
                    f"{off[0]:.2f}, {off[1]:.2f}",
                    f"{d_val:.2f}",
                    "Pass" if passed else "Fail",
                ]
                result_kind = "pass" if passed else "fail"
            elif self._analysis_running:
                if setup:
                    kind = setup.kind if setup.kind in ("MV", "kV") else ""
                    gantry, table, coll = setup.gantry, setup.table, setup.collimator
                    name = gtc_beam_name(gantry, table, coll, p)
                    values = [
                        str(row + 1),
                        name,
                        kind,
                        display_angle(gantry, p.nominal_gantry_angles),
                        display_angle(table, p.nominal_table_angles),
                        display_angle(coll, p.nominal_collimator_angles),
                        "",
                        "",
                        "Running",
                    ]
                else:
                    values = [str(row + 1), "", "", "", "", "", "", "", "Running"]
                result_kind = "running"
            elif setup:
                kind = setup.kind if setup.kind in ("MV", "kV") else ""
                gantry, table, coll = setup.gantry, setup.table, setup.collimator
                name = gtc_beam_name(gantry, table, coll, p)
                values = [
                    str(row + 1),
                    name,
                    kind,
                    display_angle(gantry, p.nominal_gantry_angles),
                    display_angle(table, p.nominal_table_angles),
                    display_angle(coll, p.nominal_collimator_angles),
                    "",
                    "",
                    "",
                ]
            type_rank = _type_rank(kind)
            has_d = 0 if d_val is not None else 1
            d_sort = d_val if d_val is not None else 0.0
            result_rank = 0 if passed is True else 1 if passed is False else 2
            g_sort = angle_sort_key(gantry, p.nominal_gantry_angles)
            t_sort = angle_sort_key(table, p.nominal_table_angles)
            c_sort = angle_sort_key(coll, p.nominal_collimator_angles)
            keys = [
                (row,),
                (name.lower(), g_sort, t_sort, c_sort),
                (type_rank, g_sort, t_sort, c_sort),
                (g_sort, type_rank, t_sort, c_sort),
                (t_sort, type_rank, g_sort, c_sort),
                (c_sort, type_rank, g_sort, t_sort),
                (has_d, d_sort),
                (has_d, d_sort),
                (result_rank, d_sort),
            ]
            for col, text in enumerate(values):
                cell = _SortItem(text, keys[col], path if col == 0 else None)
                cell.setToolTip(path.name)
                cell.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                if result_kind and col in (7, 8):
                    self._paint_result_cell(cell, result_kind)
                self.table.setItem(row, col, cell)
        return max_d, n_fail

    def _update_summary(self, max_d: float, n_fail: int) -> None:
        label = case_display_name(self.folder)
        missing = [
            b
            for b in self.plan_beams
            if b.number not in self.beam_ri and b.number not in self.ignore_beams
        ]
        required_missing = missing_required_beams(
            self.plan_beams, self.beam_ri, self.ignore_beams
        )
        n_show = len(self.items) if self.items else len(self.beam_ri) if self.plan_beams else len(self.files)
        if self.items or (self.all_ri_required and required_missing):
            operator = session_operator(self.items[0].user if self.items else "")
            self.summary.set_results(
                case=label,
                n_images=n_show,
                max_d=max_d,
                tol=self.tol_mm,
                passed=self._case_passed(n_fail),
                operator=operator,
            )
            if missing:
                extra = ", ".join(f"{b.number} {b.name}" for b in missing)
                self.summary.hint.setText(
                    f"Missing RI for beam(s): {extra}."
                    + (
                        " ALL_RI_IMAGE_REQUIRED: case fails."
                        if self.all_ri_required and required_missing
                        else " Not required for pass."
                    )
                )
                self.summary.hint.show()
        elif self.files or self.plan_beams:
            msg = f"{len(self.files)} RI images loaded. Run Analysis, or open a folder that already has result.txt."
            if self.plan_beams:
                msg = (
                    f"{len(self.beam_ri)} of {len(self.plan_beams)} plan beams have RI images. "
                    "Run Analysis, or open a case that already has result.txt."
                )
                if missing:
                    msg += " Missing: " + ", ".join(f"{b.number} {b.name}" for b in missing) + "."
            self.summary.set_message(msg, title=label or "Folder loaded")

    def _current_path(self) -> Path | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        if item is not None:
            stored = item.data(Qt.UserRole)
            if stored:
                return Path(str(stored))
        if self.plan_beams:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self.files):
            return self.files[idx]
        return None

    def _on_row_selected(self):
        path = self._current_path()
        if path is not None:
            self._show_file(path)
            return
        self.viewer.clear()
        self.statusBar().showMessage("No RI image for this beam")

    def _step_image(self, delta: int):
        n = self.table.rowCount()
        if n <= 0:
            return
        row = 0
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows:
            row = rows[0].row()
        self.table.selectRow((row + delta) % n)

    def _modality_for_path(self, path: Path) -> str:
        item = next((i for i in self.items if Path(i.DCM).resolve() == path.resolve()), None)
        if item is not None:
            return "MV" if item.MV else "kV"
        setup = self.setups.get(path.resolve())
        if setup is not None and setup.kind in ("MV", "kV"):
            return setup.kind
        kind = classify_rt_image(path, self.analysis_params)
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
            image = load_ri_for_display(path, style, params=self.analysis_params)
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
                scale = self.analysis_params.sad_mm / item.sid_mm
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
            "Open Case: in Clinic mode, pick a machine and case. In Simple mode, pick the folder that contains RI.*.dcm files (the parent folder is the machine name).\n"
            "Settings: Institution, RunMode, and per-machine folders, plan file, and analysis parameters.\n"
            "Run Analysis: detect field and BB centers (writes {file}_out).\n"
            "View Report: open report.html in the browser.\n"
            "User settings (top bar): email, My machines, and new QA case emails.\n\n"
            "Pan: drag with the left mouse button.\n"
            "Zoom: mouse wheel, or Zoom In/Out.\n"
            "Window/Level: sliders, or Shift+drag / right-drag on the image.\n\n"
            "View:\n"
            "  Report crop = 50 mm center crop, min-max to 8-bit.\n"
            "  Report LoG = same crop, then Laplacian-of-Gaussian (used in result.png / HTML reports).\n"
            "  Full image = entire DICOM, min-max to 8-bit.\n\n"
            "Red cross = radiation field center.\n"
            "Green cross = BB center.",
        )


def run_app(argv: list[str] | None = None, folder: str | Path | None = None) -> int:
    import sys

    from .identity import USER_ID_OIDC, USER_ID_OSUSER, get_user_id_method, upsert_os_user_profile
    from .oidc_dialog import OidcLoginDialog
    from PyQt5.QtWidgets import QDialog

    configure_logging()
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
    method = get_user_id_method()
    if method == USER_ID_OSUSER:
        upsert_os_user_profile()
    if method == USER_ID_OIDC:
        login = OidcLoginDialog()
        login.setWindowIcon(icon)
        if login.exec_() != QDialog.Accepted:
            return 0
    win = MainWindow()
    win.setWindowIcon(icon)
    if folder:
        win.load_folder(folder)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(run_app())
