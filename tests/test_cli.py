import os

from winstonlutz.cli import prepare_argv


def test_prepare_argv_defaults_to_gui(monkeypatch):
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    assert prepare_argv([]) == ["gui"]
    assert prepare_argv(["-v"]) == ["-v", "gui"]
    assert prepare_argv(["--help"]) == ["--help"]
    assert prepare_argv(["-h"]) == ["-h"]


def test_prepare_argv_mode_service(monkeypatch):
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    assert prepare_argv(["--mode", "service"]) == ["watch"]
    assert prepare_argv(["--mode=service"]) == ["watch"]
    assert prepare_argv(["--mode", "gui"]) == ["gui"]
    assert prepare_argv(["service"]) == ["watch"]
    assert prepare_argv(["watch"]) == ["watch"]
    assert prepare_argv(["gui", "C:\\cases"]) == ["gui", "C:\\cases"]


def test_prepare_argv_users_sets_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WINSTONLUTZ_USERS_DIR", raising=False)
    folder = tmp_path / "shared_users"
    folder.mkdir()
    assert prepare_argv(["--users", str(folder)]) == ["gui"]
    assert os.environ["WINSTONLUTZ_USERS_DIR"] == str(folder.resolve())
    monkeypatch.delenv("WINSTONLUTZ_USERS_DIR", raising=False)
    assert prepare_argv([f"--users-dir={folder}", "--mode", "service"]) == ["watch"]
    assert os.environ["WINSTONLUTZ_USERS_DIR"] == str(folder.resolve())


def test_help_lists_global_options(capsys):
    from winstonlutz.cli import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit from --help")
    text = capsys.readouterr().out
    assert "--users" in text
    assert "--settings" in text
    assert "--mode" in text
    assert "-h" in text or "--help" in text


def test_prepare_argv_settings_sets_env(monkeypatch, tmp_path):
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    assert prepare_argv(["--settings", str(settings)]) == ["gui"]
    assert os.environ["WINSTONLUTZ_APP_CONFIG"] == str(settings.resolve())
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    assert prepare_argv(["-s", str(settings), "--mode", "service"]) == ["watch"]
    assert os.environ["WINSTONLUTZ_APP_CONFIG"] == str(settings.resolve())
    monkeypatch.delenv("WINSTONLUTZ_APP_CONFIG", raising=False)
    assert prepare_argv([f"--config={settings}", "analyze", "case"])[0] == "analyze"
    assert os.environ["WINSTONLUTZ_APP_CONFIG"] == str(settings.resolve())
