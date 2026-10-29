"""Process adapter reusing the installed ML runtime and exact encoder implementation."""
import base64
import json
import os
from pathlib import Path
import subprocess

import numpy as np

from ..local_qwen3_embedding import EmbeddingBatch
from .common import require


class _Tokenizer:
    def __init__(self,parent):self.parent=parent
    def __call__(self,text,**kwargs):
        return {'input_ids':range(self.parent._call({'op':'count','text':text}))}


class LocalEncoderProcess:
    def __init__(self,python,model_path,device,*,stderr_path):
        env=os.environ.copy()
        env.update(PYTHONPATH=str(Path(__file__).resolve().parents[3]),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONIOENCODING='utf-8')
        self.log=Path(stderr_path).open('a',encoding='utf8')
        self.process=subprocess.Popen([str(python),'-u','-m','genshin_corpus.retrieval.navi_offline.embedding_worker'],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf8',env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        result=self._call({'op':'init','model_path':str(model_path),'device':device,'dtype':'float16' if device=='cuda' else 'float32'})
        self.space_identity,self.contract=result['space_identity'],result['contract']
        self._tokenizer=_Tokenizer(self)
        self.metrics={'loads':[],'encodings':[],'totals':{'query':{'texts':0,'seconds_excluding_load':0.0},
                                                       'document':{'texts':0,'seconds_excluding_load':0.0}},'peak_memory':{}}

    def _call(self,value):
        require(self.process.poll() is None,'Local embedding worker exited; inspect stderr log')
        self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.process.stdin.flush()
        response=self.process.stdout.readline()
        require(bool(response),'Embedding worker terminated')
        result=json.loads(response)
        require(result['ok'],'Embedding worker: '+result.get('error_type','')+' '+result.get('error',''))
        return result['value']

    def load(self):
        if not self.metrics['loads']:
            self.metrics['loads'].append(self._call({'op':'load'}))

    def encode_documents(self,texts,batch_size=2):
        return self._encode(texts,'document',batch_size)

    def encode_query(self,text):
        return self._encode([text],'query',1)

    def _encode(self,texts,role,batch_size):
        self.load()
        result=self._call({'op':'encode','texts':texts,'role':role,'batch_size':batch_size})
        if len(self.metrics['encodings'])<8:
            self.metrics['encodings'].append({'metadata':result['encoding_metadata'],'memory':result['memory']})
        for key in ('texts','seconds_excluding_load'):
            self.metrics['totals'][role][key]+=result['encoding_metadata'][key]
        for key,value in result['memory'].items():
            if value is not None:self.metrics['peak_memory'][key]=max(self.metrics['peak_memory'].get(key,0),value)
        return EmbeddingBatch(np.frombuffer(base64.b64decode(result['vectors']),dtype=np.float32).reshape(result['count'],1024),
                              result['space_identity'],result['role'],tuple(result['text_sha256']),tuple(result['token_audit']))

    def close(self):
        if self.process.poll() is None:
            try:self.metrics['final_memory']=self._call({'op':'close'})
            except (ValueError,OSError):self.metrics['final_memory']='UNKNOWN_WORKER_ERROR'
            finally:
                self.process.stdin.close();self.process.stdout.close()
                try:self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.log.close()
