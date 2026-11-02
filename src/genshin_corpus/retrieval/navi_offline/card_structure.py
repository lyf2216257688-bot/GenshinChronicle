"""Versioned structural packing; source identities and evidence remain unchanged."""
from collections import defaultdict
import json
import re

from . import cards_v2 as v2, card_generation as generation
from .common import digest, require

POLICY='card-structure-32k-1'
BOUNDED_POLICY='card-structure-source-groups-2'
RECOVERY='one-structure-decision-1'


def capacity(view, request_limit=100000, context_token_limit=None, strategy=generation.DEFAULT_STRATEGY):
    body=json.dumps(generation.wire(view,strategy),ensure_ascii=False)
    # Byte proxy is deliberately conservative, not a native tokenizer attestation.
    reserved=len(body.encode('utf8'))+32000
    return len(body)<=request_limit and (context_token_limit is None or reserved<=context_token_limit)


def plan(sources, source, *, group_modules=False, semantic_selection=False):
    result=v2.reading_plan(sources,source)
    if semantic_selection:
        from .card_semantics import select_plan
        result=select_plan(sources,source,result)
    record=sources.record(source)
    modules=[m['id'] for tab in record['record_metadata'].get('source_template_layout',{}).get('tab',[])
             for group in tab.get('module_group',[]) for m in group.get('module',[])]
    ranks={str(mid):i for i,mid in enumerate(modules)}
    sections=record['sections']
    order=sorted((s['ordinal'] for s in sections),key=lambda n:(
        ranks.get(str(sections[n]['source_metadata'].get('source_module_id')),len(ranks)+n),n))
    names={s['ordinal']:s['source_metadata'].get('name','') for s in sections}
    selected_sections={q['canonical_address']['section_ordinal'] for q in result['selected']}
    chapters=[s for s in selected_sections if re.search(r'第.+[卷章幕]|序章|序幕|终章',names[s])]
    section_groups={s:s for s in selected_sections}
    by_module={str(s['source_metadata'].get('source_module_id')):s['ordinal'] for s in sections}
    for tab in record['record_metadata'].get('source_template_layout',{}).get('tab',[]):
        for group in tab.get('module_group',[]):
            members=[by_module[str(m['id'])] for m in group.get('module',[]) if str(m['id']) in by_module]
            targets=[s for s in members if s in chapters]
            target=(targets[0] if len(targets)==1 else members[0] if group_modules and not targets and members else None)
            if target is not None:
                for s in members:
                    if s in selected_sections:section_groups[s]=target
    # Visit an observed graph once; retain every edge and shared-node reference,
    # without expanding combinatorial paths or declaring source order chronology.
    node_ranks={}
    for section in sections:
        for unit in section['units']:
            if unit['kind']!='dialogue_graph':continue
            for group in unit['value']['groups']:
                children=defaultdict(list)
                for edge in sorted(group['edges'],key=lambda e:e.get('ordering',0)):
                    children[edge['parent_id']].append(edge['child_id'])
                visited=set();stack=[group['root_id']];ordered=[]
                while stack:
                    node=stack.pop()
                    if node in visited:continue
                    visited.add(node);ordered.append(node);stack.extend(reversed(children[node]))
                ordered += [n['source_id'] for n in group['nodes'] if n['source_id'] not in visited]
                for i,node in enumerate(ordered):node_ranks[(section['ordinal'],unit['ordinal'],group['ordering'],node)]=i
    def position(q):
        a=q['canonical_address'];d=q['structure'].get('dialogue',{})
        rank=node_ranks.get((a['section_ordinal'],a['canonical_unit_ordinal'],d.get('group_ordering'),d.get('node_source_id')),0)
        return (order.index(a['section_ordinal']),a['canonical_unit_ordinal'],d.get('group_ordering',0),rank,q.get('canonical_start',0),q['ru_start'])
    result['selected'].sort(key=position)
    branches=defaultdict(set)
    for q in result['selected']:
        for e in q['structure'].get('dialogue',{}).get('observed_edges',[]):
            branches[(q['canonical_address']['section_ordinal'],e['parent_id'])].add(e['child_id'])
    result.update(version=BOUNDED_POLICY if group_modules else POLICY,section_order=order,section_names=names,section_groups=section_groups,
        structural_hierarchy=len(chapters)>1 or any(len(v)>1 for v in branches.values()),
        ordering_basis='SOURCE_TEMPLATE_PLACEMENT_THEN_CANONICAL_ORDER; NOT_EVENT_CHRONOLOGY')
    return result


