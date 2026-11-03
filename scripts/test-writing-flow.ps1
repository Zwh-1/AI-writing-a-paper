param([switch]$Frontend)
if ($PSVersionTable.PSVersion -le [version]'7.6.0' -or $PSVersionTable.PSEdition -ne 'Core') { throw 'Requires PowerShell > 7.6.0 Core' }
$ErrorActionPreference = 'Stop'
$writingRoot = Split-Path -Parent $PSScriptRoot
$writingPython = Join-Path $writingRoot '.runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $writingPython)) { throw 'Packaged Python runtime is missing' }
$writingData = Join-Path $writingRoot ('.runtime\writing-flow-check-' + [guid]::NewGuid().ToString('N'))
$previousData = $env:PAPER_DATA_DIR
try {
    $env:PAPER_DATA_DIR = Join-Path $writingData 'bootstrap'
    Push-Location -LiteralPath $writingRoot
    try {
        $writingCode = @'
import sys
sys.path.insert(0, '.')
import runner
import os
from pathlib import Path
from app import db
db.init()
import pytest
tests = [
    'tests/test_writing_flow_integration.py',
    'tests/test_writing_flow_safety.py',
    'tests/test_writing_retrieval.py',
    'tests/test_writing_material_flow_http.py',
    'tests/test_writing_evidence_quality.py',
    'tests/test_writing_originality.py',
    'tests/test_writing_tool_runtime.py',
    'tests/test_writing_retrieval_performance.py',
    'tests/test_writing_ops.py',
    'tests/test_writing_integrity.py',
    'tests/test_paper_writing.py',
    'tests/test_paper_revision.py',
    'tests/test_writing_context_packing.py',
    'tests/test_api_v2.py',
    'tests/test_polishing.py',
]
report=Path(os.environ['PAPER_DATA_DIR']).parent/'results.xml'
raise SystemExit(pytest.main([*tests, '-q', '--disable-warnings', '--junitxml='+str(report)]))
'@
        $writingCode | & $writingPython -I -X utf8 -
        if ($LASTEXITCODE -ne 0) { throw 'Writing flow regression tests failed' }
        Write-Output ('Writing verification report: ' + (Join-Path $writingData 'results.xml'))
        if ($Frontend) {
            & node.exe 'frontend/node_modules/typescript/bin/tsc' --noEmit -p 'frontend/tsconfig.json'
            if ($LASTEXITCODE -ne 0) { throw 'Writing frontend type check failed' }
            & node.exe 'tests/writing-evidence-editor.cjs'
            if ($LASTEXITCODE -ne 0) { throw 'Writing editor roundtrip test failed' }
        }
    } finally { Pop-Location }
} finally {
    if ($null -eq $previousData) { Remove-Item Env:PAPER_DATA_DIR -ErrorAction SilentlyContinue }
    else { $env:PAPER_DATA_DIR = $previousData }
}
