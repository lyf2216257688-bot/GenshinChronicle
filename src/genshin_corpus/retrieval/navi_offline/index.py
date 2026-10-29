"""Typed, resumable exact local vector indexes; embedding windows are not evidence."""
import json
import os
from pathlib import Path

import numpy as np

from .common import digest, read, require, seal, sha, verify, write

VERSION = 'p05-navi-typed-index-1'


def windows(text, encoder):
    require(isinstance(text, str) and bool(text), 'Empty embedding text')
    encoder.load()
    tokenizer = encoder._tokenizer
    start = 0
    while start < len(text):
        lo, hi = start + 1, len(text)
        # Longest character prefix under the actual pinned tokenizer/EOS budget.
        require(len(tokenizer(text[start:lo], add_special_tokens=True)['input_ids']) <= 2048, 'Single character exceeds model window')
        while lo < hi:
            mid = (lo + hi + 1)//2
            if len(tokenizer(text[start:mid], add_special_tokens=True)['input_ids']) <= 2048:
                lo = mid
            else:
                hi = mid-1
        yield start, lo, text[start:lo]
        start = lo


def build(root, items, encoder, *, kind, input_identity, shard_size=4096, batch_size=2):
    require(kind in {'ru', 'card'} and 1 <= shard_size <= 4096, 'Invalid typed index policy')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    spec = {'version': VERSION, 'kind': kind, 'input_identity': input_identity,
            'encoder_space': encoder.space_identity, 'encoder_contract': encoder.contract,
            'projection': 'exact_ru_text' if kind == 'ru' else 'card_semantic_labeled_v1',
            'window_policy': 'contiguous_character_ranges_tokenizer_le2048_including_eos',
            'shard_size': shard_size, 'batch_size': batch_size, 'dimension': 1024}
    if (root / 'spec.json').exists():
        require(read(root / 'spec.json') == spec, 'Index resume identity changed')
    else:
        write(root / 'spec.json', spec)
    if (root / 'complete.json').exists():
        verify(root)
        return read(root / 'manifest.json')
    existing = []
    for folder in sorted((root / 'shards').glob('*')):
        if (folder / 'complete.json').exists():
            meta = verify(folder)
            existing.append((meta['number'], folder, meta))
    existing.sort()
    require([i for i, _, _ in existing] == list(range(len(existing))), 'Shard checkpoint gap/duplicate')
    previous = (r for _, folder, _ in existing for r in read(folder / 'rows.json'))
    prior_remaining = sum(meta['rows'] for _, _, meta in existing)
    pending, number, shards = [], len(existing), [folder.relative_to(root).as_posix() for _,folder,_ in existing]

    def flush():
        nonlocal pending, number
        if not pending:
            return
        number_path = root / 'shards'
        number_path.mkdir(exist_ok=True)
        attempt = len(list(number_path.glob(f'{number:08d}-*')))
        folder = number_path / f'{number:08d}-{attempt:03d}'
        folder.mkdir()
        values = np.lib.format.open_memmap(folder / 'vectors.npy', mode='w+', dtype=np.float32, shape=(len(pending), 1024))
        audits = []
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start+batch_size]
            try:
                encoded = encoder.encode_documents([r['_text'] for r in batch], batch_size=batch_size)
            except BaseException:
                values.flush()
                values._mmap.close()
                raise
            require(encoded.space_identity == encoder.space_identity and encoded.role == 'document', 'Encoder identity changed')
            require(encoded.vectors.shape == (len(batch), 1024), 'Vector shape differs')
            require(all(not a['truncated'] for a in encoded.token_audit), 'Window unexpectedly truncated')
            require(np.isfinite(encoded.vectors).all() and np.allclose(np.linalg.norm(encoded.vectors,axis=1),1,atol=1e-5), 'Invalid normalized vectors')
            values[start:start+len(batch)] = encoded.vectors
            audits.extend(encoded.token_audit)
        values.flush()
        values._mmap.close()
        del values
        with (folder / 'vectors.npy').open('r+b') as h:
            os.fsync(h.fileno())
        rows = [{k:v for k,v in row.items() if k != '_text'} for row in pending]
        write(folder / 'rows.json', rows)
        write(folder / 'token-audit.json', audits)
        seal(folder, {'number': number, 'rows': len(rows)})
        shards.append(folder.relative_to(root).as_posix())
        number += 1
        pending = []

    for item in items:
        require(set(item) == {'id', 'source_id', 'text'}, 'Index item shape differs')
        for start, end, text in windows(item['text'], encoder):
            row = {'owner_id': item['id'], 'kind': kind, 'source_id': item['source_id'], 'start': start, 'end': end,
                   'text_sha256': digest(text), 'window_id': digest([kind,item['id'],start,end,text])}
            if prior_remaining:
                require(next(previous) == row, 'Completed shard input/order changed')
                prior_remaining -= 1
                continue
            pending.append(row | {'_text': text})
            if len(pending) == shard_size:
                flush()
    require(prior_remaining == 0, 'Resume input became shorter')
    flush()
    content=[{'path':s,'complete_sha256':sha(root/s/'complete.json')} for s in shards]
    manifest = {'spec': spec, 'identity': digest([spec,content]), 'shard_content':content,'shards': shards,
                'window_count': sum(read(root/s/'complete.json')['metadata']['rows'] for s in shards)}
    if (root/'manifest.json').exists():
        require(read(root/'manifest.json')==manifest,'Unsealed index manifest differs')
    else:
        write(root / 'manifest.json', manifest)
    seal(root)
    return manifest


