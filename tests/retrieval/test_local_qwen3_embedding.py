"""Provider-free guards; these tests do not claim a real model was loaded."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

import numpy as np

from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval.local_qwen3_embedding import (
    DIMENSION, EmbeddingBatch, LocalQwen3Config, LocalQwen3Error, _write_index,
    local_candidates, project_text,
)


class LocalQwen3Tests(unittest.TestCase):
    def setUp(self):
        base=Path('.local/embedding-tests/unit-temp');base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.root=Path(self.temp.name)
        self.source=self.root/'ru-manifest.json'
        self.source.write_text('{"build_identity":"fixture"}',encoding='utf-8')
        # Actual snapshot-sized vectors are unnecessary for these safety guards.
        self.units=[{'unit_id':'fixture-a','retrieval_visible_text':'钟离与若陀龙王','source':{'canonical_address':{'record_id':'fixture-r'},'record_context':{'record_title':'关系'}}},
                    {'unit_id':'fixture-b','retrieval_visible_text':'渊下宫的历史','source':{'canonical_address':{'record_id':'fixture-s'},'record_context':{'record_title':'历史'}}}]
        self.contract={'fixture':'local-contract'}
        self.space=sha256_json(self.contract)
        values=np.zeros((2,DIMENSION),dtype=np.float32);values[0,0]=1;values[1,1]=1
        self.documents=EmbeddingBatch(values,self.space,'document',tuple(sha256(u['retrieval_visible_text'].encode()).hexdigest() for u in self.units),({'truncated':False},{'truncated':False}))

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        return _write_index(self.root/'dense',{'build_identity':'formal-fixture'},self.units,self.documents,self.contract,self.source)

    def query(self):
        return EmbeddingBatch(self.documents.vectors[:1],self.space,'query',('a'*64,),())

    def test_query_instruction_does_not_contaminate_document(self):
        text='  钟离与若陀龙王？\n'
        self.assertEqual(project_text(text,'document'),text)
        self.assertTrue(project_text(text,'query').endswith('Query:'+text))
        for role in ('unknown',''):
            with self.assertRaises(LocalQwen3Error):project_text(text,role)

    def test_device_dtype_and_truncation_contract_fail_closed(self):
        a=LocalQwen3Config(self.root)
        b=LocalQwen3Config(self.root,'cpu','float32')
        snapshot={'files':[{'sha256':'fixture'}]}
        self.assertNotEqual(sha256_json(a.identity_projection(snapshot)),sha256_json(b.identity_projection(snapshot)))
        for kwargs in ({'device':'cpu'},{'dtype':'bfloat16'},{'max_length':8192}):
            with self.assertRaises(LocalQwen3Error):LocalQwen3Config(self.root,**kwargs)

    def test_dense_candidates_retain_formal_ids_and_source_locator(self):
        self.write()
        results=local_candidates(self.root/'dense/metadata/manifest.json',self.query())
        self.assertEqual(results[0]['unit_id'],'fixture-a')
        self.assertEqual(results[0]['source']['canonical_address']['record_id'],'fixture-r')
        self.assertEqual(results[0]['retrieval']['score'],1.0)

    def test_equal_dimension_foreign_model_is_rejected(self):
        self.write()
        for query in (replace(self.query(),space_identity='foreign-same-dimension'),self.documents,self.documents.vectors[0]):
            with self.assertRaises(LocalQwen3Error):local_candidates(self.root/'dense/metadata/manifest.json',query)

    def test_old_2048_dimensional_query_is_rejected(self):
        self.write()
        query=replace(self.query(),vectors=np.ones((1,2048),dtype=np.float32))
        with self.assertRaises(LocalQwen3Error):local_candidates(self.root/'dense/metadata/manifest.json',query)

    def test_document_order_or_text_change_is_rejected(self):
        changed=[dict(self.units[0],retrieval_visible_text='changed'),self.units[1]]
        with self.assertRaises(LocalQwen3Error):_write_index(self.root/'dense',{'build_identity':'fixture'},changed,self.documents,self.contract,self.source)
        self.assertFalse((self.root/'dense').exists())

    def test_no_overwrite_and_artifact_corruption_is_rejected(self):
        self.write()
        with self.assertRaises(LocalQwen3Error):self.write()
        vector_path=self.root/'dense/artifacts/vectors.f32.npy'
        data=bytearray(vector_path.read_bytes());data[-1]^=1;vector_path.write_bytes(data)
        with self.assertRaises(ValueError):local_candidates(self.root/'dense/metadata/manifest.json',self.query())


if __name__=='__main__':unittest.main()
