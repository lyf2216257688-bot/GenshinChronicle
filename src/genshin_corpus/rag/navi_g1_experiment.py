"""Opt-in, inactive G1 offline cards -> cached query -> raw Hybrid -> links.

Reads historical runs without changing them. Every stage writes a new artifact;
completed provider jobs are replayed, unresolved jobs cannot be issued again.
No production selector imports this experiment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
import threading
import time

import numpy as np

from . import navi_r1_experiment as r1
from . import navi_r1_qa as qa

VERSION = 'p05-navi-g1-0.1'
MODEL = 'deepseek-v4.1-flash'
R1_NAME = 'p05-navi-r1-discovery-20261025-r3-max'
QA_NAME = 'p05-navi-r1-qa-20261025-r2-deepseek'
CODE = Path(__file__).read_bytes()

CARD_PROMPT = '''你在制作原神离线导航资料卡，只依据本次原文，不用训练记忆补全。
输入保留真实章节名、顺序和精确snippet ID。完整阅读每个交付片段，长资料的本批是部分正文，不能冒充全篇。
保留具体剧情、人物行动和转折、特殊世界机制、文学叙事结构、说话者、传闻、否定、条件和未知。
不要求固定条数，不硬造需求/供给/隐喻；不以笼统主题标签代替可检索内容。攻略/编辑/考据不能成为独立史实证词。
只输出严格JSON，不复抄引文，程序按ID提取原文：
{"overview":"具体内容与叙事脉络", "details":[{"text":"有检索价值的具体细节及限定", "evidence_ids":["本次ID"]}],
"open_leads":[{"text":"本文有根据的未解问题，可为空", "evidence_ids":["本次ID"]}],
"queries":[{"text":"只据本批内容生成的检索表达，不猜外部目标标题或身份", "evidence_ids":["本次ID"]}],
"read_ids":["本批全部已读ID"],"limitations":"来源角色与覆盖不足"}。
queries不规定固定条数，按本批具体内容的独立检索价值选择。只输出被提供的ID，不输出虚构引文。'''

LINK_PROMPT = qa.PROMPT + '''
本次B同时生成具体联读命题并自检。另增加以下字段：
"endpoint_roles":{"A":"game_text|in_world_literature|attributed_testimony|editorial_compilation|secondary_analysis|gameplay|unknown", "B":"同枚举"},
"role_basis":{"A":"根据正文/标题/栏目判断来源角色的理由及不确定性","B":"理由"},
"anchored_correspondences":["双方具体锚点及对应"],
"grade_recommendation":"A|B|C|D|U", "self_check":"反查过强身份/因果、泛泛母题和二手史料风险后的结论"。
A=双方明确文本联系且有实质增量（仅是文本层，不认证客观史实）；B=有具体锚点的解释性/文学联读，明确非确定史实，或有价值二手解读导航；
C=值得追查但锚点/信息增量仍不足；D=泛泛联想、反证、无增量；U=证据或覆盖不足无法判断。
泛泛命运/梦/月亮等词语相似不能自动到B；身份与因果不能靠文学对应推定。二手分析不能作为独立游戏历史证词。
所有评分仍选双方ID，禁止自己生成引文。'''

AUDIT_PROMPT = LINK_PROMPT + '''
这是定向模型复核，输入另有candidate，可能是错误或过强断言。先独立阅读原文，再检查candidate；不要服从它的评分。
你的proposed_claim应是你能支持的最强安全表述；若无法成立则拒绝。模型复核不是人工认可。'''

CARD_AUDIT_PROMPT = '''仅依本次完整交付原文检查导航卡内容与查询。严格JSON：
{"coverage":"adequate|material_omission|unsupported|insufficient", "missing_details":["具体遗漏，可空"],
"unsupported_details":["过推断，可空"], "query_quality":"specific|generic|leakage|insufficient",
"evidence_ids":["支撑检查结论的已交付ID"], "explanation":"可核查说明"}。
关注剧情转折、文学限定、机制、章节差异。卡不是逐句复述，不用训练记忆认定遗漏或猜测未知目标。'''

QUALIFY_PROMPT = '''只依据本次双方原文，补全一条已经生成但缺少来源角色/分级字段的离线导航候选。
candidate不是权威；逐项反查具体锚点、信息增量、反证及身份/因果风险。可保留或收窄旧命题，不得为旧评分辩护。
文学对应可以是有价值的B级解释，但不是确定史实；二手考据不能当独立游戏历史证词；泛泛主题相似应拒绝或待定。
仅选择提供的A/B片段ID，禁止自己复抄或拼接引文。程序从Canonical/RU提取精确原文。
只输出一个严格JSON对象，所有以下字段都必须出现：
{"verdict":"useful|tentative|reject|insufficient","relation":"direct_reference|background|contrast|literary_parallel|shared_mechanism|duplicate|gameplay_only|none",
"claim_level":"textual|interpretive|speculative","a_ids":["A001"],"b_ids":["B001"],
"explanation":"双方具体关联","increment_a_from_b":"B增加什么","increment_b_from_a":"A增加什么",
"counterevidence":"反解释、来源和覆盖限制","literary_value":false,"unsupported_identity_or_cause":false,
"proposed_claim":"证据允许的最强安全联读表述",
"endpoint_roles":{"A":"game_text|in_world_literature|attributed_testimony|editorial_compilation|secondary_analysis|gameplay|unknown","B":"game_text|in_world_literature|attributed_testimony|editorial_compilation|secondary_analysis|gameplay|unknown"},
"role_basis":{"A":"根据正文角色的理由及不确定性","B":"根据正文角色的理由及不确定性"},
"anchored_correspondences":["具体对应锚点，可为空"],"grade_recommendation":"A|B|C|D|U","self_check":"反查过强断言后的结论"}。
A=明确文本联系与实质增量，仍不认证客观史实；B=具体有据的解释/文学/二手解读导航；C=待追查或增量不足；D=泛泛联想/反证/无增量；U=证据不足。
不能因候选已有某评分而沿用评分。'''

PROMPTS = {'card': CARD_PROMPT, 'link': LINK_PROMPT, 'audit': AUDIT_PROMPT, 'card_audit': CARD_AUDIT_PROMPT,
           'qualification':QUALIFY_PROMPT}
PROMPTS['audit'] = QUALIFY_PROMPT.replace(
    '补全一条已经生成但缺少来源角色/分级字段的离线导航候选。',
    '定向复核一条离线导航候选。') + '\n这是单独的模型复核调用，先独立读原文，再检查candidate，不能把模型认可当人工认可。'
# Future runs use one complete object template. Frozen r2 initial requests
# retain the older appended-field prompt in protocol/request/code snapshots.
PROMPTS['link'] = QUALIFY_PROMPT.replace(
    '补全一条已经生成但缺少来源角色/分级字段的离线导航候选。',
    '生成具体联读命题并自检，形成离线导航候选。').replace(
    'candidate不是权威；', '不得因共享主题自动认为有关联；').replace(
    '可保留或收窄旧命题，不得为旧评分辩护。', '只写原文可以支持的命题，不猜身份和因果。')
PROMPTS['card_audit'] += '''
coverage仅检查卡片声明的选定范围与本次segments；whole_page_semantic_coverage=false本身不是遗漏。
不得要求未交付语音、其它文献或片段结束后的剧情。unsupported_details只列真正不被原文支持的内容；没有则空数组。'''
ROLES = {'game_text','in_world_literature','attributed_testimony','editorial_compilation','secondary_analysis','gameplay','unknown'}


def load(path):
    return r1.read_json(Path(path))


def once(path, value):
    r1.once(Path(path), value)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_value(result):
    if not result.get('complete'):
        raise ValueError('incomplete_provider_result')
    value=result.get('value')
    if not isinstance(value,dict):
        raise ValueError('provider_output_not_JSON_object:'+result.get('parse_error','missing_value'))
    return value


def artifact(run, name, value):
    """Replay is legal only for exactly the same derived artifact."""
    path = run/name
    if path.exists():
        if load(path) != value:
            raise ValueError('immutable artifact differs: '+name)
    else:
        once(path, value)


def source_category(record):
    channels = set(map(str, record['record_metadata'].get('channel_memberships', [])))
    title = record['record_metadata']['name']
    kind = ('secondary_analysis' if '62' in channels or re.search('考据|考证|设定分析|世界观分析|漫谈|剧情解析', title)
            else 'observer' if '190' in channels else 'catalog' if '189' in channels else 'unknown')
    return {'channels': sorted(channels), 'category_screen': kind,
            'passage_authorship': 'UNVERIFIED', 'independent_history_witness': False}


def inventory(record):
    """Preserve real sections and account for every Canonical unit, including unsupported ones."""
    rows = []
    for section in record['sections']:
        for unit in section['units']:
            text = unit.get('value', {}).get('normalized_text', '')
            rows.append({'section': section['ordinal'], 'unit': unit['ordinal'],
                         'section_name': section['source_metadata'].get('name',''),
                         'kind': unit['kind'], 'characters': len(text),
                         'parent_component_key': unit.get('parent_component_key'),
                         'canonical_content_role': unit.get('content_role'),
                         'canonical_provenance': unit.get('provenance')})
    return rows


def selected_occurrences(record):
    # Relax only R1's short-text exclusion. Other exclusions remain explicitly audited.
    selected, excluded = r1.source_units(record)
    short = {(x['section'],x['unit']) for x in excluded if x['reason']=='short_metadata_or_label'}
    for section in record['sections']:
        for unit in section['units']:
            if (section['ordinal'],unit['ordinal']) in short:
                selected.append({'section':section['ordinal'],'unit':unit['ordinal'],
                    'section_name':section['source_metadata'].get('name',''),
                    'text':unit['value']['normalized_text']})
    return sorted(selected,key=lambda x:(x['section'],x['unit']))


def validate_evidence(s, record, ru_by_id):
    if s['record_id'] != record['record_id']:
        raise ValueError('record identity mismatch')
    section = next(x for x in record['sections'] if x['ordinal']==s['section'])
    unit = next(x for x in section['units'] if x['ordinal']==s['unit'])
    text = unit.get('value',{}).get('normalized_text','')
    if not (0 <= s['start'] < s['end'] <= len(text)) or text[s['start']:s['end']] != s['text']:
        raise ValueError('Canonical exact range mismatch')
    if sha256(s['text'].encode()).hexdigest() != s['text_sha256']:
        raise ValueError('snippet hash mismatch')
    rows = [ru_by_id[uid] for uid in s['official_unit_ids']]
    if not rows:
        raise ValueError('missing RU')
    ranges = []
    for row in rows:
        a = row['source']['canonical_address']
        if a['record_id'] != s['record_id']:
            raise ValueError('RU record mismatch')
        if (a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal']) != (s['source'],s['section'],s['unit']):
            raise ValueError('RU endpoint mismatch')
    # Complete occurrence reconstruction, not a substring search in a different passage.
    all_rows = sorted([r for r in ru_by_id.values() if
        (r['source']['canonical_address']['source_identity_key'].rsplit(':',1)[-1],r['source']['canonical_address']['section_ordinal'],r['source']['canonical_address']['canonical_unit_ordinal']) ==
        (s['source'],s['section'],s['unit'])],key=lambda r:r['fragment_selector'].get('fragment_index',0))
    offset = 0
    for row in all_rows:
        end = offset+len(row['retrieval_visible_text'])
        if offset < s['end'] and end > s['start']:
            ranges.append(row['unit_id'])
        offset = end
    if ''.join(r['retrieval_visible_text'] for r in all_rows)!=text or ranges!=s['official_unit_ids']:
        raise ValueError('RU reconstruction/range mismatch')


def grade(j, mechanical, secondary=False, qualified=False):
    """Recommendation only. Never sets active or human acceptance."""
    if mechanical!='PASS' or not j or j.get('verdict')=='insufficient':
        return 'U'
    if j['verdict']=='reject':
        return 'D'
    if j['verdict']=='tentative' or j.get('unsupported_identity_or_cause') or j.get('claim_level')=='speculative':
        return 'C'
    if j['verdict']!='useful':
        return 'U'
    if secondary or j.get('claim_level')!='textual' or not qualified or j.get('relation') not in {'direct_reference','shared_mechanism','background'}:
        return 'B'
    return 'A'


def link_from_review(pair_id, review, documents, origin, discovery=None):
    sides = review.get('sides', {})
    j = review.get('judgment', {})
    mechanical = review.get('mechanical_quote_validation','NOT_VERIFIED')
    if review.get('model_semantic_review')!='REVIEWED_NOT_HUMAN_GOLD':
        j = {}
    evidence = review.get('evidence',{})
    endpoint = {side: {k:documents[cid][k] for k in ['cid','title','record_id','canonical_path','canonical_sha256','source_category']}
                for side,cid in sides.items()}
    secondary = any(d['source_category']['category_screen'] in {'secondary_analysis','observer'} for d in endpoint.values())
    roles = j.get('endpoint_roles',{})
    qualified = all(roles.get(s) in {'game_text','in_world_literature','attributed_testimony'} for s in ['A','B'])
    g = grade(j,mechanical,secondary,qualified)
    return {'schema_version':VERSION,'link_id':pair_id,'endpoints':endpoint,
        'proposition':j.get('proposed_claim',''), 'relation_type':j.get('relation','unknown'),
        'evidence_ids':{side:[s['id'] for s in evidence.get(side,[])] for side in ['A','B']},
        'extracted_evidence':evidence,'increment_a_from_b':j.get('increment_a_from_b','UNKNOWN'),
        'increment_b_from_a':j.get('increment_b_from_a','UNKNOWN'),
        'explanation':j.get('explanation','UNKNOWN'),'interpretation_boundary':j.get('counterevidence','UNKNOWN'),
        'claim_level':j.get('claim_level','unknown'), 'endpoint_roles':roles,
        'role_basis':j.get('role_basis',{}),'anchored_correspondences':j.get('anchored_correspondences',[]),
        'unsupported_identity_or_cause':j.get('unsupported_identity_or_cause'),
        'model_verdict':j.get('verdict','unreviewed'),'model_grade':j.get('grade_recommendation','NOT_PROVIDED'),
        'grade_recommendation':g,'grade_policy':'conservative_program_projection_0.1',
        'navigation_status':'INACTIVE','human_acceptance':'NOT_REVIEWED',
        'mechanical_status':mechanical,'model_review':review.get('model_semantic_review','NOT_REVIEWED'),
        'certainty':'interpretation_not_established_history' if g=='B' else 'no_history_certification',
        'independent_history_witness':False,'reuse_origin':origin,'discovery':discovery or [],
        'generation_self_check':j.get('self_check','LEGACY_NOT_PROVIDED')}


def validate_card(value, visible):
    if not isinstance(value,dict) or not isinstance(value.get('overview'),str) or not value['overview'].strip():
        raise ValueError('missing card overview')
    if not isinstance(value.get('read_ids'),list) or set(value['read_ids']) != set(visible) or len(value['read_ids'])!=len(visible):
        raise ValueError('incomplete reading ledger')
    if not isinstance(value.get('limitations'),str):
        raise ValueError('missing limitations')
    for field in ['details','open_leads','queries']:
        if not isinstance(value.get(field),list):
            raise ValueError('missing '+field)
        for item in value[field]:
            if not isinstance(item,dict) or not isinstance(item.get('text'),str) or not item['text'].strip():
                raise ValueError('invalid '+field+' text')
            refs = item.get('evidence_ids')
            if not isinstance(refs,list) or not refs or not set(refs)<=set(visible):
                raise ValueError('unbound '+field)
            if 'quote' in item:
                raise ValueError('model quotation forbidden')
    return value


class Campaign(qa.DeepSeekCampaign):
    """Use existing audited transport; prompt changes are confined to this process/stage."""
    def __init__(self,run,kind):
        super().__init__(run)
        qa.PROMPT = PROMPTS[kind]
        self.kind = kind
        snapshot=run/'g1-code-snapshots'/(sha256(CODE).hexdigest()+'.py')
        if not snapshot.exists():
            snapshot.parent.mkdir(exist_ok=True)
            snapshot.write_bytes(CODE)
        # Prior UNKNOWNs from the read-only evidence root also remain quarantined.
        source = Path(self.config['source_root'])/'data/retrieval'
        self.quarantined.update(r1.quarantine_fingerprints(source))
        quarantined_roots=list(source.glob('p05-navi-r1-qa-*'))+list(run.parent.glob('p05-navi-g1-*'))
        for root in quarantined_roots:
            for issued in (root/'attempts').glob('*/issued.json'):
                terminal = issued.parent/'terminal.json'
                request = issued.parent/'request.json'
                if request.exists() and (not terminal.exists() or load(terminal).get('state')=='UNKNOWN'):
                    self.quarantined[r1.request_fingerprint(load(request))] = str(issued.parent)

    def reserve(self,job,*args,**kwargs):
        stem = super().reserve(job,*args,**kwargs)
        once(stem/'g1-lineage.json',{'implementation_sha256':sha256(CODE).hexdigest(),
            'stage':self.kind,'prompt_sha256':sha256(PROMPTS[self.kind].encode()).hexdigest()})
        return stem

    def call(self,job,payload):
        result = super().review(job,payload,MODEL)
        return qa.effective_response(self.run,job,result)


def roots(run):
    source = Path(load(run/'protocol.json')['source_root'])
    return source,source/'data/retrieval'/R1_NAME,source/'data/retrieval'/QA_NAME


def prepare(run,source):
    run.mkdir(parents=True,exist_ok=False)
    old = source/'data/retrieval'/R1_NAME
    prior = source/'data/retrieval'/QA_NAME
    corpus = load(old/'corpus.json')['documents']
    docs,snippets = qa.all_sources(prior)
    calibrations = load(prior/'evaluator-only-human-packet-index.json')['cases']
    pairs = load(prior/'adjudications.json')['pairs']
    pair_map = {p['pair_id']:p for p in pairs}
    calibration_ids = {x['pair_id'] for x in calibrations}
    calibration_sources = {cid for p in pairs if p['pair_id'] in calibration_ids for cid in p['sources']}
    old_sources = {d['cid'] for d in corpus}
    _,entries,read = r1.record_reader(source)
    records = {}
    for cid,d in docs.items():
        record,entry = read(cid)
        records[cid] = record
        d['source_category'] = source_category(record)
        d['canonical_path'] = str(source/entry['canonical_record_path'])
    # Source-only, outcome-blind deterministic stratification; no QA verdict/rank used.
    strata = [('book','68'),('weapon','5'),('artifact','218'),('character','25'),('quest','43'),('npc','49'),('secondary','62')]
    selected = []
    for label,channel in strata:
        options = []
        for cid,d in docs.items():
            if cid in old_sources|calibration_sources|{x['cid'] for x in selected}:
                continue
            channels = set(d['source_category']['channels'])
            if channel not in channels:
                continue
            if label!='secondary' and d['source_category']['category_screen'] in {'secondary_analysis','observer'}:
                continue
            body = selected_occurrences(records[cid])
            size = sum(len(u['text']) for u in body)
            if 400 <= size <= 24000:
                options.append(cid)
        for cid in sorted(options,key=lambda c:sha256(('g1-independent-v1|'+c).encode()).hexdigest())[:2]:
            selected.append({'cid':cid,'stratum':label,'reason':'independent_source_only_selection'})
    # Actual corpus channel IDs are checked; an empty stratum is explicit, not guessed.
    regeneration = sorted(corpus,key=lambda d:(-len(d['units']),-sum(len(u['text']) for u in d['units']),d['cid']))[:4]
    selected += [{'cid':d['cid'],'stratum':'legacy_complex','reason':'structure_density_probe_not_independent_gold'} for d in regeneration]
    occurrences = {}
    for item in selected:
        cid = item['cid']
        occurrences[cid] = selected_occurrences(records[cid])
    # Read the accepted RU artifact once; retain complete selected occurrences for reconstruction.
    rm = load(source/r1.RU)
    rp = (source/r1.RU).parent.parent/rm['artifacts']['retrieval_units']['path']
    if r1.file_hash(rp)!=rm['artifacts']['retrieval_units']['sha256']:
        raise ValueError('RU artifact hash mismatch')
    required = {(cid,s['section'],s['unit']) for cid in occurrences for s in occurrences[cid]}
    required.update((s['source'],s['section'],s['unit']) for s in snippets.values())
    rows = defaultdict(list)
    with gzip.open(rp,'rt',encoding='utf-8') as handle:
        for line in handle:
            row = json.loads(line);a=row['source']['canonical_address']
            key=(a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal'])
            if key in required and a['canonical_unit_kind']=='rich_text':
                rows[key].append(row)
    verified = 0
    for key,rr in rows.items():
        rr.sort(key=lambda r:r['fragment_selector'].get('fragment_index',0))
        cid,so,uo=key
        section=next(s for s in records[cid]['sections'] if s['ordinal']==so)
        unit=next(u for u in section['units'] if u['ordinal']==uo)
        if ''.join(r['retrieval_visible_text'] for r in rr)!=unit['value']['normalized_text']:
            raise ValueError('complete RU reconstruction mismatch '+str(key))
    for s in snippets.values():
        rr=rows[(s['source'],s['section'],s['unit'])]
        ru_by_id={r['unit_id']:r for r in rr}
        validate_evidence(s,records[s['source']],ru_by_id);verified+=1
    for cid,units in occurrences.items():
        ids=[]
        for u in units:
            rr=rows[(cid,u['section'],u['unit'])]
            if not rr:
                raise ValueError('no RU for selected occurrence')
            offset_ranges=[];offset=0
            for row in rr:
                offset_ranges.append((offset,offset+len(row['retrieval_visible_text']),row['unit_id']))
                offset=offset_ranges[-1][1]
            for start,end,text in qa.chunks(u['text']):
                sid=f'g1:{cid}:s{u["section"]}:u{u["unit"]}:{start}-{end}'
                s={'id':sid,'source':cid,'record_id':docs[cid]['record_id'],'canonical_sha256':docs[cid]['canonical_sha256'],
                   'section':u['section'],'unit':u['unit'],'section_name':u['section_name'],
                   'start':start,'end':end,'text':text,'text_sha256':sha256(text.encode()).hexdigest(),
                   'official_unit_ids':[uid for lo,hi,uid in offset_ranges if lo<end and hi>start]}
                validate_evidence(s,records[cid],{r['unit_id']:r for r in rr})
                snippets[sid]=s;ids.append(sid)
        docs[cid]['g1_snippet_ids']=ids
    for cid,d in docs.items():
        d['structure_inventory']=inventory(records[cid])
        d['source_lineage']=records[cid]['lineage']
    input_paths=[old/'corpus.json',old/'profiles.json',old/'representations.json',old/'representation-vectors.npz',
                 old/'rankings.json',prior/'adjudications.json',prior/'snippets.json',prior/'source-scope-sensitivity.json',
                 prior/'evaluator-only-human-packet-index.json',prior/'human-review-packet-v2.md',
                 prior/'qa-artifact-inventory.json',old/'artifact-inventory.json']
    protocol={'version':VERSION,'created_at':r1.now(),'source_root':str(source.resolve()),'route':'jizhi','model':MODEL,
        'identity':digest({'version':VERSION,'source_root':str(source.resolve()),'selected':selected}),
        'prompts':PROMPTS,'implementation_sha256':sha256(CODE).hexdigest(),
        'traced_persistent_transport':True,
        'circuit':{'max_model_attempts':180,'max_embedding_attempts':12,'max_tokens':16000000,
                   'max_accounting_rmb':1000000000,'max_unknown_attempts':3,
                   'rates_rmb_per_million':{'model':[0,0],'embedding':[0,0]}},
        'accounting':'Requests/tokens only; no tariff estimate. Unknown remains reserved. No retry after uncertain execution.',
        'active':False,'human_acceptance':'NOT_REVIEWED','full_production_authorized':False,
        'reading_policy':'R1 module selector, short-text exclusion relaxed; all Canonical units inventoried. Not whole-page semantic coverage.',
        'selection':selected,'calibration_pair_ids':sorted(calibration_ids),'calibration_sources':sorted(calibration_sources),
        'independent_candidate_excluded_sources':sorted(calibration_sources|old_sources),
        'empty_strata':[label for label,_ in strata if not any(x['stratum']==label for x in selected)],
        'retrieval_policy':{'raw_full_corpus_ru_count':535802,'lexical_depth':500,'dense_depth':500,'rrf_denominator':60,
            'candidate_positions':[1,2,3,10],'no_profile_index':True,'no_reranker':True,
            'exclusion':'Self and frozen calibration/R1-card sources excluded before candidate-arm budget for independent validation.'}}
    once(run/'protocol.json',protocol)
    once(run/'input-hashes.json',{str(p):r1.file_hash(p) for p in input_paths})
    once(run/'documents.json',docs);once(run/'snippets.json',snippets)
    once(run/'source-checks.json',{'verified_historical_snippets':verified,'verified_canonical_records':len(records),
        'ru_manifest_sha256':r1.file_hash(source/r1.RU),'ru_artifact_sha256':r1.file_hash(rp),
        'canonical_hashes':{cid:d['canonical_sha256'] for cid,d in docs.items()},
        'source_binding':'PASS','semantic_acceptance':'NOT_REVIEWED'})
    snap=run/'input-snapshot';snap.mkdir()
    for p in [Path('AGENTS.md'),Path('docs/current-phase.md'),Path('docs/phases/phase-05-source-bound-semantic-navigation.md'),
              Path('docs/research/phase-05/p05-navi-r1-discovery-20261025.md'),Path('docs/research/phase-05/p05-navi-r1-qa-20261025.md'),Path(__file__)]:
        (snap/p.name).write_bytes(p.read_bytes())
    print(json.dumps({'prepared_sources':len(docs),'new_card_scope':selected,'empty_strata':protocol['empty_strata']},ensure_ascii=False),flush=True)


def legacy_cards(run):
    source,old,prior=roots(run);docs=load(run/'documents.json');snips=load(run/'snippets.json')
    corpus=load(old/'corpus.json')['documents'];profiles=load(old/'profiles.json')['batches']
    rep=load(old/'representations.json');cards=[];failures=[]
    for doc in corpus:
        cid=doc['cid'];d=docs[cid];parts=[]
        for p in profiles:
            if p.get('job',[None]*4)[0]!=cid or p['job'][2:]!=['ordinary',0]:
                continue
            index=p['job'][1];rawpath=old/'responses'/f'profile-ordinary-{cid}-{index}-r0.json'
            raw=load(rawpath);value=raw.get('value',{});batch=r1.batches(doc)[index]
            local={u['id']:u for u in batch};details=[]
            for item in value.get('key_details',[]):
                refs=item.get('refs',[])
                if not refs or not set(refs)<=local.keys():
                    failures.append({'cid':cid,'batch':index,'reason':'invalid_legacy_detail_refs','item':item});continue
                ids=[sid for sid in d['snippet_ids'] if any(
                    snips[sid]['section']==local[r]['section'] and snips[sid]['unit']==local[r]['unit'] and
                    snips[sid]['start']<local[r]['end'] and snips[sid]['end']>local[r]['start'] for r in refs)]
                details.append({'text':item['text'],'evidence_ids':ids,'binding_precision':'legacy_occurrence_not_claim_span',
                    'legacy_quote_used':False,'legacy_quote_exact':any(item.get('quote','~') in local[r]['text'] for r in refs)})
            delivered=[sid for sid in d['snippet_ids'] if any(snips[sid]['section']==u['section'] and snips[sid]['unit']==u['unit'] and
                        snips[sid]['start']<u['end'] and snips[sid]['end']>u['start'] for u in batch)]
            parts.append({'overview':value['summary'],'details':details,'open_leads':[],
                'reading_receipt':{'delivered_occurrences':[{k:u[k] for k in ['section','unit','start','end','id']} for u in batch],
                                   'read_ids':delivered,'state':'HISTORICAL_DELIVERY_NOT_SEMANTIC_COMPLETENESS'},
                'origin':{'path':str(rawpath),'sha256':r1.file_hash(rawpath),'model':'qwen3.8-max'}})
        # Exactly preserve the already cached ordinary query for historical seeds.
        query=rep.get('ordinary',{}).get(cid)
        if query is None:
            query='\n'.join(p['overview']+'\n'+'\n'.join(x['text'] for x in p['details']) for p in parts)
        query_refs=sorted({sid for p in parts for sid in p['reading_receipt']['read_ids']})
        cards.append({'schema_version':VERSION,'card_id':'g1:legacy:'+cid,'source_id':cid,'title':d['title'],
            'record_id':d['record_id'],'canonical_sha256':d['canonical_sha256'],'canonical_path':d['canonical_path'],
            'source_category':d['source_category'],'structure_inventory':d['structure_inventory'],
            'parts':parts,'queries':[{'query_id':digest(query),'text':query,'evidence_ids':query_refs,
                'origin':'R1_CACHED_EXACT' if cid in rep.get('ordinary',{}) else 'DETERMINISTIC_LEGACY_CARD_VIEW',
                'target_documents_supplied':False}],
            'coverage':{'selected_scope_delivered':True,'whole_page_semantic_coverage':False,
                'excluded':doc['excluded'],'detail_limit':'historical at most five details per batch; not a G1 requirement'},
            'reuse_status':'REUSED_LEGACY_COMPATIBILITY','mechanical_status':'PASS',
            'model_quality':'LEGACY_NOT_G1_REVIEWED','human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE'})
    if len(cards)!=128:
        raise ValueError('expected 128 reusable ordinary cards')
    artifact(run,'cards-reused.json',{'cards':cards,'failures':failures})
    pairs=load(prior/'adjudications.json')['pairs'];candidates={p['pair_id']:p for p in qa.all_candidates(prior)}
    links=[link_from_review(p['pair_id'],p['primary'],docs,{'path':str(prior/'adjudications.json'),'pair_id':p['pair_id'],
        'job':p['primary'].get('job'),'model':MODEL},candidates[p['pair_id']]['discoveries']) for p in pairs]
    artifact(run,'links-reused.json',{'links':links,'note':'Legacy review converted, never a new independent G1 judgment; original orientation retained.'})
    sens=load(prior/'source-scope-sensitivity.json')
    unique=next(x for x in sens['novelty'] if x['arm']=='ordinary_profile_query_only' and x['split']=='holdout' and x['k']==20 and x['baseline']=='full_raw6_plus_rewrite_union')
    lm={l['link_id']:l for l in links}
    artifact(run,'ordinary-unique-reuse.json',{'historical_diagnostic':True,'pair_ids':unique['pair_ids'],
        'rows':[{'pair_id':p,'model_verdict':lm[p]['model_verdict'],'g1_grade':lm[p]['grade_recommendation'],
                 'evidence_ids':lm[p]['evidence_ids'],'mechanical_status':lm[p]['mechanical_status']} for p in unique['pair_ids']],
        'independent_validation':False,'not_human_gold':True})
    print(json.dumps({'cards_reused':len(cards),'links_reused':len(links),'grades':dict(Counter(x['grade_recommendation'] for x in links))}),flush=True)


def card_batches(d,snippets,limit=6500):
    current=[];used=0
    for sid in d['g1_snippet_ids']:
        n=len(snippets[sid]['text'])
        if current and used+n>limit:
            yield current;current=[];used=0
        current.append(sid);used+=n
    if current:
        yield current


def card_payload(d,ids,snips):
    return {'source_title':d['title'],'source_category':d['source_category'],
        'segments':[{'id':sid,'section':snips[sid]['section'],'unit':snips[sid]['unit'],
                     'section_name':snips[sid]['section_name'],'text':snips[sid]['text']} for sid in ids],
        'coverage':{'delivered_ids':ids,'selected_snippet_count':len(d['g1_snippet_ids']),
                    'whole_page_semantic_coverage':False}}


def generate_cards(run):
    docs=load(run/'documents.json');snips=load(run/'snippets.json');protocol=load(run/'protocol.json')
    campaign=Campaign(run,'card');jobs=[]
    for selection in protocol['selection']:
        cid=selection['cid'];d=docs[cid]
        for i,ids in enumerate(card_batches(d,snips)):
            jobs.append((cid,i,ids,card_payload(d,ids,snips)))
    results={}
    def work(job):
        cid,i,ids,payload=job;key=f'card-{cid}-{i}'
        artifact(run,'deliveries/'+key+'.json',payload)
        return key,campaign.call(key,payload)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(work,j) for j in jobs]):
            key,res=future.result();results[key]=res
            print(json.dumps({'card_job':key,'state':res.get('state'),'complete':res.get('complete')}),flush=True)
    cards=[];failures=[]
    for selection in protocol['selection']:
        cid=selection['cid'];d=docs[cid];parts=[];queries=[];bad=False
        for c,i,ids,payload in jobs:
            if c!=cid:
                continue
            key=f'card-{cid}-{i}';res=results[key]
            try:
                value=validate_card(model_value(res),ids)
                parts.append(value|{'origin':{'job':key,'model':MODEL,'response_sha256':r1.file_hash(run/'responses'/(key+'.json'))}})
                queries.extend(value['queries'])
            except (ValueError,KeyError,TypeError) as exc:
                failures.append({'job':key,'source':cid,'state':res.get('state'),'error':str(exc)});bad=True
        dedup={q['text']:q for q in queries}
        queries=[q|{'query_id':digest(q['text']),'target_documents_supplied':False,'origin':'G1_SOURCE_ONLY_CARD'} for q in dedup.values()]
        card={'schema_version':VERSION,'card_id':'g1:new:'+cid,'source_id':cid,'title':d['title'],'record_id':d['record_id'],
              'canonical_path':d['canonical_path'],'canonical_sha256':d['canonical_sha256'],
              'source_category':d['source_category'],'structure_inventory':d['structure_inventory'],
              'parts':parts,'queries':queries,'selection':selection,
              'coverage':{'selected_scope_delivered':not bad,'whole_page_semantic_coverage':False,
                          'delivered_ids':[sid for p in parts for sid in p['read_ids']],
                          'selected_ids':d['g1_snippet_ids'],'unread_ids':[sid for sid in d['g1_snippet_ids'] if not any(sid in p['read_ids'] for p in parts)]},
              'mechanical_status':'FAIL' if bad else 'PASS','reuse_status':'NEW_VERSION' if selection['stratum']=='legacy_complex' else 'NEW_SOURCE',
              'model_quality':'SELF_CHECK_ONLY','human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE'}
        cards.append(card)
    artifact(run,'cards-generated.json',{'cards':cards,'failures':failures})


def retrieve(run):
    from scipy import sparse
    from genshin_corpus.retrieval.lexical import analyze
    source,old,_=roots(run);protocol=load(run/'protocol.json')
    index=source/'data/retrieval/p05-navi-r1-discovery-20261025-r1/index'
    meta=load(index/'manifest.json')
    if r1.file_hash(index/'lexical-bm25.npz')!=meta['matrix_sha256']:
        raise ValueError('lexical acceleration hash')
    matrix=sparse.load_npz(index/'lexical-bm25.npz');vocab=load(index/'lexical-vocab.json')
    with gzip.open(index/'lexical-ids.json.gz','rt',encoding='utf-8') as h:
        lex_ids=json.load(h)
    with gzip.open(old/'official-locators.json.gz','rt',encoding='utf-8') as h:
        locators=json.load(h)
    dm=load(source/r1.DENSE);dr=(source/r1.DENSE).parent.parent
    vector_path=dr/dm['artifacts']['vectors']['path']
    if r1.file_hash(vector_path)!=dm['artifacts']['vectors']['sha256']:
        raise ValueError('dense vector hash')
    vectors=np.load(vector_path,mmap_mode='r')
    row_path=dr/dm['artifacts']['rows']['path']
    if r1.file_hash(row_path)!=dm['artifacts']['rows']['sha256']:
        raise ValueError('dense row hash')
    with gzip.open(row_path,'rt',encoding='utf-8') as h:
        dense_ids=[json.loads(line)['unit_id'] for line in h]
    if vectors.shape!=(535802,2048) or len(lex_ids)!=535802:
        raise ValueError('accepted corpus shape')
    cards=[c for c in load(run/'cards-generated.json')['cards'] if c['selection']['stratum']!='legacy_complex' and c['mechanical_status']=='PASS']
    # Bounded query projection: all queries cached; at most four diverse source-order queries executed per source.
    executed=[];omitted=[]
    for c in cards:
        qs=c['queries']
        chosen=list(dict.fromkeys(round(i*(len(qs)-1)/max(min(4,len(qs))-1,1)) for i in range(min(4,len(qs)))))
        for i,q in enumerate(qs):
            (executed if i in chosen else omitted).append({'source':c['source_id'],**q})
    artifact(run,'query-execution-plan.json',{'queries':executed,'omitted_queries':omitted,
        'policy':'at most four source-order evenly spaced queries per independent card; all cached; no target-aware selection'})
    campaign=Campaign(run,'card') # embedding uses its own established Qwen wire, never a model prompt.
    campaign.config['circuit']['max_embedding_attempts']=12
    values=r1.embed_many(campaign,'query',[q['text'] for q in executed])
    forbidden=set(protocol['independent_candidate_excluded_sources'])
    output={}
    for card in cards:
        cid=card['source_id'];excluded=forbidden|{cid}
        lm=np.array([i for i,u in enumerate(lex_ids) if locators[u][0] not in excluded])
        dmask=np.array([i for i,u in enumerate(dense_ids) if locators[u][0] not in excluded])
        rankings=[];qrecords=[];support=defaultdict(list)
        for q in [x for x in executed if x['source']==cid]:
            qv=values[q['text']]
            scores=np.asarray(vectors@qv)
            qt=sparse.lil_matrix((1,len(vocab)),dtype=np.float64)
            for token,n in Counter(analyze(q['text'])).items():
                if token in vocab:
                    qt[0,vocab[token]]=n
            ls=np.asarray((matrix@qt.tocsr().T).toarray())[:,0]
            ll=[lex_ids[i] for i in r1.stable_top(ls,lex_ids,500,lm)]
            dd=[dense_ids[i] for i in r1.stable_top(scores,dense_ids,500,dmask,positive_only=False)]
            fused=r1.rrf([ll,dd],500);pages=[]
            for uid in fused:
                target=locators[uid][0]
                if target not in pages:
                    pages.append(target)
                if len(support[target])<6 and uid not in support[target]:
                    support[target].append(uid)
            rankings.append(pages)
            qrecords.append({'query_id':q['query_id'],'lexical_unit_ids':ll,'dense_unit_ids':dd,'hybrid_unit_ids':fused,'documents':pages})
        pages=r1.rrf(rankings,100)
        output[cid]={'documents':pages,'ru_support':dict(support),'query_records':qrecords,
                     'selected':[{'target':pages[n-1],'rank':n} for n in protocol['retrieval_policy']['candidate_positions'] if len(pages)>=n]}
        print(json.dumps({'retrieved_source':cid,'queries':len(qrecords),'top3':pages[:3]}),flush=True)
    artifact(run,'rankings.json',{'sources':output,'index_hashes':{'lexical':meta['matrix_sha256'],'dense':dm['artifacts']['vectors']['sha256']},
        'full_raw_corpus':535802,'semantic_profile_index':False,'not_production_phase04_ranking':True})


def prepare_candidates(run):
    source,_,_=roots(run);docs=load(run/'documents.json');snips=load(run/'snippets.json')
    protocol=load(run/'protocol.json');rankings=load(run/'rankings.json')['sources'];_,entries,read=r1.record_reader(source)
    targets={x['target'] for v in rankings.values() for x in v['selected']}
    needed={};records={}
    for cid in targets:
        record,entry=read(cid);records[cid]=record
        units=selected_occurrences(record)
        if not units:
            units,_,_=qa.chosen_body(record)
        needed[cid]=units
        if cid not in docs:
            docs[cid]={'cid':cid,'title':record['record_metadata']['name'],'record_id':record['record_id'],
                'canonical_path':str(source/entry['canonical_record_path']),'canonical_sha256':entry['canonical_record_sha256'],
                'source_category':source_category(record),'structure_inventory':inventory(record),'source_lineage':record['lineage'],
                'body_policy':'G1_R1_selector_short_text_included'}
    rm=load(source/r1.RU);rp=(source/r1.RU).parent.parent/rm['artifacts']['retrieval_units']['path']
    keys={(cid,u['section'],u['unit']) for cid,uu in needed.items() for u in uu};rows=defaultdict(list)
    with gzip.open(rp,'rt',encoding='utf-8') as h:
        for line in h:
            r=json.loads(line);a=r['source']['canonical_address'];key=(a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal'])
            if key in keys and a['canonical_unit_kind']=='rich_text':
                rows[key].append(r)
    for cid,uu in needed.items():
        ids=[]
        for u in uu:
            rr=sorted(rows[(cid,u['section'],u['unit'])],key=lambda r:r['fragment_selector'].get('fragment_index',0))
            if not rr or ''.join(r['retrieval_visible_text'] for r in rr)!=u['text']:
                raise ValueError('candidate RU reconstruction')
            ranges=[];offset=0
            for row in rr:
                ranges.append((offset,offset+len(row['retrieval_visible_text']),row['unit_id']));offset=ranges[-1][1]
            for start,end,text in qa.chunks(u['text']):
                sid=f'g1:{cid}:s{u["section"]}:u{u["unit"]}:{start}-{end}'
                s={'id':sid,'source':cid,'record_id':docs[cid]['record_id'],'canonical_sha256':docs[cid]['canonical_sha256'],
                    'section':u['section'],'unit':u['unit'],'section_name':u['section_name'],'start':start,'end':end,
                    'text':text,'text_sha256':sha256(text.encode()).hexdigest(),
                    'official_unit_ids':[uid for lo,hi,uid in ranges if lo<end and hi>start]}
                validate_evidence(s,records[cid],{r['unit_id']:r for r in rr})
                if sid in snips and s!=snips[sid]:
                    raise ValueError('snippet collision')
                snips[sid]=s;ids.append(sid)
        docs[cid]['g1_snippet_ids']=ids
    pairs=[]
    for cid,value in rankings.items():
        for item in value['selected']:
            target=item['target'];pair={'pair_id':qa.pid(cid,target),'sources':[cid,target],
                'discovery':{'source':cid,'target':target,'rank':item['rank'],'ru_support':value['ru_support'].get(target,[])}}
            if not set(pair['sources']).isdisjoint(protocol['calibration_sources']):
                raise ValueError('calibration leakage')
            pairs.append(pair)
    artifact(run,'candidate-documents.json',docs);artifact(run,'candidate-snippets.json',snips)
    artifact(run,'candidates.json',{'pairs':pairs,'selection_frozen_before_B':True,
        'independent_of_128_sources':all(set(p['sources']).isdisjoint(protocol['independent_candidate_excluded_sources']) for p in pairs),
        'gold_status':'NOT_HUMAN_GOLD'})


def pair_payload(pair,docs,snips):
    # Existing QA selection primitive; substitutions are derived copies only.
    selected={cid:dict(d) for cid,d in docs.items() if cid in pair['sources']}
    for cid,d in selected.items():
        d['snippet_ids']=d.get('g1_snippet_ids',d.get('snippet_ids',[]))
        d['body_policy']=d.get('body_policy','G1_R1_selector_short_text_included')
    payload,aliases,sides=qa.context_for(pair,selected,snips,limit=18000)
    for side,cid in sides.items():
        payload[side]['source_category']=docs[cid]['source_category']
    return payload,aliases,sides


def validate_new_review(value,aliases,snips,sides):
    result=qa.validate(value,aliases,snips)
    if result['semantic_schema_errors']:
        raise ValueError('semantic schema: '+str(result['semantic_schema_errors']))
    for side,ev in result['evidence'].items():
        if any(s['source']!=sides[side] for s in ev):
            raise ValueError('wrong evidence endpoint')
    if set(value.get('endpoint_roles',{}))!={'A','B'} or any(x not in ROLES for x in value['endpoint_roles'].values()):
        raise ValueError('missing/invalid endpoint roles')
    if set(value.get('role_basis',{}))!={'A','B'} or any(not isinstance(x,str) or not x.strip() for x in value['role_basis'].values()):
        raise ValueError('missing role basis')
    if not isinstance(value.get('anchored_correspondences'),list) or not all(isinstance(x,str) and x.strip() for x in value['anchored_correspondences']):
        raise ValueError('missing correspondences')
    if value.get('grade_recommendation') not in set('ABCDU') or not isinstance(value.get('self_check'),str):
        raise ValueError('invalid grade/self check')
    if any(k in value for k in ['a_quote','b_quote','quote']):
        raise ValueError('model quote forbidden')
    return result|{'sides':sides}


def generate_links(run):
    docs=load(run/'candidate-documents.json');snips=load(run/'candidate-snippets.json');pairs=load(run/'candidates.json')['pairs']
    campaign=Campaign(run,'link');links=[];failures=[]
    def work(pair):
        key='link-'+pair['pair_id'];payload,aliases,sides=pair_payload(pair,docs,snips)
        artifact(run,'deliveries/'+key+'.json',{'payload':payload,'aliases':aliases,'sides':sides})
        res=campaign.call(key,payload)
        try:
            review=validate_new_review(model_value(res),aliases,snips,sides)
            review['coverage']={s:payload[s]['coverage'] for s in ['A','B']}
            link=link_from_review(pair['pair_id'],review,docs,{'job':key,'model':MODEL,'reuse':False},[pair['discovery']])
            link['reading_coverage']=review['coverage']
            return link,None
        except (ValueError,KeyError,TypeError) as exc:
            failure={'pair_id':pair['pair_id'],'sources':pair['sources'],'job':key,'error':str(exc),'state':res.get('state')}
            return link_from_review(pair['pair_id'],{},docs,{'job':key,'failed':True},[pair['discovery']]),failure
    with ThreadPoolExecutor(max_workers=3) as pool:
        for f in as_completed([pool.submit(work,p) for p in pairs]):
            link,failure=f.result();links.append(link)
            if failure:
                failures.append(failure)
            print(json.dumps({'link':link['link_id'],'grade':link['grade_recommendation'],'failure':failure}),flush=True)
    artifact(run,'links-generated.json',{'links':sorted(links,key=lambda x:x['link_id']),'failures':failures})


def effective_links(run):
    path=run/'links-qualified.json'
    return load(path if path.exists() else run/'links-generated.json')


def qualify_links(run):
    """New, explicit schema-completion contract, only for resolved base-valid failures."""
    initial=load(run/'links-generated.json');snips=load(run/'candidate-snippets.json');docs=load(run/'candidate-documents.json')
    selected=[]
    for f in initial['failures']:
        response=load(run/'responses'/(f['job']+'.json'))
        response=qa.effective_response(run,f['job'],qa.reconcile_response(run,f['job'],response))
        delivery=load(run/'deliveries'/(f['job']+'.json'))
        if response.get('state')!='RESOLVED' or not response.get('complete'):
            continue
        try:
            base=qa.validate(response['value'],delivery['aliases'],snips)
            if base['semantic_schema_errors']:
                continue
        except (ValueError,KeyError,TypeError):
            continue
        selected.append({'pair_id':f['pair_id'],'job':f['job'],'base_valid':True,'original_failure':f['error']})
    artifact(run,'protocol-addendum-qualification.json',{'selected':selected,'prompt':QUALIFY_PROMPT,
        'reason':'Original B appended-field prompt sometimes returned only the base QA schema. No UNKNOWN retry or silent field fabrication.',
        'scope':'Only resolved base-valid schema failures; original U and raw response preserved. New explicit whole-object contract.',
        'no_new_discovery':True,'implementation_sha256':sha256(CODE).hexdigest()})
    campaign=Campaign(run,'qualification');byid={l['link_id']:l for l in initial['links']};out=[]
    selected_ids={x['pair_id'] for x in selected}
    failed=[f for f in initial['failures'] if f['pair_id'] not in selected_ids]
    def work(item):
        pid=item['pair_id'];delivery=load(run/'deliveries'/(item['job']+'.json'))
        original=load(run/'responses'/(item['job']+'.json'))
        original=qa.effective_response(run,item['job'],qa.reconcile_response(run,item['job'],original))
        payload=dict(delivery['payload']);payload['candidate']=original['value']
        key='qualification-'+pid;artifact(run,'deliveries/'+key+'.json',{'payload':payload,'aliases':delivery['aliases'],'sides':delivery['sides']})
        result=campaign.call(key,payload)
        try:
            review=validate_new_review(model_value(result),delivery['aliases'],snips,delivery['sides'])
            l=link_from_review(pid,review,docs,{'job':key,'original_job':item['job'],'schema_completion_new_call':True},byid[pid]['discovery'])
            l['reading_coverage']={s:payload[s]['coverage'] for s in ['A','B']}
            return l,None
        except (ValueError,KeyError,TypeError) as exc:
            return byid[pid],{'pair_id':pid,'job':key,'error':str(exc),'state':result.get('state')}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(work,item) for item in selected]):
            l,failure=future.result();byid[l['link_id']]=l
            out.append({'pair_id':l['link_id'],'effective_grade':l['grade_recommendation'],'failure':failure})
            if failure:
                failed.append(failure)
            print(json.dumps(out[-1]),flush=True)
    artifact(run,'links-qualified.json',{'links':sorted(byid.values(),key=lambda l:l['link_id']),
        'original_failures':initial['failures'],'completion_results':out,'failures':failed,
        'note':'Separate schema-completion experiment; original first-pass dispositions stay unchanged.'})


def targeted_review(run):
    docs=load(run/'candidate-documents.json');snips=load(run/'candidate-snippets.json');protocol=load(run/'protocol.json')
    links=effective_links(run)['links']
    # Deterministic risk sample; at least one per outcome plus role/risk/partial delivery.
    chosen=[];seen=set()
    def admit(link,reason):
        if link['link_id'] not in seen:
            chosen.append({'link_id':link['link_id'],'reason':reason});seen.add(link['link_id'])
    for g in 'ABCDU':
        for l in sorted(links,key=lambda x:x['link_id']):
            if l['grade_recommendation']==g:
                admit(l,'outcome_stratum_'+g);break
    for l in sorted(links,key=lambda x:x['link_id']):
        risk=l['unsupported_identity_or_cause'] or l['model_grade']!=l['grade_recommendation'] or any(
            d['source_category']['category_screen'] in {'secondary_analysis','observer'} for d in l['endpoints'].values())
        if risk and len(chosen)<12:
            admit(l,'source_role_identity_or_grade_dispute')
    legacy_byid={l['link_id']:l for l in load(run/'links-reused.json')['links']}
    unique=load(run/'ordinary-unique-reuse.json')['rows']
    for row in unique:
        if row['model_verdict']=='useful' and row['g1_grade']=='C':
            admit(legacy_byid[row['pair_id']],'historical_ordinary_unique_identity_risk_not_independent_gold')
    for l in links:
        if l['link_id']=='24f3d8d90a7725b0a1fd':
            admit(l,'posthoc_source_reading_dispute_fire_sage_loneliness_may_be_generic')
    artifact(run,'targeted-review-plan.json',{'cases':chosen,'all_outputs_second_review':False,
        'same_model_separate_call':True,'independent_human_review':False})
    campaign=Campaign(run,'audit');byid=legacy_byid|{l['link_id']:l for l in links};output=[]
    def audit_one(item):
        l=byid[item['link_id']]
        delivery_path=run/'deliveries'/('link-'+l['link_id']+'.json')
        if delivery_path.exists():
            delivery=load(delivery_path)
        else:
            _,_,prior=roots(run)
            previous=load(prior/'deliveries'/(l['link_id']+'.json'))
            delivery={'payload':previous['payload'],'aliases':previous['aliases'],'sides':previous['sides']}
            for side,cid in delivery['sides'].items():
                delivery['payload'][side]['source_category']=docs[cid]['source_category']
        payload=dict(delivery['payload']);payload['candidate']={k:l[k] for k in ['proposition','interpretation_boundary','model_verdict','grade_recommendation']}
        key='audit-'+l['link_id'];artifact(run,'deliveries/'+key+'.json',payload)
        res=campaign.call(key,payload)
        try:
            review=validate_new_review(model_value(res),delivery['aliases'],snips,delivery['sides'])
            projected=link_from_review(l['link_id'],review,docs,{'job':key,'separate_model_call':True})
            result=item|{'first_grade':l['grade_recommendation'],'review':projected,
                'agreement':l['grade_recommendation']==projected['grade_recommendation'],
                'resolution':'RETAIN_BOTH_MODEL_JUDGMENTS_NO_HUMAN_ACCEPTANCE'}
        except (ValueError,TypeError,KeyError) as exc:
            result=item|{'failure':str(exc),'state':res.get('state')}
        print(json.dumps({'audit':l['link_id'],'state':res.get('state')}),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        output=list(pool.map(audit_one,chosen))
    artifact(run,'targeted-reviews.json',{'cases':output})
    # Audit all new/regenerated cards against their own delivered source; one combined call only if within cap.
    campaign=Campaign(run,'card_audit');card_checks=[]
    oldcards={c['source_id']:c for c in load(run/'cards-reused.json')['cards']}
    def check_card(card):
        cid=card['source_id'];d=docs[cid];ids=d['g1_snippet_ids']
        payload=card_payload(d,ids,snips)
        payload['candidate']={'parts':card['parts'],'queries':card['queries']}
        if cid in oldcards:
            payload['previous_card_for_comparison']={'parts':oldcards[cid]['parts'],'queries':oldcards[cid]['queries'],
                'note':'Historical comparison only; evaluate candidate against source, not against old model authority.'}
        key='card-audit-'+cid;artifact(run,'deliveries/'+key+'.json',payload)
        res=campaign.call(key,payload);value=res.get('value',{})
        valid=res.get('complete') and value.get('coverage') in {'adequate','material_omission','unsupported','insufficient'} and value.get('query_quality') in {'specific','generic','leakage','insufficient'} and isinstance(value.get('evidence_ids'),list) and bool(value['evidence_ids']) and set(value['evidence_ids'])<=set(ids)
        result={'source':cid,'valid_model_check':bool(valid),'judgment':value,'human_acceptance':'NOT_REVIEWED','job':key}
        print(json.dumps({'card_audit':cid,'coverage':value.get('coverage')}),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        card_checks=list(pool.map(check_card,load(run/'cards-generated.json')['cards']))
    artifact(run,'card-quality-checks.json',{'checks':card_checks,'same_model_separate_call':True,'human_acceptances':0})


def finish(run):
    source,_,_=roots(run);docs=load(run/'candidate-documents.json');snips=load(run/'candidate-snippets.json')
    for path,expected in load(run/'input-hashes.json').items():
        if r1.file_hash(Path(path))!=expected:
            raise ValueError('historical artifact changed '+path)
    for d in docs.values():
        if r1.file_hash(Path(d['canonical_path']))!=d['canonical_sha256']:
            raise ValueError('Canonical source drift')
    reused=load(run/'links-reused.json')['links'];new=effective_links(run)['links']
    checks=0
    for l in reused+new:
        for side,ev in l['extracted_evidence'].items():
            for s in ev:
                if snips.get(s['id'])!=s:
                    raise ValueError('evidence projection differs')
                if s['source']!=l['endpoints'][side]['cid']:
                    raise ValueError('link endpoint mismatch')
                checks+=1
        if l['human_acceptance']!='NOT_REVIEWED' or l['navigation_status']!='INACTIVE':
            raise ValueError('unauthorized adoption')
    campaign=Campaign(run,'audit')
    states=Counter(t.get('state') for t in campaign.terminals.values())
    counts=Counter();usage=Counter()
    for job,issued in campaign.reservations.items():
        stage=('embedding' if issued['kind']=='embedding' else 'card_quality' if job.startswith('card-audit-')
               else 'card_generation' if job.startswith('card-') else 'link_generation' if job.startswith('link-')
               else 'link_targeted_review' if job.startswith('audit-') else job.split('-')[0])
        counts[stage]+=1
        t=campaign.terminals.get(job,{})
        for k in ['input_tokens','output_tokens']:
            usage[k]+=(t.get('usage') or {}).get(k,0) or 0
    cards=load(run/'cards-generated.json');oldcards=load(run/'cards-reused.json')
    independent=[c for c in cards['cards'] if c['selection']['stratum']!='legacy_complex']
    review=load(run/'targeted-reviews.json')['cases'];quality=load(run/'card-quality-checks.json')['checks']
    findings=load(run/'agent-review-findings.json') if (run/'agent-review-findings.json').exists() else {'link_findings':[]}
    hold={x['link_id']:x for x in findings['link_findings']}
    byreview={x['link_id']:x for x in review}
    final_links=[]
    for link in reused+new:
        l=dict(link);r=byreview.get(l['link_id']);disposition='INITIAL_MODEL_RECOMMENDATION_ONLY'
        final_grade=l['grade_recommendation']
        if r:
            if 'failure' in r:
                disposition='TARGETED_REVIEW_FAILED_UNRESOLVED'
                if final_grade in {'A','B'}:
                    final_grade='C'
            elif not r['agreement']:
                disposition='MODEL_DISAGREEMENT_REQUIRES_DECISION'
                final_grade='U' if 'U' in {final_grade,r['review']['grade_recommendation']} else 'C'
            else:
                disposition='SAME_MODEL_SEPARATE_CALL_AGREES_NOT_HUMAN_ACCEPTANCE'
        if l['link_id'] in hold:
            disposition='AGENT_FLAGGED_SEMANTIC_STANDARD_DISPUTE_NOT_HUMAN_GOLD'
            final_grade='C'
        l['initial_grade_recommendation']=l['grade_recommendation']
        l['grade_recommendation']=final_grade
        l['review_disposition']=disposition
        if r:
            l['targeted_review_reference']={'artifact':'targeted-reviews.json','link_id':l['link_id']}
        final_links.append(l)
    artifact(run,'navigation-knowledge.json',{'schema_version':VERSION,'links':final_links,
        'authority':'Inactive graded navigation proposals. No human acceptance or Canonical facts.',
        'card_artifacts':['cards-reused.json','cards-generated.json'],
        'dispute_policy':'Keep both judgments; disagreement/review failure of A/B enters C, evidence failure U. Agent dispute is not human rejection.'})
    replay=load(run/'ordinary-unique-replay.json')
    history=load(run/'historical-integrity-audit.json')
    if history['status']!='PASS':
        raise ValueError('historical input inventory changed')
    summary={'version':VERSION,'integrity_audit_status':'PASS','evidence_projection_checks':checks,
        'verified_source_records':len(docs),'reused_cards':len(oldcards['cards']),
        'new_source_cards':len(independent),'regenerated_existing_cards':len(cards['cards'])-len(independent),
        'generated_card_versions':len(cards['cards']),'card_generation_failures':cards['failures'],
        'legacy_links_reused':len(reused),'legacy_valid_model_reviews_reused':sum(l['model_verdict']!='unreviewed' for l in reused),
        'new_links':len(new),'initial_new_link_failures':load(run/'links-generated.json')['failures'],
        'effective_new_link_failures':effective_links(run)['failures'],
        'reused_grades':dict(Counter(l['grade_recommendation'] for l in reused)),
        'new_grades':dict(Counter(l['grade_recommendation'] for l in new)),
        'new_model_verdicts':dict(Counter(l['model_verdict'] for l in new)),
        'card_model_quality':dict(Counter(x.get('judgment',{}).get('coverage','unreviewed') for x in quality)),
        'targeted_second_reviews':len(review),'grade_disagreements':sum(x.get('agreement') is False for x in review),
        'targeted_valid_model_reviews':sum('review' in x for x in review),
        'targeted_review_failures':[x for x in review if 'failure' in x],
        'final_grades_all':dict(Counter(l['grade_recommendation'] for l in final_links)),
        'final_grades_new':dict(Counter(l['grade_recommendation'] for l in final_links[len(reused):])),
        'ordinary_unique_replayed':replay['all_unique_rediscovered'],'ordinary_unique_useful_replayed':replay['old_useful_rediscovered'],
        'history_inventory_files_checked':sum(x['checked'] for x in history['inventories']),
        'request_counts_by_stage':dict(counts),'terminal_states':dict(states),'known_usage':dict(usage),
        'outstanding_requests':[job for job in campaign.reservations if job not in campaign.terminals],
        'human_acceptances':0,'production_ready':False,
        'independent_validation_gold':'NONE; source-disjoint bounded evaluation, not human ground truth',
        'historical_inputs_unchanged':True,'production_integration':False,'git_commit_push':False}
    artifact(run,'summary.json',summary)
    inventory_rows={str(p.relative_to(run)):r1.file_hash(p) for p in sorted(run.rglob('*')) if p.is_file() and p.name!='g1-artifact-inventory.json'}
    artifact(run,'g1-artifact-inventory.json',{'files':inventory_rows,'count':len(inventory_rows)})
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def history_audit(run):
    source,old,prior=roots(run);result=[]
    for path,base in [(old/'artifact-inventory.json',old),(prior/'qa-artifact-inventory.json',source)]:
        rows=load(path)['files']
        def check(row):
            p=base/row['path']
            if not p.exists():
                return {'path':str(p),'reason':'missing'}
            if r1.file_hash(p)!=row['sha256']:
                return {'path':str(p),'reason':'hash_mismatch'}
            return None
        failures=[]
        with ThreadPoolExecutor(max_workers=6) as pool:
            for error in pool.map(check,rows):
                if error:
                    failures.append(error)
        result.append({'inventory':str(path),'checked':len(rows),'failures':failures})
        print(json.dumps({'historical_inventory':path.name,'checked':len(rows),'mismatches':len(failures)}),flush=True)
    artifact(run,'historical-integrity-audit.json',{'inventories':result,
        'status':'PASS' if not any(x['failures'] for x in result) else 'FAIL'})


def replay_unique(run):
    """Actually execute the cached ordinary queries/vectors; zero new model/embedding calls."""
    from scipy import sparse
    from genshin_corpus.retrieval.lexical import analyze
    source,old,_=roots(run);index=source/'data/retrieval/p05-navi-r1-discovery-20261025-r1/index'
    meta=load(index/'manifest.json')
    if r1.file_hash(index/'lexical-bm25.npz')!=meta['matrix_sha256']:
        raise ValueError('replay lexical hash')
    matrix=sparse.load_npz(index/'lexical-bm25.npz');vocab=load(index/'lexical-vocab.json')
    with gzip.open(index/'lexical-ids.json.gz','rt',encoding='utf-8') as h:
        lex_ids=json.load(h)
    with gzip.open(old/'official-locators.json.gz','rt',encoding='utf-8') as h:
        locators=json.load(h)
    dm=load(source/r1.DENSE);dr=(source/r1.DENSE).parent.parent
    vectors=np.load(dr/dm['artifacts']['vectors']['path'],mmap_mode='r')
    with gzip.open(dr/dm['artifacts']['rows']['path'],'rt',encoding='utf-8') as h:
        dense_ids=[json.loads(line)['unit_id'] for line in h]
    rep=load(old/'representations.json');arrays=np.load(old/'representation-vectors.npz')
    qvectors=dict(zip(rep['query_texts'],arrays['queries']))
    cards={c['source_id']:c for c in load(run/'cards-reused.json')['cards']}
    original=load(old/'rankings.json')['sources'];replayed={};observations={}
    for cid,q in rep['ordinary'].items():
        if cards[cid]['queries'][0]['text']!=q:
            raise ValueError('cached ordinary query drift')
        lm=np.array([i for i,u in enumerate(lex_ids) if locators[u][0]!=cid])
        dmask=np.array([i for i,u in enumerate(dense_ids) if locators[u][0]!=cid])
        scores=np.asarray(vectors@qvectors[q])
        qt=sparse.lil_matrix((1,len(vocab)),dtype=np.float64)
        for token,n in Counter(analyze(q)).items():
            if token in vocab:
                qt[0,vocab[token]]=n
        ls=np.asarray((matrix@qt.tocsr().T).toarray())[:,0]
        ll=[lex_ids[i] for i in r1.stable_top(ls,lex_ids,500,lm)]
        dd=[dense_ids[i] for i in r1.stable_top(scores,dense_ids,500,dmask,positive_only=False)]
        hybrid=r1.rrf([ll,dd],500);pages=[]
        for uid in hybrid:
            c=locators[uid][0]
            if c not in pages:
                pages.append(c)
        old_top=original[cid]['full']['ordinary_profile_query_only']['documents'][:20]
        replayed[cid]={'documents':pages[:20],'same_top20_order':pages[:20]==old_top,
                       'old_top20':old_top,'query_id':digest(q),'hybrid_unit_ids':hybrid}
        for n,target in enumerate(pages[:20],1):
            observations.setdefault(qa.pid(cid,target),[]).append({'source':cid,'target':target,'rank':n})
        print(json.dumps({'cached_ordinary_replay':cid,'same_order':pages[:20]==old_top}),flush=True)
    unique=load(run/'ordinary-unique-reuse.json')['rows']
    rows=[r|{'rediscovered':r['pair_id'] in observations,'replay_observations':observations.get(r['pair_id'],[])} for r in unique]
    artifact(run,'ordinary-unique-replay.json',{'sources':replayed,'rows':rows,
        'all_unique_rediscovered':sum(r['rediscovered'] for r in rows),
        'old_useful_rediscovered':sum(r['rediscovered'] and r['model_verdict']=='useful' for r in rows),
        'model_calls':0,'embedding_calls':0,'no_profile_index':True,'not_independent_gold':True,
        'purpose':'Compatibility replay of reusable cached source queries on unchanged full raw Hybrid. Not a fresh-query generalization claim.'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['prepare','reuse','cards','retrieve','candidates','links','qualify','review','history-audit','replay-unique','finish'])
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--source-root',type=Path)
    args=p.parse_args()
    if args.stage=='prepare':
        if args.source_root is None:
            p.error('--source-root required for prepare')
        prepare(args.run,args.source_root)
    else:
        {'reuse':legacy_cards,'cards':generate_cards,'retrieve':retrieve,'candidates':prepare_candidates,
         'links':generate_links,'qualify':qualify_links,'review':targeted_review,
         'history-audit':history_audit,'replay-unique':replay_unique,'finish':finish}[args.stage](args.run)


if __name__=='__main__':
    main()
