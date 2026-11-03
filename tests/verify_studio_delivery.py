"""Compile a synthetic export and exercise live/offline data commands."""
import io,json,os,subprocess,sys,tempfile,time,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps')]
import httpx

def main():
    folder=ROOT/'.runtime/studio-acceptance-20261102';fixture=json.loads((folder/'fixture.json').read_text(encoding='utf-8'));origin='http://127.0.0.1:'+str(fixture['port']);session=json.loads((ROOT/'.runtime'/f'session-{fixture["port"]}.json').read_text(encoding='utf-8'))
    checks=[]
    with httpx.Client(base_url=origin,trust_env=False,timeout=30) as client:
        response=client.post('/api/v1/session',json={'token':session['bootstrap_token']});response.raise_for_status();client.headers['X-CSRF-Token']=response.json()['csrf']
        base='/api/v1/projects/'+fixture['project_id']+'/studio'
        english=client.post('/api/v1/projects',json={'title':'Synthetic English manuscript'}).json()['id']
        english_section=client.post('/api/v1/projects/'+english+'/sections',json={'title':'Methods'}).json()['id']
        document={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':'english-paragraph'},'content':[{'type':'text','text':'A synthetic observation retains 50% of the examples. Inline math: '},{'type':'inlineFormula','attrs':{'latex':'x^2+1'}}]},{'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'Sample'}]}]},{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'Count'}]}]}]},{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'Synthetic'}]}]},{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'12'}]}]}]}]}]}
        saved=client.put('/api/v1/sections/'+english_section+'/content',json={'document':document,'base_revision_id':None});saved.raise_for_status()
        for language in ('en','zh'):
            export_base=('/api/v1/projects/'+english+'/studio') if language=='en' else base
            response=client.post(export_base+'/exports/latex',json={'language':language});response.raise_for_status();task=response.json()
            for _ in range(100):
                row=client.get('/api/v1/tasks/'+task['id']).json()
                if row['status'] not in ('queued','running'):break
                time.sleep(.1)
            if row['status']!='succeeded':raise ValueError(row.get('error'))
            result=json.loads(row['result']) if isinstance(row['result'],str) else row['result'];raw=client.get(result['download']).content
            with tempfile.TemporaryDirectory(prefix='yantai-tex-') as temporary:
                target=Path(temporary)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    for name in archive.namelist():
                        path=(target/name).resolve()
                        if not path.is_relative_to(target):raise ValueError('Invalid generated archive path')
                        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.read(name))
                executable='xelatex' if language=='zh' else 'pdflatex'
                installer='--enable-installer' if os.environ.get('PAPER_TEX_INSTALL')=='1' else '--disable-installer'
                run=subprocess.run([executable,installer,'-interaction=nonstopmode','-halt-on-error','main.tex'],cwd=target,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
                (folder/('compile-'+language+'.log')).write_text(run.stdout+'\n'+run.stderr,encoding='utf-8')
                pdf=target/'main.pdf';record={'language':language,'engine':executable,'returncode':run.returncode,'pdf_bytes':pdf.stat().st_size if pdf.exists() else 0,'warnings':result['warnings']}
                if pdf.exists():(folder/('synthetic-paper-'+language+'.pdf')).write_bytes(pdf.read_bytes())
                checks.append(record)
        backup_task=client.post('/api/v1/backups',json={'project_id':fixture['project_id']}).json()
        for _ in range(100):
            row=client.get('/api/v1/tasks/'+backup_task['id']).json()
            if row['status'] not in ('queued','running'):break
            time.sleep(.1)
        if row['status']!='succeeded':raise ValueError(row.get('error'))
        backup=json.loads(row['result']) if isinstance(row['result'],str) else row['result'];download=client.get('/api/v1/backups/'+backup['id']+'/download');download.raise_for_status()
        with tempfile.TemporaryDirectory(prefix='yantai-cli-') as temporary:
            snapshot=Path(temporary)/'backup.zip';snapshot.write_bytes(download.content);cli=[]
            for mode in ('live','offline'):
                for args in [['summary'],['list','knowledge'],['get','knowledge',fixture['reference_id']],['search','method']]:
                    command=['node',str(ROOT/'scripts/yantai-data.mjs'),*args,'--project',fixture['project_id']]
                    command+=['--server',origin] if mode=='live' else ['--snapshot',str(snapshot)]
                    run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=20)
                    if run.returncode:raise ValueError(run.stderr)
                    json.loads(run.stdout);cli.append({'mode':mode,'command':args[0],'passed':True})
    report={'latex':checks,'cli':cli,'synthetic_only':True};(folder/'delivery-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if any(record['returncode'] for record in checks) else 0

if __name__=='__main__':raise SystemExit(main())
