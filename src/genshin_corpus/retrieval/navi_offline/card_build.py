"""Card-only batches; immutable per-source revisions reuse auditable successful jobs."""
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import json

from . import cards, cards_v2 as v2, requests, metro_chat, card_generation
from ... import model_calls
from .common import copy, digest, read, require, seal, sha, verify, write
from .sources import Sources, prepare_sources

VERSION = 'p05-card-build-1'


def prepare_snapshot(root, snapshot, *, total_request_limit, body_limit=8000, request_limit=24000, generation_strategy=card_generation.DEEPSEEK):
    """Re-use an immutable verified typed-source snapshot, never rewrite it."""
    root=Path(root);snapshot=Path(snapshot);verify(snapshot)
    require(not root.exists(),'New Card run root required')
    require(generation_strategy in card_generation.STRATEGIES,'Unknown Card strategy')
    require(type(total_request_limit) is int and total_request_limit>0,'Explicit finite request cap required')
    require(0<body_limit<=8000 and request_limit>=12000,'Invalid Card budgets')
    sources=Sources(snapshot)
    try:keys=list(sources.documents)
    finally:sources.close()
    for p in snapshot.rglob('*'):
        if p.is_file():copy(p,root/'prepared/sources'/p.relative_to(snapshot))
    config={'version':VERSION,'card_version':v2.VERSION,'prompt_sha256':digest(card_generation.PROMPT),
       'schema_sha256':digest(v2.SCHEMA),'projection':v2.PROJECTION,'reading_policy':v2.READING_POLICY,
       'route':'tokenmetro','api_surface':'chat_completions','base_url':requests.METRO_URL,'model':'deepseek-v4.1-flash' if generation_strategy==card_generation.DEEPSEEK else 'qwen3.8-flash',
       'generation_strategy':generation_strategy,'combine_sections':True,'total_request_limit':total_request_limit,
       'source_snapshot_sha256':sha(snapshot/'complete.json'),'revision':'initial','regeneration_reason':'',
       'regenerate_jobs':[],'body_limit':body_limit,'request_limit':request_limit,'sources':keys,'code':code_identity(),
       'send_authorized':False,'scope':'CARDS_ONLY_NO_LINK_NO_EMBEDDING','navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    # Short structural types first; the long source uses remaining bounded calls.
    sources=Sources(root/'prepared/sources')
    try:config['sources'].sort(key=lambda s:sum(len(q['text']) for q in v2.reading_plan(sources,s)['selected']))
    finally:sources.close()
    write(root/'prepared/config.json',config)
    for p in Path(__file__).parent.glob('*.py'):copy(p,root/'prepared/execution-code'/p.name)
    model_calls.snapshot(root/'prepared/execution-code')
    seal(root/'prepared')
    return {'prepared_sha256':sha(root/'prepared/complete.json'),'sources':len(keys),'send_authorized':False}


def code_identity():
    return {p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))} | model_calls.code_identity()


