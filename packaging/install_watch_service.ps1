# Print NSSM commands to run Winston-Lutz watch as a Windows service.
# Does not install NSSM or change services. Run the printed nssm lines in an elevated prompt.

param(
    [string]$ServiceName = "WinstonLutzWatch",
    [string]$PythonExe = "",
    [string]$AppDirectory = "",
    [string]$SettingsFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $AppDirectory) { $AppDirectory = $root }
if (-not $SettingsFile) { $SettingsFile = Join-Path $AppDirectory "winstonlutz.gui.settings.json" }

if (-not $PythonExe) {
    $conda = Join-Path $env:LOCALAPPDATA "anaconda3\envs\winstonlutz\python.exe"
    if (Test-Path $conda) {
        $PythonExe = $conda
    } else {
        $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    }
}

$logs = Join-Path $AppDirectory "_logs"
if (-not (Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs | Out-Null
}

Write-Host "Install NSSM from https://nssm.cc then run these as Administrator:"
Write-Host ""
Write-Host "nssm install $ServiceName `"$PythonExe`""
Write-Host "nssm set $ServiceName AppDirectory `"$AppDirectory`""
Write-Host "nssm set $ServiceName AppParameters `"-u -m winstonlutz watch`""
Write-Host "nssm set $ServiceName AppEnvironmentExtra WINSTONLUTZ_APP_CONFIG=$SettingsFile"
Write-Host "nssm set $ServiceName DisplayName `"Winston-Lutz Watch`""
Write-Host "nssm set $ServiceName Start SERVICE_AUTO_START"
Write-Host "nssm set $ServiceName AppStdout `"$(Join-Path $logs 'watch_stdout.log')`""
Write-Host "nssm set $ServiceName AppStderr `"$(Join-Path $logs 'watch_stderr.log')`""
Write-Host "nssm set $ServiceName AppRotateFiles 1"
Write-Host "nssm set $ServiceName ObjectName `"DOMAIN\service-account`" `"password`""
Write-Host "nssm start $ServiceName"
Write-Host ""
Write-Host "Stop the C# WinstonLutzWindowsService first so both watchers do not run."
Write-Host "Python: $PythonExe"
Write-Host "AppDirectory: $AppDirectory"
Write-Host "Settings: $SettingsFile"
