"""Explicit local-only installation validation and bounded GC retrieval CLI."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from unittest.mock import patch

from .local_qwen3_embedding import (
    DIMENSION, MAX_LENGTH, EmbeddingBatch, LocalQwen3Config, LocalQwen3Embedding,
    build_local_index, file_sha256, local_candidates,
)
from .candidate_retrieval import (
    _descriptor, _field_aware_lexical_arm_identity, _field_aware_lexical_candidates_from_loaded,
    _field_rows, _load_dense, FIELD_AWARE_LEXICAL_FIELDS, DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS,
    hybrid_candidates,
)
from .lexical import analyze, LEXICAL_ANALYZER_VERSION
from .retrieval_units import load_retrieval_units

RU = Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json")
R1 = Path("data/retrieval/p05-navi-r1-discovery-20261025-r3-max")
G1 = Path("data/retrieval/p05-navi-g1-20261025-r2")
ARCHIVED_DENSE = Path(".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json")
MODEL = Path(".local/models/qwen3-embedding-0.6b/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


@contextmanager
def network_denied():
    attempts = []
    def deny(*args, **kwargs):
        attempts.append("socket connection attempted")
        raise RuntimeError("network forbidden during local embedding validation")
    with patch.object(socket.socket, "connect", deny), patch.object(socket.socket, "connect_ex", deny), patch.object(socket, "create_connection", deny):
        yield attempts
    if attempts:
        raise RuntimeError("local validation attempted network access")


def resources():
    import psutil
    import torch
    p = psutil.Process()
    result = {"rss_bytes": p.memory_info().rss, "process_memory": p.memory_info()._asdict(), "system_ram_available": psutil.virtual_memory().available}
    if torch.cuda.is_initialized():
        result["cuda"] = {"allocated": torch.cuda.memory_allocated(), "reserved": torch.cuda.memory_reserved(), "peak_allocated": torch.cuda.max_memory_allocated(), "peak_reserved": torch.cuda.max_memory_reserved(), "free_total": list(torch.cuda.mem_get_info())}
    return result


def timed(fn):
    import psutil
    samples = []
    stop = threading.Event()
    def sample():
        while not stop.wait(.1):
            samples.append(psutil.Process().memory_info().rss)
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    import torch
    if torch.cuda.is_initialized(): torch.cuda.synchronize()
    before = resources()
    start = time.perf_counter()
    try:
        value = fn()
        if torch.cuda.is_initialized(): torch.cuda.synchronize()
    finally:
        seconds = time.perf_counter()-start
        stop.set()
        thread.join()
    return value, {"seconds": seconds, "before": before, "after": resources(), "sampled_peak_rss": max(samples, default=before["rss_bytes"])}


def prepare(run):
    """586 frozen R1 narrative RUs plus G1 cached top-20 evidence candidates."""
    selection = read(R1 / "matched-body-selection.json")
    replay = read(G1 / "ordinary-unique-replay.json")
    ids = set(selection["unit_ids"])
    for info in replay["sources"].values(): ids.update(info["hybrid_unit_ids"][:20])
    manifest, units = load_retrieval_units(RU, selected_unit_ids=sorted(ids))
    inputs = [RU, R1/"matched-body-selection.json", R1/"corpus.json", R1/"representations.json", R1/"representation-vectors.npz", R1/"evaluator-only/reference-proposals.json", G1/"ordinary-unique-replay.json", ARCHIVED_DENSE]
    save(run/"selection.json", {"policy": "all 586 frozen R1 narrative RUs plus first 20 cached G1 Hybrid RUs per ordinary query; no label-based selection", "unit_ids": [u["unit_id"] for u in units], "r1_unit_ids": selection["unit_ids"], "row_count": len(units), "ru_build_identity": manifest["build_identity"], "inputs": [{"path":str(p),"sha256":file_sha256(p)} for p in inputs]})
    with gzip.open(run/"selected-rus.jsonl.gz", "xt", encoding="utf-8") as handle:
        for unit in units: handle.write(json.dumps(unit,ensure_ascii=False)+"\n")
    print(f"PREPARE PASS: {len(units)} formal RUs", flush=True)


def smoke(run, device):
    import numpy as np
    import torch
    encoder = LocalQwen3Embedding(LocalQwen3Config(MODEL, device, "float16" if device=="cuda" else "float32"))
    with network_denied() as attempts:
        _, load = timed(encoder.load)
        docs = ["钟离曾以摩拉克斯之名守护璃月，若陀龙王与他立下契约。", "渊下宫的居民在大日御舆照耀下生活，白夜国的历史保存在书卷之中。", "少女为雷鸟歌唱，歌声回荡在被迷雾笼罩的鹤观。", "古树见证了时间，水滴与种子承载过去和未来的故事。"]
        output, perf = timed(lambda: encoder.encode_documents(docs, batch_size=2))
        repeat = encoder.encode_documents(docs,batch_size=2)
        queries, qp = timed(lambda: encoder.encode_queries(["钟离与若陀龙王是什么关系？", "雷鸟为何记住少年的歌声？"],batch_size=2))
        delta = float(np.max(np.abs(output.vectors-repeat.vectors)))
        assert delta <= 1e-6
        assert output.vectors.shape == (4,DIMENSION) and queries.vectors.shape == (2,DIMENSION)
        assert np.allclose(np.linalg.norm(output.vectors,axis=1),1,atol=1e-6,rtol=0)
        assert np.allclose(np.linalg.norm(queries.vectors,axis=1),1,atol=1e-6,rtol=0)
        # Exact real-token boundary, rather than a character-length assumption.
        tokenizer = encoder._tokenizer
        # The pinned tokenizer appends one official end-of-text token. Count
        # postprocessing rather than confusing characters with encoded tokens.
        overhead=len(tokenizer('')["input_ids"])
        token_piece=next((p for p in ("的"," test"," hello"," x") if len(tokenizer(p*20)["input_ids"])==20+overhead),None)
        assert token_piece is not None
        texts = [token_piece*(n-overhead) for n in (MAX_LENGTH-1,MAX_LENGTH,MAX_LENGTH+1)]
        assert [len(tokenizer(t)["input_ids"]) for t in texts] == [MAX_LENGTH-1,MAX_LENGTH,MAX_LENGTH+1]
        boundary, bp = timed(lambda: encoder.encode_documents(texts,batch_size=1))
        assert [a["encoded_tokens"] for a in boundary.token_audit] == [2047,2048,2048]
        assert [a["truncated"] for a in boundary.token_audit] == [False,False,True]
        assert np.allclose(boundary.vectors[1],boundary.vectors[2],atol=1e-6,rtol=0)
        query_boundary = encoder.encode_queries(texts,batch_size=1)
        assert all(a["encoded_tokens"] == MAX_LENGTH for a in query_boundary.token_audit)
        speeds=[]
        for batch_size in (1,2,4):
            b, info=timed(lambda: encoder.encode_documents(docs*2,batch_size=batch_size))
            speeds.append({"batch_size":batch_size,"documents":8,"documents_per_second":8/info["seconds"],"tokens_per_second":sum(a["encoded_tokens"] for a in b.token_audit)/info["seconds"],**info})
    with (run/f"smoke-{device}-vectors.npz").open("xb") as handle:
        np.savez_compressed(handle,documents=output.vectors,queries=queries.vectors,boundary=boundary.vectors)
    report={"status":"PASS","device":device,"contract":encoder.contract,"space_identity":encoder.space_identity,"load":load,"document_encoding":perf,"query_encoding":qp,"same_input_repeat_max_abs":delta,"norm_min":float(np.linalg.norm(output.vectors,axis=1).min()),"norm_max":float(np.linalg.norm(output.vectors,axis=1).max()),"boundary":list(boundary.token_audit),"query_boundary":list(query_boundary.token_audit),"boundary_performance":bp,"throughput":speeds,"network_connection_attempts":len(attempts)}
    save(run/f"smoke-{device}.json",report)
    print(json.dumps({"smoke":device,"status":"PASS","load_seconds":load["seconds"],"docs_per_second":[s["documents_per_second"] for s in speeds]},ensure_ascii=False),flush=True)


def cid(unit):
    return unit["source"]["canonical_address"]["source_identity_key"].rsplit(":",1)[-1]


def cached_current_documents(texts):
    """Read exact existing 10-text R1 embedding batches; never call Campaign."""
    import numpy as np
    from .qwen_embedding import QwenEmbeddingRequest
    values=sorted(set(texts)); found={}; inputs=[]
    for start in range(0,len(values),10):
        batch=values[start:start+10]
        path=R1/"embeddings"/('embed-'+QwenEmbeddingRequest('document',tuple(batch)).request_identity+'.npz')
        vectors=np.load(path,allow_pickle=False)["vectors"]
        assert vectors.shape==(len(batch),2048)
        found.update(zip(batch,vectors));inputs.append({"path":str(path),"sha256":file_sha256(path)})
    return np.stack([found[t] for t in texts]),inputs


def retrieve(run,device='cuda'):
    import numpy as np
    selection=read(run/"selection.json")
    encoder=LocalQwen3Embedding(LocalQwen3Config(MODEL,device,'float16' if device=='cuda' else 'float32'))
    with network_denied() as attempts:
        manifest, perf=timed(lambda:build_local_index(RU,selection["unit_ids"],run/"dense",encoder,batch_size=2))
        document_perf=dict(encoder.last_encoding_metadata)
        print(json.dumps({'index_built':manifest['row_count'],'document_encoding':document_perf}),flush=True)
        _,vectors,rows=_load_dense(run/"dense/metadata/manifest.json",use_mmap=True)
        _,units=load_retrieval_units(RU,selected_unit_ids=selection["unit_ids"])
        mapping={u["unit_id"]:u for u in units}
        r1ids=selection["r1_unit_ids"]; r1units=[mapping[i] for i in r1ids]
        local_r1=vectors[[next(j for j,r in enumerate(rows) if r["unit_id"]==i) for i in r1ids]]
        old=read(ARCHIVED_DENSE); oldroot=ARCHIVED_DENSE.parent.parent
        oldrows=oldroot/old["artifacts"]["rows"]["path"]
        if file_sha256(oldrows)!=old["artifacts"]["rows"]["sha256"]:raise ValueError('archived row integrity')
        with gzip.open(oldrows,'rt',encoding='utf-8') as handle:
            positions={r['unit_id']:r['occurrence_index'] for r in map(json.loads,handle) if r['unit_id'] in r1ids}
        # Entire accepted matrix integrity is verified before this comparison.
        oldpath=oldroot/old["artifacts"]["vectors"]["path"]
        if file_sha256(oldpath)!=old['artifacts']['vectors']['sha256']:raise ValueError('archived vector integrity')
        oldmatrix=np.load(oldpath,mmap_mode='r',allow_pickle=False)
        archived=np.stack([oldmatrix[positions[i]] for i in r1ids]);del oldmatrix
        current,cache_inputs=cached_current_documents([u['retrieval_visible_text'] for u in r1units])
        representations=read(R1/'representations.json')
        historical=np.load(R1/'representation-vectors.npz',allow_pickle=False)['queries']
        qhistory=dict(zip(representations['query_texts'],historical))
        proposals=read(R1/'evaluator-only/reference-proposals.json')
        lexical_rows=_field_rows(r1units,analyze)
        lm={'arm_build_identity':_field_aware_lexical_arm_identity(selection['ru_build_identity']+'-bounded-r1'), 'row_count':len(r1units),'fields':list(FIELD_AWARE_LEXICAL_FIELDS),'field_weights':DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS,'analyzer_version':LEXICAL_ANALYZER_VERSION}
        comparisons=[];query_outputs=[]
        for source in sorted(representations['ordinary']):
            for mode,query_text in [('raw',representations['raw'][source][0]),('ordinary',representations['ordinary'][source])]:
                q=encoder.encode_query(query_text);query_outputs.append(q.vectors[0])
                eligible=[i for i,u in enumerate(r1units) if cid(u)!=source]
                lex=_field_aware_lexical_candidates_from_loaded(lm,lexical_rows,query_text,top_k=len(r1units),k1=1.2,b=.75,analyzer=analyze,field_weights=DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS)
                lex=[r for r in lex if cid(mapping[r['unit_id']])!=source]
                for i,r in enumerate(lex,1):r['rank']=i
                arms={}
                for label,matrix,qv in [('local',local_r1,q.vectors[0]),('bailian_archived',archived,qhistory[query_text]),('bailian_cached_current',current,qhistory[query_text])]:
                    scores=matrix@qv
                    ranked=sorted(eligible,key=lambda i:(-float(scores[i]),r1ids[i]))
                    dense=[{'unit_id':r1ids[i],'rank':rank,'retrieval':{'score':float(scores[i]),'arm_build_identity':label}} for rank,i in enumerate(ranked[:500],1)]
                    hybrid=hybrid_candidates(lex[:500],dense,lexical_build_identity=lm['arm_build_identity'],dense_build_identity=label,top_k=500)
                    for name,candidates in [('dense',dense),('hybrid',hybrid)]:
                        pages=list(dict.fromkeys(cid(mapping[r['unit_id']]) for r in candidates))
                        refs=[p for p in proposals if p['source']==source and not p['kind'].startswith('negative') and p['kind']!='duplicate_control']
                        arms[label+'_'+name]={'documents':pages[:20],'references':[{'id':p['id'],'targets':p['targets'],'target_scope_covered':any(cid(u) in p['targets'] for u in r1units),'first_target_rank':next((i for i,t in enumerate(pages,1) if t in p['targets']),None)} for p in refs]}
                comparisons.append({'source':source,'query_mode':mode,'query':query_text,'query_token_audit':q.token_audit[0],'arms':arms})
                print(f'Compared {source}/{mode}',flush=True)
        natural=[('character','钟离与若陀龙王之间有怎样的契约和冲突？'),('background','渊下宫居民为什么要寻找遗失的禁书？'),('paraphrase','有人用歌声安抚巨鸟，却在后来遭到献祭。'),('myth','三轮明月的姐妹为何只剩一具苍白的尸体？'),('literary','时间的种子与风中的故事之间有什么呼应？')]
        examples=[]
        all_lexical_rows=_field_rows(units,analyze)
        all_lm=dict(lm,arm_build_identity=_field_aware_lexical_arm_identity(selection['ru_build_identity']+'-bounded-909'))
        for category,text in natural:
            q=encoder.encode_query(text)
            candidates=local_candidates(run/'dense/metadata/manifest.json',q,top_k=10)
            for c in candidates:c['text_excerpt']=mapping[c['unit_id']]['retrieval_visible_text'][:500]
            lexical=_field_aware_lexical_candidates_from_loaded(all_lm,all_lexical_rows,text,top_k=500,k1=1.2,b=.75,analyzer=analyze,field_weights=DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS)
            dense500=local_candidates(run/'dense/metadata/manifest.json',q,top_k=500)
            hybrid=hybrid_candidates(lexical,dense500,lexical_build_identity=all_lm['arm_build_identity'],dense_build_identity=manifest['arm_build_identity'],top_k=10)
            examples.append({'category':category,'query':text,'candidates':candidates,'hybrid_candidates':hybrid})
        # G1's 13 model-useful pairs retain model-only / inactive status.
        replay=read(G1/'ordinary-unique-replay.json');g1=[]
        for source,info in replay['sources'].items():
            q=encoder.encode_query(representations['ordinary'][source])
            candidates=local_candidates(run/'dense/metadata/manifest.json',q,top_k=len(units))
            pages=list(dict.fromkeys(cid(mapping[c['unit_id']]) for c in candidates if cid(mapping[c['unit_id']])!=source))
            for row in replay['rows']:
                if row['model_verdict']!='useful':continue
                for observation in row['replay_observations']:
                    if observation['source']==source:
                        target=observation['target']
                        g1.append({'pair_id':row['pair_id'],'source':source,'target':target,'historical_full_hybrid_rank':observation['rank'],'local_subset_dense_rank':pages.index(target)+1 if target in pages else None,'target_scope_covered':target in {cid(u) for u in units},'not_human_gold':True})
    with (run/'comparison-query-vectors.npz').open('xb') as handle:np.savez_compressed(handle,vectors=np.stack(query_outputs))
    result={'status':'PASS','network_connection_attempts':len(attempts),'index':manifest,'index_performance':perf,'document_encoding':document_perf,'documents_per_second':len(rows)/perf['seconds'],'real_ru_truncated':sum(r['token_audit']['truncated'] for r in rows),'r1_same_scope_ru_count':len(r1ids),'r1_comparisons':comparisons,'natural_query_examples':examples,'g1_model_useful_diagnostic':g1,'cached_document_inputs':cache_inputs,'limitations':['Enriched selected subset, not full-corpus effectiveness','R1 proposals and G1 useful grades are not independent human gold','Cached current/archived Bailian vectors use historical provider execution; no new API calls','G1 historical full Hybrid and new subset Dense have different scope and algorithms','BGE embedding snapshot unavailable here; no same-condition BGE run or new model download']}
    save(run/'retrieval-validation.json',result)
    print(f'RETRIEVAL PASS: {len(rows)} RUs; {len(comparisons)} exact-input historical comparisons',flush=True)


def query(args):
    encoder=LocalQwen3Embedding(LocalQwen3Config(args.model,args.device,'float16' if args.device=='cuda' else 'float32'))
    with network_denied():
        candidates=local_candidates(args.index,encoder.encode_query(args.query),top_k=args.top_k)
    print(json.dumps({'query':args.query,'candidates':candidates},ensure_ascii=False,indent=2))


def rerank_probe(run):
    """Run with the existing BGE venv, after the embedding process has exited."""
    import torch
    from .bge_reranker_v2_m3 import BgeRerankerV2M3, BgeRerankerV2M3Config, MODEL_REVISION
    from .reranking import RerankRequest, RerankCandidate, project_reranker_text, project_ranked_candidates, fuse_ranked_candidates
    result=read(run/'retrieval-validation.json')
    example=result['natural_query_examples'][0]
    with gzip.open(run/'selected-rus.jsonl.gz','rt',encoding='utf-8') as handle:units={u['unit_id']:u for u in map(json.loads,handle)}
    candidates=example['hybrid_candidates']
    request_rows=[]
    for row in candidates:
        unit=units[row['unit_id']]
        fields=dict(unit['source']['record_context'],retrieval_visible_text=unit['retrieval_visible_text'])
        text,_=project_reranker_text(fields)
        request_rows.append(RerankCandidate(row['unit_id'],text,row['rank']))
    model=BgeRerankerV2M3(BgeRerankerV2M3Config(Path('.local/models/bge-reranker-v2-m3')/MODEL_REVISION))
    torch.cuda.reset_peak_memory_stats()
    started=time.perf_counter()
    with network_denied() as attempts:
        scores=model.rerank(RerankRequest('local-qwen3-compatibility',example['query'],tuple(request_rows)))
    torch.cuda.synchronize()
    reranked=project_ranked_candidates(candidates,scores)
    fused=fuse_ranked_candidates(candidates,reranked)
    save(run/'reranker-compatibility.json',{'status':'PASS','execution':'separate process and original BGE runtime; embedding not resident','candidate_count':len(candidates),'query':example['query'],'seconds_including_load_and_hash':time.perf_counter()-started,'cuda_peak_allocated':torch.cuda.max_memory_allocated(),'cuda_peak_reserved':torch.cuda.max_memory_reserved(),'network_connection_attempts':len(attempts),'fused':fused,'response_metadata':model.last_response_metadata})
    print('RERANKER COMPATIBILITY PASS',flush=True)


def main():
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','smoke','retrieve','query','rerank-probe'])
    parser.add_argument('--run',type=Path,default=Path('.local/embedding-tests/qwen3-0.6b-20261025-r1'))
    parser.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    parser.add_argument('--model',type=Path,default=MODEL)
    parser.add_argument('--index',type=Path)
    parser.add_argument('--query')
    parser.add_argument('--top-k',type=int,default=10)
    args=parser.parse_args()
    args.run.mkdir(parents=True,exist_ok=True)
    if args.stage=='prepare':prepare(args.run)
    elif args.stage=='smoke':smoke(args.run,args.device)
    elif args.stage=='retrieve':retrieve(args.run,args.device)
    elif args.stage=='rerank-probe':rerank_probe(args.run)
    else:
        if args.index is None or not args.query:parser.error('query requires --index and --query')
        query(args)


if __name__=='__main__':main()
