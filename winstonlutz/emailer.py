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
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import re
from pathlib import Path

from .config import Param

logger = logging.getLogger(__name__)

PASSPHRASE = os.environ.get("WINSTONLUTZ_EMAIL_PASSPHRASE", "")
_sending_error_email = False


def error_email_to_list(value) -> list[str]:
    """Normalize ``error_email_to`` (string, comma list, or JSON array) to addresses."""
    chunks: list[str] = []
    if isinstance(value, (list, tuple)):
        chunks.extend(str(item or "") for item in value)
    elif value is not None:
        chunks.append(str(value))
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in chunk.replace(";", "\n").splitlines():
            for part in line.split(","):
                name = part.strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
    return out


def format_error_email_to(value) -> str:
    """One address per line for the Settings editor."""
    return "\n".join(error_email_to_list(value))


def recipient_addresses(to: str | list | None, domain: str = "") -> list[str]:
    """Resolve ``error_email_to`` entries. Local parts get ``@domain``."""
    domain = str(domain or "").strip().lstrip("@")
    out: list[str] = []
    for name in error_email_to_list(to):
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
    to: str | list | None,
    subject: str,
    body: str,
    domain: str,
    host: str,
    port: int,
    enable_ssl: bool,
    attachments: list[str] | None = None,
    inline_images: list[tuple[str, Path]] | None = None,
) -> None:
    if not from_user or not host:
        logger.info("email skipped: missing from/to/host")
        return

    addresses = recipient_addresses(to, domain)
    if not addresses:
        logger.info("email skipped: no recipients")
        return

    from_addr = from_user if "@" in from_user else f"{from_user}@{domain}"
    related = MIMEMultipart("related")
    related.attach(MIMEText(body, "html"))
    for cid, image_path in inline_images or []:
        path = Path(image_path)
        if not path.is_file():
            continue
        subtype = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif"}.get(
            path.suffix.lower(), "png"
        )
        part = MIMEImage(path.read_bytes(), _subtype=subtype)
        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", "inline", filename=path.name)
        related.attach(part)

    extra = [Path(p) for p in (attachments or []) if Path(p).is_file()]
    if extra:
        msg = MIMEMultipart("mixed")
        msg.attach(related)
        for path in extra:
            part = MIMEApplication(path.read_bytes(), Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)
    else:
        msg = related
    msg["From"] = from_addr
    msg["To"] = ", ".join(addresses)
    msg["Subject"] = subject

    smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        if enable_ssl:
            smtp.starttls()
        if from_enc_pw.strip():
            smtp.login(from_addr, decrypt_password(from_enc_pw))
        smtp.sendmail(from_addr, addresses, msg.as_string())
    finally:
        smtp.quit()


def resolve_report_image(case_dir: Path, src: str) -> Path | None:
    text = str(src or "").strip()
    if not text or text.lower().startswith("cid:") or "://" in text:
        return None
    text = text.replace("\\", "/")
    path = Path(text)
    if path.is_absolute() and path.is_file():
        return path
    candidate = (Path(case_dir) / text).resolve()
    if candidate.is_file():
        return candidate
    return None


def html_with_cid_images(html: str, case_dir: str | Path) -> tuple[str, list[tuple[str, Path]]]:
    """Rewrite local <img src> to cid: and return (html, [(cid, path), ...])."""
    case_dir = Path(case_dir)
    images: list[tuple[str, Path]] = []
    by_path: dict[str, str] = {}
    pattern = re.compile(r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])', re.IGNORECASE)

    def repl(match: re.Match) -> str:
        src = match.group(2)
        path = resolve_report_image(case_dir, src)
        if path is None:
            return match.group(0)
        key = str(path)
        cid = by_path.get(key)
        if cid is None:
            cid = f"wl{len(by_path)}"
            by_path[key] = cid
            images.append((cid, path))
        return f"{match.group(1)}cid:{cid}{match.group(3)}"

    return pattern.sub(repl, html), images


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


