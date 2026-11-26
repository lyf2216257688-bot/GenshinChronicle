"""Durable release-scoped research, verified source readback and Packet delivery."""

from contextlib import ExitStack, closing
from difflib import SequenceMatcher
import sqlite3
import time

from ..evidence_io import digest, require
from ..generation.clean_evidence import EVIDENCE_QUALITY_ADMISSION, EvidenceReader, assemble, fragment_content
from ..mcp_offline.study import Studies, WRITES, load, page, put
from ..model_calls import locked
from ..rag.clean_research_answer import bind_answer
from ..rag.clean_research_catalog import _row as catalog_row
from ..retrieval.clean_based.query_v2 import ResearchQuery


MAX_QUERIES = 5
MAX_READS = 32
MAX_OPERATIONS = 96
FIRST_READS = 6
BUDGET = {"context_tokens": 60000, "output_tokens": 8192, "thinking_tokens": 0}


def assemble_live(groups, receipts, context, selection=None):
    options = {"context": context, "budget": BUDGET, "selection_ids": selection}
    if selection is not None:
        return assemble(groups, receipts, **options, compact_content=True)
    original = assemble(groups, receipts, **options)
    preserved = [entry["fragment_id"] for entry in original["evidence"]]
    compact = assemble(groups, receipts, **{**options, "selection_ids": preserved},
                       admission=EVIDENCE_QUALITY_ADMISSION, compact_content=True)
    visible = {entry["fragment_id"] for entry in compact["evidence"]}
    return compact if set(preserved) <= visible else original


