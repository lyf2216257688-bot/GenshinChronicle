"""Versioned Card generator: structured Qwen Chat, explicit attribution and aliases.

The internal Card v2 contract remains unchanged. Aliases only shorten wire identities.
"""
from copy import deepcopy
import json
from . import cards, cards_v2 as v2, requests
from .common import digest, require

STRATEGY='qwen-card-structured-1'
DEEPSEEK='deepseek-card-structured-1'
STRATEGIES={STRATEGY,DEEPSEEK}
PROMPT=v2.PROMPT+'''
输出完整Schema定义的对象。interpretations、hypotheses、open_questions、queries的每一项
都必须是{"text":"内容","evidence_ids":["E001"]}对象，绝不是字符串数组。
原文引文必须逐字拷贝，不输出start/end。subject/action/object与引文的语法主体对应，
特别检查代词：比较句“甲与他当年一样”不等于“甲当年做了同样行动”；假设中的孩子
不是确有其人。无法解消指代时保留备选解释和原因，不把同一行动同时归给多个人。
overview也必须遵守上述归属规则，不加入原文没有的行动。假说必须指出支持线索、
缺失证据或反证；不要仅因人物相邻出现就推测身份同一。
不要以减少事实数量来规避理解。按资料复杂度覆盖人物、关键行动、时间关系、矛盾和
具体开放线索；简短文本无需凑数，长篇必须综合其故事推进和不同人物处境。
结构化语音和分支对话保留说话者及路径条件，不把任务目标、可选分支都当成已经发生。
合成模式必须综合子卡的内容关系，指出继承及未读范围，不声称重新直接阅读全文。
'''


def aliases(view):
    ids=[e['id'] for e in view['evidence']]
    for c in view['children']:
        for field in cards.FIELDS:
            for item in c[field]:ids+=item['evidence_ids']
    mapping={eid:f'E{i+1:03d}' for i,eid in enumerate(dict.fromkeys(ids))}
    def convert(x):
        if isinstance(x,list):return [convert(v) for v in x]
        if not isinstance(x,dict):return x
        return {k:([mapping[v] for v in value] if k=='evidence_ids' else
                   mapping.get(value,value) if k in {'id','evidence_id'} and isinstance(value,str) else convert(value))
                for k,value in x.items()}
    return convert(view),mapping


def wire(view,strategy=STRATEGY):
    model_view,_=aliases(view)
    require(strategy in STRATEGIES,'Unknown structured Card strategy')
    if strategy==DEEPSEEK:
        return {'model':'deepseek-v4.1-flash','stream':False,'max_tokens':12288,
            'messages':[{'role':'system','content':PROMPT+'\n完整JSON Schema:\n'+json.dumps(v2.SCHEMA,ensure_ascii=False)+
                         '\n再次检查：所有语义数组的项均为对象，evidence_ids不得省略。'},
                        {'role':'user','content':json.dumps(model_view,ensure_ascii=False)}],
            'response_format':{'type':'json_object'},'extra_body':{'thinking':{'type':'disabled'}}}
    return {'model':'qwen3.8-flash','stream':False,'max_completion_tokens':12288,
            'messages':[{'role':'system','content':PROMPT},
                        {'role':'user','content':json.dumps(model_view,ensure_ascii=False)}],
            'response_format':{'type':'json_schema','json_schema':{'name':'navi_card_v2','strict':True,'schema':deepcopy(v2.SCHEMA)}},
            'extra_body':{'enable_thinking':False}}


def validate_value(value,view,evidence):
    # No guessed source bindings, string-to-object conversion, or semantic rewriting.
    cards.Draft202012Validator(v2.SCHEMA).validate(value)
    _,mapping=aliases(view);back={v:k for k,v in mapping.items()}
    result=deepcopy(value)
    def resolve(eid):
        require(eid in back,'Unknown Card wire source alias');return back[eid]
    result['read_ids']=[resolve(eid) for eid in result['read_ids']]
    for field in cards.FIELDS:
        for item in result[field]:
            item['evidence_ids']=[resolve(eid) for eid in item['evidence_ids']]
            for a in item.get('anchors',[]):a['evidence_id']=resolve(a['evidence_id'])
    return v2.validate(result,view,evidence)


