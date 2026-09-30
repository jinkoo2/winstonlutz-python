from winstonlutz.watch_service import (
    FROZEN_APP_PARAMETERS,
    SOURCE_APP_PARAMETERS,
    WatchServicePlan,
    default_app_parameters,
    format_nssm_commands,
    nssm_commands,
)


def test_nssm_commands_hide_password_and_replace():
    plan = WatchServicePlan(
        service_name="WinstonLutzWatch",
        nssm_exe=r"C:\nssm\nssm.exe",
        program_exe=r"C:\py\python.exe",
        app_parameters=SOURCE_APP_PARAMETERS,
        app_directory=r"D:\MachineQA\projects\winstonlutz",
        settings_file=r"D:\MachineQA\projects\winstonlutz\settings.json",
        account=r"CLINIC\physicssvc",
        password="secret",
        start_after=True,
        replace_existing=True,
    )
    commands = nssm_commands(plan)
    assert commands[0][1] == "stop"
    assert commands[1][1:] == ["remove", "WinstonLutzWatch", "confirm"]
    assert commands[2] == [r"C:\nssm\nssm.exe", "install", "WinstonLutzWatch", r"C:\py\python.exe"]
    assert any(
        len(args) > 4 and args[3] == "AppParameters" and args[4] == SOURCE_APP_PARAMETERS
        for args in commands
    )
    object_name = [args for args in commands if args[3:4] == ["ObjectName"]][0]
    assert object_name[-2:] == [r"CLINIC\physicssvc", "secret"]
    text = format_nssm_commands(plan)
    assert "secret" not in text
    assert "<password>" in text
    assert commands[-1] == [r"C:\nssm\nssm.exe", "start", "WinstonLutzWatch"]


def test_nssm_commands_local_system_skips_account():
    plan = WatchServicePlan(
        nssm_exe="nssm",
        program_exe="python.exe",
        app_directory="D:\\app",
        settings_file="D:\\app\\settings.json",
        account="",
        replace_existing=False,
        start_after=False,
    )
    commands = nssm_commands(plan)
    assert commands[0][1] == "install"
    assert not any("ObjectName" in args for args in commands)
    assert commands[-1][1] != "start"
    assert any(
        len(args) > 4 and args[3] == "AppParameters" and args[4] == SOURCE_APP_PARAMETERS
        for args in commands
    )


def test_nssm_commands_packaged_exe_uses_watch():
    plan = WatchServicePlan(
        nssm_exe=r"C:\nssm\nssm.exe",
        program_exe=r"C:\Apps\WinstonLutz.exe",
        app_parameters=FROZEN_APP_PARAMETERS,
        app_directory=r"C:\Apps",
        settings_file=r"C:\Apps\settings.json",
        replace_existing=False,
        start_after=False,
    )
    commands = nssm_commands(plan)
    assert commands[0] == [
        r"C:\nssm\nssm.exe",
        "install",
        "WinstonLutzWatch",
        r"C:\Apps\WinstonLutz.exe",
    ]
    assert any(
        len(args) > 4 and args[3] == "AppParameters" and args[4] == "watch" for args in commands
    )


def test_default_app_parameters_from_program_name():
    assert default_app_parameters(r"C:\py\python.exe") == SOURCE_APP_PARAMETERS
    assert default_app_parameters(r"C:\Apps\WinstonLutz.exe") == FROZEN_APP_PARAMETERS
    assert default_app_parameters(r"C:\Apps\WinstonLutz-0.3.0-windows-x64.exe") == FROZEN_APP_PARAMETERS
