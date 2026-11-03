from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules,collect_data_files,collect_dynamic_libs
root=Path(SPECPATH).parents[1]
hidden=collect_submodules('app')
for module in ('winrt','fastembed','latex2mathml','pypdfium2','reportlab','jieba'):
    hidden+=collect_submodules(module)
data=[(str(root/'backend'),'backend'),(str(root/'backend/app'),'app'),(str(root/'backend/migrations'),'migrations'),(str(root/'prompts'),'prompts'),(str(root/'frontend/dist'),'frontend/dist'),(str(root/'runner.py'),'.'),(str(root/'services'),'services'),(str(root/'scripts'),'scripts'),(str(root/'deployment/execution.Dockerfile'),'deployment')]
binaries=[]
for module in ('pypdfium2_raw','onnxruntime'):
    binaries+=collect_dynamic_libs(module)
for module in ('pypdfium2','reportlab','jieba','latex2mathml','fastembed'):
    data+=collect_data_files(module)
analysis=Analysis([str(root/'deployment/desktop/launcher.py')],pathex=[str(root/'backend')],binaries=binaries,datas=data,hiddenimports=hidden,hookspath=[],runtime_hooks=[],excludes=['pytest','tkinter'],noarchive=False)
pyz=PYZ(analysis.pure)
exe=EXE(pyz,analysis.scripts,[],exclude_binaries=True,name='Yantai',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True)
collection=COLLECT(exe,analysis.binaries,analysis.datas,strip=False,upx=False,name='Yantai')
if sys.platform=='darwin':
    app=BUNDLE(collection,name='Yantai.app',icon=None,bundle_identifier='org.yantai.workbench',info_plist={'CFBundleDisplayName':'砚台论文工作台','NSHighResolutionCapable':True})
