# Run in a standalone PowerShell 7 terminal and keep that terminal open.
[CmdletBinding()]
param(
    [ValidateSet('Preflight','Run','Resume','Validate')][string]$Mode = 'Preflight',
    [string]$Output = 'data/retrieval/index-benchmark-v1-20261109',
    [string]$Config = 'config/index-benchmark-v1.json',
    [string]$InputManifest,
    [switch]$AllowSubset,
    [string]$Query,
    [string]$Questions,
    [ValidateRange(1,1000)][int]$TopK = 20
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$configPath = [IO.Path]::GetFullPath($Config, $projectRoot)
$settings = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json
$python = [IO.Path]::GetFullPath($settings.coordinator_python, $projectRoot)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Missing prepared runtime $python" }
$outputPath = [IO.Path]::GetFullPath($Output, $projectRoot)
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$pythonArgs = @('-u','-m','genshin_corpus.indexing.benchmark',$Mode.ToLowerInvariant(),
    '--repo',$projectRoot,'--config',$configPath,'--output',$outputPath,'--top-k',"$TopK")
if ($InputManifest) { $pythonArgs += @('--input-manifest',[IO.Path]::GetFullPath($InputManifest,$projectRoot)) }
if ($AllowSubset) { $pythonArgs += '--allow-subset' }
if ($Query) { $pythonArgs += @('--query',$Query) }
if ($Questions) { $pythonArgs += @('--questions',[IO.Path]::GetFullPath($Questions,$projectRoot)) }
Write-Host "Standalone index experiment $Mode. Output $outputPath"
Write-Host 'Codex may close. Keep this system terminal open. Resume uses the same output and options.'
Push-Location -LiteralPath $projectRoot
try {
    & $python @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Index experiment $Mode exited $LASTEXITCODE. Inspect output reports, failures and logs." }
}
finally { Pop-Location }
