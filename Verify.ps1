$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Run Setup.ps1 first.' }
$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'
& $interpreter -m pytest backend\tests -q
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
Push-Location -LiteralPath 'frontend'
try {
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
