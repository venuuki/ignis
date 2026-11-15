param([ValidateSet('all','ingest','harmonize','drivers','forecast')][string]$Step = 'all')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Run Setup.ps1 first.' }
$env:PYTHONPATH = (Join-Path $PSScriptRoot 'backend') + [IO.Path]::PathSeparator + $PSScriptRoot
Write-Host 'Australia pipeline: cached observations, independent calibration/test, calendar and outlooks.'
Write-Host 'First run may require substantial network traffic, disk, memory and training time.'
& $interpreter -c "from pipeline.common import load_config; c=load_config(); assert [r['name'] for r in c['regions']] == ['Australia'], 'Pipeline config must contain Australia only'"
if ($LASTEXITCODE -ne 0) { throw 'Australia configuration check failed.' }
$modules = @{ ingest='pipeline.ingest_firms'; harmonize='pipeline.harmonize_v3'; drivers='pipeline.drivers'; forecast='pipeline.forecast' }
$steps = if ($Step -eq 'all') { @('ingest','harmonize','drivers','forecast') } else { @($Step) }
foreach ($item in $steps) {
    Write-Host "Running $item ..."
    & $interpreter -m $modules[$item]
    if ($LASTEXITCODE -ne 0) { throw "$item failed. Fix the reported issue, then rerun with -Step $item." }
}
Write-Host 'Selected Australia pipeline steps completed. Restart Start.ps1 to reload the data.'