class Index:
    def __init__(self, root, *, kind, encoder_space):
        self.root = Path(root)
        verify(root)
        self.manifest = read(self.root / 'manifest.json')
        spec = self.manifest['spec']
        require(self.manifest['identity']==digest([spec,self.manifest['shard_content']]),'Index content identity differs')
        require(spec['kind'] == kind and spec['encoder_space'] == encoder_space and spec['dimension'] == 1024, 'Foreign index kind/model/device/dimension')
        self.kind, self.space = kind, encoder_space
        self.stamps = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}

    def search(self, query, *, kind, top_k=20):
        require(kind == self.kind and query.role == 'query' and query.space_identity == self.space, 'Foreign query/kind')
        require(type(top_k) is int and top_k > 0, 'Invalid top-k')
        require(query.vectors.shape == (1,1024) and np.isfinite(query.vectors).all() and
                np.allclose(np.linalg.norm(query.vectors,axis=1),1,atol=1e-5), 'Invalid query vector')
        current = {p: (p.stat().st_size,p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        require(current == self.stamps, 'Opened index changed')
        best = {}
        for name in self.manifest['shards']:
            folder = self.root / name
            rows = read(folder / 'rows.json')
            values = np.load(folder / 'vectors.npy', mmap_mode='r', allow_pickle=False)
            require(values.shape == (len(rows),1024), 'Shard shape differs')
            scores = values @ query.vectors[0]
            for row, raw_score in zip(rows, scores):
                score, uid = float(raw_score), row['owner_id']
                if uid in best and best[uid]['score'] >= score:
                    continue
                if uid not in best and len(best) >= top_k:
                    worst = max(best, key=lambda key: (-best[key]['score'], key))
                    if (-score,uid) >= (-best[worst]['score'],worst):
                        continue
                    del best[worst]
                best[uid] = {'id': uid, 'source_id': row['source_id'], 'kind': self.kind, 'score': score,
                             'window_id': row['window_id'], 'index_identity': self.manifest['identity']}
            del values
        return [v | {'rank':i} for i,v in enumerate(sorted(best.values(), key=lambda x:(-x['score'],x['id'])),1)]


def query(encoder, text):
    from ..local_qwen3_embedding import project_text
    encoder.load()
    ids = encoder._tokenizer(project_text(text,'query'), add_special_tokens=True)['input_ids']
    require(len(ids) <= 2048, 'Query exceeds actual tokenizer window')
    result = encoder.encode_query(text)
    require(all(not a['truncated'] for a in result.token_audit), 'Query truncated')
    return result
