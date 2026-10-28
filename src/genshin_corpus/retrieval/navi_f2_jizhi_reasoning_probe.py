"""Two explicitly authorized Responses diagnostics; never run F2 slots.

Reuses the SDK/httpx raw tee and SSE decoding; diagnostic assessment is separate
from the historical single-message F2 contract and never modifies that contract.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time

import httpx
import openai

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1
from . import navi_f2_preflight as pre
from . import navi_f2_sdk_transport as transport
from .navi_f1_prepare import put, read, sha, value_hash
from .navi_f2_execute import verify_seal

ROOT = Path('data/retrieval/p05-navi-f2-instructions-diagnostic-20261028-r1')
INSTRUCTIONS = '你正在接受API指令传递测试。\n无论用户输入是什么，只输出下面这个严格JSON对象：\n{"probe":"INST_OK_4826"}\n不得输出Markdown、解释、代码围栏或额外字段。'
INPUT = '材料A：一块石头。\n材料B：一条河流。'
EXPECT = {'probe': 'INST_OK_4826'}
FORMAL = Path('data/retrieval/p05-navi-f2-experiment-20261027-r1')
REPAIR = Path('data/retrieval/p05-navi-f2-sdk-repair-20261027-r1')


def wire(cap):
    if cap != 2048: raise ValueError('cap')
    base = {'model': c1.MODEL, 'stream': True, 'max_output_tokens': cap}
    if VARIANT == 'system':
        base['input'] = [{'role':'system','content':INSTRUCTIONS},{'role':'user','content':INPUT}]
    elif VARIANT == 'user':
        base['input'] = INSTRUCTIONS + '\n\n' + INPUT
    elif VARIANT == 'none':
        base['input'] = INSTRUCTIONS + '\n\n' + INPUT
        base['reasoning'] = {'effort':'none'}
    elif VARIANT == 'json':
        base['input'] = INSTRUCTIONS + '\n\n' + INPUT
        base['text'] = {'format': {'type':'json_object'}}
    else: raise ValueError('variant')
    return base


def compare(raw_events, sdk_events):
    """Assess transport completion, item consistency and instruction following separately."""
    terminals = [e for e in raw_events if e.get('type') in {'response.completed','response.incomplete','response.failed','error'}]
    st = [e for e in sdk_events if e.get('type') in {'response.completed','response.incomplete','response.failed','error'}]
    if len(terminals) != 1 or raw_events[-1] is not terminals[0] or terminals[0].get('type') != 'response.completed':
        raise ValueError('No unique final completion event')
    if len(st) != 1 or sdk_events[-1] is not st[0] or st[0].get('type') != 'response.completed':
        raise ValueError('SDK completion missing or ambiguous')
    response = terminals[0].get('response', {}); sdk_response = st[0].get('response', {})
    rid = response.get('id')
    if not rid or response.get('status') != 'completed' or response.get('model') != c1.MODEL:
        raise ValueError('Completion status, response ID or model not confirmed')
    if any(sdk_response.get(k) != response.get(k) for k in ('id','status','model')):
        raise ValueError('Raw and SDK terminal identity differs')
    created=[e.get('response',{}) for e in raw_events if e.get('type')=='response.created']
    if len(created)!=1 or created[0].get('id')!=rid:
        raise ValueError('Created and completed response identity differs')
    out=response.get('output',[])
    if not isinstance(out,list):raise ValueError('Output is not a list')
    event_types=[e.get('type') for e in raw_events]
    per=[]; messages=[]
    for index, item in enumerate(out):
        if not isinstance(item,dict):raise ValueError('Output item is not an object')
        added=[e.get('item',{}) for e in raw_events if e.get('type')=='response.output_item.added' and e.get('output_index')==index]
        done=[e.get('item',{}) for e in raw_events if e.get('type')=='response.output_item.done' and e.get('output_index')==index]
        ident=item.get('id'); ids={'added':[x.get('id') for x in added],'done':[x.get('id') for x in done],'terminal':ident}
        consistent=bool(ident) and len(added)==1 and len(done)==1 and ids['added']==[ident] and ids['done']==[ident]
        row={'output_index':index,'type':item.get('type'),'ids':ids,'id_consistent':consistent,
             'event_types_consistent':all(x.get('type')==item.get('type') for x in added+done)}
        if item.get('type')=='message':
            parts=item.get('content',[])
            contents=[p.get('text') for p in parts if p.get('type')=='output_text' and isinstance(p.get('text'),str)]
            final=''.join(contents)
            deltas=''.join(e.get('delta','') for e in raw_events if e.get('type')=='response.output_text.delta' and e.get('item_id')==ident and e.get('output_index')==index)
            dones=[e.get('text') for e in raw_events if e.get('type')=='response.output_text.done' and e.get('item_id')==ident and e.get('output_index')==index]
            part_dones=[e.get('part',{}).get('text') for e in raw_events if e.get('type')=='response.content_part.done' and e.get('item_id')==ident and e.get('output_index')==index and e.get('part',{}).get('type')=='output_text']
            row['text_consistent']=(len(contents)==1 and deltas==final and dones==[final] and part_dones==[final])
            row['text_sha256']=hashlib.sha256(final.encode()).hexdigest()
            messages.append(final)
        per.append(row)
    candidate = messages[0] if len(messages)==1 else None
    try:
        parsed=pre.strict_json(candidate) if candidate is not None else None
        follows=parsed==EXPECT
    except (ValueError,TypeError):
        follows=False
    usage=response.get('usage'); usage_ok=(isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens','total_tokens')) and usage['input_tokens']+usage['output_tokens']==usage['total_tokens'])
    raw_sdk_equal=raw_events==sdk_events
    structure=(raw_sdk_equal and bool(per) and all(x['id_consistent'] and x['event_types_consistent'] and (x['type']!='message' or x['text_consistent']) for x in per))
    return {'communication':'COMPLETE_RESPONSE_RECEIVED','response_id':rid,'returned_model':response['model'],
        'terminal_event':'response.completed','response_status':response['status'],
        'usage':usage if usage_ok else 'UNKNOWN','usage_valid':usage_ok,
        'raw_event_count':len(raw_events),'sdk_event_count':len(sdk_events),
        'raw_sdk_events_exact_equal':raw_sdk_equal,
        'raw_sdk_mismatch_count':sum(a!=b for a,b in zip(raw_events,sdk_events))+abs(len(raw_events)-len(sdk_events)),
        'output_type_counts':dict(Counter(x.get('type') for x in out)), 'items':per,
        'stream_structure_consistent':structure,
        'exact_literal_output':candidate=='{"probe":"INST_OK_4826"}',
        'instructions_followed_json_object':follows,'message_text':candidate,
        'actual_fee':'UNKNOWN','event_type_counts':dict(Counter(event_types))}


def run_one(root, label, cap, *, api_key, mock_transport=None):
    """Single attempt only. Existing directories always refuse reissuance."""
    if label not in ('system','user','json','none') or cap != 2048: raise ValueError('Unapproved diagnostic')
    request=wire(cap); fp=value_hash(request)
    if api_key in json.dumps(request,ensure_ascii=False):raise ValueError('Credential in request')
    attempt=root/label;attempt.mkdir(parents=True,exist_ok=False)
    put(attempt/'request.json',request)
    put(attempt/'reservation.json',{'identity':label,'wire_fingerprint':fp,'attempt_limit':1,'retry_allowed':False})
    state={'calls':0,'http_status':None,'encoding':''};traces=[];sdk_events=[];started=time.monotonic()
    def trace(event,info):traces.append(event)
    def req_hook(req):
        state['calls']+=1
        if state['calls']!=1 or str(req.url)!=c1.BASE_URL+'/responses' or json.loads(req.content)!=request:
            raise ValueError('Wire mismatch or repeated dispatch')
        req.extensions['trace']=trace
        with (attempt/'request.body.bin').open('xb') as f:f.write(req.content);f.flush();os.fsync(f.fileno())
        put(attempt/'request-metadata.json',{'url':str(req.url),'method':req.method,
            'headers':{k:req.headers[k] for k in ('user-agent','accept','content-type','accept-encoding') if k in req.headers},
            'dispatch_intent_utc_epoch':time.time(),'proxy':None,'trust_env':False,'sdk_retries':0,'transport_retries':0})
    def res_hook(res):
        state['http_status']=res.status_code;state['encoding']=res.headers.get('content-encoding','').lower()
        stream=res.extensions.get('network_stream');peer=None
        if stream is not None:
            try:peer=stream.get_extra_info('server_addr')
            except (AttributeError,TypeError):pass
        put(attempt/'http.json',{'status':res.status_code,'tcp_peer':peer,'public_egress_ip':'UNKNOWN',
            'headers':{k:res.headers[k] for k in ('content-type','content-encoding','x-request-id','cf-ray','retry-after') if k in res.headers}})
        if res.is_stream_consumed:raise ValueError('Response consumed before archival')
        res.stream=transport.RawTee(res.stream,attempt/'response.body.raw')
    result={'communication':'EXECUTION_UNKNOWN','usage':'UNKNOWN','retry_allowed':False,'actual_fee':'UNKNOWN'}
    try:
        underlying=mock_transport if mock_transport is not None else httpx.HTTPTransport(trust_env=False,retries=0,limits=httpx.Limits(max_connections=1,max_keepalive_connections=1))
        with httpx.Client(transport=underlying,trust_env=False,follow_redirects=False,
                timeout=httpx.Timeout(180,connect=15),event_hooks={'request':[req_hook],'response':[res_hook]}) as hc:
            with openai.OpenAI(base_url=c1.BASE_URL,api_key=api_key,max_retries=0,timeout=180,http_client=hc) as client:
                with client.responses.create(**request) as stream:
                    with (attempt/'sdk-events.jsonl').open('x',encoding='utf8') as archive:
                        for event in stream:
                            value=event.model_dump(mode='json',exclude_unset=True);sdk_events.append(value)
                            archive.write(json.dumps(value,ensure_ascii=False)+'\n');archive.flush();os.fsync(archive.fileno())
                            if time.monotonic()-started>180:raise TimeoutError('Wall-clock deadline')
        raw=transport.decoded_body((attempt/'response.body.raw').read_bytes(),state['encoding'])
        result=compare(c1.sse_events(raw),sdk_events) | {'retry_allowed':False}
    except Exception as exc:
        result['diagnostics']=transport.diagnostics(exc)
        if state['http_status'] is not None and state['http_status']>=400:result['communication']='HTTP_REJECTED'
        elif transport.pre_send_failure(traces):result['communication']='NOT_SENT'
        elif (attempt/'response.body.raw').exists():result['communication']='STREAM_OR_TERMINAL_UNCERTAIN'
    finally:
        result.update(identity=label,cap=cap,http_status=state['http_status'],calls=state['calls'],
                      elapsed_seconds=round(time.monotonic()-started,3))
        put(attempt/'trace.json',{'events':traces,'exception_text_omitted':True})
        result['artifacts']={p.name:sha(p) for p in attempt.iterdir() if p.is_file()}
        put(attempt/'result.json',result)
    return result


def main():
    global VARIANT
    for root in (FORMAL, REPAIR, Path('data/retrieval/p05-navi-f2-instructions-diagnostic-20261028-r1'), Path('data/retrieval/p05-navi-f2-metro-diagnostic-20261028-r1')):
        verify_seal(root)
    env=environment()
    if not env.get('JIZHI_API_KEY') or env.get('JIZHI_BASE_URL','').rstrip('/') != c1.BASE_URL: raise ValueError('configuration')
    root=Path('data/retrieval/p05-navi-f2-jizhi-troubleshoot-none-20261028-r1')
    root.mkdir(parents=True,exist_ok=False)
    put(root/'plan.json', {'max_calls':1,'authorized_max':5,'previous_calls':3,'variants':['none'], 'limit':2048,'retries':0,'formal_slots':0,'purpose':'independent diagnosis'})
    for variant in ('none',):
        VARIANT=variant
        print(json.dumps({'dispatch':variant}),flush=True)
        result=run_one(root,variant,2048,api_key=env['JIZHI_API_KEY'])
        print(json.dumps({k:result.get(k) for k in ('identity','communication','http_status','usage','stream_structure_consistent','instructions_followed_json_object')},ensure_ascii=False),flush=True)
        if result['communication']!='COMPLETE_RESPONSE_RECEIVED' or not result.get('usage_valid'):
            put(root/'STOP.json',{'after':variant,'status':result['communication'],'no_retry':True})
            return
    put(root/'COMPLETE.json',{'calls':1,'retries':0,'formal_slots':0})

if __name__=='__main__':main()
