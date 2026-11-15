param([string]$Python = "py")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Install Node.js 22 or newer first." }
$nodeVersion = & node --version
if ($LASTEXITCODE -ne 0 -or [int](($nodeVersion.TrimStart('v') -split '\.')[0]) -lt 22) { throw "Node.js 22 or newer is required." }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($Python -eq 'py') { Invoke-Checked $Python @('-3','-m','venv','.venv') }
    else { Invoke-Checked $Python @('-m','venv','.venv') }
}
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Push-Location -LiteralPath 'backend'
try { Invoke-Checked $interpreter @('-m','pip','install','-r','requirements.txt') }
finally { Pop-Location }
Push-Location -LiteralPath 'frontend'
try {
    Invoke-Checked 'npm.cmd' @('ci','--no-audit','--no-fund')
    Invoke-Checked 'npm.cmd' @('run','build')
} finally { Pop-Location }
Write-Host 'Embercal setup complete. Run .\Start.ps1 and open http://127.0.0.1:8010'