def split_quotes(sources, quotes):
    """One structural subdivision, preferring RU/node, paragraph and sentence ends."""
    require(bool(quotes),'Cannot subdivide empty reading')
    if len(quotes)>1:
        midpoint=sum(len(q['text']) for q in quotes)/2
        cursor=0;cut=1
        for i,q in enumerate(quotes[:-1],1):
            cursor+=len(q['text']);cut=i
            if cursor>=midpoint:break
        return [quotes[:cut],quotes[cut:]]
    q=quotes[0];text=q['text'];middle=len(text)//2
    require(middle>0,'Source context cannot be subdivided')
    boundaries=[m.end() for m in re.finditer(r'\n+',text) if 0<m.end()<len(text)]
    if not boundaries:boundaries=[m.end() for m in re.finditer(r'[。！？!?]',text) if 0<m.end()<len(text)]
    cut=min(boundaries,key=lambda p:abs(p-middle)) if boundaries else middle
    return [[sources.quote(q['unit_id'],q['ru_start'],q['ru_start']+cut)],
            [sources.quote(q['unit_id'],q['ru_start']+cut,q['ru_end'])]]


def batches(sources, source, reading_plan, body_limit=8000, request_limit=100000,
            context_token_limit=None, strategy=generation.DEFAULT_STRATEGY):
    selected=reading_plan['selected'];groups=defaultdict(list)
    hierarchical=reading_plan['structural_hierarchy'] or sum(len(q['text']) for q in selected)>body_limit
    for q in selected:
        section=q['canonical_address']['section_ordinal']
        groups[reading_plan['section_groups'].get(section,section) if hierarchical else None].append(q)
    for section,quotes in groups.items():
        scope={'selection_policy':reading_plan['version'],'section_name':reading_plan['section_names'].get(section,''),
            'ordering_basis':reading_plan['ordering_basis'],'reading_is_partial':True,
            'capacity_basis':'UTF8_BYTE_PROXY_PLUS_32000_OUTPUT; native tokenizer/context not attested',
            'context_token_limit':context_token_limit}
        if reading_plan.get('narrative_selection_policy'):
            scope['narrative_selection_policy']=reading_plan['narrative_selection_policy']
        if reading_plan['version']==BOUNDED_POLICY:
            scope['source_template_sections']=[{'section':s,'name':reading_plan['section_names'][s]}
                for s,target in reading_plan['section_groups'].items() if target==section]
            scope['grouping_basis']='EXPLICIT_SOURCE_MODULE_GROUP; NOT_INFERRED_EVENT_OR_CHRONOLOGY'
        def view(qs):
            return {'card_version':v2.VERSION,'mode':'read','title':sources.documents[source]['title'],
                'section':section,'scope':scope,'children':[],'evidence':[v2.model_excerpt(q) for q in qs]}
        queue=list(quotes);current=[]
        while queue:
            q=queue.pop(0);proposed=current+[q]
            if sum(len(x['text']) for x in proposed)<=body_limit and capacity(view(proposed),request_limit,context_token_limit,strategy):
                current=proposed
            elif current:
                yield section,view(current),current
                current=[];queue.insert(0,q)
            else:
                pieces=split_quotes(sources,[q]);queue[0:0]=[x for piece in pieces for x in piece]
        if current:yield section,view(current),current


def mark(card):
    formal=card['level'] in {'document','section','clue'}
    card['node_role']='SEMANTIC_CARD' if formal else 'TEMPORARY_AGGREGATION'
    spans=defaultdict(list)
    for q in card['evidence']:spans[q['unit_id']].append((q['ru_start'],q['ru_end']))
    normalized=[]
    for uid,ranges in sorted(spans.items()):
        merged=[]
        for start,end in sorted(ranges):
            if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
            else:merged.append([start,end])
        normalized.append([uid,merged])
    card['semantic_scope_id']=digest([POLICY,card['source_id'],card['canonical_sha256'],card['level'],card['section'],
        normalized,card['overview'] if card['level']=='clue' else None])
    return card
