"""Metro Card Chat: shared scheduling over raw-first, retry-free SDK attempts.

Does not change Card defaults or historical Responses validators.
"""
import json
from pathlib import Path
import time
import httpx
import openai
from ... import model_calls
from . import cards_v2, requests
from .common import digest, read, require, seal, sha, verify, write


def wire(view):
    baseline=cards_v2.wire(view)
    return {'model':baseline['model'],'stream':False,'max_tokens':8192,
            'messages':[{'role':'user','content':baseline['input'][0]['content'][0]['text']}],
            'response_format':{'type':'json_object'},'extra_body':{'thinking':{'type':'disabled'}}}


def actual_body(args):
    return {k:v for k,v in args.items() if k!='extra_body'}|args.get('extra_body',{})


def fingerprint(args):
    return digest(['tokenmetro','chat_completions',requests.METRO_URL,actual_body(args)])


def assess(raw, sdk_object, view, evidence):
    result={'provider_reported_usage':'UNKNOWN','usage':'UNKNOWN',
            'stream_validation':{'status':'NOT_APPLICABLE_NONSTREAM'},
            'protocol_validation':{'status':'FAIL'},'content_validation':{'status':'FAIL'},
            'error_codes':[],'reasoning_text':'','final_text':''}
    value=None
    try: obj=requests.candidate.strict_json(raw)
    except Exception:
        result['error_codes'].append('NON_JSON_RESPONSE');return result,value
    usage=obj.get('usage') or {}
    result['provider_reported_usage']=usage
    normalized={'input_tokens':usage.get('prompt_tokens'),'output_tokens':usage.get('completion_tokens'),
                'total_tokens':usage.get('total_tokens')}
    arithmetic=requests.old.valid_usage(normalized,normalized.get('output_tokens',-1))
    result['provider_usage_arithmetic_valid']=arithmetic
    if arithmetic: result['usage']=normalized
    output=normalized['output_tokens']
    result['output_limit']={'requested_max_tokens':8192,'provider_reported_output_tokens':output,
                            'status':('PASS' if output<=8192 else 'EXCEEDED')
                            if type(output) is int and output>=0 else 'UNKNOWN'}
    try:
        choices=obj.get('choices',[])
        require(len(choices)==1 and choices[0].get('index')==0,'AMBIGUOUS_CHOICE')
        choice=choices[0];message=choice.get('message',{})
        text=message.get('content');reason=message.get('reasoning_content')
        require(isinstance(text,str) and (reason is None or isinstance(reason,str)),'INVALID_MESSAGE')
        result['final_text']=text;result['reasoning_text']=reason or ''
        details=usage.get('completion_tokens_details') or {}
        result['thinking_validation']={'visible_reasoning_chars':len(reason or ''),
             'reported_reasoning_tokens':details.get('reasoning_tokens','UNKNOWN'),
             'status':'NO_OBSERVED_REASONING' if not reason and not details.get('reasoning_tokens',0)
                      and not message.get('reasoning') and not message.get('thinking') else 'FAIL',
             'boundary':'No visible reasoning does not certify hidden internals'}
        require(obj==sdk_object,'RAW_SDK_MISMATCH')
        require(obj.get('object')=='chat.completion' and bool(obj.get('id')),'INVALID_CHAT_IDENTITY')
        require(obj.get('model')=='deepseek-v4.1-flash','MODEL_MISMATCH')
        require(not obj.get('error'),'PROVIDER_ERROR')
        require(message.get('role')=='assistant' and not message.get('tool_calls') and not message.get('refusal'),
                'UNSUPPORTED_MESSAGE')
        require(choice.get('finish_reason')=='stop','OUTPUT_TRUNCATED_OR_INVALID_FINISH')
        require(arithmetic,'USAGE_INVALID_OR_MISSING')
        require(normalized['output_tokens']<=8192,'OUTPUT_LIMIT_EXCEEDED')
        require(not reason and not details.get('reasoning_tokens',0),'THINKING_DISABLE_NOT_OBSERVED')
        require(not message.get('reasoning') and not message.get('thinking'),'UNSUPPORTED_REASONING_FIELD')
        result['protocol_validation']={'status':'PASS'}
    except Exception as exc: result['error_codes'].append(str(exc))
    try:
        value=requests.validate_content('card',result['final_text'],view,evidence)
        result['content_validation']={'status':'PASS'}
    except Exception as exc:
        result['error_codes'].append('CONTENT_INVALID:'+type(exc).__name__)
    return result,value


