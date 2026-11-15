$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Run Setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Build the frontend with Setup.ps1 first.' }

$baseUrl = 'http://127.0.0.1:8010'
$portOccupied = $false
$probe = New-Object System.Net.Sockets.TcpClient
try {
    $connection = $probe.ConnectAsync('127.0.0.1', 8010)
    if ($connection.Wait(1000)) { $portOccupied = $probe.Connected }
} catch {
    $portOccupied = $false
} finally {
    $probe.Dispose()
}

if ($portOccupied) {
    try {
        $schema = Invoke-RestMethod -Uri "$baseUrl/openapi.json" -TimeoutSec 5
    } catch {
        throw "Port 8010 is occupied, but its server could not be identified. Stop the existing server in its terminal before starting Embercal."
    }
    $endpoints = @($schema.paths.PSObject.Properties.Name)
    if ($schema.info.title -eq 'Embercal' -and $endpoints -contains '/api/analysis/readiness') {
        Write-Host "Embercal is already running: $baseUrl"
        Write-Host "Open that address in your browser. To restart after code/data changes, press Ctrl+C in the terminal that started the server, then run Start.ps1 again."
        return
    }
    throw "Port 8010 is being used by another application. Stop that application or free the port before starting Embercal."
}

$env:PYTHONPATH = Join-Path $PSScriptRoot 'backend'
Write-Host "Embercal: $baseUrl | API: $baseUrl/docs | Ctrl+C to stop"
& $interpreter -m uvicorn ignis.studio_api:app --host 127.0.0.1 --port 8010
if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
