"""LaTeX projects and conservative, reference-aware workspace maintenance."""
import hashlib
import io
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from fastapi import HTTPException
from . import db, security, documents
from . import research_records as store

def tex(value):
    return ''.join({'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}.get(char,char) for char in str(value or ''))

def math_tex(value):
    value=str(value or '')
    if len(value)>12000 or re.search(r'\\(?:input|include|write|read|openin|openout|catcode|csname|special|usepackage|documentclass|immediate|loop|def|let|newcommand)\b',value,re.I):raise ValueError('公式包含不支持的 TeX 指令，请在公式编辑器中核对')
    return value

def latex_project(pid,body,prepared=None):
    from .config import DATA
    from .export_checks import freeze,markdown_document
    from .exports import bibliography_text
    from .export_service import prepare
    prepared=prepared or prepare(pid,body)
    frozen=prepared['snapshot'];project=frozen['project'];brief=db.loads(project['brief'],{});language=body.get('language','zh')
    if language not in ('zh','en'):raise HTTPException(422,'请选择中文或英文模板')
    image_files={};warnings=[];citations=[];sections=[];manifest_sections=[];active_section=None
    artifacts=frozen['artifacts']
    figures={}
    for item in frozen['sections']:
        revision=frozen['revisions'].get(item['id'])
        if revision:
            for node in documents.nodes(db.loads(revision['document'],{})):
                if node['type'] in ('figure','table') and node.get('attrs',{}).get('nodeId'):figures[(item['id'],node['attrs']['nodeId'])]='visual:'+item['id']+':'+node['attrs']['nodeId']
    def convert(node):
        kind=node.get('type');attrs=node.get('attrs',{});children=node.get('content',[])
        if kind=='text':
            value=tex(node.get('text',''))
            for mark in node.get('marks',[]):
                command={'bold':'textbf','italic':'emph','code':'texttt','underline':'underline','strike':'sout'}.get(mark.get('type'))
                if command:value='\\'+command+'{'+value+'}'
            return value
        if kind=='hardBreak':return '\\\\ '
        if kind=='citation':
            identity=attrs.get('citationId')
            if identity not in frozen['references']:raise ValueError('正文存在缺失书目引用')
            if identity not in citations:citations.append(identity)
            return '\\cite{ref'+identity+'}'
        if kind in ('formula','inlineFormula'):
            expression=attrs.get('latex') or attrs.get('expression')
            if not expression:
                warnings.append('存在只有 Office OMML 的公式，需人工转为 LaTeX');return tex('[公式需要转换]')
            value=math_tex(expression)
            return '$'+value+'$' if kind=='inlineFormula' else '\n\\[\n'+value+'\n\\]\n'
        if kind=='footnote':return '\\footnote{'+tex(attrs.get('noteText',''))+'}'
        if kind=='crossReference':
            identity=(attrs.get('targetSectionId') or active_section,attrs.get('targetId'))
            if identity not in figures:
                warnings.append('图表交叉引用失效：'+str(attrs.get('targetId','')));return tex('[图表引用待核对]')
            return '\\ref{'+figures[identity]+'}'
        if kind=='horizontalRule':return '\n\\noindent\\rule{\\linewidth}{0.4pt}\n'
        if kind=='figure':
            artifact=artifacts.get(attrs.get('assetVersionId') or attrs.get('artifactId'))
            if not artifact or not artifact['sha256']:raise ValueError('正文图形缺少已归档资源')
            from PIL import Image
            name='images/'+(attrs.get('assetVersionId') or artifact['id'])+'.png'
            if name not in image_files:
                with Image.open(security.safe_object(artifact['sha256'])) as image:
                    buffer=io.BytesIO();image.convert('RGB').save(buffer,format='PNG');image_files[name]=buffer.getvalue()
            label=figures.get((active_section,attrs.get('nodeId')),'visual:'+artifact['id'])
            return '\n\\begin{figure}[htbp]\n\\centering\n\\includegraphics[width=0.9\\linewidth]{'+name+'}\n\\caption{'+tex(attrs.get('caption') or artifact['name'])+'}\n\\label{'+label+'}\n\\end{figure}\n'
        if kind=='table':
            rows=[row.get('content',[]) for row in children];width=max((sum(int(cell.get('attrs',{}).get('colspan',1)) for cell in row) for row in rows),default=1)
            lines=[]
            for row in rows:
                values=[]
                for cell in row:
                    value=' '.join(convert(child).strip() for child in cell.get('content',[]));span=int(cell.get('attrs',{}).get('colspan',1))
                    if int(cell.get('attrs',{}).get('rowspan',1))>1:warnings.append('跨行表格已展平，请核对 LaTeX 表格')
                    values.append('\\multicolumn{'+str(span)+'}{l}{'+value+'}' if span>1 else value)
                lines.append(' & '.join(values)+' \\\\')
            label=figures.get((active_section,attrs.get('nodeId')))
            caption='\\caption{'+tex(attrs.get('caption') or 'Table')+'}'+('\\label{'+label+'}' if label else '')+'\\\\\n' if attrs.get('caption') or label else ''
            return '\n\\begin{longtable}{'+'l'*width+'}\n'+caption+'\\toprule\n'+'\n'.join(lines)+'\n\\bottomrule\n\\end{longtable}\n'
        content=''.join(convert(child) for child in children)
        if kind=='heading':
            command={1:'section',2:'subsection',3:'subsubsection'}.get(int(attrs.get('level',2)),'paragraph')
            return '\n\\'+command+'{'+content+'}\n'
        if kind in ('bulletList','orderedList'):
            environment='itemize' if kind=='bulletList' else 'enumerate'
            return '\n\\begin{'+environment+'}\n'+content+'\n\\end{'+environment+'}\n'
        if kind=='listItem':return '\\item '+content.strip()+'\n'
        if kind=='blockquote':return '\n\\begin{quote}\n'+content+'\n\\end{quote}\n'
        return content+'\n\n' if kind in ('paragraph','doc') else content
    markdown=[]
    for section in frozen['sections']:
        revision=frozen['revisions'].get(section['id'])
        if not revision:continue
        active_section=section['id'];document=db.loads(revision['document'],{});content=convert(document)
        if section['kind'] in ('abstract','abstract_en'):sections.append('\n\\begin{abstract}\n'+content+'\n\\end{abstract}\n')
        else:sections.append('\n\\section{'+tex(section['title'])+'}\n'+content)
        markdown.extend(['# '+section['title'],'',markdown_document(document)])
        manifest_sections.append({'section_id':section['id'],'revision_id':revision['id']})
    bibliography=[];bib=[]
    for rid in citations:
        row=frozen['references'][rid];metadata=db.loads(row['metadata'],{})
        if body.get('reference_metadata')=='adopted':
            snapshot=next((item for item in frozen['citations'] if item['reference_id']==rid),None)
            if snapshot:metadata=db.loads(snapshot['reference_snapshot'],metadata)
        from .bibliography_format import render as render_reference
        bibliography.append('\\bibitem{ref'+rid+'} '+tex(render_reference(metadata,frozen['settings']['academic_standards:'+pid]['bibliography_version'])['text']))
        fields={key:metadata.get(key,'') for key in ('title','year','journal','doi','volume','pages','publisher','url','edition')};authors=metadata.get('authors',[])
        if metadata.get('issue'):fields['number']=metadata['issue']
        if metadata.get('place'):fields['address']=metadata['place']
        if metadata.get('container_title'):fields['booktitle']=metadata['container_title']
        fields['author']=' and '.join(authors) if isinstance(authors,list) else authors
        bib_type={'J':'article','M':'book','D':'phdthesis','C':'inproceedings','S':'misc','EB':'misc'}.get(str(metadata.get('type','J')).split('/')[0],'misc')
        bib.append('@'+bib_type+'{ref'+rid+',\n'+',\n'.join('  '+key+' = {'+tex(value)+'}' for key,value in fields.items() if value)+'\n}')
    preamble=['\\documentclass[12pt,a4paper]{'+('ctexart' if language=='zh' else 'article')+'}',
              '\\usepackage{amsmath,amssymb,graphicx,longtable,hyperref,geometry}',
              '\\IfFileExists{booktabs.sty}{\\usepackage{booktabs}}{\\newcommand{\\toprule}{\\hline}\\newcommand{\\bottomrule}{\\hline}\\newcommand{\\midrule}{\\hline}}',
              '\\IfFileExists{ulem.sty}{\\usepackage[normalem]{ulem}}{\\newcommand{\\sout}[1]{\\textit{[deleted: #1]}}}',
              '\\geometry{margin=25mm}','\\title{'+tex(project['title'])+'}','\\author{'+tex(brief.get('author',''))+'}','\\date{}','\\begin{document}','\\maketitle']
    source='\n'.join(preamble+sections+(['\\begin{thebibliography}{99}',*bibliography,'\\end{thebibliography}'] if bibliography else [])+['\\end{document}',''])
    identity=db.uid();folder=DATA/'exports'/identity;folder.mkdir(parents=True,exist_ok=True)
    guide='# LaTeX 工程\n\n'+('中文模板：使用 XeLaTeX 和已安装的 CTeX 字体。\n\n```\nxelatex -interaction=nonstopmode main.tex\nxelatex -interaction=nonstopmode main.tex\n```' if language=='zh' else '英文模板：使用 pdfLaTeX 或 XeLaTeX。\n\n```\npdflatex -interaction=nonstopmode main.tex\npdflatex -interaction=nonstopmode main.tex\n```')+'\n\n参考文献已嵌入 main.tex；references.bib 供后续改用 BibTeX 使用。目录中的图片使用相对路径。请检查 warnings.json 和原件；生成工程不代表已编译或人工验收。\n'
    files={'main.tex':source.encode(),'references.bib':'\n\n'.join(bib).encode(),'document.md':'\n'.join(markdown).encode(),'HOW_TO_COMPILE.md':guide.encode(),'warnings.json':db.dumps(warnings).encode(),**image_files}
    files['research-trace.json']=db.dumps(frozen.get('research_trace',{})).encode()
    files['academic-standards.json']=db.dumps(prepared['checks']['bibliography']).encode()
    files['knowledge-trace.json']=db.dumps(frozen.get('graph_wiki',{})).encode()
    manifest={'export_format':'latex','snapshot_id':prepared['snapshot_id'],'mode':prepared['mode'],'checks':prepared['checks'],'project_id':pid,'language':language,'sections':manifest_sections,'created_at':db.now(),'warnings':warnings,'compiled':False,'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
    files['manifest.json']=db.dumps(manifest).encode();archive=folder/'latex-project.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as handle:
        for name,raw in files.items():handle.writestr(name,raw)
    db.execute('INSERT INTO exports VALUES(?,?,?,?,?,?)',(identity,pid,db.dumps(manifest),archive.name,'ready',db.now()))
    return {'export_id':identity,'download':f'/api/v1/exports/{identity}/files/latex-project.zip','warnings':warnings,'compiled':False}

def deletion_preview(pid,rids):
    from .workspace_maintenance import deletion_preview as execute
    return execute(pid,rids)

def delete_references(pid,body):
    from .workspace_maintenance import delete_references as execute
    return execute(pid,body)

def cache_roots():
    from .workspace_maintenance import cache_roots as execute
    return execute()

def cache_status():
    from .workspace_maintenance import cache_status as execute
    return execute()

def clear_cache(body):
    from .workspace_maintenance import clear_cache as execute
    return execute(body)

def orphan_objects(body=None):
    from .workspace_maintenance import orphan_objects as execute
    return execute(body)
