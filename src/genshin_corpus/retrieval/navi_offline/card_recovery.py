"""Explicit cross-version reuse and source-completion dependency verification."""
from pathlib import Path
from . import anchors, cards, cards_v2 as v2, card_generation as g, card_structure
from .common import read,write,copy,verify,seal,sha,require,digest

def import_job(root,prior,sources):
    root=Path(root);prior=Path(prior);verify(prior)
    plan=read(prior/'plan.json');result=read(prior/'result.json')
    require(result['state']=='CONTENT_VERIFIED' and plan.get('strategy') in g.EVIDENCE_STRATEGIES,
            'Only verified exact-anchor Card jobs can be reused')
    for q in plan['evidence']:sources.verify_quote(q)
    from . import requests
    assessment,value=g.assess(requests.transport.decoded_body((prior/'response.body.raw').read_bytes(),
        read(prior/'http.json')['headers'].get('content-encoding','')),read(prior/'sdk-response.json'),
        plan['view'],plan['evidence'],plan['strategy'])
    require(read(prior/'http.json')['status']==200 and assessment['protocol_validation']['status']==
        assessment['content_validation']['status']=='PASS' and value==read(prior/'value.json'),'Reuse replay failed')
    target=root/'jobs'/prior.name
    if target.exists():
        verify(target);require(sha(target/'complete.json')==sha(prior/'complete.json'),'Imported job changed')
    else:
        for p in prior.rglob('*'):
            if p.is_file():copy(p,target/p.relative_to(prior))
    origin=root/'reuse-origins/jobs'/prior.name
    if not origin.exists():
        write(origin/'origin.json',{'original':str(prior.resolve()),'complete_sha256':sha(prior/'complete.json'),
            'strategy':plan['strategy'],'reuse':'EXACT_SEMANTIC_INPUT; ORIGINAL_WIRE_AND_RESULT_PRESERVED','new_calls':0})
        seal(origin)
    return target

def validate_dependencies(root,folder,source,sources):
    """Require reachable successful direct reads, not just a claimed coverage union."""
    root=Path(root);folder=Path(folder);value=read(folder/'cards.json');plan=read(folder/'reading-plan.json')
    require(value['status']=='COMPLETE' and not value['failures'] and not value.get('missing_required_reading'),
            'Unresolved source work')
    config=read(Path(root)/'prepared/config.json')
    expected=card_structure.plan(sources,source,semantic_selection=config.get('generation_strategy')==g.SEMANTIC)
    require(digest(plan['selected'])==digest(expected['selected']) and plan['units']==expected['units'],
            'Saved reading plan differs from source policy')
    docs=[c for c in value['cards'] if c['level']=='document'];require(len(docs)==1,'Unique document Card required')
    result=_scope_dependencies(root,value,source,sources,docs[0]['card_id'],plan['selected'])
    result.pop('card_ids')
    return result

def _scope_dependencies(root,value,source,sources,root_card,required):
    nodes={}
    for c in value['cards']+value.get('reading_batches',[])+value.get('synthesis_nodes',[]):
        if c['card_id'] in nodes:require(nodes[c['card_id']]==c,'Conflicting Card identity')
        nodes[c['card_id']]=c
    complete=set();visiting=set();spans={};jobs=set()
    def visit(cid):
        require(cid in nodes,'Missing required child Card')
        if cid in complete:return
        require(cid not in visiting,'Cyclic Card dependencies');visiting.add(cid);c=nodes[cid]
        require(c['source_id']==source and c['canonical_sha256']==sources.documents[source]['canonical_sha256'],
                'Dependency belongs to another source snapshot')
        require(c.get('semantic_quality') not in {'FAIL','REJECTED','QUARANTINED'},'Known rejected Card scope')
        if c['parts'] and c['level']!='clue':
            require(c['overview']=='\n'.join(p['value']['overview'] for p in c['parts']),'Scope overview differs from model parts')
            for field in cards.FIELDS:
                require(c[field]==[x for p in c['parts'] for x in p['value'][field]],'Scope semantic content differs from model parts')
        for part in c['parts']:
            job=root/'jobs'/part['job_id'];verify(job);jp=read(job/'plan.json');r=read(job/'result.json')
            require(r['state']=='CONTENT_VERIFIED' and part['value']==read(job/'value.json'),'Invalid dependency job')
            jobs.add(job.name);view=jp['view']
            for q in jp['evidence']:sources.verify_quote(q)
            if view['mode']=='read':
                require(set(part['value']['read_ids'])=={q['id'] for q in jp['evidence']},'Direct reading receipt differs')
                for q in jp['evidence']:spans.setdefault(q['unit_id'],[]).append((q['ru_start'],q['ru_end']))
            else:
                require(set(part['value']['child_ids'])=={x['card_id'] for x in view['children']},'Child receipt differs')
                for child in view['children']:
                    child_id=child['card_id'];visit(child_id)
                    expected_child=v2.synthesis_view('',[nodes[child_id]],{},preserve_locations=True)['children'][0]
                    require(child==expected_child,'Synthesis child content differs from dependency Card')
        visiting.remove(cid);complete.add(cid)
    visit(root_card)
    for q in required:
        sources.verify_quote(q)
        cursor=q['ru_start']
        for start,end in sorted(spans.get(q['unit_id'],[])):
            if start<=cursor:cursor=max(cursor,end)
        require(cursor>=q['ru_end'],'Required source range lacks reachable successful direct read')
    return {'status':'PASS','reachable_cards':len(complete),'reachable_jobs':len(jobs),
            'required_ranges':len(required),'card_ids':sorted(complete),
            'coverage':'DECLARED_NARRATIVE_POLICY; NOT_FULL_CANONICAL_PAGE'}

