#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Preflight', 'Run', 'Resume', 'Validate')][string]$Mode = 'Preflight',
    [ValidateSet('Quick', 'Extended')][string]$Suite = 'Quick',
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$SealSha256,
    [string]$Execution = 'data/retrieval/incremental-execution-20261125-v2',
    [string]$Output = 'data/retrieval/vc-20261124'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$frozen = [IO.Path]::GetFullPath($Execution, $repo)
$runRoot = [IO.Path]::GetFullPath($Output, $repo)
$seal = Join-Path $frozen 'complete.json'
if ((Get-FileHash -LiteralPath $seal -Algorithm SHA256).Hash -ne $SealSha256) {
    throw 'Execution seal hash differs.'
}
$binding = Get-Content -LiteralPath $seal -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
$files = $binding.files
$runner = 'scripts/retrieval/snapshot_full_run.py'
$python = [IO.Path]::GetFullPath($binding.metadata.runtime_paths.dense, $repo)
if ((Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash -ne $binding.metadata.runtimes.dense.files[$python] -or
    (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash -ne $files['scripts/retrieval/run-incremental-corpus.ps1']) {
    throw 'Launcher or interpreter differs from the execution seal.'
}
$script = Join-Path $frozen $runner
foreach ($name in $files.Keys) {
    $file = [IO.Path]::GetFullPath($name, $frozen)
    if (-not $file.StartsWith($frozen + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase) -or
        (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $files[$name]) {
        throw "Frozen file differs: $name"
    }
}

$environment = @{
    PYTHONPATH = Join-Path $frozen 'src'
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
    PYTHONDONTWRITEBYTECODE = '1'
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    HF_HUB_DISABLE_TELEMETRY = '1'
    HF_HUB_DISABLE_PROGRESS_BARS = '1'
    TOKENIZERS_PARALLELISM = 'false'
}
$saved = @{}
foreach ($key in $environment.Keys) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $environment[$key], 'Process')
}
Push-Location -LiteralPath $repo
try {
    & $python -B -u $script $Mode --suite $Suite --repo $repo --execution $frozen --output $runRoot --seal $SealSha256 --owner $PID
    if ($LASTEXITCODE -ne 0) { throw "GC $Mode exited $LASTEXITCODE. Inspect the stage and its log before Resume." }
}
finally {
    Pop-Location
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}
