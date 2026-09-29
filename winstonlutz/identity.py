"""Who is using the app: None, OSUser (no extra login), or OIDC (Keycloak PKCE)."""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import socket
from pathlib import Path

from .app_settings import app_dir, load_gui_settings, user_config_path

IDENTITY_KEY = "Identity"
USER_ID_NONE = "None"
USER_ID_OSUSER = "OSUser"
USER_ID_OIDC = "OIDC"
USER_ID_METHODS = (USER_ID_NONE, USER_ID_OSUSER, USER_ID_OIDC)
USERS_DIRNAME = "_users"
NOTIFY_NEW_CASE_OFF = "off"
NOTIFY_NEW_CASE_MY_MACHINES = "my_machines"
NOTIFY_NEW_CASE_ALL_MACHINES = "all_machines"
NOTIFY_NEW_CASE_CHOICES = (
    NOTIFY_NEW_CASE_OFF,
    NOTIFY_NEW_CASE_MY_MACHINES,
    NOTIFY_NEW_CASE_ALL_MACHINES,
)
DEFAULT_OIDC_ISSUER = "https://login.apps.myphysics.net/realms/myphysics"
DEFAULT_OIDC_CLIENT_ID = "winstonlutz"
DEFAULT_OIDC_SCOPES = "openid profile email"
DEFAULT_OIDC_REDIRECT_URI = "http://127.0.0.1:17843/callback"
DEFAULT_OIDC_REGISTRATION_URL = "https://login.apps.myphysics.net/realms/myphysics/account/"

_os_facts_cache: dict | None = None

# Windows GetUserNameExW NameFormat
_WIN_NAME_SAM = 2
_WIN_NAME_DISPLAY = 3
_WIN_NAME_UPN = 8
_WIN_NAME_GIVEN = 13
_WIN_NAME_SURNAME = 14


def identity_block(data: dict | None = None) -> dict:
    settings = data if data is not None else load_gui_settings()
    block = (settings or {}).get(IDENTITY_KEY)
    return dict(block) if isinstance(block, dict) else {}


def get_user_id_method(data: dict | None = None) -> str:
    text = str(identity_block(data).get("user_id_method") or "").strip()
    for name in USER_ID_METHODS:
        if text.lower() == name.lower():
            return name
    return USER_ID_NONE


def keycloak_issuer(url: str, realm: str) -> str:
    base = str(url or "").strip().rstrip("/")
    name = str(realm or "").strip()
    if not base or not name:
        return ""
    return f"{base}/realms/{name}"


def default_registration_url(issuer: str = "") -> str:
    """Keycloak Account Console. The SPA starts OIDC with PKCE, then Register.

    Do not use /protocol/openid-connect/registrations against account-console:
    that client requires code_challenge_method (Keycloak 26).
    """
    base = str(issuer or DEFAULT_OIDC_ISSUER).rstrip("/")
    return f"{base}/account/"


def _is_pkce_less_account_console_registration(url: str) -> bool:
    lowered = (url or "").lower()
    return (
        "/protocol/openid-connect/registrations" in lowered
        and "client_id=account-console" in lowered
        and "code_challenge" not in lowered
    )


def coerce_registration_url(url: str, issuer: str = "") -> str:
    raw = str(url or "").strip()
    if not raw or _is_pkce_less_account_console_registration(raw):
        return default_registration_url(issuer)
    return raw


def oidc_settings(data: dict | None = None) -> dict:
    """Standard OIDC fields. Keycloak url+realm is only used to fill issuer if missing."""
    block = identity_block(data).get("oidc")
    raw = block if isinstance(block, dict) else {}
    issuer = str(raw.get("issuer") or "").strip()
    if not issuer:
        issuer = keycloak_issuer(
            str(raw.get("keycloak_url") or ""),
            str(raw.get("keycloak_realm") or ""),
        ) or DEFAULT_OIDC_ISSUER
    client_id = str(raw.get("client_id") or DEFAULT_OIDC_CLIENT_ID).strip() or DEFAULT_OIDC_CLIENT_ID
    scopes = str(raw.get("scopes") or DEFAULT_OIDC_SCOPES).strip() or DEFAULT_OIDC_SCOPES
    registration = coerce_registration_url(
        raw.get("registration_url") or raw.get("keycloak_registration_url") or "",
        issuer,
    )
    redirect = str(raw.get("redirect_uri") or DEFAULT_OIDC_REDIRECT_URI).strip() or DEFAULT_OIDC_REDIRECT_URI
    return {
        "issuer": issuer,
        "client_id": client_id,
        "scopes": scopes,
        "registration_url": registration,
        "redirect_uri": redirect,
        "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth" if issuer else "",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token" if issuer else "",
        "userinfo_endpoint": f"{issuer}/protocol/openid-connect/userinfo" if issuer else "",
    }


