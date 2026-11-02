param([switch]$NoDownload)
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core'){throw 'Required PowerShell 7.6.5 Core'}
$taskBrowser=Join-Path $PSScriptRoot '.runtime\browser';$taskNode=Join-Path $taskBrowser 'node.exe';$taskCli=Join-Path $taskBrowser 'node_modules\playwright-core\cli.js'
if(-not(Test-Path -LiteralPath $taskNode) -or -not(Test-Path -LiteralPath $taskCli)){throw '浏览器运行时不完整，请重新解压精简便携版或完整便携版。'}
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $PSScriptRoot '.runtime\browsers'
if($NoDownload){& $taskNode -e 'const fs=require("fs"),path=require("path");const root=process.argv[1],m=require(path.join(root,"node_modules/playwright-core/browsers.json"));const b=m.browsers.find(x=>x.name==="chromium-headless-shell");const exe=path.join(process.env.PLAYWRIGHT_BROWSERS_PATH,"chromium_headless_shell-"+b.revision,"chrome-headless-shell-win64/chrome-headless-shell.exe");if(!fs.existsSync(exe)){console.error("截图浏览器未安装；请安装离线组件或运行本脚本下载。");process.exitCode=1}else{console.log("Chromium headless shell ready")}' $taskBrowser;exit $LASTEXITCODE}
& $taskNode $taskCli install chromium --only-shell
if($LASTEXITCODE -ne 0){throw 'Chromium下载失败，请核对网络后重试；此操作不使用模型API key。'}
