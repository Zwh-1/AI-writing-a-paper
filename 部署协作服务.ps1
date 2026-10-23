param([switch]$CheckOnly,[string]$AdminName='admin')
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core'){throw 'Required PowerShell 7.6.5 Core'}
$ErrorActionPreference='Stop';$taskDirectory=Join-Path $PSScriptRoot 'deployment';$taskEnv=Join-Path $taskDirectory '.env';$taskSecretDir=Join-Path $taskDirectory 'private'
if(-not(Test-Path -LiteralPath $taskEnv)){throw '请先复制deployment/.env.example为.env，填写自己的域名、接口与模型ID。'}
$taskDocker=(Get-Command docker -ErrorAction Stop).Source
& $taskDocker compose --project-directory $taskDirectory -f (Join-Path $taskDirectory 'compose.yaml') config --quiet
if($LASTEXITCODE -ne 0){throw '部署配置检查失败'}
if($CheckOnly){Write-Output 'Compose configuration is valid.';exit}
New-Item -ItemType Directory -Path $taskSecretDir -Force|Out-Null
$taskSecret=Join-Path $taskSecretDir 'model-api-key.txt'
if(-not(Test-Path -LiteralPath $taskSecret)){
 $taskSecure=Read-Host '填写服务方模型API key（仅写入本机部署私密目录）' -AsSecureString
 $taskPlain=[System.Net.NetworkCredential]::new('',$taskSecure).Password
 [System.IO.File]::WriteAllText($taskSecret,$taskPlain,[System.Text.UTF8Encoding]::new($false));$taskPlain=$null;$taskSecure=$null
 $taskAcl=Get-Acl -LiteralPath $taskSecret;$taskAcl.SetAccessRuleProtection($true,$false);$taskIdentity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;$taskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($taskIdentity,'FullControl','Allow'));$taskAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new('SYSTEM','FullControl','Allow'));Set-Acl -LiteralPath $taskSecret -AclObject $taskAcl
}
& $taskDocker compose --project-directory $taskDirectory -f (Join-Path $taskDirectory 'compose.yaml') up -d --build
if($LASTEXITCODE -ne 0){throw '服务启动失败'}
$taskSecure=Read-Host '设置首次协作管理员密码（至少12字符）' -AsSecureString
$taskPlain=[System.Net.NetworkCredential]::new('',$taskSecure).Password
try{$taskPlain|& $taskDocker compose --project-directory $taskDirectory -f (Join-Path $taskDirectory 'compose.yaml') exec -T gateway python -I -X utf8 runner.py collaboration-admin $AdminName}finally{$taskPlain=$null;$taskSecure=$null}
if($LASTEXITCODE -ne 0){throw '管理员建立失败；已有账号时请使用现有账号登录'}
Write-Output '协作服务已启动。请通过配置的HTTPS域名登录，导入项目并分配成员权限。'
