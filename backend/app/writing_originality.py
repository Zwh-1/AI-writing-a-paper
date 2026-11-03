"""Bounded project-local overlap review; never a commercial plagiarism/AI score.

Lexical overlap is deterministic. Local embeddings only recall semantic
candidates; they cannot prove redundancy or information gain. No model is
downloaded and no remote endpoint is contacted by this module.
"""
from difflib import SequenceMatcher
from itertools import chain,islice
import math
import re

from .writing_agent.common import digest

VERSION='writing_originality.v1'
_DEFAULTS={'semantic_enabled':True,'min_characters':35,'literal_threshold':0.68,
           'semantic_threshold':0.88,'max_targets':800,'max_pairs':40000,'max_matches':200}


def _text(row):
    if isinstance(row,str):return row
    return str(row.get('text') or row.get('exact_text') or row.get('statement') or '')


def _normalize(text):
    values=[];positions=[]
    for offset,char in enumerate(text):
        if char.isalnum():values.append(char.lower());positions.append(offset)
    return ''.join(values),positions


def _grams(text,size=5):
    return {text[index:index+size] for index in range(max(1,len(text)-size+1))}


def _segments(row,scope):
    text=_text(row)
    identity=(row.get('source_id') or row.get('id') or row.get('section_id') or row.get('unit_id')) if isinstance(row,dict) else None
    # Preserve paragraph boundaries and exact positions in the original input.
    for match in re.finditer(r'[^\r\n]+',text):
        value=match.group();offset=match.start()
        # A very large original is a search corpus, not one indivisible pair.
        for start in range(0,len(value),900):
            piece=value[start:start+1200]
            if piece.strip():yield {'text':piece,'offset':offset+start,'scope':scope,'id':identity,
                                    'title':row.get('title') or row.get('name') or '' if isinstance(row,dict) else '',
                                    'locator':row.get('locator',{}) if isinstance(row,dict) else {},
                                    'version':row.get('revision_id') or row.get('version') or row.get('unit_hash') if isinstance(row,dict) else None}
            if start+1200>=len(value):break


def _exemption(paragraph,match,section_kind,terms):
    # Only a matched quoted span is exempt, never every assertion in a cited paragraph.
    for intent in paragraph.get('citation_intents',[]) if isinstance(paragraph,dict) else []:
        quote=intent.get('quote','')
        if intent.get('kind')=='quote' and intent.get('reference_id') and quote:
            normalized,_=_normalize(quote)
            overlap,_=_normalize(match['matched_text'])
            if overlap and overlap in normalized:
                return {'kind':'direct_quote','status':'declared_requires_citation_check','excluded_from_redundancy':True}
    compact,_=_normalize(match['matched_text'])
    if compact and compact in terms:
        return {'kind':'necessary_terminology','status':'registered_term','excluded_from_redundancy':True}
    if match['scope']=='cross_section' and section_kind in {'abstract','abstract_en','conclusion'}:
        return {'kind':'necessary_recap','status':'requires_information_gain_review','excluded_from_redundancy':False}
    return None


def _cosine(a,b):
    if len(a)!=len(b) or not all(math.isfinite(float(value)) for value in [*a,*b]):
        raise ValueError('本地语义模型返回不同维度或非有限向量')
    dot=sum(float(x)*float(y) for x,y in zip(a,b))
    aa=sum(float(x)**2 for x in a);bb=sum(float(x)**2 for x in b)
    return dot/math.sqrt(aa*bb) if aa and bb else 0.0


