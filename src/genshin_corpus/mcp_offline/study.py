"""Transactional, bounded studies over frozen rankings. No inference or host session state."""
from contextlib import closing
import json
import sqlite3

from ..chunking.dialogue import label
from ..evidence_io import digest, require, strict_json
from ..generation.clean_evidence import EvidenceReader, assemble, fragment_content, validate_citations
from ..rag.clean_research_answer import bind_answer
from ..retrieval.clean_based.query_v2 import ResearchQuery, validate_ancestry


WRITES = {"open_study", "follow_query", "read_source", "assemble_packet"}
PAGE_CHARS = 12000


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def page(value, offset, limit=None):
    """An explicit page of the lossless projection. Offsets address JSON text, never source text."""
    body = encode(value)
    require(0 <= offset < len(body), "Page cursor outside body")
    end = min(offset + (PAGE_CHARS if limit is None else limit), len(body))
    result = {"offset": offset, "total_chars": len(body), "next_offset": end if end < len(body) else None,
              "projection_sha256": digest(value)}
    if offset == 0 and end == len(body):
        result["body"] = value
    else:
        result["partial_json_text"] = body[offset:end]
    return result


def put(db, value):
    key = digest(value)
    db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (key, encode(value)))
    return key


def load(db, key):
    row = db.execute("SELECT value FROM artifacts WHERE hash=?", (key,)).fetchone()
    require(row is not None, "Missing audit artifact")
    value = strict_json(row[0])
    require(digest(value) == key, "Audit artifact changed")
    return value


