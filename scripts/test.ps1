param([switch]$Browser)
$taskVersion=$PSVersionTable.PSVersion.ToString()
$taskEdition=$PSVersionTable.PSEdition
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPython=Join-Path $taskRoot '.runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Packaged Python runtime is missing.' }
& $taskPython -I -X utf8 (Join-Path $taskRoot 'runner.py') test ('--junitxml='+(Join-Path $taskRoot 'docs\backend-tests.xml'))
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
if ($Browser) {
    try {
        & (Join-Path $taskRoot '启动论文工作台.ps1') -Port 8766 -DataDirectory (Join-Path $taskRoot '.runtime\browser-data') -SkipSeed -NoBrowser
        & node (Join-Path $taskRoot 'tests\browser-smoke.cjs')
        if ($LASTEXITCODE -ne 0) { throw 'Browser tests failed.' }
    } finally { & (Join-Path $taskRoot '停止论文工作台.ps1') -Port 8766 }
}
