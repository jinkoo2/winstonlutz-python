"""SMTP helper. Decrypt is optional; empty password sends without credentials."""

from __future__ import annotations

import html
import logging
import platform
import smtplib
import sys
import threading
import traceback
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
from pathlib import Path

from .config import Param

logger = logging.getLogger(__name__)

PASSPHRASE = os.environ.get("WINSTONLUTZ_EMAIL_PASSPHRASE", "")
_sending_error_email = False


def recipient_addresses(to: str, domain: str = "") -> list[str]:
    """Split a comma list into addresses. Local parts get ``@domain``."""
    domain = str(domain or "").strip().lstrip("@")
    out: list[str] = []
    for part in str(to or "").split(","):
        name = part.strip()
        if not name:
            continue
        if "@" in name:
            out.append(name)
        elif domain:
            out.append(f"{name}@{domain}")
    return out


def decrypt_password(cipher_text: str, pass_phrase: str = PASSPHRASE) -> str:
    if not cipher_text.strip():
        return ""
    try:
        from Crypto.Cipher import AES
        from Crypto.Protocol.KDF import PBKDF2
    except Exception as exc:
        raise RuntimeError("pycryptodome is required to decrypt email passwords") from exc

    raw = __import__("base64").b64decode(cipher_text)
    salt, iv, data = raw[:32], raw[32:64], raw[64:]
    key = PBKDF2(pass_phrase, salt, dkLen=32, count=1000)
    # C# RijndaelManaged BlockSize=256; try AES-256 first, then Rijndael-256.
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv[:16])
        plain = cipher.decrypt(data)
        pad = plain[-1]
        return plain[:-pad].decode("utf-8")
    except Exception:
        try:
            from Crypto.Cipher import AES as _AES

            cipher = _AES.new(key, _AES.MODE_CBC, iv)
            plain = cipher.decrypt(data)
            pad = plain[-1]
            return plain[:-pad].decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Could not decrypt email password") from exc


