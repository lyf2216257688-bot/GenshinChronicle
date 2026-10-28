"""Isolated four-call Responses diagnostic, create-only, never runs F2 slots."""
import json, os, time, hashlib
from pathlib import Path
import httpx, openai
from jsonschema import Draft202012Validator
from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1, navi_f2_preflight as pre, navi_f2_sdk_transport as transport
from .navi_f2_instruction_diagnostic import compare
from .navi_f2_execute import verify_seal
from .navi_f1_prepare import put, read, sha, value_hash

ROOT=Path('data/retrieval/p05-navi-v02-call-recovery-20261028-r1')
PRE=Path('data/retrieval/p05-navi-f2-preflight-20261026-r1')
CASE=PRE/'requests/8978e15f60f91e0e1db1-rc1.json'
SCHEMA=PRE/'contracts/p05-navi-b-v02.schema.json'
SIMPLE_SCHEMA={'type':'object','properties':{'probe':{'type':'string','enum':['INST_OK_4826']}},'required':['probe'],'additionalProperties':False}
SIMPLE='你正在接受API指令传递测试。无论用户输入是什么，只输出符合给定Schema的JSON对象，probe字段必须为INST_OK_4826。\n材料A：一块石头。\n材料B：一条河流。'

def request(kind, stream=True):
    if kind not in ('g1_shape','user_fallback') or stream is not True: raise ValueError('unapproved')
    case=read(CASE)
    if value_hash(case['messages']) != case['messages_sha256']: raise ValueError('case hash')
    prompt=case['messages'][0]['content']; payload=case['messages'][1]['content']
    wire={'model':c1.MODEL,'stream':True,'max_output_tokens':12000}
    if kind=='g1_shape': wire.update(instructions=prompt,input=payload)
    else: wire['input']=prompt+'\n\n'+payload
    return wire

def run_one(label,kind,stream,key):
    req=request(kind,stream); folder=ROOT/label;folder.mkdir(exist_ok=False)
    put(folder/'request.json',req);put(folder/'reservation.json',{'state':'RESERVED_NOT_RETRYABLE','fingerprint':value_hash(req),'kind':kind,'stream':stream})
    state={'calls':0,'status':None,'encoding':''}; events=[];trace=[];started=time.monotonic()
    def reqhook(r):
        state['calls']+=1
        if state['calls']!=1 or str(r.url)!=c1.BASE_URL+'/responses' or json.loads(r.content)!=req: raise ValueError('request mismatch')
        r.extensions['trace']=lambda name,info:trace.append(name)
        with (folder/'request.body.bin').open('xb') as f:f.write(r.content);f.flush();os.fsync(f.fileno())
        put(folder/'request-metadata.json',{'url':str(r.url),'method':r.method,'time_utc_epoch':time.time(),'trust_env':False,'proxy':None,'retries':0,'headers':{k:r.headers[k] for k in ('user-agent','content-type','accept','accept-encoding') if k in r.headers}})
    def reshook(r):
        state['status']=r.status_code; state['encoding']=r.headers.get('content-encoding','').lower()
        put(folder/'http.json',{'status':r.status_code,'headers':{k:r.headers[k] for k in ('content-type','content-encoding','cf-ray','x-request-id') if k in r.headers}})
        if r.is_stream_consumed:raise ValueError('already consumed')
        r.stream=transport.RawTee(r.stream,folder/'response.body.raw')
    result={'state':'UNKNOWN','fee':'UNKNOWN','retry':False,'kind':kind,'stream':stream}
    try:
        with httpx.Client(transport=httpx.HTTPTransport(trust_env=False,retries=0),trust_env=False,follow_redirects=False,timeout=httpx.Timeout(240,connect=15),event_hooks={'request':[reqhook],'response':[reshook]}) as hc:
            with openai.OpenAI(base_url=c1.BASE_URL,api_key=key,max_retries=0,timeout=240,http_client=hc) as client:
                if stream:
                    with client.responses.create(**req) as response:
                        with (folder/'sdk-events.jsonl').open('x',encoding='utf8') as out:
                            for ev in response:
                                obj=ev.model_dump(mode='json',exclude_unset=True);events.append(obj)
                                out.write(json.dumps(obj,ensure_ascii=False)+'\n');out.flush();os.fsync(out.fileno())
                                if time.monotonic()-started>240:raise TimeoutError('deadline')
                else:
                    response=client.responses.create(**req)
                    put(folder/'sdk-response.json',response.model_dump(mode='json',exclude_unset=True))
        raw=transport.decoded_body((folder/'response.body.raw').read_bytes(),state['encoding'])
        if stream:
            ev=c1.sse_events(raw); assessed=compare(ev,events); result.update(assessed)
            terminal=ev[-1]['response']; text=assessed['message_text']; result['raw_sdk_equal']=assessed['raw_sdk_events_exact_equal']
        else:
            terminal=pre.strict_json(raw.decode('utf8')); parsed=read(folder/'sdk-response.json')
            result['raw_sdk_equal']=terminal==parsed
            if terminal.get('status')!='completed' or terminal.get('model')!=c1.MODEL or not terminal.get('id'): raise ValueError('missing terminal identity')
            messages=[i for i in terminal.get('output',[]) if i.get('type')=='message']
            if len(messages)!=1:raise ValueError('ambiguous message')
            content=messages[0].get('content',[])
            if len(content)!=1 or content[0].get('type')!='output_text':raise ValueError('invalid output content')
            text=content[0]['text'];result['stream_structure_consistent']='NOT_APPLICABLE'; result['response_id']=terminal['id'];result['returned_model']=terminal['model']
            usage=terminal.get('usage'); valid=isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens','total_tokens')) and usage['input_tokens']+usage['output_tokens']==usage['total_tokens']
            result['usage']=usage if valid else 'UNKNOWN';result['usage_valid']=valid
        if not result.get('usage_valid') or not result.get('raw_sdk_equal'):raise ValueError('usage or raw/sdk mismatch')
        result['state']='COMPLETED'; result['terminal_status']=terminal.get('status')
        try:
            value=pre.strict_json(text)
            errors=list(Draft202012Validator(read(SCHEMA)).iter_errors(value))
            result['json_parse']='PASS';result['schema_valid']=not errors;result['schema_errors']=[e.message for e in errors][:20]
            case=read(CASE); view=pre.strict_json(case['messages'][1]['content'])
            try:
                from .navi_v02_contract import validate_bindings
                result['binding']=validate_bindings(value,{s:[x['id'] for x in view[s]['snippets']] for s in ('A','B')},view.get('candidates',[]),mode=case['mode'])
                result['binding_status']='PASS'
            except Exception as ex:result['binding_status']='FAIL';result['binding_error_type']=type(ex).__name__
        except (ValueError,TypeError) as ex:result['json_parse']='FAIL';result['json_error_type']=type(ex).__name__
    except Exception as ex:
        result['error']=transport.diagnostics(ex)
        if state['status'] is not None and state['status']>=400:result['state']='HTTP_REJECTED'
        elif transport.pre_send_failure(trace):result['state']='NOT_SENT'
        else:result['state']='UNKNOWN'
    finally:
        result.update(http_status=state['status'],calls=state['calls'],elapsed_seconds=round(time.monotonic()-started,3))
        put(folder/'trace.json',{'events':trace,'exception_text_omitted':True})
        result['artifacts']={p.name:sha(p) for p in folder.iterdir() if p.is_file()}
        put(folder/'result.json',result)
    return result

