"""Bounded whole-paper review: read leaf text, navigate indexes, verify original passages."""
from copy import deepcopy
from pydantic import Field
from .. import db,documents,security,provider
from ..source_payload import token_upper_bound
from .common import state,owned,digest,fail
from .models import Strict,Verification
from . import profiles,provider_adapter,prompts,sentences,verification,evidence
from .provenance import revision_permissions


# Severity buckets used by the whole-paper gate.  Only `blocker` findings must
# be resolved before a candidate can be written as a draft; `warning` and
# `info` remain author-confirmation or general advice and do not stop writing.
def bucket(severity):
    return {'blocker': 'hard_blocker', 'warning': 'needs_author', 'info': 'advice'}.get(str(severity or '').lower(), 'advice')


def classify(findings):
    """Bucket findings by their consequences for the whole-paper write gate."""
    buckets = {'hard_blocker': [], 'needs_author': [], 'advice': []}
    for finding in findings or []:
        key = bucket(finding.get('severity'))
        buckets[key].append(finding)
    return buckets

class RequestPassages(Strict):
    node_ids:list[str]=Field(min_length=1,max_length=12)
    question:str=Field(min_length=1,max_length=2000)
class Navigation(Verification):
    requests:list[RequestPassages]=Field(default_factory=list,max_length=16)

NAV=prompts.BOUNDARY+'''跨章导航：以下摘要仅供找问题，不能当作原文证据。选出需要跨章节对照的实际段落ID和问题，后续会读取原文。返回 {"requests":[{"node_ids":["给定段落ID"],"question":"需核对的关系"}],"findings":[],"coverage_complete":true,"summary":"检查的索引范围和限制"}。不要直接根据摘要宣布事实成立或矛盾已证明。'''

