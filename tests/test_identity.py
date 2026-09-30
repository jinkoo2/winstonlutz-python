def test_users_dir_defaults_next_to_exe(tmp_path, monkeypatch):
    from winstonlutz.identity import USERS_DIRNAME, users_dir

    monkeypatch.delenv("WINSTONLUTZ_USERS_DIR", raising=False)
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    monkeypatch.setattr("winstonlutz.identity.app_dir", lambda: tmp_path)
    assert users_dir() == tmp_path / USERS_DIRNAME
    override = tmp_path / "shared"
    monkeypatch.setenv("WINSTONLUTZ_USERS_DIR", str(override))
    assert users_dir() == override.resolve()


def test_identity_defaults_and_os_user(tmp_path, monkeypatch):
    from winstonlutz.app_settings import save_gui_settings
    from winstonlutz import identity as ident
    from winstonlutz.identity import (
        USER_ID_NONE,
        USER_ID_OSUSER,
        collect_os_user,
        current_user_label,
        get_user_id_method,
        os_user_id,
        session_operator,
        upsert_os_user_profile,
    )

    ident._os_facts_cache = None
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("WINSTONLUTZ_USERS_DIR", str(tmp_path / "_users"))

    assert get_user_id_method({}) == USER_ID_NONE
    assert get_user_id_method({"Identity": {"user_id_method": "osuser"}}) == USER_ID_OSUSER
    assert current_user_label({}) == ""
    assert session_operator("DICOM^Name") == "DICOM^Name"

    facts = collect_os_user()
    assert facts.get("username")
    assert facts.get("hostname")
    assert facts.get("os") in ("Windows", "Linux", "Darwin")
    assert os_user_id(facts).startswith("osuser:")

    save_gui_settings({"Identity": {"user_id_method": "OSUser"}})
    ident._os_facts_cache = None
    profile = upsert_os_user_profile()
    assert profile["method"] == "OSUser"
    assert profile["id"] == os_user_id()
    assert "subscriptions" in profile
    assert (tmp_path / "_users").is_dir()
    assert current_user_label() == profile["display_name"]
    assert session_operator("") == profile["display_name"]
    assert session_operator("FromDICOM") == "FromDICOM"

    from winstonlutz.identity import coerce_registration_url, oidc_settings

    cfg = oidc_settings({})
    assert cfg["issuer"] == "https://login.apps.myphysics.net/realms/myphysics"
    assert cfg["client_id"] == "winstonlutz"
    assert cfg["scopes"] == "openid profile email"
    assert cfg["registration_url"].endswith("/realms/myphysics/account/")
    assert "openid-connect/registrations" not in cfg["registration_url"]
    filled = oidc_settings(
        {
            "Identity": {
                "oidc": {
                    "keycloak_url": "https://login.apps.myphysics.net",
                    "keycloak_realm": "myphysics",
                }
            }
        }
    )
    assert filled["issuer"] == "https://login.apps.myphysics.net/realms/myphysics"
    assert cfg["redirect_uri"] == "http://127.0.0.1:17843/callback"

    broken = (
        "https://login.apps.myphysics.net/realms/myphysics/protocol/openid-connect/registrations"
        "?client_id=account-console&response_type=code&scope=openid"
        "&redirect_uri=https%3A%2F%2Flogin.apps.myphysics.net%2Frealms%2Fmyphysics%2Faccount%2F"
    )
    rewritten = oidc_settings({"Identity": {"oidc": {"registration_url": broken}}})
    assert rewritten["registration_url"].endswith("/realms/myphysics/account/")
    assert coerce_registration_url(broken).endswith("/account/")

    from winstonlutz.oidc import authorization_url, decode_jwt_payload, pkce_pair

    verifier, challenge = pkce_pair()
    assert verifier and challenge and verifier != challenge
    payload = {"sub": "abc", "name": "Pat", "email": "pat@example.edu"}
    import base64
    import json as jsonlib

    body = base64.urlsafe_b64encode(jsonlib.dumps(payload).encode()).rstrip(b"=").decode()
    claims = decode_jwt_payload(f"hdr.{body}.sig")
    assert claims["sub"] == "abc"
    url = authorization_url(
        cfg,
        redirect_uri=cfg["redirect_uri"],
        state="st",
        code_challenge=challenge,
        authorization_endpoint="https://login.apps.myphysics.net/realms/myphysics/protocol/openid-connect/auth",
    )
    assert "code_challenge_method=S256" in url
    assert "client_id=winstonlutz" in url


def test_user_profile_machines_and_subscribers(tmp_path, monkeypatch):
    from winstonlutz import identity as ident
    from winstonlutz.identity import (
        NOTIFY_NEW_CASE_ALL_MACHINES,
        NOTIFY_NEW_CASE_MY_MACHINES,
        looks_like_email,
        save_user_profile,
        subscribers_for_new_qa_case,
        upsert_os_user_profile,
        user_needs_email,
    )

    ident._os_facts_cache = None
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("WINSTONLUTZ_USERS_DIR", str(tmp_path / "_users"))

    assert looks_like_email("pat@hospital.edu")
    assert not looks_like_email("not-an-email")

    profile = upsert_os_user_profile()
    profile["email"] = ""
    profile["my_machines"] = ["Edge"]
    profile["subscriptions"] = {
        "new_qa_case": NOTIFY_NEW_CASE_MY_MACHINES,
        "email": False,
        "google_chat": False,
        "slack": False,
        "microsoft_teams": False,
        "discord": False,
    }
    save_user_profile(profile)
    assert subscribers_for_new_qa_case("Edge") == []

    profile["email"] = "pat@hospital.edu"
    save_user_profile(profile)
    mine = subscribers_for_new_qa_case("Edge")
    assert len(mine) == 1
    assert mine[0]["email"] == "pat@hospital.edu"
    assert subscribers_for_new_qa_case("TrueBeam") == []

    profile["subscriptions"]["new_qa_case"] = NOTIFY_NEW_CASE_ALL_MACHINES
    save_user_profile(profile)
    assert subscribers_for_new_qa_case("TrueBeam")[0]["email"] == "pat@hospital.edu"

    from winstonlutz.app_settings import save_gui_settings

    save_gui_settings({"Identity": {"user_id_method": "OSUser"}})
    profile["email"] = ""
    save_user_profile(profile)
    assert user_needs_email() is True

