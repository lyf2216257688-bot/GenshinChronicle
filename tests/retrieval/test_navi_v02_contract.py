"""Synthetic contract probes, never new corpus samples or human semantic gold."""
from copy import deepcopy
from hashlib import sha256
import unittest

from genshin_corpus.retrieval.navi_v02_contract import VERSION, validate_bindings


def proposition(pid, *, role='ORIGINAL_CORE', cid=None, action='PROPOSE', grade='A', release='ELIGIBLE_PENDING_RELEASE'):
    support={'A':'EXPLICIT','B':'GROUNDED_INTERPRETATION','C':'WEAK',
             'D':'INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION','U':'UNDETERMINED'}[grade]
    return {'id':pid,'candidate_id':cid,'review_action':action,'role':role,'text':'合成命题'+pid,
        'relation':'direct_reference','a_ids':['a1'],'b_ids':['b1'],'support':support,
        'increment_a_from_b':'可回查具体条件句','increment_b_from_a':'','boundary':'不证明条件假说为真',
        'grade':grade,'release':release,'navigation_label':'解释性联读 / 非确定史实' if grade=='B' else None,
        'critical_error':'把条件假说当确定事实' if grade=='D' else None,
        'evidence_gap':'缺少必要后半段' if grade=='U' else '', 'reason':'支持/边界及内容价值分别说明'}


def fixture(mode='review'):
    delivery={'A':['a1','a2'],'B':['b1','b2']}
    if mode=='review':
        original=proposition('o',cid='c1',action='REJECT',grade='D',release='EXCLUDED')
        candidates=[{'id':'c1','text':original['text'],'role':'ORIGINAL_CORE',
                     'text_sha256':sha256(original['text'].encode()).hexdigest(),'historical_grade':'B'}]
        props=[original,proposition('safe',role='SAFE_REPHRASING',cid='c1',action='REWRITE'),
               proposition('new',role='NEW_DISCOVERY',grade='B'),
               proposition('guard',role='COUNTERFACTUAL_GUARD',cid='c1',grade='D',release='EXCLUDED')]
    else:
        candidates=[];props=[proposition('g')]
    value={'schema_version':VERSION,'decision':'CANDIDATES',
        'reading':{s:{'read_ids':ids.copy(),'unread_ids':[],'limitations':'仅指定范围'} for s,ids in delivery.items()},
        'source_roles':{s:[{'role':'game_text','basis':'指定段落','evidence_ids':[ids[0]],'attribution':'叙述者'}] for s,ids in delivery.items()},
        'propositions':props,'no_link_reason':''}
    return value,delivery,candidates


