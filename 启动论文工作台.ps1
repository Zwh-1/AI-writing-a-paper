param([int]$Port=8765,[string]$DataDirectory='',[switch]$NoBrowser,[switch]$SkipSeed)
$taskVersion=$PSVersionTable.PSVersion.ToString()
$taskEdition=$PSVersionTable.PSEdition
if ($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core') { throw 'Required: PowerShell 7.6.5 Core. No version fallback is permitted.' }
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$taskRuntime=Join-Path $taskRoot '.runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
$taskCandidates=@((Join-Path $taskRoot '.runtime\python\python.exe'),$env:PAPER_PYTHON,'C:\Users\abcta\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
$taskPython=$null
foreach ($taskCandidate in $taskCandidates) {
    if ($taskCandidate -and (Test-Path -LiteralPath $taskCandidate)) {
        $taskCheck=& $taskCandidate -c 'import sys;print(int(sys.version_info >= (3,12)))'
        if ($LASTEXITCODE -eq 0 -and $taskCheck -eq '1') { $taskPython=$taskCandidate; break }
    }
}
if (-not $taskPython) { throw 'Python 3.12+ was not found. Set PAPER_PYTHON to the installed interpreter.' }
$env:PYTHONUTF8='1'
$env:PAPER_PORT=[string]$Port
if ($DataDirectory) { $env:PAPER_DATA_DIR=[System.IO.Path]::GetFullPath($DataDirectory) } else { Remove-Item Env:PAPER_DATA_DIR -ErrorAction SilentlyContinue }
$taskRunner=Join-Path $taskRoot 'runner.py'
$taskReady=$false
try { $taskHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2 -NoProxy; $taskReady=$taskHealth.status -eq 'ok' } catch { }
if ($taskReady -and $DataDirectory) {
    $taskExistingSessionPath=Join-Path $taskRuntime ("session-"+$Port+".json")
    if (Test-Path -LiteralPath $taskExistingSessionPath) {
        $taskExistingSession=Get-Content -LiteralPath $taskExistingSessionPath -Raw | ConvertFrom-Json
        if (-not $taskExistingSession.data_directory -or $taskExistingSession.data_directory -ne $env:PAPER_DATA_DIR) { throw 'The running instance uses another or unverified data directory. Stop it first or use another -Port.' }
    }
}
if (-not $taskReady) {
    if (-not $SkipSeed) {
        & $taskPython -X utf8 $taskRunner seed
        if ($LASTEXITCODE -ne 0) { throw 'Project initialization failed.' }
    }
    $taskServer=Start-Process -FilePath $taskPython -ArgumentList @('-X','utf8',('"'+$taskRunner+'"'),'server') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime ('server-'+$Port+'.out.log')) -RedirectStandardError (Join-Path $taskRuntime ('server-'+$Port+'.error.log'))
    $taskWorker=Start-Process -FilePath $taskPython -ArgumentList @('-X','utf8',('"'+$taskRunner+'"'),'worker') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime ('worker-'+$Port+'.out.log')) -RedirectStandardError (Join-Path $taskRuntime ('worker-'+$Port+'.error.log'))
    @{server_pid=$taskServer.Id;worker_pid=$taskWorker.Id;runner=$taskRunner;data_directory=$env:PAPER_DATA_DIR;started_at=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRuntime ("processes-"+$Port+".json")) -Encoding utf8NoBOM
    for ($taskAttempt=0;$taskAttempt -lt 40;$taskAttempt++) {
        try { $taskHealth=Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 1 -NoProxy; if ($taskHealth.status -eq 'ok') { $taskReady=$true;break } } catch { }
        Start-Sleep -Milliseconds 250
    }
}
# A healthy browser server does not prove the independent task worker is alive.
$taskProcessState=Join-Path $taskRuntime ("processes-"+$Port+".json")
if ($taskReady -and (Test-Path -LiteralPath $taskProcessState)) {
    $taskRegistered=Get-Content -LiteralPath $taskProcessState -Raw | ConvertFrom-Json
    $taskWorkerProcess=Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId = "+$taskRegistered.worker_pid) -ErrorAction SilentlyContinue
    $taskWorkerValid=$taskWorkerProcess -and $taskWorkerProcess.CommandLine -and $taskWorkerProcess.CommandLine.Contains($taskRunner)
    if (-not $taskWorkerValid -and $taskRegistered.runner -eq $taskRunner -and $taskRegistered.started_at) {
        $taskFallbackWorker=Get-Process -Id $taskRegistered.worker_pid -ErrorAction SilentlyContinue
        if ($taskFallbackWorker -and $taskFallbackWorker.Path -eq $taskPython) {
            $taskWorkerStartValue=if ($taskRegistered.worker_started_at) { $taskRegistered.worker_started_at } else { $taskRegistered.started_at }
            $taskExpectedStart=[DateTimeOffset]::Parse($taskWorkerStartValue).LocalDateTime
            $taskWorkerValid=[Math]::Abs(($taskFallbackWorker.StartTime-$taskExpectedStart).TotalSeconds) -lt 30
        }
    }
    # A worker started before the newest backend source change runs stale code
    # and would keep executing tasks with the old logic; replace it.
    if ($taskWorkerValid) {
        $taskNewestSource=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'backend\app') -Filter '*.py' -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($taskNewestSource) {
            $taskWorkerLive=Get-Process -Id $taskRegistered.worker_pid -ErrorAction SilentlyContinue
            if ($taskWorkerLive -and $taskWorkerLive.StartTime -lt $taskNewestSource.LastWriteTime) {
                Write-Output ('Worker ' + $taskRegistered.worker_pid + ' was started before backend code changed (' + $taskNewestSource.Name + '); restarting it with current code.')
                Stop-Process -Id $taskRegistered.worker_pid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 500
                $taskWorkerValid=$false
            }
        }
    }
    if (-not $taskWorkerValid) {
        $taskRecoveredWorker=Start-Process -FilePath $taskPython -ArgumentList @('-X','utf8',('"'+$taskRunner+'"'),'worker') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime ('worker-'+$Port+'.out.log')) -RedirectStandardError (Join-Path $taskRuntime ('worker-'+$Port+'.error.log'))
        $taskRegistered.worker_pid=$taskRecoveredWorker.Id
        $taskRegistered | Add-Member -NotePropertyName worker_started_at -NotePropertyValue (Get-Date).ToString('o') -Force
        $taskRegistered | ConvertTo-Json | Set-Content -LiteralPath $taskProcessState -Encoding utf8NoBOM
    }
}
if (-not $taskReady) { throw 'Server did not become ready. Inspect .runtime/server.error.log.' }
$taskSession=Get-Content -LiteralPath (Join-Path $taskRuntime ("session-"+$Port+".json")) -Raw | ConvertFrom-Json
$taskUrl="http://127.0.0.1:$Port/#token=$([uri]::EscapeDataString($taskSession.bootstrap_token))"
Write-Output "Paper Workbench is ready: $taskUrl"
if (-not $NoBrowser) { Start-Process $taskUrl }

