"""OFFLINE ONLY: candidate wires and non-stream parsing, no dispatch entrypoint.

Vendor documentation and SDK serialization do not certify Metro enforcement.
These drafts never change cards_v2.wire or the production execution defaults.
"""
from copy import deepcopy
import json

from . import cards_v2
from .common import digest, require

BASE_URL='https://tokenmetro.com/v1'
PROFILES=('ds-stream-none','ds-stream-low','ds-sync-none','ds-sync-schema-none',
          'qwen-sync-none','qwen-sync-schema-none','qwen-sync-budget')


def draft(view, profile):
    require(profile in PROFILES,'Unknown explicit parameter draft')
    baseline=cards_v2.wire(view)
    if profile.startswith('ds-'):
        args=deepcopy(baseline)
        args['stream']=profile.startswith('ds-stream-')
        args['reasoning']={'effort':'low' if profile.endswith('-low') else 'none'}
        surface='responses'
        if '-schema-' in profile:
            args['text']['format']={'type':'json_schema','name':'navi_card_v2',
                                   'schema':deepcopy(cards_v2.SCHEMA)}
    else:
        surface='chat_completions'
        args={'model':'qwen3.8-flash','stream':False,'max_completion_tokens':8192,
              'messages':[{'role':'user','content':baseline['input'][0]['content'][0]['text']}],
              'response_format':{'type':'json_object'},'extra_body':{'enable_thinking':False}}
        if '-schema-' in profile:
            args['response_format']={'type':'json_schema','json_schema':{
                'name':'navi_card_v2','strict':True,'schema':deepcopy(cards_v2.SCHEMA)}}
        if profile.endswith('-budget'):
            args['extra_body']={'enable_thinking':True,'thinking_budget':1024}
    actual={k:v for k,v in args.items() if k!='extra_body'}|args.get('extra_body',{})
    path='/responses' if surface=='responses' else '/chat/completions'
    return {'version':'metro-card-parameter-draft-1','profile':profile,'route':'tokenmetro',
            'api_surface':surface,'url':BASE_URL+path,'sdk_kwargs':args,'expected_actual_body':actual,
            'fingerprint':digest(['tokenmetro',surface,BASE_URL,actual]),
            'source_view_sha256':digest(view),'schema_sha256':digest(cards_v2.SCHEMA),
            'support':'VENDOR_DOCUMENTED_METRO_PARAMETER_ENFORCEMENT_UNVERIFIED',
            'send_authorized':False,'production_default_changed':False}


def parse_nonstream(raw, sdk_object, plan, view, evidence):
    """Pure offline prototype. Never supplies a CONTENT_VERIFIED job/value file."""
    require(plan['send_authorized'] is False,'Draft cannot authorize execution')
    require(plan['sdk_kwargs']['stream'] is False,'Non-stream parser cannot replace SSE checks')
    require(plan['source_view_sha256']==digest(view),'Draft/source mismatch')
    response=json.loads(raw)
    require(response==sdk_object,'Raw and SDK object differ')
    require(response.get('model')==plan['sdk_kwargs']['model'],'Returned model differs')
    reasons=[]
    if plan['api_surface']=='responses':
        require(response.get('object')=='response' and response.get('id') and response.get('status')=='completed',
                'Invalid Responses completion')
        require(not response.get('error') and not response.get('incomplete_details'),'Incomplete/failed response')
        messages=[];ids=[]
        for item in response.get('output',[]):
            require(item.get('id') and item['id'] not in ids,'Duplicate/missing output identity');ids.append(item['id'])
            if item['type']=='reasoning':
                reasons += [c['text'] for c in item.get('summary',[]) if c.get('type')=='summary_text']
                reasons += [c['text'] for c in item.get('content',[]) if c.get('type')=='reasoning_text']
            else:
                require(item['type']=='message' and item.get('role')=='assistant' and item.get('status')=='completed',
                        'Unsupported output item')
                content=item['content']
                require(len(content)==1 and content[0]['type']=='output_text','Unsupported message content')
                messages.append(content[0]['text'])
        require(len(messages)==1,'Missing/ambiguous final message');text=messages[0]
        usage=response.get('usage',{});normalized=usage
    else:
        require(response.get('object')=='chat.completion' and response.get('id'),'Invalid Chat object')
        choices=response.get('choices',[])
        require(len(choices)==1 and choices[0].get('index')==0 and choices[0].get('finish_reason')=='stop',
                'Chat truncated/ambiguous/not stopped')
        message=choices[0]['message']
        require(message.get('role')=='assistant' and not message.get('tool_calls') and not message.get('refusal'),
                'Unsupported Chat message')
        text=message.get('content');require(isinstance(text,str),'No final Chat text')
        reason=message.get('reasoning_content')
        require(reason is None or isinstance(reason,str),'Unsupported reasoning shape')
        if reason:reasons.append(reason)
        usage=response.get('usage',{})
        normalized={'input_tokens':usage.get('prompt_tokens'),'output_tokens':usage.get('completion_tokens'),
                    'total_tokens':usage.get('total_tokens')}
        if 'prompt_tokens_details' in usage:normalized['input_tokens_details']=usage['prompt_tokens_details']
        if 'completion_tokens_details' in usage:normalized['output_tokens_details']=usage['completion_tokens_details']
    from .requests import old
    require(old.valid_usage(normalized,8192),'Invalid/over-cap provider counters')
    reason_tokens=normalized.get('output_tokens_details') or {}
    no_thinking=(plan['sdk_kwargs'].get('reasoning',{}).get('effort')=='none' or
                 plan['sdk_kwargs'].get('extra_body',{}).get('enable_thinking') is False)
    require(not no_thinking or (not reasons and not reason_tokens.get('reasoning_tokens',0)),
            'Observed reasoning contradicts requested disabled thinking')
    require(not reasons or text!=''.join(reasons),'Reasoning duplicated into final')
    value=cards_v2.validate(json.loads(text),view,evidence)
    return {'authority':'OFFLINE_NONSTREAM_PARSE_PROTOTYPE_NOT_PRODUCTION_ACCEPTANCE',
            'provider_reported_usage':usage,'normalized_usage':normalized,'reasoning_text':'\n'.join(reasons),
            'final_text':text,'value':value,'protocol_validation':'PASS',
            'stream_validation':'NOT_APPLICABLE_NONSTREAM_NO_SSE_ID_CONSISTENCY_CLAIM',
            'content_validation':'PASS','navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED'}
