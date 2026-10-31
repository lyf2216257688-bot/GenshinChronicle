"""Independent provider counters, strict protocol outcome and Card outcome."""
import json
from pathlib import Path
from jsonschema import ValidationError

from .common import read, write


def assess(folder, stage, wire, view, evidence):
    from . import requests
    folder=Path(folder);errors=[];events=[];sdk=[];terminal={};reason='';final='';value=None;terminals=[]
    def error(code,message):errors.append({'code':code,'message':str(message)[:240]})
    try:
        encoding=read(folder/'http.json')['headers'].get('content-encoding','')
        events=requests.old.c1.sse_events(requests.transport.decoded_body((folder/'response.body.raw').read_bytes(),encoding))
        sdk=[requests.candidate.strict_json(l) for l in (folder/'sdk-events.jsonl').read_text(encoding='utf8').splitlines()]
        terminals=[e for e in events if e['type'] in {'response.completed','response.incomplete','response.failed'}]
        if len(terminals)==1:terminal=terminals[0]['response']
        else:error('OTHER_PROTOCOL_ERROR','Missing/duplicate terminal response')
        if events!=sdk:error('RAW_SDK_MISMATCH','Raw and SDK event objects differ')
        if terminals:
            event=terminals[-1]
            if event['type']=='response.completed' and terminal.get('status')!='completed':
                error('TERMINAL_CONTRADICTION','completed event has non-completed status')
            if terminal.get('status')=='incomplete' or terminal.get('incomplete_details',{}):
                error('OUTPUT_TRUNCATED','Provider reports incomplete output')
        for i,item in enumerate(terminal.get('output',[])):
            for kind in ('response.output_item.added','response.output_item.done'):
                ids=[e['item']['id'] for e in events if e['type']==kind and e.get('output_index')==i]
                if ids!=[item.get('id')]:error('OBJECT_ID_DRIFT',f'Output item {i} identity/order differs')
            if item.get('type')=='reasoning':
                reason+=''.join(c.get('text','') for c in item.get('summary',[]))
            elif item.get('type')=='message':
                final+=''.join(c.get('text','') for c in item.get('content',[]) if c.get('type')=='output_text')
        if not terminal:
            # Preserve partial text as observation only, never fabricate a final.
            reason=''.join(e.get('delta','') for e in events if e['type']=='response.reasoning_summary_text.delta')
            final=''.join(e.get('delta','') for e in events if e['type']=='response.output_text.delta')
        if reason and reason==final:error('REASONING_AS_FINAL','Reasoning duplicated into final message')
    except Exception as exc:error('OTHER_PROTOCOL_ERROR',type(exc).__name__)
    usage=terminal.get('usage','UNKNOWN')
    usage_valid=isinstance(usage,dict) and requests.old.valid_usage(usage,usage.get('output_tokens',-1))
    if usage_valid and usage['output_tokens']>wire['max_output_tokens']:
        error('OUTPUT_LIMIT_EXCEEDED','Provider-reported output exceeds requested cap')
    if not usage_valid:error('USAGE_INVALID_OR_MISSING','No valid provider terminal counters')
    try:
        requests.checked_stream(folder,wire['max_output_tokens'])
    except Exception as exc:
        if not errors:error('OTHER_PROTOCOL_ERROR',str(exc))
    stream_errors=list(errors)
    content_errors=[]
    try:value=requests.validate_content(stage,final,view,evidence)
    except json.JSONDecodeError:content_errors.append({'code':'NON_JSON','message':'Final message is not strict JSON'})
    except ValidationError as exc:content_errors.append({'code':'SCHEMA_INVALID','message':'Schema path: '+'.'.join(map(str,exc.absolute_path))})
    except ValueError as exc:content_errors.append({'code':'SOURCE_OR_CONTENT_CONTRACT_INVALID','message':str(exc)[:200]})
    except Exception as exc:content_errors.append({'code':'CONTENT_ERROR','message':type(exc).__name__})
    for name,text in (('reasoning.txt',reason),('final.txt',final)):
        if events and not (folder/name).exists():
            with (folder/name).open('x',encoding='utf8') as handle:handle.write(text)
    records={'provider_reported_usage':usage,'provider_usage_arithmetic_valid':usage_valid,
             'provider_usage_observations':[{'event':e['type'],'response_id':e['response'].get('id'),
                                            'usage':e['response'].get('usage','UNKNOWN')} for e in terminals],
             'usage_basis':'PROVIDER_TERMINAL_OBSERVATION_NOT_INVOICE',
             'stream_validation':{'status':'FAIL' if stream_errors else 'PASS','errors':stream_errors,
                                  'raw_sdk_events_equal':bool(events) and events==sdk},
             'content_validation':{'status':'FAIL' if content_errors else 'PASS','errors':content_errors},
             'error_codes':sorted({e['code'] for e in errors+content_errors}),
             'output_limit':{'requested_max_output_tokens':wire['max_output_tokens'],
                             'status':'EXCEEDED' if usage_valid and usage['output_tokens']>wire['max_output_tokens'] else 'PASS' if usage_valid else 'UNKNOWN'}}
    return records,value
