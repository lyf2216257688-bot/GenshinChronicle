"""Program-owned exact locations inside an explicitly delivered source fragment."""
from .common import require
from .common import digest
from copy import deepcopy
import re

CONTRACT = 'card-evidence-selection-2'
STABLE_SEGMENT_CONTRACT = 'card-evidence-selection-1'
SEGMENT_POLICY = 'verbatim-sentence-paragraph-boundaries-1+request-local-short-alias-1'


def segments(evidence_id, text):
    """Lossless occurrence coordinates, not a new evidence or semantic layer."""
    ends = [m.end() for m in re.finditer(r'[。！？!?]+[”’」』\"\']*|\r\n|[\r\n]', text)]
    if not ends or ends[-1] != len(text): ends.append(len(text))
    result = []; start = 0
    for end in ends:
        if end > start:
            result.append({'id': 'S'+digest([STABLE_SEGMENT_CONTRACT,evidence_id,start,end])[:16],
                           'start':start,'end':end,'text':text[start:end]})
        start = end
    require(''.join(s['text'] for s in result)==text, 'Segment coverage differs')
    require(len({s['id'] for s in result})==len(result), 'Segment identity collision')
    return result


def aliased_segments(evidence):
    """Model sees compact sequential IDs; source coordinates retain their stable identity."""
    result = {}; counter = 0
    for q in evidence:
        rows = []
        for s in segments(q['id'], q['text']):
            counter += 1
            rows.append({**s, 'stable_id': s['id'], 'id': f'S{counter:04d}'})
        result[q['id']] = rows
    require(sum(map(len, result.values())) == counter, 'Segment aliases not unique')
    return result


def verify_location(anchor, evidence):
    """Known coordinates disambiguate repeated text; never search/rebind a quote."""
    require(set(anchor)=={'evidence_id','quote','start','end','ru_start','ru_end'}, 'Invalid located anchor shape')
    require(anchor['evidence_id']==evidence['id'], 'Anchor source differs')
    start,end=anchor['start'],anchor['end']
    require(type(start) is int and type(end) is int and 0<=start<end<=len(evidence['text']), 'Invalid anchor range')
    require(type(anchor['ru_start']) is int and type(anchor['ru_end']) is int and
            (anchor['ru_start'],anchor['ru_end'])==(evidence['ru_start']+start,evidence['ru_start']+end), 'Anchor RU range differs')
    require(anchor['quote']==evidence['text'][start:end], 'Anchor text differs from exact range')
    require(bool(anchor['quote'].strip()), 'Whitespace-only anchor cannot support observation')
    return anchor


def anchor_id(anchor):
    return digest([CONTRACT,anchor])


def inherited(view):
    registry={}
    for child in view['children']:
        for observation in child['source_observations']:
            for a in observation['anchors']:
                require(all(k in a for k in ('start','end','ru_start','ru_end')), 'Inherited anchor lacks verified coordinates')
                key=anchor_id(a)
                registry.setdefault(key,deepcopy(a))
    return registry


def selection_view(view):
    result=deepcopy(view);result['quote_contract']=CONTRACT
    if view['mode']=='read':
        aliases=aliased_segments(result['evidence'])
        for q in result['evidence']:
            q['segments']=[{k:s[k] for k in ('id','text')} for s in aliases[q['id']]]
            q.pop('text')
    else:
        registry=inherited(view);aliases={key:f'A{i+1:03d}' for i,key in enumerate(registry)}
        result['inherited_anchors']=[{'anchor_id':aliases[key],'evidence_id':a['evidence_id'],'quote':a['quote']}
                                     for key,a in registry.items()]
        for child in result['children']:
            for observation in child['source_observations']:
                observation['anchors']=[{'anchor_id':aliases[anchor_id(a)]} for a in observation['anchors']]
    return result


def location_map(view, evidence):
    bound={q['id']:q for q in evidence}
    if view['mode']=='read':
        require(len(bound)==len(evidence) and len({q['id'] for q in view['evidence']})==len(view['evidence']),
                'Duplicate selected evidence identity')
        require({q['id'] for q in view['evidence']}==set(bound),'Selected evidence map differs')
        for q in view['evidence']:
            require(q['text']==bound[q['id']]['text'],'Delivered text differs from bound evidence')
        aliases=aliased_segments(view['evidence'])
        return {'contract':CONTRACT,'segment_policy':SEGMENT_POLICY,'evidence':[
            {**{k:q[k] for k in ('id','source_id','canonical_sha256','unit_id','ru_start','ru_end')},
             'segments':[{k:s[k] for k in ('id','stable_id','start','end')} for s in aliases[q['id']]]}
            for q in evidence]}
    registry=inherited(view)
    for a in registry.values():
        require(a['evidence_id'] in bound, 'Inherited anchor source missing')
        verify_location(a,bound[a['evidence_id']])
    return {'contract':CONTRACT,'inherited_anchors':[
        {'alias':f'A{i+1:03d}','anchor_id':key,'anchor':a} for i,(key,a) in enumerate(registry.items())]}


def materialize(value, view, evidence):
    location_map(view,evidence)
    result=deepcopy(value);bound={q['id']:q for q in evidence}
    delivered={q['id'] for q in view['evidence']}
    positions={eid:{s['id']:s for s in rows} for eid,rows in aliased_segments(view['evidence']).items()} if view['mode']=='read' else {}
    registry=list(inherited(view).values()) if view['mode']=='synthesis' else []
    choices={f'A{i+1:03d}':a for i,a in enumerate(registry)}
    for observation in result['source_observations']:
        located=[]
        for selection in observation['anchors']:
            if view['mode']=='synthesis':
                require(selection['anchor_id'] in choices, 'Unknown inherited anchor ID')
                a=deepcopy(choices[selection['anchor_id']])
                require(a['evidence_id'] in bound,'Inherited source not bound')
                verify_location(a,bound[a['evidence_id']])
            else:
                eid=selection['evidence_id']
                require(eid in delivered and eid in bound,'Unknown/undelivered selected evidence ID')
                q=bound[eid];source_positions=positions[eid]
                require(selection['start_segment_id'] in source_positions and selection['end_segment_id'] in source_positions,
                        'Unknown segment ID in selected evidence')
                start=source_positions[selection['start_segment_id']]['start'];end=source_positions[selection['end_segment_id']]['end']
                require(start<end,'Reversed segment range')
                a={'evidence_id':eid,'quote':q['text'][start:end],'start':start,'end':end,
                   'ru_start':q['ru_start']+start,'ru_end':q['ru_start']+end}
                verify_location(a,q)
            located.append(a)
        observation['anchors']=located
    return result


def locate(text, quote):
    require(isinstance(quote, str) and bool(quote), 'Empty quotation')
    start = text.find(quote)
    require(start >= 0, 'Quotation not in delivered source')
    require(text.find(quote, start + 1) < 0, 'Ambiguous quotation; select a longer unique fragment')
    return start, start + len(quote)