def prepare(root, canonical, ru, source_ids=None, *, revision='initial', reason='', regenerate_jobs=(), body_limit=8000, request_limit=24000,
            generation_strategy=None,total_request_limit=None,combine_sections=False):
    root=Path(root)
    require(not root.exists(),'New Card run root required')
    require(revision=='initial' or bool(reason.strip()),'A targeted regeneration revision needs an explicit reason')
    require(not regenerate_jobs or (revision!='initial' and bool(reason.strip())), 'Targeted jobs need a new revision and reason')
    require(0<body_limit<=8000 and request_limit>=12000,'Invalid Card reading budgets')
    require(generation_strategy is None or generation_strategy in card_generation.STRATEGIES,'Unsupported Card strategy')
    require(total_request_limit is None or type(total_request_limit) is int and total_request_limit>0,'Invalid campaign call limit')
    manifest=read(canonical); keys=[e['parsed_identity_key'] for e in manifest['records']]
    selected=keys if source_ids is None else []
    if source_ids is not None:
        for s in source_ids:
            matches=[k for k in keys if k==s or k.rsplit(':',1)[-1]==s]
            require(len(matches)==1,'Unknown/ambiguous Card source');selected.append(matches[0])
    prepared=root/'prepared';prepared.mkdir(parents=True)
    prepare_sources(prepared/'sources',canonical,ru,selected)
    config={'version':VERSION,'card_version':v2.VERSION,'prompt_sha256':digest(v2.PROMPT),
            'schema_sha256':digest(v2.SCHEMA),'projection':v2.PROJECTION,'reading_policy':v2.READING_POLICY,
            'route':'tokenmetro','api_surface':'responses','base_url':requests.METRO_URL,'model':'deepseek-v4.1-flash',
            'revision':revision,'regeneration_reason':reason,'regenerate_jobs':list(regenerate_jobs),
            'body_limit':body_limit,'request_limit':request_limit,
            'sources':selected,'code':code_identity(),'send_authorized':False,
            'scope':'CARDS_ONLY_NO_LINK_NO_EMBEDDING','navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    if generation_strategy:
        config.update(generation_strategy=generation_strategy,api_surface='chat_completions',model='deepseek-v4.1-flash' if generation_strategy==card_generation.DEEPSEEK else 'qwen3.8-flash',
                      prompt_sha256=digest(card_generation.PROMPT),total_request_limit=total_request_limit,
                      combine_sections=combine_sections)
    write(prepared/'config.json',config)
    for p in Path(__file__).parent.glob('*.py'):copy(p,prepared/'execution-code'/p.name)
    model_calls.snapshot(prepared/'execution-code')
    seal(prepared)
    return {'prepared_sha256':sha(prepared/'complete.json'),'sources':len(selected),'send_authorized':False}


def run(root, *, backend='mock', max_new_requests=20, reuse=(), live_authorized=False,
        ack_plan=None, max_provider_tokens=None, api_key=None, call_policy=None):
    root=Path(root);verify(root/'prepared');config=read(root/'prepared/config.json')
    require(config['code']==code_identity(),'Card code changed since preparation')
    require(backend in {'mock','live'} and type(max_new_requests) is int and max_new_requests>0,'Invalid Card execution mode/batch size')
    if backend=='live':
        require(config.get('route')=='tokenmetro','New live Cards require explicit Metro preparation')
        require(live_authorized is True and ack_plan==sha(root/'prepared/complete.json') and
                type(max_provider_tokens) is int and max_provider_tokens>0,'Separate Card call authorization and exact budget required')
        if api_key is None:
            from genshin_corpus.rag.navi_r1_experiment import environment
            env=environment()
            require(env.get('TOKENMETRO_BASE_URL','').rstrip('/')==requests.METRO_URL,'Card Metro route mismatch')
            api_key=env.get('TOKENMETRO_API_KEY')
        require(bool(api_key),'Missing configured Card credential')
    policy=call_policy or model_calls.Policy()
    require(backend!='live' or type(config.get('total_request_limit')) is int,'Live campaign requires explicit total_request_limit')
    budget_root=root/'model-call-budget' if config.get('total_request_limit') else root/'model-call-batches'/f'{len(list((root / "model-call-batches").glob("*"))):04d}'
    batch=model_calls.Batch(budget_root,max_requests=config.get('total_request_limit') or max_new_requests,max_tokens=max_provider_tokens,dispatch_limit=max_new_requests)
    execution={'call_policy':asdict(policy),'backend':backend,'max_provider_tokens':max_provider_tokens,'ack_plan':ack_plan,'live_authorized':live_authorized}
    if (root/'execution.json').exists():require(read(root/'execution.json')==execution,'Card resume mode/budget changed')
    else:write(root/'execution.json',execution)
    count=0; new_calls=0
    strategy=config.get('generation_strategy')
    cache={};targets=set(config.get('regenerate_jobs',[]));target_inputs=set();found_targets=set()
    for other in reuse:
        other=Path(other);verify(other/'prepared')
        require(read(other/'execution.json')['backend']==backend,'Cannot reuse Mock as real Card output')
        for p in (other/'jobs').glob('*/plan.json'):
            plan=read(p)
            if p.parent.name in targets:
                base_view={k:v for k,v in plan['view'].items() if k not in {'build_revision','regeneration_reason'}}
                target_inputs.add(digest([base_view,plan['evidence']]))
                found_targets.add(p.parent.name)
            if (p.parent/'complete.json').exists() and (strategy or read(p.parent/'result.json')['state']=='CONTENT_VERIFIED'):
                cache.setdefault(read(p)['fingerprint'],p.parent)
    require(found_targets==targets,'Targeted regeneration jobs must exist in explicit reuse archives')
    sources=Sources(root/'prepared/sources')

    def job(view,evidence):
        nonlocal count,new_calls
        targeted=not targets or digest([view,evidence]) in target_inputs
        view=view|{'build_revision':config['revision'] if targeted else 'initial',
                   'regeneration_reason':config['regeneration_reason'] if targeted else ''}
        wire=v2.wire(view) if not strategy else card_generation.wire(view,strategy)
        route=config.get('route');fp=requests.request_fingerprint(wire,route) if not strategy else metro_chat.fingerprint(wire)
        jid=digest(['card',fp,evidence]);folder=root/'jobs'/jid
        if not folder.exists() and fp in cache and strategy:
            prior=cache[fp];verify(prior)
            for p in prior.rglob('*'):
                if p.is_file():copy(p,folder/p.relative_to(prior))
        if not folder.exists() and fp in cache and not strategy:
            prior=cache[fp]
            requests.execute(folder,'card',wire,view,evidence,backend='replay',replay_root=prior.parent,route=route)
        if (folder/'call.json').exists() and not (folder/'complete.json').exists():
            saved_policy=model_calls.Policy(**read(folder/'call.json')['policy'])
            require({k:v for k,v in asdict(saved_policy).items() if k!='max_attempts'}=={k:v for k,v in asdict(policy).items() if k!='max_attempts'},'Resume call policy changed')
            require(new_calls<max_new_requests,'BATCH_REQUEST_BUDGET_DEFERRED')
            before=batch.accounting()['request_reservations']
            if strategy:metro_chat.execute(folder,view,evidence,live_authorized=live_authorized,api_key=api_key,strategy=strategy,call_policy=saved_policy,call_batch=batch)
            else:requests.execute(folder,'card',wire,view,evidence,backend=backend,mock_value=v2.mock(view) if backend=='mock' else None,live_authorized=live_authorized,api_key=api_key,route=route,call_policy=saved_policy,call_batch=batch)
            new_calls+=batch.accounting()['request_reservations']-before
        if folder.exists():
            require((folder/'complete.json').exists(),'Pending Card reservation: isolated, never reissue')
            verify(folder); result=read(folder/'result.json')
            plan=read(folder/'plan.json')
            require(all(plan[k]==v for k,v in {'stage':'card','wire':wire,'view':view,'evidence':evidence}.items()),'Cached Card input changed')
            require(result['state']=='CONTENT_VERIFIED','Failed Card isolated; raw evidence retained; independent jobs may continue')
            if strategy:
                records,value=card_generation.assess(requests.transport.decoded_body((folder/'response.body.raw').read_bytes(),
                    read(folder/'http.json')['headers'].get('content-encoding','')),read(folder/'sdk-response.json'),view,evidence,strategy)
                require(records['protocol_validation']['status']==records['content_validation']['status']=='PASS','Cached Chat validation failed')
                require(read(folder/'http.json')['status']==200,'Cached Chat HTTP failed')
                recovered=root/'recovered-jobs'/jid
                if not recovered.exists():
                    write(recovered/'assessment.json',{k:v for k,v in records.items() if k not in {'final_text','reasoning_text'}})
                    write(recovered/'value.json',value)
                    write(recovered/'origin.json',{'original_job_complete_sha256':sha(folder/'complete.json'),
                        'original_state':result['state'],'original_result_unchanged':True,'current_code':code_identity(),
                        'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'})
                    seal(recovered)
                verify(recovered);require(read(recovered/'value.json')==value,'Recovery content changed')
            else:
                checked=requests.checked_stream(folder,wire['max_output_tokens'])
                value=requests.validate_content('card',checked['message_text'],view,evidence)
            require(value==read((recovered if strategy else folder)/'value.json'),'Cached Card content differs')
        else:
            require(count<max_new_requests,'BATCH_REQUEST_BUDGET_DEFERRED')
            limit=config.get('total_request_limit')
            require(limit is None or len(list((root/'jobs').glob('*/plan.json')))<limit,'CAMPAIGN_REQUEST_BUDGET_DEFERRED')
            require(new_calls<max_new_requests,'BATCH_REQUEST_BUDGET_DEFERRED')
            remaining=min(max_new_requests-new_calls,batch.config['max_requests']-batch.accounting()['request_reservations'])
            require(remaining>0,'CAMPAIGN_REQUEST_BUDGET_DEFERRED')
            job_policy=policy
            count+=1
            if strategy:
                require(backend=='live','Structured strategy Mock must use isolated transport tests')
                result=metro_chat.execute(folder,view,evidence,live_authorized=live_authorized,api_key=api_key,strategy=strategy,call_policy=job_policy,call_batch=batch)
            else:
                result=requests.execute(folder,'card',wire,view,evidence,backend=backend,mock_value=v2.mock(view) if backend=='mock' else None,
                    live_authorized=live_authorized,api_key=api_key,route=route,call_policy=job_policy,call_batch=batch)
            new_calls+=result.get('calls',0)
            require(result['state']=='CONTENT_VERIFIED','Card response invalid; raw evidence retained, no retry')
            value=read(folder/'value.json')
        return {'job_id':jid,'value':value,'authority':result['authority']}

    aggregations=[]
    def synth(source, children, level, section, plan):
        evidence={q['id']:q for c in children for q in c['evidence']}
        scope={'unread_or_excluded':sum(bool(u['unread_ranges']) for u in plan['units']),
               'upstream_skipped':len(plan['upstream_skips']),'children_only':True,
               'failed_or_deferred_read_batches':len(failures),
               'sections_with_some_unread_ranges_NOT_fully_unread_sections':sorted({u['section'] for u in plan['units'] if u['unread_ranges']}),
               'per_section_representation_coverage':[{'section':section,
                 'selected_chars':sum(b-a for u in plan['units'] if u['section']==section for a,b in u['selected_ranges']),
                 'omitted_chars_including_alternate_representations':sum(b-a for u in plan['units'] if u['section']==section for a,b in u['unread_ranges']),
                 'kinds':sorted({u['kind'] for u in plan['units'] if u['section']==section})}
                 for section in sorted({u['section'] for u in plan['units']})],
               'coverage_authority':'PROGRAM_RANGES; omission of some ranges or alternate representations never means an entire chapter was unread',
               'not_direct_full_source_reading':True}
        queue=list(children); groups=[];current=[]
        for c in queue:
            if len(json.dumps(v2.wire(v2.synthesis_view(sources.documents[source]['title'],current+[c],scope)),ensure_ascii=False))>config['request_limit']:
                require(bool(current),'Single child exceeds synthesis budget; no silent semantic truncation')
                groups.append(current);current=[]
            current.append(c)
        if current:groups.append(current)
        if len(groups)>1:
            require(len(groups)<len(children),'Synthesis cannot reduce within budget')
            children=[synth(source,g,'synthesis_batch',section,plan) for g in groups]
            return synth(source,children,level,section,plan)
        view=v2.synthesis_view(sources.documents[source]['title'],children,scope)
        part=job(view,list(evidence.values()))
        result=v2.make_card(sources,source,level,section,part,list(evidence.values()),children=children,plan=plan)
        # A tree's original leaf dependencies remain explicit, never fake sections.
        result['synthesis_inputs']=[{'card_id':c['card_id'],'job_ids':[p['job_id'] for p in c['parts']],
                                    'level':c['level']} for c in children]
        aggregations.append(result)
        return result

    outcomes=[]
    try:
        for source in config['sources']:
            aggregations=[]
            base=root/'documents'/digest(source)
            complete=base/'accepted-build.json'
            if complete.exists():
                ref=read(complete);target=base/ref['directory'];verify(target)
                require(sha(target/'complete.json')==ref['sha256'],'Card source pointer differs')
                outcomes.append({'source_id':source,'status':ref['status'],'directory':str(target.relative_to(root))});continue
            plan=v2.reading_plan(sources,source); group=defaultdict(list); reading=[];failures=[]
            for section,view,evidence in v2.read_batches(sources,source,plan,config['body_limit'],config['request_limit'],config.get('combine_sections',False)):
                try:
                    part=job(view,evidence)
                    c=v2.make_card(sources,source,'reading_batch',section,part,evidence,plan=plan)
                    reading.append(c);group[section].append(c)
                except (ValueError,FileExistsError) as exc:failures.append(str(exc))
            final=[]
            try:
                if not reading:
                    doc=v2.make_card(sources,source,'document',None,None,[],plan=plan)
                elif set(group)=={None} and len(reading)==1:
                    doc=v2.make_card(sources,source,'document',None,reading[0]['parts'][0],reading[0]['evidence'],plan=plan)
                else:
                    sections=[]
                    for section,cs in group.items():
                        if section is None:sections+=cs
                        elif len(cs)==1:sections.append(v2.make_card(sources,source,'section',section,cs[0]['parts'][0],cs[0]['evidence'],plan=plan))
                        else:sections.append(synth(source,cs,'section',section,plan))
                    doc=synth(source,sections,'document',None,plan)
                    final += [c for c in sections if c['level'] in {'section','reading_batch'}]
                    for c in final:c['parent_id']=doc['card_id']
                doc['child_ids']=[c['card_id'] for c in final]
                for c in final:c['child_ids']=[]
                final.insert(0,doc)
                for parent in list(final):
                    for n,lead in enumerate(parent['open_questions']):
                        quotes=[q for q in parent['evidence'] if q['id'] in lead['evidence_ids']]
                        clue=v2.make_card(sources,source,'clue',parent['section'],None,quotes,children=[parent],plan=plan)
                        clue.update(card_id=digest([parent['card_id'],'clue',n,lead]),parent_id=parent['card_id'],child_ids=[],
                                    overview=lead['text'],open_questions=[lead],queries=[lead],limitations=parent['limitations'])
                        parent['child_ids'].append(clue['card_id']);final.append(clue)
            except (ValueError,FileExistsError) as exc:failures.append(str(exc))
            attempt=base/f'attempt-{len(list(base.glob("attempt-*"))):04d}'
            status='COMPLETE' if not failures else 'PARTIAL_REQUIRES_TARGETED_ACTION'
            write(attempt/'cards.json',{'cards':final,'reading_batches':reading,'synthesis_nodes':aggregations,
                                      'failures':failures,'status':status})
            write(attempt/'reading-plan.json',plan);seal(attempt)
            if not failures:write(complete,{'directory':attempt.name,'sha256':sha(attempt/'complete.json'),'status':status})
            outcomes.append({'source_id':source,'status':status,'directory':str(attempt.relative_to(root))})
        summary={'version':VERSION,'sources':outcomes,'new_sdk_calls':new_calls,'new_jobs':count,
                 'call_accounting':batch.accounting(),'backend':backend,'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED',
                 'semantic_quality':'NOT_VALIDATED','actual_fee':'UNKNOWN'}
        write(root/f'batch-{len(list(root.glob("batch-*.json"))):04d}.json',summary)
        return summary
    finally:sources.close()


def iter_cards(root):
    root=Path(root);verify(root/'prepared')
    config=read(root/'prepared/config.json')
    for source in config['sources']:
        base=root/'documents'/digest(source)
        require((base/'accepted-build.json').exists(),'Unfinished source cannot enter complete Card index')
        ref=read(base/'accepted-build.json');folder=base/ref['directory'];verify(folder)
        require(sha(folder/'complete.json')==ref['sha256'],'Card revision pointer differs')
        for card in read(folder/'cards.json')['cards']:
            # Clues duplicate parent questions; retained, without independent vectors.
            if card['level']!='clue' and card['parts']:yield card


def audit(root):
    root=Path(root);verify(root/'prepared')
    config=read(root/'prepared/config.json')
    archived={p.name:sha(p) for p in (root/'prepared/execution-code').glob('*.py')}
    require(archived==config['code'],'Card code snapshot differs')
    sources=Sources(root/'prepared/sources');states=defaultdict(int)
    try:
        for p in (root/'jobs').glob('*/plan.json'):
            if not (p.parent/'complete.json').exists():states['PENDING_ISOLATED']+=1;continue
            verify(p.parent);plan=read(p);result=read(p.parent/'result.json');states[result['state']]+=1
            strategy=plan.get('strategy')
            require((metro_chat.fingerprint(plan['wire']) if strategy else requests.request_fingerprint(plan['wire'],plan.get('route')))==plan['fingerprint'],'Card job fingerprint differs')
            if (p.parent/'request.body.bin').exists():
                require(requests.candidate.strict_json((p.parent/'request.body.bin').read_text(encoding='utf8'))==(plan['actual_body'] if strategy else plan['wire']),
                        'Actual Card body differs')
            for q in plan['evidence']:sources.verify_quote(q)
            if result['state']=='CONTENT_VERIFIED':
                if strategy:
                    records,value=card_generation.assess(requests.transport.decoded_body((p.parent/'response.body.raw').read_bytes(),
                        read(p.parent/'http.json')['headers'].get('content-encoding','')),read(p.parent/'sdk-response.json'),plan['view'],plan['evidence'],strategy)
                    require(records['protocol_validation']['status']==records['content_validation']['status']=='PASS' and value==read(p.parent/'value.json'),'Card Chat content replay differs')
                else:
                    checked=requests.checked_stream(p.parent,plan['wire']['max_output_tokens'])
                    require(requests.validate_content('card',checked['message_text'],plan['view'],plan['evidence'])==read(p.parent/'value.json'),
                            'Card content replay differs')
        for p in (root/'documents').glob('*/attempt-*/complete.json'):verify(p.parent)
        for p in (root/'recovered-jobs').glob('*/origin.json'):
            verify(p.parent);origin=read(p);job=root/'jobs'/p.parent.name
            require(origin['original_job_complete_sha256']==sha(job/'complete.json'),'Recovered origin differs')
            plan=read(job/'plan.json')
            records,value=card_generation.assess(requests.transport.decoded_body((job/'response.body.raw').read_bytes(),
                read(job/'http.json')['headers'].get('content-encoding','')),read(job/'sdk-response.json'),
                plan['view'],plan['evidence'],plan['strategy'])
            require(records['protocol_validation']['status']==records['content_validation']['status']=='PASS'
                    and value==read(p.parent/'value.json'),'Recovered Card replay differs')
        completed=0
        for p in (root/'documents').glob('*/accepted-build.json'):
            ref=read(p);require(sha(p.parent/ref['directory']/'complete.json')==ref['sha256'],'Card source pointer differs');completed+=1
        return {'archive_integrity':'PASS','completed_sources':completed,'total_sources':len(config['sources']),
                'job_states':dict(states),'current_code_matches_run':code_identity()==config['code'],
                'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED','semantic_quality':'NOT_VALIDATED'}
    finally:sources.close()


def build_index(root, out, encoder, *, reuse_from=None):
    from . import index
    root=Path(root)
    items=({'id':c['card_id'],'source_id':c['source_id'],'text':v2.projection(c)} for c in iter_cards(root))
    identity=digest([sha(root/'prepared/complete.json'),
        [sha(p) for p in sorted((root/'documents').glob('*/accepted-build.json'))]])
    return index.build(out,items,encoder,kind='card',input_identity=identity,projection=v2.PROJECTION,reuse_from=reuse_from)