def check(paragraphs,related=(),sources=(),section_kind='body',pid=None):
    """Return located overlap candidates and explicit local-model coverage.

    Caller-provided sources must be the actual authorized/read source scope.
    Semantic candidates are reported for independent assessment, not labelled as
    confirmed repetition. Necessary recaps remain visible for human/quality review.
    """
    from . import db
    config={**_DEFAULTS,**(db.setting('writing_originality:'+pid,{}) if pid else {})}
    records=[];skipped=[]
    for index,row in enumerate(paragraphs):
        value=_text(row);normalized,positions=_normalize(value)
        if len(normalized)<int(config['min_characters']):skipped.append(index);continue
        records.append({'text':value,'normalized':normalized,'positions':positions,'paragraph_index':index,'row':row})
    target_limit=min(5000,max(1,int(config['max_targets'])))
    target_stream=chain(_segments_collection(related,'cross_section'),_segments_collection(sources,'source'))
    targets=list(islice(target_stream,target_limit+1))
    omitted=max(0,len(targets)-target_limit);targets=targets[:target_limit]
    for row in targets:
        row['normalized'],row['positions']=_normalize(row['text'])
    targets=[row for row in targets if len(row['normalized'])>=int(config['min_characters'])]
    terms=set()
    if pid:
        for row in db.rows('SELECT zh,en FROM terms WHERE project_id=?',(pid,)):
            terms.update(value for value in (_normalize(row['zh'])[0],_normalize(row['en'])[0]) if value)
    pairs=[];pair_limit=min(200000,max(1,int(config['max_pairs'])));truncated_pairs=False
    for index,row in enumerate(records):
        for prior in records[:index]:
            pairs.append((row,{**prior,'scope':'within_section','id':prior['paragraph_index'],'offset':0,'version':None,'title':'本章前文','locator':{}}))
        for target in targets:pairs.append((row,target))
        if len(pairs)>pair_limit:
            pairs=pairs[:pair_limit];truncated_pairs=True;break
    matches=[];matched_keys=set()
    for row,other in pairs:
        a,b=row['normalized'],other['normalized']
        if not a or not b:continue
        aa,bb=_grams(a),_grams(b)
        containment=len(aa&bb)/max(1,min(len(aa),len(bb)))
        exact=a in b or b in a
        # Fast n-gram screening avoids O(n*m) alignments on unrelated passages.
        if not exact and containment<float(config['literal_threshold']):continue
        similarity=SequenceMatcher(None,a,b,autojunk=False)
        ratio=similarity.ratio()
        if not exact and ratio<0.72:continue
        block=similarity.find_longest_match(0,len(a),0,len(b))
        if block.size<12:continue
        start=row['positions'][block.a];end=row['positions'][block.a+block.size-1]+1
        target_start=other['positions'][block.b];target_end=other['positions'][block.b+block.size-1]+1
        matched={'paragraph_index':row['paragraph_index'],'scope':other['scope'],'target_id':other['id'],
                 'target_title':other.get('title',''),'target_version':other.get('version'),'locator':other.get('locator',{}),
                 'start_cp':start,'end_cp':end,'target_start_cp':other.get('offset',0)+target_start,
                 'target_end_cp':other.get('offset',0)+target_end,'matched_text':row['text'][start:end],
                 'target_text':other['text'][target_start:target_end],'method':'exact' if exact else 'character_ngram_alignment',
                 'similarity':round(ratio,4),'ngram_containment':round(containment,4),
                 'redundancy':'unassessed','semantic_support':'not_checked'}
        matched['exemption']=_exemption(row['row'] if isinstance(row['row'],dict) else {},matched,section_kind,terms)
        matches.append(matched);matched_keys.add((row['paragraph_index'],other['scope'],str(other['id'])))
    semantic={'state':'not_run','model':None,'fingerprint':None,'reason':'semantic_disabled'}
    if config['semantic_enabled'] and pairs:
        from . import retrieval,model_install
        model=retrieval.config(pid)['model'] if pid else retrieval.DEFAULT_MODEL
        status=model_install.status(model)
        semantic.update(model=model,fingerprint=status.get('fingerprint'))
        if not status.get('ready'):
            semantic.update(state='unavailable',reason='local_model_not_installed_no_download')
        else:
            try:
                texts=list(dict.fromkeys([value['text'] for pair in pairs for value in pair]))
                vectors=retrieval.embed(texts,model)
                if len(vectors)!=len(texts):raise ValueError('本地语义模型未返回全部向量')
                by_text=dict(zip(texts,vectors));semantic.update(state='completed',reason=None)
                for row,other in pairs:
                    if (row['paragraph_index'],other['scope'],str(other['id'])) in matched_keys:continue
                    score=_cosine(by_text[row['text']],by_text[other['text']])
                    if score<float(config['semantic_threshold']):continue
                    matched={'paragraph_index':row['paragraph_index'],'scope':other['scope'],'target_id':other['id'],
                             'target_title':other.get('title',''),'target_version':other.get('version'),'locator':other.get('locator',{}),
                             'start_cp':0,'end_cp':len(row['text']),'target_start_cp':other.get('offset',0),
                             'target_end_cp':other.get('offset',0)+len(other['text']),
                             'matched_text':row['text'],'target_text':other['text'],'method':'local_embedding_candidate',
                             'similarity':round(score,4),'redundancy':'requires_information_gain_review','semantic_support':'not_checked'}
                    matched['exemption']=_exemption(row['row'] if isinstance(row['row'],dict) else {},matched,section_kind,terms)
                    matches.append(matched)
            except (ValueError,RuntimeError,ImportError,OSError) as error:
                semantic.update(state='unavailable',reason='local_embedding_failed:'+str(error)[:200])
    matches.sort(key=lambda row:(row['method']=='local_embedding_candidate',-row['similarity'],row['paragraph_index']))
    match_limit=min(1000,max(1,int(config['max_matches'])));omitted_matches=max(0,len(matches)-match_limit);matches=matches[:match_limit]
    findings=[]
    for match in matches:
        exemption=match['exemption']
        if exemption and exemption['excluded_from_redundancy']:continue
        semantic_only=match['method']=='local_embedding_candidate'
        recap=bool(exemption and exemption['kind']=='necessary_recap')
        kind='semantic_overlap_candidate' if semantic_only else 'source_expression_overlap' if match['scope']=='source' else 'repeated_passage'
        message=('语义相近，需确认本段是否新增主张、证据、解释或适用条件' if semantic_only else
                 '表述与已读来源存在较大重合，需核对直接引用、转述及综合方式' if match['scope']=='source' else
                 '正文存在相同或近似片段，需按章节职责判断是否冗述')
        if recap:message+='；摘要/结论的必要回顾需保留，不自动删除'
        findings.append({'id':digest([VERSION,match])[:24],'kind':kind,'code':kind,'severity':'info' if semantic_only or recap else 'warning',
                         'message':message,'paragraph_index':match['paragraph_index'],'start_cp':match['start_cp'],'end_cp':match['end_cp'],
                         'scope':match['scope'],'target_id':match['target_id'],'target_version':match['target_version'],
                         'excerpt':match['matched_text'][:300],'repair_query':match['matched_text'][:1000],
                         'method':match['method'],'requires_semantic_review':True,'exemption':exemption})
    coverage={'scope':'provided_project_texts_only','candidate_paragraphs':len(paragraphs),'checked_paragraphs':len(records),
              'short_paragraphs_skipped':skipped,'comparison_passages':len(targets),'checked_pairs':len(pairs),
              'omitted_targets':omitted,'pair_limit_reached':truncated_pairs,'omitted_matches':omitted_matches,
              'omitted_targets_is_lower_bound':bool(omitted),
              'literal_complete':not(omitted or truncated_pairs or omitted_matches),'semantic':semantic,
              'confirmed_redundancy':'not_measured','external_plagiarism_rate':'not_measured','aigc_rate':'not_measured'}
    report={'version':VERSION,'findings':findings,'matches':matches,'method':'local_ngram_alignment_and_optional_embeddings',
            'coverage':coverage,'thresholds':{key:config[key] for key in ('literal_threshold','semantic_threshold','min_characters')}}
    return {**report,'hash':digest(report)}


def _segments_collection(rows,scope):
    for row in rows:
        yield from _segments(row,scope)
