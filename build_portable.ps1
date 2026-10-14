param([string]$Python = 'python', [switch]$SkipDependencyInstall)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed: $LASTEXITCODE" } }
& $Python -c "import sys,struct; assert sys.platform=='win32' and struct.calcsize('P')==8 and sys.version_info[:2]==(3,12), 'Use Windows Python 3.12 x64'"
Check-Exit
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    Check-Exit
}
$BuildPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not $SkipDependencyInstall) {
    & $Python -m pip --python $BuildPython install --index-url https://pypi.org/simple -r requirements-build.txt
    Check-Exit
}
New-Item -ItemType Directory -Force -Path 'build','dist' | Out-Null
$env:PUMPVALVE_DATA_DIR = Join-Path $PSScriptRoot 'build\test user data'
& $BuildPython -m pytest -q -p no:cacheprovider --basetemp 'build\pytest temporary files'
Check-Exit
& $BuildPython -m PyInstaller --noconfirm --clean Max4PumpValve.spec
Check-Exit
$Distribution = Join-Path $PSScriptRoot 'dist\Max4PumpValve-1.1.0'
Copy-Item -LiteralPath 'PORTABLE_README.txt' -Destination $Distribution
Copy-Item -LiteralPath 'docs\SDK_RUNTIME.md','docs\SOURCE_PROVENANCE.md','docs\VERIFICATION.md','docs\TERMINAL.md' -Destination $Distribution
# Retain dependency licenses beside the portable application.
& $BuildPython tools\collect_licenses.py $Distribution
Check-Exit
$env:PUMPVALVE_DATA_DIR = Join-Path $PSScriptRoot 'build\packaged smoke user data'
$SmokeReport = Join-Path $PSScriptRoot 'build\packaged smoke results\report.json'
$ApplicationExe = Join-Path $Distribution 'Max4PumpValve.exe'
$process = Start-Process -FilePath $ApplicationExe -ArgumentList @('--smoke-test', ('"' + $SmokeReport + '"')) -PassThru -WindowStyle Hidden
if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'Packaged simulation smoke test timed out' }
if ($process.ExitCode -ne 0) { throw "Packaged smoke test failed. See $SmokeReport" }
$result = Get-Content -Raw -LiteralPath $SmokeReport | ConvertFrom-Json
if (-not $result.passed) { throw "Packaged smoke test failed: $($result.error)" }
Copy-Item -LiteralPath $SmokeReport -Destination (Join-Path $Distribution 'verification.json')
& $BuildPython tools\verify_console.py $Distribution 'build\console verification\report.json'
Check-Exit
Copy-Item -LiteralPath 'build\console verification\report.json' -Destination (Join-Path $Distribution 'console-verification.json')
$ZipPath = Join-Path $PSScriptRoot 'dist\Max4PumpValve-1.1.0-windows-x64-portable.zip'
Compress-Archive -LiteralPath $Distribution -DestinationPath $ZipPath -Force
& $BuildPython tools\source_archive.py
Check-Exit
Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath | Format-List
Write-Output "Portable ZIP: $ZipPath"
