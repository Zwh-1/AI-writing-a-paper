param([Parameter(Mandatory=$true)][string]$ApplicationRoot,[Parameter(Mandatory=$true)][string]$QaDirectory,[int]$Port=8818)
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference='Stop'
$taskFullRoot=[System.IO.Path]::GetFullPath($ApplicationRoot)
$taskActualPowerShell=(Get-Process -Id $PID).Path
if (-not $taskActualPowerShell.StartsWith($taskFullRoot,[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Use the PowerShell from the extracted ZIP for this acceptance.' }
$taskSourceRoot=Split-Path -Parent $PSScriptRoot
$env:PATH=(Join-Path $taskFullRoot '.runtime/powershell')+[System.IO.Path]::PathSeparator+$env:PATH
$taskAcceptancePath=Join-Path $taskFullRoot ('.runtime\release-acceptance-'+$Port+'.json')
$taskSessionPath=Join-Path $taskFullRoot ('.runtime\session-'+$Port+'.json')
$taskProcessPath=Join-Path $taskFullRoot ('.runtime\processes-'+$Port+'.json')
$taskBrowserSession=Join-Path $QaDirectory 'browser-session.json'
try {
    Write-Output 'Single-ZIP acceptance is using its own PowerShell 7.6.5 Core.'
    & (Join-Path $taskFullRoot '.runtime/python/python.exe') -I -X utf8 (Join-Path $PSScriptRoot 'verify_small_portable.py') --app $taskFullRoot --qa $QaDirectory --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Single ZIP runtime acceptance failed.' }
    $env:PAPER_PORTABLE_QA=[System.IO.Path]::GetFullPath($QaDirectory)
    & (Join-Path $taskFullRoot '.runtime/browser/node.exe') (Join-Path $PSScriptRoot 'portable-release-browser.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Single ZIP browser acceptance failed.' }
    Write-Output 'All single-ZIP runtime and browser acceptance checks passed.'
} finally {
    if (Test-Path -LiteralPath $taskAcceptancePath) {
        $taskAcceptance=Get-Content -LiteralPath $taskAcceptancePath -Raw | ConvertFrom-Json
        if ($taskAcceptance.root -eq $taskFullRoot) {
            foreach ($taskProcessId in @($taskAcceptance.worker_pid,$taskAcceptance.server_pid)) {
                $taskProcess=Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = "+$taskProcessId) -ErrorAction SilentlyContinue
                if ($taskProcess -and $taskProcess.CommandLine -and $taskProcess.CommandLine.Contains($taskFullRoot)) { Stop-Process -Id $taskProcessId -ErrorAction SilentlyContinue }
            }
        }
        Remove-Item -LiteralPath $taskAcceptancePath -Force
    }
    if (Test-Path -LiteralPath $taskSessionPath) { Remove-Item -LiteralPath $taskSessionPath -Force }
    if (Test-Path -LiteralPath $taskProcessPath) { Remove-Item -LiteralPath $taskProcessPath -Force }
    if (Test-Path -LiteralPath $taskBrowserSession) { Remove-Item -LiteralPath $taskBrowserSession -Force }
}
