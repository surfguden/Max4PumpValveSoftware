param([string]$ZipPath = (Join-Path $PSScriptRoot '..\dist\Max4PumpValve-1.1.0-windows-x64-portable.zip'))
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Extraction = Join-Path $Root 'build\extracted distribution with spaces'
if (Test-Path -LiteralPath $Extraction) {
    Get-ChildItem -LiteralPath $Extraction -File -Recurse | ForEach-Object { $_.IsReadOnly = $false }
}
Expand-Archive -LiteralPath $ZipPath -DestinationPath $Extraction -Force
$AppFolder = Join-Path $Extraction 'Max4PumpValve-1.1.0'
Get-ChildItem -LiteralPath $AppFolder -File -Recurse | ForEach-Object { $_.IsReadOnly = $true }
$SavedPath = $env:PATH
$SavedPythonPath = $env:PYTHONPATH
$SavedPythonHome = $env:PYTHONHOME
$SavedData = $env:PUMPVALVE_DATA_DIR
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONPATH = ''
    $env:PYTHONHOME = ''
    $env:PUMPVALVE_DATA_DIR = Join-Path $Root 'build\extracted smoke user data'
    $Report = Join-Path $Root 'build\extracted smoke results\report.json'
    $process = Start-Process -FilePath (Join-Path $AppFolder 'Max4PumpValve.exe') -ArgumentList @('--smoke-test', ('"' + $Report + '"')) -WorkingDirectory $AppFolder -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'Extracted executable timed out' }
    if ($process.ExitCode -ne 0) { throw "Extracted executable failed: $($process.ExitCode)" }
    $result = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if (-not $result.passed) { throw "Extracted smoke failed: $($result.error)" }
    $TestPython = Join-Path $Root '.venv\Scripts\python.exe'
    & $TestPython (Join-Path $Root 'tools\verify_console.py') $AppFolder (Join-Path $Root 'build\extracted console results\report.json')
    if ($LASTEXITCODE -ne 0) { throw 'Extracted console interface verification failed' }
    Write-Output "Extracted smoke passed with no Python on PATH and read-only distribution files: $Report"
} finally {
    $env:PATH = $SavedPath
    $env:PYTHONPATH = $SavedPythonPath
    $env:PYTHONHOME = $SavedPythonHome
    $env:PUMPVALVE_DATA_DIR = $SavedData
}
