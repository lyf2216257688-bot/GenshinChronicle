"""Static interface tests; optional pre-existing jsonschema, no corpus or API."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from tests.retrieval.test_navi_v02_contract import fixture, proposition
from genshin_corpus.retrieval.navi_v02_contract import ROLES, VERSION, validate_bindings


@unittest.skipIf(Draft202012Validator is None, 'Static schema gate PARTIAL: jsonschema unavailable')
class NaviV02SchemaTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[2]
        self.schema=json.loads((root/'docs/research/phase-05/p05-navi-b-v02.schema.json').read_text(encoding='utf-8'))
        self.validator=Draft202012Validator(self.schema)

    def test_schema_meta_and_enum_alignment(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual(self.schema['properties']['schema_version']['const'],VERSION)
        self.assertEqual(set(self.schema['properties']['propositions']['items']['properties']['role']['enum']),ROLES)

    def test_mixed_origin_pair_is_a_valid_interface(self):
        value,delivery,candidates=fixture();self.validator.validate(value)
        validate_bindings(value,delivery,candidates,mode='review')

    def test_zero_eligible_pending_and_zero_proposition_paths(self):
        for release,decision in [('REVIEW_REQUIRED','UNRESOLVED'),('EXCLUDED','NO_FORMAL_NAVI'),('CATALOG_ONLY','NO_FORMAL_NAVI')]:
            value,delivery,candidates=fixture('generation')
            value.update(propositions=[proposition('b',grade='B',release=release)],decision=decision)
            self.validator.validate(value);validate_bindings(value,delivery,candidates,mode='generation')
        value,_,_=fixture('generation');value.update(propositions=[],decision='NO_FORMAL_NAVI',no_link_reason='没有具体联系')
        self.validator.validate(value)
        value['no_link_reason']=' '
        self.assertTrue(list(self.validator.iter_errors(value)))

    def test_new_fields_and_original_action_are_required(self):
        for key in ['role','evidence_gap']:
            value,_,_=fixture();value['propositions'][0].pop(key)
            self.assertTrue(list(self.validator.iter_errors(value)),key)
        value,_,_=fixture();value['propositions'][0]['review_action']='KEEP'
        self.assertTrue(list(self.validator.iter_errors(value)))

    def test_misleading_support_or_eligibility_is_rejected(self):
        for index,key,bad in [(0,'support','EXPLICIT'),(0,'critical_error',' '),(2,'navigation_label',None),
                              (3,'release','ELIGIBLE_PENDING_RELEASE'),(1,'grade','C')]:
            with self.subTest(key=key):
                value,_,_=fixture();value['propositions'][index][key]=bad
                self.assertTrue(list(self.validator.iter_errors(value)))
        value,_,_=fixture('generation');p=proposition('u',grade='U',release='UNRESOLVED');p['evidence_gap']=' '
        value.update(propositions=[p],decision='UNRESOLVED')
        self.assertTrue(list(self.validator.iter_errors(value)))

    def test_schema_alone_cannot_prove_original_retention(self):
        value,delivery,candidates=fixture();value['propositions'].pop(0)
        self.validator.validate(value)
        with self.assertRaisesRegex(ValueError,'Every original'):
            validate_bindings(value,delivery,candidates,mode='review')

    def test_model_cannot_emit_acceptance_or_quotes(self):
        for key in ['human_acceptance','navigation_status','mechanical_status','extracted_evidence']:
            value,_,_=fixture();value[key]='ACTIVE'
            self.assertTrue(list(self.validator.iter_errors(value)))


if __name__=='__main__':
    unittest.main()
