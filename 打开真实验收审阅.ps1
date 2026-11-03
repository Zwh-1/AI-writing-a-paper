param([int]$Port=8791)
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference='Stop'
$taskReviewData=Join-Path $PSScriptRoot '.runtime\writing-v4-live-data'
if (-not (Test-Path -LiteralPath (Join-Path $taskReviewData 'workbench.sqlite3'))) { throw '真实验收资料不在此安装目录；便携包不包含本机私人验收数据。' }
& (Join-Path $PSScriptRoot '启动论文工作台.ps1') -Port $Port -DataDirectory $taskReviewData -SkipSeed -NoBrowser | Out-Null
$taskReviewSession=Get-Content -LiteralPath (Join-Path $PSScriptRoot ('.runtime\session-'+$Port+'.json')) -Raw | ConvertFrom-Json
$taskReviewUrl='http://127.0.0.1:'+$Port+'/#token='+[uri]::EscapeDataString($taskReviewSession.bootstrap_token)
Start-Process $taskReviewUrl
Write-Output '已打开隔离的真实验收工作台。进入“有据写作 → 模型诊断”可逐项盲评；正式论文数据保留在原工作台。'
