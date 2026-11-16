def test_new_case_scan_seeds_then_notifies(tmp_path, monkeypatch):
    from winstonlutz.app_settings import save_gui_settings
    from winstonlutz.case_notify import notify_new_qa_case, scan_and_notify_new_cases
    from winstonlutz.identity import (
        NOTIFY_NEW_CASE_MY_MACHINES,
        save_user_profile,
        upsert_os_user_profile,
    )
    from winstonlutz import identity as ident

    ident._os_facts_cache = None
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("WINSTONLUTZ_USERS_DIR", str(tmp_path / "_users"))

    data = tmp_path / "Edge" / "Data"
    existing = data / "26-09-01_06-00-00"
    existing.mkdir(parents=True)
    (existing / "RI.old.dcm").write_bytes(b"")
    save_gui_settings(
        {
            "Identity": {"user_id_method": "OSUser"},
            "MACHINES": [
                {
                    "NAME": "Edge",
                    "DATA_FOLDER": str(data),
                    "CASE_FOLDER_NAME_REGEX": r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$",
                }
            ],
        }
    )
    profile = upsert_os_user_profile()
    profile["email"] = "pat@hospital.edu"
    profile["my_machines"] = ["Edge"]
    profile["subscriptions"]["new_qa_case"] = NOTIFY_NEW_CASE_MY_MACHINES
    save_user_profile(profile)

    first = scan_and_notify_new_cases(send_mail=False)
    assert first == []

    fresh = data / "26-09-29_12-00-00"
    fresh.mkdir()
    (fresh / "RI.new.dcm").write_bytes(b"")
    second = scan_and_notify_new_cases(send_mail=False)
    assert second == ["Edge/26-09-29_12-00-00"]
    assert notify_new_qa_case("Edge", fresh, send_mail=False) is False


def test_analyzed_archive_case_is_not_emailed(tmp_path, monkeypatch):
    from winstonlutz.app_settings import save_gui_settings
    from winstonlutz.case_notify import scan_and_notify_new_cases
    from winstonlutz.identity import (
        NOTIFY_NEW_CASE_MY_MACHINES,
        save_user_profile,
        upsert_os_user_profile,
    )
    from winstonlutz import case_notify, identity as ident

    ident._os_facts_cache = None
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setenv("WINSTONLUTZ_USERS_DIR", str(tmp_path / "_users"))
    sent = []
    monkeypatch.setattr(case_notify, "send", lambda **kwargs: sent.append(kwargs["subject"]))

    data = tmp_path / "TrueBeamSH" / "Data"
    done = data / "26-10-28_08-06-36"
    done.mkdir(parents=True)
    (done / "RI.done.dcm").write_bytes(b"")
    (done / "report.html").write_text("<html>done</html>", encoding="utf-8")
    fresh = data / "26-11-16_08-00-00"
    fresh.mkdir()
    (fresh / "RI.new.dcm").write_bytes(b"")
    save_gui_settings(
        {
            "Identity": {"user_id_method": "OSUser"},
            "Notifications": {
                "email": {
                    "email_from": "physics",
                    "email_domain": "hospital.edu",
                    "email_host_address": "mail.hospital.edu",
                    "email_host_port": 25,
                }
            },
            "MACHINES": [
                {
                    "NAME": "TrueBeamSH",
                    "DATA_FOLDER": str(data),
                    "CASE_FOLDER_NAME_REGEX": r"^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$",
                }
            ],
        }
    )
    (tmp_path / "_users").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_users" / "notified_cases.json").write_text(
        '{"seeded": true, "cases": []}\n',
        encoding="utf-8",
    )
    profile = upsert_os_user_profile()
    profile["email"] = "pat@hospital.edu"
    profile["my_machines"] = ["TrueBeamSH"]
    profile["subscriptions"]["new_qa_case"] = NOTIFY_NEW_CASE_MY_MACHINES
    save_user_profile(profile)

    found = scan_and_notify_new_cases(send_mail=True)
    assert found == ["TrueBeamSH/26-11-16_08-00-00"]
    assert sent == ["New WL QA case: TrueBeamSH 26-11-16_08-00-00"]
    assert scan_and_notify_new_cases(send_mail=True) == []
