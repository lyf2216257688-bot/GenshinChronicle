"""Tiny generated fixtures exercise transactions, crash recovery and publication."""
from hashlib import sha256
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.local_qwen3_build import (
    BuildOptions, build_lock, run_build, validate_final, verified_source, read_json,
)
from genshin_corpus.retrieval.local_qwen3_embedding import DIMENSION, EmbeddingBatch, LocalQwen3Error
from genshin_corpus.retrieval.candidate_retrieval import _load_dense, hybrid_candidates
from genshin_corpus.retrieval.retrieval_units import RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION, RETRIEVAL_UNIT_SCHEMA_VERSION


class FakeEncoder:
    contract={"fixture":"same-model-space"}
    space_identity=sha256_json(contract)
    def __init__(self, fail_after=None):
        self.count=0; self.fail_after=fail_after; self._tokenizer=self.tokenize
    def load(self): pass
    def tokenize(self,texts,**kwargs): return {"input_ids":[list(range(len(t)+1)) for t in texts]}
    def encode_documents(self,texts,batch_size):
        if self.fail_after is not None and self.count>=self.fail_after: raise KeyboardInterrupt()
        self.count+=len(texts)
        vectors=np.zeros((len(texts),DIMENSION),dtype=np.float32)
        for i,t in enumerate(texts): vectors[i,int(sha256(t.encode()).hexdigest()[:6],16)%DIMENSION]=1
        return EmbeddingBatch(vectors,self.space_identity,"document",tuple(sha256(t.encode()).hexdigest() for t in texts),tuple({"truncated":False,"encoded_tokens":len(t)+1} for t in texts))


def fixture(root, texts=None):
    texts=texts or ["琴和芭芭拉", "鹤观的雷鸟", "璃月的契约", "古老的书卷", "神明与人民"]
    units=[{"schema_version":RETRIEVAL_UNIT_SCHEMA_VERSION,"unit_id":f"ru-{i}","retrieval_visible_text":t,
            "source":{"canonical_address":{"record_id":f"r-{i}"},"source_order":[i],"record_context":{"record_title":str(i)}}} for i,t in enumerate(texts)]
    blobs={"retrieval_units":("artifacts/ru.gz",gzip.compress(b"".join(canonical_json_bytes(u)+b"\n" for u in units),mtime=0),len(units)),
           "skip_ledger":("artifacts/skips.gz",gzip.compress(b"",mtime=0),0),
           "failure_ledger":("metadata/failure.json",b'{"status":"clear","failure_count":0}',None)}
    artifacts={}
    for name,(relative,body,n) in blobs.items():
        path=root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(body)
        artifacts[name]={"path":relative,"sha256":sha256(body).hexdigest(),"byte_count":len(body)}
        if n is not None: artifacts[name]["row_count"]=n
    manifest={"schema_version":RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION,"status":"complete","build_identity":"fixture-ru",
              "accounting":{"retrieval_unit_count":len(units)},"artifacts":artifacts}
    path=root/"metadata/manifest.json"; path.write_bytes(canonical_json_bytes(manifest))
    return path


