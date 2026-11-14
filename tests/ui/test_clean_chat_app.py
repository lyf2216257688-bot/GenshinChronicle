"""Streamlit AppTest interaction with the actual offline local executor."""
import os
from pathlib import Path
import shutil
import socket
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import streamlit as st

from genshin_corpus.rag.clean_conversation import ConversationStore
from genshin_corpus.rag.clean_conversation_graph import fixture_settings
from genshin_corpus.rag.clean_executor import LocalExecutor

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "src/genshin_corpus/ui/clean_chat_app.py"


class ChatAppTests(unittest.TestCase):
    def setUp(self):
        scratch = REPO / "tmp-g4-ui"; scratch.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=scratch)
        self.store = ConversationStore(self.temp.name)
        self.workers = []
        self.env = patch.dict(os.environ, {"GC_CLEAN_CHAT_ROOT": str(self.store.root), "LANGSMITH_TRACING": "false",
                                          "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"})
        self.env.start()
        self.factory = patch("genshin_corpus.rag.clean_executor.LocalExecutor", side_effect=self.worker)
        self.factory.start()
        connect = socket.socket.connect
        self.external_connections = []
        def guard(sock, address):
            # Windows asyncio/AppTest uses an actual loopback socketpair.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return connect(sock, address)
            self.external_connections.append(address)
            raise AssertionError("UI must not connect externally")
        self.network = patch.object(socket.socket, "connect", guard)
        self.network.start()

    def tearDown(self):
        for worker in self.workers: worker.close()
        st.cache_resource.clear()
        self.factory.stop(); self.env.stop(); self.network.stop()
        self.assertEqual(self.external_connections, [])
        shutil.rmtree(self.store.root)
        self.temp.cleanup()

    def worker(self, root, repo):
        result = LocalExecutor(root, repo, delay=.04)
        self.workers.append(result)
        return result

    def app(self):
        app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assertFalse(app.exception)
        return app

    def button(self, app, label):
        return next(b for b in app.button if b.label == label)

    def new(self, app):
        self.button(app, "新建对话").click().run()
        self.assertFalse(app.exception)
        return self.store.chats()[0]["id"]

    def send(self, app, text):
        app.text_area[0].set_value(text).run()
        self.button(app, "发送").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(self.workers[0].wait_idle(15))
        app.run()
        self.assertFalse(app.exception)

    def test_send_citation_parent_refresh_and_reopen(self):
        app = self.app(); sid = self.new(app)
        self.send(app, "请查看原文的条件")
        self.assertEqual(len(app.chat_message), 2)
        self.assertTrue(any(e.label == "Parent原文与结构" for e in app.expander))
        self.button(app, "发送").click().run()
        self.assertTrue(self.workers[0].wait_idle(10))
        self.assertEqual(len(self.store.jobs(sid)), 1)
        reopened = AppTest.from_file(str(APP), default_timeout=15)
        reopened.query_params["chat"] = sid
        reopened.run()
        self.assertFalse(reopened.exception)
        self.assertEqual(len(reopened.chat_message), 2)
        self.assertEqual(len(self.store.jobs(sid)), 1)

    def test_live_ui_requires_finite_prepared_campaign(self):
        with patch.dict(os.environ, {"GC_CLEAN_CHAT_MODE": "LIVE"}):
            app = self.app()
            self.button(app, "新建对话").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertEqual(self.store.chats(), [])

    def test_typed_correction_and_new_chat_isolation(self):
        app = self.app(); sid = self.new(app)
        next(s for s in app.selectbox if s.label == "这条消息的类型").select("用户剧情断言（待核验）").run()
        self.send(app, "我认为资料证明了因果")
        next(s for s in app.selectbox if s.label == "这条消息的类型").select("纠正剧情前提（待核验）").run()
        self.send(app, "撤回这个前提，请重新核对")
        self.assertEqual([n["status"] for n in self.store.notes(sid)], ["needs_review", "active"])
        other = self.new(app)
        self.assertNotEqual(other, sid)
        self.assertEqual(len(app.chat_message), 0)
        self.assertEqual(app.text_area[0].value, "")

    def test_reopen_only_displays_queued_work_until_continue(self):
        sid = self.store.create_chat("Saved", fixture_settings())
        tid = self.store.enqueue(sid, "stable", "此前保存的提问")
        app = self.app()
        self.assertEqual(self.store.job(tid)["status"], "queued")
        self.assertFalse(self.workers[0].pending)
        self.button(app, "继续本轮").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(self.workers[0].wait_idle(15))
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message), 2)

    def test_ui_cancel_retains_nonfinal_draft(self):
        app = self.app(); sid = self.new(app)
        app.text_area[0].set_value("取消这个Mock回答").run()
        self.button(app, "发送").click().run()
        tid = self.store.jobs(sid)[0]["turn_id"]
        end = time.monotonic() + 10
        while not any(e["kind"] == "answer_draft" for e in self.store.events(tid)) and time.monotonic() < end:
            time.sleep(.01)
        app.run()
        self.button(app, "停止本轮").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(self.workers[0].wait_idle(15))
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.job(tid)["status"], "cancelled")
        self.assertTrue(any(e.label == "未完成草稿 · 不是最终答案" for e in app.expander))


if __name__ == "__main__": unittest.main()
