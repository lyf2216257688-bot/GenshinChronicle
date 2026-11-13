# Model-free export/validation of the selected candidate interface.
[CmdletBinding()]
param(
    [ValidateSet('Export','Validate')][string]$Mode = 'Validate',
    [string]$Selection = 'config/reranking-selected-v1.json',
    [string]$Output,
    [ValidateSet('bge_structural','bge_relevance')][string]$Profile
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$selectionPath = [IO.Path]::GetFullPath($Selection, $projectRoot)
$settings = Get-Content -LiteralPath $selectionPath -Raw -Encoding utf8 | ConvertFrom-Json
$python = [IO.Path]::GetFullPath($settings.python, $projectRoot)
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$pythonArgs = @('-B','-u','-m','genshin_corpus.retrieval.clean_based.rerank_handoff',
    $Mode.ToLowerInvariant(),'--repo',$projectRoot,'--selection',$selectionPath)
if ($Output) { $pythonArgs += @('--output',[IO.Path]::GetFullPath($Output,$projectRoot)) }
if ($Profile) { $pythonArgs += @('--profile',$Profile) }
Push-Location -LiteralPath $projectRoot
try {
    & $python @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw "Reranking handoff $Mode failed; preserve the evidence and inspect the error." }
}
finally { Pop-Location }
