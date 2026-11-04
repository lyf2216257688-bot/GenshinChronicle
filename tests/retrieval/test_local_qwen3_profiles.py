"""Profile separation and typed Hybrid binding without model/API dependencies."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
import json
from unittest.mock import patch
import numpy as np

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.rag.config import artifact_paths_from_environment, ONLINE_QWEN_PROFILE, LOCAL_QWEN3_PROFILE
from genshin_corpus.retrieval.candidate_retrieval import BatchCandidateRetriever, CandidateRetrievalError
from genshin_corpus.retrieval.local_qwen3_embedding import MODEL_ID, REVISION, WEIGHT_SHA256, EmbeddingBatch
from genshin_corpus.rag.backend import run_single_question, SingleQuestionBackendConfig


class ProfileTests(unittest.TestCase):
    def query(self,text='问题'):
        v=np.zeros((1,1024),dtype=np.float32);v[0,0]=1
        return EmbeddingBatch(v,sha256_json({'fixture':'space'}),'query',(sha256(text.encode()).hexdigest(),),({'truncated':False},))
    def retriever(self):
        manifest={'local_qwen3_contract':{'fixture':'space'},'vector_space_identity':self.query().space_identity,
                  'model_name':MODEL_ID,'model_revision':REVISION,'model_sha256':WEIGHT_SHA256,
                  'embedding_dimension':1024,'arm_build_identity':'dense'}
        return BatchCandidateRetriever({'arm_build_identity':'lex'},[],manifest,self.query().vectors,[{'unit_id':'u1'}])
    def test_config_defaults_local_and_keeps_explicit_online_rollback(self):
        default=artifact_paths_from_environment({})
        self.assertEqual(default.embedding_profile,LOCAL_QWEN3_PROFILE)
        self.assertIn('p04-local-qwen3-1024',str(default.dense_manifest_path))
        old=artifact_paths_from_environment({'GENSHIN_EMBEDDING_PROFILE':ONLINE_QWEN_PROFILE,
                                             'GENSHIN_LOCAL_DENSE_MANIFEST':'ignored/manifest.json'})
        self.assertEqual(old.embedding_profile,ONLINE_QWEN_PROFILE)
        self.assertIn('qwen-full-batch',str(old.dense_manifest_path))
        local=artifact_paths_from_environment({'GENSHIN_EMBEDDING_PROFILE':LOCAL_QWEN3_PROFILE,'GENSHIN_LOCAL_DENSE_MANIFEST':'new/metadata/manifest.json'})
        self.assertEqual(local.embedding_profile,LOCAL_QWEN3_PROFILE)
        self.assertNotEqual(local.resolved_key(),old.resolved_key())
        self.assertNotEqual(local.resolved_key(),default.resolved_key())
        with self.assertRaises(ValueError):
            artifact_paths_from_environment({'GENSHIN_EMBEDDING_PROFILE':'bad'})
    def test_existing_hybrid_retriever_accepts_bound_local_query_only(self):
        retriever=self.retriever()
        lexical=[{'unit_id':'u1','rank':1,'retrieval':{'score':1,'arm_build_identity':'lex'}}]
        with patch('genshin_corpus.retrieval.candidate_retrieval._lexical_candidates_from_loaded',return_value=lexical):
            rows=retriever.candidates_for_query('问题',self.query(),instruction=None,top_k=1)
            self.assertEqual(rows['hybrid'][0]['unit_id'],'u1')
            for query in (self.query().vectors[0],replace(self.query(),space_identity='foreign'),replace(self.query(),vectors=np.ones((1,2048),dtype=np.float32))):
                with self.assertRaises(CandidateRetrievalError): retriever.candidates_for_query('问题',query,instruction=None)
    def test_backend_local_evidence_path_never_invokes_online_encoder(self):
        retriever=self.retriever()
        state=SimpleNamespace(embedding_profile=LOCAL_QWEN3_PROFILE,local_encoding_contract={'fixture':'space'},
            retrieval_unit_build_identity='ru',lexical_build_identity='lex',dense_build_identity='dense',closed=False,
            retriever=retriever,assembly_context=object(),retrieval_unit_manifest_path=Path('fixture'))
        lexical=[{'unit_id':'u1','rank':1,'retrieval':{'score':1,'arm_build_identity':'lex'}}]
        config=SingleQuestionBackendConfig(candidate_supply_depth=1,rerank_depth=1,final_top_n=1,reranker_enabled=False)
        with patch('genshin_corpus.rag.backend.encode_qwen_query',side_effect=AssertionError('no remote embedding')), patch(
            'genshin_corpus.retrieval.local_qwen3_query.encode_local_query',return_value=self.query()) as encode, patch(
            'genshin_corpus.retrieval.candidate_retrieval._lexical_candidates_from_loaded',return_value=lexical), patch(
            'genshin_corpus.rag.backend.assemble_deferred_footprint_charge_packet',return_value={'evidence':[]}):
            result=run_single_question(state,'问题',config=config,execution_mode='evidence_only')
            self.assertEqual(result['status'],'succeeded',result['error']);encode.assert_called_once()
            self.assertEqual(result['audit']['embedding_profile'],LOCAL_QWEN3_PROFILE)
            bad=run_single_question(state,'问题',embedding_transport=object(),config=config,execution_mode='evidence_only')
            self.assertEqual(bad['status'],'failed')
            self.assertEqual(bad['error']['stage'],'input')
    def test_prepare_local_requires_full_completed_index_and_preserves_old_guard(self):
        from genshin_corpus.rag.backend import prepare_rag_state, RagBackendPreparationError
        base=Path('.local/embedding-tests/profile-fixtures');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root=Path(folder);(root/'metadata').mkdir()
            source=root/'source.json';source.write_text(json.dumps({'accounting':{'retrieval_unit_count':1}}))
            (root/'validation.json').write_text(json.dumps({'status':'PASS','coverage_rows':1}))
            retriever=self.retriever()
            retriever.lexical_manifest['retrieval_unit_build_identity']='ru'
            retriever.dense_manifest.update({'retrieval_unit_build_identity':'ru','row_count':1,'scope':'full-original-RU-candidate','source_ru_manifest_sha256':sha256(source.read_bytes()).hexdigest()})
            model=SimpleNamespace(contract=retriever.dense_manifest['local_qwen3_contract'],space_identity=self.query().space_identity)
            with patch('genshin_corpus.rag.backend.load_batch_candidate_retriever',return_value=retriever) as load, patch(
                'genshin_corpus.retrieval.local_qwen3_embedding.LocalQwen3Embedding',return_value=model), patch(
                'genshin_corpus.rag.backend.prepare_evidence_assembly_context',return_value=SimpleNamespace(retrieval_unit_build_identity='ru')):
                state=prepare_rag_state(retrieval_unit_manifest_path=source,lexical_manifest_path=root/'lex',dense_manifest_path=root/'metadata/manifest.json')
                self.assertEqual(state.embedding_profile,LOCAL_QWEN3_PROFILE)
                self.assertFalse(load.call_args.kwargs['accepted_qwen']);self.assertTrue(load.call_args.kwargs['use_mmap'])
                # Avoid closing a fixture ndarray; production owns an mmap.
                state.close()
                retriever.dense_manifest['scope']='bounded-smoke-not-production'
                with self.assertRaises(RagBackendPreparationError):
                    prepare_rag_state(retrieval_unit_manifest_path=source,lexical_manifest_path=root/'lex',dense_manifest_path=root/'metadata/manifest.json',embedding_profile=LOCAL_QWEN3_PROFILE)


if __name__=='__main__': unittest.main()