def decode(text,view,evidence):
    """Repair only non-JSON punctuation outside strings; retain every prose byte.

    Observation citations are the verified quotations, not uncited context IDs.
    Removed context declarations remain in the conversion log and raw response.
    """
    edits=[]
    try:value=requests.candidate.strict_json(text)
    except ValueError:
        out=[];inside=False;escape=False;i=0
        while i<len(text):
            ch=text[i]
            if inside:
                out.append(ch)
                if escape:escape=False
                elif ch=='\\':escape=True
                elif ch=='"':inside=False
            else:
                if text[i:i+2]=="\\'" and out and out[-1] in ']}':
                    edits.append({'kind':'NON_JSON_PUNCTUATION_OUTSIDE_STRINGS','offset':i,'removed':text[i:i+2]});i+=2;continue
                if ch=="'" and out and out[-1] in ']}':
                    edits.append({'kind':'NON_JSON_PUNCTUATION_OUTSIDE_STRINGS','offset':i,'removed':ch});i+=1;continue
                out.append(ch)
                if ch=='"':inside=True
            i+=1
        require(bool(edits),'No safe deterministic JSON repair')
        value=requests.candidate.strict_json(''.join(out))
    cards.Draft202012Validator(v2.SCHEMA).validate(value)
    model_view,mapping=aliases(view);bound={mapping[e['id']]:e for e in evidence if e['id'] in mapping}
    for i,item in enumerate(value['source_observations']):
        for a in item['anchors']:
            require(a['evidence_id'] in bound,'Unknown quote source')
            source=bound[a['evidence_id']]['text'];quote=a['quote']
            if quote not in source and any(x in quote for x in ('\\n','\\r')):
                # JSON may contain a doubly escaped line ending. Only the cited,
                # delivered fragment can authorize this representation conversion.
                converted=quote.replace('\\r\\n','\r\n').replace('\\n','\n').replace('\\r','\r')
                variants={converted,converted.replace('\r\n','\n').replace('\r','\n')}
                matches=[q for q in variants if q != quote and source.count(q)==1]
                require(len(matches)==1,'Escaped newline quote is not uniquely verified in bound source')
                corrected=matches[0]
                edits.append({'kind':'EXACT_UNIQUE_BOUND_QUOTE_NEWLINE_DECODE',
                              'observation':i,'evidence_alias':a['evidence_id'],
                              'original_quote':quote,'verbatim_quote':corrected,
                              'fragment_offset':source.index(corrected)})
                a['quote']=quote=corrected
            if quote not in source:
                matches=[eid for eid,e in bound.items() if e['text'].count(quote)==1]
                if len(matches)==1:
                    require(len({e.get('source_id') for e in bound.values()})==1,'Never rebind across source documents')
                    edits.append({'kind':'EXACT_UNIQUE_QUOTE_SAME_DOCUMENT_REBIND','observation':i,
                                  'model_alias':a['evidence_id'],'verified_alias':matches[0],'quote':quote})
                    a['evidence_id']=matches[0]
                else:
                    # Model sometimes repeats a known speaker label for a later sentence
                    # in that speaker's same line. Remove only that non-verbatim label.
                    prefix,sep,body=quote.partition('：')
                    require(sep and 0<len(prefix)<=20 and body and source.count(body)==1,'Quotation not in delivered source')
                    pos=source.index(body);line=source[source.rfind('\n',0,pos)+1:pos]
                    require(line.startswith(prefix+'：'),'Speaker label not verified on same source line')
                    edits.append({'kind':'SAME_LINE_SPEAKER_LABEL_REMOVED_FROM_QUOTE','observation':i,
                                  'original_quote':quote,'verbatim_quote':body,'verified_speaker':prefix})
                    a['quote']=body
            v2.locate(bound[a['evidence_id']]['text'],a['quote'])
        quoted=list(dict.fromkeys(a['evidence_id'] for a in item['anchors']))
        extra=[e for e in item['evidence_ids'] if e not in quoted]
        if extra or set(quoted)!=set(item['evidence_ids']):
            require(all(e in bound for e in extra),'Unknown context source')
            edits.append({'kind':'UNQUOTED_CONTEXT_IDS_RETAINED_IN_LOG_NOT_EVIDENCE','observation':i,
                          'original':item['evidence_ids'],'quoted_evidence_ids':quoted,'context_only':extra})
            item['evidence_ids']=quoted
    return value,edits


