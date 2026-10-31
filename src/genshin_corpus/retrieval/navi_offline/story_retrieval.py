"""Evidence-filtered retrieval views; original Cards and the RU route stay intact."""
import re
from collections import Counter

from .common import digest, read, require

POLICY = 'card-story-evidence-v2'
PROJECTION = 'card_story_observation_interpretation_question_v2'
RANKING_POLICY = 'source-balanced-rrf-v1'
MECHANICS = re.compile(r'成长|数值|属性|推荐|配装|搭配|技能|天赋|命之座|掉落|奖励|突破|材料|装备展示|秘境详情')
STORY = re.compile(r'故事|剧情|对话|语音|配音|台词|背景')
QUERY_NOISE = re.compile(r'配装|精炼|重击伤害|成长数值|突破材料|各等级|图片|图像|alt文本|image|record_id|canonical_unit|source_id|modules/|其他组件')
STAT_TEXT = re.compile(r'[%％]|攻击力|伤害|暴击|冷却|生命值|防御力|元素爆发|元素战技|充能效率|精炼')
NARRATIVE_CUE = re.compile(r'传说|故事|昔|曾|原本|过去|从前|当年|据说|旧时')


def description_ranges(text):
    """Conservative exact flavor lines, not an inferred source role or NLP parser.

    A mixed equipment description is not wholly narrative or wholly mechanics.
    Unclassified lines remain in the original Card/RU, with no coverage claim.
    """
    result=[];offset=0
    for line in text.splitlines(keepends=True):
        stripped=line.strip();start=offset+len(line)-len(line.lstrip())
        if (len(stripped)>=8 and NARRATIVE_CUE.search(stripped) and
                not STAT_TEXT.search(stripped) and not stripped.startswith(('·','/','http'))):
            result.append({'start':start,'end':start+len(stripped),'text':stripped})
        offset+=len(line)
    return result


def evidence_policy(sources, source_id):
    """Reading labels are routing hints, never factual/fictional truth judgments."""
    record = read(sources.root / sources.documents[source_id]['canonical_file'])
    names = {s['ordinal']: s['source_metadata'].get('name', '') for s in record['sections']}
    result = {}
    for row in sources.rows(source_id):
        a = row['source']['canonical_address']
        kind, label = a['canonical_unit_kind'], names[a['section_ordinal']]
        # Selector names are observed upstream structure, not inferred plot roles.
        paths = str(row['nested_selector'])
        if kind == 'dialogue_graph': allowed, reason = True, 'DIALOGUE'
        elif MECHANICS.search(label) or label == '装备描述': allowed, reason = False, 'MECHANICS_OR_DESCRIPTION_SECTION'
        elif STORY.search(label): allowed, reason = True, 'STORY_SECTION'
        elif kind == 'rich_text' and label != '装备描述': allowed, reason = True, 'READABLE_BODY'
        elif kind == 'structured_observation' and re.search(r'/(?:content|rich_text|dialogue|voice|story)(?:/|[\x27\x22])', paths, re.I):
            allowed, reason = True, 'STRUCTURED_BODY_SELECTOR'
        else: allowed, reason = False, 'CONTEXT_OR_UNKNOWN_RETAIN_RU_FALLBACK'
        result[row['unit_id']] = {'allowed': allowed, 'reason': reason, 'section': label}
    return result