class NaviV02ContractTests(unittest.TestCase):
    def test_mixed_pair_retains_error_and_independent_candidates(self):
        v,d,c=fixture();before=deepcopy((v,d,c))
        r=validate_bindings(v,d,c,mode='review')
        self.assertEqual(r['content_candidate_ids'],['safe','new'])
        self.assertEqual(r['original_assessment_count'],1)
        self.assertEqual(r['navigation_status'],'INACTIVE')
        self.assertEqual(r['semantic_quality'],'NOT_VALIDATED')
        self.assertEqual(before,(v,d,c))

    def test_zero_candidates_is_valid(self):
        v,d,c=fixture('generation');v.update(propositions=[],decision='NO_FORMAL_NAVI',no_link_reason='无可支持的具体联系')
        validate_bindings(v,d,c,mode='generation')
        v['no_link_reason']=' '
        with self.assertRaisesRegex(ValueError,'no-link'):validate_bindings(v,d,c,mode='generation')

    def test_wrong_original_cannot_be_washed_by_rewriting(self):
        for mutation in ['rewrite_in_place','omit','duplicate']:
            with self.subTest(mutation=mutation):
                v,d,c=fixture()
                if mutation=='rewrite_in_place':v['propositions'][0]['text']='安全改写冒充原句'
                elif mutation=='omit':v['propositions'].pop(0)
                else:v['propositions'].append(deepcopy(v['propositions'][0])|{'id':'o2'})
                with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='review')

    def test_discovery_and_guard_cannot_claim_generation_credit(self):
        for index,field,val in [(2,'candidate_id','c1'),(3,'release','ELIGIBLE_PENDING_RELEASE')]:
            v,d,c=fixture();v['propositions'][index][field]=val
            with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='review')

    def test_safe_rewrite_needs_real_parent(self):
        for cid in [None,'unknown']:
            v,d,c=fixture();v['propositions'][1]['candidate_id']=cid
            with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='review')

    def test_B_may_be_pending_or_excluded_without_semantic_downgrade(self):
        for release,decision in [('REVIEW_REQUIRED','UNRESOLVED'),('EXCLUDED','NO_FORMAL_NAVI'),('CATALOG_ONLY','NO_FORMAL_NAVI')]:
            v,d,c=fixture('generation');v['propositions']=[proposition('local',grade='B',release=release)];v['decision']=decision
            validate_bindings(v,d,c,mode='generation')

    def test_U_needs_gap_but_may_lack_one_evidence_endpoint(self):
        v,d,c=fixture('generation');p=proposition('u',grade='U',release='UNRESOLVED');p['b_ids']=[]
        v.update(propositions=[p],decision='UNRESOLVED');validate_bindings(v,d,c,mode='generation')
        p['evidence_gap']=' '
        with self.assertRaisesRegex(ValueError,'gap'):validate_bindings(v,d,c,mode='generation')

    def test_partial_page_read_does_not_force_U(self):
        v,d,c=fixture('generation')
        for side in ['A','B']:
            v['reading'][side]['unread_ids']=[v['reading'][side]['read_ids'].pop()]
        validate_bindings(v,d,c,mode='generation')

    def test_cross_endpoint_or_unread_evidence_rejected(self):
        for bad in ['b1','missing','a2']:
            v,d,c=fixture('generation');v['reading']['A'].update(read_ids=['a1'],unread_ids=['a2'])
            v['propositions'][0]['a_ids']=[bad]
            with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='generation')

    def test_false_reading_receipts_rejected(self):
        for kind in ['overlap','omission','extra']:
            v,d,c=fixture('generation')
            if kind=='overlap':v['reading']['A']['unread_ids']=['a1']
            elif kind=='omission':v['reading']['A']['read_ids'].pop()
            else:v['reading']['A']['read_ids'].append('invented')
            with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='generation')

    def test_original_registry_hash_is_verified(self):
        v,d,c=fixture();c[0]['text_sha256']='bad'
        with self.assertRaisesRegex(ValueError,'hash'):validate_bindings(v,d,c,mode='review')

    def test_guard_not_used_as_original_error_count(self):
        v,d,c=fixture();v['propositions'][3].update(text='另一警戒命题',id='guard2')
        self.assertEqual(validate_bindings(v,d,c,mode='review')['original_assessment_count'],1)

    def test_generation_cannot_pretend_to_review(self):
        v,d,c=fixture()
        with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='generation')

    def test_wrong_B_label_and_D_without_error_fail(self):
        for index,key in [(2,'navigation_label'),(0,'critical_error')]:
            v,d,c=fixture();v['propositions'][index][key]=None
            with self.assertRaises(ValueError):validate_bindings(v,d,c,mode='review')

    def test_pair_summary_does_not_replace_individual_outcomes(self):
        v,d,c=fixture();v['decision']='NO_FORMAL_NAVI'
        with self.assertRaisesRegex(ValueError,'decision'):validate_bindings(v,d,c,mode='review')

    def test_original_action_does_not_hide_a_D_judgment(self):
        v,d,c=fixture();v['propositions'][0]['review_action']='KEEP'
        with self.assertRaisesRegex(ValueError,'action/grade'):validate_bindings(v,d,c,mode='review')

    def test_output_cannot_set_human_release_or_program_fields(self):
        for key in ['human_acceptance','navigation_status','mechanical_status','endpoints','extracted_evidence']:
            v,d,c=fixture();v[key]='ACTIVE'
            with self.assertRaisesRegex(ValueError,'top fields'):validate_bindings(v,d,c,mode='review')


if __name__=='__main__':
    unittest.main()