class BuildTests(unittest.TestCase):
    def setUp(self):
        base=Path('.local/embedding-tests/build-fixtures'); base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base); self.root=Path(self.temp.name)
        self.source=fixture(self.root/'source'); self.output=self.root/'new-index'
        self.options=BuildOptions(shard_rows=2,max_batch=2)
        self.patch=patch('genshin_corpus.retrieval.local_qwen3_build.resources',return_value={});self.patch.start()
    def tearDown(self): self.patch.stop(); self.temp.cleanup()
    def build(self,encoder=None,**kwargs):
        # Socket failures make any accidental transport call fatal.
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            return run_build(self.source,self.output,encoder or FakeEncoder(),self.options,**kwargs)
    def test_pause_resume_matches_uninterrupted_and_is_loadable(self):
        first=FakeEncoder(); r=self.build(first,stop_after_shards=1)
        self.assertEqual((r['status'],first.count),('paused',2))
        self.assertFalse((self.output/'metadata/manifest.json').exists())
        with self.assertRaises((ValueError,OSError)): _load_dense(self.output/'metadata/manifest.json')
        resumed=FakeEncoder(); r=self.build(resumed)
        self.assertEqual((r['coverage_rows'],resumed.count),(5,3))
        other=self.root/'uninterrupted'
        run_build(self.source,other,FakeEncoder(),self.options)
        m,v,rows=_load_dense(self.output/'metadata/manifest.json',use_mmap=True)
        m2,v2,_=_load_dense(other/'metadata/manifest.json',use_mmap=True)
        try:
            np.testing.assert_array_equal(v,v2)
            self.assertEqual(m['arm_build_identity'],m2['arm_build_identity'])
            self.assertEqual([row['source_occurrence_index'] for row in rows],list(range(5)))
        finally: v._mmap.close();v2._mmap.close()
        final_encoder=FakeEncoder(); self.build(final_encoder); self.assertEqual(final_encoder.count,0)
    def test_actual_keyboard_interrupt_commits_only_prior_shards(self):
        with self.assertRaises(KeyboardInterrupt): self.build(FakeEncoder(fail_after=2))
        self.assertTrue((self.output/'shards/000000/receipt.json').exists())
        self.assertFalse((self.output/'shards/000001/receipt.json').exists())
        r=self.build(); self.assertEqual(r['status'],'complete')
        self.assertEqual(read_json(self.output/'last-error.json')['status'],'interrupted')
    def test_partial_uncommitted_shard_is_recomputed_but_committed_corruption_stops(self):
        self.build(stop_after_shards=1)
        folder=self.output/'shards/000001';folder.mkdir();(folder/'vectors.npy').write_bytes(b'partial-crash')
        self.build()
        path=self.output/'artifacts/rows.jsonl.gz';path.write_bytes(b'corrupt')
        with self.assertRaises(LocalQwen3Error): self.build()
    def test_corrupt_committed_shard_and_changed_configuration_fail_without_encoding(self):
        self.build(stop_after_shards=1)
        encoder=FakeEncoder()
        with self.assertRaises(LocalQwen3Error):
            run_build(self.source,self.output,encoder,BuildOptions(shard_rows=3))
        path=self.output/'shards/000000/vectors.npy';path.write_bytes(b'bad')
        with self.assertRaises(LocalQwen3Error): self.build(encoder)
        self.assertEqual(encoder.count,0)
    def test_oversized_ru_fails_with_audit_and_no_truncation(self):
        self.source=fixture(self.root/'long-source',['文'*2048])
        encoder=FakeEncoder()
        with self.assertRaises(LocalQwen3Error): self.build(encoder)
        self.assertEqual(read_json(self.output/'length-audit.json')['over_limit'],1)
        self.assertEqual(encoder.count,0)
        self.assertFalse((self.output/'metadata/manifest.json').exists())
    def test_nan_or_wrong_lineage_prevents_publication(self):
        encoder=FakeEncoder(); original=encoder.encode_documents
        def bad(*args,**kwargs):
            batch=original(*args,**kwargs);batch.vectors[0,0]=np.nan;return batch
        encoder.encode_documents=bad
        with self.assertRaises(LocalQwen3Error): self.build(encoder)
        self.assertFalse((self.output/'metadata/manifest.json').exists())
    def test_cuda_oom_halves_only_uncommitted_microbatch_without_device_fallback(self):
        import torch
        encoder=FakeEncoder();original=encoder.encode_documents
        def constrained(texts,batch_size):
            if batch_size>1: raise torch.cuda.OutOfMemoryError('fixture GPU pressure')
            return original(texts,batch_size)
        encoder.encode_documents=constrained
        result=self.build(encoder)
        self.assertEqual(result['coverage_rows'],5)
        self.assertEqual(encoder.count,5)
        self.assertIn('cuda_oom',(self.output/'events.jsonl').read_text(encoding='utf-8'))
    def test_kernel_lock_blocks_second_process_and_releases(self):
        code='from pathlib import Path; from genshin_corpus.retrieval.local_qwen3_build import build_lock; import sys\nwith build_lock(Path(sys.argv[1])): print("acquired")'
        with build_lock(self.output):
            child=subprocess.run([sys.executable,'-c',code,str(self.output)],capture_output=True,text=True)
            self.assertNotEqual(child.returncode,0);self.assertIn('another process',child.stderr)
        child=subprocess.run([sys.executable,'-c',code,str(self.output)],capture_output=True,text=True)
        self.assertEqual(child.returncode,0,child.stderr)
    def test_explicit_subset_keeps_original_occurrences_and_hard_limit(self):
        options=BuildOptions(shard_rows=2,limit=2,selected_indices=(1,4))
        r=run_build(self.source,self.output,FakeEncoder(),options)
        _,v,rows=_load_dense(self.output/'metadata/manifest.json',use_mmap=True)
        self.assertEqual([row['source_occurrence_index'] for row in rows],[1,4]);v._mmap.close()
        self.assertEqual(r['coverage_rows'],2)
        with self.assertRaises(LocalQwen3Error): BuildOptions(limit=4097)
    def test_cli_full_gate_and_smoke_cap_fail_before_any_build(self):
        from genshin_corpus.retrieval.local_qwen3_build import main
        with patch('genshin_corpus.retrieval.local_qwen3_build.run_build',side_effect=AssertionError('must not build')), patch(
            'genshin_corpus.retrieval.local_qwen3_build.preflight',side_effect=AssertionError('must not dispatch')):
            for args in (['run'],['resume'],['smoke']):
                with self.assertRaises(SystemExit) as failure: main(args)
                self.assertEqual(failure.exception.code,2)


if __name__=='__main__': unittest.main()