def review(task,body,candidate_sections=None,on_progress=None):
    """Review either saved sections or an explicit whole-paper candidate.

    ``candidate_sections`` is a list of ``{section_id, document, title, text}``
    items produced by ``paper_writing.run``.  When provided, the review reads
    those in-memory documents instead of the currently saved revisions, so a
    not-yet-adopted candidate can be checked before it is written.  All version
    and permission guards still run against the frozen inputs of the candidate.
    """
    from .paper_review import coverage
    pid=task['project_id'];head=state(pid);profile=profiles.get(pid,body['profile_id']);_,cfg,_=profiles.for_session(pid,profile['id']);scope=coverage(pid)
    p=security.require_project(pid);package=db.one('SELECT * FROM rule_packages WHERE id=?',(p['rule_package_id'],))
    if not package:fail('RULE_PACKAGE_REQUIRED','请先发布论文规则包')
    rules=[r for r in db.loads(package['rules'],[]) if r['category']!='format' and r['modality'] in ('mandatory','recommended')]
    for rule in rules:
        if rule.get('source_id'):
            original=db.one('SELECT file_id FROM sources WHERE id=?',(rule['source_id'],))
            if not original or not security.file_allows_remote(original['file_id'],pid):fail('SOURCE_ACCESS_REVOKED','全文规则来源禁止外发',403)
    # When a candidate is supplied, use its in-memory documents and frozen
    # manifests.  Otherwise fall back to the currently saved revisions.
    candidate = {row['section_id']: row for row in (candidate_sections or [])
                 if isinstance(row, dict) and row.get('section_id')}
    selected=body.get('section_ids') or (list(candidate) if candidate else [s['section_id'] for s in scope['sections']]);versions={};by_node={};chapter_evidence={};chapter_nodes={};all_evidence={};chapter_contracts={};gaps=[];findings=[];summaries=[];chunk_records=[]
    provenance={};tracked_evidence={};cross_records=[]
    for sid in selected:
        s=db.one('SELECT * FROM sections WHERE id=? AND project_id=?',(sid,pid))
        if not s:fail('NOT_FOUND','章节不属于本项目',404)
        from .contracts import require_remote_content
        contract_id=head['contracts'].get(sid);contract=db.loads(owned('contract',contract_id,pid)['content_json'],{}) if contract_id else {}
        require_remote_content(pid,contract);chapter_contracts[sid]={'version_id':contract_id,'content':contract}
        if sid in candidate:
            row=candidate[sid]
            document=row.get('document') or {}
            if not document:gaps.append({'section_id':sid,'reason':'no_text'});continue
            rid=row.get('revision_id') or ('candidate:'+digest(document)[:24])
            manifest=row.get('manifest') or {}
            # Only evidence_versions keys are real evidence-item ids.  fact_revisions
            # and source_ids reference facts and sources, which are not evidence
            # items, so they must not be passed to evidence.get.
            ids=set(manifest.get('evidence_versions',{}).keys()) if isinstance(manifest.get('evidence_versions',{}),dict) else set()
            for eid in row.get('evidence_ids') or []:ids.add(eid)
            from ..writing_review_context import restore
            originals,restored=restore(pid,manifest)
            chapter_evidence[sid]=originals
            tracked_evidence.update({e['id']:e for e in restored['project_evidence']})
            for field in ('knowledge_unit_versions','knowledge_read_scopes','document_reading_versions','fact_revisions','source_read_scopes'):
                target=provenance.setdefault(field,{})
                for identity,value in manifest.get(field,{}).items():
                    if identity in target and target[identity]!=value:fail('INPUT_VERSION_CHANGED','全文候选使用了不同版本的依据',409)
                    target[identity]=deepcopy(value)
            for field in ('source_ids','file_ids'):
                provenance[field]=list(dict.fromkeys([*provenance.get(field,[]),*manifest.get(field,[])]))
        else:
            r=documents.current(s)
            if not r:gaps.append({'section_id':sid,'reason':'no_text'});continue
            document=db.loads(r['document'],{});rid=r['id']
            revision_permissions(pid,r['id'])
            ids={d['upstream_id'] for d in db.rows("SELECT upstream_id FROM object_dependencies WHERE project_id=? AND downstream_type='revision' AND downstream_id=? AND upstream_type='evidence'",(pid,r['id']))}
        versions[sid]=rid;chapter_nodes[sid]=[]
        for node in sentences.paragraphs(document):
            text=sentences.paragraph_text(node)
            if not text.strip():continue
            nid=node['attrs']['nodeId'];passage={'node_id':nid,'section_id':sid,'section_title':s['title'],'revision_id':rid,'text':text};by_node[nid]=passage;chapter_nodes[sid].append(passage)
        if sid not in candidate:
            chapter_evidence[sid]=[evidence.get(pid,eid,True) for eid in ids if eid]
            tracked_evidence.update({e['id']:e for e in chapter_evidence[sid]})
        all_evidence.update({e['id']:e for e in chapter_evidence[sid]})
    core=db.loads(owned('core',head['core_version_id'],pid)['content_json'],{}) if head['core_version_id'] else {}
    from .contracts import require_remote_content
    require_remote_content(pid,core)
    base={'thesis_core':core,'rules':[{key:r[key] for key in ('id','statement','scope','operations','value') if key in r} for r in rules]}
    # Candidate sections are not yet saved revisions, so their pseudo ids must
    # not enter `related_revisions` — `check_manifest` compares those against
    # the live sections and would reject an un-adopted candidate.  The report
    # keeps the reviewed candidate ids separately; only real saved revisions
    # participate in the provenance manifest.
    review_versions = {sid: rid for sid, rid in versions.items()}
    manifest_related = {}
    for sid, rid in versions.items():
        if isinstance(rid, str) and rid.startswith('candidate:'):
            continue
        manifest_related[sid] = rid
    manifest={**provenance,'project_id':pid,'stage':'paper_review','writing_task_id':task.get('id'),'paper_hash':body.get('paper_hash'),'profile_id':profile['id'],'profile_fingerprint':profile['route_fingerprint'],'project_state_revision':head['revision'],'core_version_id':head['core_version_id'],'target_snapshot_id':head['target_snapshot_id'],'rule_package_id':p['rule_package_id'],'related_revisions':manifest_related,'contract_versions':{sid:c['version_id'] for sid,c in chapter_contracts.items()},'evidence_versions':{e['id']:e['origin_version'] for e in tracked_evidence.values()},'permission_stamps':{eid:evidence.permission_stamp(pid,eid) for eid in tracked_evidence},'remote':True}
    review_run_ids=[]
    def report(complete=False):
        normalized=[]
        for finding in findings:
            value=deepcopy(finding);nid=value.get('node_id');sids=value.get('section_ids') or []
            if nid in by_node:sids=[by_node[nid]['section_id']]
            value.update(id=digest([body.get('paper_hash'),value])[:24],section_ids=sids or list(versions),
                         code=value.get('kind','coherence'),method='model_assisted',
                         suggested_action=value.get('rationale','对照原文修订后重新审查'))
            normalized.append(value)
        return {'findings':normalized,'coverage_complete':bool(complete and by_node and not gaps),'run_ids':list(dict.fromkeys(review_run_ids)),
                'summary':'按连续原文和已绑定来源检查章节，再定位跨章原文核对；未覆盖范围单列。',
                'reviewed_sections':versions,'coverage_report':scope,'chunks':chunk_records,
                'cross_passage_checks':cross_records,'unresolved_scopes':deepcopy(gaps),
                'navigation_summaries_are_evidence':False,'paper_hash':body.get('paper_hash'),
                'coherence_coverage':{'complete':bool(complete and by_node and not gaps),'paragraphs_read':len({n for r in chunk_records for n in r['node_ids']}),'paragraphs_total':len(by_node)},
                'evidence_coverage':{'originals_provided':len(all_evidence),'versions_and_permissions_checked':True,
                    'full_review_certifies_all_claims':False,'claim_checks':{sid:{
                        'coverage_complete':bool((row.get('writing_quality') or {}).get('semantic',{}).get('coverage_complete')),
                        'status':(row.get('writing_quality') or {}).get('semantic',{}).get('status','not_available')}
                        for sid,row in candidate.items()},'method':'prior_claim_checks_and_restored_originals_not_human_approval'}}
    def checkpoint():
        if on_progress:on_progress(report())
    def model_evidence(items):
        from .context import share_exact_code_text
        return share_exact_code_text([evidence.for_model(e) if 'origin_type' in e else deepcopy(e) for e in items])
    def fits(system,payload):
        frame={'messages':[{'role':'user','content':db.dumps(payload)}],'tools':[]}
        return token_upper_bound(system,db.dumps(frame))+int(cfg['max_output_tokens'])<=int(cfg['context_limit'])
    def call(system,payload,stage,extra):
        result=provider_adapter.call(task,profile,system,payload,{**manifest,'stage':stage,**extra})
        parsed=provider.parse_json(result['text'])
        if result.get('run_id'):review_run_ids.append(result['run_id'])
        return parsed
    def chunk(sid,passages):
        return {**base,'scope':'连续原文单元；不能从摘要推断事实；按规则scope和operations核对对应范围','contract':chapter_contracts[sid],'passages':passages,'evidence':model_evidence(chapter_evidence[sid])}
    def review_chunk(sid,passages):
        scope_id=digest([versions[sid],[x['node_id'] for x in passages]]);parsed=Verification.model_validate(call(prompts.COHERENCE,chunk(sid,passages),'paper_chunk',{'scope_id':scope_id})).model_dump()
        node_ids={x['node_id'] for x in passages}
        for f in parsed['findings']:
            if f['node_id'] and f['node_id'] not in node_ids:fail('INVALID_FINDING','章内核验指向范围外段落')
        record={'scope_id':scope_id,'section_id':sid,'revision_id':versions[sid],'node_ids':list(node_ids),'coverage_complete':parsed['coverage_complete']};chunk_records.append(record);findings.extend(parsed['findings']);summaries.append({**record,'summary':parsed['summary']})
        verification.save_run(pid,None,'paper_chunk',{**manifest,**record},parsed)
        if not parsed['coverage_complete']:gaps.append({'scope_id':scope_id,'reason':'model_reported_incomplete'})
        checkpoint()
    for sid,passages in chapter_nodes.items():
        group=[]
        for passage in passages:
            if fits(prompts.COHERENCE,chunk(sid,[*group,passage])):group.append(passage);continue
            if group:
                review_chunk(sid,group);overlap=group[-1:];group=overlap if fits(prompts.COHERENCE,chunk(sid,[*overlap,passage])) else []
            if fits(prompts.COHERENCE,chunk(sid,[*group,passage])):group.append(passage)
            else:gaps.append({'node_id':passage['node_id'],'section_id':sid,'reason':'indivisible_passage_or_required_evidence_exceeds_budget'})
        if group:review_chunk(sid,group)
    groups=[];current=[]
    for summary in summaries:
        payload={**base,'navigation_indexes':[*current,summary]}
        if fits(NAV,payload):current.append(summary)
        else:
            if current:groups.append(current);current=[]
            if fits(NAV,{**base,'navigation_indexes':[summary]}):current=[summary]
            else:gaps.append({'scope_id':summary['scope_id'],'reason':'navigation_index_exceeds_budget'})
    if current:groups.append(current)
    if len(groups)>1:gaps.append({'reason':'cross_navigation_groups_not_jointly_reviewed','groups':len(groups),'action':'需要更具体的全文问题或人工跨组复核'})
    for indexes in groups:
        nav=Navigation.model_validate(call(NAV,{**base,'navigation_indexes':indexes},'paper_navigation',{})).model_dump()
        if nav['findings']:gaps.append({'reason':'navigation_findings_require_original_passages'})
        if not nav['coverage_complete']:gaps.append({'reason':'navigation_incomplete'})
        for request in nav['requests']:
            if set(request['node_ids'])-set(by_node):gaps.append({'reason':'navigation_unknown_node','request':request});continue
            passages=[by_node[nid] for nid in dict.fromkeys(request['node_ids'])];ids={e['id'] for passage in passages for e in chapter_evidence[passage['section_id']]}
            payload={**base,'question':request['question'],'passages':passages,'contracts':[{'section_id':sid,**chapter_contracts[sid]} for sid in dict.fromkeys(passage['section_id'] for passage in passages)],'evidence':model_evidence([all_evidence[eid] for eid in ids])}
            if not fits(prompts.COHERENCE,payload):gaps.append({'reason':'cross_original_passages_exceed_budget','request':request});continue
            result=Verification.model_validate(call(prompts.COHERENCE,payload,'paper_cross_passages',{})).model_dump();findings.extend(result['findings']);cross_records.append({'request':request,'result':result})
            if not result['coverage_complete']:gaps.append({'reason':'cross_passages_incomplete','request':request})
            checkpoint()
    covered={nid for chunk in chunk_records for nid in chunk['node_ids']}
    for nid in set(by_node)-covered:gaps.append({'node_id':nid,'reason':'not_read'})
    result=report(True)
    identity=verification.save_run(pid,None,'paper_review',manifest,result,'model_assisted','completed')
    return {'verification_run_id':identity,**result}
