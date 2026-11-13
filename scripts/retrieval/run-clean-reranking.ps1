# Run real Preflight/Smoke/70Q manually in an independent PowerShell 7 window.
[CmdletBinding()]
param(
    [ValidateSet('Prepare','Preflight','Smoke','Run','Resume','Validate','Report','Analyze','Status')]
    [string]$Mode = 'Status',
    [string]$Config = 'config/clean-reranking-v1.json',
    [string]$Output,
    [ValidateSet('bge','gte')][string]$Model
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$configPath = [IO.Path]::GetFullPath($Config, $projectRoot)
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$python = [IO.Path]::GetFullPath($settings.python, $projectRoot)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Prepare the CPython 3.14.5 reranker environment first.' }
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$pythonArgs = @('-B','-u','-m','genshin_corpus.retrieval.clean_based.rerank_job',
    $Mode.ToLowerInvariant(),'--repo',$projectRoot,'--config',$configPath)
if ($Output) { $pythonArgs += @('--output',[IO.Path]::GetFullPath($Output,$projectRoot)) }
if ($Model) { $pythonArgs += @('--model',$Model) }
Push-Location -LiteralPath $projectRoot
try {
    & $python @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Reranking $Mode failed. Inspect failures/ and workers/*/stderr.log; do not delete old attempts." }
}
finally { Pop-Location }
