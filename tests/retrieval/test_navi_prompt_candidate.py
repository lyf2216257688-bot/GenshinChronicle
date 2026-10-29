"""Offline contracts and authored F2 regressions, never model-quality scores."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

from genshin_corpus.retrieval import navi_prompt_candidate as navi

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/fixtures/retrieval/navi_prompt_final_cases.json'


def cases():
    return json.loads(FIXTURE.read_text(encoding='utf8'))['cases']


def evaluate(c, value=None):
    return navi.validate_output(json.dumps(c['authored_output'] if value is None else value, ensure_ascii=False),
                                {s:[x['id'] for x in c['view'][s]['snippets']] for s in ['A','B']},
                                c['view'].get('candidates', []), mode=c['mode'])


class NaviPromptCandidateTests(unittest.TestCase):
    def test_seven_authored_regressions_on_six_f2_pairs(self):
        for c in cases():
            with self.subTest(case=c['case_id']):
                before=deepcopy(c); result=evaluate(c)
                self.assertEqual(c,before)
                self.assertEqual(result['decision'],c['expected']['decision'])
                self.assertEqual([p['release'] for p in result['propositions']],c['expected']['releases'])
                self.assertEqual(result['schema_version'],navi.VERSION)
                self.assertEqual(result['navigation_status'],'INACTIVE')
                self.assertEqual(result['human_acceptance'],'NOT_REVIEWED')
                self.assertEqual(result['semantic_quality'],'NOT_VALIDATED')

    def test_fixture_text_hash_and_endpoint_lineage(self):
        for c in cases():
            for side in ['A','B']:
                for q in c['view'][side]['snippets']:
                    evidence=c['evidence_lineage'][q['id']]
                    self.assertEqual(sha256(q['text'].encode()).hexdigest(),evidence['text_sha256'])
                    self.assertTrue(evidence['official_unit_ids'])
                    self.assertTrue(evidence['record_id'])
                    self.assertEqual(len(q['text']),evidence['end']-evidence['start'])

    def test_schemas_use_conservative_subset_and_separate_modes(self):
        for mode in ['generation','review']:
            schema=navi.model_schema(mode);Draft202012Validator.check_schema(schema)
            def check(node):
                if isinstance(node,dict):
                    self.assertFalse(set(node)&{'allOf','if','then','pattern','uniqueItems','minItems','minLength'})
                    if node.get('type')=='object':
                        self.assertIs(node['additionalProperties'],False)
                        self.assertEqual(set(node['required']),set(node['properties']))
                    for v in node.values():check(v)
                elif isinstance(node,list):
                    for v in node:check(v)
            check(schema)
            props=schema['properties']['propositions']['items']['properties']
            self.assertFalse(set(props)&navi.DERIVED)
            self.assertEqual('role' in props,mode=='review')
            self.assertEqual('candidate_id' in props,mode=='review')

    def test_program_fields_cannot_be_injected(self):
        for field in ['decision','schema_version','human_acceptance','navigation_status','mechanical_status','extracted_evidence']:
            c=cases()[0];c['authored_output'][field]='CANDIDATES'
            with self.subTest(field=field),self.assertRaises(ValidationError):evaluate(c)
        for field in ['id','review_action','release','navigation_label','role','candidate_id']:
            c=cases()[0];c['authored_output']['propositions'][0][field]='COUNTERFACTUAL_GUARD'
            with self.subTest(field=field),self.assertRaises(ValidationError):evaluate(c)

    def test_strict_json_never_repairs_or_strips(self):
        raw=json.dumps(cases()[0]['authored_output'],ensure_ascii=False)
        invalid=['```json\n'+raw+'\n```',raw+' trailing',raw[:-1],raw[:-1]+',"no_link_reason":""}',
                 raw.replace('"no_link_reason": ""','"no_link_reason": NaN'),
                 raw.replace('"no_link_reason": ""','"no_link_reason": Infinity'), '[]', 'null']
        c=cases()[0];delivery={s:[q['id'] for q in c['view'][s]['snippets']] for s in ['A','B']}
        for text in invalid:
            with self.subTest(text=text[:35]),self.assertRaises((ValueError,ValidationError)):
                navi.validate_output(text,delivery,mode='generation')

    def test_no_common_name_or_cross_reference_gate(self):
        # Pure contract probe; anonymized aliases are NOT an extra real-world case.
        c=cases()[0]
        result=navi.validate_output(json.dumps(c['authored_output'],ensure_ascii=False),
                                   {s:[q['id'] for q in c['view'][s]['snippets']] for s in ['A','B']},mode='generation')
        self.assertEqual(result['mechanical']['content_candidate_ids'],['p1'])
        self.assertEqual(result['propositions'][0]['relation'],'background')

    def test_literary_B_is_preserved_but_excluded_from_story(self):
        r=evaluate(cases()[1]);self.assertEqual(r['propositions'][0]['grade'],'B')
        self.assertEqual(r['dispositions'][0]['policy_reason'],'LITERARY_WITHOUT_SHARED_STORY')
        self.assertFalse(r['mechanical']['content_candidate_ids'])

    def test_grade_and_story_conflicts_do_not_get_silently_repaired(self):
        for key,bad in [('support','GROUNDED_INTERPRETATION'),('grade','D'),('grade','U'),
                        ('critical_error',None),('a_ids',[]),('increment_a_from_b',' '),('boundary',' ')]:
            c=cases()[0];p=c['authored_output']['propositions'][0]
            if key=='critical_error':p.update(grade='D',support='INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION')
            p[key]=bad
            with self.subTest(key=key,bad=bad),self.assertRaises((ValueError,ValidationError)):evaluate(c)

    def test_unknown_needs_gap_D_needs_error_and_evidence(self):
        c=cases()[2];c['authored_output']['propositions'][1]['evidence_gap']=' '
        with self.assertRaisesRegex(ValueError,'gap'):evaluate(c)
        for key,bad in [('critical_error',' '),('a_ids',[]),('support','EXPLICIT')]:
            c=cases()[2];p=c['authored_output']['propositions'][0];p[key]=bad
            if key=='a_ids':p['b_ids']=[]
            with self.subTest(key=key),self.assertRaises((ValueError,ValidationError)):evaluate(c)

    def test_bad_aliases_reading_and_source_roles_remain_fail_closed(self):
        for kind in ['other_side','unread','omitted_receipt','overlap','duplicate','source_role_unread']:
            c=cases()[0];v=c['authored_output'];p=v['propositions'][0]
            if kind=='other_side':p['a_ids']=['B004']
            elif kind=='unread':p['a_ids']=['A999']
            elif kind=='omitted_receipt':v['reading']['A']['read_ids'].pop()
            elif kind=='overlap':v['reading']['A']['unread_ids']=['A004']
            elif kind=='duplicate':p['a_ids'].append('A004')
            else:v['source_roles']['A'][0]['evidence_ids']=['B004']
            with self.subTest(kind=kind),self.assertRaises((ValueError,ValidationError)):evaluate(c)

    def test_missing_original_rewrite_in_place_duplicate_and_bad_hash_rejected(self):
        for kind in ['omission','rewrite','duplicate','hash','new_claims_old_id','safe_without_parent']:
            c=cases()[4];v=c['authored_output'];p=v['propositions'][0]
            if kind=='omission':v['propositions'].pop(0)
            elif kind=='rewrite':p['text']='用正确改写覆盖原句'
            elif kind=='duplicate':v['propositions'].append(deepcopy(p))
            elif kind=='hash':c['view']['candidates'][0]['text_sha256']='bad'
            elif kind=='new_claims_old_id':v['propositions'][1]['role']='NEW_DISCOVERY'
            else:v['propositions'][1]['candidate_id']=None
            with self.subTest(kind=kind),self.assertRaises((ValueError,ValidationError)):evaluate(c)

    def test_mixed_D_and_safe_A_keep_original_error(self):
        r=evaluate(cases()[-1]);self.assertEqual(r['mechanical']['original_assessment_count'],3)
        self.assertEqual([p['review_action'] for p in r['propositions']],['REJECT']*3+['REWRITE'])
        self.assertEqual(r['mechanical']['content_candidate_ids'],['p4'])

    def test_guard_never_gets_release_or_original_credit(self):
        c=cases()[2];v=c['authored_output'];p=deepcopy(v['propositions'][0])
        p.update(role='COUNTERFACTUAL_GUARD',candidate_id=None);p['story']['status']='CONCRETE_STORY'
        v['propositions'].append(p);r=evaluate(c)
        self.assertEqual(r['mechanical']['original_assessment_count'],1)
        self.assertEqual(r['propositions'][-1]['release'],'EXCLUDED')
        self.assertFalse(r['mechanical']['content_candidate_ids'])

    def test_disputed_story_and_catalog_remain_separate(self):
        c=cases()[0];p=c['authored_output']['propositions'][0]
        p['story']['status']='DISPUTED_STORY';self.assertEqual(evaluate(c)['decision'],'UNRESOLVED')
        p['story']['status']='NON_STORY';p['relation']='duplicate'
        self.assertEqual(evaluate(c)['propositions'][0]['release'],'CATALOG_ONLY')
        p['story']['status']='CONCRETE_STORY';p.update(grade='C',support='WEAK')
        self.assertEqual(evaluate(c)['propositions'][0]['release'],'REVIEW_REQUIRED')

    def test_zero_and_partial_receipts_do_not_force_U(self):
        c=cases()[0];v=c['authored_output'];v.update(propositions=[],no_link_reason='没有共同剧情证据')
        self.assertEqual(evaluate(c)['decision'],'NO_FORMAL_NAVI')
        v['no_link_reason']=' '
        with self.assertRaises((ValueError,ValidationError)):evaluate(c)
        c=cases()[0];receipt=c['authored_output']['reading']['A']
        receipt['read_ids'].remove('A002');receipt['unread_ids']=['A002']
        self.assertEqual(evaluate(c)['decision'],'CANDIDATES')

    def test_unverified_strict_draft_and_observed_json_mode_are_unsent(self):
        for c in cases():
            for fmt in ['json_object','json_schema']:
                draft=navi.prepare_request(c['view'],mode=c['mode'],model='deepseek-v4.1-flash',
                    route='jizhi',max_output_tokens=30000,format_mode=fmt)
                self.assertIs(draft['send_authorized'],False)
                self.assertIs(draft['automatic_retry'],False)
                self.assertEqual(draft['strict_provider_enforcement'],'UNKNOWN')
                text=draft['wire']['input'][0]['content'][0]['text']
                self.assertIn(navi.prompt(c['mode']),text)
                self.assertIn('完整输出JSON Schema',text)
                self.assertEqual(draft['wire']['text']['format']['type'],fmt)
                if fmt=='json_schema':self.assertIs(draft['wire']['text']['format']['strict'],True)

    def test_prepare_refuses_score_leakage_and_unbounded_configuration(self):
        c=cases()[0];kwargs=dict(mode=c['mode'],model='deepseek-v4.1-flash',route='jizhi',max_output_tokens=30000)
        for key,bad in [('max_output_tokens',True),('max_output_tokens',0),('format_mode','text'),('model','')]:
            with self.subTest(key=key),self.assertRaises(ValueError):navi.prepare_request(c['view'],**(kwargs|{key:bad}))
        c['view']['expected_grade']='A'
        with self.assertRaises(ValueError):navi.prepare_request(c['view'],**kwargs)

    def test_installed_sdk_forwards_both_formats_through_mock_only(self):
        import httpx
        import openai
        for mode in ['generation','review']:
            c=next(c for c in cases() if c['mode']==mode)
            for fmt in ['json_object','json_schema']:
                draft=navi.prepare_request(c['view'],mode=mode,model='deepseek-v4.1-flash',
                    route='jizhi',max_output_tokens=30000,format_mode=fmt)
                seen=[]
                def handler(request):
                    seen.append(json.loads(request.content))
                    return httpx.Response(200,headers={'content-type':'text/event-stream'},
                                          content=b'data: [DONE]\n\n')
                with httpx.Client(transport=httpx.MockTransport(handler),trust_env=False) as client:
                    with openai.OpenAI(api_key='offline-dummy',base_url='https://offline.invalid/v1',
                                       max_retries=0,http_client=client) as sdk:
                        with sdk.responses.create(**draft['wire']) as stream:
                            self.assertEqual(list(stream),[])
                self.assertEqual(seen,[draft['wire']])


if __name__=='__main__':
    unittest.main()
