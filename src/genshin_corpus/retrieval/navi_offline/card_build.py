"""Card-only batches; immutable per-source revisions reuse auditable successful jobs."""
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
import json

from . import cards, cards_v2 as v2, requests, metro_chat, card_generation, card_structure, anchors
from ... import model_calls
from .common import copy, digest, read, require, seal, sha, verify, write
from .sources import Sources, prepare_sources

VERSION = 'p05-card-build-1'


def prepare_snapshot(root, snapshot, *, total_request_limit, body_limit=8000, request_limit=100000, generation_strategy=card_generation.DEFAULT_STRATEGY, context_token_limit=None, source_ids=None, reading_policy=None, revision='initial', reason=''):
    """Re-use an immutable verified typed-source snapshot, never rewrite it."""
    root=Path(root);snapshot=Path(snapshot);verify(snapshot)
    require(not root.exists(),'New Card run root required')
    require(revision=='initial' or bool(reason.strip()),'Continuation revision requires an explicit reason')
    require(generation_strategy in card_generation.STRATEGIES,'Unknown Card strategy')
    require(type(total_request_limit) is int and total_request_limit>0,'Explicit finite request cap required')
    require(0<body_limit<=8000 and request_limit>=12000,'Invalid Card budgets')
    sources=Sources(snapshot)
    try:
        keys=list(sources.documents) if source_ids is None else list(source_ids)
        require(bool(keys) and len(set(keys))==len(keys) and set(keys)<=set(sources.documents),'Invalid snapshot source selection')
    finally:sources.close()
    for p in snapshot.rglob('*'):
        if p.is_file():copy(p,root/'prepared/sources'/p.relative_to(snapshot))
    config={'version':VERSION,'card_version':v2.VERSION,'prompt_sha256':digest(card_generation.prompt(generation_strategy)),
       'schema_sha256':digest(v2.SCHEMA),'projection':v2.PROJECTION,'reading_policy':v2.READING_POLICY,
       'route':'tokenmetro','api_surface':'chat_completions','base_url':requests.METRO_URL,'model':('deepseek-v4.1-flash-1017' if generation_strategy in card_generation.EVIDENCE_STRATEGIES else 'deepseek-v4.1-flash') if generation_strategy in {card_generation.DEEPSEEK,*card_generation.STRUCTURED_32K} else 'qwen3.8-flash',
       'generation_strategy':generation_strategy,'combine_sections':True,'total_request_limit':total_request_limit,
       'source_snapshot_sha256':sha(snapshot/'complete.json'),'revision':revision,'regeneration_reason':reason,
       'regenerate_jobs':[],'body_limit':body_limit,'request_limit':request_limit,'sources':keys,'code':code_identity(),
       'send_authorized':False,'scope':'CARDS_ONLY_NO_LINK_NO_EMBEDDING','navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
    # Short structural types first; the long source uses remaining bounded calls.
    sources=Sources(root/'prepared/sources')
    try:config['sources'].sort(key=lambda s:sum(len(q['text']) for q in v2.reading_plan(sources,s)['selected']))
    finally:sources.close()
    configure(config,generation_strategy,context_token_limit)
    if reading_policy is not None:
        require(reading_policy in {card_structure.POLICY,card_structure.BOUNDED_POLICY},'Unknown reading policy')
        config['reading_policy']=reading_policy
    bind_locations(root,config)
    write(root/'prepared/config.json',config)
    for p in Path(__file__).parent.glob('*.py'):copy(p,root/'prepared/execution-code'/p.name)
    model_calls.snapshot(root/'prepared/execution-code')
    seal(root/'prepared')
    return {'prepared_sha256':sha(root/'prepared/complete.json'),'sources':len(keys),'send_authorized':False}


def code_identity():
    return {p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))} | model_calls.code_identity()


def prepare(root, canonical, ru, source_ids=None, *, revision='initial', reason='', regenerate_jobs=(), body_limit=8000, request_limit=100000,
            generation_strategy=card_generation.DEFAULT_STRATEGY,total_request_limit=None,combine_sections=False,context_token_limit=None):
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
        config.update(generation_strategy=generation_strategy,api_surface='chat_completions',model=('deepseek-v4.1-flash-1017' if generation_strategy in card_generation.EVIDENCE_STRATEGIES else 'deepseek-v4.1-flash') if generation_strategy in {card_generation.DEEPSEEK,*card_generation.STRUCTURED_32K} else 'qwen3.8-flash',
                      prompt_sha256=digest(card_generation.prompt(generation_strategy)),total_request_limit=total_request_limit,
                      combine_sections=combine_sections)
    configure(config,generation_strategy,context_token_limit)
    bind_locations(root,config)
    write(prepared/'config.json',config)
    for p in Path(__file__).parent.glob('*.py'):copy(p,prepared/'execution-code'/p.name)
    model_calls.snapshot(prepared/'execution-code')
    seal(prepared)
    return {'prepared_sha256':sha(prepared/'complete.json'),'sources':len(selected),'send_authorized':False}


