param([string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
$savedLocation = Get-Location
$previousSensorHome = $env:SWEAT_SENSOR_HOME
$selfTestHome = Join-Path ([IO.Path]::GetTempPath()) ('sweat-build-' + [guid]::NewGuid().ToString('N'))
try {
    Set-Location -LiteralPath $PSScriptRoot
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Create a Python 3.12 x64 venv and install requirements-lock.txt first.' }
    & $Python -c "import struct,sys; assert sys.platform == 'win32' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Use Windows Python 3.12 x64'"
    if ($LASTEXITCODE -ne 0) { throw 'Unsupported build interpreter.' }
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
    & $Python tools/run_unit_tests.py
    if ($LASTEXITCODE -ne 0) { throw 'Simulation tests failed.' }
    $env:SWEAT_SENSOR_HOME = $selfTestHome
    & $Python src/app.py --self-test --report verification/source-gui.json
    if ($LASTEXITCODE -ne 0) { throw 'Source GUI acceptance failed.' }
    & $Python -m PyInstaller --noconfirm WearableSweatSensor.spec
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
    & $Python tools/finalize_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release finalization failed.' }
    Write-Host 'Release ready: dist\WearableSweatSensor-1.1.0-win64-with-manual.zip'
} finally {
    $env:SWEAT_SENSOR_HOME = $previousSensorHome
    if (Test-Path -LiteralPath $selfTestHome) {
        $resolvedTestHome = [IO.Path]::GetFullPath($selfTestHome)
        $allowedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $resolvedTestHome.StartsWith($allowedTempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected temporary directory.' }
        Remove-Item -LiteralPath $resolvedTestHome -Recurse -Force
    }
    Set-Location -LiteralPath $savedLocation
}
