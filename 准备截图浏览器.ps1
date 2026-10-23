param([switch]$NoDownload)
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core'){throw 'Required PowerShell 7.6.5 Core'}
$taskBrowser=Join-Path $PSScriptRoot '.runtime\browser';$taskNode=Join-Path $taskBrowser 'node.exe';$taskCli=Join-Path $taskBrowser 'node_modules\playwright\cli.js'
if(-not(Test-Path -LiteralPath $taskNode) -or -not(Test-Path -LiteralPath $taskCli)){throw '浏览器运行时不完整，请使用完整便携包或设置PAPER_NODE及PAPER_PLAYWRIGHT。'}
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $PSScriptRoot '.runtime\browsers'
if($NoDownload){& $taskNode -e 'const p=require(process.argv[1]);const fs=require("fs");console.log(fs.existsSync(p.chromium.executablePath())?"Chromium ready":"Chromium missing")' (Join-Path $taskBrowser 'node_modules\playwright');exit}
& $taskNode $taskCli install chromium
if($LASTEXITCODE -ne 0){throw 'Chromium下载失败，请核对网络后重试；此操作不使用模型API key。'}