def _attempt(root, view, evidence, *, live_authorized=False, api_key=None, transport_override=None, mock=False, strategy=None, _headers=None):
    from . import card_generation
    require(strategy is None or strategy in card_generation.STRATEGIES,'Unknown versioned Card strategy')
    root=Path(root);args=wire(view) if strategy is None else card_generation.wire(view,strategy)
    body=actual_body(args);fp=fingerprint(args)
    plan={'version':'metro-card-chat-1','route':'tokenmetro','api_surface':'chat_completions',
          'wire':args,'actual_body':body,'view':view,'evidence':evidence,'fingerprint':fp,'backend':'mock' if mock else 'live'}
    if strategy:plan.update(stage='card',strategy=strategy,prompt_sha256=digest(card_generation.prompt(strategy)),
                            schema_sha256=digest(card_generation.schema(strategy,view.get('mode','read'),view)))
    if strategy in card_generation.EVIDENCE_STRATEGIES:
        plan['quote_locations']=card_generation.anchors.location_map(view,evidence)
    if (root/'complete.json').exists():
        verify(root);require(read(root/'plan.json')==plan,'Completed Chat identity changed')
        return read(root/'result.json')
    require(root.is_dir() and not any(root.iterdir()),'Attempt must be freshly reserved')
    write(root/'plan.json',plan)
    state={'http_status':None,'calls':0};trace=[];started=time.monotonic()
    result={'state':'UNKNOWN','usage':'UNKNOWN','provider_reported_usage':'UNKNOWN',
            'stream_validation':{'status':'NOT_APPLICABLE_NONSTREAM'},
            'protocol_validation':{'status':'NOT_COMPLETED'},'content_validation':{'status':'NOT_COMPLETED'}}
    def request_hook(req):
        state['calls']+=1
        require(state['calls']==1 and str(req.url)==requests.METRO_URL+'/chat/completions' and req.method=='POST'
                and requests.candidate.strict_json(req.content.decode())==body,'Actual Chat body/route differs')
        req.extensions['trace']=lambda name,info:trace.append(name)
        with (root/'request.body.bin').open('xb') as handle:
            handle.write(req.content);handle.flush();__import__('os').fsync(handle.fileno())
        write(root/'request-metadata.json',{'url':str(req.url),'sdk_retries':0,'transport_retries':0,
                                          'trust_env':False,'redirects':False})
    def response_hook(res):
        state['http_status']=res.status_code
        write(root/'http.json',{'status':res.status_code,'headers':{k:res.headers[k] for k in
              ('content-type','content-encoding','x-request-id','retry-after') if k in res.headers}})
        require(not res.is_stream_consumed,'Response consumed before archival')
        res.stream=requests.transport.RawTee(res.stream,root/'response.body.raw')
    sdk_object=None
    try:
        transport=transport_override if mock else httpx.HTTPTransport(trust_env=False,retries=0)
        with httpx.Client(transport=transport,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(900 if strategy in card_generation.STRUCTURED_32K else 180,connect=15),
                          event_hooks={'request':[request_hook],'response':[response_hook]}) as client:
            with openai.OpenAI(base_url=requests.METRO_URL,api_key='offline-mock' if mock else api_key,
                               max_retries=0,http_client=client) as sdk:
                response=sdk.chat.completions.create(**args, extra_headers=_headers or {})
                sdk_object=response.model_dump(mode='json',exclude_unset=True)
                write(root/'sdk-response.json',sdk_object)
    except Exception as exc:
        result['failure_type']=type(exc).__name__
        result['diagnostics']=requests.transport.diagnostics(exc)
    # Parse archived raw even if SDK failed, preserving independently observed counters.
    if (root/'response.body.raw').exists():
        try:
            encoding=read(root/'http.json')['headers'].get('content-encoding','')
            raw=requests.transport.decoded_body((root/'response.body.raw').read_bytes(),encoding)
            records,value=assess(raw,sdk_object,view,evidence) if strategy is None else card_generation.assess(raw,sdk_object,view,evidence,strategy)
            for name in ('reasoning_text','final_text'):
                with (root/(name.replace('_text','')+'.txt')).open('x',encoding='utf8') as handle:handle.write(records.pop(name))
            result.update(records)
            valid=state['http_status']==200 and all(result[k]['status']=='PASS' for k in ('protocol_validation','content_validation'))
            result['state']='CONTENT_VERIFIED' if valid else 'PROTOCOL_OR_CONTENT_INVALID'
            if valid:write(root/'value.json',value)
        except Exception as exc:result.update(state='RESPONSE_INVALID',assessment_failure=type(exc).__name__)
    result.update(**state,elapsed_seconds=round(time.monotonic()-started,3),automatic_retry=False,
                  actual_fee='UNKNOWN',navigation_status='INACTIVE',human_acceptance='NOT_REVIEWED',
                  authority='MOCK_NOT_MODEL_OUTPUT' if mock else 'LIVE_NOT_HUMAN_REVIEWED')
    if strategy in card_generation.STRUCTURED_32K:
        result.setdefault('card_failure','NETWORK_FAILURE' if state['http_status']!=200 else 'PROTOCOL_INVALID')
    write(root/'trace.json',{'events':trace});write(root/'result.json',result);seal(root)
    return result


