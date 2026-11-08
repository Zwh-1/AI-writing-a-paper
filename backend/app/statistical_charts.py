"""Reproducible charts over frozen registered data; no generated code."""
import io
import threading
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from . import db,security
from .visual_delivery import specification,quality
router=APIRouter()
_LOCK=threading.RLock()


def _prepare(file,body):
    import pandas as pd
    import numpy as np
    path=security.safe_object(file['sha256']);sheet=body.get('sheet_name') or 0
    if not isinstance(sheet,str) and type(sheet) is not int:raise HTTPException(422,'工作表名称或索引无效')
    if file['extension']=='.xlsx':
        book=pd.ExcelFile(path,engine='openpyxl')
        try:
            if type(sheet) is int:
                if not 0<=sheet<len(book.sheet_names):raise HTTPException(422,'工作表不存在')
                sheet=book.sheet_names[sheet]
            if sheet not in book.sheet_names:raise HTTPException(422,'工作表不存在')
            frame=pd.read_excel(book,sheet_name=sheet)
        finally:book.close()
    else:frame=pd.read_csv(path);sheet=None
    if len(frame)>500000:raise HTTPException(422,'数据量超过本地绘图上限')
    kind=body.get('kind','histogram');column=body.get('column');x=body.get('x_column') or None;group=body.get('group_column') or None
    lower=body.get('lower_column') or None;upper=body.get('upper_column') or None
    if kind not in ('histogram','line','scatter','bar'):raise HTTPException(422,'统计图类型无效')
    if kind=='histogram' and (lower or upper or x):raise HTTPException(422,'直方图只统计数值频数，不接受横轴列或观测误差区间')
    if kind in ('line','scatter') and not x:raise HTTPException(422,'折线和散点图须提供横轴数值列')
    if bool(lower)!=bool(upper):raise HTTPException(422,'误差区间须提供实际上下界两列')
    if any(name not in frame for name in [column,*[n for n in (x,group,lower,upper) if n]]):raise HTTPException(422,'图表列不存在')
    start=body.get('row_start',1);end=body.get('row_end',len(frame))
    if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(frame):raise HTTPException(422,'数据行范围须为从1开始的闭区间（不含表头）')
    selected=frame.iloc[start-1:end];invalid={}
    numeric={name:pd.to_numeric(selected[name],errors='coerce') for name in (column,*([x] if x and kind!='bar' else []),*([lower,upper] if lower else []))}
    mask=pd.Series(True,index=selected.index)
    for name,values in numeric.items():
        bad=values.isna() | ~np.isfinite(values);invalid[name]=bad;mask &= ~bad
    if x and kind=='bar':invalid[x]=selected[x].isna();mask &= ~invalid[x]
    if not mask.any():raise HTTPException(422,'没有有效可绘图数值')
    if lower and ((numeric[lower][mask]>numeric[column][mask]) | (numeric[upper][mask]<numeric[column][mask])).any():raise HTTPException(422,'误差上下界必须包含对应观测值')
    if group and selected.loc[mask,group].nunique(dropna=False)>20:raise HTTPException(422,'分组超过20组，请缩小数据范围')
    bins=body.get('bins',min(20,max(3,int(mask.sum())//2)))
    if type(bins) is not int or not 1<=bins<=200:raise HTTPException(422,'分箱数量为1至200')
    title=body.get('title') or str(column)+'图'
    if not isinstance(title,str) or len(title)>300:raise HTTPException(422,'图题无效')
    rows=[];excluded=[]
    for i in selected.index:
        if not mask.loc[i]:
            excluded.append({'data_row':int(i)+1,'invalid_columns':[n for n,bad in invalid.items() if bad.loc[i]]});continue
        rows.append({'data_row':int(i)+1,'y':float(numeric[column].loc[i]),
            'x':str(selected.loc[i,x]) if x and kind=='bar' else float(numeric[x].loc[i]) if x else int(i)+1,
            'group':None if not group or pd.isna(selected.loc[i,group]) else str(selected.loc[i,group]),
            'lower':float(numeric[lower].loc[i]) if lower else None,'upper':float(numeric[upper].loc[i]) if upper else None})
    if kind=='bar' and x:
        pairs=[(r['group'],r['x']) for r in rows]
        if len(set(pairs))!=len(pairs):raise HTTPException(422,'同一分组和类别有多条观测，请先明确聚合方法并提供聚合后的真实数据')
    return {'schema':'chart.recipe.v1','source_hash':file['sha256'],'source_file_id':file['id'],
        'sheet_name':sheet,'row_start':start,'row_end':end,'input_rows':len(frame),
        'column':column,'x_column':x,'group_column':group,'kind':kind,'title':title,
        'lower_column':lower,'upper_column':upper,'interval_description':body.get('interval_description') or '用户数据中的上下界，未推断置信水平',
        'bins':bins,'delivery_spec':specification(body.get('delivery_spec')),'rows':rows,'excluded_rows':excluded,
        'filter_counts':{name:int(bad.sum()) for name,bad in invalid.items()},
        'row_numbering':'1-based data rows excluding header','transform':'numeric coercion; exclude missing and nonfinite; no implicit aggregation'}


def calculate(recipe):
    import numpy as np
    groups=list(dict.fromkeys(r['group'] for r in recipe['rows']));result={}
    if recipe['kind']=='histogram':
        edges=np.histogram_bin_edges([r['y'] for r in recipe['rows']],bins=recipe['bins'])
        for g in groups:
            counts,_=np.histogram([r['y'] for r in recipe['rows'] if r['group']==g],bins=edges)
            result[db.dumps(g) if recipe['group_column'] else 'all']={'counts':counts.tolist(),'bin_edges':edges.tolist()}
    return result


def _render(recipe):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from .font_provenance import identity
    spec=recipe['delivery_spec'];kind=recipe['kind'];rows=recipe['rows'];group=recipe['group_column'];histograms=calculate(recipe)
    with _LOCK,plt.rc_context({'font.family':['Microsoft YaHei','DejaVu Sans'],'svg.fonttype':'none','axes.unicode_minus':False}):
        fig,ax=plt.subplots(figsize=(spec['width_mm']/25.4,spec['width_mm']/25.4*.62),dpi=spec['target_dpi'])
        try:
            groups=list(dict.fromkeys(r['group'] for r in rows))
            categories=list(dict.fromkeys(r['x'] for r in rows)) if kind=='bar' and recipe['x_column'] else []
            for gi,g in enumerate(groups):
                selected=[r for r in rows if r['group']==g];ys=np.array([r['y'] for r in selected]);xs=np.array([r['x'] for r in selected])
                label=('（缺失分组）' if g is None else g) if group else None
                if kind=='histogram':ax.hist(ys,bins=next(iter(histograms.values()))['bin_edges'],label=label,alpha=.7)
                elif kind=='scatter':ax.scatter(xs,ys,label=label)
                elif kind=='line':
                    order=np.argsort(xs,kind='stable');ax.plot(xs[order],ys[order],label=label)
                else:
                    if categories:xs=np.array([categories.index(r['x']) for r in selected],dtype=float)
                    width=.8/max(1,len(groups));xs=xs+(gi-(len(groups)-1)/2)*width
                    ax.bar(xs,ys,width=width,label=label,alpha=.75)
                if recipe['lower_column']:
                    ax.errorbar(xs,ys,yerr=[ys-np.array([r['lower'] for r in selected]),np.array([r['upper'] for r in selected])-ys],fmt='none',capsize=3)
            if categories:ax.set_xticks(np.arange(len(categories)),categories)
            ax.set_title(recipe['title']);ax.set_xlabel(spec['x_label'] or recipe['x_column'] or (recipe['column']+(('/'+spec['unit']) if spec['unit'] else '') if kind=='histogram' else '观测序号'))
            ax.set_ylabel(spec['y_label'] or ('频数' if kind=='histogram' else str(recipe['column'])+(('/'+spec['unit']) if spec['unit'] else '')))
            if group:ax.legend(title=spec['legend'] or group)
            for text in fig.findobj(match=matplotlib.text.Text):text.set_fontsize(max(spec['minimum_text_pt'],8))
            fig.tight_layout();fonts={font_manager.findfont(t.get_fontproperties()) for t in fig.findobj(match=matplotlib.text.Text) if t.get_text()};outputs={}
            for fmt in ('png','svg','pdf'):
                stream=io.BytesIO();fig.savefig(stream,format=fmt,dpi=spec['target_dpi']);outputs[fmt]=stream.getvalue()
            provenance={'files':[identity(f) for f in sorted(fonts)],'svg_fonts_embedded':False,'pdf_fonttype':matplotlib.rcParams['pdf.fonttype']}
        finally:plt.close(fig)
    return outputs,histograms,provenance,'matplotlib '+matplotlib.__version__


def _save(pid,recipe,egress,parent=None):
    outputs,counts,fonts,renderer=_render(recipe)
    from PIL import Image
    with Image.open(io.BytesIO(outputs['png'])) as image:width,height=image.size
    spec=recipe['delivery_spec'];recipe={**recipe,'calculated_histograms':counts}
    meta={'generator':'statistical-charts.v2','source_file_id':recipe['source_file_id'],'source_hash':recipe['source_hash'],
        'column':recipe['column'],'x_column':recipe['x_column'],'group_column':recipe['group_column'],'operation':recipe['kind'],
        'n':len(recipe['rows']),'input_rows':recipe['input_rows'],'missing_or_non_numeric_rows':len(recipe['excluded_rows']),
        'actual_execution':True,'egress':egress,'parent_artifact_id':parent,'recipe_sha256':security.put_blob(db.dumps(recipe).encode()),
        'data_processing':{'numeric_conversion':'coerce_and_exclude_nonfinite','row_filter':'all_selected_numeric_columns_valid',
            'column_exclusions':recipe['filter_counts'],'sheet_name':recipe['sheet_name'],'row_start':recipe['row_start'],'row_end':recipe['row_end'],
            'bins':recipe['bins'] if recipe['kind']=='histogram' else None,'histograms':counts,
            'error_bounds':{'lower':recipe['lower_column'],'upper':recipe['upper_column'],'interpretation':recipe['interval_description']} if recipe['lower_column'] else None},
        'delivery_spec':spec,'width':width,'height':height,'media_type':'image/png','figure_purpose':'original_result',
        'logical_width':spec['width_mm']*72/25.4,'minimum_logical_font':max(spec['minimum_text_pt'],8),
        'svg_sha256':security.put_blob(outputs['svg']),'pdf_sha256':security.put_blob(outputs['pdf']),
        'font_provenance':fonts,'renderer':renderer}
    meta['delivery_quality']=quality(meta);aid=db.uid();sha=security.put_blob(outputs['png'])
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,recipe['title'],'figure',sha,db.dumps(meta),db.now()))
    return {'id':aid,'sha256':sha,'metadata':meta}


def create(pid,body):
    security.require_project(pid)
    file=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(body.get('file_id'),pid))
    if not file or not file['active'] or file['extension'] not in ('.csv','.xlsx'):raise HTTPException(422,'选择本项目有效CSV/XLSX')
    return _save(pid,_prepare(file,body),file['egress'])


