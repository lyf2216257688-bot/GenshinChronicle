"""Bounded Card wire contract; all semantic source bindings come from exact choices."""
from copy import deepcopy
from . import anchors, cards, cards_v2 as v2
from .common import require

CONTRACT='card-evidence-selection-3'
SINGLE_CONTRACT='card-evidence-selection-4'
PROMPT='''
本修复版本以以下输出合同替代旧版的语义项evidence_ids和anchors说明：
所有语义项（观察、解释、假说、问题、查询）均用anchors选择原文证据，禁止输出evidence_ids。
read时每个anchor只有{start_segment_id,end_segment_id}，从segments精确复制短编号；
两个端点必须属于同一证据，程序据此唯一确定来源，模型不再重复填写来源编号。
synthesis时每个anchor只有{anchor_id}，仅从inherited_anchors精确复制编号。
非观察项的anchors同样必须指向具体原文，解释和假说不能作为原文引证。
read_ids、child_ids严格回执本次输入，禁止在语义项里把E来源编号当成A锚点编号。
输出应是可检索的分层语义卡：阅读卡保留关键人物行动、转折、证词立场和分支条件；
父卡作跨子卡综合并选择关键引证，完整细节保存在子卡，不逐条复写所有子卡观察。
一次read最多20条观察，synthesis最多12条；其余各类最多4条；不凑数量。
不能为了条目上限把不同主体、不同分支或不同来源模态硬并成一个事实。
overview用连贯叙述覆盖故事推进、人物处境、关系、疑点及重要差异；不能只摘最前部分。
每项text最多240字，subject最多80字，action/object/modality各最多120字；
overview最多1800字、limitations最多1000字；每项最多4个独立锚点。
目标输出不超过10000 Token。不得输出引文、片段全文、字符位置、额外字段或内部推演。
限制一次输出不代表整篇语义已完整；保留未读与排除范围，保持INACTIVE/NOT_REVIEWED。
'''

SINGLE_PROMPT=PROMPT.replace(
    'read时每个anchor只有{start_segment_id,end_segment_id}，从segments精确复制短编号；\n两个端点必须属于同一证据，程序据此唯一确定来源，模型不再重复填写来源编号。',
    'read时每个anchor只有{segment_id}，从segments精确复制一个非空片段的短编号；\n每个ID对应且只对应一个原文片段，程序唯一确定来源和位置。需要多句支持时选择多个独立锚点，\n不填写起止范围，不合并、不改写原文；引证应包括人物归属、指代和模态所需的具体线索。')
SINGLE_PROMPT=SINGLE_PROMPT.replace('每项最多4个独立锚点','每项最多8个独立锚点')


def schema(view=None,mode='read',strategy=None):
    from .card_generation import model_view, BOUNDED, SINGLE, ATOMIC_STRATEGIES
    strategy=strategy or BOUNDED
    result=deepcopy(v2.SCHEMA)
    selected=model_view(view,strategy) if view is not None else None
    keys=('anchor_id',) if mode=='synthesis' else ('segment_id',) if strategy in ATOMIC_STRATEGIES else ('start_segment_id','end_segment_id')
    ids=([x['anchor_id'] for x in selected.get('inherited_anchors',[])] if mode=='synthesis' else
         [s['id'] for q in selected['evidence'] for s in q['segments'] if strategy not in ATOMIC_STRATEGIES or s['text'].strip()]) if selected else []
    item={'type':'string','minLength':1}
    if ids:item['enum']=ids
    reference=cards.obj({k:deepcopy(item) for k in keys})
    result['$defs']={'selection':reference}
    for field in cards.FIELDS:
        detail=deepcopy(result['properties'][field]['items'])
        result['properties'][field]['items']=detail
        del detail['properties']['evidence_ids']
        detail['properties']['text']['maxLength']=240
        if field=='source_observations':
            for k,cap in [('subject',80),('action',120),('object',120),('modality',120)]:
                detail['properties'][k]['maxLength']=cap
        detail['properties']['anchors']={'type':'array','items':{'$ref':'#/$defs/selection'},'minItems':1,'maxItems':8 if strategy in ATOMIC_STRATEGIES else 4}
        detail['required']=list(detail['properties'])
        result['properties'][field]['maxItems']=(12 if mode=='synthesis' else 20) if field=='source_observations' else 4
    result['properties']['overview']['maxLength']=1800
    result['properties']['limitations']['maxLength']=1000
    if selected:
        result['properties']['read_ids']['const']=[q['id'] for q in selected['evidence']]
        result['properties']['child_ids']['const']=[c['card_id'] for c in selected['children']]
    return result

def validate(value,view,evidence,strategy=None):
    from .card_generation import aliases,BOUNDED,SINGLE,ATOMIC_STRATEGIES
    strategy=strategy or BOUNDED
    cards.Draft202012Validator(schema(view,view['mode'],strategy)).validate(value)
    result=deepcopy(value)
    _,mapping=aliases(view);back={v:k for k,v in mapping.items()}
    require(all(x in back for x in result['read_ids']),'Unknown Card wire source alias')
    result['read_ids']=[back[x] for x in result['read_ids']]
    positions={s['id']:(eid,s) for eid,rows in anchors.aliased_segments(view['evidence']).items() for s in rows}
    for field in cards.FIELDS:
        for item in result[field]:
            for a in item['anchors']:
                if view['mode']=='read':
                    if strategy in ATOMIC_STRATEGIES:
                        segment=a.pop('segment_id');a.update(start_segment_id=segment,end_segment_id=segment)
                    require(a['start_segment_id'] in positions and a['end_segment_id'] in positions,'Unknown selected segment')
                    first,last=positions[a['start_segment_id']],positions[a['end_segment_id']]
                    require(first[0]==last[0],'Cross-evidence segment range')
                    a['evidence_id']=first[0]
            # Use the same strict coordinate materializer for every semantic field.
            materialized=anchors.materialize({'source_observations':[item]},view,evidence)['source_observations'][0]
            item['evidence_ids']=list(dict.fromkeys(a['evidence_id'] for a in materialized['anchors']))
            if field=='source_observations':item['anchors']=materialized['anchors']
            else:del item['anchors']
    return v2.validate(result,view,evidence,located=True)

def mock(view,strategy=None):
    from .card_generation import model_view, BOUNDED,SINGLE,ATOMIC_STRATEGIES
    strategy=strategy or BOUNDED
    selected=model_view(view,strategy)
    result={'overview':'MOCK_NOT_SEMANTIC','limitations':'MOCK_NOT_REVIEWED',
        'read_ids':[q['id'] for q in selected['evidence']],
        'child_ids':[c['card_id'] for c in selected['children']],**{f:[] for f in cards.FIELDS}}
    choice=None
    if view['mode']=='read' and selected['evidence']:
        s=next(x['id'] for x in selected['evidence'][0]['segments'] if x['text'].strip())
        choice={'segment_id':s} if strategy in ATOMIC_STRATEGIES else {'start_segment_id':s,'end_segment_id':s}
    elif selected.get('inherited_anchors'):choice={'anchor_id':selected['inherited_anchors'][0]['anchor_id']}
    if choice:result['source_observations']=[{'text':'MOCK','subject':'UNKNOWN','action':'MOCK',
        'object':'UNKNOWN','modality':'MOCK_NOT_SEMANTIC','anchors':[choice]}]
    return result
