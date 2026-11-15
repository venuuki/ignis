$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Run Setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend with Setup.ps1 first.' }
$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'
Write-Host 'Embercal: http://127.0.0.1:8010 | API: http://127.0.0.1:8010/docs | Ctrl+C to stop'
& $interpreter -m uvicorn ignis.studio_api:app --host 127.0.0.1 --port 8010
if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
