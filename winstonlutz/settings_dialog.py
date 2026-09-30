"""Tabbed Settings dialog: General, Identity, Machines, Notifications, Watcher."""

from __future__ import annotations

import copy
from functools import partial
from pathlib import Path

from PyQt5.QtCore import QSettings, QTimer, QUrl, Qt, QTime
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .emailer import (
    CHAT_CHANNEL_LABELS,
    error_email_to_list,
    format_error_email_to,
    send_test_chat,
    send_test_email,
)
from .identity import (
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_ISSUER,
    DEFAULT_OIDC_REDIRECT_URI,
    DEFAULT_OIDC_REGISTRATION_URL,
    DEFAULT_OIDC_SCOPES,
    IDENTITY_KEY,
    USER_ID_METHODS,
    USER_ID_NONE,
    USER_ID_OIDC,
    USER_ID_OSUSER,
    coerce_registration_url,
    format_os_user_preview,
    get_user_id_method,
    identity_block,
    oidc_settings,
    upsert_os_user_profile,
)

from .watch_service import (
    FROZEN_APP_PARAMETERS,
    SOURCE_APP_PARAMETERS,
    WatchServicePlan,
    default_app_parameters,
    default_plan,
    format_nssm_commands,
    install_watch_service,
    is_admin,
    is_windows,
)
from .app_settings import (
    BB_SEARCH_METHODS,
    DEFAULT_WATCH_ARCHIVE,
    DEFAULT_WATCH_ARCHIVE_AGE_DAYS,
    DEFAULT_WATCH_ARCHIVE_AT,
    DEFAULT_WATCH_DISK_SCAN,
    DEFAULT_WATCH_DISK_SCAN_SEC,
    DEFAULT_WATCH_POLL_SEC,
    DOCUFORMS2_IGRT_TYPE,
    ERROR_EMAIL_TO_KEY,
    EVENT_EMAIL_TO_KEY,
    INSTITUTION_KEY,
    KV_FIELD_SEARCH_METHODS,
    MACHINES_KEY,
    MV_FIELD_SEARCH_METHODS,
    NEW_CASE_EMAIL_TO_KEY,
    NOTIFICATIONS_KEY,
    POST_PROCESSING_KEY,
    RUN_MODE_CLINIC,
    RUN_MODE_KEY,
    RUN_MODES,
    TOP_LEVEL_EMAIL_KEYS,
    WATCHER_KEY,
    chat_webhook_urls,
    default_docuforms2_igrt_step,
    default_machine,
    email_settings_block,
    find_post_step,
    format_csv_numbers,
    format_form_ids,
    form_ids_from_machines,
    form_ids_from_step,
    get_institution,
    get_machines,
    get_run_mode,
    load_gui_settings,
    normalize_file_patterns,
    parse_csv_numbers,
    parse_form_ids_text,
    parse_int_list,
    post_processing_steps,
    save_gui_settings,
    user_config_path,
    upsert_post_step,
    watcher_settings,
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


def _hint_label(text: str) -> QLabel:
    """Wrapping note that does not force the dialog to the full line width."""
    hint = QLabel(text)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: #64748b;")
    hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return hint


def _scroll_page(inner: QWidget) -> QWidget:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(inner)
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(scroll)
    return page


class MachineForm(QWidget):
    """Editor for one MACHINES JSON object."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: dict = {}
        self._loading = False
        self.name_changed = None  # optional callable(str)

        self.name = QLineEdit()
        self.watch_folder = QLineEdit()
        self.data_folder = QLineEdit()
        self.report_template = QLineEdit()
        self.case_regex = QLineEdit()
        self.record_csv = QLineEdit()
        self.new_case_email_to = QPlainTextEdit()
        self.new_case_email_to.setTabChangesFocus(True)
        self.new_case_email_to.setFixedHeight(72)
        self.new_case_email_to.setPlaceholderText("optional extra addresses for this machine")
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
        form.addRow("WATCH_FOLDER", self._path_row(self.watch_folder, "dir"))
        form.addRow("DATA_FOLDER", self._path_row(self.data_folder, "dir"))
        form.addRow("REPORT_TEMPLATE_FILE_PATH", self._path_row(self.report_template, "html"))
        form.addRow("CASE_FOLDER_NAME_REGEX", self.case_regex)
        form.addRow("record_csv_file", self.record_csv)
        form.addRow("new_case_email_to", self.new_case_email_to)
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
        self.watch_folder.setText(str(m.get("WATCH_FOLDER") or ""))
        self.data_folder.setText(str(m.get("DATA_FOLDER") or ""))
        self.report_template.setText(str(m.get("REPORT_TEMPLATE_FILE_PATH") or ""))
        self.case_regex.setText(str(m.get("CASE_FOLDER_NAME_REGEX") or ""))
        self.record_csv.setText(str(m.get("record_csv_file") or ""))
        self.new_case_email_to.setPlainText(format_error_email_to(m.get("new_case_email_to")))
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
        data["WATCH_FOLDER"] = self.watch_folder.text().strip()
        data["DATA_FOLDER"] = self.data_folder.text().strip()
        data["REPORT_TEMPLATE_FILE_PATH"] = self.report_template.text().strip()
        data["CASE_FOLDER_NAME_REGEX"] = self.case_regex.text().strip()
        data["record_csv_file"] = self.record_csv.text().strip()
        data["new_case_email_to"] = error_email_to_list(self.new_case_email_to.toPlainText())
        data.pop("docuforms2_form_id", None)
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


class InstallWatchServiceDialog(QDialog):
    """Collect NSSM paths and the Windows account that can reach the UNC shares."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install Watcher as Service")
        self.setModal(True)
        self.resize(640, 480)
        plan = default_plan()

        self.service_name = QLineEdit(plan.service_name)
        self.display_name = QLineEdit(plan.display_name)
        self.nssm_exe = QLineEdit(plan.nssm_exe)
        self.program_exe = QLineEdit(plan.program_exe)
        self.app_parameters = QLineEdit(plan.app_parameters)
        self.app_directory = QLineEdit(plan.app_directory)
        self.settings_file = QLineEdit(plan.settings_file)
        self.use_account = QCheckBox("Log on as this account (needed for UNC share access)")
        self.use_account.setChecked(bool(plan.account))
        self.account = QLineEdit(plan.account)
        self.account.setPlaceholderText(r"DOMAIN\username")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Windows password for that account")
        self.replace_existing = QCheckBox("Replace the service if it already exists")
        self.replace_existing.setChecked(True)
        self.start_after = QCheckBox("Start the service after install")
        self.start_after.setChecked(True)
        self.use_account.toggled.connect(self._sync_account)
        self.program_exe.textChanged.connect(self._sync_app_parameters)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.addRow("service_name", self.service_name)
        form.addRow("display_name", self.display_name)
        form.addRow("nssm.exe", self._path_row(self.nssm_exe, "nssm"))
        form.addRow("program", self._path_row(self.program_exe, "exe"))
        form.addRow("arguments", self.app_parameters)
        form.addRow("app_directory", self._path_row(self.app_directory, "dir"))
        form.addRow("settings_file", self._path_row(self.settings_file, "json"))
        form.addRow("", self.use_account)
        form.addRow("account", self.account)
        form.addRow("password", self.password)
        form.addRow("", self.replace_existing)
        form.addRow("", self.start_after)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Install")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            _hint_label(
                "Installs the watcher with NSSM (https://nssm.cc). Packaged app: "
                "WinstonLutz.exe with arguments watch (no separate Python). From source: "
                "python.exe with -u -m winstonlutz watch. Use the same Windows account as "
                "the old C# service so UNC shares work. Administrator rights are required. "
                "Stop WinstonLutzWindowsService first so both watchers do not run."
            )
        )
        layout.addWidget(buttons)
        self._sync_account()

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
        start = edit.text().strip()
        if kind == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif kind == "json":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select settings file", start, "JSON (*.json);;All files (*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select executable", start, "Programs (*.exe);;All files (*)"
            )
        if path:
            edit.setText(path)

    def _sync_account(self, *_args) -> None:
        on = self.use_account.isChecked()
        self.account.setEnabled(on)
        self.password.setEnabled(on)

    def _sync_app_parameters(self, *_args) -> None:
        current = self.app_parameters.text().strip()
        if current and current not in (SOURCE_APP_PARAMETERS, FROZEN_APP_PARAMETERS):
            return
        self.app_parameters.setText(default_app_parameters(self.program_exe.text().strip()))

    def plan(self) -> WatchServicePlan:
        return WatchServicePlan(
            service_name=self.service_name.text().strip(),
            display_name=self.display_name.text().strip(),
            nssm_exe=self.nssm_exe.text().strip(),
            program_exe=self.program_exe.text().strip(),
            app_parameters=self.app_parameters.text().strip(),
            app_directory=self.app_directory.text().strip(),
            settings_file=self.settings_file.text().strip(),
            account=self.account.text().strip() if self.use_account.isChecked() else "",
            password=self.password.text() if self.use_account.isChecked() else "",
            start_after=self.start_after.isChecked(),
            replace_existing=self.replace_existing.isChecked(),
        )


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
        email = email_settings_block(self._original)
        self.error_email_to = QPlainTextEdit()
        self.error_email_to.setPlainText(format_error_email_to(email.get(ERROR_EMAIL_TO_KEY)))
        self.error_email_to.setPlaceholderText("one address per line")
        self.error_email_to.setTabChangesFocus(True)
        self.error_email_to.setFixedHeight(72)
        self.event_email_to = QPlainTextEdit()
        self.event_email_to.setPlainText(format_error_email_to(email.get(EVENT_EMAIL_TO_KEY)))
        self.event_email_to.setPlaceholderText("one address per line")
        self.event_email_to.setTabChangesFocus(True)
        self.event_email_to.setFixedHeight(72)
        self.notify_new_case_email_to = QPlainTextEdit()
        self.notify_new_case_email_to.setPlainText(
            format_error_email_to(email.get(NEW_CASE_EMAIL_TO_KEY))
        )
        self.notify_new_case_email_to.setPlaceholderText("one address per line")
        self.notify_new_case_email_to.setTabChangesFocus(True)
        self.notify_new_case_email_to.setFixedHeight(72)
        self.email_from = QLineEdit()
        self.email_from.setText(str(email.get("email_from") or ""))
        self.email_domain = QLineEdit()
        self.email_domain.setText(str(email.get("email_domain") or ""))
        self.email_host = QLineEdit()
        self.email_host.setText(str(email.get("email_host_address") or ""))
        self.email_port = QSpinBox()
        self.email_port.setRange(1, 65535)
        try:
            self.email_port.setValue(int(email.get("email_host_port") or 25))
        except (TypeError, ValueError):
            self.email_port.setValue(25)
        self.email_ssl = QCheckBox("Use STARTTLS (enable_ssl)")
        ssl_val = email.get("enable_ssl", False)
        if isinstance(ssl_val, str):
            ssl_val = ssl_val.strip().lower() in ("true", "1", "yes")
        self.email_ssl.setChecked(bool(ssl_val))
        hooks = chat_webhook_urls(self._original)
        self.google_chat_url = QLineEdit()
        self.google_chat_url.setText(hooks.get("google_chat") or "")
        self.google_chat_url.setPlaceholderText("https://chat.googleapis.com/v1/spaces/.../messages?key=...")
        self.slack_url = QLineEdit()
        self.slack_url.setText(hooks.get("slack") or "")
        self.slack_url.setPlaceholderText("https://hooks.slack.com/services/...")
        self.teams_url = QLineEdit()
        self.teams_url.setText(hooks.get("microsoft_teams") or "")
        self.teams_url.setPlaceholderText("https://...webhook.office.com/webhookb2/...")
        self.discord_url = QLineEdit()
        self.discord_url.setText(hooks.get("discord") or "")
        self.discord_url.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.path_label = QLabel(str(user_config_path()))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        watcher = watcher_settings(self._original)
        self.watch_path = QLineEdit()
        self.watch_path.setText(watcher.get("watch_path") or "")
        self.watch_path.setPlaceholderText(r"\\varianfs\VA_TRANSFER\QA\2.IGRT")
        self.watch_data_root = QLineEdit()
        self.watch_data_root.setText(watcher.get("winstonlutz_data_root") or "")
        self.watch_data_root.setPlaceholderText(r"\\uhmc-fs-share\Shares\RadOnc\Planning\Physics QA\WinstonLutz")
        self.watch_recursive = QCheckBox("Watch subfolders")
        self.watch_recursive.setChecked(bool(watcher.get("watch_subfolders", True)))
        self.watch_disk_scan = QCheckBox("Scan disk for missed cases (UNC backup)")
        self.watch_disk_scan.setChecked(
            bool(watcher.get("disk_scan_for_new_case_detection", DEFAULT_WATCH_DISK_SCAN))
        )
        self.watch_file_patterns = QPlainTextEdit()
        self.watch_file_patterns.setPlainText("\n".join(watcher.get("new_case_file_patterns") or []))
        self.watch_file_patterns.setPlaceholderText("RE.*.dcm")
        self.watch_file_patterns.setTabChangesFocus(True)
        self.watch_file_patterns.setFixedHeight(56)
        self.watch_case_folder_regex = QLineEdit()
        self.watch_case_folder_regex.setText(str(watcher.get("case_folder_name_regex") or ""))
        self.watch_case_folder_regex.setPlaceholderText(r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
        self.watch_case_dir_levels = QSpinBox()
        self.watch_case_dir_levels.setRange(1, 8)
        self.watch_case_dir_levels.setValue(int(watcher.get("machine_to_case_dir_levels") or 1))
        self.watch_poll_sec = QDoubleSpinBox()
        self.watch_poll_sec.setRange(0.5, 3600.0)
        self.watch_poll_sec.setDecimals(1)
        self.watch_poll_sec.setValue(float(watcher.get("queued_case_poll_sec") or DEFAULT_WATCH_POLL_SEC))
        self.watch_disk_scan_sec = QDoubleSpinBox()
        self.watch_disk_scan_sec.setRange(0.5, 3600.0)
        self.watch_disk_scan_sec.setDecimals(1)
        self.watch_disk_scan_sec.setValue(
            float(watcher.get("disk_scan_for_new_case_detection_sec") or DEFAULT_WATCH_DISK_SCAN_SEC)
        )
        self.watch_disk_scan.toggled.connect(self.watch_disk_scan_sec.setEnabled)
        self.watch_disk_scan_sec.setEnabled(self.watch_disk_scan.isChecked())
        self.watch_archive = QCheckBox("Archive old cases from WATCH_FOLDER to DATA_FOLDER")
        self.watch_archive.setChecked(bool(watcher.get("archive_old_cases", DEFAULT_WATCH_ARCHIVE)))
        self.watch_archive_age_days = QSpinBox()
        self.watch_archive_age_days.setRange(1, 365)
        self.watch_archive_age_days.setSuffix(" days")
        try:
            self.watch_archive_age_days.setValue(
                int(watcher.get("archive_cases_older_than_days") or DEFAULT_WATCH_ARCHIVE_AGE_DAYS)
            )
        except (TypeError, ValueError):
            self.watch_archive_age_days.setValue(DEFAULT_WATCH_ARCHIVE_AGE_DAYS)
        self.watch_archive_at = QTimeEdit()
        self.watch_archive_at.setDisplayFormat("HH:mm")
        at_parts = str(
            watcher.get("archive_old_cases_at") or DEFAULT_WATCH_ARCHIVE_AT
        ).split(":")
        try:
            self.watch_archive_at.setTime(QTime(int(at_parts[0]), int(at_parts[1])))
        except (TypeError, ValueError, IndexError):
            self.watch_archive_at.setTime(QTime(1, 0))
        self.watch_archive.toggled.connect(self._sync_archive_ui)
        self._sync_archive_ui()
        df = {**default_docuforms2_igrt_step(), **find_post_step(DOCUFORMS2_IGRT_TYPE, self._original)}
        self.df_enabled = QCheckBox("Upload IGRT results to DocuForms2 after analysis")
        self.df_enabled.setChecked(_as_bool(df.get("enabled", True)))
        self.df_backend = QLineEdit()
        self.df_backend.setText(str(df.get("backend_url") or ""))
        self.df_backend.setPlaceholderText("https://roweb3.uhmc.sbuh.stonybrook.edu:9001")
        self.df_verify_ssl = QCheckBox("Verify SSL")
        self.df_verify_ssl.setChecked(_as_bool(df.get("verify_ssl", False)))
        self.df_dry_run = QCheckBox("Dry run (parse only, do not submit)")
        self.df_dry_run.setChecked(_as_bool(df.get("dry_run", False)))
        self.df_zip = QCheckBox("Attach input_dcm.zip")
        self.df_zip.setChecked(_as_bool(df.get("attach_dcm_zip", True)))
        self.df_pdf = QCheckBox("Attach report.pdf (full report.html)")
        self.df_pdf.setChecked(_as_bool(df.get("attach_pdf", True)))
        self.df_resubmit = QCheckBox("Resubmit cases that already have .docuforms2_igrt.json")
        self.df_resubmit.setChecked(_as_bool(df.get("resubmit", False)))
        self.df_timeout = QSpinBox()
        self.df_timeout.setRange(30, 3600)
        try:
            self.df_timeout.setValue(int(df.get("timeout_sec") or 300))
        except (TypeError, ValueError):
            self.df_timeout.setValue(300)
        self.df_form_ids = QPlainTextEdit()
        form_id_rows = form_ids_from_step(df) or form_ids_from_machines(
            self._original.get(MACHINES_KEY)
        )
        self.df_form_ids.setPlainText(format_form_ids(form_id_rows))
        self.df_form_ids.setPlaceholderText("Edge = sb_edge_mlc_wl")
        self.df_form_ids.setTabChangesFocus(True)
        self.df_form_ids.setFixedHeight(110)
        self.df_email_success_event_to = QPlainTextEdit()
        self.df_email_success_event_to.setPlainText(
            format_error_email_to(df.get("email_success_event_to"))
        )
        self.df_email_success_event_to.setPlaceholderText("one address per line")
        self.df_email_success_event_to.setTabChangesFocus(True)
        self.df_email_success_event_to.setFixedHeight(72)
        self.df_email_failure_event_to = QPlainTextEdit()
        self.df_email_failure_event_to.setPlainText(
            format_error_email_to(df.get("email_failure_event_to"))
        )
        self.df_email_failure_event_to.setPlaceholderText("one address per line")
        self.df_email_failure_event_to.setTabChangesFocus(True)
        self.df_email_failure_event_to.setFixedHeight(72)
        oidc = oidc_settings(self._original)
        self.user_id_method = QComboBox()
        self.user_id_method.addItems(list(USER_ID_METHODS))
        method_idx = self.user_id_method.findText(get_user_id_method(self._original))
        self.user_id_method.setCurrentIndex(method_idx if method_idx >= 0 else 0)
        self.user_id_method.currentTextChanged.connect(self._sync_identity_ui)
        self.os_user_preview = QPlainTextEdit()
        self.os_user_preview.setReadOnly(True)
        self.os_user_preview.setTabChangesFocus(True)
        self.os_user_preview.setFixedHeight(140)
        self.oidc_issuer = QLineEdit()
        self.oidc_issuer.setText(oidc["issuer"] or DEFAULT_OIDC_ISSUER)
        self.oidc_issuer.setPlaceholderText(DEFAULT_OIDC_ISSUER)
        self.oidc_client_id = QLineEdit()
        self.oidc_client_id.setText(oidc["client_id"] or DEFAULT_OIDC_CLIENT_ID)
        self.oidc_scopes = QLineEdit()
        self.oidc_scopes.setText(oidc["scopes"] or DEFAULT_OIDC_SCOPES)
        self.oidc_redirect = QLineEdit()
        self.oidc_redirect.setText(oidc.get("redirect_uri") or DEFAULT_OIDC_REDIRECT_URI)
        self.oidc_redirect.setPlaceholderText(DEFAULT_OIDC_REDIRECT_URI)
        self.oidc_registration = QLineEdit()
        self.oidc_registration.setText(oidc["registration_url"] or DEFAULT_OIDC_REGISTRATION_URL)
        self.oidc_register_btn = QPushButton("Open registration")
        self.oidc_register_btn.setAutoDefault(False)
        self.oidc_register_btn.setDefault(False)
        self.oidc_register_btn.clicked.connect(self._open_oidc_registration)

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
        tabs.addTab(self._identity_page(), "Identity")
        tabs.addTab(self._machines_page(), "Machines")
        tabs.addTab(self._notifications_page(), "Notifications")
        tabs.addTab(self._postprocess_page(), "Post-processing")
        tabs.addTab(self._watcher_page(), "Watcher")

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
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow("Institution", self.institution)
        form.addRow("RunMode", self.run_mode)
        form.addRow("Settings file", self.path_label)
        layout.addLayout(form)
        layout.addWidget(
            _hint_label(
                "Clinic: Open Case picks a configured machine, then a case. "
                "Simple: Open Case picks a folder of RI images; the parent folder is the machine name. "
                "Simple is also used when this file is missing or MACHINES is empty. "
                "Login is on the Identity tab. The Windows watch service is on the Watcher tab. "
                "Error alerts (email and chat) are on the Notifications tab."
            )
        )
        layout.addStretch(1)
        return page

    def _identity_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self._identity_group())
        layout.addStretch(1)
        return _scroll_page(inner)

    def _watcher_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self._watcher_group())
        if is_windows():
            install_btn = QPushButton("Install Watcher as Service")
            install_btn.clicked.connect(self._install_watch_service)
            layout.addWidget(install_btn)
        layout.addStretch(1)
        return _scroll_page(inner)

    def _watcher_group(self) -> QGroupBox:
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow("watch_path", self.watch_path)
        form.addRow("winstonlutz_data_root", self.watch_data_root)
        form.addRow("watch_subfolders", self.watch_recursive)
        form.addRow("disk_scan_for_new_case_detection", self.watch_disk_scan)
        form.addRow("new_case_file_patterns", self.watch_file_patterns)
        form.addRow("case_folder_name_regex", self.watch_case_folder_regex)
        form.addRow("machine_to_case_dir_levels", self.watch_case_dir_levels)
        form.addRow("queued_case_poll_sec", self.watch_poll_sec)
        form.addRow("disk_scan_for_new_case_detection_sec", self.watch_disk_scan_sec)
        form.addRow("archive_old_cases", self.watch_archive)
        form.addRow("archive_cases_older_than_days", self.watch_archive_age_days)
        form.addRow("archive_old_cases_at", self.watch_archive_at)
        box = QGroupBox("Watcher (Windows service)")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "Used by WinstonLutz.exe watch or python -m winstonlutz watch "
                "(Windows service via NSSM), not the GUI. "
                "new_case_file_patterns are filename globs (one per line); default RE.*.dcm. "
                "watch_subfolders watches machine/case subfolders. "
                "machine_to_case_dir_levels is how many parents above the trigger file is the case folder "
                "(1 = same folder as the file). "
                "case_folder_name_regex must match that folder name; leave empty to accept any name. "
                "Machine name is still the parent of the case folder (Edge/26-09-23_... → Edge). "
                "queued_case_poll_sec is how often a queued case is started. "
                "disk_scan_for_new_case_detection walks the share for folders watchdog missed; "
                "disk_scan_for_new_case_detection_sec is that interval. "
                "archive_old_cases moves folders older than archive_cases_older_than_days from each machine "
                "WATCH_FOLDER to DATA_FOLDER at archive_old_cases_at (local time, about a 3-hour window). "
                "Analysis settings come from MACHINES."
            )
        )
        return box

    def _install_watch_service(self) -> None:
        dlg = InstallWatchServiceDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        plan = dlg.plan()
        if not plan.nssm_exe:
            QMessageBox.warning(
                self,
                "NSSM not found",
                "nssm.exe was not found. Install NSSM from https://nssm.cc, "
                "add it to PATH, or choose nssm.exe in the dialog.",
            )
            QDesktopServices.openUrl(QUrl("https://nssm.cc"))
            return
        if plan.account and not plan.password:
            if (
                QMessageBox.question(
                    self,
                    "No password",
                    "Account is set but the password is empty. Install anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                != QMessageBox.Yes
            ):
                return
        save = QMessageBox.question(
            self,
            "Save settings",
            "Save the current settings to disk before installing the service?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if save == QMessageBox.Cancel:
            return
        if save == QMessageBox.Yes:
            self._save_settings()
        if not is_admin():
            commands = format_nssm_commands(plan)
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Administrator required")
            box.setText(
                "Installing a Windows service requires Administrator. "
                "Run this GUI as Administrator and try again, or run the NSSM commands "
                "in an elevated prompt (password is shown as <password>)."
            )
            box.setDetailedText(commands)
            box.exec_()
            return
        ok, log = install_watch_service(plan)
        if ok:
            QMessageBox.information(self, "Watcher service", f"Service installed.\n\n{log}")
        else:
            QMessageBox.critical(self, "Watcher service", f"Install failed.\n\n{log}")

    def _sync_archive_ui(self, *_args) -> None:
        enabled = self.watch_archive.isChecked()
        self.watch_archive_age_days.setEnabled(enabled)
        self.watch_archive_at.setEnabled(enabled)

    def _postprocess_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        form = QFormLayout()
        form.addRow("", self.df_enabled)
        form.addRow("backend_url", self.df_backend)
        form.addRow("", self.df_verify_ssl)
        form.addRow("", self.df_dry_run)
        form.addRow("", self.df_zip)
        form.addRow("", self.df_pdf)
        form.addRow("", self.df_resubmit)
        form.addRow("timeout_sec", self.df_timeout)
        form.addRow("form_ids", self.df_form_ids)
        form.addRow("email_success_event_to", self.df_email_success_event_to)
        form.addRow("email_failure_event_to", self.df_email_failure_event_to)
        box = QGroupBox("DocuForms2 IGRT")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "After analysis, winstonlutz can push the case to DocuForms2 "
                "(same payload as _ref_projects/docuforms_import/scripts/upload_igrt). "
                "form_ids maps machine NAME to a DocuForms2 form (one per line: "
                "Edge = sb_edge_mlc_wl). A machine with no row is skipped. "
                "Successful uploads write .docuforms2_igrt.json in the case folder so the "
                "watcher does not submit twice. Cases are not moved. "
                "email_success_event_to gets ok / dry-run; email_failure_event_to gets failed "
                "(clinic SMTP from Notifications). Skipped cases are not emailed. "
                "More PostProcessing types can be added later in JSON."
            )
        )
        layout.addWidget(box)
        layout.addStretch(1)
        return _scroll_page(inner)

    def _identity_group(self) -> QGroupBox:
        form = QFormLayout()
        form.addRow("user_id_method", self.user_id_method)
        form.addRow("OS user (this PC)", self.os_user_preview)
        form.addRow("issuer", self.oidc_issuer)
        form.addRow("client_id", self.oidc_client_id)
        form.addRow("scopes", self.oidc_scopes)
        form.addRow("redirect_uri", self.oidc_redirect)
        form.addRow("registration_url", self.oidc_registration)
        form.addRow("", self.oidc_register_btn)
        box = QGroupBox("Identity")
        root = QVBoxLayout(box)
        root.addLayout(form)
        root.addWidget(
            _hint_label(
                "None: no user id. OSUser: Windows / Linux / macOS login (no extra prompt). "
                "OIDC is the protocol; Keycloak is the issuer. No separate Keycloak method. "
                "At startup the app opens a Sign in window, then your browser (Authorization Code + PKCE). "
                "Create a dedicated public client (winstonlutz), not account-console. "
                "Add redirect_uri exactly to Valid redirect URIs; use 127.0.0.1, not localhost "
                "(http://127.0.0.1:17843/callback). Standard flow + PKCE S256. "
                "registration_url is the Account Console ({issuer}/account/), not "
                "/protocol/openid-connect/registrations. Profiles go under _users."
            )
        )
        self._sync_identity_ui()
        return box

    def _open_oidc_registration(self) -> None:
        issuer = self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER
        url = coerce_registration_url(self.oidc_registration.text(), issuer)
        self.oidc_registration.setText(url)
        QDesktopServices.openUrl(QUrl(url))

    def _sync_identity_ui(self, _text: str = "") -> None:
        method = self.user_id_method.currentText() or USER_ID_NONE
        oidc_on = method == USER_ID_OIDC
        for widget in (
            self.oidc_issuer,
            self.oidc_client_id,
            self.oidc_scopes,
            self.oidc_redirect,
            self.oidc_registration,
            self.oidc_register_btn,
        ):
            widget.setEnabled(oidc_on)
        if method == USER_ID_OSUSER:
            self.os_user_preview.setPlainText(format_os_user_preview())
        elif method == USER_ID_OIDC:
            issuer = self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER
            self.os_user_preview.setPlainText(
                "At startup you will get a Sign in window, then Keycloak in the browser.\n"
                f"issuer: {issuer}\n"
                f"client_id: {self.oidc_client_id.text().strip() or DEFAULT_OIDC_CLIENT_ID}\n"
                f"redirect_uri: {self.oidc_redirect.text().strip() or DEFAULT_OIDC_REDIRECT_URI}\n"
                "That redirect_uri must be allowed on the Keycloak client."
            )
        else:
            self.os_user_preview.setPlainText("No user id (clinic-wide settings only).")

    def _notify_group(self, title: str, form: QFormLayout) -> QGroupBox:
        box = QGroupBox(title)
        box.setLayout(form)
        return box

    def _email_form(self) -> QFormLayout:
        form = QFormLayout()
        form.addRow("error_email_to", self.error_email_to)
        form.addRow("event_email_to", self.event_email_to)
        form.addRow("new_case_email_to", self.notify_new_case_email_to)
        form.addRow("email_from", self.email_from)
        form.addRow("email_domain", self.email_domain)
        form.addRow("email_host_address", self.email_host)
        form.addRow("email_host_port", self.email_port)
        form.addRow("", self.email_ssl)
        hint = QLabel(
            "One address per line (JSON arrays). Leave a list empty to skip that mail. "
            "error_email_to: crashes and analysis failures. "
            "event_email_to: watcher start/stop. "
            "new_case_email_to: full IGRT report after watcher analysis (clinic-wide; "
            "a machine may add extra addresses). SMTP also needs email_from and email_host_address."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")
        form.addRow("", hint)
        form.addRow("", self._notify_test_button("Send test email", self._test_email))
        return form

    def _webhook_form(self, edit: QLineEdit, hint_text: str, channel: str) -> QFormLayout:
        form = QFormLayout()
        form.addRow("webhook_url", edit)
        hint = QLabel(hint_text)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")
        form.addRow("", hint)
        label = CHAT_CHANNEL_LABELS.get(channel, channel)
        form.addRow(
            "",
            self._notify_test_button(
                f"Send test to {label}",
                partial(self._test_chat, channel),
            ),
        )
        return form

    def _notify_test_button(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.clicked.connect(lambda *_args: self._run_notify_test(text, slot, btn))
        return btn

    def _run_notify_test(self, title: str, work, button: QPushButton) -> None:
        button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            work()
            ok, message = True, "Test message sent."
        except Exception as exc:
            ok, message = False, str(exc) or "Send failed."
        finally:
            QApplication.restoreOverrideCursor()
            button.setEnabled(True)
        if ok:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def _test_email(self) -> None:
        send_test_email(self.collected())

    def _test_chat(self, channel: str) -> None:
        send_test_chat(channel, self.collected())

    def _notifications_page(self) -> QWidget:
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(self._notify_group("Email", self._email_form()))
        layout.addWidget(
            self._notify_group(
                "Google Chat",
                self._webhook_form(
                    self.google_chat_url,
                    "Incoming webhook for a Google Chat space. Empty = off. "
                    "Errors are posted as text.",
                    "google_chat",
                ),
            )
        )
        layout.addWidget(
            self._notify_group(
                "Slack",
                self._webhook_form(
                    self.slack_url,
                    "Incoming webhook for a Slack channel. Empty = off.",
                    "slack",
                ),
            )
        )
        layout.addWidget(
            self._notify_group(
                "Microsoft Teams",
                self._webhook_form(
                    self.teams_url,
                    "Incoming webhook (Workflows or Office 365 connector). Empty = off.",
                    "microsoft_teams",
                ),
            )
        )
        layout.addWidget(
            self._notify_group(
                "Discord",
                self._webhook_form(
                    self.discord_url,
                    "Channel webhook. Empty = off.",
                    "discord",
                ),
            )
        )
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
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
        ident = identity_block(self._original)
        ident["user_id_method"] = self.user_id_method.currentText() or USER_ID_NONE
        ident["oidc"] = {
            "issuer": self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER,
            "client_id": self.oidc_client_id.text().strip() or DEFAULT_OIDC_CLIENT_ID,
            "scopes": self.oidc_scopes.text().strip() or DEFAULT_OIDC_SCOPES,
            "redirect_uri": self.oidc_redirect.text().strip() or DEFAULT_OIDC_REDIRECT_URI,
            "registration_url": coerce_registration_url(
                self.oidc_registration.text().strip(),
                self.oidc_issuer.text().strip() or DEFAULT_OIDC_ISSUER,
            ),
        }
        data[IDENTITY_KEY] = ident
        data[WATCHER_KEY] = {
            "watch_path": self.watch_path.text().strip(),
            "winstonlutz_data_root": self.watch_data_root.text().strip(),
            "watch_subfolders": self.watch_recursive.isChecked(),
            "new_case_file_patterns": normalize_file_patterns(self.watch_file_patterns.toPlainText()),
            "case_folder_name_regex": self.watch_case_folder_regex.text().strip(),
            "machine_to_case_dir_levels": self.watch_case_dir_levels.value(),
            "queued_case_poll_sec": self.watch_poll_sec.value(),
            "disk_scan_for_new_case_detection": self.watch_disk_scan.isChecked(),
            "disk_scan_for_new_case_detection_sec": self.watch_disk_scan_sec.value(),
            "archive_old_cases": self.watch_archive.isChecked(),
            "archive_cases_older_than_days": self.watch_archive_age_days.value(),
            "archive_old_cases_at": self.watch_archive_at.time().toString("HH:mm"),
        }
        email_to = error_email_to_list(self.error_email_to.toPlainText())
        event_to = error_email_to_list(self.event_email_to.toPlainText())
        new_case_to = error_email_to_list(self.notify_new_case_email_to.toPlainText())
        email_from = self.email_from.text().strip()
        email_domain = self.email_domain.text().strip()
        email_host = self.email_host.text().strip()
        email_port = self.email_port.value()
        email_ssl = self.email_ssl.isChecked()
        enc = str(self._original.get("email_from_enc_pw") or "")
        notes_in = self._original.get(NOTIFICATIONS_KEY)
        if isinstance(notes_in, dict):
            nested_email = notes_in.get("email")
            if isinstance(nested_email, dict):
                enc = str(nested_email.get("email_from_enc_pw") or enc)
        for key in TOP_LEVEL_EMAIL_KEYS:
            data.pop(key, None)
        data[NOTIFICATIONS_KEY] = {
            "email": {
                ERROR_EMAIL_TO_KEY: email_to,
                EVENT_EMAIL_TO_KEY: event_to,
                NEW_CASE_EMAIL_TO_KEY: new_case_to,
                "email_from": email_from,
                "email_domain": email_domain,
                "email_host_address": email_host,
                "email_host_port": email_port,
                "enable_ssl": email_ssl,
                "email_from_enc_pw": enc,
            },
            "google_chat": {"webhook_url": self.google_chat_url.text().strip()},
            "slack": {"webhook_url": self.slack_url.text().strip()},
            "microsoft_teams": {"webhook_url": self.teams_url.text().strip()},
            "discord": {"webhook_url": self.discord_url.text().strip()},
        }
        df_step = {
            **default_docuforms2_igrt_step(),
            "enabled": self.df_enabled.isChecked(),
            "backend_url": self.df_backend.text().strip(),
            "verify_ssl": self.df_verify_ssl.isChecked(),
            "dry_run": self.df_dry_run.isChecked(),
            "attach_dcm_zip": self.df_zip.isChecked(),
            "attach_pdf": self.df_pdf.isChecked(),
            "resubmit": self.df_resubmit.isChecked(),
            "timeout_sec": self.df_timeout.value(),
            "form_ids": parse_form_ids_text(self.df_form_ids.toPlainText()),
            "email_success_event_to": error_email_to_list(
                self.df_email_success_event_to.toPlainText()
            ),
            "email_failure_event_to": error_email_to_list(
                self.df_email_failure_event_to.toPlainText()
            ),
        }
        data[POST_PROCESSING_KEY] = upsert_post_step(
            post_processing_steps(self._original), df_step
        )
        machines = copy.deepcopy(self._machines)
        for machine in machines:
            if isinstance(machine, dict):
                machine.pop("docuforms2_form_id", None)
        data[MACHINES_KEY] = machines
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
        if get_user_id_method(data) == USER_ID_OSUSER:
            upsert_os_user_profile()
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