def send(
    from_user: str,
    from_enc_pw: str,
    to: str,
    subject: str,
    body: str,
    domain: str,
    host: str,
    port: int,
    enable_ssl: bool,
    attachments: list[str] | None = None,
) -> None:
    if not from_user or not to or not host:
        logger.info("email skipped: missing from/to/host")
        return

    msg = MIMEMultipart()
    from_addr = from_user if "@" in from_user else f"{from_user}@{domain}"
    msg["From"] = from_addr
    addresses = recipient_addresses(to, domain)
    if not addresses:
        logger.info("email skipped: no recipients")
        return
    msg["To"] = ", ".join(addresses)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))
    for path in attachments or []:
        data = Path(path).read_bytes()
        part = MIMEApplication(data, Name=Path(path).name)
        part["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        msg.attach(part)

    smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        if enable_ssl:
            smtp.starttls()
        if from_enc_pw.strip():
            smtp.login(from_addr, decrypt_password(from_enc_pw))
        smtp.sendmail(from_addr, addresses, msg.as_string())
    finally:
        smtp.quit()


def send_from_config(param: Param, subject: str, body: str, to_key: str = "email_to") -> None:
    send(
        from_user=param.get_value("email_from"),
        from_enc_pw=param.get_value("email_from_enc_pw"),
        to=param.get_value(to_key),
        subject=subject,
        body=body,
        domain=param.get_value("email_domain"),
        host=param.get_value("email_host_address"),
        port=param.get_int("email_host_port", 25) or 25,
        enable_ssl=param.get_bool("enable_ssl", False),
    )


def error_email_settings(data: dict | None = None) -> dict | None:
    """Return SMTP fields if ``error_email_to`` is set; otherwise None."""
    from .app_settings import ERROR_EMAIL_TO_KEY, load_gui_settings

    settings = data if data is not None else load_gui_settings()
    to = str((settings or {}).get(ERROR_EMAIL_TO_KEY) or "").strip()
    if not to:
        return None
    host = str(settings.get("email_host_address") or "").strip()
    from_user = str(settings.get("email_from") or "").strip()
    if not host or not from_user:
        return None
    port = settings.get("email_host_port", 25)
    try:
        port_n = int(port)
    except (TypeError, ValueError):
        port_n = 25
    ssl = settings.get("enable_ssl", False)
    if isinstance(ssl, str):
        ssl = ssl.strip().lower() in ("true", "1", "yes")
    return {
        "to": to,
        "from_user": from_user,
        "from_enc_pw": str(settings.get("email_from_enc_pw") or ""),
        "domain": str(settings.get("email_domain") or ""),
        "host": host,
        "port": port_n,
        "enable_ssl": bool(ssl),
    }


def _error_email_body(message: str, context: str, details: str) -> str:
    from . import __version__

    safe = html.escape(details or message or "")
    meta = (
        f"Winston-Lutz {html.escape(__version__)}\n"
        f"host={html.escape(platform.node())}\n"
        f"argv={html.escape(' '.join(sys.argv))}\n"
        f"context={html.escape(context or '')}\n"
        f"error={html.escape(message or '')}\n"
    )
    return (
        "<html><body>"
        f"<pre>{html.escape(meta)}</pre>"
        f"<pre>{safe}</pre>"
        "</body></html>"
    )


def send_error_email(
    message: str,
    details: str = "",
    *,
    context: str = "",
    blocking: bool = False,
) -> None:
    """Email an exception if ``error_email_to`` is set. Failures are ignored."""
    global _sending_error_email
    if _sending_error_email:
        return
    cfg = error_email_settings()
    if cfg is None:
        return

    def _run() -> None:
        global _sending_error_email
        _sending_error_email = True
        try:
            send(
                from_user=cfg["from_user"],
                from_enc_pw=cfg["from_enc_pw"],
                to=cfg["to"],
                subject=f"Winston-Lutz error: {message[:120] or context or 'exception'}",
                body=_error_email_body(message, context, details),
                domain=cfg["domain"],
                host=cfg["host"],
                port=cfg["port"],
                enable_ssl=cfg["enable_ssl"],
            )
        except Exception:
            logger.warning("error email failed", exc_info=True)
        finally:
            _sending_error_email = False

    if blocking:
        _run()
        return
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        return


class ExceptionEmailHandler(logging.Handler):
    """Email ``logger.exception`` records when error_email_to is configured."""

    def emit(self, record: logging.LogRecord) -> None:
        if not record.exc_info:
            return
        try:
            typ, val, tb = record.exc_info
            details = "".join(traceback.format_exception(typ, val, tb))
            send_error_email(
                record.getMessage(),
                details,
                context=record.name,
                blocking=False,
            )
        except Exception:
            return


def install_error_email_hooks() -> None:
    """sys.excepthook, thread crashes, and winstonlutz logger.exception emails."""
    if "pytest" in sys.modules:
        return
    root_wl = logging.getLogger("winstonlutz")
    if not any(isinstance(h, ExceptionEmailHandler) for h in root_wl.handlers):
        handler = ExceptionEmailHandler()
        handler.setLevel(logging.ERROR)
        root_wl.addHandler(handler)

    def _hook(exc_type, exc, tb) -> None:
        try:
            details = "".join(traceback.format_exception(exc_type, exc, tb))
            send_error_email(str(exc) or exc_type.__name__, details, context="uncaught", blocking=True)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    if hasattr(threading, "excepthook"):
        def _thread_hook(args) -> None:
            try:
                details = "".join(
                    traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
                )
                send_error_email(
                    str(args.exc_value) or args.exc_type.__name__,
                    details,
                    context=f"thread:{getattr(args.thread, 'name', '')}",
                    blocking=True,
                )
            except Exception:
                pass

        threading.excepthook = _thread_hook
