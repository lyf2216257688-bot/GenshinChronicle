import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from genshin_corpus.collector.__main__ import main as collector_main
from genshin_corpus.collector.collector import Collector
from genshin_corpus.collector.config import CollectorConfig
from genshin_corpus.collector.storage import RunStore, atomic_write, write_json
from genshin_corpus.collector.transport import HttpTransport, RequestError, Response


class FakeTransport:
    def __init__(self, fixture_dir):
        self.fixture_dir = Path(fixture_dir)
        self.calls = []

    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        headers = dict(headers)
        self.calls.append((url, params, headers))
        if url.endswith("/home/map"):
            name = "map.json"
        elif url.endswith("/home/content/list"):
            name = f"listing-{params['channel_id']}.json"
        elif url.endswith("/entry_page"):
            name = f"detail-{params['entry_page_id']}.json"
        else:
            raise AssertionError(url)
        return Response(200, (self.fixture_dir / name).read_bytes(), {}, url)


class FailingDetailTransport(FakeTransport):
    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        if url.endswith("/entry_page") and params["entry_page_id"] == "509653":
            self.calls.append((url, params, dict(headers)))
            raise RequestError("HTTP 403", status=403)
        return super().get(url, params=params, headers=headers)


class AnomalyTransport:
    def __init__(self):
        self.calls = []

    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        headers = dict(headers)
        self.calls.append((url, params, headers))
        if url.endswith("/home/map"):
            body = json.dumps({"data": {"list": [{"channel_id": 43}]}}).encode("utf-8")
            return Response(200, body, {}, url)
        if url.endswith("/home/content/list"):
            body = json.dumps({"data": {"list": [{"title": "missing"}, {"content_id": 501157}]}}).encode("utf-8")
            return Response(200, body, {}, url)
        if url.endswith("/entry_page"):
            body = (Path(__file__).parents[1] / "fixtures" / "mihoyo_obc" / "detail-501157.json").read_bytes()
            return Response(200, body, {}, url)
        raise AssertionError(url)