def default_subscriptions() -> dict:
    return {
        "email": False,
        "google_chat": False,
        "slack": False,
        "microsoft_teams": False,
        "discord": False,
        "new_qa_case": NOTIFY_NEW_CASE_OFF,
    }


def normalize_new_qa_case_scope(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in ("my", "my_machines", "mine"):
        return NOTIFY_NEW_CASE_MY_MACHINES
    if text in ("all", "all_machines"):
        return NOTIFY_NEW_CASE_ALL_MACHINES
    if value is True or text in ("email", "on", "true", "1"):
        return NOTIFY_NEW_CASE_MY_MACHINES
    return NOTIFY_NEW_CASE_OFF


def merge_subscriptions(raw) -> dict:
    merged = default_subscriptions()
    if not isinstance(raw, dict):
        return merged
    for key in merged:
        if key == "new_qa_case":
            merged[key] = normalize_new_qa_case_scope(raw.get(key))
        else:
            merged[key] = bool(raw.get(key))
    return merged


def normalize_my_machines(value) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def looks_like_email(text: str) -> bool:
    value = str(text or "").strip()
    if "@" not in value or " " in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain


def profile_email(profile: dict | None) -> str:
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("email") or "").strip()


def new_qa_case_scope(profile: dict | None) -> str:
    if not isinstance(profile, dict):
        return NOTIFY_NEW_CASE_OFF
    subs = profile.get("subscriptions")
    raw = subs.get("new_qa_case") if isinstance(subs, dict) else None
    return normalize_new_qa_case_scope(raw)


def configured_machine_names(data: dict | None = None) -> list[str]:
    from .app_settings import MACHINES_KEY, load_gui_settings, named_machines

    settings = data if data is not None else load_gui_settings()
    return [str(m.get("NAME") or "").strip() for m in named_machines(settings.get(MACHINES_KEY))]


def _attach_prefs(profile: dict, existing: dict) -> dict:
    profile["subscriptions"] = merge_subscriptions(existing.get("subscriptions"))
    profile["my_machines"] = normalize_my_machines(existing.get("my_machines"))
    return profile


def collect_os_user() -> dict:
    """Facts the OS can give without a separate login. Email is often empty."""
    global _os_facts_cache
    if _os_facts_cache is not None:
        return dict(_os_facts_cache)
    username = _username()
    system = platform.system()
    out: dict = {
        "username": username,
        "hostname": socket.gethostname(),
        "home": str(Path.home()),
        "os": system,
        "os_release": platform.release(),
        "machine": platform.machine(),
    }
    if system == "Windows":
        out.update(_windows_extras())
    else:
        out.update(_posix_extras())
        if system == "Darwin":
            out.update(_macos_extras(username))
    if not str(out.get("display_name") or "").strip():
        out["display_name"] = username
    if not str(out.get("email") or "").strip():
        out["email"] = _email_from_upn(out.get("user_principal_name") or "")
    _os_facts_cache = dict(out)
    return dict(out)


def os_user_id(facts: dict | None = None) -> str:
    facts = facts if facts is not None else collect_os_user()
    sam = str(facts.get("sam_compatible") or "").strip()
    if sam:
        return f"osuser:{sam}"
    user = str(facts.get("username") or "").strip() or "unknown"
    host = str(facts.get("hostname") or "").strip()
    if host:
        return f"osuser:{host}:{user}"
    return f"osuser:{user}"


def current_user_profile(data: dict | None = None) -> dict | None:
    """Active user record, or None when method is None (or OIDC until login exists)."""
    method = get_user_id_method(data)
    if method == USER_ID_NONE:
        return None
    if method == USER_ID_OSUSER:
        uid = os_user_id()
        return load_user_profile(uid) or upsert_os_user_profile()
    if method == USER_ID_OIDC:
        uid = str(identity_block(data).get("oidc_sub") or "").strip() or load_current_user_id()
        return load_user_profile(uid)
    return None


def current_user_label(data: dict | None = None) -> str:
    profile = current_user_profile(data)
    if not profile:
        return ""
    return str(profile.get("display_name") or profile.get("username") or "").strip()


def current_user_email(data: dict | None = None) -> str:
    return profile_email(current_user_profile(data))


