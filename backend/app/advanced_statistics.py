"""Named statistical procedures. No generated Python is executed."""
import hashlib,math
from . import db,security

def analyze(pid,body):
    import numpy as np
    import pandas as pd
    from scipy import stats
    from statsmodels.stats.multitest import multipletests
    fid=body['file_id'];source=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
    if not source or source['extension'] not in ('.csv','.xlsx'):raise ValueError('请选择本项目的CSV或XLSX原始数据')
    path=security.safe_object(source['sha256']);df=pd.read_csv(path) if source['extension']=='.csv' else pd.read_excel(path)
    if len(df)>200000 or len(df.columns)>500:raise ValueError('数据超出当前统计上限')
    columns=body.get('columns',[]);method=body.get('method');missing=body.get('missing','reject')
    if not 1<=len(columns)<=20 or any(c not in df.columns for c in columns):raise ValueError('统计列不存在或数量不在1至20之间')
    if missing not in ('reject','complete_cases','groupwise'):raise ValueError('缺失值策略无效')
    numeric=df[columns].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan);dropped=int(numeric.isna().any(axis=1).sum())
    if dropped and missing=='reject':raise ValueError(f'发现{dropped}行缺失或非数值，请明确选择完整案例删除策略')
    if missing=='groupwise' and method not in ('welch_t','mann_whitney','anova'):raise ValueError('组内删除仅适用于独立组分析，配对分析须保持行对齐')
    arrays=[numeric[c].dropna().to_numpy(dtype=float) for c in columns] if missing=='groupwise' else [numeric.dropna()[c].to_numpy(dtype=float) for c in columns]
    numeric=numeric.dropna()
    if min(map(len,arrays))<3:raise ValueError('每个有效样本组须至少3行')
    alpha=float(body.get('alpha',.05))
    if not 0<alpha<1:raise ValueError('显著性水平须介于0和1之间')
    base={'method':method,'n':len(numeric),'n_by_group':{c:len(a) for c,a in zip(columns,arrays)},'original_rows':len(df),'dropped_rows':dropped,'source_sha256':source['sha256'],'columns':columns,'alpha':alpha,'missing_policy':missing,'warnings':['观察数据不能单独证明因果；显著性与实际效应大小需分别解释。']}
    if method=='welch_t':
        if len(arrays)!=2:raise ValueError('Welch检验需要两列')
        result=stats.ttest_ind(*arrays,equal_var=False);delta=float(arrays[0].mean()-arrays[1].mean());var=[a.var(ddof=1)/len(a) for a in arrays];se=math.sqrt(sum(var))
        if not se:raise ValueError('样本方差为零，不能检验')
        freedom=sum(var)**2/sum(v*v/(len(a)-1) for v,a in zip(var,arrays));half=stats.t.ppf(1-alpha/2,freedom)*se;base.update(statistic=float(result.statistic),p_value=float(result.pvalue),mean_difference=delta,confidence_interval=[float(delta-half),float(delta+half)],effect_size_d=delta/math.sqrt(sum((len(a)-1)*a.var(ddof=1) for a in arrays)/(sum(map(len,arrays))-2)))
    elif method=='paired_t':
        if len(arrays)!=2:raise ValueError('配对检验需要两列配对观测')
        result=stats.ttest_rel(*arrays);diff=arrays[0]-arrays[1];se=stats.sem(diff);half=stats.t.ppf(1-alpha/2,len(diff)-1)*se;base.update(statistic=float(result.statistic),p_value=float(result.pvalue),pairing='row_by_row',mean_difference=float(diff.mean()),confidence_interval=[float(diff.mean()-half),float(diff.mean()+half)])
    elif method in ('pearson','spearman'):
        if len(arrays)!=2:raise ValueError('相关分析需要两列')
        result=getattr(stats,method+'r')(*arrays);base.update(coefficient=float(result.statistic),p_value=float(result.pvalue))
    elif method=='mann_whitney':
        if len(arrays)!=2:raise ValueError('Mann–Whitney检验需要两列')
        result=stats.mannwhitneyu(*arrays,alternative='two-sided');base.update(statistic=float(result.statistic),p_value=float(result.pvalue))
    elif method=='anova':
        if len(arrays)<2:raise ValueError('方差分析需要至少两组')
        result=stats.f_oneway(*arrays);base.update(statistic=float(result.statistic),p_value=float(result.pvalue),assumptions='独立性、方差齐性和残差分布需另行诊断')
    elif method=='correlation_matrix':
        from itertools import combinations
        pairs=[];pvalues=[]
        for i,j in combinations(range(len(arrays)),2):
            r=stats.pearsonr(arrays[i],arrays[j]);pairs.append({'left':columns[i],'right':columns[j],'coefficient':float(r.statistic),'p_value':float(r.pvalue)});pvalues.append(r.pvalue)
        if not pairs:raise ValueError('相关矩阵至少需要两列')
        adjusted=multipletests(pvalues,alpha=alpha,method='holm')[1]
        for pair,p in zip(pairs,adjusted):pair['p_adjusted_holm']=float(p)
        base.update(pairs=pairs,multiple_comparison='Holm')
    elif method=='ols':
        import statsmodels.api as sm
        if len(arrays)<2:raise ValueError('回归需要因变量与至少一个自变量')
        result=sm.OLS(arrays[0],sm.add_constant(numeric[columns[1:]],has_constant='add')).fit(cov_type='HC3');base.update(coefficients={str(k):float(v) for k,v in result.params.items()},p_values={str(k):float(v) for k,v in result.pvalues.items()},r_squared=float(result.rsquared),confidence_intervals=result.conf_int(alpha=alpha).to_dict(),covariance='HC3')
    elif method=='bootstrap_mean':
        if len(arrays)!=1:raise ValueError('均值自助法需要一列')
        seed=int(body.get('seed',20261021));iterations=int(body.get('iterations',2000))
        if not 100<=iterations<=10000 or len(arrays[0])*iterations>20_000_000:raise ValueError('自助采样规模超过上限')
        rng=np.random.default_rng(seed);means=np.array([rng.choice(arrays[0],len(arrays[0]),replace=True).mean() for _ in range(iterations)]);base.update(mean=float(arrays[0].mean()),confidence_interval=np.quantile(means,[alpha/2,1-alpha/2]).tolist(),seed=seed,iterations=iterations)
    else:raise ValueError('请选择受支持的统计方案')
    def finite(v):
        if isinstance(v,float) and not math.isfinite(v):raise ValueError('统计结果非有限值，请检查样本方差、共线性和样本量')
        if isinstance(v,dict):
            for item in v.values():finite(item)
        elif isinstance(v,list):
            for item in v:finite(item)
    finite(base);base['human_interpretation']='pending';base['software']={'numpy':np.__version__,'pandas':pd.__version__,'scipy':__import__('scipy').__version__};return base
