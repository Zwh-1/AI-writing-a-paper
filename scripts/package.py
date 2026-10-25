from pathlib import Path
import json,zipfile,hashlib,os,re
ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'frontend/package.json').read_text(encoding='utf-8'))['version']
OUTPUT=ROOT.parent/f'砚台论文工作台_{VERSION}_便携版.zip'
EXCLUDED={'__pycache__','.pytest_cache','node_modules','tests','test','browser-data'}
def selected_files():
    folders=['backend','frontend/src','frontend/dist','prompts','scripts','.runtime/python','.runtime/python-deps','.runtime/advanced-deps','.runtime/browser','.runtime/browsers','.runtime/models/local','.runtime/writing-agent']
    individual=['runner.py','requirements.lock.txt','README.md','启动论文工作台.ps1','停止论文工作台.ps1','frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.ts','frontend/index.html','docs/实现与验收说明.md','docs/V2.0目标缺口审计与补齐记录.md','docs/0.2.1设置修复说明.md','docs/0.2.2余额与验收测试说明.md','docs/0.2.3API迁移说明.md','docs/0.3.0六项扩展实现与使用说明.md','docs/0.3.1资料解析修复说明.md','requirements-advanced.lock.txt','准备截图浏览器.ps1','准备隔离执行环境.ps1','部署协作服务.ps1','.dockerignore','deployment/Dockerfile','deployment/compose.yaml','deployment/Caddyfile','deployment/.env.example','deployment/requirements.server.lock.txt','deployment/README.md']
    for name in individual:
        p=ROOT/name
        if p.is_file():yield p
    if (ROOT/'docs/0.4.0有据写作实现与验收.md').is_file():yield ROOT/'docs/0.4.0有据写作实现与验收.md'
    if (ROOT/'docs/0.4.1剩余功能与验收说明.md').is_file():yield ROOT/'docs/0.4.1剩余功能与验收说明.md'
    for name in (f'docs/{VERSION}更新与升级说明.md','docs/文献库导入与使用说明.md','docs/文献知识库存储与检索.md','docs/英文摘要翻译说明.md','docs/资料与规则页面组件结构.md','docs/资料与规则AI托管说明.md','docs/从源码运行.md'):
        if (ROOT/name).is_file():yield ROOT/name
    if (ROOT/'打开真实验收审阅.ps1').is_file():yield ROOT/'打开真实验收审阅.ps1'
    for name in folders:
        folder=ROOT/name
        for p in folder.rglob('*'):
            rel=p.relative_to(folder)
            if p.is_file() and not any(part in (EXCLUDED-{'node_modules'} if name=='.runtime/browser' else EXCLUDED) for part in rel.parts) and p.suffix not in ('.pyc','.pyo','.log','.part','.tmp'):yield p
    for p in (ROOT/'tests').glob('*.py'):yield p
    for p in (ROOT/'tests').glob('*.cjs'):yield p
    for p in (ROOT/'tests').glob('*.ps1'):yield p

def main():
    assert OUTPUT.parent.resolve()==ROOT.parent.resolve()
    backend_version=re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]",(ROOT/'backend/app/config.py').read_text(encoding='utf-8'))
    lock=json.loads((ROOT/'frontend/package-lock.json').read_text(encoding='utf-8'))
    if not backend_version or {VERSION,backend_version[1],lock['version'],lock['packages']['']['version']}!={VERSION}:raise RuntimeError('Frontend, backend and lock file versions do not match')
    files=sorted(set(selected_files()),key=lambda p:str(p));manifest={};size=0
    partial=OUTPUT.with_suffix('.partial.zip')
    with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as archive:
        for p in files:
            rel=p.relative_to(ROOT).as_posix()
            if rel.startswith(('data/','.experimental/')) or p.suffix=='.dpapi' or p.name=='workbench.sqlite3' or rel.endswith('session.json') or (p.name.startswith('session-') and p.suffix=='.json'):raise RuntimeError('Private data was selected for packaging')
            with p.open('rb') as source:manifest[rel]=hashlib.file_digest(source,'sha256').hexdigest()
            size+=p.stat().st_size;archive.write(p,rel)
        archive.writestr('便携包清单.json',json.dumps({'version':VERSION,'files':manifest,'user_data_included':False,'api_key_included':False},ensure_ascii=False,indent=2))
    with zipfile.ZipFile(partial) as archive:
        for relative,expected in manifest.items():
            with archive.open(relative) as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=expected:raise RuntimeError('Package integrity verification failed: '+relative)
    os.replace(partial,OUTPUT)
    with OUTPUT.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    print(json.dumps({'package':str(OUTPUT),'version':VERSION,'files':len(files),'uncompressed_bytes':size,'package_bytes':OUTPUT.stat().st_size,'sha256':digest,'manifest_hashes_verified':True,'user_data_included':False,'api_key_included':False},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
