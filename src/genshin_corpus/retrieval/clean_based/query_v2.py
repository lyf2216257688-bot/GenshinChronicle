"""Versioned research queries; the frozen original-only QuerySpec is unchanged."""
from dataclasses import asdict, dataclass, field

from ...evidence_io import digest, require
from .core import fuse_candidates, validate_profile


WEIGHTS = {"S0": {"bm25f": .5, "dense": .5},
           "S2": {"bm25f": .25, "dense": .5, "sparse": .25}}


def validate_profile_v2(profile, combination=None):
    """G6 admits the frozen P4 S2 point; this is not a tuning interface."""
    validate_profile(profile)
    selected = profile["combination"]
    require(selected in WEIGHTS and (combination is None or selected == combination),
            "Query/retrieval combination differs")
    require(profile["channel_weights"] == WEIGHTS[selected]
            and profile["fusion"] == "f1_max" and profile["structural_cleanup"] is True
            and all(profile[k] == v for k, v in {"depth": 50, "candidate_limit": 100, "rrf_k": 60}.items()),
            "Frozen S0/S2 operating point changed")


@dataclass(frozen=True)
class ResearchQuery:
    session_id: str
    turn_id: str
    query_id: str
    original_question: str
    subquestion: str
    search_text: str
    rerank_text: str
    kind: str = "original"
    round: int = 0
    parent_query_ids: tuple[str, ...] = ()
    parent_evidence_refs: tuple[str, ...] = ()
    trigger_gap: str = ""
    combination: str = "S0"
    version: str = field(default="gc-research-query-2", init=False)

    def __post_init__(self):
        for key in ("session_id", "turn_id", "query_id", "original_question", "subquestion", "search_text", "rerank_text"):
            require(isinstance(getattr(self, key), str) and bool(getattr(self, key).strip()), "Missing " + key)
        require(self.combination in WEIGHTS, "Only explicit S0/S2 is supported")
        require(type(self.round) is int and self.round >= 0, "Invalid research round")
        require(len(set(self.parent_query_ids)) == len(self.parent_query_ids) and self.query_id not in self.parent_query_ids,
                "Invalid query ancestry")
        if self.kind == "original":
            require(self.round == 0 and not self.parent_query_ids and not self.parent_evidence_refs
                    and self.subquestion == self.search_text == self.rerank_text == self.original_question,
                    "Original query must retain the complete original question")
        else:
            require(self.kind in {"rewrite", "subquestion", "step_back", "counterevidence", "follow_up"}
                    and self.round > 0 and bool(self.parent_query_ids) and bool(self.trigger_gap.strip()),
                    "Enhanced query requires parents, round and a concrete gap")

    @property
    def question_id(self):
        return self.turn_id

    @property
    def text(self):
        return self.search_text

    def value(self):
        value = asdict(self)
        value["parent_query_ids"] = list(self.parent_query_ids)
        value["parent_evidence_refs"] = list(self.parent_evidence_refs)
        return value

    @classmethod
    def parse(cls, value):
        require(value.get("version") == "gc-research-query-2", "Unsupported query version")
        return cls(**{**{k: v for k, v in value.items() if k != "version"},
                      "parent_query_ids": tuple(value["parent_query_ids"]),
                      "parent_evidence_refs": tuple(value["parent_evidence_refs"])})


def validate_ancestry(query, parents=(), evidence_refs=()):
    catalog = {p.query_id: p for p in parents}
    require(len(catalog) == len(parents), "Duplicate parent query")
    for pid in query.parent_query_ids:
        require(pid in catalog, "Unknown parent query")
        p = catalog[pid]
        require((p.session_id, p.turn_id, p.original_question) ==
                (query.session_id, query.turn_id, query.original_question) and p.round < query.round
                and p.combination == query.combination,
                "Foreign/cyclic parent query")
    require(set(query.parent_evidence_refs) <= set(evidence_refs), "Unknown parent evidence")


def fuse_v2(query, hits, documents, profile, *, input_bundle_id, code):
    validate_profile_v2(profile, query.combination)
    result = fuse_candidates(query, hits, documents, profile, input_bundle_id=input_bundle_id)
    result.update(version="gc-clean-candidates-2", query=query.value(),
                  query_digest=digest(query.value()), retrieval_code=code)
    result["status"] = "complete" if result["candidates"] else "empty"
    return result
