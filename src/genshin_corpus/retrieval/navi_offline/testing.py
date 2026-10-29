"""Deterministic TEST-ONLY vectors; cannot share identities with local Qwen."""
from hashlib import sha256
import numpy as np
from ..local_qwen3_embedding import EmbeddingBatch
from .common import digest


class Tokenizer:
    def __call__(self,text,**kwargs):
        return {'input_ids':[ord(c)%100 for c in text]+[151643]}


class MockEncoder:
    def __init__(self):
        self.contract={'authority':'MOCK_NOT_QWEN','dimension':1024}
        self.space_identity=digest(self.contract)
        self._tokenizer=Tokenizer()

    def load(self):
        pass

    def encode_documents(self,texts,batch_size=2):
        return self.encode(texts,'document')

    def encode_query(self,text):
        return self.encode([text],'query')

    def encode(self,texts,role):
        vectors=[]
        for text in texts:
            v=np.zeros(1024,dtype=np.float32)
            for char in text:
                v[int.from_bytes(sha256(char.encode()).digest()[:2],'little')%1024]+=1
            if not np.any(v):v[0]=1
            vectors.append(v/np.linalg.norm(v))
        return EmbeddingBatch(np.array(vectors),self.space_identity,role,
                              tuple(sha256(t.encode()).hexdigest() for t in texts),
                              tuple({'truncated':False,'untruncated_tokens':len(t)+1,'encoded_tokens':len(t)+1} for t in texts))
