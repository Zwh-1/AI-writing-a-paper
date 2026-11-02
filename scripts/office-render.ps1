param([Parameter(Mandatory=$true)][string]$Source,[Parameter(Mandatory=$true)][string]$Output,[ValidateSet('pptx','docx')][string]$Kind,[string]$UpdatedDocx='')
$taskVersion=$PSVersionTable.PSVersion.ToString();$taskEdition=$PSVersionTable.PSEdition
if($taskVersion -cne '7.6.5' -or $taskEdition -cne 'Core'){throw 'Required PowerShell 7.6.5 Core'}
$ErrorActionPreference='Stop';$taskApp=$null;$taskDocument=$null;$taskBefore=0;$taskProg=$null;$taskSecurity=$null;$taskAlerts=$null
if(-not(Test-Path -LiteralPath $Source)){throw 'Source file missing'}
if([System.IO.Path]::GetExtension($Source) -cne ('.'+$Kind)){throw 'Unexpected source extension'}
if($UpdatedDocx -and ($Kind -ne 'docx' -or [System.IO.Path]::GetFullPath($UpdatedDocx) -eq [System.IO.Path]::GetFullPath($Source))){throw 'Updated document must be a separate DOCX output'}
try{
 $taskCandidates=if($Kind -eq 'pptx'){@('PowerPoint.Application','KWPP.Application')}else{@('Word.Application','KWPS.Application')}
 foreach($taskName in $taskCandidates){try{$taskApp=New-Object -ComObject $taskName -ErrorAction Stop;$taskProg=$taskName;break}catch{}}
 if(-not $taskApp){throw 'No Office COM renderer available'}
 try{$taskSecurity=$taskApp.AutomationSecurity;$taskApp.AutomationSecurity=3}catch{throw 'Cannot disable Office automation macros'}
 if($Kind -eq 'pptx'){
  $taskBefore=$taskApp.Presentations.Count
  try{$taskAlerts=$taskApp.DisplayAlerts;$taskApp.DisplayAlerts=1}catch{}
  $taskDocument=$taskApp.Presentations.Open([System.IO.Path]::GetFullPath($Source),-1,0,0)
  try{$taskDocument.PrintOptions.PrintHiddenSlides=-1;$taskDocument.PrintOptions.OutputType=1}catch{}
  $taskPages=$taskDocument.Slides.Count
  $taskDocument.SaveAs([System.IO.Path]::GetFullPath($Output),32)
 }else{
  $taskBefore=$taskApp.Documents.Count
  if($taskBefore -eq 0){$taskApp.Visible=$false};$taskAlerts=$taskApp.DisplayAlerts;$taskApp.DisplayAlerts=0
  $taskDocument=$taskApp.Documents.Open([System.IO.Path]::GetFullPath($Source),$false,$true,$false)
  try{$taskDocument.Fields.Update()|Out-Null}catch{}
  try{$taskDocument.Repaginate();foreach($taskToc in $taskDocument.TablesOfContents){$taskToc.Update()};foreach($taskFigures in $taskDocument.TablesOfFigures){$taskFigures.Update()};$taskDocument.Fields.Update()|Out-Null}catch{}
  if($UpdatedDocx){
   try{$taskDocument.SaveAs2([System.IO.Path]::GetFullPath($UpdatedDocx),16)}catch{$taskDocument.SaveAs([System.IO.Path]::GetFullPath($UpdatedDocx),16)}
   if(-not(Test-Path -LiteralPath $UpdatedDocx)){throw 'Updated DOCX was not saved'}
  }
  try{$taskDocument.ExportAsFixedFormat([System.IO.Path]::GetFullPath($Output),17)}catch{$taskDocument.SaveAs([System.IO.Path]::GetFullPath($Output),17)}
  $taskPages=$null
 }
 if(-not(Test-Path -LiteralPath $Output)){throw 'Renderer did not produce PDF'}
 [pscustomobject]@{engine=$taskProg;version=[string]$taskApp.Version;source_readonly=$true;pages=$taskPages;updated_docx_written=[bool]$UpdatedDocx}|ConvertTo-Json -Compress
}finally{
 if($taskDocument){try{if($Kind -eq 'pptx'){$taskDocument.Close()}else{$taskDocument.Close(0)}}catch{};[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskDocument)}
 if($taskApp){try{if($null -ne $taskSecurity){$taskApp.AutomationSecurity=$taskSecurity};if($null -ne $taskAlerts){$taskApp.DisplayAlerts=$taskAlerts};$taskRemaining=if($Kind -eq 'pptx'){$taskApp.Presentations.Count}else{$taskApp.Documents.Count};if($taskBefore -eq 0 -and $taskRemaining -eq 0){$taskApp.Quit()}}catch{};[void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($taskApp)}
 [GC]::Collect();[GC]::WaitForPendingFinalizers()
}
