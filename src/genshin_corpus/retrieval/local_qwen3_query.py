"""Short-lived isolated query encoder. Its process exits before BGE allocates GPU.

Reuse the pinned encoder; no second ML installation or persistent GPU service.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Sequence
from hashlib import sha256

from genshin_corpus.canonical.fingerprints import sha256_json
from .local_qwen3_embedding import (
    DIMENSION, EmbeddingBatch, LocalQwen3Config, LocalQwen3Embedding, LocalQwen3Error,
)
from .local_qwen3_build import DEFAULT_MODEL, offline_only


def encode_local_query(question: str, *, expected_contract: dict, model_path=DEFAULT_MODEL,
                       python_path=Path('.local/embedding-runtime/Scripts/python.exe')) -> EmbeddingBatch:
    return encode_local_queries((question,), expected_contract=expected_contract,
                                model_path=model_path, python_path=python_path)


def encode_local_queries(questions: Sequence[str], *, expected_contract: dict, model_path=DEFAULT_MODEL,
                         python_path=Path('.local/embedding-runtime/Scripts/python.exe')) -> EmbeddingBatch:
    """Encode a bounded query set with the same per-question forward pass as serving."""
    import numpy as np
    if isinstance(questions, str) or not 1 <= len(questions) <= 70 or any(
        not isinstance(question, str) or not question.strip() for question in questions
    ):
        raise LocalQwen3Error("expected 1–70 nonempty queries")
    if not Path(python_path).is_file(): raise LocalQwen3Error("pinned embedding runtime is missing")
    env=dict(os.environ)
    env.update({"PYTHONPATH":str(Path(__file__).resolve().parents[2]),"PYTHONIOENCODING":"utf-8","HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","TOKENIZERS_PARALLELISM":"false"})
    payload={"questions":list(questions),"model_path":str(Path(model_path).resolve()),"expected_contract":expected_contract}
    flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    # communicate() completes only after process exit; BGE is not loaded here.
    response=subprocess.run([str(Path(python_path).resolve()),'-m','genshin_corpus.retrieval.local_qwen3_query'],
        input=json.dumps(payload,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',
        timeout=180 + len(questions),env=env,creationflags=flags)
    if response.returncode != 0:
        raise LocalQwen3Error(f"isolated local query failed: {response.stderr[-1500:]}")
    body=json.loads(response.stdout)
    values=np.frombuffer(base64.b64decode(body['vectors'],validate=True),dtype=np.float32).copy()
    if values.size != len(questions) * DIMENSION: raise LocalQwen3Error("invalid isolated query vectors")
    batch=EmbeddingBatch(values.reshape(len(questions),DIMENSION),body['space_identity'],'query',tuple(body['text_sha256']),tuple(body['token_audit']))
    if (batch.space_identity != sha256_json(expected_contract)
            or batch.text_sha256 != tuple(sha256(question.encode('utf-8')).hexdigest() for question in questions)
            or len(batch.token_audit) != len(questions) or any(r['truncated'] for r in batch.token_audit)):
        raise LocalQwen3Error("isolated query identity/truncation mismatch")
    return batch


def main():
    try:
        request=json.load(sys.stdin)
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        with offline_only():
            encoder=LocalQwen3Embedding(LocalQwen3Config(Path(request['model_path'])))
            if encoder.contract != request['expected_contract']: raise LocalQwen3Error("query/index encoding contract mismatch")
            encoder.load()
            from .local_qwen3_embedding import project_text, MAX_LENGTH
            questions = request['questions']
            if not isinstance(questions, list) or not 1 <= len(questions) <= 70:
                raise LocalQwen3Error("expected 1–70 queries")
            input_ids = encoder._tokenizer([project_text(q, 'query') for q in questions],
                                           truncation=False, add_special_tokens=True)['input_ids']
            if any(len(ids) > MAX_LENGTH for ids in input_ids):
                raise LocalQwen3Error("query exceeds 2048 tokens; no silent truncation")
            batch=encoder.encode_queries(questions, batch_size=1)
            if any(row['truncated'] for row in batch.token_audit): raise LocalQwen3Error("query exceeds 2048 tokens; no silent truncation")
            result={"vectors":base64.b64encode(batch.vectors.tobytes()).decode('ascii'),"space_identity":batch.space_identity,
                    "text_sha256":batch.text_sha256,"token_audit":batch.token_audit}
        print(json.dumps(result),flush=True)
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr,flush=True)
        return 1


if __name__=='__main__': raise SystemExit(main())
