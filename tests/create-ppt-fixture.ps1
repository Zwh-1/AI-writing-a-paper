param([Parameter(Mandatory=$true)][string]$Output)
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core'){throw 'Required PowerShell 7.6.5 Core'}
$ErrorActionPreference='Stop';$taskApp=$null;$taskPresentation=$null;$taskBefore=0;$taskSecurity=$null
try{
 $taskApp=New-Object -ComObject PowerPoint.Application;$taskBefore=$taskApp.Presentations.Count;$taskSecurity=$taskApp.AutomationSecurity;$taskApp.AutomationSecurity=3
 $taskPresentation=$taskApp.Presentations.Add(0);$taskPresentation.PageSetup.SlideWidth=960;$taskPresentation.PageSetup.SlideHeight=540
 foreach($taskPage in @(1,2)){
  $taskSlide=$taskPresentation.Slides.Add($taskPage,12);$taskTitle=$taskSlide.Shapes.AddTextbox(1,50,20,800,55);$taskTitle.TextFrame.TextRange.Text='续表集成测试样例 第'+$taskPage+'页';$taskTitle.TextFrame.TextRange.Font.Size=24
  $taskTable=$taskSlide.Shapes.AddTable(3,2,60,120,820,280).Table;$taskValues=if($taskPage -eq 1){@(@('编号','指标'),@('1','规则加载'),@('2','原文定位'))}else{@(@('编号','指标'),@('3','引用检查'),@('4','格式核对'))}
  for($taskRow=1;$taskRow -le 3;$taskRow++){for($taskColumn=1;$taskColumn -le 2;$taskColumn++){$taskText=$taskTable.Cell($taskRow,$taskColumn).Shape.TextFrame.TextRange;$taskText.Text=$taskValues[$taskRow-1][$taskColumn-1];$taskText.Font.Size=22}}
 }
 $taskPresentation.SaveAs([System.IO.Path]::GetFullPath($Output),24)
 [pscustomobject]@{file=$Output;slides=2;fixture_only=$true}|ConvertTo-Json -Compress
}finally{if($taskPresentation){$taskPresentation.Close();[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskPresentation)};if($taskApp){try{$taskApp.AutomationSecurity=$taskSecurity;if($taskBefore -eq 0 -and $taskApp.Presentations.Count -eq 0){$taskApp.Quit()}}catch{};[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskApp)};[GC]::Collect()}
