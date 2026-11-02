"""Versioned Card generator: structured Qwen Chat, explicit attribution and aliases.

The internal Card v2 contract remains unchanged. Aliases only shorten wire identities.
"""
from copy import deepcopy
import json
from . import cards, cards_v2 as v2, requests, anchors
from .common import digest, require

STRATEGY='qwen-card-structured-1'
DEEPSEEK='deepseek-card-structured-1'
DEEPSEEK_32K='deepseek-card-structured-32k-20261031'
EVIDENCE_32K='deepseek-card-evidence-32k-20261031'
BOUNDED='deepseek-card-bounded-evidence-20261101'
SINGLE='deepseek-card-single-segment-20261101'
SEMANTIC='deepseek-card-evidence-judgment-20261102'
ATOMIC_STRATEGIES={SINGLE,SEMANTIC}
BOUNDED_STRATEGIES={BOUNDED,*ATOMIC_STRATEGIES}
EVIDENCE_STRATEGIES={EVIDENCE_32K,*BOUNDED_STRATEGIES}
DEFAULT_STRATEGY=EVIDENCE_32K
STRUCTURED_32K={DEEPSEEK_32K,*EVIDENCE_STRATEGIES}
STRATEGIES={STRATEGY,DEEPSEEK,*STRUCTURED_32K}
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


def quote_contract(strategy):
    from .card_contract import CONTRACT,SINGLE_CONTRACT
    require(strategy in EVIDENCE_STRATEGIES,'Unsupported evidence contract')
    return SINGLE_CONTRACT if strategy in ATOMIC_STRATEGIES else CONTRACT if strategy==BOUNDED else anchors.CONTRACT


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


def prompt(strategy):
    if strategy in BOUNDED_STRATEGIES:
        from .card_contract import PROMPT as bounded_prompt, SINGLE_PROMPT
        if strategy in ATOMIC_STRATEGIES:bounded_prompt=SINGLE_PROMPT
        base=PROMPT.replace('每项观察至少一个anchors={evidence_id,quote}；quote逐字来自交付原文或子卡的原文引证，\n不计算字符位置。','每项观察至少一个精确证据选择；原文由程序提取，不输出引文或字符位置。')
        base=base.replace('每条语义项绑定evidence_ids。','每条语义项绑定精确anchors。')
        base=base.replace('都必须是{"text":"内容","evidence_ids":["E001"]}对象，绝不是字符串数组。','都必须是{"text":"内容","anchors":[证据选择]}对象，绝不是字符串数组。')
        base=base.replace('原文引文必须逐字拷贝，不输出start/end。','原文由程序提取，禁止输出quote。')
        if strategy==SEMANTIC:
            from .card_semantics import PROMPT as semantic_prompt
            base=base.replace('合成时不可创造子卡未有的事实或新引文；子卡的解释/假说不是事实证据。',
                '合成时可以据实际交付的原文引证纠正子卡的分类与归属；不得添加未交付事实或新引文。')
            return base+bounded_prompt+semantic_prompt
        return base+bounded_prompt
    base=PROMPT
    if strategy==EVIDENCE_32K:
        base=base.replace('每项观察至少一个anchors={evidence_id,quote}；quote逐字来自交付原文或子卡的原文引证，\n不计算字符位置。',
            '每项观察至少一个证据选择anchors；模型不输出quote或字符偏移。')
        base=base.replace('原文引文必须逐字拷贝，不输出start/end。','原文引文由程序提取，禁止自行抄写或拼接。')
        base+='''\n引文选择契约card-evidence-selection-2：read时anchors每项只填写
{evidence_id,start_segment_id,end_segment_id}，起止片段必须来自所选同一证据。
片段ID是本次请求内唯一的短编号（如S0001），必须从给出的segments逐字复制，
不要自行生成、修改、延长或猜测ID；证据编号必须与该片段所属证据匹配。
选择覆盖起止片段及中间全部原文；不连续证据须使用多个独立anchors，不能拼接引用。
synthesis时anchors每项只填写{anchor_id}，从inherited_anchors选择已验证子卡锚点。
不得重新截取、改写或创造子卡引文。evidence_ids必须与所选锚点的来源集合一致。
选择准确不等于结论正确；仍检查主体、行动、对象、说话者、叙事立场及证据支持性。
不支持的归属或行动不得当作source_observations输出；未知与解释保留原有边界。'''
    return base + ('''\n只输出最终JSON对象，禁止输出任务规划、草稿或内部推演。
阅读时保留连续事件和分支条件。综合时以跨章节事件推进、人物处境、联系、冲突和未知
形成连贯归纳，不逐项抄写子卡。子卡保留细节和证据，父卡选择关键观察支撑综合；
不必重复子卡的全部观察。章节展示顺序不自动等于事件时序。''' if strategy in STRUCTURED_32K else '')


