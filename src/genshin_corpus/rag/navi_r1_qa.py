"""Isolated R1-QA source-pair review; no production callers or active views."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import csv
import gzip
import json
import os
from pathlib import Path
import random
import re
import threading
import time

from . import navi_r1_experiment as r1

PARENT = Path('data/retrieval/p05-navi-r1-discovery-20261025-r3-max')
MODELS = ['qwen3.7-plus-2026-05-26', 'qwen3.8-max']
CODE = Path(__file__).read_bytes()
CODE_SHA = sha256(CODE).hexdigest()
PROMPT = '''你在审核《原神》两份真实资料的联读价值，不知道检索方法、排名、预期关系或旧判断。
只依据此次A/B正文。小说、童话、传说通常可以提供世界观补充、历史隐喻或平行叙事；不能因为它是文学就判无价值，也不能把解释直接等同于客观历史。
寻找具体人物特征、事件结构、特殊机制、意象组合与相互补充。泛泛共同主题、同名、配装推荐不足以判有剧情增量。
每个snippet都有已校验的ID。只选择ID，绝对不要重抄引文；程序会提取原文。所有verdict包括reject均应选择双方最相关的ID。
两方可能部分交付或只有攻略/编辑上下文，按coverage和body_policy记录不足，不编造未交付资料。标题是来源标识，不是身份关联的证据。不要借训练记忆补入文外事实。
输出严格JSON对象，不加围栏：
{"verdict":"useful|tentative|reject|insufficient", "relation":"direct_reference|background|contrast|literary_parallel|shared_mechanism|duplicate|gameplay_only|none", "claim_level":"textual|interpretive|speculative", "a_ids":["A001"], "b_ids":["B001"], "explanation":"双方具体对应及为何值得/不值得联读", "increment_a_from_b":"B对阅读A增加什么，若无则明确无", "increment_b_from_a":"A对阅读B增加什么", "counterevidence":"反解释、差异、遗漏及限制", "literary_value":false, "unsupported_identity_or_cause":false, "proposed_claim":"最强但不超出证据的关联表述"}。
useful=双方有具体支持且有实质新增联读价值，允许有明确锚点且谨慎表述的文学解释；tentative=值得追查但对应或增量仍不充分；reject=没有具体剧情增量/只是泛泛相似/明确反证；insufficient=此次证据不足判断。同文本重复可有版本价值，但不能充当独立历史证词。
interpretive不自动降为tentative；文学对应强弱由原文具体锚点决定。unsupported_identity_or_cause表示你发现需要警惕的未经支持身份/因果命题，不应因此否定所有较弱联读价值。'''


def once(path, value):
    r1.once(Path(path), value)


def load(path):
    return r1.read_json(Path(path))


def pair_key(a, b):
    return tuple(sorted((str(a), str(b)), key=int))


def pid(a, b):
    return sha256(('|'.join(pair_key(a, b))).encode()).hexdigest()[:20]


def chunks(text, maximum=440):
    """Partition exact character ranges; no normalization or invented text."""
    start = 0
    while start < len(text):
        end = min(len(text), start + maximum)
        if end < len(text):
            cuts = [m.end() + start for m in re.finditer(r'[\n。！？；]', text[start:end]) if m.end() >= 100]
            if cuts: end = cuts[-1]
        yield start, end, text[start:end]
        start = end


def chosen_body(record):
    scoped, excluded = r1.source_units(record)
    if scoped: return scoped, excluded, 'r1_selected_narrative_policy'
    # Explicit fallback supplies context, not invented narrative eligibility.
    units = []
    for section in record['sections']:
        for unit in section['units']:
            text = unit.get('value', {}).get('normalized_text', '')
            if unit['kind'] == 'rich_text' and text:
                units.append({'section': section['ordinal'], 'unit': unit['ordinal'],
                    'section_name': section['source_metadata'].get('name', ''), 'text': text})
    return units, excluded, 'fallback_rich_text_context_not_asserted_narrative'


def prepare(run):
    run.mkdir(parents=True, exist_ok=False)
    snapshot = run/'input-snapshot'
    snapshot.mkdir()
    for path in [Path('docs/current-phase.md'), Path('AGENTS.md'), Path('docs/research/phase-05/p05-navi-r1-discovery-20261025.md')]:
        (snapshot/path.name).write_bytes(path.read_bytes())
    source_files = ['rankings.json', 'quality-final.json', 'quality-continuation.json', 'quality.json',
        'scope-control-rankings.json', 'scope-controls-report.json', 'matched-body-control.json',
        'summary.json', 'supplemental-analysis.json', 'profiles.json', 'rewrites.json', 'stability.json',
        'corpus.json', 'external-reading-documents.json', 'final-audit.json', 'final-costs.json',
        'evaluator-only/reference-review.json', 'evaluator-only/reference-proposals.json', 'artifact-inventory.json']
    parent_hashes = {f:r1.file_hash(PARENT/f) for f in source_files}
    ranks = load(PARENT/'rankings.json')['sources']
    records = {}; discoveries = []; directional = set()
    def get(a, b):
        key = pair_key(a, b)
        return records.setdefault(key, {'pair_id':pid(*key), 'sources':list(key), 'discoveries':[], 'legacy':[]})
    def add(a, b, scope, method, rank):
        p = get(a, b); directional.add((a,b))
        item = {'source':a, 'target':b, 'scope':scope, 'method':method, 'rank':rank}
        p['discoveries'].append(item); discoveries.append(item|{'pair_id':p['pair_id']})
    for a, scopes in ranks.items():
        for scope, methods in scopes.items():
            for method, value in methods.items():
                for n, b in enumerate(value['documents'][:20], 1): add(a,b,scope,method,n)
    del ranks
    for filename, scope in [('scope-control-rankings.json','scope_control'), ('matched-body-control.json','matched_body')]:
        for a, methods in load(PARENT/filename)['sources'].items():
            for method, values in methods.items():
                for n, b in enumerate(values[:20],1): add(a,b,scope,method,n)
    for filename in ['quality.json','quality-continuation.json','quality-final.json']:
        for index, p in enumerate(load(PARENT/filename)['pairs']):
            if 'source' not in p: continue
            a,b = p['source'],p['target'];directional.add((a,b))
            get(a,b)['legacy'].append({'artifact':filename, 'row':index, 'source':a, 'target':b,
                'complete':p.get('complete',False), 'verdict':p.get('judgment',{}).get('verdict','unreviewed'),
                'state':p.get('state'), 'validation_error':p.get('validation_error'), 'reason':p.get('reason')})
    for index,p in enumerate(load(PARENT/'scope-controls-report.json')['additional_pairs']):
        if 'source' in p:
            a,b=p['source'],p['target'];get(a,b)['legacy'].append({'artifact':'scope-controls-report.json','row':index,
                'source':a,'target':b,'verdict':p.get('judgment',{}).get('verdict','unreviewed'),'complete':p.get('complete',False)})
    # Reference review is provenance only; it is never review-model input.
    for index,p in enumerate(load(PARENT/'evaluator-only/reference-review.json')['pairs']):
        if 'source' in p:get(p['source'],p['target'])['legacy'].append({'artifact':'evaluator-only/reference-review.json','row':index,
            'source':p['source'],'target':p['target'],'verdict':p.get('judgment',{}).get('verdict','unreviewed'),'complete':p.get('complete',False)})
    manifest, entries, read = r1.record_reader(Path.cwd())
    docs = {}; occurrences = {}
    for cid in sorted({s for key in records for s in key},key=int):
        record, entry = read(cid); body, excluded, policy = chosen_body(record)
        doc = {'cid':cid,'title':record['record_metadata']['name'],'record_id':record['record_id'],
            'canonical_path':entry['canonical_record_path'],'canonical_sha256':entry['canonical_record_sha256'],
            'body_policy':policy,'excluded':excluded,'units':[]}
        for unit in body:
            key=(cid,unit['section'],unit['unit']);occurrences[key]=unit
            doc['units'].append(unit)
        docs[cid]=doc
    rm = load(r1.RU); ru_path=Path(r1.RU).parent.parent/rm['artifacts']['retrieval_units']['path']
    if r1.file_hash(ru_path)!=rm['artifacts']['retrieval_units']['sha256']:raise ValueError('RU hash mismatch')
    ru_rows=defaultdict(list)
    with gzip.open(ru_path,'rt',encoding='utf-8') as handle:
        for line in handle:
            row=json.loads(line);a=row['source']['canonical_address']
            key=(a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal'])
            if key in occurrences and a['canonical_unit_kind']=='rich_text':ru_rows[key].append(row)
    snippets={};source_issues=[]
    for cid,doc in docs.items():
        passages=[];number=0
        for unit in doc['units']:
            rows=sorted(ru_rows[(cid,unit['section'],unit['unit'])],key=lambda r:r['fragment_selector'].get('fragment_index',0))
            joined=''.join(r['retrieval_visible_text'] for r in rows)
            if joined!=unit['text']:
                source_issues.append({'source':cid,'section':unit['section'],'unit':unit['unit'],'reason':'incomplete_ru_reconstruction'})
                unit['source_binding']='FAIL';continue
            unit['source_binding']='PASS';unit['official_unit_ids']=[r['unit_id'] for r in rows]
            ranges=[];offset=0
            for row in rows:
                ranges.append((offset,offset+len(row['retrieval_visible_text']),row['unit_id']));offset=ranges[-1][1]
            for start,end,text in chunks(unit['text']):
                number+=1;sid=f'{cid}:{number:04d}'
                snippet={'id':sid,'source':cid,'record_id':doc['record_id'],'canonical_sha256':doc['canonical_sha256'],
                    'section':unit['section'],'unit':unit['unit'],'section_name':unit['section_name'],
                    'start':start,'end':end,'text':text,'text_sha256':sha256(text.encode()).hexdigest(),
                    'official_unit_ids':[uid for lo,hi,uid in ranges if lo<end and hi>start]}
                passages.append(snippet);snippets[sid]=snippet
        doc['snippet_ids']=[p['id'] for p in passages]
    ledger=sorted(records.values(),key=lambda p:p['pair_id'])
    protocol={'version':'r1-qa-0.1','created_at':r1.now(),'parent_run':str(PARENT),'parent_hashes':parent_hashes,
        'models':MODELS,'prompt':PROMPT,'prompt_sha256':sha256(PROMPT.encode()).hexdigest(),
        'circuit':{'max_model_attempts':9000,'max_embedding_attempts':0,'max_tokens':120000000,
            'max_accounting_rmb':2000,'max_unknown_attempts':100,
            'rates_rmb_per_million':{'model':[15,80],'embedding':[5,0]}},
        'review_policy':'Both planned models review every recoverable unordered source pair; directions preserved. No legacy verdicts, methods, ranks or targets delivered.',
        'failure_policy':'No retry of UNKNOWN or issued-but-unresolved calls, including renamed jobs. Only positively proven pre-header failures allow up to 3 reconnects. Resolved invalid output remains failed, no automatic model repair.',
        'coverage':'Complete existing Top20 union, all repeat/query-only arms, scope/current-body controls, original 959 and extra/reference pairs. Not all corpus pairs.',
        'body_limit_per_side':24000,'workers':8,'human_semantic_acceptance':'NOT_REVIEWED',
        'selection_changes':'Source-pair direction-neutral orientation; same source body/selection for both models. Literary interpretation explicitly assessed without method labels.'}
    once(run/'protocol.json',protocol);once(run/'candidates.json',{'pairs':ledger,'directional_pairs':len(directional),'discoveries':len(discoveries)})
    once(run/'documents.json',{'documents':docs,'source_issues':source_issues});once(run/'snippets.json',snippets)
    once(run/'source-manifest.json',{'canonical_manifest_sha256':r1.file_hash(Path(r1.CANONICAL)),
        'ru_artifact_sha256':rm['artifacts']['retrieval_units']['sha256'],'sources':{cid:{k:d[k] for k in ['canonical_path','canonical_sha256','record_id']} for cid,d in docs.items()}})
    once(run/'scope-summary.json',{'unordered_pairs':len(ledger),'directional_pairs':len(directional),'discoveries':len(discoveries),
        'documents':len(docs),'snippets':len(snippets),'source_issues':source_issues,
        'body_policies':dict(Counter(d['body_policy'] for d in docs.values()))})
    print(json.dumps(load(run/'scope-summary.json'),ensure_ascii=False),flush=True)


def context_for(pair, docs, snippets, limit=24000):
    """Same source-only bounded payload for both judges, no method metadata."""
    from genshin_corpus.retrieval.lexical import analyze
    cids=list(pair['sources'])
    if int(pair['pair_id'][-2:],16)%2:cids.reverse()
    payload={};mapping={}
    for side,cid,other in [('A',cids[0],cids[1]),('B',cids[1],cids[0])]:
        d=docs[cid];available=[snippets[s] for s in d['snippet_ids']]
        full=sum(len(s['text']) for s in available)
        if full<=limit:chosen=available
        else:
            terms=Counter(analyze(' '.join(snippets[s]['text'] for s in docs[other]['snippet_ids'])))
            def score(s):
                tf=Counter(analyze(s['text']))
                return sum(min(n,tf.get(t,0)) for t,n in terms.items())/max(len(s['text']),1)**.5
            admitted=set();used=0
            for s in sorted(available,key=lambda s:(-score(s),s['id'])):
                if used+len(s['text'])<=limit:admitted.add(s['id']);used+=len(s['text'])
            chosen=[s for s in available if s['id'] in admitted]
        visible=[]
        for i,s in enumerate(chosen,1):
            alias=side+str(i).zfill(3);mapping[alias]=s['id']
            visible.append({'id':alias,'section':s['section_name'],'text':s['text']})
        payload[side]={'title':d['title'],'body_policy':d['body_policy'],'snippets':visible,
            'coverage':{'delivered_characters':sum(len(s['text']) for s in chosen),'verified_scoped_characters':full,
                'complete_verified_scope':len(chosen)==len(available),'whole_page_coverage':False,
                'omitted_snippet_count':len(available)-len(chosen)}}
    return payload,mapping,dict(zip(['A','B'],cids))


def validate(value, mapping, snippets):
    if not isinstance(value,dict):raise ValueError('not_object')
    evidence={}
    for side,key in [('A','a_ids'),('B','b_ids')]:
        ids=value.get(key)
        if not isinstance(ids,list) or not ids:raise ValueError('missing_ids_'+side)
        if any(not isinstance(i,str) or not i.startswith(side) or i not in mapping for i in ids):raise ValueError('invalid_id_'+side)
        evidence[side]=[snippets[mapping[i]] for i in dict.fromkeys(ids)]
        for s in evidence[side]:
            if not s['official_unit_ids'] or sha256(s['text'].encode()).hexdigest()!=s['text_sha256']:
                raise ValueError('source_integrity_'+side)
    errors=[]
    if value.get('verdict') not in {'useful','tentative','reject','insufficient'}:errors.append('invalid_verdict')
    if value.get('relation') not in {'direct_reference','background','contrast','literary_parallel','shared_mechanism','duplicate','gameplay_only','none'}:errors.append('invalid_relation')
    if value.get('claim_level') not in {'textual','interpretive','speculative'}:errors.append('invalid_claim_level')
    for key in ['explanation','increment_a_from_b','increment_b_from_a','counterevidence','proposed_claim']:
        if not isinstance(value.get(key),str):errors.append('missing_text_'+key)
    for key in ['literary_value','unsupported_identity_or_cause']:
        if not isinstance(value.get(key),bool):errors.append('missing_bool_'+key)
    return {'judgment':value,'evidence':evidence,'mechanical_quote_validation':'PASS','semantic_schema_errors':errors,
        'model_semantic_review':'REVIEWED_NOT_HUMAN_GOLD' if not errors else 'MODEL_OUTPUT_SCHEMA_INCOMPLETE',
        'human_semantic_acceptance':'NOT_REVIEWED'}


class QACampaign(r1.Campaign):
    def __init__(self,run,**kwargs):
        super().__init__(run,**kwargs)
        # Include this new campaign and unresolved reservations in quarantine.
        for root in self.run.parent.glob('p05-navi-r1-qa-*'):
            for issued in (root/'attempts').glob('*/issued.json'):
                terminal=issued.parent/'terminal.json'
                if not terminal.exists() or load(terminal).get('state')=='UNKNOWN':
                    request=issued.parent/'request.json'
                    if request.exists():self.quarantined[r1.request_fingerprint(load(request))]=str(issued.parent)
        if not kwargs.get('audit_only'):
            path=run/'qa-code-snapshots'/(CODE_SHA+'.py')
            if not path.exists():path.parent.mkdir(exist_ok=True);path.write_bytes(CODE)

    def reserve(self,*args,**kwargs):
        with self.lock:
            if self.unknown>=20 and self.unknown/max(len(self.reservations),1)>.05:
                raise r1.CircuitOpen('QA UNKNOWN ratio exceeds 5 percent')
            return super().reserve(*args,**kwargs)

    def review(self,job,payload,model):
        result=self.run/'responses'/(job+'.json')
        if result.exists():return load(result)
        # An issued reservation without a cached response is never redispatched.
        if list((self.run/'attempts').glob(job+'-connection-*')):
            raise r1.CircuitOpen('issued job without cached result; manual reconciliation required')
        from genshin_corpus.generation.generation import (
            BailianControlConfig,BailianOpenAICompatibleTransport,workspace_from_bailian_base_url,
            BASELINE_QWEN_MODEL_ID,EXACT_SNAPSHOT_POLICY,REQUESTED_ALIAS_POLICY)
        config=BailianControlConfig(region='cn-beijing',endpoint=self.env['BAILIAN_BASE_URL'],
            workspace=workspace_from_bailian_base_url(self.env['BAILIAN_BASE_URL']),model_id=model,
            model_reference_policy=EXACT_SNAPSHOT_POLICY if model==BASELINE_QWEN_MODEL_ID else REQUESTED_ALIAS_POLICY,
            max_output_tokens=2200,max_attempts=1,timeout_seconds=120)
        transport=BailianOpenAICompatibleTransport.from_environment(config,environment=self.env)
        wire=transport.wire_payload({'model':model,'messages':[{'role':'system','content':PROMPT},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
            'generation_parameters':{'enable_thinking':False,'temperature':0,'max_output_tokens':2200}})
        return self.qwen_traced(job,'qa',wire,transport.endpoint,config.api_key_env)


def review(run, limit=None):
    protocol=load(run/'protocol.json')
    if protocol['prompt']!=PROMPT:raise ValueError('frozen protocol mismatch')
    models=protocol['models']
    if models!=MODELS and models!=['deepseek-v4.1-flash']:raise ValueError('unapproved review model')
    pairs=all_candidates(run);docs,snippets=all_sources(run)
    campaign=DeepSeekCampaign(run) if models==['deepseek-v4.1-flash'] else QACampaign(run)
    tasks=[(p,m) for p in pairs for m in models]
    if limit is not None:tasks=tasks[:limit]
    stop=threading.Event();counts=Counter();count_lock=threading.Lock()
    def work(task):
        pair,model=task;tag='deepseek' if model=='deepseek-v4.1-flash' else ('plus' if model==MODELS[0] else 'max')
        job='review-'+pair['pair_id']+'-'+tag
        path=run/'reviews'/(job+'.json')
        if path.exists():
            previous=load(path)
            recovery=run/'reviews'/(job+'-presend-recovery.json')
            if recovery.exists():return load(recovery)
            if (previous.get('execution_state')=='NOT_SENT' and previous.get('reason')=='connection_warming_exhausted'
                and not list((run/'attempts').glob(job+'-connection-*'))):
                path=recovery
            else:return previous
        if stop.is_set():return {'not_started':True}
        payload,mapping,sides=context_for(pair,docs,snippets,protocol['body_limit_per_side'])
        envelope={'pair_id':pair['pair_id'],'model':model,'job':job,'qa_implementation_sha256':CODE_SHA,
            'sides':sides,'payload_sha256':sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'mechanical_quote_validation':'NOT_VERIFIED','model_semantic_review':'NOT_REVIEWED','human_semantic_acceptance':'NOT_REVIEWED'}
        if not mapping or not payload['A']['snippets'] or not payload['B']['snippets']:
            envelope['reason']='missing_verified_source_side';once(path,envelope);return envelope
        delivered=run/'deliveries'/(pair['pair_id']+'.json')
        if not delivered.exists():
            with campaign.lock:
                if not delivered.exists():once(delivered,{'payload':payload,'aliases':mapping,'sides':sides})
        try:result=campaign.review(job,payload,model)
        except r1.CircuitOpen as exc:
            stop.set();return envelope|{'not_started':True,'reason':str(exc)}
        envelope['execution_state']=result.get('state')
        if result.get('complete') and 'value' in result:
            try:envelope.update(validate(result['value'],mapping,snippets))
            except ValueError as exc:envelope['validation_error']=str(exc)
        else:envelope['reason']=result.get('error_type',result.get('parse_error','incomplete'))
        envelope['coverage']={side:payload[side]['coverage'] for side in ['A','B']}
        once(path,envelope)
        return envelope
    # Keep only a bounded number of futures queued so an open circuit does not
    # dispatch the remaining thousands of jobs. Completed evidence is durable.
    from concurrent.futures import wait,FIRST_COMPLETED
    iterator=iter(tasks);pending={};processed=0
    with ThreadPoolExecutor(max_workers=protocol['workers']) as pool:
        for _ in range(protocol['workers']):
            task=next(iterator,None)
            if task:pending[pool.submit(work,task)]=task
        while pending:
            done,_=wait(pending,timeout=30,return_when=FIRST_COMPLETED)
            if not done:
                print(json.dumps({'waiting':True,'processed':processed,'planned':len(tasks),'totals':campaign.totals()}),flush=True)
            for future in done:
                task=pending.pop(future)
                try:
                    result=future.result();counts[result.get('mechanical_quote_validation','NOT_VERIFIED')]+=1
                except Exception as exc:
                    stop.set();counts['local_error']+=1
                    error_path=run/'local-errors'/(task[0]['pair_id']+'-'+task[1]+'-'+CODE_SHA[:8]+'.json')
                    if not error_path.exists():once(error_path,{'pair_id':task[0]['pair_id'],'model':task[1],'error_type':type(exc).__name__,'error':str(exc)[:200]})
                processed+=1
                if processed%25==0:print(json.dumps({'processed':processed,'planned':len(tasks),'validation':dict(counts),'totals':campaign.totals()}),flush=True)
                if not stop.is_set():
                    task=next(iterator,None)
                    if task:pending[pool.submit(work,task)]=task
    report={'time':r1.now(),'planned':len(tasks),'processed':processed,'stopped':stop.is_set(),'counts':dict(counts),'totals':campaign.totals()}
    name='review-pass-'+str(len(list(run.glob('review-pass-*.json')))+1)+'.json';once(run/name,report)
    print(json.dumps(report),flush=True)


def canary(run):review(run,limit=8)


class DeepSeekCampaign(QACampaign):
    """Verified existing Responses SDK with persistent, traced network transport."""
    def review(self,job,payload,model):
        if model!='deepseek-v4.1-flash' or self.config.get('route')!='jizhi':raise ValueError('explicit jizhi DeepSeek only')
        result_path=self.run/'responses'/(job+'.json')
        if result_path.exists():return reconcile_response(self.run,job,load(result_path))
        if (self.run/'attempts'/job).exists():raise r1.CircuitOpen('issued DeepSeek job cannot be reissued')
        from .navi_e2_experiment import E2SDK
        from .navi_e2_routes import e2_route
        import httpx
        profile=e2_route(model,'jizhi',self.env)
        client=getattr(self.clients,'deepseek_client',None)
        if client is None:
            proxy=self.env.get('NAVI_R1_PROXY_URL')
            if proxy not in {None,'http://127.0.0.1:7890'}:raise ValueError('unverified proxy')
            client=httpx.Client(proxy=proxy,trust_env=False,follow_redirects=False,
                timeout=httpx.Timeout(180,connect=15),limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=120))
            warm=[];ready=False
            for index in range(3):
                try:
                    response=client.get(profile.base_url,timeout=httpx.Timeout(10,connect=5))
                    warm.append({'index':index,'status':response.status_code});ready=True;break
                except httpx.TransportError as exc:warm.append({'index':index,'error_type':type(exc).__name__})
            r1.record_warming(self.run,job,warm)
            if not ready:return {'state':'NOT_SENT','complete':False,'usage':{'input_tokens':0,'output_tokens':0},'error_type':'connection_warming_exhausted'}
            self.clients.deepseek_client=client
        wire={'model':model,'instructions':PROMPT,'input':json.dumps(payload,ensure_ascii=False),'stream':True,'max_output_tokens':8192}
        self.check_quarantine(wire)
        stem=self.reserve(job,'model',len(wire['instructions'])+len(wire['input']),8192)
        once(stem/'request.json',wire)
        traces=[]
        def forward(request):
            for number in range(1,4):
                events=[]
                def trace(event,info):events.append(event)
                request.extensions['trace']=trace
                try:
                    # Keep response streaming so the existing SDK persists every
                    # event and checks the final model/usage/terminal identity.
                    response=client.send(request,stream=True)
                    traces.append({'connection':number,'events':events,'outcome':'response_headers'})
                    return response
                except httpx.TransportError:
                    unsent=r1.traced_not_sent(events)
                    traces.append({'connection':number,'events':events,'outcome':'NOT_SENT' if unsent else 'UNKNOWN'})
                    if not unsent or number==3:raise
                    time.sleep(.5)
        sdk=E2SDK('jizhi',model=model,transport=httpx.MockTransport(forward),environment=self.env,
            max_attempts=1,mode='standard',format_retry_enabled=False)
        started=time.monotonic()
        try:
            terminal=sdk(stem/'sdk',wire,self.config['identity'],attempt_limit=1)
            raw_state=terminal['execution_state']
            state='NOT_SENT' if traces and all(t['outcome']=='NOT_SENT' for t in traces) else ('UNKNOWN' if raw_state=='EXECUTION UNKNOWN' else raw_state)
            usage=terminal.get('usage') if isinstance(terminal.get('usage'),dict) else None
            if state=='NOT_SENT':usage={'input_tokens':0,'output_tokens':0}
            result={'state':state,'complete':terminal['complete'],'usage':usage,'model':model,'route':'jizhi',
                'latency_ms':round((time.monotonic()-started)*1000,3),'raw_state':raw_state,
                'returned_models':terminal.get('returned_models'),'finish_reason':terminal.get('finish_reason')}
            if terminal['complete']:
                try:
                    value=json.loads(terminal['content'].strip())
                    if not isinstance(value,dict):raise ValueError('object required')
                    result['value']=value
                except ValueError:result['parse_error']='invalid_JSON'
            else:result['error_type']=(terminal.get('error') or {}).get('type','incomplete')
        except Exception as exc:
            result={'state':'UNKNOWN','complete':False,'usage':None,'error_type':type(exc).__name__}
        once(stem/'transport-traces.json',{'events':traces,'credential_info_not_persisted':True})
        self.resolve(stem,result);once(result_path,result)
        return reconcile_response(self.run,job,result)


def extract_response_text(events, model):
    """Permit absent/null empty metadata only; text and identities stay exact."""
    if not events or events[-1].get('type')!='response.completed':raise ValueError('missing_terminal')
    terminals=[e for e in events if e.get('type') in {'response.completed','response.failed','error'}]
    if len(terminals)!=1:raise ValueError('multiple_or_failed_terminal')
    response=terminals[0]['response']
    if response.get('status')!='completed' or response.get('model')!=model or response.get('error'):raise ValueError('terminal_identity')
    created=[e['response'] for e in events if e.get('type')=='response.created']
    if len(created)!=1 or created[0].get('id')!=response.get('id'):raise ValueError('response_id_conflict')
    messages=response.get('output',[])
    if len(messages)!=1 or messages[0].get('type')!='message':raise ValueError('ambiguous_output')
    message=messages[0];done=[e['item'] for e in events if e.get('type')=='response.output_item.done']
    if len(done)!=1:raise ValueError('ambiguous_item_done')
    def normalized(value):
        if isinstance(value,dict):
            return {k:normalized(v) for k,v in value.items() if not (k in {'phase','annotations','logprobs'} and v in (None,[]))}
        if isinstance(value,list):return [normalized(v) for v in value]
        return value
    if normalized(done[0])!=normalized(message):raise ValueError('nonempty_metadata_or_content_conflict')
    if message.get('role')!='assistant' or message.get('status')!='completed' or message.get('phase') not in {None,'final_answer'}:raise ValueError('message_identity')
    contents=message.get('content',[])
    if len(contents)!=1 or contents[0].get('type')!='output_text':raise ValueError('ambiguous_content')
    text=contents[0].get('text')
    if not isinstance(text,str) or not text.strip():raise ValueError('empty_text')
    deltas=[];text_done=[];part_done=[]
    for event in events:
        kind=event.get('type')
        if kind in {'response.output_text.delta','response.output_text.done','response.content_part.done'}:
            if event.get('item_id')!=message['id'] or event.get('output_index')!=0 or event.get('content_index')!=0:
                raise ValueError('stream_item_index_conflict')
            if kind.endswith('.delta'):deltas.append(event['delta'])
            elif kind=='response.output_text.done':text_done.append(event['text'])
            else:part_done.append(event['part']['text'])
    if text_done!=[text] or part_done!=[text] or ''.join(deltas)!=text:raise ValueError('stream_terminal_text_conflict')
    usage=response.get('usage') or {}
    if not all(isinstance(usage.get(k),int) and usage[k]>=0 for k in ['input_tokens','output_tokens','total_tokens']):raise ValueError('missing_usage')
    if usage['input_tokens']+usage['output_tokens']!=usage['total_tokens']:raise ValueError('usage_sum_conflict')
    return text,usage


def reconcile_response(run,job,result):
    if result.get('complete') or result.get('state')!='RESOLVED' or result.get('error_type')!='ResponsesDecisionTextError':return result
    output=run/'response-reconciliations'/(job+'.json')
    if output.exists():return load(output)['effective_result']
    terminal_path=run/'attempts'/job/'sdk/attempts/01/terminal.json'
    terminal=load(terminal_path)
    descriptor=terminal['stream'];archive=run/'attempts'/descriptor['path']
    if r1.file_hash(archive)!=descriptor['sha256']:raise ValueError('archived_stream_hash')
    raw=gzip.decompress(archive.read_bytes()) if descriptor['format']=='jsonl-gzip-1' else archive.read_bytes()
    if sha256(raw).hexdigest()!=descriptor.get('uncompressed_sha256',descriptor['sha256']):raise ValueError('raw_stream_hash')
    events=[json.loads(line) for line in raw.splitlines()]
    if len(events)!=descriptor['chunk_count']:raise ValueError('stream_count')
    try:
        text,usage=extract_response_text(events,result['model'])
        if text!=terminal.get('content') or usage.get('input_tokens')!=result['usage'].get('input_tokens') or usage.get('output_tokens')!=result['usage'].get('output_tokens'):
            raise ValueError('sdk_text_or_usage_conflict')
        value=json.loads(text)
        if not isinstance(value,dict):raise ValueError('review_object')
    except ValueError as exc:
        once(output,{'job':job,'mechanical_envelope_reconciliation':'FAIL','reason':str(exc),'effective_result':result})
        return result
    effective=result|{'complete':True,'value':value,'original_parser_complete':False,
        'envelope_reconciliation':'EXACT_TEXT_AND_IDENTITIES_MATCH_ONLY_EMPTY_METADATA_DIFFERS'}
    once(output,{'job':job,'original_result_sha256':r1.file_hash(run/'responses'/(job+'.json')),
        'mechanical_envelope_reconciliation':'PASS','text_sha256':sha256(text.encode()).hexdigest(),
        'stream_sha256':descriptor['sha256'],'paid_request_reissued':False,'effective_result':effective})
    return effective


def decode_review_json(text):
    """Decode one complete object, allowing only a whole JSON code fence."""
    text=text.strip();wrapped=False
    match=re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*)\r?\n```',text,flags=re.S)
    if match:text=match.group(1);wrapped=True
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('review_object_required')
    return value,wrapped


def effective_response(run,job,result):
    if result.get('state')!='RESOLVED' or (result.get('complete') and isinstance(result.get('value'),dict)):return result
    # This second projection fixes complete whole-code-fence packaging. It never
    # repairs punctuation, truncation or model prose, and preserves prior FAILs.
    output=run/'response-reconciliations-v2'/(job+'.json')
    if output.exists():return load(output)['effective_result']
    terminal_path=run/'attempts'/job/'sdk/attempts/01/terminal.json'
    if not terminal_path.exists():return result
    terminal=load(terminal_path)
    if not terminal.get('complete') and (terminal.get('error') or {}).get('type')!='ResponsesDecisionTextError':return result
    descriptor=terminal['stream'];archive=run/'attempts'/descriptor['path']
    if r1.file_hash(archive)!=descriptor['sha256']:raise ValueError('stream_archive_hash')
    raw=gzip.decompress(archive.read_bytes()) if descriptor['format']=='jsonl-gzip-1' else archive.read_bytes()
    if sha256(raw).hexdigest()!=descriptor.get('uncompressed_sha256',descriptor['sha256']):raise ValueError('stream_raw_hash')
    try:
        text,usage=extract_response_text([json.loads(line) for line in raw.splitlines()],result['model'])
        if text!=terminal['content']:raise ValueError('terminal_content_conflict')
        value,wrapped=decode_review_json(text)
    except ValueError as exc:
        once(output,{'job':job,'status':'FAIL','reason':str(exc),'effective_result':result});return result
    effective=result|{'complete':True,'value':value,'json_code_fence_removed':wrapped,
        'envelope_reconciliation':'EXACT_STREAM_TEXT_WITH_COMPLETE_JSON_ENVELOPE'}
    once(output,{'job':job,'status':'PASS','original_result_sha256':r1.file_hash(run/'responses'/(job+'.json')),
        'raw_text_sha256':sha256(text.encode()).hexdigest(),'stream_sha256':descriptor['sha256'],
        'paid_request_reissued':False,'json_code_fence_removed':wrapped,'effective_result':effective})
    return effective


def fork_deepseek(run):
    first=Path('data/retrieval/p05-navi-r1-qa-20261025-r1')
    run.mkdir(parents=True,exist_ok=False)
    for name in ['documents.json','snippets.json','source-manifest.json']:(run/name).write_bytes((first/name).read_bytes())
    original=load(first/'candidates.json');pairs={p['pair_id']:p for p in original['pairs']}
    stability=load(PARENT/'stability.json')
    def get(a,b):
        key=pid(a,b)
        return pairs.setdefault(key,{'pair_id':key,'sources':list(pair_key(a,b)),'discoveries':[],'legacy':[]})
    repeat_records=arrival_records=0
    for a,methods in stability['profile_repeat_rankings'].items():
        for method,value in methods.items():
            for rank,b in enumerate(value['documents'][:20],1):
                get(a,b)['discoveries'].append({'source':a,'target':b,'scope':'profile_repeat','method':method,'rank':rank,
                    'artifact':'stability.json/profile_repeat_rankings'});repeat_records+=1
    for order,value in stability['arrival'].items():
        for a,b in value['edges']:
            get(a,b)['discoveries'].append({'sources':[a,b],'scope':'arrival','method':order,'rank':None,
                'undirected':True,'artifact':'stability.json/arrival'});arrival_records+=1
    ledger=sorted(pairs.values(),key=lambda p:p['pair_id'])
    protocol=load(first/'protocol.json')
    protocol.update(created_at=r1.now(),version='r1-qa-deepseek-0.2',models=['deepseek-v4.1-flash'],route='jizhi',workers=16,
        identity=sha256(('R1-QA-DeepSeek-'+r1.now()).encode()).hexdigest(),previous_qa_run=str(first),
        provider_change='Operator explicitly exhausted Aliyun quota and requested jizhi DeepSeek-v4.1-Flash. No new Aliyun calls. Uniform DeepSeek review on all pairs; earlier partial Qwen is supplemental, not a second complete judge.',
        coverage='Original complete Top20 union plus all profile-repeat Top20 and all preserved undirected arrival-simulation edges. Arrival edges are separately evaluated, not assigned invented retrieval ranks.',
        review_policy='One uniform DeepSeek reviewer for every recoverable pair; source-ID prompt unchanged, all original context/quote checks. Model review is not human gold.')
    once(run/'protocol.json',protocol)
    once(run/'candidates.json',{'pairs':ledger,'original_directional_pairs':original['directional_pairs'],
        'original_discoveries':original['discoveries'],'profile_repeat_discoveries':repeat_records,'arrival_discoveries':arrival_records})
    once(run/'provider-transition.json',{'operator_instruction':'Use jizhi deepseek-v4.1-flash after Aliyun quota exhaustion',
        'old_campaign':str(first),'old_campaign_switch_audit_sha256':r1.file_hash(first/'operator-provider-switch.json'),
        'initial_pairs':len(original['pairs']),'extended_pairs':len(ledger),'reason_for_extension':'Ledger audit found 20 additional profile-repeat pairs and 3166 additional arrival pairs. Included for complete accounting and review; comparative ranked scope remains unchanged.',
        'copy_hashes':{name:r1.file_hash(first/name) for name in ['documents.json','snippets.json','source-manifest.json']},
        'joint_qa_accounting_envelope_rmb':4000,'per_campaign_cap_rmb':2000})
    print(json.dumps({'deepseek_pairs':len(ledger),'profile_repeat_records':repeat_records,'arrival_records':arrival_records,'models':protocol['models']}),flush=True)


def deepseek_canary(run):review(run,limit=3)


def all_sources(run):
    docs=load(run/'documents.json')['documents'];snippets=load(run/'snippets.json')
    for path in sorted(run.glob('source-extension-*.json')):
        extra=load(path)
        if docs.keys()&extra['documents'].keys() or snippets.keys()&extra['snippets'].keys():raise ValueError('source extension collision')
        docs.update(extra['documents']);snippets.update(extra['snippets'])
    return docs,snippets


def all_candidates(run):
    pairs={p['pair_id']:p for p in load(run/'candidates.json')['pairs']}
    for path in sorted(run.glob('candidate-extension-*.json')):
        extra=load(path)
        for p in extra['additional_pairs']:
            if p['pair_id'] in pairs:raise ValueError('candidate extension collision')
            pairs[p['pair_id']]=p
        for pairid,events in extra['pair_updates'].items():pairs[pairid]['discoveries'].extend(events)
    return sorted(pairs.values(),key=lambda p:p['pair_id'])


def bridge_extension(run):
    existing={p['pair_id']:p for p in all_candidates(run)};additional={};updates=defaultdict(list);chains=[]
    rankings=load(PARENT/'rankings.json')['sources']
    for a,scopes in rankings.items():
        for scope,methods in scopes.items():
            bridge=methods['narrative_bridge']
            for path_index,path in enumerate(bridge.get('paths',[])):
                mid=path['intermediate']
                for rank,target in enumerate(bridge['documents'][:20],1):
                    if target not in path['documents']:continue
                    chain={'scope':scope,'source':a,'intermediate':mid,'target':target,'final_rank':rank,'path_index':path_index,
                        'edge1':pid(a,mid),'edge2':pid(mid,target),'direct_pair':pid(a,target),
                        'attribution':'Stored path membership, not proof that this path caused the final merged rank.'}
                    chains.append(chain)
                    for source,dest,edge in [(a,mid,'edge1'),(mid,target,'edge2')]:
                        pairid=pid(source,dest)
                        event={'scope':'bridge_support','method':edge,'support_only':True,'rank':None,
                            'source':source,'target':dest,'chain_source':a,'chain_target':target,'path_index':path_index}
                        if pairid in existing:updates[pairid].append(event)
                        else:
                            p=additional.setdefault(pairid,{'pair_id':pairid,'sources':list(pair_key(source,dest)),'discoveries':[],'legacy':[]})
                            p['discoveries'].append(event)
    once(run/'candidate-extension-01.json',{'reason':'Review both stored bridge edges for every Top20 path trace, without inferring transitive identity or causes.',
        'additional_pairs':list(additional.values()),'pair_updates':dict(updates),'chain_traces':chains})
    print(json.dumps({'additional_bridge_support_pairs':len(additional),'chain_traces':len(chains),'final_pair_union':len(existing)+len(additional)}),flush=True)


def extend_sources(run):
    docs,_=all_sources(run);pairs=load(run/'candidates.json')['pairs']
    missing={cid for p in pairs for cid in p['sources']}-docs.keys()
    _,_,read=r1.record_reader(Path.cwd());new_docs={};wanted={}
    for cid in sorted(missing,key=int):
        record,entry=read(cid);units,excluded,policy=chosen_body(record)
        new_docs[cid]={'cid':cid,'title':record['record_metadata']['name'],'record_id':record['record_id'],
            'canonical_path':entry['canonical_record_path'],'canonical_sha256':entry['canonical_record_sha256'],
            'body_policy':policy,'excluded':excluded,'units':units}
        for u in units:wanted[(cid,u['section'],u['unit'])]=u
    rm=load(r1.RU);path=Path(r1.RU).parent.parent/rm['artifacts']['retrieval_units']['path']
    if r1.file_hash(path)!=rm['artifacts']['retrieval_units']['sha256']:raise ValueError('RU source changed')
    rows=defaultdict(list)
    with gzip.open(path,'rt',encoding='utf-8') as handle:
        for line in handle:
            row=json.loads(line);a=row['source']['canonical_address'];key=(a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal'])
            if key in wanted and a['canonical_unit_kind']=='rich_text':rows[key].append(row)
    extra_snippets={};issues=[]
    for cid,doc in new_docs.items():
        ids=[];number=0
        for u in doc['units']:
            rr=sorted(rows[(cid,u['section'],u['unit'])],key=lambda x:x['fragment_selector'].get('fragment_index',0))
            if ''.join(r['retrieval_visible_text'] for r in rr)!=u['text']:
                issues.append({'source':cid,'section':u['section'],'unit':u['unit']});continue
            ranges=[];offset=0
            for r in rr:ranges.append((offset,offset+len(r['retrieval_visible_text']),r['unit_id']));offset=ranges[-1][1]
            u['source_binding']='PASS';u['official_unit_ids']=[r['unit_id'] for r in rr]
            for start,end,text in chunks(u['text']):
                number+=1;sid=f'{cid}:{number:04d}';ids.append(sid)
                extra_snippets[sid]={'id':sid,'source':cid,'record_id':doc['record_id'],'canonical_sha256':doc['canonical_sha256'],
                    'section':u['section'],'unit':u['unit'],'section_name':u['section_name'],'start':start,'end':end,
                    'text':text,'text_sha256':sha256(text.encode()).hexdigest(),'official_unit_ids':[uid for lo,hi,uid in ranges if lo<end and hi>start]}
        doc['snippet_ids']=ids
    once(run/('source-extension-'+str(len(list(run.glob('source-extension-*.json')))+1).zfill(2)+'.json'),
        {'reason':'Arrival/repeat union contains previously unranked distractor sources; source verification only, no new generation/index.',
            'documents':new_docs,'snippets':extra_snippets,'source_issues':issues,'ru_artifact_sha256':rm['artifacts']['retrieval_units']['sha256']})
    print(json.dumps({'additional_sources':len(new_docs),'additional_snippets':len(extra_snippets),'source_issues':issues}),flush=True)


def quote_diagnosis(quote, refs, payload):
    """Diagnostic matching only; never rewrites or semantically accepts quotes."""
    if not isinstance(quote,str) or not quote:return {'category':'missing_quote'}
    segments=payload.get('segments',[]);byid={s['id']:s['text'] for s in segments}
    cited={i:t for i,t in byid.items() if i in refs}
    if not cited:return {'category':'missing_or_invalid_refs'}
    for sid,text in cited.items():
        at=text.find(quote)
        if at>=0:return {'category':'exact_cited','segment':sid,'start':at,'end':at+len(quote)}
    for sid,text in byid.items():
        at=text.find(quote)
        if at>=0:return {'category':'exact_other_delivered_segment','segment':sid,'start':at,'end':at+len(quote)}
    def compact(value, punctuation=False):
        import unicodedata
        keep=[i for i,c in enumerate(value) if not c.isspace() and not (punctuation and unicodedata.category(c).startswith('P'))]
        return ''.join(value[i] for i in keep),keep
    for punctuation,label in [(False,'whitespace_difference'),(True,'punctuation_or_whitespace_difference')]:
        needle,_=compact(quote,punctuation)
        if len(needle)<6:continue
        hits=[]
        for sid,text in cited.items():
            hay,index=compact(text,punctuation);start=0
            while True:
                at=hay.find(needle,start)
                if at<0:break
                lo,hi=index[at],index[at+len(needle)-1]+1
                hits.append({'segment':sid,'start':lo,'end':hi,'exact_source_text':text[lo:hi]});start=at+1
        if hits:return {'category':label,'candidate_spans':hits,'unique':len(hits)==1,
            'status':'DIAGNOSTIC_SOURCE_SPAN_ONLY_NOT_SEMANTIC_ACCEPTANCE'}
    # Treat omitted runs as a diagnosis, never stitch them into one quote.
    pieces=[x.strip() for x in re.split(r'[…⋯]+|\.{2,}|\n',quote) if len(x.strip())>=6]
    if len(pieces)>=2:
        texts='\n'.join(cited.values())
        if all(x in texts for x in pieces):return {'category':'multiple_source_pieces_not_one_contiguous_quote'}
    if '\\n' in quote and any(quote.replace('\\n','\n') in t for t in cited.values()):return {'category':'literal_escape_difference'}
    return {'category':'not_located_by_conservative_diagnostics','note':'May be paraphrase, addition, truncation, unsupported text or another mismatch; no semantic conclusion.'}


def legacy_audit(run):
    output=[];orphans=[];counts=Counter();old=load(PARENT/'quality-final.json')['pairs']
    for row,p in enumerate(old):
        a,b=p['source'],p['target'];job=f'quality-{a}-{b}'
        response=PARENT/'responses'/(job+'.json')
        result=load(response) if response.exists() else {}
        value=p.get('judgment',result.get('value',{}))
        payload=p.get('delivered',{})
        if not payload and (PARENT/'attempts').exists():
            requests=list((PARENT/'attempts').glob(job+'-connection-*/request.json'))
            if requests:
                wire=load(requests[0]);payload=json.loads(next(m['content'] for m in wire['messages'] if m['role']=='user'))
        diagnosis={}
        for side,prefix in [('A','a'),('B','b')]:
            diagnosis[side]=quote_diagnosis(value.get(prefix+'_quote'),value.get(prefix+'_refs',[]),payload.get(side,{}))
            counts[diagnosis[side]['category']]+=1
        output.append({'pair_id':pid(a,b),'source':a,'target':b,'old_row':row,'old_complete':p.get('complete',False),
            'old_verdict':p.get('judgment',{}).get('verdict','unreviewed'),'raw_model_verdict':value.get('verdict'),
            'old_validation_error':p.get('validation_error'),'old_execution_state':p.get('state'),'diagnosis':diagnosis,
            'human_semantic_acceptance':'NOT_REVIEWED','old_judgment_not_promoted':True})
    for filename in ['quality.json','quality-continuation.json','quality-final.json']:
        for index,p in enumerate(load(PARENT/filename)['pairs']):
            if 'source' not in p:orphans.append({'artifact':filename,'row':index,'original_record':p,
                'status':'ROW_HAS_NO_SOURCE_IDS_PRESERVED_NOT_GUESSED','membership_note':'Final 959-pair ledger is authoritative for pair membership; this row alone cannot identify its source pair.'})
    inventory=load(PARENT/'artifact-inventory.json');failures=[]
    for item in inventory['files']:
        path=PARENT/item['path']
        if not path.exists() or r1.file_hash(path)!=item['sha256']:failures.append(item['path'])
    current_parent={name:r1.file_hash(PARENT/name) for name in load(run/'protocol.json')['parent_hashes']}
    if current_parent!=load(run/'protocol.json')['parent_hashes']:raise ValueError('R1 parent changed during QA')
    once(run/'legacy-quote-audit.json',{'pairs':output,'diagnostic_endpoint_counts':dict(counts),
        'unassigned_historical_rows':orphans,'historical_inventory_files_checked':len(inventory['files']),
        'historical_hash_failures':failures,'automatic_semantic_promotions':0,
        'caveat':'Diagnostic compact matching is not a repaired model quotation or correctness proof. Any candidate original span remains independently reviewable.'})
    with (run/'legacy-dispositions.csv').open('x',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['pair_id','source','target','old_verdict','raw_model_verdict','old_complete','old_validation_error','A_diagnosis','B_diagnosis'])
        writer.writeheader()
        for p in output:writer.writerow({k:p.get(k) for k in writer.fieldnames if k not in {'A_diagnosis','B_diagnosis'}}|
            {'A_diagnosis':p['diagnosis']['A']['category'],'B_diagnosis':p['diagnosis']['B']['category']})
    print(json.dumps({'old_pairs':len(output),'diagnosis':dict(counts),'orphan_rows':len(orphans),'parent_hash_failures':len(failures)}),flush=True)


def model_tag(model):
    return 'deepseek' if model=='deepseek-v4.1-flash' else ('plus' if model==MODELS[0] else 'max')


def project_one(root,pair,model,snippets):
    job='review-'+pair['pair_id']+'-'+model_tag(model)
    original=root/'reviews'/(job+'.json');recovered=root/'reviews'/(job+'-presend-recovery.json')
    envelope=load(recovered if recovered.exists() else original) if original.exists() or recovered.exists() else {}
    response=root/'responses'/(job+'.json')
    result=load(response) if response.exists() else {}
    if model=='deepseek-v4.1-flash' and result:result=reconcile_response(root,job,result)
    if model=='deepseek-v4.1-flash' and result:result=effective_response(root,job,result)
    if not result:
        terminals=list((root/'attempts').glob(job+'*/terminal.json'))
        if terminals:result=load(sorted(terminals)[-1])
    projection={'model':model,'job':job,'cohort':str(root),'execution_state':result.get('state',envelope.get('execution_state','NOT_DISPATCHED')),
        'mechanical_quote_validation':'NOT_VERIFIED','model_semantic_review':'NOT_REVIEWED','human_semantic_acceptance':'NOT_REVIEWED',
        'original_mechanical_status':envelope.get('mechanical_quote_validation'),
        'raw_verdict':result.get('value',{}).get('verdict') if isinstance(result.get('value'),dict) else None}
    if result.get('complete') and 'value' in result:
        delivery_path=root/'deliveries'/(pair['pair_id']+'.json')
        if not delivery_path.exists():return projection|{'reason':'delivery_missing'}
        delivered=load(delivery_path)
        try:
            projection.update(validate(result['value'],delivered['aliases'],snippets))
            for side,evidence in projection['evidence'].items():
                if any(s['source']!=delivered['sides'][side] for s in evidence):raise ValueError('endpoint_source_mismatch')
            projection.update(sides=delivered['sides'],coverage={s:delivered['payload'][s]['coverage'] for s in ['A','B']},
                payload_sha256=sha256(json.dumps(delivered['payload'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                delivery_path=str(delivery_path))
        except ValueError as exc:
            projection.update(mechanical_quote_validation='NOT_VERIFIED',model_semantic_review='NOT_REVIEWED',reason=str(exc))
            projection.pop('evidence',None)
    else:projection['reason']=envelope.get('reason',result.get('parse_error',result.get('error_type','not_dispatched_or_incomplete')))
    return projection


def project(run,output_name='adjudications.json'):
    pairs=all_candidates(run);docs,snippets=all_sources(run)
    old_root=Path('data/retrieval/p05-navi-r1-qa-20261025-r1')
    rows=[];missing=[];before_after=Counter();claim_rows=[]
    for pair in pairs:
        primary=project_one(run,pair,'deepseek-v4.1-flash',snippets)
        clarification=run/'clarifications'/(pair['pair_id']+'.json')
        if output_name=='adjudications.json' and clarification.exists():
            followup=load(clarification)
            if label(followup)!='unreviewed':
                followup['first_pass_projection']=primary;primary=followup
            else:primary['failed_clarification']=followup
        if primary['execution_state']=='NOT_DISPATCHED' and not (run/'reviews'/('review-'+pair['pair_id']+'-deepseek.json')).exists():missing.append(pair['pair_id'])
        secondary=[]
        for model in MODELS:
            job='review-'+pair['pair_id']+'-'+model_tag(model)
            if (old_root/'reviews'/(job+'.json')).exists() or list((old_root/'attempts').glob(job+'*')):
                secondary.append(project_one(old_root,pair,model,snippets))
        for value in [primary]+secondary:
            before_after[str(value.get('original_mechanical_status'))+' -> '+value['mechanical_quote_validation']]+=1
            judgment=value.get('judgment')
            if judgment:
                claim_rows.append({'claim_id':sha256((pair['pair_id']+'|'+value['model']+'|'+judgment.get('proposed_claim','')).encode()).hexdigest(),
                    'pair_id':pair['pair_id'],'sources':pair['sources'],'model':value['model'],
                    'relation_label':judgment.get('relation'),'proposed_claim':judgment.get('proposed_claim'),
                    'mechanical_quote_validation':value['mechanical_quote_validation'],'model_semantic_review':value['model_semantic_review'],
                    'human_semantic_acceptance':'NOT_REVIEWED','authority':'candidate_interpretation_not_canonical_fact'})
        rows.append({'pair_id':pair['pair_id'],'sources':pair['sources'],'titles':[docs[c]['title'] for c in pair['sources']],
            'primary':primary,'supplemental_reviews':secondary,'legacy_records':pair['legacy']})
    if missing:raise ValueError(f'{len(missing)} pairs have not been dispatched or dispositioned; refusing final projection')
    snapshot=run/'analysis-code-snapshots'/(CODE_SHA+'.py')
    if not snapshot.exists():snapshot.parent.mkdir(exist_ok=True);snapshot.write_bytes(CODE)
    once(run/output_name,{'pairs':rows,'before_after_mechanical_counts':dict(before_after),'qa_analysis_sha256':CODE_SHA,
        'model_policy':'Uniform DeepSeek primary, partial Qwen supplemental only. No human gold.',
        'relation_deduplication':'Metrics count unique verified source-snapshot pairs. Distinct model relation labels/claims remain separate; no guessed semantic equivalence.',
        'human_semantic_acceptance':'NOT_REVIEWED'})
    if output_name=='adjudications.json':
        with (run/'relation-claims.jsonl').open('x',encoding='utf-8') as handle:
            for row in claim_rows:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
        fields=['pair_id','source_a','title_a','source_b','title_b','execution_state','mechanical_quote_validation',
            'model_semantic_review','verdict','relation','claim_level','literary_value','unsupported_identity_or_cause','human_semantic_acceptance']
        with (run/'candidate-review-ledger.csv').open('x',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for row in rows:
                p=row['primary'];v=p.get('judgment',{})
                writer.writerow({'pair_id':row['pair_id'],'source_a':row['sources'][0],'title_a':row['titles'][0],
                    'source_b':row['sources'][1],'title_b':row['titles'][1],**{k:p.get(k) for k in fields if k in p},
                    **{k:v.get(k) for k in ['verdict','relation','claim_level','literary_value','unsupported_identity_or_cause']},
                    'human_semantic_acceptance':'NOT_REVIEWED'})
    print(json.dumps({'projection_pairs':len(rows),'before_after':dict(before_after),
        'primary_status':dict(Counter(label(row['primary']) for row in rows))}),flush=True)


def label(review):
    if review.get('mechanical_quote_validation')=='PASS' and review.get('model_semantic_review')=='REVIEWED_NOT_HUMAN_GOLD':
        return review['judgment']['verdict']
    return 'unreviewed'


def project_initial(run):project(run,'adjudications-initial.json')


def clarify(run):
    initial=load(run/'adjudications-initial.json')['pairs']
    selected=[p for p in initial if label(p['primary'])=='unreviewed' and p['primary']['execution_state']=='RESOLVED']
    policy=run/'clarification-policy.json'
    if not policy.exists():once(policy,{'time':r1.now(),'selection':'All complete primary attempts with invalid mechanical/schema/JSON outputs, method blind; no useful/reject-based selection.',
        'rule':'At most one independently worded formatting clarification, only after original execution is RESOLVED. UNKNOWN, unsent or missing-source cases are ineligible. Original results remain immutable.',
        'reason':'Complete source-ID review as authorized; offline parsing is preferred and precedes any additional paid call. No model or verdict/rank information supplied.',
        'selected_pair_ids':[p['pair_id'] for p in selected],'same_qa_budget_and_model':True})
    campaign=DeepSeekCampaign(run);docs,snippets=all_sources(run)
    byid={p['pair_id']:p for p in all_candidates(run)}
    def work(row):
        pairid=row['pair_id'];path=run/'clarifications'/(pairid+'.json')
        if path.exists():return load(path)
        if row['primary']['execution_state']!='RESOLVED':raise ValueError('clarification cannot retry unknown')
        delivery=load(run/'deliveries'/(pairid+'.json'));payload=dict(delivery['payload'])
        payload['format_requirement']='这是一次独立重新判读，请勿猜测旧结论。只输出合法JSON对象，不加Markdown围栏或多余括号；a_ids/b_ids中的每个ID必须与A/B清单逐字相同，禁止省略前导零或发明ID。verdict/relation/claim_level严格使用指令中枚举。保持文学解释与确定历史的区分。'
        job='review-'+pairid+'-deepseek-clarification'
        result=campaign.review(job,payload,'deepseek-v4.1-flash')
        result=effective_response(run,job,result)
        output={'model':'deepseek-v4.1-flash','job':job,'cohort':str(run),'execution_state':result.get('state'),
            'mechanical_quote_validation':'NOT_VERIFIED','model_semantic_review':'NOT_REVIEWED','human_semantic_acceptance':'NOT_REVIEWED',
            'clarification_of':row['primary']['job'],'original_execution_was':'RESOLVED','sides':delivery['sides'],
            'coverage':{s:delivery['payload'][s]['coverage'] for s in ['A','B']},
            'payload_sha256':sha256(json.dumps(delivery['payload'],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'delivery_path':str(run/'deliveries'/(pairid+'.json'))}
        if result.get('complete') and 'value' in result:
            try:output.update(validate(result['value'],delivery['aliases'],snippets))
            except ValueError as exc:output['reason']=str(exc)
        else:output['reason']=result.get('error_type',result.get('parse_error','incomplete'))
        once(path,output);return output
    results=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(work,p):p['pair_id'] for p in selected}
        for f in as_completed(futures):
            try:results.append(f.result())
            except Exception as exc:results.append({'pair_id':futures[f],'reason':type(exc).__name__,'model_semantic_review':'NOT_REVIEWED'})
    once(run/'clarification-summary.json',{'selected':len(selected),'completed':len(results),
        'labels':dict(Counter(label(p) for p in results)),'totals':campaign.totals()})
    print(json.dumps(load(run/'clarification-summary.json')),flush=True)


def pair_statistics(pair_ids, reviews):
    values=[reviews[i]['primary'] for i in pair_ids];counts=Counter(label(v) for v in values)
    n=len(values);missing=counts.get('unreviewed',0);useful=counts.get('useful',0)
    return {'source_pair_count':n,'counts':dict(counts),'mechanical_pass':sum(v.get('mechanical_quote_validation')=='PASS' for v in values),
        'non_duplicate_story_useful':sum(label(v)=='useful' and v['judgment'].get('relation') not in {'duplicate','gameplay_only','none'} for v in values),
        'duplicate_useful_navigation_only':sum(label(v)=='useful' and v['judgment'].get('relation')=='duplicate' for v in values),
        'gameplay_useful_navigation_only':sum(label(v)=='useful' and v['judgment'].get('relation')=='gameplay_only' for v in values),
        'literary_useful':sum(label(v)=='useful' and v['judgment'].get('literary_value',False) for v in values),
        'interpretive_useful':sum(label(v)=='useful' and v['judgment'].get('claim_level')=='interpretive' for v in values),
        'useful_with_identity_cause_risk':sum(label(v)=='useful' and v['judgment'].get('unsupported_identity_or_cause',False) for v in values),
        'model_useful_completion_bounds':[useful/max(n,1),(useful+missing)/max(n,1)],
        'bound_meaning':'Bounds for completion of this model-review label only, not bounds on human precision or objective truth. All pairs remain in denominator.'}


def compare(run):
    rows=load(run/'adjudications.json')['pairs'];reviews={p['pair_id']:p for p in rows}
    pairs=all_candidates(run)
    proposals=load(PARENT/'evaluator-only/reference-proposals.json');seed_split={c['source']:c['split'] for c in proposals}
    groups=defaultdict(list);arrival=defaultdict(set)
    for p in pairs:
        for d in p['discoveries']:
            if d.get('support_only'):continue
            if d.get('undirected'):
                arrival[d['method']].add(p['pair_id']);continue
            groups[(d['scope'],d['method'])].append(d|{'pair_id':p['pair_id']})
    statistics={};sets={}
    for (scope,method),events in groups.items():
        key=scope+'/'+method;statistics[key]={}
        for split in ['all','dev','holdout']:
            statistics[key][split]={}
            for k in [5,20]:
                admitted={e['pair_id'] for e in events if e['rank']<=k and (split=='all' or seed_split.get(e['source'])==split)}
                sets[(key,split,k)]=admitted
                statistics[key][split][str(k)]=pair_statistics(admitted,reviews)
    baselines=['local/raw_hybrid','local/raw_hybrid_budget6','local/rewrite_hybrid_r0',
        'scope_control/raw_subset_augmented','scope_control/raw6_subset_augmented','scope_control/rewrite_subset_augmented',
        'matched_body/raw_current_hybrid','matched_body/rewrite_current_hybrid','full/raw_lexical']
    arms=['local/ordinary_profile','local/a0_merged','local/bidirectional','local/narrative_bridge',
        'full/ordinary_profile','full/a0_merged','full/bidirectional','full/narrative_bridge',
        'local/a0_merged_locator','local/bidirectional_locator','full/a0_merged_locator','full/bidirectional_locator']
    increments=[]
    for arm in arms:
        for base in baselines:
            for split in ['all','dev','holdout']:
                for k in [5,20]:
                    ids=sets.get((arm,split,k),set())-sets.get((base,split,k),set())
                    increments.append({'arm':arm,'baseline':base,'split':split,'k':k,'pair_ids':sorted(ids),
                        'statistics':pair_statistics(ids,reviews),
                        'comparability':'Scope/current-body controls retain R1 limitations; this is measured set difference, not an isolated causal architecture effect.'})
    # Direct matching-mechanism comparison shares A0 information, vectors,
    # queries, binding condition and source prior in the original experiment.
    for scope in ['local','full']:
        for suffix in ['','_locator']:
            for split in ['all','dev','holdout']:
                for k in [5,20]:
                    for arm,base in [(scope+'/bidirectional'+suffix,scope+'/a0_merged'+suffix),
                        (scope+'/a0_merged'+suffix,scope+'/bidirectional'+suffix)]:
                        ids=sets[(arm,split,k)]-sets[(base,split,k)]
                        increments.append({'arm':arm,'baseline':base,'split':split,'k':k,'pair_ids':sorted(ids),
                            'statistics':pair_statistics(ids,reviews),'comparability':'Same original A0 information/geometry/query budget; role-mask mechanism difference only at this operating point.'})
    cross=Counter();disagreements=[]
    for row in rows:
        for secondary in row['supplemental_reviews']:
            a,b=label(row['primary']),label(secondary)
            if a!='unreviewed' and b!='unreviewed':
                cross[secondary['model']+'/'+b+' -> '+a]+=1
                if a!=b:disagreements.append({'pair_id':row['pair_id'],'other_model':secondary['model'],'other':b,'deepseek':a,
                    'same_payload':secondary.get('payload_sha256')==row['primary'].get('payload_sha256')})
    rank_sets={key:{k:sets[(key,'all',k)] for k in [5,20]} for key in statistics}
    unique_any=[]
    for k in [5,20]:
        for arm in ['full/ordinary_profile','full/a0_merged','full/bidirectional','full/narrative_bridge']:
            union=set().union(*(rank_sets[b][k] for b in ['scope_control/raw6_subset_augmented','scope_control/rewrite_subset_augmented']))
            ids=rank_sets[arm][k]-union
            unique_any.append({'arm':arm,'k':k,'baseline':'union of same-scope raw6 and rewrite subset controls',
                'pair_ids':sorted(ids),'statistics':pair_statistics(ids,reviews)})
    repeats={}
    for method in ['ordinary_profile','a0_merged','bidirectional']:
        base=rank_sets['local/'+method][20]
        for repeat in [1,2]:
            key='profile_repeat/'+str(repeat)+'-'+method
            events=groups[('profile_repeat',str(repeat)+'-'+method)]
            seeds={e['source'] for e in events}
            restricted={e['pair_id'] for e in groups[('local',method)] if e['source'] in seeds and e['rank']<=20}
            new=rank_sets[key][20]
            useful_base={p for p in restricted if label(reviews[p]['primary'])=='useful'}
            useful_new={p for p in new if label(reviews[p]['primary'])=='useful'}
            repeats[method+'/'+str(repeat)]={'base_pairs':len(restricted),'repeat_pairs':len(new),
                'base_model_useful':len(useful_base),'repeat_model_useful':len(useful_new),
                'model_useful_set_jaccard':len(useful_base&useful_new)/max(len(useful_base|useful_new),1),
                'caveat':'Repeated source/target profiles vary together; one invalid ordinary repeat remains absent.'}
    chains=[]
    for extension in run.glob('candidate-extension-*.json'):
        for chain in load(extension).get('chain_traces',[]):
            statuses={key:label(reviews[chain[key]]['primary']) for key in ['edge1','edge2','direct_pair']}
            chains.append(chain|{'model_statuses':statuses,
                'both_edges_model_useful':statuses['edge1']=='useful' and statuses['edge2']=='useful',
                'both_edges_model_useful_or_tentative':all(statuses[k] in {'useful','tentative'} for k in ['edge1','edge2']),
                'historical_identity_or_cause_transitivity':'NOT_ASSERTED'})
    first_qa=Path('data/retrieval/p05-navi-r1-qa-20261025-r1')
    old_audit=load(first_qa/'legacy-quote-audit.json');old_raw=load(PARENT/'quality-final.json')['pairs']
    transitions=defaultdict(Counter);transition_rows=[]
    for old in old_audit['pairs']:
        original=old_raw[old['old_row']];kind=old['old_verdict']
        if kind=='unreviewed':
            if (old.get('old_validation_error') or '').startswith('judge supporting'):kind='quote_binding_failure'
            elif original.get('reason')=='no_narrative_body':kind='no_narrative_body'
            elif old.get('old_execution_state')=='UNKNOWN':kind='execution_unknown'
            else:kind='other_invalid_output'
        elif kind=='reject':
            exact=all(old['diagnosis'][s]['category']=='exact_cited' for s in ['A','B'])
            kind='reject_with_dual_exact_quote' if exact else 'reject_without_dual_exact_quote'
        primary=reviews[old['pair_id']]['primary'];new=label(primary)
        transitions[kind][new]+=1
        transition_rows.append({'old_row':old['old_row'],'pair_id':old['pair_id'],'source':old['source'],'target':old['target'],
            'old_disposition':kind,'new_model_label':new,'new_mechanical_status':primary.get('mechanical_quote_validation'),
            'old_execution_unknown_not_resolved_by_new_review':old.get('old_execution_state')=='UNKNOWN',
            'new_review_does_not_validate_old_claim_automatically':True})
    orphan_links=[]
    for orphan in old_audit['unassigned_historical_rows']:
        original=orphan['original_record'];match=re.search(r'quality-(\d+)-(\d+)',original.get('error',''))
        value=orphan|{'reconciliation':'NO_UNIQUE_SOURCE_MAPPING'}
        if match and original.get('error_type')=='FileExistsError':
            a,b=match.groups();key=pid(a,b)
            if key in reviews:value|={'pair_id':key,'source':a,'target':b,
                'reconciliation':'EXACT_JOB_NAME_RECOVERS_PAIR_ONLY_OLD_SEMANTIC_REVIEW_STILL_UNVERIFIED'}
        orphan_links.append(value)
    once(run/'legacy-final-dispositions.json',{'old_observations':len(transition_rows),'transition_counts':{k:dict(v) for k,v in transitions.items()},
        'rows':transition_rows,'historical_orphan_rows':orphan_links,
        'caveat':'New model, source-ID protocol, literary rubric and some context sizes differ from R1. Label changes are not a quote-only causal effect or human correction of old truth.'})
    once(run/'method-comparison.json',{'statistics':statistics,'unique_increments':increments,'novel_over_both_scope_controls':unique_any,'qa_analysis_sha256':CODE_SHA,
        'global_primary':pair_statistics(set(reviews),reviews),'arrival_model_quality':{k:pair_statistics(v,reviews) for k,v in arrival.items()},
        'cross_model_partial_counts':dict(cross),'cross_model_disagreements':disagreements,'profile_repeat_model_yield':repeats,
        'bridge_path_reviews':chains,
        'r1_rank_stability':load(PARENT/'supplemental-analysis.json')['profile_stability'],
        'limits':['Uniform DeepSeek model labels, no independent human gold. Qwen overlap is incomplete after quota exhaustion.',
            'QA repairs evidence review, not R1 candidate generation, vector-space compatibility or subset priors.',
            'Scores retain all unreviewed pairs; model yield is not unbiased precision or final QA accuracy.',
            'Arrival simulations have no preserved directed rank; they are separate from ranked method comparisons.',
            'Literary interpretation can be valuable without proving objective history or identity.']})
    print(json.dumps({'global':pair_statistics(set(reviews),reviews),'method_groups':len(statistics),'cross_model_comparisons':sum(cross.values())}),flush=True)


def human_packet(run):
    import html
    rows={p['pair_id']:p for p in load(run/'adjudications.json')['pairs']}
    comparison=load(run/'method-comparison.json');docs,snippets=all_sources(run)
    chosen=[];reasons={}
    def admit(pairid,reason):
        if pairid in rows and pairid not in reasons:chosen.append(pairid);reasons[pairid]=reason
    for a,b in [('6350','500037'),('1146','3867'),('1148','5404'),('1632','3121'),('1612','1627'),('1288','296')]:
        admit(pid(a,b),'Preselected reference/source-boundary/literary contrast; no assumption about correct verdict.')
    for method in ['a0_merged','bidirectional','narrative_bridge']:
        item=next(x for x in comparison['novel_over_both_scope_controls'] if x['arm']=='full/'+method and x['k']==20)
        for pairid in item['pair_ids']:
            p=rows[pairid]['primary']
            if label(p)=='useful' and p['judgment'].get('relation') not in {'duplicate','gameplay_only','none'} and pairid not in reasons:
                admit(pairid,'Unique model-valued pair over both scope controls; method hidden.');break
    for item in comparison['cross_model_disagreements']:
        if item['pair_id'] not in reasons:
            admit(item['pair_id'],'Cross-family model disagreement on the same frozen source pair.');break
    random.Random(20261025).shuffle(chosen)
    lines=['# R1-QA 精简人工盲审材料','','**全部待人工确认。** 此文件隐藏检索方法、排名、模型名称和模型评分；这些是定向争议案例，不是随机金标准，不能由本材料计算全体准确率。',
        '','请分别判断：联读是否有具体信息增量；文学/世界观对应是否有足够锚点；是否混淆了身份或因果；证据是否足够。`useful/tentative/reject/insufficient` 都是合法回答。',
        '','下面只展示程序提取的部分完整片段。它们的真实来源和偏移已校验，但这不证明解释正确。完整交付正文、选中证据及原始响应仍保留在独立 JSON 中。']
    index=[];template=[]
    for number,pairid in enumerate(chosen,1):
        row=rows[pairid];p=row['primary'];caseid=f'Q{number:02d}';delivery=load(run/'deliveries'/(pairid+'.json'))
        once(run/'human-context'/(caseid+'.json'),{'case':caseid,'proposed_claim':p.get('judgment',{}).get('proposed_claim'),
            'selected_source_evidence':p.get('evidence',{}),'full_delivered_bodies':delivery['payload'],
            'source_aliases':delivery['aliases'],'human_semantic_acceptance':'NOT_REVIEWED',
            'note':'No method, rank, model name or model grade. Selected evidence has exact source text; this is not a semantic acceptance.'})
        lines+=['',f'## {caseid}：{row["titles"][0]} ／ {row["titles"][1]}','',
            '**待审关联表述**：'+p.get('judgment',{}).get('proposed_claim','请独立判断双方是否有具体联读价值，当前模型输出未完成有效核验。'),
            '', '[完整交付正文及全部选中证据]('+str((Path.cwd()/run/'human-context'/(caseid+'.json')).as_posix())+')']
        for side in ['A','B']:
            cid=delivery['sides'][side];d=docs[cid]
            evidence=p.get('evidence',{}).get(side,[])
            evidence_mode='模型选择且程序核验的原文片段'
            if not evidence:
                aliases=delivery['aliases'];evidence=[snippets[v] for k,v in aliases.items() if k.startswith(side)][:2]
                evidence_mode='已验证来源背景，当前没有通过核验的模型选中证据'
            lines+=['',f'### {side}：{d["title"]}',f'{evidence_mode}。来源模块策略：`{d["body_policy"]}`。']
            for s in evidence[:2]:
                lines+=['',f'Canonical section={s["section"]}, unit={s["unit"]}, 字符范围 [{s["start"]},{s["end"]})；片段 `{s["id"]}`。',
                    '']+['> '+html.escape(line).replace('[','\\[') for line in s['text'].splitlines()]
            lines+=['','[来源 Canonical]('+str((Path.cwd()/d['canonical_path']).as_posix())+')']
        lines+=['','**人工判断**：待确认。请填写联读价值、原文支持程度、是否过推断及建议修订。']
        index.append({'case':caseid,'pair_id':pairid,'selection_reason':reasons[pairid],'primary_verdict':label(p),
            'human_semantic_acceptance':'NOT_REVIEWED','not_representative':True})
        template.append({'case':caseid,'human_verdict':'NOT_REVIEWED','support_sufficient':'NOT_REVIEWED',
            'identity_or_cause_overreach':'NOT_REVIEWED','revised_claim':'','notes':''})
    (run/'human-review-packet.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    once(run/'evaluator-only-human-packet-index.json',{'cases':index,'do_not_include_with_blind_packet':True})
    with (run/'human-review-template.csv').open('x',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(template[0]));writer.writeheader();writer.writerows(template)
    print(json.dumps({'human_cases':len(chosen),'human_acceptances':0}),flush=True)


def audit(run):
    roots=[Path('data/retrieval/p05-navi-r1-qa-20261025-r1'),run]
    secrets=[v.encode() for k,v in r1.environment().items() if k.endswith('_API_KEY') and len(v)>10]
    leaks=[];pending=[];code_failures=[];costs=[];events=[];input_failures=[]
    for root in roots:
        for name,expected in load(root/'prepared-input-hashes.json').items():
            if r1.file_hash(root/name)!=expected:input_failures.append(str(root/name))
        campaign=QACampaign(root,audit_only=True);known_in=known_out=known_cost=0;states=Counter()
        for job,issued in campaign.reservations.items():
            stem=root/'attempts'/job;terminal=campaign.terminals.get(job,{})
            if not terminal:pending.append(str(stem))
            states[terminal.get('state','UNRESOLVED')]+=1
            usage=terminal.get('usage') or {};i,o=usage.get('input_tokens'),usage.get('output_tokens')
            if isinstance(i,int) and isinstance(o,int):known_in+=i;known_out+=o;known_cost+=(i*issued['input_rate']+o*issued['output_rate'])/1e6
            request=stem/'request.json'
            if request.exists():events.append((issued['time'],str(stem),r1.request_fingerprint(load(request)),terminal.get('state','UNKNOWN')))
        costs.append(campaign.totals()|{'cohort':str(root),'known_input_tokens':known_in,'known_output_tokens':known_out,
            'known_accounting_rmb':known_cost,'terminal_states':dict(states),'actual_provider_bill':'UNKNOWN'})
        for snapshot in list((root/'qa-code-snapshots').glob('*.py'))+list((root/'analysis-code-snapshots').glob('*.py')):
            if r1.file_hash(snapshot)!=snapshot.stem:code_failures.append(str(snapshot))
        for path in root.rglob('*'):
            if not path.is_file():continue
            if path.suffix.lower() in {'.json','.jsonl','.bin','.md','.py','.csv'}:raw=path.read_bytes()
            elif path.name.endswith('.jsonl.gz'):raw=gzip.decompress(path.read_bytes())
            else:continue
            if any(secret in raw for secret in secrets):leaks.append(str(path))
    # Include historical UNKNOWNs to test that switching job/cohort/provider
    # labels never causes the same model+semantic request to be resent.
    for root in PARENT.parent.glob('p05-navi-r1-discovery-20261025-r*'):
        for issued_path in (root/'attempts').glob('*/issued.json'):
            stem=issued_path.parent;issued=load(issued_path);terminal=load(stem/'terminal.json') if (stem/'terminal.json').exists() else {'state':'UNKNOWN'}
            state='NOT_SENT' if (stem/'dispatch-reconciliation.json').exists() else terminal.get('state')
            request=stem/'request.json'
            if not request.exists():request=stem/'sdk/request.json'
            if request.exists():events.append((issued['time'],str(stem),r1.request_fingerprint(load(request)),state))
    unknown={};reissues=[]
    for time_,stem,fingerprint,state in sorted(events):
        if state!='NOT_SENT' and fingerprint in unknown:reissues.append({'prior':unknown[fingerprint],'later':stem,'state':state})
        if state=='UNKNOWN':unknown.setdefault(fingerprint,stem)
    docs,snippets=all_sources(run);source_failures=[];snippet_failures=[]
    for cid,d in docs.items():
        if r1.file_hash(Path(d['canonical_path']))!=d['canonical_sha256']:source_failures.append(cid)
        units={(u['section'],u['unit']):u['text'] for u in d['units']}
        for sid in d['snippet_ids']:
            s=snippets[sid]
            if units[(s['section'],s['unit'])][s['start']:s['end']]!=s['text'] or sha256(s['text'].encode()).hexdigest()!=s['text_sha256']:
                snippet_failures.append(sid)
    parent_failures=[]
    for item in load(PARENT/'artifact-inventory.json')['files']:
        if r1.file_hash(PARENT/item['path'])!=item['sha256']:parent_failures.append(item['path'])
    switch=load(roots[0]/'operator-provider-switch.json');late_aliyun=[str(p) for p in (roots[0]/'attempts').glob('*/issued.json') if load(p)['time']>switch['time']]
    # Audit blinding from actual persisted wire requests, not runner intent.
    leaking_review_keys=[]
    forbidden={'method','rank','legacy','expected_target','target_titles','reference_proposals','retrieval_method'}
    def has_forbidden(value):
        if isinstance(value,dict):return bool(forbidden&value.keys()) or any(has_forbidden(v) for v in value.values())
        if isinstance(value,list):return any(has_forbidden(v) for v in value)
        return False
    for root in roots:
        for request in (root/'attempts').glob('*/request.json'):
            wire=load(request)
            payload=wire.get('input')
            if payload is None:
                payload=next((m['content'] for m in wire.get('messages',[]) if m.get('role')=='user'),'{}')
            if isinstance(payload,str):payload=json.loads(payload)
            if has_forbidden(payload):leaking_review_keys.append(str(request))
    report={'time':r1.now(),'qa_analysis_sha256':CODE_SHA,'canonical_sources_checked':len(docs),'canonical_hash_failures':source_failures,
        'snippets_checked':len(snippets),'snippet_offset_hash_failures':snippet_failures,'prepared_input_hash_failures':input_failures,
        'credential_leak_files':leaks,'missing_terminals':pending,'code_hash_failures':code_failures,
        'historical_r1_inventory_checked':len(load(PARENT/'artifact-inventory.json')['files']),'historical_r1_hash_failures':parent_failures,
        'all_unknown_reissues':reissues,'qa_unknown_reissues':[r for r in reissues if 'p05-navi-r1-qa-' in r['later']],
        'aliyun_dispatch_after_operator_switch':late_aliyun,'blinding_key_leaks':leaking_review_keys,'costs':costs,
        'total_known_accounting_rmb':sum(c['known_accounting_rmb'] for c in costs),
        'total_accounted_or_reserved_rmb':sum(c['accounted_or_reserved_rmb'] for c in costs),
        'total_known_tokens':sum(c['known_input_tokens']+c['known_output_tokens'] for c in costs),
        'human_semantic_acceptances':0,'actual_provider_bill':'UNKNOWN'}
    once(run/'final-audit.json',report)
    inventory=[]
    for root in roots:
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.name!='qa-artifact-inventory.json':inventory.append({'path':str(path),'bytes':path.stat().st_size,'sha256':r1.file_hash(path)})
    once(run/'qa-artifact-inventory.json',{'files':inventory,'file_count':len(inventory)})
    print(json.dumps({k:v for k,v in report.items() if k!='costs'},ensure_ascii=False),flush=True)


def finish(run):
    # One bounded second pass recovers only never-issued connection warming.
    if not (run/'post-review-warm-recovery.json').exists():
        review(run)
        once(run/'post-review-warm-recovery.json',{'time':r1.now(),'rule':'At most one recovery after unauthenticated warming failed and zero model reservations existed. UNKNOWN is never reissued.'})
    if not (run/'adjudications-initial.json').exists():project_initial(run)
    if not (run/'clarification-summary.json').exists():clarify(run)
    if not (run/'adjudications.json').exists():project(run)
    if not (run/'method-comparison.json').exists():compare(run)
    if not (run/'human-review-packet.md').exists():human_packet(run)
    if not (run/'final-audit.json').exists():audit(run)


def source_sensitivity(run):
    """Post-review source-category diagnostic; no re-ranking or model calls."""
    docs,_=all_sources(run);reviews={p['pair_id']:p for p in load(run/'adjudications.json')['pairs']}
    map_path=Path('data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/responses/map/e8a1a63e29d6fe1c886bd55f590d0b3770a03d92848b9da49c6928e5b09cd3e5.json')
    channels={}
    def walk(value):
        if isinstance(value,dict):
            if 'id' in value and 'name' in value and 'parent_id' in value:
                channels[str(value['id'])]={k:value.get(k) for k in ['id','name','parent_id','depth']}
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(load(map_path))
    if channels['189']['name']!='图鉴' or channels['62']['name']!='观测·考据':raise ValueError('channel contract conflict')
    source={}
    for cid,d in docs.items():
        record=load(d['canonical_path']);membership=set(map(str,record['record_metadata'].get('channel_memberships',[])))
        explicit=bool('62' in membership or re.search('考据|考证|设定分析|世界观分析|漫谈|剧情解析|世界观解读',d['title']))
        kind='explicit_analysis' if explicit else ('guide_or_observer' if '190' in membership else ('catalog' if '189' in membership else 'other_unclassified'))
        source[cid]={'title':d['title'],'channels':sorted(membership),'channel_names':[channels.get(c,{}).get('name','UNKNOWN') for c in sorted(membership)],
            'diagnostic_kind':kind,'book_category':'68' in membership,'canonical_sha256':d['canonical_sha256'],
            'authority':'Category screening only; publisher authorship and primary-game-text attribution not inferred.'}
    info={}
    for pairid,p in reviews.items():
        aa=[source[c] for c in p['sources']];j=p['primary'].get('judgment',{})
        info[pairid]={'involves_secondary_category':any(s['diagnostic_kind'] in {'explicit_analysis','guide_or_observer'} for s in aa),
            'catalog_only':all(s['diagnostic_kind']=='catalog' for s in aa),
            'book_involved':any(s['book_category'] for s in aa),'primary_model_label':label(p['primary']),
            'relation_label':j.get('relation')}
    def summarize(ids):
        good={i for i in ids if info[i]['primary_model_label']=='useful'}
        return {'all_retrieved_pair_denominator':len(ids),'model_useful_all_source_kinds':len(good),
            'model_useful_involving_secondary':sum(info[i]['involves_secondary_category'] for i in good),
            'model_useful_catalog_only':sum(info[i]['catalog_only'] for i in good),
            'model_useful_not_secondary':sum(not info[i]['involves_secondary_category'] for i in good),
            'model_useful_book_involved_not_secondary':sum(info[i]['book_involved'] and not info[i]['involves_secondary_category'] for i in good),
            'no_human_precision_claim':True}
    proposals=load(PARENT/'evaluator-only/reference-proposals.json');split={p['source']:p['split'] for p in proposals};sets=defaultdict(set)
    for p in all_candidates(run):
        for d in p['discoveries']:
            if d.get('undirected') or d.get('support_only'):continue
            for k in [5,20]:
                if d['rank']<=k:
                    sets[(d['scope']+'/'+d['method'],'all',k)].add(p['pair_id'])
                    sets[(d['scope']+'/'+d['method'],split.get(d['source'],'other'),k)].add(p['pair_id'])
    stats={f'{method}/{sp}/{k}':summarize(ids) for (method,sp,k),ids in sets.items()}
    novelty=[]
    for sp in ['all','holdout']:
        for k in [5,20]:
            baseline=sets[('full/raw_hybrid_budget6',sp,k)]|sets[('full/rewrite_hybrid_r0',sp,k)]
            scopebase=sets[('scope_control/raw6_subset_augmented',sp,k)]|sets[('scope_control/rewrite_subset_augmented',sp,k)]
            for arm in ['ordinary_profile_query_only','a0_merged_query_only','narrative_bridge_query_only',
                'ordinary_profile','a0_merged','bidirectional','narrative_bridge']:
                own=sets[('full/'+arm,sp,k)]
                for name,base in [('full_raw6_plus_rewrite_union',baseline),('both_scope_controls_union',scopebase)]:
                    ids=own-base;novelty.append({'arm':arm,'split':sp,'k':k,'baseline':name,'pair_ids':sorted(ids),'statistics':summarize(ids)})
    once(run/'source-scope-sensitivity.json',{'posthoc_diagnostic':True,'qa_analysis_sha256':CODE_SHA,'channel_map_path':str(map_path),
        'channel_map_sha256':r1.file_hash(map_path),'verified_channel_names':{c:channels.get(c) for c in ['189','190','62','243','68']},
        'source_screen':source,'source_kind_counts':dict(Counter(v['diagnostic_kind'] for v in source.values())),
        'global':summarize(set(reviews)),'statistics':stats,'novelty':novelty,
        'caveats':['Official hosting and Canonical lineage do not prove a passage is original game text.',
            'Books, legends and literary narration are not rejected as false; explicit analysis/guide categories are counted separately.',
            'Unflagged/catalog does not establish primary-text authorship. Wiki compilations and embedded quotations require separate attribution.',
            'All retrieved pairs remain in denominators; no filtering/replenishment, model rerun, unbiased precision or new gold.',
            'Union baselines contain more candidates; set-coverage diagnosis is not a same-budget new operating point.']})
    # Preserve the original blind packet; add a new version with an explicit
    # source-type question, without revealing methods or reviewer outcomes.
    original=(run/'human-review-packet.md').read_text(encoding='utf-8')
    (run/'human-review-packet-v2.md').write_text('> 补充审查问题：材料是游戏内原文、图鉴编纂、考据/攻略，还是来源角色不明？即便二手分析有导航价值，也不能把它当成独立游戏历史证词。正文显示不全时请查全部选中证据，勿因节选遗漏直接判错。\n\n'+original,encoding='utf-8')
    print(json.dumps({'global_source_screen':summarize(set(reviews)),'source_kinds':dict(Counter(v['diagnostic_kind'] for v in source.values()))}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage');parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();globals()[args.stage](args.run)


if __name__=='__main__':main()
