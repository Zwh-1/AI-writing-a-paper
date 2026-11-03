param([int]$Port=8765)
$taskVersion=$PSVersionTable.PSVersion.ToString()
$taskEdition=$PSVersionTable.PSEdition
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$taskState=Join-Path $PSScriptRoot (".runtime\processes-"+$Port+".json")
if (-not (Test-Path -LiteralPath $taskState)) { Write-Output 'No registered processes.';exit }
$taskSaved=Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
foreach ($taskPid in @($taskSaved.worker_pid,$taskSaved.server_pid)) {
    $taskProcess=Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $taskPid" -ErrorAction SilentlyContinue
    $taskVerified=$taskProcess -and $taskProcess.CommandLine -and $taskProcess.CommandLine.Contains($taskSaved.runner)
    if (-not $taskVerified -and $taskSaved.runner -eq (Join-Path $PSScriptRoot 'runner.py')) {
        $taskFallback=Get-Process -Id $taskPid -ErrorAction SilentlyContinue
        if ($taskFallback -and $taskFallback.Path -eq (Join-Path $PSScriptRoot '.runtime\python\python.exe') -and $taskSaved.started_at) {
            $taskStartValue=if ($taskPid -eq $taskSaved.worker_pid -and $taskSaved.worker_started_at) { $taskSaved.worker_started_at } else { $taskSaved.started_at }
            $taskExpectedTime=[DateTimeOffset]::Parse($taskStartValue).LocalDateTime
            $taskVerified=[Math]::Abs(($taskFallback.StartTime-$taskExpectedTime).TotalSeconds) -lt 30
        }
    }
    if ($taskVerified) { Stop-Process -Id $taskPid -ErrorAction SilentlyContinue }
}
Write-Output 'Registered workbench processes stopped; saved projects remain available.'