def run(root, *, backend='mock', max_new_requests=20, reuse=(), live_authorized=False,
        ack_plan=None, max_provider_tokens=None, api_key=None, call_policy=None):
    root=Path(root);verify(root/'prepared');config=read(root/'prepared/config.json')
    if config['code']!=code_identity():
        from .card_runtime import archived_run
        return archived_run(root,backend=backend,max_new_requests=max_new_requests,reuse=reuse,live_authorized=live_authorized,
            ack_plan=ack_plan,max_provider_tokens=max_provider_tokens,api_key=api_key,call_policy=call_policy)
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
    require(config.get('generation_strategy') not in card_generation.STRUCTURED_32K or policy.format_retries==0,
            '32k Cards use structural recovery, not identical format retries')
    require(backend!='live' or type(config.get('total_request_limit')) is int,'Live campaign requires explicit total_request_limit')
    budget_root=root/'model-call-budget' if config.get('total_request_limit') else root/'model-call-batches'/f'{len(list((root / "model-call-batches").glob("*"))):04d}'
    batch=model_calls.Batch(budget_root,max_requests=config.get('total_request_limit') or max_new_requests,max_tokens=max_provider_tokens,dispatch_limit=max_new_requests)
    execution={'call_policy':asdict(policy),'backend':backend,'max_provider_tokens':max_provider_tokens,'ack_plan':ack_plan,'live_authorized':live_authorized}
    if (root/'execution.json').exists():require(read(root/'execution.json')==execution,'Card resume mode/budget changed')
    else:write(root/'execution.json',execution)
    count=0; new_calls=0
    strategy=config.get('generation_strategy')
    semantic_cache={}
    def input_key(view,evidence):
        return digest([{k:v for k,v in view.items() if k not in {'build_revision','regeneration_reason'}},evidence])
    cache={};targets=set(config.get('regenerate_jobs',[]));target_inputs=set();found_targets=set()
    for other in reuse:
        other=Path(other);verify(other/'prepared')
        require(read(other/'execution.json')['backend']==backend,'Cannot reuse Mock as real Card output')
        for p in (other/'jobs').glob('*/plan.json'):
            plan=read(p)
            if strategy in card_generation.BOUNDED_STRATEGIES and (strategy!=card_generation.SEMANTIC or plan.get('strategy')==strategy) and (p.parent/'complete.json').exists() and read(p.parent/'result.json')['state']=='CONTENT_VERIFIED':
                semantic_cache.setdefault(input_key(plan['view'],plan['evidence']),p.parent)
            if p.parent.name in targets:
                base_view={k:v for k,v in plan['view'].items() if k not in {'build_revision','regeneration_reason'}}
                target_inputs.add(digest([base_view,plan['evidence']]))
                found_targets.add(p.parent.name)
            if (p.parent/'complete.json').exists() and (strategy or read(p.parent/'result.json')['state']=='CONTENT_VERIFIED'):
                cache.setdefault(read(p)['fingerprint'],p.parent)
    require(found_targets==targets,'Targeted regeneration jobs must exist in explicit reuse archives')
    sources=Sources(root/'prepared/sources')
    from .card_recovery import reading_scopes, reuse_reading_scope
    scope_cache=reading_scopes(reuse,config['sources']) if strategy in card_generation.BOUNDED_STRATEGIES and strategy!=card_generation.SEMANTIC else {}

    def raw_job(view,evidence):
        nonlocal count,new_calls
        targeted=not targets or digest([view,evidence]) in target_inputs
        view=view|{'build_revision':config['revision'] if targeted else 'initial',
                   'regeneration_reason':config['regeneration_reason'] if targeted else ''}
        if strategy in card_generation.STRUCTURED_32K:require(fits(view,config),'INPUT_CAPACITY_EXCEEDED')
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
            if strategy:metro_chat.execute(folder,view,evidence,live_authorized=live_authorized,api_key=api_key,strategy=strategy,call_policy=saved_policy,call_batch=batch,mock=backend=='mock',transport_override=mock_transport(view,strategy) if backend=='mock' else None)
            else:requests.execute(folder,'card',wire,view,evidence,backend=backend,mock_value=v2.mock(view) if backend=='mock' else None,live_authorized=live_authorized,api_key=api_key,route=route,call_policy=saved_policy,call_batch=batch)
            new_calls+=batch.accounting()['request_reservations']-before
        if folder.exists():
            require((folder/'complete.json').exists(),'Pending Card reservation: isolated, never reissue')
            verify(folder); result=read(folder/'result.json')
            plan=read(folder/'plan.json')
            require(all(plan[k]==v for k,v in {'stage':'card','wire':wire,'view':view,'evidence':evidence}.items()),'Cached Card input changed')
            if result['state']!='CONTENT_VERIFIED':raise ContentFailure(folder,result)
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
            # Imported jobs consume no new dispatch slots; Batch owns actual attempts.
            require(new_calls<max_new_requests,'BATCH_REQUEST_BUDGET_DEFERRED')
            remaining=min(max_new_requests-new_calls,batch.config['max_requests']-batch.accounting()['request_reservations'])
            require(remaining>0,'CAMPAIGN_REQUEST_BUDGET_DEFERRED')
            job_policy=policy
            count+=1
            if strategy:
                result=metro_chat.execute(folder,view,evidence,live_authorized=live_authorized,api_key=api_key,strategy=strategy,call_policy=job_policy,call_batch=batch,
                    mock=backend=='mock',transport_override=mock_transport(view,strategy) if backend=='mock' else None)
            else:
                result=requests.execute(folder,'card',wire,view,evidence,backend=backend,mock_value=v2.mock(view) if backend=='mock' else None,
                    live_authorized=live_authorized,api_key=api_key,route=route,call_policy=job_policy,call_batch=batch)
            new_calls+=result.get('calls',0)
            if result['state']!='CONTENT_VERIFIED':raise ContentFailure(folder,result)
            value=read(folder/'value.json')
        return {'job_id':jid,'value':value,'authority':result['authority']}

    def reused(view,evidence):
        prior=semantic_cache.get(input_key(view,evidence))
        if prior is None:return None
        from .card_recovery import import_job
        folder=import_job(root,prior,sources)
        return {'job_id':folder.name,'value':read(folder/'value.json'),'authority':read(folder/'result.json')['authority']}

    aggregations=[]
    def synthesis_view(title,children,scope):
        view=v2.synthesis_view(title,children,scope,preserve_locations=strategy in card_generation.EVIDENCE_STRATEGIES)
        if strategy==card_generation.SEMANTIC:
            from .card_semantics import synthesis_context
            view=synthesis_context(view,children)
        return view

    def make_card(*args,**kwargs):
        part=args[4]
        if part and 'recovery_evidence' in part:
            args=list(args);args[5]=part['recovery_evidence']
            kwargs['children']=part.get('recovery_children',[])
        card=v2.make_card(*args,**kwargs)
        if strategy in card_generation.EVIDENCE_STRATEGIES:
            if part:
                actual_strategy=read(root/'jobs'/part['job_id']/'plan.json')['strategy']
                card['quote_contract']=card_generation.quote_contract(actual_strategy)
            elif kwargs.get('children'):
                inherited_contracts={c.get('quote_contract') for c in kwargs['children']}
                card['quote_contract']=next(iter(inherited_contracts)) if len(inherited_contracts)==1 else 'MIXED_INHERITED'
            else:card['quote_contract']=card_generation.quote_contract(strategy)
            bound={q['id']:q for q in card['evidence']}
            for observation in card['source_observations']:
                for a in observation['anchors']:
                    q=bound[a['evidence_id']];anchors.verify_location(a,q)
                    require(sources.quote(q['unit_id'],a['ru_start'],a['ru_end'])['text']==a['quote'],
                            'Extracted anchor differs from verified RU')
        return card_structure.mark(card) if strategy in card_generation.STRUCTURED_32K else card

    def job(view,evidence,allow_reorganization=True):
        prior=reused(view,evidence)
        if prior is not None:return prior
        if strategy in card_generation.BOUNDED_STRATEGIES and view['mode']=='read':
            saved=reuse_reading_scope(root,view,evidence,scope_cache,sources,aggregations)
            if saved is not None:return saved
        if strategy in card_generation.BOUNDED_STRATEGIES and view['mode']=='read' and (sum(len(q['text']) for q in evidence)>4000 or not fits(view|{'build_revision':config['revision'],'regeneration_reason':config['regeneration_reason']},config)):
            groups=card_structure.split_quotes(sources,evidence);children=[]
            for index,qs in enumerate(groups):
                child_view=view|{'evidence':[v2.model_excerpt(q) for q in qs],
                    'scope':view.get('scope',{})|{'bounded_partition':digest([view,evidence]),'part':index}}
                part=job(child_view,qs,allow_reorganization)
                child=make_card(sources,qs[0]['source_id'],'reading_batch',view.get('section'),part,qs)
                children.append(child);aggregations.append(child)
            inherited=list({q['id']:q for c in children for q in c['evidence']}.values())
            part=job(synthesis_view(view['title'],children,view.get('scope',{})|{'bounded_partition':digest([view,evidence])}),inherited,allow_reorganization)
            return part|{'recovery_children':children,'recovery_evidence':inherited}
        try:return raw_job(view,evidence)
        except ContentFailure as exc:
            if not (strategy in card_generation.STRUCTURED_32K and allow_reorganization and
                    exc.result.get('http_status')==200 and model_calls.token_count(exc.result) is not None and
                    ((strategy in card_generation.BOUNDED_STRATEGIES and exc.result.get('card_failure') in
                        {'JSON_TRUNCATED','OUTPUT_TRUNCATED','VISIBLE_PLANNING_LEAK','OUTPUT_LIMIT_EXCEEDED','SOURCE_INVALID','SCHEMA_INVALID','JSON_INVALID'}) or
                     (exc.result.get('output_limit',{}).get('status')!='EXCEEDED' and exc.result.get('card_failure') in
                        {'JSON_TRUNCATED','OUTPUT_TRUNCATED','VISIBLE_PLANNING_LEAK'}))):raise
            decision={'version':card_structure.RECOVERY,'origin':exc.folder.name,
                'origin_sha256':sha(exc.folder/'complete.json'),'reason':exc.result['card_failure'],
                'view':view,'evidence':evidence,'recursive_reorganization':False}
            path=root/'reorganizations'/digest([view,evidence])
            if path.exists():verify(path);require(read(path/'decision.json')==decision,'Reorganization changed')
            else:write(path/'decision.json',decision);seal(path)
            source=evidence[0]['source_id'];children=[]
            if view['mode']=='read':
                groups=card_structure.split_quotes(sources,evidence)
                for index,qs in enumerate(groups):
                    child_view=view|{'evidence':[v2.model_excerpt(q) for q in qs],
                        'scope':view.get('scope',{})|{'reorganization_of':exc.folder.name,'part':index}}
                    part=job(child_view,qs,False)
                    child=make_card(sources,source,'reading_batch',view.get('section'),part,qs)
                    children.append(child);aggregations.append(child)
            else:
                original=view['children']
                if len(original)<=1 and strategy in card_generation.BOUNDED_STRATEGIES:
                    repair_view=view|{'scope':view.get('scope',{})|{'repair_of':exc.folder.name,'failure':exc.result['card_failure']}}
                    part=job(repair_view,evidence,False)
                    resolved=root/'reorganization-results'/path.name
                    result={'job_id':part['job_id'],'children':[c['card_id'] for c in original]}
                    if not resolved.exists():write(resolved/'result.json',result);seal(resolved)
                    else:verify(resolved);require(read(resolved/'result.json')==result,'Resolution changed')
                    return part
                require(len(original)>1,'Single-child synthesis needs targeted action')
                # Only model-visible child content is needed, but original evidence is retained.
                for index,group in enumerate((original[:len(original)//2],original[len(original)//2:])):
                    child_view=view|{'children':group,'scope':view.get('scope',{})|{'reorganization_of':exc.folder.name,'part':index}}
                    used={eid for c in group for field in cards.FIELDS for item in c[field] for eid in item['evidence_ids']}
                    qs=[q for q in evidence if q['id'] in used]
                    part=job(child_view,qs,False)
                    child=make_card(sources,source,'synthesis_batch',None,part,qs,children=group)
                    children.append(child);aggregations.append(child)
            inherited=list({q['id']:q for c in children for q in c['evidence']}.values())
            final_view=synthesis_view(view['title'],children,view.get('scope',{})|{'reorganization_of':exc.folder.name})
            part=job(final_view,inherited,False)
            resolved=root/'reorganization-results'/path.name
            result={'job_id':part['job_id'],'children':[c['card_id'] for c in children]}
            if not resolved.exists():write(resolved/'result.json',result);seal(resolved)
            else:verify(resolved);require(read(resolved/'result.json')==result,'Resolution changed')
            return part|{'recovery_children':children,'recovery_evidence':inherited}

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
        reuse_view=synthesis_view(sources.documents[source]['title'],children,scope)
        prior=reused(reuse_view,list(evidence.values()))
        if prior is not None:
            result=make_card(sources,source,level,section,prior,list(evidence.values()),children=children,plan=plan)
            aggregations.append(result);return result
        queue=list(children); groups=[];current=[]
        for c in queue:
            proposed=synthesis_view(sources.documents[source]['title'],current+[c],scope)
            bounded_ok=(strategy not in card_generation.BOUNDED_STRATEGIES or len(current+[c])==1 or
                (len(current+[c])<=4 and sum(len(x['source_observations']) for x in current+[c])<=48))
            if not bounded_ok or not fits(proposed,config):
                require(bool(current),'Single child exceeds synthesis budget; no silent semantic truncation')
                groups.append(current);current=[]
            current.append(c)
        if current:groups.append(current)
        if len(groups)>1:
            require(len(groups)<len(children) or strategy in card_generation.BOUNDED_STRATEGIES and
                any(len(c['source_observations'])>12 for c in children),'Synthesis cannot reduce within budget')
            children=[synth(source,g,'synthesis_batch',section,plan) for g in groups]
            return synth(source,children,level,section,plan)
        view=synthesis_view(sources.documents[source]['title'],children,scope)
        part=job(view,list(evidence.values()))
        result=make_card(sources,source,level,section,part,list(evidence.values()),children=children,plan=plan)
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
            if not complete.exists() and strategy in card_generation.BOUNDED_STRATEGIES and strategy!=card_generation.SEMANTIC:
                from .card_recovery import import_source
                import_source(root,source,reuse,sources)
            if complete.exists():
                ref=read(complete);target=base/ref['directory'];verify(target)
                require(sha(target/'complete.json')==ref['sha256'],'Card source pointer differs')
                outcomes.append({'source_id':source,'status':ref['status'],'directory':str(target.relative_to(root))});continue
            plan=(card_structure.plan(sources,source,group_modules=config.get('reading_policy')==card_structure.BOUNDED_POLICY, semantic_selection=config.get('generation_strategy')==card_generation.SEMANTIC) if strategy in card_generation.STRUCTURED_32K else v2.reading_plan(sources,source)); group=defaultdict(list); reading=[];failures=[];missing=[]
            try:
                planned=list(card_structure.batches(sources,source,plan,config['body_limit'],config['request_limit'],config.get('context_token_limit'),card_generation.EVIDENCE_32K if strategy in card_generation.BOUNDED_STRATEGIES else strategy) if strategy in card_generation.STRUCTURED_32K else v2.read_batches(sources,source,plan,config['body_limit'],config['request_limit'],config.get('combine_sections',False)))
            except ValueError as exc:
                planned=[];failures.append('READING_PLAN_INVALID: '+str(exc))
            for section,view,evidence in planned:
                try:
                    part=job(view,evidence)
                    c=make_card(sources,source,'reading_batch',section,part,evidence,plan=plan)
                    reading.append(c);group[section].append(c)
                except (ValueError,FileExistsError) as exc:
                    failures.append(str(exc))
                    missing.append({'section':section,'reason':str(exc),'status':'UNRESOLVED_REQUIRED_READING',
                        'ranges':[{'unit_id':q['unit_id'],'start':q['ru_start'],'end':q['ru_end']} for q in evidence]})
            final=[]
            try:
                require(not failures,'REQUIRED_READING_MISSING; document synthesis deferred')
                if not reading:
                    doc=make_card(sources,source,'document',None,None,[],plan=plan)
                elif set(group)=={None} and len(reading)==1:
                    doc=make_card(sources,source,'document',None,reading[0]['parts'][0],reading[0]['evidence'],plan=plan)
                else:
                    sections=[]
                    for section,cs in group.items():
                        if section is None:sections+=cs
                        elif len(cs)==1:sections.append(make_card(sources,source,'section',section,cs[0]['parts'][0],cs[0]['evidence'],plan=plan))
                        else:sections.append(synth(source,cs,'section',section,plan))
                    doc=synth(source,sections,'document',None,plan)
                    final += [c for c in sections if c['level']=='section' or strategy not in card_generation.STRUCTURED_32K]
                    for c in final:c['parent_id']=doc['card_id']
                doc['child_ids']=[c['card_id'] for c in final]
                for c in final:c['child_ids']=[]
                final.insert(0,doc)
                for parent in list(final):
                    for n,lead in enumerate(parent['open_questions']):
                        quotes=[q for q in parent['evidence'] if q['id'] in lead['evidence_ids']]
                        clue=make_card(sources,source,'clue',parent['section'],None,quotes,children=[parent],plan=plan)
                        clue.update(card_id=digest([parent['card_id'],'clue',n,lead]),parent_id=parent['card_id'],child_ids=[],
                                    overview=lead['text'],open_questions=[lead],queries=[lead],limitations=parent['limitations'])
                        if strategy in card_generation.STRUCTURED_32K:card_structure.mark(clue)
                        parent['child_ids'].append(clue['card_id']);final.append(clue)
            except (ValueError,FileExistsError) as exc:failures.append(str(exc))
            attempt=base/f'attempt-{len(list(base.glob("attempt-*"))):04d}'
            status='COMPLETE' if not failures else 'PARTIAL_REQUIRES_TARGETED_ACTION'
            write(attempt/'cards.json',{'cards':final,'reading_batches':reading,'synthesis_nodes':aggregations,
                                      'failures':failures,'missing_required_reading':missing,'status':status})
            write(attempt/'reading-plan.json',plan);seal(attempt)
            if not failures:write(complete,{'directory':attempt.name,'sha256':sha(attempt/'complete.json'),'status':status})
            outcomes.append({'source_id':source,'status':status,'directory':str(attempt.relative_to(root))})
        summary={'version':VERSION,'sources':outcomes,'new_sdk_calls':new_calls,'new_jobs':count,
                 'call_accounting':batch.accounting(),'backend':backend,'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED',
                 'semantic_quality':'NOT_VALIDATED','actual_fee':'UNKNOWN'}
        write(root/f'batch-{len(list(root.glob("batch-*.json"))):04d}.json',summary)
        return summary
    finally:sources.close()


EXPORT_POLICY='card-complete-sources-export-1'


def indexable(card):
    # Clues duplicate parent questions; temporary nodes are audit/recovery only.
    return (card['level'] in {'document','section','story'} and bool(card['parts'])
            and card.get('node_role','SEMANTIC_CARD')=='SEMANTIC_CARD')


def card_selection(root):
    root=Path(root);verify(root/'prepared')
    config=read(root/'prepared/config.json');selected=[];skipped=[]
    sources=Sources(root/'prepared/sources')
    try:
        for source in config['sources']:
            base=root/'documents'/digest(source);pointer=base/'accepted-build.json'
            record={'source_id':source,'status':'NOT_STARTED','reason':'SOURCE_NOT_COMPLETE','archive_identity':None}
            try:
                if not pointer.exists():
                    attempts=sorted(base.glob('attempt-*'))
                    if attempts:
                        latest=attempts[-1];record['directory']=str(latest.relative_to(root))
                        if (latest/'complete.json').exists():
                            record['archive_identity']=sha(latest/'complete.json');verify(latest)
                            partial=read(latest/'cards.json')
                            record.update(status=partial['status'],failures=partial['failures'])
                    skipped.append(record);continue
                ref=read(pointer)
                require(ref['directory'] not in {'.','..'} and Path(ref['directory']).name==ref['directory'],'Invalid source revision path')
                folder=base/ref['directory'];record.update(status='INVALID',directory=str(folder.relative_to(root)),
                    archive_identity=sha(pointer))
                verify(folder);require(sha(folder/'complete.json')==ref['sha256'],'Card revision pointer differs')
                value=read(folder/'cards.json')
                require(ref['status']==value['status']=='COMPLETE' and not value['failures'] and
                        not value.get('missing_required_reading'),'Source is not complete')
                if config.get('generation_strategy') in card_generation.EVIDENCE_STRATEGIES:
                    from .card_recovery import validate_dependencies
                    validate_dependencies(root,folder,source,sources)
                nodes=value['cards']+value.get('reading_batches',[])+value.get('synthesis_nodes',[])
                ids={c['card_id'] for c in nodes};d=sources.documents[source];verified_jobs=set()
                require(sum(c['level']=='document' for c in value['cards'])==1,'Complete source lacks unique document Card')
                for c in nodes:
                    require(c['source_id']==source and c['canonical_sha256']==d['canonical_sha256'] and
                            c['record_id']==d['record_id'],'Card source identity differs')
                    require(c.get('semantic_quality') not in {'FAIL','REJECTED','QUARANTINED'},'Card has known semantic rejection')
                    require(set(c['coverage'].get('model_child_ids',[]))<=ids,'Missing required child Card')
                    bound={q['id']:q for q in c['evidence']}
                    for q in bound.values():sources.verify_quote(q)
                    for o in c['source_observations']:
                        require({a['evidence_id'] for a in o['anchors']}==set(o['evidence_ids']),'Anchor/evidence references differ')
                        for a in o['anchors']:
                            require(a['evidence_id'] in bound,'Anchor source missing')
                            anchors.verify_location(a,bound[a['evidence_id']])
                    for part in c['parts']:
                        jid=part['job_id'];require(len(jid)==64 and all(ch in '0123456789abcdef' for ch in jid),'Invalid job identity')
                        job=root/'jobs'/jid
                        if jid not in verified_jobs:
                            verify(job);require(read(job/'result.json')['state']=='CONTENT_VERIFIED','Card depends on invalid job')
                            verified_jobs.add(jid)
                        require(part['value']==read(job/'value.json'),'Card part differs from accepted job')
                    if c['parts'] and c['level']!='clue':
                        require(c['overview']=='\n'.join(p['value']['overview'] for p in c['parts']),
                                'Card overview differs from accepted parts')
                        for field in cards.FIELDS:
                            require(c[field]==[x for p in c['parts'] for x in p['value'][field]],'Card semantic content differs from accepted parts')
                doc=next(c for c in value['cards'] if c['level']=='document')
                plan=read(folder/'reading-plan.json')
                require(not plan['selected'] or doc['parts'],'Complete document lacks accepted model output')
                ranges=doc['coverage']['supported_ru_ranges']
                for q in plan['selected']:
                    cursor=q['ru_start']
                    for start,end in sorted(ranges.get(q['unit_id'],[])):
                        if start<=cursor:cursor=max(cursor,end)
                    require(cursor>=q['ru_end'],'Required selected source range missing')
                count=sum(indexable(c) for c in value['cards'])
                if not count:
                    record.update(status='COMPLETE',reason='NO_ELIGIBLE_SEMANTIC_CARDS');skipped.append(record);continue
                selected.append({'source_id':source,'directory':str(folder.relative_to(root)),
                    'accepted_pointer_sha256':sha(pointer),'complete_sha256':sha(folder/'complete.json'),'card_count':count})
            except (ValueError,KeyError,TypeError,OSError) as exc:
                record.update(status='ISOLATED_INVALID_SOURCE',reason=type(exc).__name__+': '+str(exc))
                skipped.append(record)
    finally:sources.close()
    return {'version':EXPORT_POLICY,'prepared_sha256':sha(root/'prepared/complete.json'),
        'selected_sources':selected,'skipped_sources':skipped,'selected_source_count':len(selected),
        'skipped_source_count':len(skipped),'card_count':sum(s['card_count'] for s in selected),
        'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED','semantic_quality':'NOT_VALIDATED'}


def _iter_selected(root, selection):
    root=Path(root)
    for source in selection['selected_sources']:
        folder=root/source['directory'];verify(folder)
        require(sha(folder/'complete.json')==source['complete_sha256'] and
                sha(folder.parent/'accepted-build.json')==source['accepted_pointer_sha256'],'Selected source changed during export')
        for card in read(folder/'cards.json')['cards']:
            if indexable(card):yield card


def iter_cards(root):
    yield from _iter_selected(root,card_selection(root))


def audit(root):
    root=Path(root);verify(root/'prepared')
    config=read(root/'prepared/config.json')
    if config['code']!=code_identity():
        from .card_runtime import archived_audit
        return archived_audit(root)
    archived={p.name:sha(p) for p in (root/'prepared/execution-code').glob('*.py')}
    require(archived==config['code'],'Card code snapshot differs')
    if config.get('generation_strategy') in card_generation.EVIDENCE_STRATEGIES:
        require(sha(root/'prepared/quote-locations.json')==config['quote_locations_sha256'],'Prepared quote map differs')
    sources=Sources(root/'prepared/sources');states=defaultdict(int)
    try:
        for p in (root/'jobs').glob('*/plan.json'):
            if not (p.parent/'complete.json').exists():states['PENDING_ISOLATED']+=1;continue
            verify(p.parent);plan=read(p);result=read(p.parent/'result.json');states[result['state']]+=1
            strategy=plan.get('strategy')
            if strategy in card_generation.EVIDENCE_STRATEGIES:
                require(plan['quote_locations']==anchors.location_map(plan['view'],plan['evidence']),'Job quote map differs')
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
        for p in (root/'reorganizations').glob('*/decision.json'):
            verify(p.parent);decision=read(p);origin=root/'jobs'/decision['origin'];verify(origin)
            require(sha(origin/'complete.json')==decision['origin_sha256'],'Reorganization origin changed')
        for p in (root/'reorganization-results').glob('*/result.json'):
            verify(p.parent);require((root/'reorganizations'/p.parent.name/'decision.json').exists(),'Missing reorganization decision')
            job=root/'jobs'/read(p)['job_id'];verify(job)
            require(read(job/'result.json')['state']=='CONTENT_VERIFIED','Reorganization result invalid')
        completed=0
        for p in (root/'documents').glob('*/accepted-build.json'):
            ref=read(p);require(sha(p.parent/ref['directory']/'complete.json')==ref['sha256'],'Card source pointer differs');completed+=1
        selection=card_selection(root)
        require(selection['selected_source_count']==completed,'Completed Card dependencies or source coverage invalid')
        return {'archive_integrity':'PASS','document_integrity':'PASS','reference_integrity':'PASS','completed_sources':completed,'total_sources':len(config['sources']),
                'job_states':dict(states),'current_code_matches_run':code_identity()==config['code'],
                'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED','semantic_quality':'NOT_VALIDATED'}
    finally:sources.close()


def build_index(root, out, encoder, *, reuse_from=None):
    from . import index
    root=Path(root);out=Path(out);selection=card_selection(root);path=out/'card-selection.json'
    if path.exists():require(read(path)==selection,'Card export selection changed; use a new output directory')
    else:
        require(not out.exists() or not any(out.iterdir()),'New Card export directory required')
        write(path,selection)
    if not selection['card_count']:
        result={'status':'EMPTY_NO_ELIGIBLE_CARDS','card_export':selection}
        if (out/'complete.json').exists():verify(out)
        else:write(out/'export-result.json',result);seal(out)
        return result
    items=({'id':c['card_id'],'source_id':c['source_id'],'text':v2.projection(c)} for c in _iter_selected(root,selection))
    manifest=index.build(out,items,encoder,kind='card',input_identity=digest(selection),projection=v2.PROJECTION,reuse_from=reuse_from)
    return manifest|{'card_export':selection}


class ContentFailure(ValueError):
    def __init__(self,folder,result):
        self.folder=folder;self.result=result
        super().__init__('Card failed and isolated: '+folder.name+' '+str(result.get('card_failure',result['state'])))


def configure(config,strategy,context_token_limit=None):
    require(context_token_limit is None or type(context_token_limit) is int and context_token_limit>32000,
            "Context limit must reserve 32000 output tokens")
    if strategy in card_generation.STRUCTURED_32K:
        config.update(reading_policy=card_structure.BOUNDED_POLICY if strategy in card_generation.BOUNDED_STRATEGIES else card_structure.POLICY,reorganization_policy=card_structure.RECOVERY,
            combine_sections=False,output_cap=32000,read_timeout_seconds=900,context_token_limit=context_token_limit,
            context_capacity_status='NATIVE_CONTEXT_NOT_ATTESTED; optional explicit token limit uses conservative UTF8 proxy')
    if strategy==card_generation.SEMANTIC:
        from .card_semantics import POLICY, SELECTION
        config.update(semantic_policy=POLICY,narrative_selection_policy=SELECTION)
    if strategy in card_generation.EVIDENCE_STRATEGIES:
        from .card_contract import CONTRACT as bounded_contract
        config.update(quote_contract=card_generation.quote_contract(strategy),segment_policy=anchors.SEGMENT_POLICY,
            schema_sha256=digest(card_generation.schema(strategy,'read')),
            synthesis_schema_sha256=digest(card_generation.schema(strategy,'synthesis')))


def bind_locations(root,config):
    if config.get('generation_strategy') not in card_generation.EVIDENCE_STRATEGIES:return
    sources=Sources(Path(root)/'prepared/sources')
    try:
        maps={}
        for source in config['sources']:
            plan=card_structure.plan(sources,source,group_modules=config.get('reading_policy')==card_structure.BOUNDED_POLICY, semantic_selection=config.get('generation_strategy')==card_generation.SEMANTIC)
            maps[source]=[anchors.location_map(view,evidence) for _,view,evidence in card_structure.batches(
                sources,source,plan,config['body_limit'],config['request_limit'],config.get('context_token_limit'),config['generation_strategy'])]
        path=Path(root)/'prepared/quote-locations.json'
        write(path,{'quote_contract':config['quote_contract'],'sources':maps})
        config['quote_locations_sha256']=sha(path)
    finally:sources.close()


def fits(view,config):
    if config.get('generation_strategy') in card_generation.STRUCTURED_32K:
        return card_structure.capacity(view,config['request_limit'],config.get('context_token_limit'),config['generation_strategy'])
    return len(json.dumps(v2.wire(view),ensure_ascii=False))<=config['request_limit']


def mock_transport(view,strategy):
    import httpx
    obj={'id':'mock-card','object':'chat.completion','created':1,'model':card_generation.wire(view,strategy)['model'],
        'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(card_generation.mock(view,strategy),ensure_ascii=False)}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
    class Body(httpx.SyncByteStream):
        def __iter__(self):yield json.dumps(obj,ensure_ascii=False).encode('utf8')
    return httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'application/json'},stream=Body()))
