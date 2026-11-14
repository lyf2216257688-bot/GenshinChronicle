"""Opt-in G4 local Mock chat CLI. No live mode or automatic startup recovery."""
import argparse
import json
from pathlib import Path

from ..retrieval.clean_based.source import InputReader
from .clean_conversation import ConversationStore
from .clean_conversation_graph import fixture_settings, run_turn, saved_result
from .clean_mock import FixturePipeline, fixture_inputs


def execute(store, tid, repo, *, resume=False):
    manifest = fixture_inputs(repo, store.root / "mock_corpus")
    with InputReader(manifest) as reader:
        return run_turn(store, tid, FixturePipeline(reader, repo), continue_turn=resume)


def demo(store, repo):
    sid = store.create_chat("G4 Mock连续对话", fixture_settings(), session="g4-demo")
    steps = [("提出问题：对白的条件是什么？", None, "normal"),
             ("追问：这个解释的原文依据在哪里？", "unresolved", "normal"),
             ("补充材料：我认为后续原句说明了因果。", "user_assertion", "limited"),
             ("纠正：撤回先前的因果前提，只讨论原文先后。", "correction_fact", "retract"),
             ("请依据纠正重新分析，保留未解事项。", "requirement", "source_conflict"),
             ("关闭后重开：继续查看这些原文条件。", None, "normal")]
    results = []
    for i, (text, kind, scenario) in enumerate(steps):
        targets = [n["note_id"] for n in store.notes(sid) if n["kind"] == "user_assertion"] if kind == "correction_fact" else []
        tid = store.enqueue(sid, "demo-" + str(i), text, note_kind=kind, targets=targets, scenario=scenario)
        results.append(execute(store, tid, repo))
        # A new object between turns models closing/reopening the local store.
        store = ConversationStore(store.root)
    other = store.create_chat("独立新对话", fixture_settings(), session="g4-isolated-demo")
    return {"mode": "MOCK", "session_id": sid, "turns": len(results), "statuses": [r["status"] for r in results],
            "history_messages": len(store.history(sid)), "isolated_messages": len(store.history(other)),
            "semantic_acceptance": "NOT_REVIEWED", "live_capability": "NOT_VALIDATED", "remote_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="data/generation/clean-v1-local")
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="action", required=True)
    new = sub.add_parser("new"); new.add_argument("--title", default="Mock研究对话"); new.add_argument("--session")
    sub.add_parser("list")
    show = sub.add_parser("show"); show.add_argument("--session", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--session", required=True); submit.add_argument("--submit-token", required=True)
    submit.add_argument("--question", required=True); submit.add_argument("--note-kind")
    submit.add_argument("--target", action="append", default=[])
    submit.add_argument("--scenario", default="normal"); submit.add_argument("--run", action="store_true")
    for action in ("continue", "cancel"):
        p = sub.add_parser(action); p.add_argument("--turn", required=True)
    sub.add_parser("demo")
    args = parser.parse_args(argv)
    store, repo = ConversationStore(args.root), Path(args.repo).resolve()
    if args.action == "new": result = {"session_id": store.create_chat(args.title, fixture_settings(), session=args.session)}
    elif args.action == "list": result = store.chats()
    elif args.action == "show":
        result = {"chat": store.chat(args.session), "history": store.history(args.session),
                  "notes": store.notes(args.session), "jobs": store.jobs(args.session)}
    elif args.action == "submit":
        tid = store.enqueue(args.session, args.submit_token, args.question, note_kind=args.note_kind,
                            targets=args.target, scenario=args.scenario)
        result = execute(store, tid, repo) if args.run else {"turn_id": tid, "status": store.job(tid)["status"]}
    elif args.action == "continue": result = execute(store, args.turn, repo, resume=True)
    elif args.action == "cancel":
        store.cancel(args.turn); result = execute(store, args.turn, repo, resume=True)
    else: result = demo(store, repo)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
