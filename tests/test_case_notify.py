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
    monkeypatch.setenv("WINSTONLUTZ_APP_CONFIG", str(tmp_path / "winstonlutz.gui.settings.json"))
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