def user_needs_email(data: dict | None = None) -> bool:
    """True when a signed-in OSUser/OIDC person has no usable email address."""
    method = get_user_id_method(data)
    if method not in (USER_ID_OSUSER, USER_ID_OIDC):
        return False
    profile = current_user_profile(data)
    if not profile:
        return False
    return not looks_like_email(profile_email(profile))


def session_operator(dicom_operator: str = "", data: dict | None = None) -> str:
    """DICOM OperatorsName if set, else OSUser / OIDC display name."""
    text = str(dicom_operator or "").strip()
    if text:
        return text
    return current_user_label(data)


def users_dir() -> Path:
    override = os.environ.get("WINSTONLUTZ_USERS_DIR", "").strip()
    if override:
        return Path(override)
    parent = user_config_path().parent if os.environ.get("WINSTONLUTZ_APP_CONFIG") else app_dir()
    return parent / USERS_DIRNAME


def user_profile_path(user_id: str) -> Path:
    return users_dir() / f"{_safe_filename(user_id)}.json"


def load_user_profile(user_id: str) -> dict | None:
    uid = str(user_id or "").strip()
    if not uid:
        return None
    path = user_profile_path(uid)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_user_profile(profile: dict) -> None:
    uid = str(profile.get("id") or "").strip()
    if not uid:
        raise ValueError("user profile needs id")
    path = user_profile_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")


def load_current_user_id() -> str:
    path = users_dir() / "current.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("id") or "").strip()


def set_current_user_id(user_id: str) -> None:
    uid = str(user_id or "").strip()
    path = users_dir() / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": uid}) + "\n", encoding="utf-8")


def clear_current_user() -> None:
    path = users_dir() / "current.json"
    try:
        path.unlink()
    except OSError:
        pass


def list_user_profiles() -> list[dict]:
    root = users_dir()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(root.glob("*.json")):
        if path.name.lower() == "current.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and str(data.get("id") or "").strip():
            out.append(data)
    return out


def update_current_user_profile(updates: dict, data: dict | None = None) -> dict:
    profile = current_user_profile(data)
    if not profile:
        raise ValueError("no signed-in user")
    merged = dict(profile)
    if "email" in updates:
        merged["email"] = str(updates.get("email") or "").strip()
    if "display_name" in updates:
        merged["display_name"] = str(updates.get("display_name") or "").strip() or merged.get(
            "display_name"
        )
    if "my_machines" in updates:
        merged["my_machines"] = normalize_my_machines(updates.get("my_machines"))
    if "subscriptions" in updates:
        merged["subscriptions"] = merge_subscriptions(updates.get("subscriptions"))
    save_user_profile(merged)
    return merged


def subscribers_for_new_qa_case(machine_name: str) -> list[dict]:
    name = str(machine_name or "").strip()
    out: list[dict] = []
    seen: set[str] = set()
    for profile in list_user_profiles():
        scope = new_qa_case_scope(profile)
        if scope == NOTIFY_NEW_CASE_OFF:
            continue
        email = profile_email(profile)
        if not looks_like_email(email) or email.lower() in seen:
            continue
        if scope == NOTIFY_NEW_CASE_MY_MACHINES:
            mine = {item.lower() for item in normalize_my_machines(profile.get("my_machines"))}
            if name.lower() not in mine:
                continue
        seen.add(email.lower())
        out.append(profile)
    return out


def save_oidc_profile(claims: dict) -> dict:
    """Persist an OIDC user from ID token / userinfo claims."""
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise ValueError("OIDC token has no sub")
    uid = f"oidc:{sub}"
    existing = load_user_profile(uid) or {}
    username = str(
        claims.get("preferred_username") or claims.get("email") or sub
    ).strip()
    display = str(
        existing.get("display_name")
        or claims.get("name")
        or claims.get("preferred_username")
        or claims.get("email")
        or username
    ).strip()
    profile = _attach_prefs(
        {
            "id": uid,
            "method": USER_ID_OIDC,
            "username": username,
            "display_name": display,
            "email": str(existing.get("email") or claims.get("email") or "").strip(),
            "oidc": {
                "sub": sub,
                "iss": str(claims.get("iss") or ""),
                "preferred_username": str(claims.get("preferred_username") or ""),
            },
        },
        existing,
    )
    save_user_profile(profile)
    set_current_user_id(uid)
    return profile


def upsert_os_user_profile() -> dict:
    facts = collect_os_user()
    uid = os_user_id(facts)
    existing = load_user_profile(uid) or {}
    email = str(existing.get("email") or "").strip() or str(facts.get("email") or "").strip()
    display = str(existing.get("display_name") or "").strip() or str(
        facts.get("display_name") or facts.get("username") or ""
    )
    profile = _attach_prefs(
        {
            "id": uid,
            "method": USER_ID_OSUSER,
            "username": str(facts.get("username") or ""),
            "display_name": display,
            "email": email,
            "os": facts,
        },
        existing,
    )
    try:
        save_user_profile(profile)
    except OSError:
        pass
    return profile


