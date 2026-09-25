"""SMTP helper. Decrypt is optional; empty password sends without credentials."""

from __future__ import annotations

import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
from pathlib import Path

from .config import Param

logger = logging.getLogger(__name__)

PASSPHRASE = os.environ.get("WINSTONLUTZ_EMAIL_PASSPHRASE", "")


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
    msg["From"] = f"{from_user}@{domain}"
    addresses = [f"{name.strip()}@{domain.strip()}" for name in to.split(",") if name.strip()]
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
            smtp.login(f"{from_user}@{domain}", decrypt_password(from_enc_pw))
        smtp.sendmail(msg["From"], addresses, msg.as_string())
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