def assess(raw,sdk_object,view,evidence,strategy=STRATEGY):
    """Only accounting arithmetic is a warning; identity/truncation/source remain fatal."""
    obj=requests.candidate.strict_json(raw)
    result={'provider_reported_usage':obj.get('usage','UNKNOWN'),'usage':'UNKNOWN','warnings':[],
            'stream_validation':{'status':'NOT_APPLICABLE_NONSTREAM'},
            'protocol_validation':{'status':'FAIL'},'content_validation':{'status':'FAIL'},
            'reasoning_text':'','final_text':'','error_codes':[]}
    value=None;usage=obj.get('usage') or {}
    counts=[usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
    valid_counts=all(type(x) is int and x>=0 for x in counts)
    if valid_counts:
        result['usage']={'input_tokens':counts[0],'output_tokens':counts[1],'total_tokens':counts[2]}
        result['conservative_accounting_tokens']=max(counts[2],counts[0]+counts[1])
        if counts[0]+counts[1]!=counts[2]:result['warnings'].append('PROVIDER_USAGE_ARITHMETIC_MISMATCH')
    result['provider_usage_arithmetic_valid']=valid_counts and counts[0]+counts[1]==counts[2]
    try:
        require(obj==sdk_object,'RAW_SDK_MISMATCH')
        expected=wire(view,strategy)['model'];reported=obj.get('model')
        allowed={expected}|({'deepseek-v4-1-flash'} if expected=='deepseek-v4.1-flash' else set())
        require(obj.get('object')=='chat.completion' and obj.get('id') and reported in allowed,'MODEL_OR_IDENTITY_MISMATCH')
        result['model_identity']={'requested':expected,'provider_reported':reported,
             'status':'EXACT' if reported==expected else 'OBSERVED_METRO_SPELLING_ALIAS',
             'native_upstream_attestation':'UNKNOWN'}
        if reported!=expected:result['warnings'].append('PROVIDER_MODEL_SPELLING_ALIAS')
        require(not obj.get('error'),'PROVIDER_ERROR')
        choices=obj.get('choices',[]);require(len(choices)==1 and choices[0].get('index')==0,'AMBIGUOUS_CHOICE')
        choice=choices[0];message=choice.get('message',{})
        text=message.get('content');reason=message.get('reasoning_content')
        require(isinstance(text,str) and (reason is None or isinstance(reason,str)),'INVALID_MESSAGE')
        result['final_text']=text;result['reasoning_text']=reason or ''
        require(message.get('role')=='assistant' and not message.get('tool_calls') and not message.get('refusal'),'UNSUPPORTED_MESSAGE')
        require(choice.get('finish_reason')=='stop','OUTPUT_TRUNCATED_OR_INVALID_FINISH')
        details=usage.get('completion_tokens_details') or {}
        require(not reason and not message.get('reasoning') and not message.get('thinking') and not details.get('reasoning_tokens',0),'THINKING_NOT_DISABLED')
        require(valid_counts,'MISSING_OR_INVALID_USAGE')
        require(counts[1]<=12288,'OUTPUT_LIMIT_EXCEEDED')
        result['protocol_validation']={'status':'PASS'}
    except Exception as exc:result['error_codes'].append(str(exc))
    try:
        decoded,edits=decode(result['final_text'],view,evidence)
        result['deterministic_conversions']=edits
        value=validate_value(decoded,view,evidence)
        result['content_validation']={'status':'PASS'}
    except json.JSONDecodeError as exc:result['error_codes'].append('NON_JSON:'+str(exc)[:500])
    except Exception as exc:result['error_codes'].append('CONTENT_INVALID:'+str(exc)[:500])
    return result,value
