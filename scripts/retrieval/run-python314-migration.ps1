# Baseline/Candidate/Replay perform finite real inference: operator-started only.
[CmdletBinding()]
param(
    [ValidateSet('Inventory','Baseline','Candidate','Compare','Activate','Replay')][string]$Mode = 'Inventory',
    [string]$Config = 'config/python314-migration-v1.json',
    [string]$Output
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if ($Mode -in @('Baseline','Replay') -and (Test-Path -LiteralPath (Join-Path $projectRoot '.local/runtime-py314/retired-entrypoints.json'))) {
    throw 'Historical Python 3.12 Baseline/Replay entrypoints are retired after validated migration. Inspect the preserved witnesses.'
}
$configPath = [IO.Path]::GetFullPath($Config, $projectRoot)
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$relativePython = if ($Mode -in @('Baseline','Replay')) { $settings.baseline_python } else { $settings.python }
$python = [IO.Path]::GetFullPath($relativePython, $projectRoot)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Required runtime is missing; retain old environments.' }
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:OPENBLAS_NUM_THREADS = '4'
$env:OMP_NUM_THREADS = '4'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$pythonArgs = @('-B','-u','-m','genshin_corpus.runtime_migration',$Mode.ToLowerInvariant(),
    '--repo',$projectRoot,'--config',$configPath)
if ($Output) { $pythonArgs += @('--output',$Output) }
Push-Location -LiteralPath $projectRoot
try {
    & $python @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Migration $Mode failed; preserve old environments and indexes." }
}
finally { Pop-Location }
