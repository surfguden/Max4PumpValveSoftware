$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$applicationFolder = Join-Path $repositoryRoot 'dist\hardware-accepted-20261015\Max4PumpValve-1.1.0'
$env:PUMPVALVE_DATA_DIR = Join-Path $PSScriptRoot 'session'
$env:QT_QPA_PLATFORM = 'windows'
Remove-Item Env:PUMPVALVE_TEST_SIMULATION -ErrorAction SilentlyContinue
$response = & (Join-Path $applicationFolder 'Max4PumpValveCtl.exe') status
if ($LASTEXITCODE -eq 0) {
    $endpoint = Get-Content -Raw -LiteralPath (Join-Path $env:PUMPVALVE_DATA_DIR 'endpoint.json') | ConvertFrom-Json
    $desktopShell = New-Object -ComObject WScript.Shell
    $desktopShell.AppActivate([int]$endpoint.pid) | Out-Null
} else {
    Start-Process -FilePath (Join-Path $applicationFolder 'Max4PumpValve.exe') -WindowStyle Normal
}
