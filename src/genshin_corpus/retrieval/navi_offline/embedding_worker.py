"""Persistent local Qwen worker in its already-qualified isolated runtime.

Stdlib JSON-lines IPC avoids installing generation SDKs into the pinned ML runtime.
"""
import base64
import json
from pathlib import Path
import socket
import sys
import time

from ..local_qwen3_embedding import LocalQwen3Config, LocalQwen3Embedding


def memory():
    # Optional telemetry must never invalidate an already computed vector batch.
    peak=None
    try:
        import psutil
        peak=getattr(psutil.Process().memory_info(),'peak_wset',None)
    except (ImportError,OSError):
        pass
    import torch
    return {'peak_working_set_bytes':peak,
            'peak_cuda_allocated_bytes':torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None,
            'peak_cuda_reserved_bytes':torch.cuda.max_memory_reserved() if torch.cuda.is_available() else None}


def main():
    def deny(*args,**kwargs):
        raise RuntimeError('Embedding worker network forbidden')
    socket.socket.connect=deny
    encoder=None
    for line in sys.stdin:
        try:
            request=json.loads(line)
            op=request['op']
            if op=='init':
                encoder=LocalQwen3Embedding(LocalQwen3Config(Path(request['model_path']),request['device'],request['dtype']))
                result={'space_identity':encoder.space_identity,'contract':encoder.contract}
            elif op=='close':
                print(json.dumps({'ok':True,'value':memory() if encoder._model is not None else {}}),flush=True)
                break
            else:
                started=time.perf_counter();encoder.load()
                if op=='load':
                    result={'seconds':time.perf_counter()-started}
                elif op=='count':
                    result=len(encoder._tokenizer(request['text'],add_special_tokens=True)['input_ids'])
                elif op=='encode':
                    batch=encoder.encode(request['texts'],role=request['role'],batch_size=request['batch_size'])
                    result={'vectors':base64.b64encode(batch.vectors.tobytes()).decode(),'count':len(request['texts']),
                            'space_identity':batch.space_identity,'role':batch.role,'text_sha256':batch.text_sha256,
                            'token_audit':batch.token_audit,'encoding_metadata':encoder.last_encoding_metadata,'memory':memory()}
                else:
                    raise ValueError('Unknown worker operation')
            print(json.dumps({'ok':True,'value':result},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({'ok':False,'error_type':type(exc).__name__,'error':str(exc)}),flush=True)


if __name__=='__main__':main()
