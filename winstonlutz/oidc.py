"""Authorization Code + PKCE for a desktop OIDC login (Keycloak)."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable

from .identity import DEFAULT_OIDC_REDIRECT_URI, oidc_settings, save_oidc_profile


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def decode_jwt_payload(token: str) -> dict:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    pad = "=" * (-len(parts[1]) % 4)
    try:
        raw = base64.urlsafe_b64decode(parts[1] + pad)
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def discover(issuer: str) -> dict:
    url = str(issuer or "").rstrip("/") + "/.well-known/openid-configuration"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("OIDC discovery did not return a JSON object")
    return data


def authorization_url(
    cfg: dict,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    authorization_endpoint: str,
) -> str:
    scopes = str(cfg.get("scopes") or "openid").strip() or "openid"
    if "openid" not in scopes.split():
        scopes = "openid " + scopes
    query = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return authorization_endpoint + "?" + urllib.parse.urlencode(query)


def _parse_redirect_uri(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(redirect_uri or DEFAULT_OIDC_REDIRECT_URI)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 17843
    path = parsed.path or "/callback"
    if not path.startswith("/"):
        path = "/" + path
    return host, port, path


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.server.callback_path:
            self.send_error(404)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        self.server.query = {k: v[0] if v else "" for k, v in qs.items()}
        body = (
            b"<html><body style='font-family:sans-serif;padding:2em'>"
            b"<p>Signed in. You can close this tab and return to Winston-Lutz.</p>"
            b"</body></html>"
        )
        if self.server.query.get("error"):
            err = self.server.query.get("error_description") or self.server.query.get("error")
            body = (
                b"<html><body style='font-family:sans-serif;padding:2em'>"
                b"<p>Sign-in failed.</p><pre>"
                + err.encode("utf-8", errors="replace")
                + b"</pre></body></html>"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _wait_for_callback(httpd, timeout: float, pump_events: Callable | None) -> dict:
    httpd.socket.settimeout(0.4)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if getattr(httpd, "query", None):
            return httpd.query
        try:
            httpd.handle_request()
        except TimeoutError:
            pass
        except socket.timeout:
            pass
        except OSError:
            pass
        if pump_events:
            pump_events()
    raise TimeoutError("Timed out waiting for Keycloak to redirect back to the app.")


def _post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        raise RuntimeError(
            f"Token request failed (HTTP {exc.code}). "
            "On Keycloak, this client must be public, Standard flow enabled, PKCE S256, "
            f"and Valid redirect URIs must include {fields.get('redirect_uri')}. "
            + (detail or "")
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Token endpoint did not return JSON")
    return payload


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return {}
    return data if isinstance(data, dict) else {}


def login_oidc(
    data: dict | None = None,
    *,
    on_status: Callable[[str], None] | None = None,
    pump_events: Callable | None = None,
    timeout: float = 300,
) -> dict:
    """Open the system browser for Keycloak login and return a saved user profile."""
    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    cfg = oidc_settings(data)
    issuer = cfg.get("issuer") or ""
    if not issuer or not cfg.get("client_id"):
        raise RuntimeError("OIDC issuer and client_id must be set in Settings → Identity.")
    redirect_uri = cfg.get("redirect_uri") or DEFAULT_OIDC_REDIRECT_URI
    host, port, path = _parse_redirect_uri(redirect_uri)
    redirect_uri = f"http://{host}:{port}{path}"

    status("Looking up Keycloak…")
    meta = discover(issuer)
    auth_ep = str(meta.get("authorization_endpoint") or cfg.get("authorization_endpoint") or "")
    token_ep = str(meta.get("token_endpoint") or cfg.get("token_endpoint") or "")
    userinfo_ep = str(meta.get("userinfo_endpoint") or cfg.get("userinfo_endpoint") or "")
    if not auth_ep or not token_ep:
        raise RuntimeError("OIDC discovery is missing authorization or token endpoint.")

    verifier, challenge = pkce_pair()
    state = _b64url(secrets.token_bytes(16))
    url = authorization_url(
        cfg,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=challenge,
        authorization_endpoint=auth_ep,
    )

    try:
        httpd = http.server.HTTPServer((host, port), _CallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot listen on {redirect_uri}: {exc}. Close whatever is using that port, "
            "or change redirect_uri in Settings → Identity."
        ) from exc
    httpd.callback_path = path
    httpd.query = None
    try:
        status("Opening Keycloak in your browser…")
        webbrowser.open(url)
        status(f"Waiting for sign-in (redirect {redirect_uri})…")
        query = _wait_for_callback(httpd, timeout, pump_events)
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass

    if query.get("error"):
        raise RuntimeError(query.get("error_description") or query.get("error") or "OIDC error")
    if query.get("state") != state:
        raise RuntimeError("OIDC state mismatch. Try Sign in again.")
    code = query.get("code") or ""
    if not code:
        raise RuntimeError("Keycloak did not return an authorization code.")

    status("Exchanging code for tokens…")
    tokens = _post_form(
        token_ep,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": cfg["client_id"],
            "code_verifier": verifier,
        },
    )
    id_token = str(tokens.get("id_token") or "")
    claims = decode_jwt_payload(id_token)
    access = str(tokens.get("access_token") or "")
    if userinfo_ep and access:
        info = _get_json(userinfo_ep, access)
        for key, value in info.items():
            if value and not claims.get(key):
                claims[key] = value
    if not claims.get("iss"):
        claims["iss"] = issuer
    if claims.get("iss") and str(claims["iss"]).rstrip("/") != issuer.rstrip("/"):
        raise RuntimeError(f"Token issuer {claims.get('iss')} does not match {issuer}")
    status("Saving profile…")
    return save_oidc_profile(claims)