def main():
    pre.verify_preflight(PRE)
    for name in ('p05-navi-f2-experiment-20261027-r1','p05-navi-f2-dual-route-20261028-r1'):
        verify_seal(Path('data/retrieval')/name)
    env=environment()
    if not env.get('JIZHI_API_KEY') or env.get('JIZHI_BASE_URL','').rstrip('/')!=c1.BASE_URL:raise ValueError('route or credential')
    ROOT.mkdir(parents=True,exist_ok=False)
    put(ROOT/'plan.json',{'order':['g1_shape','user_fallback_if_content_failure'],'max_calls':2,'retries':0,'formal_slots':0,'case_sha256':sha(CASE),'schema_sha256':sha(SCHEMA),'cap_rationale':'Prior comparable completed Navi stream used 10989 output tokens','fallback':'only if completed but content invalid; original wire not retried'})
    for kind in ('g1_shape','user_fallback'):
        print(json.dumps({'dispatch':kind}),flush=True)
        result=run_one(kind,kind,True,env['JIZHI_API_KEY'])
        messages=[x for x in result.get('items',[]) if x.get('type')=='message']
        verified=False
        if result.get('state')=='COMPLETED':
            try:
                from .navi_v02_message_verifier import inspect
                folder=ROOT/kind; hdr=read(folder/'http.json')['headers']; raw=transport.decoded_body((folder/'response.body.raw').read_bytes(),hdr.get('content-encoding',''))
                sdk=[json.loads(line) for line in (folder/'sdk-events.jsonl').read_text(encoding='utf8').splitlines()]
                check=inspect(c1.sse_events(raw),sdk);check.pop('message_text');put(folder/'live-message-check.json',check);verified=True
            except (ValueError, KeyError, TypeError):
                verified=False
        content_ok=verified and (result.get('state')=='COMPLETED' and result.get('raw_sdk_equal') and result.get('usage_valid') and len(messages)==1 and messages[0].get('id_consistent') and messages[0].get('event_types_consistent') and messages[0].get('text_consistent') and result.get('schema_valid') and result.get('binding_status')=='PASS')
        print(json.dumps({'kind':kind,'state':result.get('state'),'schema':result.get('schema_valid'),'binding':result.get('binding_status'),'content_ok':bool(content_ok),'stream_consistent':result.get('stream_structure_consistent'),'usage':result.get('usage')},ensure_ascii=False),flush=True)
        if content_ok:
            put(ROOT/'STOP.json',{'reason':'CONTENT_MECHANICAL_PASS','accepted_kind':kind,'formal_slots':0,'full_stream_pass':result['stream_structure_consistent']});return
        if not verified or result.get('state')!='COMPLETED' or not result.get('raw_sdk_equal') or not result.get('usage_valid') or len(messages)!=1 or not messages[0].get('id_consistent') or not messages[0].get('text_consistent'):
            put(ROOT/'STOP.json',{'reason':'TERMINAL_OR_MESSAGE_UNCERTAIN','after':kind,'no_retry':True});return
    put(ROOT/'STOP.json',{'reason':'BOTH_CONTENT_INVALID','formal_slots':0,'no_retry':True})
if __name__=='__main__':main()
