param([int]$Port=8765,[string]$DataDirectory='',[switch]$NoBrowser,[switch]$SkipSeed,[switch]$Stop,[switch]$PrepareResources)
if ($PSVersionTable.PSVersion.ToString() -cne '7.6.5' -or $PSVersionTable.PSEdition -cne 'Core') { throw 'Required PowerShell 7.6.5 Core. No version fallback is permitted.' }
$ErrorActionPreference='Stop'
$taskPortableRoot=Split-Path -Parent $PSScriptRoot
$taskPortableShell=Join-Path $taskPortableRoot '.runtime\powershell'
# Office/OCR helper processes inherit the same checked PowerShell distribution.
$env:PATH=$taskPortableShell+[System.IO.Path]::PathSeparator+$env:PATH
if ($PrepareResources) {
    & (Join-Path $taskPortableRoot '.runtime/python/python.exe') -I -X utf8 (Join-Path $taskPortableRoot 'runner.py') portable-resources
    if ($LASTEXITCODE -ne 0) { throw '资源准备未完成；请检查网络后再次运行，已下载的资源会保留。' }
} elseif ($Stop) {
    & (Join-Path $taskPortableRoot '停止论文工作台.ps1') -Port $Port
} else {
    & (Join-Path $taskPortableRoot '启动论文工作台.ps1') -Port $Port -DataDirectory $DataDirectory -NoBrowser:$NoBrowser -SkipSeed:$SkipSeed
    $taskManifestPath=Join-Path $taskPortableRoot '便携包清单.json'
    if (Test-Path -LiteralPath $taskManifestPath) {
        $taskManifest=Get-Content -LiteralPath $taskManifestPath -Raw | ConvertFrom-Json
        if ($taskManifest.automatic_resource_download) {
            & (Join-Path $taskPortableRoot '.runtime/python/python.exe') -I -X utf8 (Join-Path $taskPortableRoot 'runner.py') portable-resources-start
            if ($LASTEXITCODE -ne 0) { Write-Warning '资源自动准备未启动；请双击“准备便携资源.cmd”重试。' }
        }
    }
}
