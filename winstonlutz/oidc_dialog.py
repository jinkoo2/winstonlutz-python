"""Modal OIDC sign-in at GUI startup."""

from __future__ import annotations

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .app_settings import get_institution
from .identity import oidc_settings
from .oidc import login_oidc


class OidcLoginDialog(QDialog):
    def __init__(self, parent=None, *, can_quit: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Winston-Lutz — Sign in")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self.resize(480, 240)
        cfg = oidc_settings()
        institution = get_institution()
        title = QLabel("Sign in with Keycloak")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        intro = QLabel(
            (f"{institution}\n" if institution else "")
            + "A browser window will open for your myphysics account.\n"
            + f"issuer: {cfg.get('issuer') or '—'}\n"
            + f"client_id: {cfg.get('client_id') or '—'}"
        )
        intro.setWordWrap(True)
        self.status = QLabel("Click Sign in to continue.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #64748b;")

        self.sign_in = QPushButton("Sign in")
        self.register = QPushButton("Create account")
        self.quit_btn = QPushButton("Quit" if can_quit else "Cancel")
        self.sign_in.setDefault(True)
        self.sign_in.clicked.connect(self._sign_in)
        self.register.clicked.connect(self._register)
        self.quit_btn.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.register)
        buttons.addStretch(1)
        buttons.addWidget(self.quit_btn)
        buttons.addWidget(self.sign_in)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(self.status, 1)
        layout.addLayout(buttons)

    def _busy(self, busy: bool) -> None:
        self.sign_in.setEnabled(not busy)
        self.register.setEnabled(not busy)
        self.quit_btn.setEnabled(not busy)

    def _sign_in(self) -> None:
        self._busy(True)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            profile = login_oidc(
                on_status=self.status.setText,
                pump_events=QApplication.processEvents,
            )
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._busy(False)
            self.status.setText(str(exc) or "Sign-in failed.")
            QMessageBox.warning(
                self,
                "Sign in",
                str(exc)
                or "Sign-in failed. On Keycloak, add this app's redirect_uri to the client's "
                "Valid redirect URIs (see Settings → Identity).",
            )
            return
        QApplication.restoreOverrideCursor()
        self._busy(False)
        name = profile.get("display_name") or profile.get("username") or "signed in"
        self.status.setText(f"Signed in as {name}.")
        self.accept()

    def _register(self) -> None:
        url = oidc_settings().get("registration_url") or ""
        if url:
            QDesktopServices.openUrl(QUrl(url))
            self.status.setText("Registration opened in the browser. Sign in here when you have an account.")
