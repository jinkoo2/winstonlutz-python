# Print NSSM commands to run Winston-Lutz watch as a Windows service.
# Does not install NSSM or change services. Run the printed nssm lines in an elevated prompt.

param(
    [string]$ServiceName = "WinstonLutzWatch",
    [string]$ProgramExe = "",
    [string]$PythonExe = "",
    [string]$AppParameters = "",
    [string]$AppDirectory = "",
    [string]$SettingsFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $AppDirectory) { $AppDirectory = $root }
if (-not $SettingsFile) {
    $canonical = Join-Path $AppDirectory "settings.json"
    $legacy = Join-Path $AppDirectory "winstonlutz.gui.settings.json"
    if (Test-Path $canonical) {
        $SettingsFile = $canonical
    } elseif (Test-Path $legacy) {
        $SettingsFile = $legacy
    } else {
        $SettingsFile = $canonical
    }
}
if (-not $ProgramExe) { $ProgramExe = $PythonExe }

function Find-PackagedExe {
    $suffix = ".exe"
    $nextToApp = Join-Path $AppDirectory "WinstonLutz$suffix"
    if (Test-Path $nextToApp) { return $nextToApp }
    $dist = Join-Path $root "dist"
    if (Test-Path $dist) {
        $found = Get-ChildItem $dist -Filter "WinstonLutz*" -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike "WinstonLutz.gui*" -and $_.Name -notlike "WinstonLutz.service*" } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return ""
}

if (-not $ProgramExe) {
    $packaged = Find-PackagedExe
    if ($packaged) {
        $ProgramExe = $packaged
    } else {
        $conda = Join-Path $env:LOCALAPPDATA "anaconda3\envs\winstonlutz\python.exe"
        if (Test-Path $conda) {
            $ProgramExe = $conda
        } else {
            $ProgramExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        }
    }
}

if (-not $AppParameters) {
    $name = [IO.Path]::GetFileName($ProgramExe)
    if ($name -like "python*") {
        $AppParameters = "-u -m winstonlutz watch"
    } else {
        $AppParameters = "--mode service"
    }
}

$logs = Join-Path $AppDirectory "_logs"
if (-not (Test-Path $logs)) {
    New-Item -ItemType Directory -Path $logs | Out-Null
}

Write-Host "Install NSSM from https://nssm.cc then run these as Administrator:"
Write-Host ""
Write-Host "nssm install $ServiceName `"$ProgramExe`""
Write-Host "nssm set $ServiceName AppDirectory `"$AppDirectory`""
if ($AppParameters) {
    Write-Host "nssm set $ServiceName AppParameters `"$AppParameters`""
}
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
Write-Host "Program: $ProgramExe"
Write-Host "Arguments: $AppParameters"
Write-Host "AppDirectory: $AppDirectory"
Write-Host "Settings: $SettingsFile"