class LiveStudies:
    def __init__(self, releases, root, active):
        self.releases, self.root = releases, root
        self.active = releases.selected(active)
        # Use the same content-addressed artifact and operation tables as the offline service.
        self.path = Studies(None, root).path
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")

    def close(self):
        self.releases.close()

    def call(self, name, args, check):
        if name == "get_scope":
            return {"new_studies": self.active.summary(), "releases": self.releases.catalog(),
                    "retrieval": "S0", "reranking": "bge_structural", "remote_calls": 0,
                    "limits": {"queries": MAX_QUERIES, "reads": MAX_READS, "operations": MAX_OPERATIONS}}
        study, writing = args["study"], name in WRITES
        with ExitStack() as stack:
            if writing:
                try:
                    stack.enter_context(locked(self.root / "write.lock"))
                except OSError as exc:
                    raise ValueError("Study store is busy") from exc
            db = stack.enter_context(closing(sqlite3.connect(self.path, timeout=2)))
            db.execute("BEGIN")
            row = db.execute("SELECT binding,state FROM studies WHERE id=?", (study,)).fetchone()
            state = load(db, row[1]) if row else None
            release = self.releases.resolve(state["release"], row[0]) if row else self.active
            release.files.check()
            if writing or name == "request_status":
                saved = db.execute("SELECT args,result FROM operations WHERE study=? AND id=?",
                                   (study, args["request_id"])).fetchone()
                if name == "request_status":
                    if saved:
                        return {"status": "committed", "result": load(db, saved[1])}
                    failure = db.execute("SELECT error_type FROM failed_attempts WHERE study=? AND id=? "
                                         "AND binding=? ORDER BY rowid DESC LIMIT 1",
                                         (study, args["request_id"], release.binding)).fetchone()
                    return {"status": "no_committed_result", "last_failure_type": failure[0] if failure else None}
                if saved:
                    require(load(db, saved[0]) == [name, args], "Request ID reused with different arguments")
                    return load(db, saved[1])
            if name == "open_study":
                require(state is None and args["question"].strip(), "Existing study or empty question")
                state = {"release": release.key, "question": args["question"], "groups": {},
                         "reads": {}, "scopes": {}, "packets": {}, "current": None}
            else:
                require(state is not None, "Unknown study")
            if writing:
                count, = db.execute("SELECT count(*) FROM operations WHERE study=?", (study,)).fetchone()
                require(count < MAX_OPERATIONS, "Study operation budget exhausted")
            try:
                check()
                result = Research(db, study, release, state, check).run(name, args, self.root)
                release.files.check()
                check()
                result.update(study=study, **release.summary())
                if writing:
                    db.execute("INSERT INTO studies VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                               (study, release.binding, put(db, state)))
                    db.execute("INSERT INTO operations VALUES (?,?,?,?)",
                               (study, args["request_id"], put(db, [name, args]), put(db, result)))
                    check()
                db.commit()
            except Exception as exc:
                db.rollback()
                if writing:
                    db.execute("INSERT INTO failed_attempts VALUES (?,?,?,?,?,?)",
                               (study, args["request_id"], release.binding, digest([name, args]),
                                type(exc).__name__, str(exc)[:512]))
                    db.commit()
                raise
            return result


class Research:
    def __init__(self, db, study, release, state, check):
        self.db, self.study, self.release, self.state, self.check = db, study, release, state, check

    def address(self, kind, key):
        return digest([self.release.binding, self.study, kind, key])

    def load_groups(self):
        return {key: load(self.db, value["ref"]) for key, value in self.state["groups"].items()}

    def candidate_handles(self, ranked):
        # The ranked group is hash-checked on load and fully registered against the
        # bound reader before commit. EvidenceReader.read verifies a chosen row again.
        return {c["chunk_id"]: digest(["gc-source-handle-1", ranked["input_bundle_id"],
                                       c["chunk_id"], digest(c["source"])]) for c in ranked["candidates"]}

    def save_read(self, evidence, handle, scope):
        self.check()
        receipt = evidence.read(handle, scope)
        raw = receipt["source_snapshot"].get("raw_observations")
        effective_run = raw["effective"]["archive"]["run_id"] if raw else self.release.spec["raw_run"]
        require(raw is None or raw["current"]["archive"]["run_id"] == self.release.spec["raw_run"],
                "Foreign current Raw observation")
        require(all(t["raw_lineage"]["raw_run_id"] == effective_run
                    for f in receipt["fragments"] for t in f["texts"]), "Foreign effective Raw observation")
        reads = self.state["reads"]
        rid = receipt["receipt_id"]
        require(rid in reads or len(reads) < MAX_READS, "Study read budget exhausted")
        reads[rid] = put(self.db, receipt)
        scopes = self.state["scopes"].setdefault(receipt["chunk_id"], [])
        if scope not in scopes:
            scopes.append(scope)
        return receipt

    def search(self, args, groups, root):
        require(len(groups) < MAX_QUERIES, "Study query budget exhausted")
        question = self.state["question"]
        if not groups:
            query = ResearchQuery(self.study, self.study, "original", question, question, question, question)
            parents, refs = [], ()
        else:
            key, parent = args["request_id"], args["parent_query"]
            require(key not in groups and parent in groups, "Duplicate query or unknown parent")
            handles = self.candidate_handles(groups[parent]).values()
            offered = {self.address("source", h): h for h in handles}
            inputs = args.get("evidence_handles", [])
            require(set(inputs) <= offered.keys(), "Foreign follow-up source handle")
            refs = tuple(offered[h] for h in inputs)
            parents = [ResearchQuery.parse(group["query"]) for group in groups.values()]
            previous = ResearchQuery.parse(groups[parent]["query"])
            text = args["search_text"]
            query = ResearchQuery(self.study, self.study, key, question, args.get("subquestion", text),
                                  text, args.get("rerank_text", text), args.get("kind", "follow_up"),
                                  previous.round + 1, (parent,), refs, args["gap"])
        started = time.perf_counter()
        ranked = self.release.search(query, parents, refs, root / "queries", self.check)
        ranked_at = time.perf_counter()
        require(ranked["query"] == query.value(), "Ranked query identity differs")
        evidence = EvidenceReader(self.release.reader, verified_parent_ranges=True)
        handles = evidence.register(ranked)
        require(handles == self.candidate_handles(ranked), "Registered source handle differs")
        for handle in list(handles.values())[:FIRST_READS]:
            self.save_read(evidence, handle, "chunk")
        self.state["groups"][query.query_id] = {"ref": put(self.db, ranked), "query": query.value(),
                                               "candidates": len(handles)}
        groups[query.query_id] = ranked
        result = self.publish(groups, {})
        result.update(query=query.value(), search_seconds=ranked_at - started,
                      read_and_packet_seconds=time.perf_counter() - ranked_at)
        return result

    def publish(self, groups, args):
        context = {"system": "依据已核验原文作答，保留来源范围、说话归属、条件和未知状态。",
                   "question": self.state["question"], "conversation": {"query_ids": list(groups)},
                   "tools": [], "protocol_history": []}
        receipts = [load(self.db, ref) for ref in self.state["reads"].values()]
        selected = args.get("fragment_ids")
        packet = assemble_live(list(groups.values()), receipts, context, selected)
        pref = self.address("packet", packet["packet_id"])
        self.state["current"] = pref
        self.state["packets"][pref] = put(self.db, packet)
        result = {**self.packet_view(packet, 0), "query_ids": list(groups)}
        if selected is not None:
            result["selected_dispositions"] = [part for row in packet["dispositions"] for part in row["fragments"]
                                               if part["fragment_id"] in selected]
        return result

    def packet(self, pref):
        require(pref in self.state["packets"], "Foreign Packet")
        return load(self.db, self.state["packets"][pref])

    def packet_view(self, packet, offset, citation=None):
        return {**Studies.packet_view(packet, self.address, offset, citation=citation),
                "budget": packet["policy"]["budget"], "estimated_message_tokens": packet["estimated_message_tokens"]}

    def source_identity(self, receipt):
        fragment, = receipt["fragments"]
        row = receipt["source_snapshot"]
        raw = row.get("raw_observations")
        effective = raw["effective"]["archive"]["run_id"] if raw else self.release.spec["raw_run"]
        return {"source_item": fragment["source_item"], "observation_id": fragment["observation_id"],
                "scope": receipt["scope"], "handle": self.address("source", receipt["handle"]),
                "read_ref": self.address("read", receipt["receipt_id"]),
                "current_raw_run": self.release.spec["raw_run"], "effective_raw_run": effective,
                "raw_byte_revalidation": "current_and_effective" if raw else "not_performed"}

    def show_read(self, receipt, offset):
        fragment, = receipt["fragments"]
        return {**self.source_identity(receipt), "receipt_id": receipt["receipt_id"], "fragment_id": fragment["fragment_id"],
                "read_extent": receipt["read_extent"], "unread": receipt["unread"], "semantic_support": "NOT_EVALUATED",
                "projection": page(fragment_content(fragment), offset)}

    def run(self, name, args, root):
        if name == "get_study":
            return {"question": self.state["question"], "queries": [{k: v for k, v in row.items() if k != "ref"}
                                                                   for row in self.state["groups"].values()],
                    "current_packet_ref": self.state["current"], "packet_refs": list(self.state["packets"]),
                    "read_refs": [self.address("read", receipt) for receipt in self.state["reads"]]}
        if name == "get_packet":
            return self.packet_view(self.packet(args["packet_ref"]), args.get("offset", 0), args.get("citation"))
        if name == "check_answer":
            require(args["packet_ref"] == self.state["current"], "Foreign or superseded Packet")
            packet = self.packet(args["packet_ref"])
            _, ids, status = bind_answer(packet, args["answer"])
            bindings = {i: packet["citations"][i] for i in ids}
            return {**status, "citations": bindings, "sources": {
                i: self.source_identity(load(self.db, self.state["reads"][b["receipt_id"]])) for i, b in bindings.items()}}
        if name == "get_read":
            key = next((ref for rid, ref in self.state["reads"].items() if self.address("read", rid) == args["read_ref"]), None)
            require(key is not None, "Foreign read receipt")
            return self.show_read(load(self.db, key), args.get("offset", 0))
        groups = self.load_groups()
        if name in {"open_study", "follow_query"}:
            return self.search(args, groups, root)
        if name == "assemble_packet":
            return self.publish(groups, args)
        if name == "browse_candidates":
            require(args["query"] in groups, "Query outside study")
            return self.browse(groups, args)
        if name == "read_source":
            evidence = EvidenceReader(self.release.reader, verified_parent_ranges=True)
            for ranked in groups.values():
                handles = self.candidate_handles(ranked)
                for item in ranked["candidates"]:
                    handle = handles[item["chunk_id"]]
                    if self.address("source", handle) == args["handle"]:
                        evidence.handles[handle] = item
                        return self.show_read(self.save_read(evidence, handle, args["scope"]), 0)
            raise ValueError("Source handle outside study")
        raise ValueError("Unknown study action")

    def browse(self, groups, args):
        key, start = args["query"], args.get("offset", 0)
        ranked = groups[key]
        handles = self.candidate_handles(ranked)
        items = ranked["candidates"]
        packet = self.packet(self.state["current"])
        index = list(groups).index(key)
        dispositions = {d["chunk_id"]: d["disposition"] for d in packet["dispositions"] if d["group"] == index}
        require(start <= len(items), "Candidate cursor outside group")
        rows = []
        matcher = SequenceMatcher(None, "", ranked["query"]["search_text"][:500], autojunk=False)
        for item in items[start:start + 12]:
            cid = item["chunk_id"]
            scopes = self.state["scopes"].get(cid, [])
            disposition = dispositions[cid]
            included = disposition in {"included", "context_expansion"}
            status = "in_current_packet" if included else "read" if scopes else "not_read"
            row = catalog_row(item, item["reranking"]["final_rank"], self.address("source", handles[cid]),
                              matcher, status, {}, included)
            rows.append({**row, "read_scopes": scopes, "packet_disposition": disposition})
        end = start + len(rows)
        return {"query": key, "total": len(items), "rows": rows, "navigation_only": True,
                "next_offset": end if end < len(items) else None}
