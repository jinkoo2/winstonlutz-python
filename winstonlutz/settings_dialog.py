"""Tabbed Settings dialog: General (Institution) and Machines."""

from __future__ import annotations

import copy
from pathlib import Path

from PyQt5.QtCore import QSettings, QTimer, Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .app_settings import (
    BB_SEARCH_METHODS,
    ERROR_EMAIL_TO_KEY,
    INSTITUTION_KEY,
    KV_FIELD_SEARCH_METHODS,
    MACHINES_KEY,
    MV_FIELD_SEARCH_METHODS,
    RUN_MODE_CLINIC,
    RUN_MODE_KEY,
    RUN_MODES,
    default_machine,
    format_csv_numbers,
    get_institution,
    get_machines,
    get_run_mode,
    load_gui_settings,
    parse_csv_numbers,
    parse_int_list,
    save_gui_settings,
    user_config_path,
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "yes")


def _browse_start(text: str) -> str:
    path = Path(str(text or "").strip())
    if path.is_file():
        return str(path.parent)
    if path.is_dir():
        return str(path)
    parent = path.parent
    if parent.is_dir():
        return str(parent)
    return ""


class MachineForm(QWidget):
    """Editor for one MACHINES JSON object."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: dict = {}
        self._loading = False
        self.name_changed = None  # optional callable(str)

        self.name = QLineEdit()
        self.data_folder = QLineEdit()
        self.report_template = QLineEdit()
        self.case_regex = QLineEdit()
        self.record_csv = QLineEdit()
        self.plan_file = QLineEdit()
        self.ignore_beams = QLineEdit()
        self.all_ri_required = QCheckBox("Require every plan beam (except IGNORE_BEAMS)")
        self.tol = QDoubleSpinBox()
        self.tol.setRange(0.1, 10.0)
        self.tol.setSingleStep(0.1)
        self.tol.setDecimals(2)
        self.mv_method = QComboBox()
        self.mv_method.addItems(list(BB_SEARCH_METHODS))
        self.kv_method = QComboBox()
        self.kv_method.addItems(list(BB_SEARCH_METHODS))
        self.mv_field_method = QComboBox()
        self.mv_field_method.addItems(list(MV_FIELD_SEARCH_METHODS))
        self.kv_field_method = QComboBox()
        self.kv_field_method.addItems(list(KV_FIELD_SEARCH_METHODS))
        self.crop_mm = QLineEdit()
        self.sad_mm = QDoubleSpinBox()
        self.sad_mm.setRange(1.0, 5000.0)
        self.sad_mm.setDecimals(1)
        self.sid_mm = QDoubleSpinBox()
        self.sid_mm.setRange(1.0, 5000.0)
        self.sid_mm.setDecimals(1)
        self.mv_kvp_min = QDoubleSpinBox()
        self.mv_kvp_min.setRange(0.0, 20000.0)
        self.mv_kvp_min.setDecimals(0)
        self.mv_size = QLineEdit()
        self.kv_size = QLineEdit()
        self.gantry_angles = QLineEdit()
        self.table_angles = QLineEdit()
        self.coll_angles = QLineEdit()

        self.name.textChanged.connect(self._on_name_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._group("Identity / folders", self._identity_form()))
        root.addWidget(self._group("RT Plan", self._plan_form()))
        root.addWidget(self._group("Pass / fail and BB search", self._analysis_form()))
        root.addWidget(self._group("Geometry", self._geometry_form()))
        root.addWidget(self._group("Nominal angles (snap and sort)", self._angles_form()))
        root.addStretch(1)

    def _group(self, title: str, form: QFormLayout) -> QGroupBox:
        box = QGroupBox(title)
        box.setLayout(form)
        return box

    def _path_row(self, edit: QLineEdit, kind: str) -> QWidget:
        btn = QPushButton("...")
        btn.setFixedWidth(32)
        btn.clicked.connect(lambda: self._browse(edit, kind))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(edit, 1)
        layout.addWidget(btn, 0)
        return row

    def _browse(self, edit: QLineEdit, kind: str) -> None:
        start = _browse_start(edit.text())
        if kind == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif kind == "dcm":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select RT Plan", start, "DICOM (*.dcm);;All files (*)"
            )
        elif kind == "html":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select report template", start, "HTML (*.html *.tmpl.html);;All files (*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select file", start, "All files (*)")
        if path:
            edit.setText(path)

    def _identity_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("NAME", self.name)
        form.addRow("DATA_FOLDER", self._path_row(self.data_folder, "dir"))
        form.addRow("REPORT_TEMPLATE_FILE_PATH", self._path_row(self.report_template, "html"))
        form.addRow("CASE_FOLDER_NAME_REGEX", self.case_regex)
        form.addRow("record_csv_file", self.record_csv)
        return form

    def _plan_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("DICOM_PLAN_FILE", self._path_row(self.plan_file, "dcm"))
        form.addRow("IGNORE_BEAMS", self.ignore_beams)
        self.ignore_beams.setPlaceholderText("e.g. 2")
        form.addRow("", self.all_ri_required)
        return form

    def _analysis_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("WL_pass_tolerance (mm)", self.tol)
        form.addRow("MV_bb_search_method", self.mv_method)
        form.addRow("kV_bb_search_method", self.kv_method)
        form.addRow("MV_field_search_method", self.mv_field_method)
        form.addRow("kV_field_search_method", self.kv_field_method)
        return form

    def _geometry_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("crop_mm", self.crop_mm)
        self.crop_mm.setPlaceholderText("50  or  50, 50")
        form.addRow("sad_mm", self.sad_mm)
        form.addRow("default_sid_mm", self.sid_mm)
        form.addRow("MV_kvp_min", self.mv_kvp_min)
        form.addRow("MV_image_size", self.mv_size)
        self.mv_size.setPlaceholderText("1190, 1190")
        form.addRow("kV_image_size", self.kv_size)
        self.kv_size.setPlaceholderText("1024, 768")
        return form

    def _angles_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("nominal_gantry_angles", self.gantry_angles)
        form.addRow("nominal_table_angles", self.table_angles)
        form.addRow("nominal_collimator_angles", self.coll_angles)
        return form

    def _on_name_changed(self, text: str) -> None:
        if self._loading or self.name_changed is None:
            return
        self.name_changed(text)

    def set_enabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)

    def load_machine(self, machine: dict | None) -> None:
        self._loading = True
        self._source = dict(machine or default_machine())
        m = self._source
        self.name.setText(str(m.get("NAME") or ""))
        self.data_folder.setText(str(m.get("DATA_FOLDER") or ""))
        self.report_template.setText(str(m.get("REPORT_TEMPLATE_FILE_PATH") or ""))
        self.case_regex.setText(str(m.get("CASE_FOLDER_NAME_REGEX") or ""))
        self.record_csv.setText(str(m.get("record_csv_file") or ""))
        self.plan_file.setText(str(m.get("DICOM_PLAN_FILE") or ""))
        self.ignore_beams.setText(format_csv_numbers(m.get("IGNORE_BEAMS")))
        self.all_ri_required.setChecked(_as_bool(m.get("ALL_RI_IMAGE_REQUIRED")))
        try:
            self.tol.setValue(float(m.get("WL_pass_tolerance") or 1.0))
        except (TypeError, ValueError):
            self.tol.setValue(1.0)
        self._set_combo(self.mv_method, str(m.get("MV_bb_search_method") or "ConnectedComponent"))
        self._set_combo(self.kv_method, str(m.get("kV_bb_search_method") or "ConnectedComponent"))
        self._set_combo(self.mv_field_method, str(m.get("MV_field_search_method") or "Otsu"))
        self._set_combo(self.kv_field_method, str(m.get("kV_field_search_method") or "ImageCenter"))
        crop = m.get("crop_mm")
        self.crop_mm.setText(format_csv_numbers(crop) if crop not in (None, "") else "50")
        try:
            self.sad_mm.setValue(float(m.get("sad_mm") or 1000.0))
        except (TypeError, ValueError):
            self.sad_mm.setValue(1000.0)
        try:
            self.sid_mm.setValue(float(m.get("default_sid_mm") or 1500.0))
        except (TypeError, ValueError):
            self.sid_mm.setValue(1500.0)
        try:
            self.mv_kvp_min.setValue(float(m.get("MV_kvp_min") or 1000.0))
        except (TypeError, ValueError):
            self.mv_kvp_min.setValue(1000.0)
        self.mv_size.setText(format_csv_numbers(m.get("MV_image_size") or [1190, 1190]))
        self.kv_size.setText(format_csv_numbers(m.get("kV_image_size") or [1024, 768]))
        self.gantry_angles.setText(format_csv_numbers(m.get("nominal_gantry_angles")))
        self.table_angles.setText(format_csv_numbers(m.get("nominal_table_angles")))
        self.coll_angles.setText(format_csv_numbers(m.get("nominal_collimator_angles")))
        self._loading = False

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx < 0 and value:
            combo.addItem(value)
            idx = combo.findText(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def collect_machine(self) -> dict:
        data = dict(self._source)
        data["NAME"] = self.name.text().strip()
        data["DATA_FOLDER"] = self.data_folder.text().strip()
        data["REPORT_TEMPLATE_FILE_PATH"] = self.report_template.text().strip()
        data["CASE_FOLDER_NAME_REGEX"] = self.case_regex.text().strip()
        data["record_csv_file"] = self.record_csv.text().strip()
        data["DICOM_PLAN_FILE"] = self.plan_file.text().strip()
        data["IGNORE_BEAMS"] = parse_int_list(self.ignore_beams.text())
        data["ALL_RI_IMAGE_REQUIRED"] = self.all_ri_required.isChecked()
        data["WL_pass_tolerance"] = self.tol.value()
        data["MV_bb_search_method"] = self.mv_method.currentText()
        data["kV_bb_search_method"] = self.kv_method.currentText()
        data["MV_field_search_method"] = self.mv_field_method.currentText()
        data["kV_field_search_method"] = self.kv_field_method.currentText()
        data.pop("kV_field_search", None)
        crop = parse_csv_numbers(self.crop_mm.text())
        if len(crop) >= 2:
            data["crop_mm"] = crop[:2]
        elif len(crop) == 1:
            data["crop_mm"] = crop[0]
        else:
            data["crop_mm"] = 50.0
        data["sad_mm"] = self.sad_mm.value()
        data["default_sid_mm"] = self.sid_mm.value()
        data["MV_kvp_min"] = self.mv_kvp_min.value()
        mv_size = [int(x) for x in parse_csv_numbers(self.mv_size.text())]
        kv_size = [int(x) for x in parse_csv_numbers(self.kv_size.text())]
        data["MV_image_size"] = mv_size[:2] if len(mv_size) >= 2 else [1190, 1190]
        data["kV_image_size"] = kv_size[:2] if len(kv_size) >= 2 else [1024, 768]
        data["nominal_gantry_angles"] = parse_csv_numbers(self.gantry_angles.text())
        data["nominal_table_angles"] = parse_csv_numbers(self.table_angles.text())
        data["nominal_collimator_angles"] = parse_csv_numbers(self.coll_angles.text())
        return data


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(960, 680)
        self._qs = QSettings("MachineQA", "WinstonLutz")
        self._original = load_gui_settings()
        self._machines: list[dict] = copy.deepcopy(get_machines())
        self._index = -1
        self.did_save = False

        self.institution = QLineEdit()
        self.institution.setText(get_institution())
        self.run_mode = QComboBox()
        self.run_mode.addItems(list(RUN_MODES))
        mode_idx = self.run_mode.findText(get_run_mode(self._original))
        self.run_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        self.error_email_to = QLineEdit()
        self.error_email_to.setText(str(self._original.get(ERROR_EMAIL_TO_KEY) or ""))
        self.error_email_to.setPlaceholderText("jinkoo.kim@stonybrookmedicine.edu")
        self.email_from = QLineEdit()
        self.email_from.setText(str(self._original.get("email_from") or ""))
        self.email_domain = QLineEdit()
        self.email_domain.setText(str(self._original.get("email_domain") or ""))
        self.email_host = QLineEdit()
        self.email_host.setText(str(self._original.get("email_host_address") or ""))
        self.email_port = QSpinBox()
        self.email_port.setRange(1, 65535)
        try:
            self.email_port.setValue(int(self._original.get("email_host_port") or 25))
        except (TypeError, ValueError):
            self.email_port.setValue(25)
        self.email_ssl = QCheckBox("enable_ssl")
        ssl_val = self._original.get("enable_ssl", False)
        if isinstance(ssl_val, str):
            ssl_val = ssl_val.strip().lower() in ("true", "1", "yes")
        self.email_ssl.setChecked(bool(ssl_val))
        self.path_label = QLabel(str(user_config_path()))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)

        self.machine_list = QListWidget()
        self.form = MachineForm()
        self.form.name_changed = self._rename_current
        self.add_btn = QPushButton("Add")
        self.dup_btn = QPushButton("Duplicate")
        self.remove_btn = QPushButton("Remove")
        self.add_btn.clicked.connect(self._add_machine)
        self.dup_btn.clicked.connect(self._duplicate_machine)
        self.remove_btn.clicked.connect(self._remove_machine)
        self.machine_list.currentRowChanged.connect(self._row_changed)

        tabs = QTabWidget()
        tabs.addTab(self._general_page(), "General")
        tabs.addTab(self._machines_page(), "Machines")

        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save_settings)
        self.close_btn.clicked.connect(self.reject)
        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.save_btn)
        button_row.addWidget(self.close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

        self._fill_list()
        if self._machines:
            self.machine_list.setCurrentRow(0)
        else:
            self.form.set_enabled(False)
        geom = self._qs.value("settings/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        split_state = self._qs.value("settings/splitter")
        if split_state is not None:
            self.splitter.restoreState(split_state)
        QTimer.singleShot(0, self.showMaximized)

    def _general_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.addRow("Institution", self.institution)
        form.addRow("RunMode", self.run_mode)
        form.addRow("error_email_to", self.error_email_to)
        form.addRow("email_from", self.email_from)
        form.addRow("email_domain", self.email_domain)
        form.addRow("email_host_address", self.email_host)
        form.addRow("email_host_port", self.email_port)
        form.addRow("", self.email_ssl)
        form.addRow("Settings file", self.path_label)
        hint = QLabel(
            "Clinic: Open Case picks a configured machine, then a case. "
            "Simple: Open Case picks a folder of RI images; the parent folder is the machine name. "
            "Simple is also used when this file is missing or MACHINES is empty. "
            "If error_email_to is set (with email_from and email_host_address), uncaught exceptions "
            "and logger.exception events are emailed."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")
        form.addRow("", hint)
        return page

    def _machines_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.machine_list, 1)
        btns = QHBoxLayout()
        btns.addWidget(self.add_btn)
        btns.addWidget(self.dup_btn)
        btns.addWidget(self.remove_btn)
        left_layout.addLayout(btns)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 680])
        outer.addWidget(self.splitter)
        return page

    def _fill_list(self) -> None:
        self.machine_list.blockSignals(True)
        self.machine_list.clear()
        for machine in self._machines:
            self.machine_list.addItem(QListWidgetItem(self._label(machine)))
        self.machine_list.blockSignals(False)

    def _label(self, machine: dict) -> str:
        return str(machine.get("NAME") or "").strip() or "(unnamed)"

    def _store_current(self) -> None:
        if 0 <= self._index < len(self._machines):
            self._machines[self._index] = self.form.collect_machine()

    def _row_changed(self, row: int) -> None:
        self._store_current()
        self._index = row
        if 0 <= row < len(self._machines):
            self.form.set_enabled(True)
            self.form.load_machine(self._machines[row])
        else:
            self.form.set_enabled(False)

    def _rename_current(self, text: str) -> None:
        item = self.machine_list.currentItem()
        if item is not None:
            item.setText(text.strip() or "(unnamed)")

    def _add_machine(self) -> None:
        self._store_current()
        existing = {str(m.get("NAME") or "") for m in self._machines}
        name = "New machine"
        n = 2
        while name in existing:
            name = f"New machine {n}"
            n += 1
        self._machines.append(default_machine(name))
        self.machine_list.addItem(QListWidgetItem(name))
        self.machine_list.setCurrentRow(len(self._machines) - 1)
        self.form.set_enabled(True)

    def _duplicate_machine(self) -> None:
        self._store_current()
        if not (0 <= self._index < len(self._machines)):
            return
        clone = copy.deepcopy(self._machines[self._index])
        base = str(clone.get("NAME") or "Machine").strip() or "Machine"
        clone["NAME"] = f"{base} copy"
        self._machines.append(clone)
        self.machine_list.addItem(QListWidgetItem(clone["NAME"]))
        self.machine_list.setCurrentRow(len(self._machines) - 1)

    def _remove_machine(self) -> None:
        if not (0 <= self._index < len(self._machines)):
            return
        name = self._label(self._machines[self._index])
        if (
            QMessageBox.question(
                self,
                "Remove machine",
                f"Remove {name}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        del self._machines[self._index]
        gone = self._index
        self._index = -1
        self._fill_list()
        if self._machines:
            self.machine_list.setCurrentRow(min(gone, len(self._machines) - 1))
        else:
            self.form.set_enabled(False)

    def collected(self) -> dict:
        self._store_current()
        data = dict(self._original)
        data[INSTITUTION_KEY] = self.institution.text().strip()
        data[RUN_MODE_KEY] = self.run_mode.currentText() or RUN_MODE_CLINIC
        data[ERROR_EMAIL_TO_KEY] = self.error_email_to.text().strip()
        data["email_from"] = self.email_from.text().strip()
        data["email_domain"] = self.email_domain.text().strip()
        data["email_host_address"] = self.email_host.text().strip()
        data["email_host_port"] = self.email_port.value()
        data["enable_ssl"] = self.email_ssl.isChecked()
        data[MACHINES_KEY] = copy.deepcopy(self._machines)
        return data

    def _save_settings(self) -> None:
        self._store_current()
        unnamed = [
            i + 1 for i, m in enumerate(self._machines) if not str(m.get("NAME") or "").strip()
        ]
        if unnamed:
            QMessageBox.warning(
                self,
                "Settings",
                "Each machine needs a NAME. Empty at: " + ", ".join(str(i) for i in unnamed),
            )
            return
        data = self.collected()
        save_gui_settings(data)
        self._original = copy.deepcopy(data)
        self.did_save = True
        self._save_window_state()

    def reject(self) -> None:
        self._save_window_state()
        super().reject()

    def _save_window_state(self) -> None:
        self._qs.setValue("settings/geometry", self.saveGeometry())
        self._qs.setValue("settings/splitter", self.splitter.saveState())
        self._qs.sync()