class HostileTransport:
    def __init__(self, *, channel_id="43", content_id="501157"):
        self.channel_id = channel_id
        self.content_id = content_id
        self.calls = []

    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        self.calls.append((url, params, dict(headers)))
        if url.endswith("/home/map"):
            body = json.dumps({"data": {"list": [{"channel_id": self.channel_id}]}}).encode("utf-8")
        elif url.endswith("/home/content/list"):
            body = json.dumps({"data": {"list": [{"content_id": self.content_id}]}}).encode("utf-8")
        elif url.endswith("/entry_page"):
            body = b'{"hostile":true}'
        else:
            raise AssertionError(url)
        return Response(200, body, {}, url)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).parents[1] / "fixtures" / "mihoyo_obc"

    def test_offline_run_deduplicates_memberships_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = Collector(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport=transport).run()
            self.assertEqual(manifest["inventory_count"], 2)
            self.assertEqual(manifest["listing_records_discovered"], 3)
            self.assertEqual(manifest["cross_channel_duplicate_memberships"], 1)
            self.assertEqual(manifest["successful_detail_fetches"], 2)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len([call for call in transport.calls if call[0].endswith("/entry_page")]), 2)
            raw = RunStore(Path(directory), "mihoyo_obc", "zh-cn", "run-1").response_paths("details", "501157")[0]
            meta = raw.with_suffix(".meta.json")
            self.assertEqual(raw.read_bytes(), (self.fixtures / "detail-501157.json").read_bytes())
            self.assertTrue(meta.exists())
            self.assertNotEqual(raw.read_bytes(), meta.read_bytes())
            inventory = json.loads((Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "metadata" / "inventory.json").read_text())
            memberships = {item["content_id"]: item["channels"] for item in inventory}
            self.assertEqual(memberships["501157"], ["43", "25"])

    def test_same_run_resume_skips_saved_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            config = CollectorConfig(output_root=Path(directory), run_id="run-1")
            Collector(config, transport=FakeTransport(self.fixtures)).run()
            transport = FakeTransport(self.fixtures)
            Collector(config, transport=transport).run()
            self.assertEqual(transport.calls, [])

    def test_same_run_resume_rejects_missing_or_corrupt_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            config = CollectorConfig(output_root=Path(directory), run_id="run-1")
            Collector(config, transport=FakeTransport(self.fixtures)).run()
            raw = RunStore(Path(directory), "mihoyo_obc", "zh-cn", "run-1").response_paths("details", "501157")[0]
            meta = raw.with_suffix(".meta.json")

            raw.write_bytes(b"broken")
            transport = FakeTransport(self.fixtures)
            Collector(config, transport=transport).run()
            self.assertGreater(len(transport.calls), 0)

            meta_data = json.loads(meta.read_text())
            meta_data["sha256"] = "0" * 64
            write_json(meta, meta_data)
            transport = FakeTransport(self.fixtures)
            Collector(config, transport=transport).run()
            self.assertGreater(len(transport.calls), 0)

            meta_data = json.loads(meta.read_text())
            meta_data["status"] = 403
            write_json(meta, meta_data)
            transport = FakeTransport(self.fixtures)
            Collector(config, transport=transport).run()
            self.assertGreater(len(transport.calls), 0)

    def test_new_run_does_not_reuse_old_run_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Collector(CollectorConfig(output_root=root, run_id="run-1"), transport=FakeTransport(self.fixtures)).run()
            transport = FakeTransport(self.fixtures)
            Collector(CollectorConfig(output_root=root, run_id="run-2"), transport=transport).run()
            self.assertGreater(len(transport.calls), 0)

    def test_missing_content_id_records_anomaly(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Collector(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport=AnomalyTransport()).run()
            self.assertEqual(manifest["inventory_count"], 1)
            self.assertEqual(manifest["successful_detail_fetches"], 1)
            self.assertTrue(any(item["kind"] == "listing_item_anomaly" for item in manifest["failures"]))
            failure_log = Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "failures.jsonl"
            self.assertIn("listing_item_anomaly", failure_log.read_text())
            self.assertIn("missing content_id", failure_log.read_text())

    def test_atomic_write_produces_complete_file_before_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out.bin"
            observed = {}
            original_replace = atomic_write.__globals__["os"].replace

            def replace(src, dst):
                observed["src"] = src
                observed["dst"] = dst
                self.assertTrue(Path(src).exists())
                self.assertFalse(Path(dst).exists())
                self.assertEqual(Path(src).read_bytes(), b"payload")
                return original_replace(src, dst)

            with mock.patch("genshin_corpus.collector.storage.os.replace", side_effect=replace):
                atomic_write(target, b"payload")

            self.assertEqual(target.read_bytes(), b"payload")
            self.assertEqual(Path(observed["dst"]), target)

    def test_cli_defaults_do_not_fetch_details(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("genshin_corpus.collector.__main__.Collector") as collector_cls:
                instance = collector_cls.return_value
                instance.run.return_value = {}
                collector_main(["--output-root", directory, "--run-id", "run-1"])
                collector_cls.assert_called_once()
                self.assertEqual(collector_cls.call_args.args[0].output_root, Path(directory))
                instance.run.assert_called_once_with(fetch_details=False)

    def test_listing_anomaly_keeps_valid_items_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Collector(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport=AnomalyTransport()).run()
            self.assertEqual(manifest["inventory_count"], 1)
            self.assertGreaterEqual(len(manifest["unknown_unhandled_structures"]), 1)
            self.assertEqual(manifest["successful_detail_fetches"], 1)

    def test_untrusted_content_ids_are_safe_storage_keys_and_remain_auditable(self):
        malicious_ids = [
            "../outside-content",
            "nested/../../outside-content",
            "/absolute/content",
            r"C:\absolute\content",
            r"\\server\share\content",
        ]
        for content_id in malicious_ids:
            with self.subTest(content_id=content_id), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = Collector(
                    CollectorConfig(output_root=root, run_id="run-1"),
                    transport=HostileTransport(content_id=content_id),
                ).run()
                self.assertEqual(manifest["successful_detail_fetches"], 1)
                store = RunStore(root, "mihoyo_obc", "zh-cn", "run-1")
                raw, meta = store.response_paths("details", content_id)
                self.assertTrue(raw.is_relative_to(store.responses.resolve()))
                self.assertTrue(raw.exists())
                self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["key"], content_id)
                self.assertFalse((root / "outside-content.json").exists())

    def test_untrusted_channel_ids_are_safe_storage_keys_and_remain_auditable(self):
        malicious_ids = [
            "../outside-channel",
            "nested/../../outside-channel",
            "/absolute/channel",
            r"C:\absolute\channel",
            r"\\server\share\channel",
        ]
        for channel_id in malicious_ids:
            with self.subTest(channel_id=channel_id), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = Collector(
                    CollectorConfig(output_root=root, run_id="run-1"),
                    transport=HostileTransport(channel_id=channel_id),
                ).run(fetch_details=False)
                self.assertEqual(manifest["listing_responses_saved"], 1)
                store = RunStore(root, "mihoyo_obc", "zh-cn", "run-1")
                raw, meta = store.response_paths("listings", channel_id)
                self.assertTrue(raw.is_relative_to(store.responses.resolve()))
                self.assertTrue(raw.exists())
                self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["key"], channel_id)
                self.assertFalse((root / "outside-channel.json").exists())

    def test_failure_is_recorded_and_detail_header_is_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FailingDetailTransport(self.fixtures)
            manifest = Collector(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport=transport).run()
            self.assertEqual(manifest["failed_detail_fetches"], 1)
            self.assertEqual(manifest["failures"][0]["status"], 403)
            detail_headers = [call[2] for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertTrue(any(headers.get("x-rpc-wiki_app") == "genshin" for headers in detail_headers))
            failures = Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "failures.jsonl"
            self.assertIn('"status": 403', failures.read_text())

    def test_429_retry_after_and_5xx_backoff_are_bounded(self):
        transport = HttpTransport(max_retries=2, backoff_base=0.5)

        class FakeResponse:
            def __init__(self, status=200, body=b"{}", headers=None, url="https://example.test"):
                self.status = status
                self._body = body
                self.headers = headers or {}
                self.url = url

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        retry_after = urllib.error.HTTPError("https://example.test/api", 429, "Too Many Requests",
                                             {"Retry-After": "7"}, io.BytesIO(b""))
        server_error = urllib.error.HTTPError("https://example.test/api", 502, "Bad Gateway", {}, io.BytesIO(b""))

        with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
            with mock.patch("genshin_corpus.collector.transport.random.uniform", return_value=0.0):
                with mock.patch(
                    "genshin_corpus.collector.transport.urllib.request.urlopen",
                    side_effect=[retry_after, FakeResponse()],
                ) as open_mock:
                    response = transport.get("https://example.test/api")
        self.assertEqual(response.status, 200)
        self.assertEqual(open_mock.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(sleep.call_args[0][0], 7.0)

        with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
            with mock.patch("genshin_corpus.collector.transport.random.uniform", return_value=0.0):
                with mock.patch(
                    "genshin_corpus.collector.transport.urllib.request.urlopen",
                    side_effect=[server_error, server_error, FakeResponse()],
                ) as open_mock:
                    response = transport.get("https://example.test/api")
        self.assertEqual(response.status, 200)
        self.assertEqual(open_mock.call_count, 3)
        self.assertEqual([round(call.args[0], 3) for call in sleep.call_args_list], [0.5, 1.0])

        network_error = urllib.error.URLError("temporary")
        transport = HttpTransport(max_retries=2, backoff_base=0.5, max_retry_delay=1.0)
        with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
            with mock.patch("genshin_corpus.collector.transport.random.uniform", return_value=0.0):
                with mock.patch(
                    "genshin_corpus.collector.transport.urllib.request.urlopen",
                    side_effect=[network_error, network_error, FakeResponse()],
                ) as open_mock:
                    response = transport.get("https://example.test/api")
        self.assertEqual(response.status, 200)
        self.assertEqual(open_mock.call_count, 3)
        self.assertEqual([round(call.args[0], 3) for call in sleep.call_args_list], [0.5, 1.0])

        with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
            with mock.patch("genshin_corpus.collector.transport.random.uniform", return_value=0.0):
                with mock.patch(
                    "genshin_corpus.collector.transport.urllib.request.urlopen",
                    side_effect=[urllib.error.HTTPError(
                        "https://example.test/api", 429, "Too Many Requests",
                        {"Retry-After": "9999"}, io.BytesIO(b"")
                    ), FakeResponse()],
                ):
                    transport = HttpTransport(max_retries=1, backoff_base=0.5, max_retry_delay=2.0)
                    transport.get("https://example.test/api")
        self.assertEqual(sleep.call_args.args[0], 2.0)


if __name__ == "__main__":
    unittest.main()