def smtp_settings(data: dict | None = None) -> dict | None:
    """SMTP from/host for outgoing mail. Does not require error_email_to."""
    from .app_settings import email_settings_block, load_gui_settings

    raw = data if data is not None else load_gui_settings()
    settings = email_settings_block(raw)
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
        "from_user": from_user,
        "from_enc_pw": str(settings.get("email_from_enc_pw") or ""),
        "domain": str(settings.get("email_domain") or ""),
        "host": host,
        "port": port_n,
        "enable_ssl": bool(ssl),
    }


def merge_address_lists(*values) -> list[str]:
    """Deduped addresses from one or more ``error_email_to``-style lists."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for name in error_email_to_list(value):
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def smtp_list_settings(to_key: str, data: dict | None = None) -> dict | None:
    """Return SMTP fields plus ``to`` if *to_key* has addresses; otherwise None."""
    from .app_settings import email_settings_block, load_gui_settings

    raw = data if data is not None else load_gui_settings()
    smtp = smtp_settings(raw)
    if smtp is None:
        return None
    settings = email_settings_block(raw)
    to = error_email_to_list((settings or {}).get(to_key))
    if not to:
        return None
    return {**smtp, "to": to}


def error_email_settings(data: dict | None = None) -> dict | None:
    """Return SMTP fields if ``error_email_to`` is set; otherwise None."""
    from .app_settings import ERROR_EMAIL_TO_KEY

    return smtp_list_settings(ERROR_EMAIL_TO_KEY, data)


def event_email_settings(data: dict | None = None) -> dict | None:
    """Return SMTP fields if ``event_email_to`` is set; otherwise None."""
    from .app_settings import EVENT_EMAIL_TO_KEY

    return smtp_list_settings(EVENT_EMAIL_TO_KEY, data)


def igrt_report_recipients(machine_cfg: dict | None = None, data: dict | None = None) -> list[str]:
    """System-wide ``Notifications.email.new_case_email_to``, plus optional per-machine extras."""
    from .app_settings import NEW_CASE_EMAIL_TO_KEY, email_settings_block, load_gui_settings

    raw = data if data is not None else load_gui_settings()
    settings = email_settings_block(raw)
    return merge_address_lists(
        (settings or {}).get(NEW_CASE_EMAIL_TO_KEY),
        (machine_cfg or {}).get(NEW_CASE_EMAIL_TO_KEY),
    )


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


def _post_webhook(url: str, payload: dict) -> None:
    import json
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()[:300]
        except Exception:
            pass
        extra = f": {detail}" if detail else ""
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}{extra}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"webhook failed: {exc.reason}") from exc


def _error_plain_text(message: str, context: str, details: str) -> str:
    from . import __version__

    return (
        f"Winston-Lutz {__version__} error\n"
        f"host={platform.node()}\n"
        f"argv={' '.join(sys.argv)}\n"
        f"context={context or ''}\n"
        f"error={message or ''}\n\n"
        f"{details or ''}"
    )


def post_chat_webhooks(
    message: str,
    details: str = "",
    *,
    context: str = "",
    data: dict | None = None,
) -> None:
    from .app_settings import chat_webhook_urls

    text = _error_plain_text(message, context, details)
    hooks = chat_webhook_urls(data)
    for name in ("google_chat", "slack", "microsoft_teams", "discord"):
        url = hooks.get(name) or ""
        if url:
            try:
                _post_webhook(url, _chat_payload(name, text))
            except Exception:
                logger.warning("chat webhook %s failed", name)


CHAT_CHANNEL_LABELS = {
    "google_chat": "Google Chat",
    "slack": "Slack",
    "microsoft_teams": "Microsoft Teams",
    "discord": "Discord",
}


def _chat_payload(channel: str, text: str) -> dict:
    if channel == "discord":
        clipped = text if len(text) <= 1900 else text[:1900] + "\n…"
        return {"content": clipped}
    clipped = text if len(text) <= 3500 else text[:3500] + "\n…"
    return {"text": clipped}


def _test_plain_text(channel_label: str) -> str:
    from . import __version__

    return (
        f"Winston-Lutz {__version__} notification test\n"
        f"host={platform.node()}\n"
        f"channel={channel_label}\n"
        "This is a configuration test from Settings → Notifications."
    )


def send_test_email(data: dict | None = None) -> None:
    """Send a test email using ``data`` (unsaved dialog values). Raises on failure."""
    cfg = error_email_settings(data)
    if cfg is None:
        raise RuntimeError(
            "Fill error_email_to, email_from, and email_host_address first."
        )
    if not recipient_addresses(cfg["to"], cfg["domain"]):
        raise RuntimeError(
            "error_email_to has no valid address (use full emails or set email_domain)."
        )
    try:
        send(
            from_user=cfg["from_user"],
            from_enc_pw=cfg["from_enc_pw"],
            to=cfg["to"],
            subject="Winston-Lutz notification test",
            body=_error_email_body(
                "Notification test",
                "settings-test",
                _test_plain_text("Email"),
            ),
            domain=cfg["domain"],
            host=cfg["host"],
            port=cfg["port"],
            enable_ssl=cfg["enable_ssl"],
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"SMTP failed: {exc}") from exc


def send_test_chat(channel: str, data: dict | None = None) -> None:
    """Post a test message to one chat webhook. Raises on failure."""
    from .app_settings import chat_webhook_urls

    label = CHAT_CHANNEL_LABELS.get(channel, channel)
    url = (chat_webhook_urls(data).get(channel) or "").strip()
    if not url:
        raise RuntimeError(f"Enter a {label} webhook_url first.")
    _post_webhook(url, _chat_payload(channel, _test_plain_text(label)))


def send_error_email(
    message: str,
    details: str = "",
    *,
    context: str = "",
    blocking: bool = False,
) -> None:
    """Email and/or post chat webhooks if those channels are set. Failures are ignored."""
    global _sending_error_email
    if _sending_error_email:
        return
    from .app_settings import chat_webhook_urls

    cfg = error_email_settings()
    hooks = chat_webhook_urls()
    if cfg is None and not any(hooks.values()):
        return

    def _run() -> None:
        global _sending_error_email
        _sending_error_email = True
        try:
            if cfg is not None:
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
            post_chat_webhooks(message, details, context=context)
        except Exception:
            logger.warning("error notification failed", exc_info=True)
        finally:
            _sending_error_email = False

    if blocking:
        _run()
        return
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        return


def send_list_email(
    to,
    message: str,
    details: str = "",
    *,
    context: str = "event",
    subject: str | None = None,
    blocking: bool = True,
    data: dict | None = None,
) -> None:
    """Send *message* to an explicit address list using clinic SMTP."""
    smtp = smtp_settings(data)
    addresses = error_email_to_list(to)
    if smtp is None or not addresses:
        return
    subj = subject or f"Winston-Lutz event: {(message or context)[:120]}"

    def _run() -> None:
        try:
            send(
                from_user=smtp["from_user"],
                from_enc_pw=smtp["from_enc_pw"],
                to=addresses,
                subject=subj,
                body=_error_email_body(message, context, details),
                domain=smtp["domain"],
                host=smtp["host"],
                port=smtp["port"],
                enable_ssl=smtp["enable_ssl"],
            )
        except Exception:
            logger.warning("list email failed", exc_info=True)

    if blocking:
        _run()
        return
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        return


def send_event_email(
    message: str,
    details: str = "",
    *,
    context: str = "event",
    blocking: bool = True,
) -> None:
    """Email ``event_email_to`` for service start/stop and other system events."""
    cfg = event_email_settings()
    if cfg is None:
        return
    send_list_email(
        cfg["to"],
        message,
        details,
        context=context,
        blocking=blocking,
    )


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