def execute(root, view, evidence, *, live_authorized=False, api_key=None, transport_override=None,
            mock=False, strategy=None, call_policy=None, call_batch=None, legacy_origin=None):
    """Default shared attempts/recovery; existing archives remain read-only."""
    from . import card_generation
    require(strategy is None or strategy in card_generation.STRATEGIES,'Unknown versioned Card strategy')
    root=Path(root);args=wire(view) if strategy is None else card_generation.wire(view,strategy)
    plan={'version':'metro-card-chat-1','route':'tokenmetro','api_surface':'chat_completions',
          'wire':args,'actual_body':actual_body(args),'view':view,'evidence':evidence,
          'fingerprint':fingerprint(args),'backend':'mock' if mock else 'live'}
    if strategy:plan.update(stage='card',strategy=strategy,prompt_sha256=digest(card_generation.prompt(strategy)),
                            schema_sha256=digest(card_generation.schema(strategy,view.get('mode','read'),view)))
    if strategy in card_generation.EVIDENCE_STRATEGIES:
        plan['quote_locations']=card_generation.anchors.location_map(view,evidence)
    if mock:
        ledger=root.parent/'mock-chat-ledger'
        require((root/'complete.json').exists() or transport_override is not None,'Mock transport missing')
    else:
        ledger=requests.METRO_LEDGER
        require((root/'complete.json').exists() or live_authorized is True and bool(api_key),'Live authorization/credential missing')
        require(transport_override is None,'Live transport override forbidden')
    allowance=len(json.dumps(plan['actual_body'],ensure_ascii=False).encode())+args.get('max_tokens',12288)
    return model_calls.run(root,plan,ledger,
        lambda folder,headers:_attempt(folder,view,evidence,live_authorized=live_authorized,api_key=api_key,
            transport_override=transport_override,mock=mock,strategy=strategy,_headers=headers),
        channel=['tokenmetro','chat_completions',args['model']],policy=call_policy,batch=call_batch,
        token_allowance=allowance,legacy_origin=legacy_origin)