def prepare_views(card_list, sources):
    """No mixed overview, hypothetical identity, parent copies or state in vectors.

    Document nodes remain in storage; only direct reading leaves (including a
    single small document) are vector owners. Clues reuse parent questions and
    therefore do not add independent index rows or votes. Field-level citations
    restrict mixed document content without pretending to correct model prose.
    """
    policies = {s: evidence_policy(sources, s) for s in sources.documents}
    items, queries, audit, seen = [], [], [], {}
    for card in sorted(card_list, key=lambda c: (c['level'] != 'section', c['card_id'])):
        source = card['source_id']
        quotes = {q['id']: q for q in card['evidence']}
        for q in quotes.values():
            sources.verify_quote(q)
            require(q['source_id'] == source, 'Card evidence crosses sources')
        eligible = {eid for eid, q in quotes.items() if policies[source][q['unit_id']]['allowed']}
        row = {'card_id': card['card_id'], 'level': card['level'], 'fields': [], 'queries': [],
               'description_ranges': []}
        direct = card['level'] != 'clue' and not card['coverage']['derived_from_cards']
        parts = []
        description_ids=set()
        if direct:
            for eid,q in quotes.items():
                if (policies[source][q['unit_id']]['section']=='装备描述' and
                        q['canonical_address']['canonical_unit_kind']=='rich_text'):
                    for span in description_ranges(q['text']):
                        parts.append('source_excerpt_not_model_fact: '+span['text'])
                        row['description_ranges'].append({'evidence_id':eid,'unit_id':q['unit_id'],
                            'ru_start':q['ru_start']+span['start'],'ru_end':q['ru_start']+span['end'],
                            'text':span['text'],'reason':'EXACT_NARRATIVE_DESCRIPTION_LINE'})
                        description_ids.add(eid)
        for field in ('source_observations', 'interpretations', 'open_questions'):
            for detail in card[field]:
                refs = set(detail['evidence_ids'])
                require(bool(refs) and refs <= quotes.keys(), 'Card detail lacks bound evidence')
                keep = direct and refs <= eligible
                row['fields'].append({'field': field, 'detail_sha256': digest(detail), 'included': keep,
                                      'reason': 'BOUND_NARRATIVE' if keep else 'NON_STORY_OR_INHERITED'})
                if keep:
                    parts.append(field + ': ' + detail['text'])
        if parts:
            items.append({'id': card['card_id'], 'source_id': source,
                          'text': '\n'.join([card['title'], 'DERIVED_NOT_SOURCE_FACT'] + parts)})
        row['hypotheses'] = {'included': False, 'reason': 'RETAINED_IN_CARD_NOT_VECTOR_OR_LINK_EVIDENCE',
                             'count': len(card['hypotheses'])}
        row['overview_included'] = False
        for detail in card['queries']:
            refs = set(detail['evidence_ids'])
            require(bool(refs) and refs <= quotes.keys(), 'Query lacks bound evidence')
            key = (source, ' '.join(detail['text'].split()))
            reason = 'NARRATIVE_QUERY'
            keep = direct and refs <= (eligible | description_ids) and not QUERY_NOISE.search(detail['text'])
            if refs & description_ids and STAT_TEXT.search(detail['text']):keep=False
            if not keep: reason = 'NON_STORY_OR_INHERITED_OR_CATALOG_QUERY'
            elif key in seen:
                keep, reason = False, 'DUPLICATE_SOURCE_QUERY'
                previous = queries[seen[key]]
                previous['evidence_ids'] = sorted(set(previous['evidence_ids']) | refs)
                previous['origin_card_ids'].append(card['card_id'])
            qid = digest([POLICY, source, key[1]])
            row['queries'].append({'query_id': qid, 'detail_sha256': digest(detail),
                                   'included': keep, 'reason': reason})
            if keep:
                seen[key] = len(queries)
                queries.append({'query_id': qid, 'card_id': card['card_id'], 'source_id': source,
                                'text': detail['text'], 'evidence_ids': list(detail['evidence_ids']),
                                'origin_card_ids': [card['card_id']]})
        audit.append(row)
    counts = Counter(q['source_id'] for q in queries)
    for q in queries:
        q['rrf_weight'] = 1 / counts[q['source_id']]
    coverage = [{'source_id': s, 'vector_owners': sum(i['source_id'] == s for i in items),
                 'queries': sum(q['source_id'] == s for q in queries),
                 'reason': 'NARRATIVE_VIEW_AVAILABLE' if any(i['source_id'] == s for i in items)
                           else 'NO_DIRECT_BOUND_NARRATIVE_CARD_RETAIN_RU_FALLBACK'} for s in sources.documents]
    return {'policy': POLICY, 'projection': PROJECTION, 'ranking_policy': RANKING_POLICY,
            'items': items, 'queries': queries, 'sources': coverage,
            'audit': audit, 'evidence_policy': policies, 'ru_fallback': 'UNCHANGED_EXACT_RU_INDEX',
            'semantic_quality': 'NOT_VALIDATED'}
