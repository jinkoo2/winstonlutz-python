"""Per-user email, My Machines, and new-QA-case notification subscriptions."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .identity import (
    NOTIFY_NEW_CASE_ALL_MACHINES,
    NOTIFY_NEW_CASE_MY_MACHINES,
    NOTIFY_NEW_CASE_OFF,
    configured_machine_names,
    current_user_profile,
    looks_like_email,
    merge_subscriptions,
    new_qa_case_scope,
    normalize_my_machines,
    update_current_user_profile,
    user_needs_email,
)


class UserSettingsDialog(QDialog):
    def __init__(self, parent=None, *, prompt_email: bool = False):
        super().__init__(parent)
        self.setWindowTitle("User settings")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(520, 560)
        self.did_save = False
        profile = current_user_profile() or {}

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #b45309;")
        if prompt_email or user_needs_email():
            self.hint.setText(
                "This login has no email. Enter one here so you can receive QA case notifications."
            )
        else:
            self.hint.hide()

        self.method = QLabel(str(profile.get("method") or "—"))
        self.user_id = QLabel(str(profile.get("id") or "—"))
        self.user_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.user_id.setWordWrap(True)
        self.display_name = QLineEdit(str(profile.get("display_name") or ""))
        self.email = QLineEdit(str(profile.get("email") or ""))
        self.email.setPlaceholderText("you@hospital.edu")

        self.machines = QListWidget()
        self.machines.setSelectionMode(QListWidget.NoSelection)
        selected = set(normalize_my_machines(profile.get("my_machines")))
        for name in configured_machine_names():
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.machines.addItem(item)
        if self.machines.count() == 0:
            empty = QListWidgetItem("No machines in clinic Settings yet.")
            empty.setFlags(Qt.NoItemFlags)
            self.machines.addItem(empty)

        self.notify_case = QComboBox()
        self.notify_case.addItem("Off", NOTIFY_NEW_CASE_OFF)
        self.notify_case.addItem("My machines", NOTIFY_NEW_CASE_MY_MACHINES)
        self.notify_case.addItem("All machines", NOTIFY_NEW_CASE_ALL_MACHINES)
        scope = new_qa_case_scope(profile)
        idx = self.notify_case.findData(scope)
        self.notify_case.setCurrentIndex(idx if idx >= 0 else 0)

        form = QFormLayout()
        form.addRow("Method", self.method)
        form.addRow("User id", self.user_id)
        form.addRow("Display name", self.display_name)
        form.addRow("Email", self.email)
        form.addRow("My machines", self.machines)
        form.addRow("New QA case emails", self.notify_case)
        note = QLabel(
            "New QA case emails use the clinic SMTP on Settings → Notifications. "
            "Choose My machines only for the list above, or All machines for every linac."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748b;")

        save_btn = QPushButton("Save")
        close_btn = QPushButton("Close")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        close_btn.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        buttons.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addLayout(buttons)

    def _checked_machines(self) -> list[str]:
        names: list[str] = []
        for row in range(self.machines.count()):
            item = self.machines.item(row)
            if item is None or not (item.flags() & Qt.ItemIsUserCheckable):
                continue
            if item.checkState() == Qt.Checked:
                names.append(item.text())
        return names

    def _save(self) -> None:
        email = self.email.text().strip()
        scope = str(self.notify_case.currentData() or NOTIFY_NEW_CASE_OFF)
        machines = self._checked_machines()
        if scope != NOTIFY_NEW_CASE_OFF and not looks_like_email(email):
            QMessageBox.warning(
                self,
                "User settings",
                "Enter a valid email address before subscribing to new QA case emails.",
            )
            self.email.setFocus()
            return
        if scope == NOTIFY_NEW_CASE_MY_MACHINES and not machines:
            QMessageBox.warning(
                self,
                "User settings",
                "Select at least one machine under My machines, or choose All machines / Off.",
            )
            return
        profile = current_user_profile() or {}
        subs = merge_subscriptions(profile.get("subscriptions"))
        subs["new_qa_case"] = scope
        if looks_like_email(email):
            subs["email"] = True
        try:
            update_current_user_profile(
                {
                    "email": email,
                    "display_name": self.display_name.text().strip(),
                    "my_machines": machines,
                    "subscriptions": subs,
                }
            )
        except ValueError:
            QMessageBox.warning(self, "User settings", "No signed-in user.")
            return
        self.did_save = True
        self.accept()