def _scope_key(source,canonical,section,evidence):
    c={'source_id':source,'canonical_sha256':canonical,'level':'reading_batch','section':section,'overview':'','evidence':evidence}
    return card_structure.mark(c)['semantic_scope_id']

def reading_scopes(reuse,source_ids):
    """Reuse completed reading subtrees even when their source is still partial."""
    result={}
    for prior in reuse:
        prior=Path(prior)
        for source in source_ids:
            base=prior/'documents'/digest(source)
            if (base/'accepted-build.json').exists():continue
            attempts=sorted(base.glob('attempt-*/cards.json'))
            if not attempts:continue
            folder=attempts[-1].parent;verify(folder);value=read(folder/'cards.json')
            for c in value.get('reading_batches',[]):
                if not c['parts']:continue
                key=_scope_key(c['source_id'],c['canonical_sha256'],c['section'],c['evidence'])
                require(c.get('semantic_scope_id')==key,'Reading scope identity differs')
                result.setdefault(key,(prior,folder,value,c))
    return result

def reuse_reading_scope(root,view,evidence,cache,sources,aggregations):
    if not evidence:return None
    q=evidence[0];key=_scope_key(q['source_id'],q['canonical_sha256'],view.get('section'),evidence)
    if key not in cache:return None
    prior,folder,value,card=cache[key]
    require(len(card['parts'])==1,'Reading scope needs an explicit single root job')
    check=_scope_dependencies(prior,value,q['source_id'],sources,card['card_id'],evidence)
    nodes={c['card_id']:c for field in ('cards','reading_batches','synthesis_nodes') for c in value.get(field,[])}
    for cid in check['card_ids']:
        for part in nodes[cid]['parts']:import_job(root,prior/'jobs'/part['job_id'],sources)
        if cid!=card['card_id']:aggregations.append(nodes[cid])
    part=card['parts'][0]
    plan=read(prior/'jobs'/part['job_id']/'plan.json')
    children=[nodes[c['card_id']] for c in plan['view']['children']]
    origin=Path(root)/'reuse-origins/scopes'/key
    if not origin.exists():
        write(origin/'origin.json',{'policy':'verified-reading-subtree-1','original':str(folder.resolve()),
            'original_complete_sha256':sha(folder/'complete.json'),'original_card_id':card['card_id'],
            'requested_view':view,'requested_evidence_ids':[q['id'] for q in evidence],
            'scope_identity':key,'dependency_validation':check,'new_calls':0,
            'equivalence':'EXACT_CANONICAL_RU_RANGES_AND_SECTION; ORIGINAL_GENERATION_CONTEXT_PRESERVED'})
        seal(origin)
    return part|{'recovery_children':children,'recovery_evidence':card['evidence']}

def import_source(root,source,reuse,sources):
    root=Path(root)
    for other in reuse:
        other=Path(other);base=other/'documents'/digest(source);pointer=base/'accepted-build.json'
        if not pointer.exists():continue
        ref=read(pointer);folder=base/ref['directory'];verify(folder)
        require(ref['status']=='COMPLETE' and sha(folder/'complete.json')==ref['sha256'],'Reuse source pointer differs')
        validate_dependencies(other,folder,source,sources)
        data=read(folder/'cards.json')
        for c in data['cards']+data.get('reading_batches',[])+data.get('synthesis_nodes',[]):
            for part in c['parts']:import_job(root,other/'jobs'/part['job_id'],sources)
        target=root/'documents'/digest(source)
        for p in folder.rglob('*'):
            if p.is_file():copy(p,target/ref['directory']/p.relative_to(folder))
        copy(pointer,target/'accepted-build.json')
        origin=root/'reuse-origins/sources'/digest(source)
        write(origin/'origin.json',{'source_id':source,'original':str(folder.resolve()),
            'complete_sha256':ref['sha256'],'new_calls':0});seal(origin)
        return True
    return False
