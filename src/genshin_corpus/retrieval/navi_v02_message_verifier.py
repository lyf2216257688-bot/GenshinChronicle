"""Distinguish verifiable message content from unresolved reasoning identity."""
from .navi_f2_instruction_diagnostic import compare

def inspect(raw, sdk):
    result=compare(raw,sdk)
    items=result['items']; messages=[x for x in items if x['type']=='message']
    if not result['raw_sdk_events_exact_equal'] or not result['usage_valid'] or len(messages)!=1 or any(x['type'] not in ('reasoning','message') or not x['event_types_consistent'] for x in items):
        raise ValueError('terminal, raw/sdk, usage or output type invalid')
    msg=messages[0]; terminal=raw[-1]['response']; index=msg['output_index']; obj=terminal['output'][index]; ident=obj['id']
    if obj.get('role')!='assistant' or obj.get('status')!='completed' or not msg['id_consistent'] or not msg['text_consistent']:
        raise ValueError('message identity or text mismatch')
    seen=[]
    for i,e in enumerate(raw):
        if e.get('type') in ('response.output_text.delta','response.output_text.done','response.content_part.done'):
            if e.get('item_id')!=ident or e.get('output_index')!=index or e.get('content_index')!=0:
                raise ValueError('unbound message event')
            seen.append(i)
    add=[i for i,e in enumerate(raw) if e.get('type')=='response.output_item.added' and e.get('output_index')==index]
    done=[i for i,e in enumerate(raw) if e.get('type')=='response.output_item.done' and e.get('output_index')==index]
    if len(add)!=1 or len(done)!=1 or not seen or not (add[0]<min(seen)<=max(seen)<done[0]<len(raw)-1):
        raise ValueError('message event ordering')
    return {'message':'PASS','full_stream':'PASS' if result['stream_structure_consistent'] else 'FAIL','reasoning_id_conflicts':[x['ids'] for x in items if x['type']=='reasoning' and not x['id_consistent']], 'usage':result['usage'], 'message_text':result['message_text']}
