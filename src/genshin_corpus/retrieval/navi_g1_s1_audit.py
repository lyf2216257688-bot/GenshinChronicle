"""G1-S1 provider-free evidence packets and comparison of agent-authored reviews.

This module performs no semantic grading. Grades, propositions and reasons are
authored in a separate review ledger and validated here. Only the Python
standard library is imported; there is no model, embedding or network seam.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re

AUTHORITY = 'AGENT_REVIEW_NOT_HUMAN_GOLD'
VERSION = 'p05-navi-g1-s1-0.2'
GRADES = set('ABCDU')
SUPPORT = {'EXPLICIT','GROUNDED_INTERPRETATION','WEAK','INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION','UNDETERMINED'}
PROPOSITION_ROLES = {'ORIGINAL_CORE','ORIGINAL_COMPONENT','SAFE_REPHRASING','COUNTERFACTUAL_GUARD'}
RELEASE = {'ELIGIBLE_PENDING_RELEASE','REVIEW_REQUIRED','CATALOG_ONLY','EXCLUDED','UNRESOLVED'}
REASONS = {'DEFINITION_MIGRATION','PROPOSITION_MIXING','SOURCE_ROLE','INCREMENT_INSUFFICIENT',
           'PRIOR_INCONSISTENCY','EVIDENCE_MISSING','NEW_SOURCE_INSPECTION','SPECIFICITY_DISPUTE',
           'CLAIM_SCOPE_CORRECTION','NO_CHANGE','EXECUTION_SCHEMA_NOT_SEMANTIC','OTHER'}
REVIEW_FIELDS = ['proposition','explanation','increment_a_from_b','increment_b_from_a','interpretation_boundary',
                 'claim_level','endpoint_roles','role_basis','anchored_correspondences']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def put(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2,sort_keys=True);handle.write('\n')


def write_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as handle:
        handle.write(text)


def raw_value(g1,job):
    """Read complete historical objects, never repair malformed semantic JSON."""
    result=read(g1/'responses'/(job+'.json'))
    for folder in ['response-reconciliations','response-reconciliations-v2']:
        path=g1/folder/(job+'.json')
        if path.exists():
            projected=read(path).get('effective_result',{})
            if projected.get('complete') and isinstance(projected.get('value'),dict):
                result=projected
    return result.get('value') if result.get('complete') and isinstance(result.get('value'),dict) else None


def verify_span(snippet,record):
    if record['record_id']!=snippet['record_id']:
        raise ValueError('record identity mismatch')
    section=next(s for s in record['sections'] if s['ordinal']==snippet['section'])
    unit=next(u for u in section['units'] if u['ordinal']==snippet['unit'])
    text=unit.get('value',{}).get('normalized_text','')
    if not (0 <= snippet['start'] < snippet['end'] <= len(text)) or text[snippet['start']:snippet['end']]!=snippet['text']:
        raise ValueError('source range differs')
    if hashlib.sha256(snippet['text'].encode()).hexdigest()!=snippet['text_sha256']:
        raise ValueError('snippet text hash differs')


def prepare(g1,out,source_root):
    out.mkdir(parents=True,exist_ok=False)
    candidates=read(g1/'candidates.json')['pairs']
    if len(candidates)!=48 or len({p['pair_id'] for p in candidates})!=48:
        raise ValueError('G1 48-pair denominator differs')
    docs=read(g1/'candidate-documents.json');snips=read(g1/'candidate-snippets.json')
    latest={x['link_id']:x for x in read(g1/'links-qualified.json')['links']}
    initial={x['link_id']:x for x in read(g1/'links-generated.json')['links']}
    if set(latest)!={x['pair_id'] for x in candidates} or set(initial)!=set(latest):
        raise ValueError('initial/effective pair set differs')
    files={str(p.relative_to(g1)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(g1.rglob('*')) if p.is_file()}
    cases=[]
    for number,p in enumerate(sorted(candidates,key=lambda p:p['pair_id']),1):
        pid=p['pair_id'];delivery=read(g1/'deliveries'/('link-'+pid+'.json'))
        link=latest[pid];job=link.get('reuse_origin',{}).get('job','link-'+pid)
        value=raw_value(g1,job)
        if not value:
            value=raw_value(g1,'link-'+pid)
        if value:
            interpretation={
                'proposition':value.get('proposed_claim',''), 'explanation':value.get('explanation',''),
                'increment_a_from_b':value.get('increment_a_from_b',''),
                'increment_b_from_a':value.get('increment_b_from_a',''),
                'interpretation_boundary':value.get('counterevidence',''),
                **{k:value.get(k) for k in ['claim_level','endpoint_roles','role_basis','anchored_correspondences']}}
            selected={side:[delivery['aliases'][a] for a in value.get(key,[]) if a in delivery['aliases'] and a.startswith(side)]
                      for side,key in [('A','a_ids'),('B','b_ids')]}
        else:
            interpretation={k:link.get(k) for k in REVIEW_FIELDS}
            selected=link.get('evidence_ids',{})
        if not interpretation.get('proposition'):
            raise ValueError('no recoverable candidate proposition '+pid)
        bodies={side:{'source_id':cid,'title':docs[cid]['title'],'source_category_screen':docs[cid]['source_category'],
            'canonical_path':docs[cid]['canonical_path'],'canonical_sha256':docs[cid]['canonical_sha256'],
            'record_id':docs[cid]['record_id'],'coverage':delivery['payload'][side]['coverage'],
            'delivered_ids':[sid for alias,sid in delivery['aliases'].items() if alias.startswith(side)],
            'selected_evidence_ids':selected.get(side,[])} for side,cid in delivery['sides'].items()}
        packet={'case_id':f'S{number:02d}','pair_id':pid,'cohort':'PRIMARY_48','sources':p['sources'],
            'sides':bodies,'candidate':interpretation,'candidate_origin':{'job':job,'delivery':'deliveries/link-'+pid+'.json'},
            'blinding':'OLD_SCORE_FIELDS_WITHHELD_NOT_STRICT_INDEPENDENT_BLIND',
            'historical_opinion_in_candidate':bool(re.search('等级|useful|tentative|判为|维持[ABCDU]',str(interpretation)))}
        cases.append(packet)
    supplement=[]
    legacy=read(g1/'links-reused.json')['links']
    for i,cids in enumerate([['1638','505763'],['1288','1495']],1):
        link=next(x for x in legacy if {d['cid'] for d in x['endpoints'].values()}==set(cids))
        sides={side:{'source_id':cid,'title':docs[cid]['title'],'source_category_screen':docs[cid]['source_category'],
            'canonical_path':docs[cid]['canonical_path'],'canonical_sha256':docs[cid]['canonical_sha256'],
            'record_id':docs[cid]['record_id'], 'coverage':{'scope':'G1 retained verified source snippets; S1 supplement, not a new G1 pair'},
            'delivered_ids':docs[cid]['snippet_ids'],'selected_evidence_ids':link['evidence_ids'][side]}
            for side,cid in [(s,d['cid']) for s,d in link['endpoints'].items()]}
        supplement.append({'case_id':f'X{i:02d}','pair_id':link['link_id'],'cohort':'SUPPLEMENT_NOT_IN_48','sources':cids,
            'sides':sides,'candidate':{k:link.get(k) for k in REVIEW_FIELDS},
            'candidate_origin':{'artifact':'links-reused.json','reason':'User-required countercheck outside the frozen 48'},
            'blinding':'OLD_SCORE_FIELDS_WITHHELD_NOT_STRICT_INDEPENDENT_BLIND'})
    used={sid for c in cases+supplement for side in c['sides'].values() for sid in side['delivered_ids']}
    needed={sid:snips[sid] for sid in used}
    records={};record_hashes={}
    for c in cases+supplement:
        for side in c['sides'].values():
            cid=side['source_id']
            if cid not in records:
                path=Path(side['canonical_path'])
                if sha(path)!=side['canonical_sha256']:
                    raise ValueError('Canonical hash differs '+cid)
                records[cid]=read(path);record_hashes[cid]=side['canonical_sha256']
    for s in needed.values():
        verify_span(s,records[s['source']])
    # Reconstruct complete Canonical occurrences from the accepted RU artifact.
    ru_manifest=source_root/'data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json'
    rm=read(ru_manifest);rp=ru_manifest.parent.parent/rm['artifacts']['retrieval_units']['path']
    if sha(rp)!=rm['artifacts']['retrieval_units']['sha256']:
        raise ValueError('RU artifact hash differs')
    occurrence_keys={(s['source'],s['section'],s['unit']) for s in needed.values()};rows=defaultdict(list)
    with gzip.open(rp,'rt',encoding='utf-8') as handle:
        for line in handle:
            row=json.loads(line);a=row['source']['canonical_address']
            key=(a['source_identity_key'].rsplit(':',1)[-1],a['section_ordinal'],a['canonical_unit_ordinal'])
            if key in occurrence_keys:
                rows[key].append(row)
    spans={}
    for key,rr in rows.items():
        rr.sort(key=lambda r:r['fragment_selector'].get('fragment_index',0));cid,so,uo=key
        section=next(s for s in records[cid]['sections'] if s['ordinal']==so)
        unit=next(u for u in section['units'] if u['ordinal']==uo)
        if ''.join(r['retrieval_visible_text'] for r in rr)!=unit['value']['normalized_text']:
            raise ValueError('RU occurrence differs '+str(key))
        offset=0;spans[key]=[]
        for row in rr:
            end=offset+len(row['retrieval_visible_text']);spans[key].append((offset,end,row['unit_id']));offset=end
    for s in needed.values():
        expected=[uid for lo,hi,uid in spans.get((s['source'],s['section'],s['unit']),[]) if lo<s['end'] and hi>s['start']]
        if expected!=s['official_unit_ids']:
            raise ValueError('RU snippet range differs '+s['id'])
    put(out/'protocol.json',{'version':VERSION,'g1_root':str(g1.resolve()),'source_root':str(source_root.resolve()),
        'primary_pair_ids':sorted(latest),'primary_denominator':48,'supplementary_denominator':2,
        'review_authority':AUTHORITY,'human_acceptance':'NOT_REVIEWED','release_authorized':False,
        'project_model_calls':0,'embedding_calls':0,'strict_independent_blinding':False,
        'blinding_limits':['Same chat contains historical G1 results and user-supplied case expectations.',
                           'Candidate wording may reveal prior opinions. Score fields are withheld until pass1 is frozen.'],
        'parallel_task_boundary':'Work only in the calling worktree; no installation/environment/process/other-worktree mutations.'})
    put(out/'input-manifest.json',{'g1_root':str(g1.resolve()),'files':files})
    put(out/'pass1-packets.json',{'cases':cases,'supplementary_cases':supplement})
    put(out/'evidence.json',needed)
    put(out/'mechanical-source-checks.json',{'status':'PASS','canonical_records':len(records),'snippets':len(needed),
        'occurrences':len(spans),'canonical_hashes':record_hashes,'ru_artifact_sha256':sha(rp),
        'snapshot_authority':'Verified Canonical/RU only; source-role and proposition meaning remain semantic judgments.'})
    write_text(out/'implementation-snapshot.py',Path(__file__).read_text(encoding='utf-8'))
    print(json.dumps({'primary':48,'supplementary':2,'sources':len(records),'snippets_verified':len(needed),'history_files_pinned':len(files)}))


def show(out,start,end,limit):
    packets=read(out/'pass1-packets.json');snips=read(out/'evidence.json')
    for case in (packets['cases']+packets['supplementary_cases'])[start-1:end]:
        print(json.dumps({k:case[k] for k in ['case_id','pair_id','candidate']},ensure_ascii=False))
        for side,d in case['sides'].items():
            print(side,d['source_id'],d['title'],json.dumps(d['coverage'],ensure_ascii=False))
            selected=d['selected_evidence_ids'] or d['delivered_ids'][:2]
            for sid in selected:
                s=snips[sid];text=s['text']
                print(sid,s['section_name'],text[:limit] if limit else text)


def validate_reviews(packets,reviews,evidence):
    expected={c['pair_id']:c for c in packets['cases']}
    rows=reviews['cases']
    if len(rows)!=48 or {r['pair_id'] for r in rows}!=set(expected):
        raise ValueError('Review denominator is not exactly the original 48')
    if len({r['pair_id'] for r in rows})!=len(rows):
        raise ValueError('Duplicate pair reviews')
    extra=reviews.get('supplementary_cases',[])
    expected_extra={c['pair_id'] for c in packets['supplementary_cases']}
    if len(extra)!=len(expected_extra) or {r['pair_id'] for r in extra}!=expected_extra:
        raise ValueError('Supplementary denominator differs')
    for r in rows+extra:
        c=expected.get(r['pair_id']) or next(c for c in packets['supplementary_cases'] if c['pair_id']==r['pair_id'])
        if r['case_id']!=c['case_id'] or r['sources']!=[c['sides'][s]['title'] for s in ['A','B']]:
            raise ValueError('Case/source orientation mismatch')
        if r['review_authority']!=AUTHORITY or r['human_acceptance']!='NOT_REVIEWED' or r['navigation_status']!='INACTIVE':
            raise ValueError('Acceptance authority violation')
        if r['grade_v02'] not in GRADES or r['release_recommendation'] not in RELEASE:
            raise ValueError('Invalid grade/release enum')
        if r['release_recommendation']=='ELIGIBLE_PENDING_RELEASE' and r['grade_v02'] not in {'A','B'}:
            raise ValueError('Pair release recommendation requires A/B')
        for field in ['proposition_decision','increment','source_limitations','reading_scope']:
            if not isinstance(r.get(field),str) or not r[field].strip():
                raise ValueError('Missing review account: '+field)
        if not r['reason_codes'] or not set(r['reason_codes'])<=REASONS:
            raise ValueError('Invalid reason codes')
        if not r.get('subpropositions'):
            raise ValueError('No proposition-level decision')
        if r['grade_v02'] in {'A','B'} and not r.get('increment'):
            raise ValueError('Missing concrete information increment')
        allowed={sid for d in c['sides'].values() for sid in d['delivered_ids']}
        for sp in r['subpropositions']:
            if sp.get('role') not in PROPOSITION_ROLES or not sp.get('reason'):
                raise ValueError('Missing proposition role/reason')
            if sp['support'] not in SUPPORT or sp['grade'] not in GRADES or sp['release'] not in RELEASE:
                raise ValueError('Subproposition enum')
            if not isinstance(sp.get('text'),str) or not sp['text'].strip():
                raise ValueError('Empty subproposition')
            for side in ['A','B']:
                ids=sp.get('evidence_ids',{}).get(side,[])
                if not ids or not set(ids)<=allowed:
                    raise ValueError('Evidence missing or outside delivered scope')
                if any(evidence[sid]['source']!=c['sides'][side]['source_id'] for sid in ids):
                    raise ValueError('Evidence endpoint mismatch')
            if sp['grade']=='D' and not sp.get('critical_error'):
                raise ValueError('D requires an explicit critical-error account')
            if sp['release']=='ELIGIBLE_PENDING_RELEASE' and sp['grade'] not in {'A','B'}:
                raise ValueError('Ineligible grade cannot be proposed for narrative release')
            if sp['grade']=='B' and sp.get('navigation_label')!='解释性联读 / 非确定史实':
                raise ValueError('Missing interpretive navigation label')
        if r['grade_v02'] in {'D','U'} and not any(
            sp['grade']==r['grade_v02'] and sp['role'] in {'ORIGINAL_CORE','ORIGINAL_COMPONENT'}
            for sp in r['subpropositions']):
            raise ValueError('Pair D/U cannot derive only from an added guard')
    return {'primary_count':48,'subpropositions':sum(len(r['subpropositions']) for r in rows),'status':'PASS'}


def freeze(out,draft):
    reviews=read(draft);packets=read(out/'pass1-packets.json');evidence=read(out/'evidence.json')
    result=validate_reviews(packets,reviews,evidence)
    put(out/'pass1-reviews-frozen.json',reviews)
    put(out/'pass1-freeze.json',result|{'reviews_sha256':sha(out/'pass1-reviews-frozen.json'),
        'score_unmasking_permitted_after_this_file':True,'strict_independent_blind_review':False})


def compare(out):
    freeze_info=read(out/'pass1-freeze.json')
    if sha(out/'pass1-reviews-frozen.json')!=freeze_info['reviews_sha256']:
        raise ValueError('Frozen first pass changed')
    protocol=read(out/'protocol.json');g1=Path(protocol['g1_root']);reviews=read(out/'pass1-reviews-frozen.json')
    packets={c['pair_id']:c for c in read(out/'pass1-packets.json')['cases']}
    initial={l['link_id']:l for l in read(g1/'links-generated.json')['links']}
    qualified={l['link_id']:l for l in read(g1/'links-qualified.json')['links']}
    final={l['link_id']:l for l in read(g1/'navigation-knowledge.json')['links']}
    targeted={r['link_id']:r for r in read(g1/'targeted-reviews.json')['cases']}
    rows=[]
    for r in reviews['cases']:
        pid=r['pair_id'];old=initial[pid];q=qualified[pid];f=final[pid];t=targeted.get(pid)
        packet=packets[pid]
        row=r|{'original_candidate':packet['candidate'],'source_endpoints':packet['sides'],
            'candidate_origin':packet['candidate_origin'],
            'historical':{'initial_model_grade':old['model_grade'],'initial_program_grade':old['grade_recommendation'],
            'post_schema_model_grade':q['model_grade'],'post_schema_program_grade':q['grade_recommendation'],
            'final_conservative_grade':f['grade_recommendation'],'targeted_model_grade':t.get('review',{}).get('model_grade') if t else None,
            'targeted_program_grade':t.get('review',{}).get('grade_recommendation') if t else None,
            'targeted_failure':t.get('failure') if t else None,'targeted_cohort':t is not None},
            'grade_changed_from_final':r['grade_v02']!=f['grade_recommendation']}
        rows.append(row)
    transitions=Counter((r['historical']['final_conservative_grade'],r['grade_v02']) for r in rows)
    summary={'primary_denominator':48,'supplementary_denominator':len(reviews.get('supplementary_cases',[])),
        'grades_v02':dict(Counter(r['grade_v02'] for r in rows)),
        'release_recommendations':dict(Counter(r['release_recommendation'] for r in rows)),
        'grade_transitions':[{'from':a,'to':b,'count':n} for (a,b),n in sorted(transitions.items())],
        'changed_pairs':sum(r['grade_changed_from_final'] for r in rows),
        'reason_code_incidence':dict(Counter(c for r in rows for c in r['reason_codes'])),
        'subproposition_count':sum(len(r['subpropositions']) for r in rows),
        'split_pairs':sum(len(r['subpropositions'])>1 for r in rows),
        'review_authority':AUTHORITY,'human_acceptances':0,'released_links':0,'project_model_calls':0,'embedding_calls':0,
        'claims_of_model_accuracy':False,'note':'Paired rubric replay on all 48 G1 cases, not population precision or human gold.'}
    put(out/'reviews-v02.json',{'cases':rows,'supplementary_cases':reviews.get('supplementary_cases',[]),'summary':summary})
    put(out/'comparison-summary.json',summary)
    fields=['case_id','pair_id','sources','original_candidate','source_endpoints','historical','old_model_grade','old_final_grade','targeted_grade','grade_v02','release_recommendation',
            'proposition_decision','increment','source_limitations','reading_scope','reason_codes','subpropositions','review_authority','human_acceptance','navigation_status']
    with (out/'reviews-v02.csv').open('x',encoding='utf-8-sig',newline='') as handle:
        w=csv.DictWriter(handle,fieldnames=fields);w.writeheader()
        for r in rows:
            value={k:r.get(k) for k in fields};h=r['historical']
            value.update(old_model_grade=h['post_schema_model_grade'],old_final_grade=h['final_conservative_grade'],targeted_grade=h['targeted_program_grade'])
            for key in ['sources','original_candidate','source_endpoints','historical','reason_codes','subpropositions']:
                value[key]=json.dumps(value[key],ensure_ascii=False)
            w.writerow(value)
    lines=['# G1-S1：48条逐条离线回放','','AGENT_REVIEW_NOT_HUMAN_GOLD；人类认可0、发布0。两例补充案例不计入48分母。',
           '','|案例|来源对|G1最终|v0.2|发布建议|原因|','|---|---|---|---|---|---|']
    for r in rows:
        lines.append('|'+ '|'.join([r['case_id'],' ↔ '.join(r['sources']),r['historical']['final_conservative_grade'],r['grade_v02'],
            r['release_recommendation'],', '.join(r['reason_codes'])])+'|')
    for r in rows+reviews.get('supplementary_cases',[]):
        lines+=['',f'## {r["case_id"]} {r["pair_id"]}：'+ ' ↔ '.join(r['sources']),'',
            r['proposition_decision'],'','**信息增量**：'+r['increment'],'','**来源边界**：'+r['source_limitations'],
            '','**建议**：'+r['grade_v02']+' / '+r['release_recommendation'],'']
        if 'historical' in r:
            lines+=['**旧记录**：'+json.dumps(r['historical'],ensure_ascii=False),'',
                    '**原命题**：'+r['original_candidate']['proposition'],'']
        for sp in r['subpropositions']:
            lines+=['- '+sp['text']+'；'+sp['support']+'；'+sp['grade']+' / '+sp['release']+'。',
                    '  证据：'+ '; '.join(side+': '+', '.join(ids) for side,ids in sp['evidence_ids'].items())+'。',
                    '  理由：'+sp['reason']]
    write_text(out/'reviews-v02.md','\n'.join(lines)+'\n')
    print(json.dumps(summary,ensure_ascii=False))


def integrity(out):
    manifest=read(out/'input-manifest.json');g1=Path(manifest['g1_root']);changed=[]
    current={str(p.relative_to(g1)) for p in g1.rglob('*') if p.is_file()}
    if current!=set(manifest['files']):
        changed.append('G1 file set differs')
    for name,desc in manifest['files'].items():
        if sha(g1/name)!=desc['sha256']:
            changed.append(name)
    put(out/'final-input-integrity.json',{'status':'PASS' if not changed else 'FAIL',
        'g1_files_verified':len(manifest['files']),'changed':changed,'project_model_calls':0,'embedding_calls':0})
    if changed:
        raise ValueError('Historical inputs changed')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['prepare','show','freeze','compare','integrity'])
    p.add_argument('--out',type=Path,required=True);p.add_argument('--g1',type=Path);p.add_argument('--source-root',type=Path)
    p.add_argument('--draft',type=Path);p.add_argument('--start',type=int,default=1);p.add_argument('--end',type=int,default=48)
    p.add_argument('--limit',type=int,default=0)
    args=p.parse_args()
    if args.stage=='prepare':
        if args.g1 is None or args.source_root is None:p.error('prepare requires --g1 and --source-root')
        prepare(args.g1,args.out,args.source_root)
    elif args.stage=='show':show(args.out,args.start,args.end,args.limit)
    elif args.stage=='freeze':freeze(args.out,args.draft)
    elif args.stage=='compare':compare(args.out)
    elif args.stage=='integrity':integrity(args.out)


if __name__=='__main__':
    main()
