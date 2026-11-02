"""Opt-in evidence-first Card judgment and narrowly scoped narrative admission.

This is input/prompt policy, not an automatic semantic judge. No relation is
promoted, repaired or certified by string matching. Historical Cards stay intact.
"""
from copy import deepcopy
import re

from . import cards_v2 as v2
from .common import require

POLICY='card-evidence-judgment-1'
SELECTION='card-narrative-admission-20261102'
PROMPT='''
证据判断规则（同时防止无故存疑和过度推断）：
1. 区分“原文直接陈述”“人物或文献声称”“合理推断”“缺乏证据”。
   前两者写source_observations，modality明确说明是哪一种、谁在何种叙事中陈述。
   原文直接命名、指认、定义或明确承接的关系应如实记录，不因“未独立证实史实”降级为假说。
   “本文明确写X最早名为Y”与“X/Y在游戏世界所有记载中客观同一”是不同命题。
   人物证言、文献、歌谣、传说、小说、社区考据的内容只能在相应来源与叙事层内成立；
   直接引文也可能是谎言、假设或传说，不能凭语气肯定就认定客观史实。
2. 合理推断放interpretations；有源线索但待证的具体命题放hypotheses，写清缺口/反证；
   缺乏证据不产生肯定观察，不凭空制造假说，可在limitations或具体open_questions记录缺失。
   一个命题可局部明确、局部未知：分别写出已明示的安排承接与尚不明确的职责细节。
   不把已被当前引文明答的问题继续放在hypotheses/open_questions，也不为了凑数制造反证。
3. synthesis先读inherited_anchors原文和source_context，再核对children。
   子卡的概述、分类、模态和问题均是待核的模型判断，不是独立证据，重复次数不增加可信度。
   已交付原文明示时，允许纠正子卡的不必要假说、归属或遗漏；保留具体引证与来源模态。
   原文没有交付时保留未读，不用子卡的确定语气补出事实；不得创造未交付引文。
4. 明确指认需要在同一叙事、人物/分支上下文内核对。相似名字、容貌、镜像情节、比较句
   不能跨不同故事合并身份。假想/反事实的子女不是已存在人物；代词无法解消时保留未知。
   歌谣/传说中的称呼不自动指认历史人物或说话者；不能把不确定的歌者强行设为叙述主体。
5. 占位“暂无”、孤立目录/人名、纯数值、任务操作说明和技术标识不产生剧情行动或疑点；
   可保留作为上下文但不要为它们凑观察、假说或查询。没有剧情时语义数组可以全空，说明信息不足。
   装备描述中的回忆、对话、称谓与事件有剧情价值，即使提到伤害、战斗、数量也应保留。
6. read_ids/child_ids是交付回执，不是理解证明。语义覆盖未评估，不写“整页都已理解”。
'''


def synthesis_context(view, children):
    """Deliver source modality/branch context independently of child prose.

    Only metadata of evidence actually carrying inherited anchors is sent. Full
    text is not smuggled in as uncitable context; quote choices remain exact.
    """
    result=deepcopy(view)
    ids={a['evidence_id'] for c in children for x in c['source_observations'] for a in x['anchors']}
    context={}
    for c in children:
        for q in c['evidence']:
            if q['id'] not in ids: continue
            item=v2.model_excerpt(q);item.pop('text')
            if q['id'] in context: require(context[q['id']]==item,'Conflicting synthesis source context')
            context[q['id']]=item
    require(set(context)==ids,'Inherited anchor lacks source context')
    result['source_context']=list(context.values())
    return result


def evidence_first(selected):
    """Wire-only ordering; neither child prose nor source truth is rewritten."""
    result={'semantic_policy':POLICY,
        'judgment_basis':'DELIVERED_SOURCE_WORDING_WITH_MODALITY; CHILD_JUDGMENTS_ARE_FALLIBLE'}
    for key in ('mode','title','scope','evidence','source_context','inherited_anchors'):
        if key in selected: result[key]=selected[key]
    result.update({k:v for k,v in selected.items() if k not in result and k!='children'})
    result['children']=selected['children']
    return result


_PLACEHOLDER=re.compile(r'(?:暂无(?:内容|资料|描述)?|待补充|N/?A|[-—–]{2,})[。.!！]?',re.I)
_NUMBER=re.compile(r'[\s\d.,，。%％+\-×÷/=:：()（）]+')
_TECH=re.compile(r'/\S*/(?:root_id|node_id|parent_id|id)\s+[\w.:-]+')
_RULE=re.compile(r'^(?:结束阶段[：:]|可用次数|(?:本|每)回合|此牌在场时|角色施放|装备此牌后|对角色造成|造成\d+点|生命值[：:]|(?:攻击力|防御力|暴击率)[：:])')
_WALKTHROUGH=re.compile(r'^现实时间.{0,12}(?:完成|等待|连续).*(?:接取|委托|任务|完成)')


