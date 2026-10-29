"""Bounded offline orchestration; independent work continues around blocked jobs."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import time

from .. import navi_prompt_candidate as candidate
from .. import navi_v03_runner as old
from ..local_qwen3_embedding import LocalQwen3Config, LocalQwen3Embedding, REVISION
from . import VERSION, cards, index, requests
from .common import copy, digest, inventory, read, require, seal, sha, verify, write
from .sources import Sources, prepare_sources

REPO = Path(__file__).resolve().parents[4]
CANONICAL = 'data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json'
RU = 'data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json'
MODEL = '.local/models/qwen3-embedding-0.6b/' + REVISION


def code_identity():
    return {p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))}


def prepare(root, canonical_path, ru_path, source_ids, *, device='cuda', model_path=MODEL,
            max_ru=512, max_cards=64, max_pairs=20, max_card_jobs=32, body_limit=8000):
    root=Path(root)
    require(all(type(x) is int and x>0 for x in (max_ru,max_cards,max_pairs,max_card_jobs,body_limit)), 'Positive budgets required')
    require(device in {'cuda','cpu'},'Explicit embedding device required')
    require(max_cards>=len(source_ids),'Card budget must retain every document management node')
    require(not root.exists(),'New run root required')
    root.mkdir(parents=True)
    prepared=root/'prepared'
    prepared.mkdir()
    canonical=read(canonical_path)
    keys=[e['parsed_identity_key'] for e in canonical['records']]
    resolved=[]
    for source in source_ids:
        options=[key for key in keys if key==source or key.rsplit(':',1)[-1]==source]
        require(len(options)==1,'Source selection ambiguous/missing')
        resolved.append(options[0])
    require(len(set(resolved))==len(resolved),'Duplicate source selection')
    prepare_sources(prepared/'sources',canonical_path,ru_path,resolved)
    freeze=read(old.FREEZE)
    engine=old.frozen_engine(freeze)
    copy(old.FREEZE,prepared/'candidate-freeze.json')
    config={'version':VERSION,'sources':resolved,'device':device,'model_path':str(Path(model_path).resolve()),
            'max_ru':max_ru,'max_cards':max_cards,'max_pairs':max_pairs,'max_card_jobs':max_card_jobs,
            'body_limit':body_limit,'request_limit':24000,'top_k':20,'rrf_denominator':60,
            'code':code_identity(),'semantic_engine':engine,'model_revision':REVISION,
            'send_authorized':False,'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    write(prepared/'config.json',config)
    for p in Path(__file__).parent.glob('*.py'):
        copy(p,prepared/'execution-code'/p.name)
    seal(prepared)
    return {'plan_sha256':sha(prepared/'complete.json'),'config':config}


def verify_prepared(root, *, historical_audit=False):
    root=Path(root)
    verify(root/'prepared')
    config=read(root/'prepared/config.json')
    require(config['version']==VERSION,'Pipeline version differs')
    if historical_audit:
        folder=root/'prepared/execution-code'
        if not folder.exists():folder=root/'execution-code'
        require({p.name:sha(p) for p in folder.glob('*.py')}==config['code'],'Archived execution code missing/changed')
    else:
        require(config['code']==code_identity(),'Pipeline code changed since preparation')
    require(config['semantic_engine']==old.frozen_engine(read(root/'prepared/candidate-freeze.json')),'Frozen engine changed')
    return config


def link_view(sources, endpoints, quotes, candidates=(), scoped_quotes=None):
    view={}
    registry={}
    for side,source in zip(('A','B'),endpoints):
        selected=[q for q in quotes if q['source_id']==source]
        snippets=[]
        for i,q in enumerate(selected,1):
            sources.verify_quote(q)
            alias=side+f'{i:04d}'
            registry[alias]=q
            snippets.append({'id':alias,'text':q['text'],
                             'section':str(q['canonical_address']['section_ordinal']),
                             'source_context':{'kind':q['canonical_address']['canonical_unit_kind'],
                                               'content_role':q['content_role'],'provenance':q['provenance'],
                                               'structure':q['structure'],
                                               'caveat':'来源角色待核；展示顺序不是剧情顺序，分支并非同时发生'}})
        require(bool(snippets),'Missing endpoint text')
        scoped=[q for q in (scoped_quotes if scoped_quotes is not None else quotes) if q['source_id']==source]
        available=sources.rows(source)
        view[side]={'title':sources.documents[source]['title'],'snippets':snippets,
                    'coverage':{'scope':'query-selected source excerpts; not whole-page semantic coverage',
                                'available_ru_count':len(available),'selected_excerpt_count':len(scoped),
                                'delivered_excerpt_count':len(selected),'selected_chars':sum(len(q['text']) for q in scoped),
                                'delivered_chars':sum(len(q['text']) for q in selected),
                                'omitted_excerpt_ids':[q['id'] for q in scoped if q not in selected],
                                'whole_page_semantic_coverage':False}}
    if candidates:
        view['candidates']=list(candidates)
    return view,registry


def mock_link(view, *, review=False):
    # Honest zero-discovery mock; synthetic positive claims belong only to contract tests.
    ids={s:[q['id'] for q in view[s]['snippets']] for s in ('A','B')}
    value={'reading':{s:{'read_ids':ids[s],'unread_ids':[],'limitations':'MOCK delivery only; not semantic reading'} for s in ids},
           'source_roles':{s:[{'role':'unknown','basis':'MOCK未裁定来源角色','attribution':'UNKNOWN','evidence_ids':ids[s]}] for s in ids},
           'propositions':[],'no_link_reason':'MOCK_NOT_MODEL_OUTPUT: 未执行剧情语义判断'}
    if review:
        # A conservative U preserves every supplied original without certifying it.
        from .mock_contract import unresolved_review
        value['propositions']=[unresolved_review(c,ids) for c in view['candidates']]
    else:
        from .mock_contract import unresolved
        value['propositions']=[unresolved('MOCK：双方原文的剧情关系仍待核验',ids)]
    return value


def _run_job(root, stage, view, evidence, *, backend, mock_value, replay_root, live, api_key, sources):
    wire=cards.wire(view) if stage=='card' else candidate.prepare_request(view,mode=stage,model=old.c1.MODEL,
                        route='jizhi',max_output_tokens=30000,format_mode='json_object')['wire']
    job_id=digest([stage,wire,evidence])
    folder=Path(root)/'jobs'/job_id
    if stage!='card' and backend!='replay' and all(q['canonical_address']['canonical_unit_kind']=='rich_text' for q in evidence.values()):
        result=requests.execute_native(folder,stage,wire,view,evidence,sources,backend=backend,mock_value=mock_value,
                            candidate_freeze=Path(root)/'prepared/candidate-freeze.json',live_authorized=live,api_key=api_key,
                            ledger=None if backend=='live' else Path(root)/'mock-ledger')
    else:
        result=requests.execute(folder,stage,wire,view,evidence,backend=backend,mock_value=mock_value,
                                replay_root=replay_root,live_authorized=live,api_key=api_key,
                                ledger=None if backend=='live' else Path(root)/'mock-ledger')
    return job_id,result,read(folder/'value.json') if result['state']=='CONTENT_VERIFIED' else None


def _selected_rows(sources, config):
    # Round-robin across source and actual RU kind avoids first-source monopoly.
    groups=[]
    for source in config['sources']:
        for kind in ('rich_text','structured_observation','dialogue_graph'):
            groups.append(sources.iter_rows(source,kind))
    count=0
    while groups and count<config['max_ru']:
        remaining=[]
        for group in groups:
            if count>=config['max_ru']:
                break
            row=next(group,None)
            if row is not None:
                yield row
                count+=1
                remaining.append(group)
        groups=remaining


def run(root, encoder=None, *, backend='mock', replay_root=None, live_authorized=False,
        ack_plan=None, max_provider_tokens=None, api_key=None):
    root=Path(root)
    config=verify_prepared(root)
    require(backend in {'mock','replay','live'},'Invalid backend')
    if backend=='live':
        require(live_authorized is True and ack_plan==sha(root/'prepared/complete.json') and
                type(max_provider_tokens) is int and max_provider_tokens>0,'Separate live authorization, exact plan and token cap required')
        # Credentials are consulted only after all authorization/identity checks.
        if api_key is None:
            from genshin_corpus.rag.navi_r1_experiment import environment
            env=environment()
            require(env.get('JIZHI_BASE_URL','').rstrip('/')==old.c1.BASE_URL,'Configured route mismatch')
            api_key=env.get('JIZHI_API_KEY')
        require(bool(api_key),'Missing explicitly configured live credential')
    mode_path=root/'execution.json'
    execution={'backend':backend,'encoder_space':encoder.space_identity if encoder is not None else None,
               'live_authorized':live_authorized,'max_provider_tokens':max_provider_tokens,'ack_plan':ack_plan}
    if encoder is None:
        from .embedding import LocalEncoderProcess
        encoder=LocalEncoderProcess(REPO/'.local/embedding-runtime/Scripts/python.exe',Path(config['model_path']),config['device'],
                                    stderr_path=root/'embedding-worker.stderr.log')
        execution['encoder_space']=encoder.space_identity
        owned_encoder=True
    else:
        owned_encoder=False
    require(backend!='live' or encoder.contract.get('model_id')=='Qwen/Qwen3-Embedding-0.6B','Live requires the approved local encoder')
    if mode_path.exists():
        require(read(mode_path)==execution,'Resume execution mode/model/authorization changed')
    else:
        write(mode_path,execution)
    if not list(root.glob('completion-*.json')):
        committed=[p for p in root.glob('output*') if p.is_dir() and (p/'complete.json').exists()]
        require(len(committed)<=1,'Ambiguous committed outputs')
        if committed:
            meta=verify(committed[0])
            require(meta=={'prepared_sha256':sha(root/'prepared/complete.json'),'execution_sha256':sha(mode_path)},'Unpointed output identity differs')
            write(root/'completion-000.json',{'output':committed[0].name,'sha256':sha(committed[0]/'complete.json')})
    if list(root.glob('completion-*.json')):
        audit(root)
        if owned_encoder:
            encoder.close()
        return read(output_root(root)/'summary.json')
    sources=Sources(root/'prepared/sources')
    started=time.perf_counter()
    failures,deferred=[],[]
    card_list=[]
    jobs_used=0

    def job(stage, view, evidence, mock_value):
        nonlocal jobs_used
        if backend=='live':
            outcomes=[read(p) for p in (root/'jobs').glob('*/result.json')]
            # Conservative UTF-8-byte input reservation plus output cap; never billing/tokenizer truth.
            pending=[p for p in (root/'jobs').glob('*/plan.json') if not (p.parent/'complete.json').exists()]
            require(not pending and all(isinstance(r['usage'],dict) for r in outcomes),'Unresolved provider usage blocks dispatch')
            wire=cards.wire(view) if stage=='card' else candidate.prepare_request(view,mode=stage,model=old.c1.MODEL,route='jizhi',max_output_tokens=30000)['wire']
            reservation=len(json.dumps(wire,ensure_ascii=False).encode())+wire['max_output_tokens']
            require(sum(r['usage']['total_tokens'] for r in outcomes)+reservation<=max_provider_tokens,'Provider token budget exhausted/unknown')
        jobs_used+=1
        return _run_job(root,stage,view,evidence,backend=backend,mock_value=mock_value,replay_root=replay_root,live=live_authorized,api_key=api_key,sources=sources)

    try:
        for source in config['sources']:
            parts=[]
            for section,view in cards.batches(sources,source,config['body_limit'],config['request_limit']):
                if jobs_used>=config['max_card_jobs']:
                    deferred.append({'stage':'card','source_id':source,'reason':'CARD_JOB_BUDGET','evidence_ids':[q['id'] for q in view['evidence']]})
                    continue
                try:
                    jid,result,value=job('card',view,view['evidence'],cards.mock(view))
                    if value is None:
                        failures.append({'stage':'card','source_id':source,'job_id':jid,'state':result['state']})
                    else:
                        parts.append((section,{'job_id':jid,'value':value,'authority':result['authority']},view))
                except (ValueError,FileExistsError) as exc:
                    failures.append({'stage':'card','source_id':source,'diagnostics':requests.transport.diagnostics(exc)})
            card_list.extend(cards.hierarchy(sources,source,parts))
        # Retain all document nodes, then sections before clues; budget omissions stay visible.
        ordered=sorted(card_list,key=lambda c:({'document':0,'section':1,'clue':2}[c['level']],c['source_id'],c['card_id']))
        kept=[]
        ids=set()
        for card in ordered:
            if len(kept)<config['max_cards'] and (card['parent_id'] is None or card['parent_id'] in ids):
                kept.append(card);ids.add(card['card_id'])
            else:
                deferred.append({'stage':'card_index','card_id':card['card_id'],'reason':'CARD_COUNT_BUDGET'})
        for card in kept:
            card['child_ids']=[uid for uid in card['child_ids'] if uid in ids]
        ref=root/'card-stage-reference.json'
        stage=root/read(ref)['directory'] if ref.exists() else root/'card-stage'
        payload={'cards':kept,'failures':failures,'deferred':deferred}
        if stage.exists() and (stage/'complete.json').exists():
            verify(stage)
            require(read(stage/'cards.json')==payload,'Card recovery result changed')
        else:
            if stage.exists():stage=root/f'card-stage-attempt-{len(list(root.glob("card-stage*"))):03d}'
            write(stage/'cards.json',payload);seal(stage)
        if not ref.exists():write(ref,{'directory':stage.name,'sha256':sha(stage/'complete.json')})
        ru_items=({'id':r['unit_id'],'source_id':r['source']['canonical_address']['source_identity_key'],'text':r['retrieval_visible_text']} for r in _selected_rows(sources,config))
        ru_identity=digest([sha(root/'prepared/complete.json'),config['max_ru'],'round_robin_source_kind_uid_v1'])
        index.build(root/'indexes/ru',ru_items,encoder,kind='ru',input_identity=ru_identity)
        card_items=({'id':c['card_id'],'source_id':c['source_id'],'text':cards.projection(c)} for c in kept)
        index.build(root/'indexes/card',card_items,encoder,kind='card',input_identity=sha(stage/'cards.json'))
        indexes={k:index.Index(root/'indexes'/k,kind=k,encoder_space=encoder.space_identity) for k in ('ru','card')}
        rankings=[]
        pairs={}
        for card in kept:
            seen=set()
            for detail in card['queries']:
                text=detail['text']
                if text in seen:
                    continue
                seen.add(text)
                qid=digest([card['card_id'],text,detail['evidence_ids']])
                try:
                    vector=index.query(encoder,text)
                    for kind,reader in indexes.items():
                        hits=reader.search(vector,kind=kind,top_k=config['top_k'])
                        rankings.append({'query_id':qid,'query':text,'card_id':card['card_id'],'kind':kind,'hits':hits})
                        source_ranks={}
                        for hit in hits:
                            target=hit['source_id']
                            if target==card['source_id']:
                                continue
                            if target in source_ranks:
                                continue
                            source_ranks[target]=hit['rank']
                            endpoints=tuple(sorted([card['source_id'],target]))
                            pid=digest(endpoints)
                            pair=pairs.setdefault(pid,{'pair_id':pid,'endpoints':list(endpoints),'rrf':0.0,'discoveries':[]})
                            pair['rrf']+=1/(config['rrf_denominator']+hit['rank'])
                            pair['discoveries'].append({'query_id':qid,'source_id':card['source_id'],'target':target,'hit':hit,
                                                        'source_evidence_ids':detail['evidence_ids']})
                except ValueError as exc:
                    failures.append({'stage':'query','query_id':qid,'diagnostics':requests.transport.diagnostics(exc)})
        ordered_pairs=sorted(pairs.values(),key=lambda p:(-p['rrf'],p['pair_id']))
        links=[]
        card_map={c['card_id']:c for c in kept}
        all_quotes={q['id']:q for c in kept for q in c['evidence']}
        for position,pair in enumerate(ordered_pairs):
            if position>=config['max_pairs']:
                deferred.append({'stage':'link','pair_id':pair['pair_id'],'reason':'PAIR_BUDGET'})
                continue
            try:
                quotes={}
                for found in pair['discoveries']:
                    for eid in found['source_evidence_ids']:
                        quotes[eid]=all_quotes[eid]
                    hit=found['hit']
                    targets=[sources.quote(hit['id'])] if hit['kind']=='ru' else card_map[hit['id']]['evidence']
                    for q in targets:
                        quotes[q['id']]=q
                # Complete selected RUs when possible; budgeted delivery records every omitted quote.
                admitted=[];omitted=[]
                for source in pair['endpoints']:
                    chars=0
                    own=[q for q in quotes.values() if q['source_id']==source]
                    for q in own:
                        if chars+len(q['text'])<=8000:
                            admitted.append(q);chars+=len(q['text'])
                        else:
                            omitted.append(q['id'])
                view,binding=link_view(sources,pair['endpoints'],admitted,scoped_quotes=list(quotes.values()))
                jid,result,value=job('generation',view,binding,mock_link(view))
                link={'link_id':digest([pair['pair_id'],jid]),'pair':pair,'generation_job':jid,'generation':value,
                      'delivery':binding,'omitted_evidence_ids':omitted,'review_job':None,'review':None,
                      'human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE'}
                links.append(link)
                if value is None:
                    failures.append({'stage':'generation','job_id':jid,'state':result['state']})
                elif value['propositions']:
                    originals=[{'id':p['id'],'text':p['text'],'role':p['role'],
                                'text_sha256':candidate.sha256(p['text'].encode()).hexdigest()} for p in value['propositions']]
                    reviewed=deepcopy(view);reviewed['candidates']=originals
                    try:
                        rid,rresult,rvalue=job('review',reviewed,binding,mock_link(reviewed,review=True))
                        link.update(review_job=rid,review=rvalue)
                        if rvalue is None:
                            failures.append({'stage':'review','job_id':rid,'state':rresult['state']})
                    except (ValueError,FileExistsError) as exc:
                        link['review_disposition']='BLOCKED_RETAIN_ORIGINAL_GENERATION'
                        failures.append({'stage':'review','link_id':link['link_id'],'diagnostics':requests.transport.diagnostics(exc)})
                else:
                    link['review_disposition']='NO_PROPOSITIONS_TO_REVIEW'
            except (ValueError,FileExistsError) as exc:
                failures.append({'stage':'link','pair_id':pair['pair_id'],'diagnostics':requests.transport.diagnostics(exc)})
        output=root/'output'
        # A crash before the completion seal keeps this attempt; later attempts never overwrite it.
        if output.exists():
            require(not (output/'complete.json').exists(),'Output already completed')
            output=root/f'output-attempt-{len(list(root.glob("output*"))):03d}'
        total_rows=sources.db.execute('select count(*) from rows').fetchone()[0]
        selected_count=min(total_rows,config['max_ru'])
        summary={'version':VERSION,'backend':backend,'semantic_quality':'NOT_VALIDATED','cards':len(kept),
                 'ru_indexed':selected_count,'ru_deferred':total_rows-selected_count,'candidate_pairs':len(ordered_pairs),
                 'links':len(links),'failures':len(failures),'deferred':len(deferred),'jobs':jobs_used,
                 'elapsed_seconds':round(time.perf_counter()-started,3),'encoder_space':encoder.space_identity,
                 'human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE','backup_status':'NOT_CONFIRMED',
                 'output_directory':output.name}
        for name,value in [('cards',payload),('rankings',rankings),('candidates',ordered_pairs),('links',links),
                           ('failures',failures),('deferred',deferred),('summary',summary)]:
            write(output/(name+'.json'),value)
        seal(output,{'prepared_sha256':sha(root/'prepared/complete.json'),'execution_sha256':sha(root/'execution.json')})
        write(root/f'completion-{len(list(root.glob("completion-*.json"))):03d}.json',{'output':output.name,'sha256':sha(output/'complete.json')})
        return summary
    finally:
        sources.close()
        if owned_encoder:
            encoder.close()
            metrics=root/f'embedding-metrics-{len(list(root.glob("embedding-metrics-*.json"))):03d}.json'
            write(metrics,encoder.metrics)


def output_root(root):
    paths=sorted(Path(root).glob('completion-*.json'))
    require(bool(paths),'Run has no completed output')
    ref=read(paths[-1]);folder=Path(root)/ref['output']
    require(sha(folder/'complete.json')==ref['sha256'],'Completion pointer differs')
    return folder


def audit(root):
    root=Path(root)
    config=verify_prepared(root,historical_audit=True)
    sources=Sources(root/'prepared/sources')
    try:
        output=output_root(root)
        meta=verify(output)
        require(meta['prepared_sha256']==sha(root/'prepared/complete.json') and
                meta['execution_sha256']==sha(root/'execution.json'),'Output execution/input binding differs')
        stage_ref=read(root/'card-stage-reference.json')
        card_stage=root/stage_ref['directory']
        require(sha(card_stage/'complete.json')==stage_ref['sha256'],'Card checkpoint pointer differs')
        verify(card_stage)
        for path in (root/'jobs').glob('*/plan.json'):
            if not (path.parent/'complete.json').exists():
                # A pending reservation remains forensic evidence and is never resubmitted.
                continue
            verify(path.parent)
            plan=read(path)
            if plan.get('executor')=='UNCHANGED_V03_RUNNER':
                old.audit_run(path.parent/'native-run')
            for q in (plan['evidence'].values() if isinstance(plan['evidence'],dict) else plan['evidence']):
                sources.verify_quote(q)
            if read(path.parent/'result.json')['state']=='CONTENT_VERIFIED':
                checked=requests.checked_stream(path.parent,plan['wire']['max_output_tokens'])
                require(requests.validate_content(plan['stage'],checked['message_text'],plan['view'])==read(path.parent/'value.json'),'Archived content differs')
        for kind in ('ru','card'):
            verify(root/'indexes'/kind)
        pending=[p.parent.name for p in (root/'jobs').glob('*/plan.json') if not (p.parent/'complete.json').exists()]
        return {'archive_integrity':'PASS','pending_jobs_isolated':pending,'current_code_matches_run':config['code']==code_identity(),
                'semantic_quality':'NOT_VALIDATED','summary':read(output/'summary.json')}
    finally:
        sources.close()


def export_review(root, destination):
    audit(root)
    root,destination=Path(root),Path(destination)
    require(not destination.exists(),'New export directory required')
    destination.mkdir(parents=True)
    # Include the self-contained prepared snapshots and raw jobs, not just a reviewer summary.
    stage=root/read(root/'card-stage-reference.json')['directory']
    for base in (root/'prepared',root/'jobs',stage,root/'human-reviews',output_root(root)):
        for p in base.rglob('*'):
            if p.is_file():
                copy(p,destination/p.relative_to(root))
    write(destination/'review-instructions.json',{'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED',
          'backup_status':'SAME_DISK_EXPORT_NOT_INDEPENDENT_BACKUP','source_run':str(root.resolve()),
          'review_contract':'Exact artifact hash, reviewer, timestamp, decision and reason required; never activates Navi'})
    seal(destination)
    return {'files':len(inventory(destination)),'manifest_sha256':sha(destination/'complete.json')}


def record_review(root, artifact, reviewer, timestamp, decision, reason, item_id=None):
    audit(root)
    require(decision in {'ACCEPT','REJECT','NEEDS_INFORMATION'} and all(isinstance(x,str) and x.strip() for x in (reviewer,timestamp,reason)), 'Incomplete human review')
    from datetime import datetime
    require(datetime.fromisoformat(timestamp).tzinfo is not None,'Review timestamp needs timezone')
    artifact=Path(artifact).resolve()
    require(artifact.is_relative_to(output_root(root).resolve()) and artifact.is_file(),'Review must bind completed output artifact')
    row={'artifact':artifact.relative_to(Path(root).resolve()).as_posix(),'sha256':sha(artifact),'reviewer':reviewer,
         'timestamp':timestamp,'decision':decision,'reason':reason,'navigation_status':'INACTIVE','publication_authorized':False}
    if item_id is not None:
        value=read(artifact)
        items=value.get('cards',[]) if isinstance(value,dict) else value
        require(isinstance(items,list),'Artifact has no reviewable Card/Link collection')
        matches=[v for v in items if v.get('card_id',v.get('link_id'))==item_id]
        require(len(matches)==1,'Review item missing/ambiguous')
        row.update(item_id=item_id,item_sha256=digest(matches[0]))
    path=Path(root)/'human-reviews'/(digest(row)+'.json')
    write(path,row)
    return row
