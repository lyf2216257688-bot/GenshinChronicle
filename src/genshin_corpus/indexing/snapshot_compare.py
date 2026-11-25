"""Full index payload comparisons for independently built versioned releases."""

from collections import Counter
from functools import lru_cache

from ..evidence_io import digest, require
from .schema import GRANULARITIES


def compare_vectors(left, right):
    import numpy as np

    require(left.contract == right.contract and left.active == right.active, "Numerical spaces or active rows differ")
    counts = Counter()
    load = lru_cache(maxsize=8)(lambda path: right.matrix(path, right.blocks[path][1]))
    positions = {}
    for path, (ref, meta) in right.blocks.items():
        active = set(ref["active_keys"])
        positions.update({(ref["channel"], key): (path, n) for n, key in enumerate(meta["keys"]) if key in active})
    for path, (ref, meta) in left.blocks.items():
        channel = ref["channel"]
        matrix = left.matrix(path, meta)
        active = set(ref["active_keys"])
        for i, key in enumerate(meta["keys"]):
            if key not in active:
                continue
            other, j = positions[channel, key]
            reference = load(other)
            if channel == "dense":
                identical = np.array_equal(matrix[i].view(np.uint32), reference[j].view(np.uint32))
            else:
                a, b = matrix.indptr[i:i + 2]
                x, y = reference.indptr[j:j + 2]
                identical = np.array_equal(matrix.indices[a:b], reference.indices[x:y]) and np.array_equal(
                    matrix.data[a:b].view(np.uint32), reference.data[x:y].view(np.uint32))
            require(identical, f"{channel} independent value differs for {key}")
            counts[channel] += 1
    return dict(counts)


def _lexical_stats(index, route):
    searcher, ids = index.route(route)
    if searcher is None:
        return {"count": 0}
    reader = searcher.reader()
    fields = [field for field in reader.schema.names() if "__" in field]
    entries = ((field, token.hex(), leaf.doc_frequency(field, token))
               for leaf, _ in reader.leaf_readers() for field, token in leaf.all_terms() if field in fields)
    df = Counter()
    for field, token, count in entries:
        df[field, token] += count
    return {"count": reader.doc_count(), "membership_sha256": digest(ids),
            "field_lengths": {field: reader.field_length(field) for field in fields},
            "df_sha256": digest([[field, token, n] for (field, token), n in sorted(df.items())])}


def compare_lexical(left, right, queries):
    require(left.effective == right.effective and left.groups == right.groups, "Lexical spaces or active rows differ")
    require(bool(queries) and all(isinstance(text, str) and text.strip() for text in queries), "A pinned test query list is required")
    stats = {route: _lexical_stats(left, route) for route in GRANULARITIES}
    require(stats == {route: _lexical_stats(right, route) for route in GRANULARITIES},
            "Independent BM25F global statistics differ")
    count = 0
    for text in queries:
        depth = max(1, left.manifest["counts"]["documents"])
        observed, reference = left.search(text, depth), right.search(text, depth)
        require(observed == reference, "Independent BM25F score or order differs")
        count += sum(map(len, observed.values()))
    return {"route_stats": stats, "queries": len(queries), "scores": count}