class Studies:
    def __init__(self, archive, root):
        self.archive = archive
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "studies.sqlite"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(study TEXT, id TEXT, args TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(study,id));
                CREATE TABLE IF NOT EXISTS failed_attempts(study TEXT, id TEXT, binding TEXT, args_hash TEXT,
                    error_type TEXT, detail TEXT);
            """)

    def call(self, name, args, check_cancel):
        a = self.archive
        a.files.check()
        if name == "list_cases":
            return {"mode": a.catalog["source"], "binding": a.binding,
                    "cases": [{"id": c["id"], "question": c["question"],
                               "queries": list(c["groups"])} for c in a.cases.values()]}
        study = args["study"]
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.execute("BEGIN IMMEDIATE" if name in WRITES else "BEGIN")
            row = db.execute("SELECT binding,state FROM studies WHERE id=?", (study,)).fetchone()
            require(row is None or row[0] == a.binding, "Study bound to a different release or implementation")
            if name in WRITES or name == "request_status":
                saved = db.execute("SELECT args,result FROM operations WHERE study=? AND id=?",
                                   (study, args["request_id"])).fetchone()
                if name == "request_status":
                    if saved:
                        return {"status": "committed", "result": load(db, saved[1])}
                    failure = db.execute("SELECT error_type FROM failed_attempts WHERE study=? AND id=? AND binding=? "
                                         "ORDER BY rowid DESC LIMIT 1", (study,args["request_id"],a.binding)).fetchone()
                    return {"status": "no_committed_result", "last_failure_type": failure[0] if failure else None}
                if saved:
                    require(load(db, saved[0]) == [name, args], "Request ID reused with different arguments")
                    return load(db, saved[1])
            if name == "open_study":
                require(row is None and args["case"] in a.cases, "Existing study or unknown case")
                state = {"case": args["case"], "groups": [], "reads": {}, "scopes": {}, "packet": None}
            else:
                require(row is not None, "Unknown study")
                state = load(db, row[1])
            if name in WRITES:
                count, = db.execute("SELECT count(*) FROM operations WHERE study=?", (study,)).fetchone()
                require(count < 96, "Study operation budget exhausted")
            try:
                check_cancel()
                result = self.execute(db, name, args, state, check_cancel)
                a.files.check()
                check_cancel()
            except (ValueError, OSError, KeyError, IndexError, TypeError) as exc:
                db.rollback()
                if name in WRITES:
                    db.execute("INSERT INTO failed_attempts VALUES (?,?,?,?,?,?)",
                               (study, args["request_id"], a.binding, digest([name,args]), type(exc).__name__, str(exc)[:512]))
                    db.commit()
                raise
            result.update(study=study, binding=a.binding, source=a.catalog["source"], raw_run=a.catalog["raw_run"])
            if name in WRITES:
                db.execute("INSERT INTO studies VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                           (study, a.binding, put(db, state)))
                db.execute("INSERT INTO operations VALUES (?,?,?,?)",
                           (study, args["request_id"], put(db, [name, args]), put(db, result)))
            db.commit()
            return result

    def execute(self, db, name, args, state, check_cancel):
        archive, case, study = self.archive, state["case"], args["study"]
        def address(kind, key):
            return digest([archive.binding, study, kind, key])

        groups, candidates = [], {}
        evidence = EvidenceReader(archive.reader, verified_parent_ranges=True)

        def register(key):
            ranked, handles = archive.group(case, key)
            groups.append(ranked)
            for item in ranked["candidates"]:
                handle = handles[item["chunk_id"]]
                evidence.handles[handle] = item
                candidates[address("source", handle)] = handle
            return ranked, handles

        def save_read(handle, scope):
            check_cancel()
            receipt = evidence.read(handle, scope)
            require(all(t["raw_lineage"]["raw_run_id"] == archive.catalog["raw_run"]
                        for f in receipt["fragments"] for t in f["texts"]), "Foreign Raw observation")
            reads = state["reads"]
            require(receipt["receipt_id"] in reads or len(reads) < 32, "Study read budget exhausted")
            reads[receipt["receipt_id"]] = put(db, receipt)
            scopes = state["scopes"].setdefault(receipt["chunk_id"], [])
            if scope not in scopes:
                scopes.append(scope)
            return receipt

        for key in state["groups"]:
            register(key)
        if name in {"open_study", "follow_query"}:
            key = args.get("query", "original")
            require(key not in state["groups"], "Query already used in this study")
            ranked, _ = archive.group(case, key)
            query = ResearchQuery.parse(ranked["query"])
            validate_ancestry(query, [ResearchQuery.parse(g["query"]) for g in groups], evidence.handles)
            if name == "follow_query":
                require(query.kind != "original" and args["parent_query"] in query.parent_query_ids
                        and args["gap"].strip(), "Missing or foreign follow-up parent/gap")
                require(args["evidence_handles"] == [address("source", h) for h in query.parent_evidence_refs],
                        "Follow-up evidence handles differ")
            ranked, handles = register(key)
            state["groups"].append(key)
            for handle in list(handles.values())[:6]:
                save_read(handle, "chunk")
        if name in {"open_study", "follow_query", "assemble_packet"}:
            context, golden = archive.initial(case)
            if name != "open_study":
                context = {"system": "按来源范围使用证据，保留条件与未知状态。",
                           "question": archive.cases[case]["question"], "tools": [], "protocol_history": [],
                           "conversation": {"frozen_queries": [g["query"] for g in groups]}}
            receipts = [load(db, ref) for ref in state["reads"].values()]
            for receipt in receipts:
                check_cancel()
                if receipt["receipt_id"] not in evidence.receipts:
                    require(evidence.read(receipt["handle"], receipt["scope"]) == receipt, "Saved source receipt changed")
            packet = assemble(groups, receipts,
                              selection_ids=args.get("fragment_ids"), context=context, budget=golden["policy"]["budget"])
            if name == "open_study":
                require(packet == golden, "First Packet differs from registered direct Generation baseline")
            state["packet"] = put(db, packet)
            result = self.packet_view(packet, address, 0, limit=60000)
            available = []
            for follow in archive.cases[case]["follow_ups"].values():
                if follow["query_id"] not in state["groups"] and set(follow["parent_query_ids"]) <= set(state["groups"]):
                    available.append({"query": follow["query_id"], "search_text": follow["search_text"],
                                      "gap": follow["trigger_gap"], "parent_queries": follow["parent_query_ids"],
                                      "evidence_handles": [address("source", h) for h in follow["parent_evidence_refs"]]})
            result.update(query_ids=state["groups"], first_packet_parity=(name == "open_study"),
                          available_queries=available)
            if name == "follow_query":
                result["checked_gap"] = args["gap"]
                result["frozen_query"] = {k: query.value()[k] for k in ("kind", "subquestion", "search_text",
                                                                       "rerank_text", "trigger_gap", "parent_query_ids")}
            return result
        if name == "browse_candidates":
            require(args["query"] in state["groups"], "Query outside study")
            group, handles = archive.group(case, args["query"])
            start = args.get("offset", 0)
            items = group["candidates"]
            group_index = state["groups"].index(args["query"])
            packet = load(db, state["packet"])
            dispositions = {d["chunk_id"]: d["disposition"] for d in packet["dispositions"] if d["group"] == group_index}
            require(start <= len(items), "Candidate cursor outside group")
            rows = []
            for item in items[start:start + 12]:
                raw = handles[item["chunk_id"]]
                source = item["source"]
                labels = source["source_context"]["label_evidence"]
                rows.append({"rank": item["reranking"]["final_rank"], "handle": address("source", raw),
                             "title": label(labels.get("document_title")), "section": label(labels.get("section_title")),
                             "source_item": source["source_item"], "read_scopes": state["scopes"].get(item["chunk_id"], []),
                             "packet_disposition": dispositions[item["chunk_id"]]})
            end = start + len(rows)
            return {"query": args["query"], "total": len(items), "rows": rows, "navigation_only": True,
                    "next_offset": end if end < len(items) else None}
        if name in {"read_source", "get_read"}:
            if name == "read_source":
                require(args["handle"] in candidates, "Source handle outside study")
                receipt = save_read(candidates[args["handle"]], args["scope"])
            else:
                receipt_key = next((ref for rid, ref in state["reads"].items()
                                    if address("read", rid) == args["read_ref"]), None)
                require(receipt_key is not None, "Foreign read receipt")
                receipt = load(db, receipt_key)
            fragment, = receipt["fragments"]
            return {"receipt_id": receipt["receipt_id"], "fragment_id": fragment["fragment_id"],
                    "read_ref": address("read", receipt["receipt_id"]),
                    "source_item": fragment["source_item"], "observation_id": fragment["observation_id"],
                    "read_extent": receipt["read_extent"], "unread": receipt["unread"],
                    "raw_byte_revalidation": "not_performed", "semantic_support": "NOT_EVALUATED",
                    "projection": page(fragment_content(fragment), args.get("offset", 0))}
        packet = load(db, state["packet"])
        require(args["packet_ref"] == address("packet", packet["packet_id"]), "Foreign or superseded Packet")
        if name == "get_packet":
            return self.packet_view(packet, address, args.get("offset", 0), citation=args.get("citation"))
        if name == "check_answer":
            _, ids, check = bind_answer(packet, args["answer"])
            sources = {}
            for i in ids:
                binding = packet["citations"][i]
                receipt = load(db, state["reads"][binding["receipt_id"]])
                fragment = next(f for f in receipt["fragments"] if f["fragment_id"] == binding["fragment_id"])
                sources[i] = {"source_item": fragment["source_item"], "observation_id": fragment["observation_id"],
                              "scope": receipt["scope"], "handle": address("source", receipt["handle"])}
            return {**check, "citations": {i: packet["citations"][i] for i in ids}, "sources": sources}
        raise ValueError("Unknown study action")

    @staticmethod
    def packet_view(packet, address, offset, *, limit=None, citation=None):
        validate_citations(packet, packet["citations"])
        require(citation is None or citation in packet["citations"], "Unknown Packet citation")
        entries = [e for e in packet["evidence"] if citation is None or e["citation_id"] == citation]
        return {"packet_id": packet["packet_id"], "packet_ref": address("packet", packet["packet_id"]),
                "status": packet["status"], "candidates_accounted": len(packet["dispositions"]),
                "receipts": len(packet["read_receipt_ids"]), "semantic_support": "NOT_EVALUATED",
                "citation_ids": list(packet["citations"]), "projection": page(entries, offset, limit)}