def format_os_user_preview(facts: dict | None = None) -> str:
    facts = facts if facts is not None else collect_os_user()
    lines = [
        f"id: {os_user_id(facts)}",
        f"username: {facts.get('username') or '—'}",
        f"display_name: {facts.get('display_name') or '—'}",
        f"email: {facts.get('email') or '—'}",
        f"hostname: {facts.get('hostname') or '—'}",
        f"os: {facts.get('os') or '—'} {facts.get('os_release') or ''}".strip(),
        f"home: {facts.get('home') or '—'}",
    ]
    if facts.get("sam_compatible"):
        lines.append(f"sam: {facts['sam_compatible']}")
    if facts.get("user_principal_name"):
        lines.append(f"upn: {facts['user_principal_name']}")
    if facts.get("uid") is not None:
        lines.append(f"uid/gid: {facts.get('uid')}/{facts.get('gid')}")
    if facts.get("gecos"):
        lines.append(f"gecos: {facts['gecos']}")
    return "\n".join(lines)


def _username() -> str:
    try:
        name = getpass.getuser()
        if name:
            return str(name)
    except Exception:
        pass
    return str(os.environ.get("USERNAME") or os.environ.get("USER") or os.environ.get("LOGNAME") or "")


def _email_from_upn(upn: str) -> str:
    text = str(upn or "").strip()
    if "@" in text and " " not in text:
        return text
    return ""


def _safe_filename(user_id: str) -> str:
    return re.sub(r"[<>:\"/\\|?*]", "_", user_id).strip(" .") or "user"


def _windows_name_ex(name_format: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return ""
    try:
        GetUserNameExW = ctypes.windll.secur32.GetUserNameExW
    except Exception:
        return ""
    size = wintypes.ULONG(256)
    buf = ctypes.create_unicode_buffer(256)
    if GetUserNameExW(name_format, buf, ctypes.byref(size)):
        return buf.value.strip()
    n = int(size.value or 0) + 2
    if n < 2 or n > 4096:
        return ""
    buf = ctypes.create_unicode_buffer(n)
    size = wintypes.ULONG(n)
    if GetUserNameExW(name_format, buf, ctypes.byref(size)):
        return buf.value.strip()
    return ""


def _windows_extras() -> dict:
    extras = {
        "userdomain": str(os.environ.get("USERDOMAIN") or ""),
        "userdnsdomain": str(os.environ.get("USERDNSDOMAIN") or ""),
        "sam_compatible": _windows_name_ex(_WIN_NAME_SAM),
        "display_name": _windows_name_ex(_WIN_NAME_DISPLAY),
        "user_principal_name": _windows_name_ex(_WIN_NAME_UPN),
        "given_name": _windows_name_ex(_WIN_NAME_GIVEN),
        "surname": _windows_name_ex(_WIN_NAME_SURNAME),
    }
    extras["email"] = _email_from_upn(extras["user_principal_name"])
    return extras


def _posix_extras() -> dict:
    extras: dict = {}
    try:
        import pwd

        rec = pwd.getpwuid(os.getuid())
    except Exception:
        return extras
    extras["uid"] = rec.pw_uid
    extras["gid"] = rec.pw_gid
    extras["shell"] = rec.pw_shell
    extras["gecos"] = rec.pw_gecos or ""
    extras["display_name"] = (rec.pw_gecos or "").split(",")[0].strip() or rec.pw_name
    try:
        extras["groups"] = sorted({os.getgid(), *os.getgroups()})
    except Exception:
        extras["groups"] = [rec.pw_gid]
    return extras


def _macos_extras(username: str) -> dict:
    extras: dict = {}
    if not username:
        return extras
    real = _dscl_read(username, "RealName")
    if real:
        extras["display_name"] = real
    unique = _dscl_read(username, "GeneratedUID")
    if unique:
        extras["generated_uid"] = unique
    mail = _dscl_read(username, "EMailAddress")
    if mail:
        extras["email"] = mail
    return extras


def _dscl_read(username: str, key: str) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            ["dscl", ".", "-read", f"/Users/{username}", key],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    text = (proc.stdout or "").strip()
    prefix = f"{key}:"
    if text.startswith(prefix):
        text = text[len(prefix) :].strip()
    return " ".join(text.split())
