"""Small sealed Mock turns for product/API and Edge citation acceptance."""
from pathlib import Path
import sys

from genshin_corpus.local_chat import batches
from genshin_corpus.local_chat.contract import Draft, Grant
from genshin_corpus.local_chat.store import ProductStore
from genshin_corpus.rag.clean_conversation_graph import run_turn
from genshin_corpus.rag.clean_mock import FixturePipeline, MockOpener, fixture_inputs
from genshin_corpus.retrieval.clean_based.source import InputReader


def citation_cases(store, repo, sid):
    examples = (
        ("查看原文编号", "圆括号来源（E01）"),
        ("复查证据包编号", "其他包的编号[E99]"),
        ("核查身份推断", "以此断言两人身份相同[E01]"),
    )
    tids = []
    with InputReader(fixture_inputs(repo, store.root / "fixture")) as reader:
        for question, answer in examples:
            draft = Draft(**{**store.get_draft(sid), "text": question})
            tid, _ = store.submit_draft(sid, draft, lambda mode: batches.available(store, mode))
            binding = store.binding(store.input_for(tid)["batch_id"])
            assert binding["mode"] == "MOCK"
            view = batches.ExecutionView(store, binding, tid)
            run_turn(view, tid, FixturePipeline(reader, repo), opener_factory=lambda *args: MockOpener(answer))
            tids.append(tid)
    return tids


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[2]
    root = Path(sys.argv[1]).resolve()
    if root.parent != repo / ".local/tmp/browser-chat" or not root.name.startswith("case-"):
        raise SystemExit("An isolated browser test directory is required.")
    store = ProductStore(root)
    grant = batches.latest(store, "MOCK")
    if not grant or batches.status(store, grant)["state"] != "open":
        batches.open_batch(store, repo, Grant(mode="MOCK", requests=8, minutes=10, confirm_live=False))
    sid = store.new_chat("MOCK", "引用复查")["id"]
    citation_cases(store, repo, sid)
    print(sid)
