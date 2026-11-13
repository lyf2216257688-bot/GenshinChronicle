# Execute in an independent PowerShell 7 terminal. Nothing auto-starts 70Q.
[CmdletBinding()]
param(
    [ValidateSet('Preflight','Smoke','Run','Resume','Validate','Report','Status')][string]$Mode = 'Preflight',
    [string]$Config = 'config/recall-experiment-v1.json',
    [string]$Output
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$configPath = [IO.Path]::GetFullPath($Config, $projectRoot)
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$python = [IO.Path]::GetFullPath($settings.python, $projectRoot)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Missing pinned runtime $python" }
if (-not $Output) { $Output = if ($Mode -eq 'Smoke') { $settings.smoke_output } else { $settings.output } }
$outputPath = [IO.Path]::GetFullPath($Output, $projectRoot)
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:OPENBLAS_NUM_THREADS = '4'
$env:OMP_NUM_THREADS = '4'
$pythonArgs = @('-B','-u','-m','genshin_corpus.retrieval.clean_based.experiment',$Mode.ToLowerInvariant(),
    '--repo',$projectRoot,'--config',$configPath,'--output',$outputPath)
Write-Host "P4 $Mode -> $outputPath"
if ($Mode -in @('Run','Resume')) { Write-Host 'Keep this independent terminal open. Ctrl+C stops; use Resume with the same Output.' }
Push-Location -LiteralPath $projectRoot
try {
    & $python @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "P4 $Mode exited $LASTEXITCODE. Inspect runs/*/failure.json and worker stderr logs." }
}
finally { Pop-Location }