def schema(strategy, mode='read', view=None):
    if strategy in BOUNDED_STRATEGIES:
        from .card_contract import schema as bounded_schema
        return bounded_schema(view,mode,strategy)
    result=deepcopy(v2.SCHEMA)
    if strategy==EVIDENCE_32K:
        result['properties']['source_observations']['items']['properties']['anchors']['items']=cards.obj(
            {'anchor_id':{'type':'string','minLength':1}} if mode=='synthesis' else
            {k:{'type':'string','minLength':1} for k in ('evidence_id','start_segment_id','end_segment_id')})
    return result


def model_view(view,strategy):
    result=aliases(anchors.selection_view(view) if strategy in EVIDENCE_STRATEGIES else view)[0]
    if strategy in BOUNDED_STRATEGIES:
        result['quote_contract']=quote_contract(strategy)
        # Aliases hide stable occurrence IDs from model output, but distinct source
        # occurrences must still have distinct wire/billing identities.
        result['input_scope_policy']='exact-evidence-occurrence-and-child-identities-1'
        result['input_scope_id']=digest([view['mode'],[q['id'] for q in view['evidence']],
            [c['card_id'] for c in view['children']]])
    if strategy==SEMANTIC:
        from .card_semantics import evidence_first
        result=evidence_first(result)
    return result


def mock(view,strategy):
    if strategy in BOUNDED_STRATEGIES:
        from .card_contract import mock as bounded_mock
        return bounded_mock(view,strategy)
    if strategy!=EVIDENCE_32K:return v2.mock(model_view(view,strategy))
    value=v2.mock(aliases(view)[0]);selected=model_view(view,strategy)
    if selected['mode']=='read' and selected['evidence']:
        q=selected['evidence'][0];ss=q['segments']
        value['source_observations'][0]['anchors']=[{'evidence_id':q['id'],
            'start_segment_id':ss[0]['id'],'end_segment_id':ss[-1]['id']}]
    elif selected['mode']=='synthesis':
        value['source_observations']=[deepcopy(x) for c in selected['children'] for x in c['source_observations']]
    return value


def output_cap(strategy):
    require(strategy in STRATEGIES,'Unknown structured Card strategy')
    return 32000 if strategy in STRUCTURED_32K else 12288


def wire(view,strategy=STRATEGY):
    model_input=model_view(view,strategy)
    require(strategy in STRATEGIES,'Unknown structured Card strategy')
    if strategy in {DEEPSEEK,*STRUCTURED_32K}:
        return {'model':'deepseek-v4.1-flash-1017' if strategy in EVIDENCE_STRATEGIES else 'deepseek-v4.1-flash','stream':False,'max_tokens':output_cap(strategy),
            'messages':[{'role':'system','content':prompt(strategy)+'\n完整JSON Schema:\n'+json.dumps(schema(strategy,view.get('mode','read'),view),ensure_ascii=False)+
                         ('\n再次检查：语义项必须有anchors，禁止evidence_ids。' if strategy in BOUNDED_STRATEGIES else '\n再次检查：所有语义数组的项均为对象，evidence_ids不得省略。')},
                        {'role':'user','content':json.dumps(model_input,ensure_ascii=False)}],
            'response_format':{'type':'json_object'},'extra_body':{'thinking':{'type':'disabled'}}}
    return {'model':'qwen3.8-flash','stream':False,'max_completion_tokens':12288,
            'messages':[{'role':'system','content':PROMPT},
                        {'role':'user','content':json.dumps(model_input,ensure_ascii=False)}],
            'response_format':{'type':'json_schema','json_schema':{'name':'navi_card_v2','strict':True,'schema':deepcopy(v2.SCHEMA)}},
            'extra_body':{'enable_thinking':False}}


