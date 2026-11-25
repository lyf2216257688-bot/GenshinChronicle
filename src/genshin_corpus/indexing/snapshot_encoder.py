"""Fixed-shape BGE-M3 document encoding for versioned numerical indexes."""

from collections import defaultdict
from pathlib import Path

from ..evidence_io import read, require, sha
from ..retrieval.clean_based.indexes import model_spec, selected
from .benchmark_dense import Encoder, validate_vectors
from .sparse_trial import SparseEncoder


LENGTHS = (64, 128, 256, 512, 768, 1024, 1152, 2048, 4096, 8192)


def _sparse_head(sparse):
    encoder, torch = sparse.encoder, sparse.torch
    if sparse.head is None:
        head = torch.nn.Linear(encoder.contract["dimensions"], 1,
                               dtype=getattr(torch, encoder.contract["compute_dtype"]))
        head.load_state_dict(sparse.head_state, strict=True)
        sparse.head = head.to("cuda").eval()
    return sparse.head


def _sparse_weights(sparse, head, hidden, tokens):
    excluded = {getattr(sparse.encoder.tokenizer, key) for key in sparse.contract["special_tokens"]}
    weights = sparse.torch.relu(head(hidden)).squeeze(-1)
    require(bool(weights.isfinite().all()), "Nonfinite Sparse token weight")
    result = []
    for ids, mask, values in zip(tokens["input_ids"].tolist(), tokens["attention_mask"].tolist(), weights.float().tolist()):
        row = {}
        for token, visible, weight in zip(ids, mask, values):
            if visible and token not in excluded and weight > 0:
                row[token] = max(row.get(token, 0.0), weight)
        result.append(row)
    return result


class SnapshotEncoder:
    def __init__(self, repo, *, sparse):
        repo = Path(repo).resolve()
        config = read(repo / "config/clean-retrieval-v1.json")
        selection, manifests = selected(repo, config)
        self.backbone = Encoder(model_spec(selection, manifests["dense"][1]), repo)
        torch = self.backbone.torch
        require(torch.cuda.is_available(), "The versioned representation requires CUDA")
        require(self.backbone.contract["pooling"] == "cls_token", "The fixed-shape encoder requires BGE CLS pooling")
        self.sparse = SparseEncoder(self.backbone, head_sha256=config["sparse_head_sha256"]) if sparse else None
        common = {
            "backbone": self.backbone.contract,
            "document_shape": {"batch": 2, "lengths": list(LENGTHS), "tail": "duplicate real input"},
            "query_shape": "single, no extra padding",
            "runtime": {"gpu": torch.cuda.get_device_name(), "capability": list(torch.cuda.get_device_capability()),
                        "cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
                        "encoder_sha256": sha(__file__), "dense_sha256": sha(Path(__file__).with_name("benchmark_dense.py"))},
        }
        self.contract = {"dense": common}
        if self.sparse:
            self.contract["sparse"] = {
                **common, "head": self.sparse.contract, "vocabulary_size": read(self.backbone.path / "config.json")["vocab_size"],
                "kernel_sha256": sha(Path(__file__).with_name("sparse_trial.py")),
            }

    def encode(self, texts, channels):
        import numpy as np

        encoder, torch = self.backbone, self.backbone.torch
        lengths = encoder.lengths(texts)
        require(max(lengths) <= min(LENGTHS[-1], encoder.contract["max_tokens"]), "Overlength document. No truncation permitted")
        groups = defaultdict(list)
        for number, length in enumerate(lengths):
            groups[next(pad for pad in LENGTHS if pad >= length)].append(number)
        encoder.load()
        result = {}
        if "dense" in channels:
            result["dense"] = np.empty((len(texts), encoder.contract["dimensions"]), dtype=np.float32)
        sparse = self.sparse if "sparse" in channels else None
        if sparse:
            head = _sparse_head(sparse)
            result["sparse"] = [None] * len(texts)
        for pad, group in sorted(groups.items()):
            for offset in range(0, len(group), 2):
                actual = group[offset:offset + 2]
                batch = actual if len(actual) == 2 else actual * 2
                tokens = encoder.tokenizer(encoder.project([texts[i] for i in batch], "document"),
                                           padding="max_length", max_length=pad, truncation=False,
                                           add_special_tokens=True, return_tensors="pt").to("cuda")
                require(tuple(tokens["input_ids"].shape) == (2, pad)
                        and tokens["attention_mask"].sum(dim=1).tolist() == [lengths[i] for i in batch],
                        "Fixed padding changed the token input")
                with torch.inference_mode():
                    hidden = encoder.model(**tokens, use_cache=False).last_hidden_state
                    if "dense" in result:
                        pooled = hidden[:, 0].float()
                        vectors = (pooled / torch.linalg.vector_norm(pooled, dim=1, keepdim=True)).cpu().numpy()
                        result["dense"][actual] = vectors[:len(actual)]
                    if sparse:
                        vectors = _sparse_weights(sparse, head, hidden, tokens)
                        for number, vector in zip(actual, vectors):
                            result["sparse"][number] = vector
        if "dense" in result:
            validate_vectors(result["dense"], len(texts), encoder.contract["dimensions"])
        return result

    def query(self, text, channel):
        encoder = self.backbone if channel == "dense" else self.sparse
        return encoder.encode([text], role="query")[0]

    def close(self):
        if self.sparse:
            self.sparse.close()
        else:
            self.backbone.model = None
            self.backbone.torch.cuda.empty_cache()
