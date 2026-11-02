param([Parameter(Mandatory=$true)][string]$PackagePath,[switch]$VerifyOnly)
if ($PSVersionTable.PSVersion.ToString() -cne '7.6.5' -or $PSVersionTable.PSEdition -cne 'Core') { throw 'Required PowerShell 7.6.5 Core. No version fallback is permitted.' }
$ErrorActionPreference='Stop'
$taskComponentPython=Join-Path $PSScriptRoot '.runtime\python\python.exe'
$taskComponentScript=Join-Path $PSScriptRoot 'scripts\install_portable_component.py'
if (-not (Test-Path -LiteralPath $taskComponentPython) -or -not (Test-Path -LiteralPath $taskComponentScript)) { throw '请先解压精简便携版，再运行此脚本。' }
$taskComponentFile=(Resolve-Path -LiteralPath $PackagePath).Path
$taskComponentArgs=@('-I','-X','utf8',$taskComponentScript,$taskComponentFile)
if ($VerifyOnly) { $taskComponentArgs+='--verify-only' }
& $taskComponentPython @taskComponentArgs
if ($LASTEXITCODE -ne 0) { throw '组件校验或安装失败，请查看上方原因；不要将组件覆盖到用户数据目录。' }
if (-not $VerifyOnly) { Write-Output '离线组件安装完成；请重启工作台后使用对应功能。' }
