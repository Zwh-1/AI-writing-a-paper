param([switch]$OpenDesktop)
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference='Stop';$taskDocker=Get-Command docker -ErrorAction SilentlyContinue
if(-not $taskDocker){throw '本机尚未安装Docker，请先安装并启动Docker Desktop的Linux容器引擎。'}
$taskPrincipal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if($taskPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {try{Start-Service com.docker.service -ErrorAction Stop}catch{Write-Warning 'Windows服务未成功启动，请在Docker Desktop中核对引擎状态。'}}
$taskApp=Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
if(Test-Path -LiteralPath $taskApp){if($OpenDesktop){Start-Process -FilePath $taskApp -WindowStyle Normal}else{Start-Process -FilePath $taskApp -ArgumentList '--background' -WindowStyle Hidden}}
Write-Output '已请求启动Docker。若需要首次初始化，请打开Docker Desktop完成；脚本不更改WSL或Windows可选功能，不触发重启。'
& $taskDocker.Source info --format '{{.ServerVersion}}'
if($LASTEXITCODE -ne 0){throw 'Docker引擎尚不可用。请使用管理员权限的PowerShell 7.6.5启动现有Docker服务，再返回工作台准备执行镜像。'}
Write-Output 'Docker引擎已就绪，可在高级研究与协作中准备Python镜像并隔离运行代码。'
