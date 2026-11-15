$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot 'frontend')
if (-not (Test-Path -LiteralPath 'node_modules')) { throw 'Run Setup.ps1 first.' }
Write-Host 'Start the backend with Start.ps1 in another terminal. Open http://127.0.0.1:5173'
& npm.cmd run dev
if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE" }