def _asset(pid,aid):
    security.require_project(pid);row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
    if not row:raise HTTPException(404,'图表不属于项目')
    meta=db.loads(row['metadata'],{})
    if meta.get('generator')!='statistical-charts.v2':raise HTTPException(422,'不是统计图')
    return row,meta


def _recipe(meta):
    sha=meta.get('recipe_sha256')
    if not sha:raise HTTPException(409,'旧图未保存数值序列，须重新从源数据生成')
    import hashlib
    raw=security.safe_object(sha).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise HTTPException(409,'冻结图表数据完整性校验失败')
    return db.loads(raw.decode())


@router.post('/projects/{pid}/charts/{aid}/reproduce')
def reproduce(pid,aid):
    _,meta=_asset(pid,aid);recipe=_recipe(meta)
    source=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(meta['source_file_id'],pid))
    if not source or not source['active'] or source['sha256']!=meta['source_hash']:raise HTTPException(409,'原数据已失效或变化，冻结记录保留供核对')
    return _save(pid,recipe,'local_only' if source['egress']=='local_only' or meta['egress']=='local_only' else meta['egress'],aid)


@router.get('/projects/{pid}/charts/{aid}/verification')
def verification(pid,aid):
    _,meta=_asset(pid,aid);recipe=_recipe(meta);computed=calculate(recipe)
    return {'recipe_hash':meta['recipe_sha256'],'histogram_counts_match':computed==recipe['calculated_histograms'] if recipe['kind']=='histogram' else None,
            'recalculated_histograms':computed,'used_rows':len(recipe['rows']),'excluded_rows':len(recipe['excluded_rows']),
            'semantic_verification':'not_assessed','rendering_verification':'requires_target_layout_review'}


@router.get('/projects/{pid}/charts/{aid}/download')
def download(pid,aid,format:str='png'):
    row,meta=_asset(pid,aid)
    if format not in ('png','svg','pdf','source'):raise HTTPException(422,'统计图格式无效')
    if format=='source':data=db.dumps({**meta,'recipe':_recipe(meta)} if meta.get('recipe_sha256') else meta).encode();media='application/json'
    else:
        sha=row['sha256'] if format=='png' else meta[format+'_sha256'];data=security.safe_object(sha).read_bytes()
        media={'png':'image/png','svg':'image/svg+xml','pdf':'application/pdf'}[format]
    return Response(data,media_type=media,headers={'Content-Disposition':'attachment; filename="chart.'+('json' if format=='source' else format)+'"'})