def validate_value(value,view,evidence,strategy=STRATEGY):
    # No guessed source bindings, string-to-object conversion, or semantic rewriting.
    if strategy in BOUNDED_STRATEGIES:
        from .card_contract import validate
        return validate(value,view,evidence,strategy)
    cards.Draft202012Validator(schema(strategy,view.get('mode','read'),view)).validate(value)
    _,mapping=aliases(view);back={v:k for k,v in mapping.items()}
    result=deepcopy(value)
    def resolve(eid):
        require(eid in back,'Unknown Card wire source alias');return back[eid]
    result['read_ids']=[resolve(eid) for eid in result['read_ids']]
    for field in cards.FIELDS:
        for item in result[field]:
            item['evidence_ids']=[resolve(eid) for eid in item['evidence_ids']]
            for a in item.get('anchors',[]):
                if 'evidence_id' in a:a['evidence_id']=resolve(a['evidence_id'])
    if strategy==EVIDENCE_32K:
        result=anchors.materialize(result,view,evidence)
    return v2.validate(result,view,evidence,located=strategy==EVIDENCE_32K)


def decode(text,view,evidence,strategy=STRATEGY):
    """Repair only non-JSON punctuation outside strings; retain every prose byte.

    Observation citations are the verified quotations, not uncited context IDs.
    Removed context declarations remain in the conversion log and raw response.
    """
    if strategy in EVIDENCE_STRATEGIES:
        value=requests.candidate.strict_json(text)
        cards.Draft202012Validator(schema(strategy,view.get('mode','read'),view)).validate(value)
        return value,[]
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
        require(counts[1]<=output_cap(strategy),'OUTPUT_LIMIT_EXCEEDED')
        result['protocol_validation']={'status':'PASS'}
    except Exception as exc:result['error_codes'].append(str(exc))
    try:
        decoded,edits=decode(result['final_text'],view,evidence,strategy)
        result['deterministic_conversions']=edits
        value=validate_value(decoded,view,evidence,strategy)
        result['content_validation']={'status':'PASS'}
    except json.JSONDecodeError as exc:result['error_codes'].append('NON_JSON:'+str(exc)[:500])
    except Exception as exc:result['error_codes'].append('CONTENT_INVALID:'+str(exc)[:500])
    if strategy in STRUCTURED_32K:
        result['output_limit']={'requested_max_tokens':32000,'provider_reported_output_tokens':counts[1],
            'status':('EXCEEDED' if counts[1]>32000 else 'PASS') if valid_counts else 'UNKNOWN'}
        result['card_failure']=('OUTPUT_LIMIT_EXCEEDED' if strategy in BOUNDED_STRATEGIES and 'OUTPUT_LIMIT_EXCEEDED' in result['error_codes']
            else failure_category(result, obj, schema(strategy,view.get('mode','read'),view)))
    if strategy in EVIDENCE_STRATEGIES:
        result['quote_contract']=quote_contract(strategy)
        result['reference_validation']=dict(result['content_validation'])
        result['semantic_quality']='NOT_VALIDATED'
    return result,value


def failure_category(result, obj=None, output_schema=None):
    """Observable task diagnosis, independent of transport retry decisions."""
    text=result.get('final_text','').lstrip()
    errors=' '.join(result.get('error_codes',[]))
    message=((obj or {}).get('choices') or [{}])[0]
    if result['protocol_validation']['status']!='PASS' and any(e not in {'OUTPUT_TRUNCATED_OR_INVALID_FINISH'} and not e.startswith(('NON_JSON:','CONTENT_INVALID:')) for e in result.get('error_codes',[])):
        return 'PROTOCOL_INVALID'
    if text and not text.startswith('{') and any(x in text[:1500].lower() for x in
            ('we need','need to','let\'s','schema','我需要','输出json','输出 json')):
        return 'VISIBLE_PLANNING_LEAK'
    m=message.get('message',{});details=((obj or {}).get('usage') or {}).get('completion_tokens_details') or {}
    if any(m.get(k) for k in ('reasoning_content','reasoning','thinking')) or details.get('reasoning_tokens',0):return 'PROTOCOL_INVALID'
    if message.get('finish_reason')=='length':return 'JSON_TRUNCATED' if text.startswith('{') else 'OUTPUT_TRUNCATED'
    if result['protocol_validation']['status']!='PASS':return 'PROTOCOL_INVALID'
    try: requests.candidate.strict_json(text)
    except ValueError:return 'JSON_INVALID'
    if result['content_validation']['status']!='PASS':
        try:cards.Draft202012Validator(output_schema or v2.SCHEMA).validate(requests.candidate.strict_json(text))
        except Exception:return 'SCHEMA_INVALID'
        return 'SOURCE_INVALID'
    return None
