param([switch]$InstallDependencies,[string]$PythonPath='')
$taskVersion=$PSVersionTable.PSVersion.ToString()
$taskEdition=$PSVersionTable.PSEdition
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
if ($InstallDependencies) {
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) { throw 'Pass -PythonPath pointing to a Python 3.12+ interpreter with pip. The packaged runtime does not need pip.' }
    $taskVersionCheck=& $PythonPath -c 'import sys;print(int(sys.version_info >= (3,12)))'
    if ($LASTEXITCODE -ne 0 -or $taskVersionCheck -ne '1') { throw 'Python 3.12+ is required.' }
    & $PythonPath -m pip install --target (Join-Path $taskRoot '.runtime\python-deps') -r (Join-Path $taskRoot 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & npm ci --prefix (Join-Path $taskRoot 'frontend') --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
}
& npm run build --prefix (Join-Path $taskRoot 'frontend')
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Write-Output 'Build completed. Run the launcher to open the workbench.'
