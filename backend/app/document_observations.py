"""Rejoin storage slices without treating byte/character limits as semantic boundaries."""
from collections import defaultdict
from . import db

VERSION = 'complete-observations-v2'


def recognition_texts(rows, asset):
    """Recover complete saved engine output, including lines omitted from storage slices."""
    texts = {}
    for _, source in rows:
        loc = db.loads(source.get('locator'), {})
        for result in loc.get('recognition', []):
            if not isinstance(result, dict) or result.get('asset_sha256', asset) != asset:
                continue
            text = result.get('text')
            if isinstance(text, str) and text.strip():
                texts.setdefault(text, []).append(source['id'])
    return texts


def current_source_sql(alias='s'):
    if alias not in ('s','source'):raise ValueError('来源别名无效')
    # Build the superseded-ID set once per statement instead of walking the
    # whole JSON map for every source/vector candidate. NULL keys must not
    # suppress the complete corpus when the optional setting is JSON null.
    return f"{alias}.id NOT IN (SELECT supersession.key FROM settings observation_settings,json_each(observation_settings.value) supersession WHERE observation_settings.key='document_observation_supersessions' AND supersession.key IS NOT NULL)"


def observations(sources,superseded=None,preserve_unverified=False):
    groups=defaultdict(list);single=[]
    superseded=superseded or {};lookup={s['id']:s for s in sources};restored={}
    for source in sources:
        loc=db.loads(source.get('locator'),{})
        if source['id'] in superseded:
            target=source['id'];seen=set()
            while target in superseded and target not in seen:
                seen.add(target);target=superseded[target].get('replacement_source_id')
            replacement=lookup.get(target);meta=db.loads((replacement or {}).get('locator'),{})
            complete=bool(replacement and meta.get('transcription_scope')=='complete_image_text' and not meta.get('source_issues')
                and (meta.get('evidence_asset_sha256') or meta.get('asset_sha256'))==loc.get('asset_sha256')
                and replacement.get('file_id')==source.get('file_id') and replacement.get('page_id')==source.get('page_id'))
            # An inspected excerpt replaces only its own text, not the rest of
            # the image. Keep the original observation in a separate channel.
            # Missing coverage on historical corrections is unknown, never full.
            if not (preserve_unverified and not complete and source['method']=='windows_ocr'):continue
            restored[source['id']]=target
        start=loc.get('char_start');asset=loc.get('asset_sha256')
        if source['method']=='windows_ocr' and asset and type(start) is int and start>=0:
            groups[(asset,loc.get('ocr_version'),db.dumps(loc.get('origin',{})))].append((start,source))
        else:single.append({'text':source['text'],'source_ids':[source['id']],'origin':source['method'],'issues':[],
                            'locator':{'source_id':source['id'],'observation_locator':loc}})
    for (asset,version,origin),rows in groups.items():
        engine_texts=recognition_texts(rows,asset)
        if engine_texts:
            ids=list(dict.fromkeys(s['id'] for _,s in rows))
            covered=set()
            for text,proof_ids in engine_texts.items():
                # Offsets from a filtered storage view can differ from the engine
                # output. Recover its actual full text; retain incompatible slices.
                represented=[s['id'] for start,s in rows if text[start:start+len(s['text'])]==s['text'] or s['text'] in text]
                covered.update(represented)
                single.append({'text':text,'source_ids':list(dict.fromkeys([*proof_ids,*represented])), 'origin':'windows_ocr',
                    'issues':['conflicting_recognition_results'] if len(engine_texts)>1 else [],
                    'locator':{'source_id':proof_ids[0],'source_ids':ids,'asset_sha256':asset,'ocr_version':version,
                        'observation_origin':db.loads(origin,{}),'read_scope':'complete_saved_engine_observation',
                        'recovered_from_recognition':True,'boundary_basis':'image_observation_not_semantic_closure'}})
            rows=[(start,s) for start,s in rows if s['id'] not in covered]
            if not rows:continue
        complete=[s for start,s in rows if start==0 and db.loads(s['locator'],{}).get('storage_scope')=='complete_image_observation']
        if complete and len({s['text'] for s in complete})==1 and all(complete[0]['text'][start:start+len(s['text'])]==s['text'] for start,s in rows):
            first=complete[0];ids=[first['id']]+[s['id'] for _,s in rows if s['id']!=first['id']]
            single.append({'text':first['text'],'source_ids':ids,'origin':'windows_ocr','issues':[],
                           'locator':{'source_id':first['id'],'source_ids':ids,'asset_sha256':asset,'ocr_version':version,
                                      'read_scope':'complete_saved_image_observation','boundary_basis':'image_observation_not_semantic_closure'}})
            continue
        by_start=defaultdict(list)
        for start,source in rows:by_start[start].append(source)
        if any(len({s['text'] for s in values})!=1 for values in by_start.values()):
            for _,source in rows:
                single.append({'text':source['text'],'source_ids':[source['id']],'origin':'windows_ocr','issues':['conflicting_observation_slices'],
                               'locator':{'source_id':source['id'],'asset_sha256':asset,'read_scope':'conflicting_saved_observation'}})
            continue
        runs=[];current=[];end=None;overlap_conflict=False
        for start,values in sorted(by_start.items()):
            if current and start<end:
                assembled=current[0][1][0]['text']
                for at,parts in current[1:]:assembled+=parts[0]['text'][max(0,current[0][0]+len(assembled)-at):]
                overlap=min(end-start,len(values[0]['text']))
                if assembled[start-current[0][0]:start-current[0][0]+overlap]!=values[0]['text'][:overlap]:
                    runs.append(current);current=[];end=None;overlap_conflict=True
            if current and start>end:runs.append(current);current=[]
            if not current:end=None
            current.append((start,values));end=max(end or 0,start+len(values[0]['text']))
        if current:runs.append(current)
        for run in runs:
            start=run[0][0];text=run[0][1][0]['text']
            for at,values in run[1:]:text+=values[0]['text'][max(0,start+len(text)-at):]
            ids=[s['id'] for _,values in run for s in values]
            single.append({'text':text,'source_ids':ids,'origin':'windows_ocr',
                           'issues':(['observation_has_missing_slices'] if len(runs)>1 or start!=0 else [])+(['conflicting_observation_slices'] if overlap_conflict else []),
                           'locator':{'source_id':ids[0],'source_ids':ids,'asset_sha256':asset,'ocr_version':version,
                                      'observation_origin':db.loads(origin,{}),'observation_char_start':start,'observation_char_end':start+len(text),
                                      'read_scope':'contiguous_saved_observation','boundary_basis':'image_observation_not_semantic_closure'}})
    for observation in single:
        targets={restored[i] for i in observation['source_ids'] if i in restored}
        nested=observation['locator'].get('observation_locator',{})
        asset=observation['locator'].get('asset_sha256') or nested.get('asset_sha256') or nested.get('evidence_asset_sha256')
        if targets:
            observation['origin']='unverified_image_transcript'
            observation['issues']=list(dict.fromkeys([*observation['issues'],'ocr_requires_original_comparison','retained_beyond_verified_excerpt']))
            observation['locator'].update(evidence_role='unverified_full_observation',read_scope='full_saved_ocr_observation',index_use='location_only',
                cannot_support_claims=True,related_verified_source_ids=sorted(t for t in targets if t))
        if asset:
            observation['locator']['context_stream']='image:'+str(asset)+':'+('unverified' if targets else observation['origin'])
            if observation['origin']=='assistant_visual_excerpt':
                observation['locator']['context_stream']+=':passage:'+observation['source_ids'][0]
    return single