def noise_reason(text, *, dialogue=False):
    """Exclude only positive, narrow surface evidence; unknown prose is retained."""
    if dialogue: return None  # A spoken instruction can itself be plot.
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    if not lines: return 'EMPTY'
    if all(_PLACEHOLDER.fullmatch(x) for x in lines): return 'PLACEHOLDER'
    if all(_NUMBER.fullmatch(x) for x in lines): return 'PURE_NUMERIC'
    if all(_TECH.fullmatch(x) for x in lines): return 'TECHNICAL_SELECTOR'
    if all(_RULE.search(x) and re.search(r'\d',x) for x in lines): return 'EXPLICIT_MECHANIC_RULE'
    if all(_WALKTHROUGH.search(x) for x in lines): return 'EXPLICIT_WALKTHROUGH'
    return None


def _merge(ranges):
    merged=[]
    for s,e in sorted(ranges):
        if merged and s<=merged[-1][1]: merged[-1][1]=max(e,merged[-1][1])
        else: merged.append([s,e])
    return merged


def _missing(length, ranges):
    out=[];cursor=0
    for start,end in _merge(ranges):
        if cursor<start: out.append([cursor,start])
        cursor=max(cursor,end)
    if cursor<length: out.append([cursor,length])
    return out


def select_plan(sources, source, base):
    """Create audited exact ranges; equipment prose needs no trigger keyword.

    The old plan remains the owner of structural companion/unknown exclusions.
    This selection never alters RU text or declares its provenance authoritative.
    """
    result=deepcopy(base);result['selected']=[]
    by_unit={}
    for q in base['selected']: by_unit.setdefault(q['unit_id'],[]).append(q)
    units={u['unit_id']:u for u in result['units']}
    for row in sources.rows(source):
        uid=row['unit_id'];unit=units[uid];text=row['retrieval_visible_text']
        equipment=unit['section_name']=='装备描述' and unit['kind']=='rich_text'
        original=by_unit.get(uid,[])
        ranges=[(0,len(text))] if equipment else [(q['ru_start'],q['ru_end']) for q in original]
        kept=[];omitted=[]
        for start,end in ranges:
            # Equipment is assessed line by line; other prose retains context.
            chunks=text[start:end].splitlines(keepends=True) if equipment else [text[start:end]]
            pos=start
            for chunk in chunks:
                reason=noise_reason(chunk,dialogue=unit['kind']=='dialogue_graph')
                if reason: omitted.append({'range':[pos,pos+len(chunk)],'reason':reason})
                elif chunk.strip(): kept.append([pos,pos+len(chunk)])
                pos+=len(chunk)
        for start,end in _merge(kept): result['selected'].append(sources.quote(uid,start,end))
        unit['selected_ranges']=_merge(kept)
        unit['unread_ranges']=_missing(len(text),kept)
        if omitted or equipment:
            unit.update(base_reason=unit['reason'],reason='NARRATIVE_ADMISSION_WITH_EXPLICIT_OMISSIONS',
                narrative_omissions=omitted)
    result['narrative_selection_policy']=SELECTION
    return result


def delivery_coverage(plan, spans):
    """Range accounting only. Text delivery cannot measure semantic completeness."""
    if plan is None: return {'status':'NOT_PLANNED','semantic_assessment':'NOT_ASSESSED'}
    required={}
    for q in plan['selected']: required.setdefault(q['unit_id'],[]).append([q['ru_start'],q['ru_end']])
    total=covered=0;undelivered=[]
    for uid,ranges in required.items():
        for start,end in _merge(ranges):
            total+=end-start
            intersections=[[max(start,s),min(end,e)] for s,e in spans.get(uid,[]) if s<end and e>start]
            union=_merge(intersections);covered+=sum(e-s for s,e in union)
            for s,e in _missing(end-start,[[s-start,e-start] for s,e in union]):
                undelivered.append({'unit_id':uid,'range':[s+start,e+start]})
    return {'status':'EMPTY_SELECTION' if total==0 else 'COMPLETE' if covered==total else 'PARTIAL',
        'required_chars':total,'delivered_or_inherited_chars':covered,'undelivered_selected_ranges':undelivered,
        'basis':'BOUND_RU_RANGES; NOT_SUCCESSFUL_DEPENDENCY_OR_UNDERSTANDING_PROOF',
        'semantic_assessment':'NOT_ASSESSED'}
