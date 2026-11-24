from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from genshin_corpus.collector.__main__ import main as collector_main
from genshin_corpus.collector.access import AccessPaused, SourceAccess
from genshin_corpus.collector.collector import Collector
from genshin_corpus.collector.config import DEFAULT_BASE_URL, CollectorConfig
from genshin_corpus.collector.contract import RawIntegrityError
from genshin_corpus.collector.storage import atomic_write, write_json
from genshin_corpus.collector.transport import HttpTransport, Response


def channel(key, *, rows=(), children=(), **fields):
    return {"id": key, "name": str(key), "parent_id": 0,
            "list": list(rows), "children": list(children), **fields}


def discovery(*nodes):
    return json.dumps({"retcode": 0, "data": {"list": nodes}}).encode("utf-8")


def detail(key):
    return json.dumps({"retcode": 0, "data": {"page": {"id": key, "name": str(key), "modules": []}}}).encode("utf-8")


def attempt_paths(root, kind):
    return sorted((root / "responses" / kind).glob("*/*/response.meta.json"))


class FakeTransport:
    def __init__(self, fixture_dir, *, map_name="map.json", listing_names=None):
        self.fixture_dir = Path(fixture_dir)
        self.map_name = map_name
        self.listing_names = listing_names or {}
        self.calls = []

    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        headers = dict(headers)
        self.calls.append((url, params, headers))
        if url.endswith("/home/map"):
            name = self.map_name
        elif url.endswith("/home/content/list"):
            name = self.listing_names.get(params["channel_id"], f"listing-{params['channel_id']}.json")
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
            return Response(403, b"access denied", {}, url)
        return super().get(url, params=params, headers=headers)


class AnomalyTransport:
    def __init__(self):
        self.calls = []

    def get(self, url, *, params=(), headers=()):
        params = dict(params)
        headers = dict(headers)
        self.calls.append((url, params, headers))
        if url.endswith("/home/map"):
            body = discovery(channel(43))
            return Response(200, body, {}, url)
        if url.endswith("/home/content/list"):
            body = discovery(channel(43, rows=[{"title": "missing", "unknown": "kept"}, {"content_id": 501157}]))
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
            body = discovery(channel(self.channel_id))
        elif url.endswith("/home/content/list"):
            body = discovery(channel(self.channel_id, rows=[{"content_id": self.content_id}]))
        elif url.endswith("/entry_page"):
            body = detail(self.content_id)
        else:
            raise AssertionError(url)
        return Response(200, body, {}, url)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).parents[1] / "fixtures" / "mihoyo_obc"
        access_temp = tempfile.TemporaryDirectory()
        self.addCleanup(access_temp.cleanup)
        self.access_root = Path(access_temp.name)
        self.access = SourceAccess(self.access_root)

    def collect(self, config, transport):
        return Collector(config, transport=transport, access=self.access)

    def cli(self, args, transport):
        output = io.StringIO()
        with redirect_stdout(output):
            code = collector_main(args, transport=transport, access=self.access)
        return code, json.loads(output.getvalue())

    def test_offline_run_deduplicates_memberships_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport)
            manifest = collector.run()
            self.assertEqual(manifest["inventory_count"], 2)
            self.assertEqual(manifest["listing_records_discovered"], 3)
            self.assertEqual(manifest["cross_channel_duplicate_memberships"], 1)
            self.assertEqual(manifest["successful_detail_fetches"], 2)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len([call for call in transport.calls if call[0].endswith("/entry_page")]), 2)
            entry = manifest["paths"]["details:501157"]
            raw, meta = (collector.store.root / entry[field] for field in ("raw", "metadata"))
            self.assertEqual(raw.read_bytes(), (self.fixtures / "detail-501157.json").read_bytes())
            self.assertTrue(meta.exists())
            self.assertNotEqual(raw.read_bytes(), meta.read_bytes())
            inventory = json.loads((Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "metadata" / "inventory.json").read_text())
            memberships = {item["content_id"]: item["channels"] for item in inventory}
            self.assertEqual(memberships["501157"], ["43", "25"])

    def test_same_run_resume_skips_saved_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            config = CollectorConfig(output_root=Path(directory), run_id="run-1")
            collector = self.collect(config, FakeTransport(self.fixtures))
            first = collector.run()
            original = {path: (path.stat().st_mtime_ns, path.read_bytes())
                        for path in collector.store.root.rglob("*") if path.is_file()}
            transport = FakeTransport(self.fixtures)
            self.assertEqual(self.collect(config, transport).run(), first)
            self.assertEqual(transport.calls, [])
            self.assertEqual({path: (path.stat().st_mtime_ns, path.read_bytes())
                              for path in collector.store.root.rglob("*") if path.is_file()}, original)

    def test_same_run_resume_rejects_missing_or_corrupt_evidence(self):
        for damage in ("missing_metadata", "invalid_metadata", "corrupt_body", "corrupt_reference"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                config = CollectorConfig(output_root=Path(directory), run_id="run-1")
                collector = self.collect(config, FakeTransport(self.fixtures))
                manifest = collector.run()
                entry = manifest["paths"]["details:501157"]
                raw, meta = (collector.store.root / entry[field] for field in ("raw", "metadata"))
                if damage == "missing_metadata":
                    meta.unlink()
                elif damage == "invalid_metadata":
                    meta.write_bytes(b"{")
                elif damage == "corrupt_body":
                    raw.write_bytes(b"broken")
                else:
                    (raw.parent.parent / "accepted.json").write_bytes(b'{"attempt": 200}')
                original = collector.store.manifest_path.read_bytes()
                body = raw.read_bytes()
                transport = FakeTransport(self.fixtures)
                with self.assertRaises(RawIntegrityError):
                    self.collect(config, transport).run()
                self.assertEqual(transport.calls, [])
                self.assertEqual(raw.read_bytes(), body)
                self.assertEqual(collector.store.manifest_path.read_bytes(), original)

    def test_new_run_does_not_reuse_old_run_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.collect(CollectorConfig(output_root=root, run_id="run-1"), FakeTransport(self.fixtures)).run()
            transport = FakeTransport(self.fixtures)
            self.collect(CollectorConfig(output_root=root, run_id="run-2"), transport).run()
            self.assertGreater(len(transport.calls), 0)

    def test_missing_content_id_records_anomaly(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), AnomalyTransport())
            manifest = collector.run()
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["successful_detail_fetches"], 0)
            metadata, = attempt_paths(collector.store.root, "listings")
            self.assertIn("missing content_id", json.loads(metadata.read_bytes())["error"])

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

    def test_atomic_write_retries_transient_replace_permission_error(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out.bin"
            replace = mock.Mock(side_effect=[PermissionError(5, "sharing conflict"),
                                              PermissionError(5, "sharing conflict"),
                                              None])
            with mock.patch("genshin_corpus.collector.storage.os.replace", replace), \
                    mock.patch("genshin_corpus.collector.storage.time.sleep") as sleep:
                atomic_write(target, b"payload")
            self.assertEqual(replace.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.01, 0.02])
            self.assertFalse(any(target.parent.glob(f".{target.name}.*")))

    def test_atomic_write_raises_after_bounded_replace_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "out.bin"
            replace = mock.Mock(side_effect=PermissionError(5, "persistent sharing conflict"))
            with mock.patch("genshin_corpus.collector.storage.os.replace", replace), \
                    mock.patch("genshin_corpus.collector.storage.time.sleep") as sleep:
                with self.assertRaises(PermissionError):
                    atomic_write(target, b"payload")
            self.assertEqual(replace.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertFalse(any(target.parent.glob(f".{target.name}.*")))

    def test_cli_defaults_do_not_fetch_details(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            code, summary = self.cli(["--output-root", directory, "--run-id", "run-1"], transport)
            self.assertEqual((code, summary["status"]), (0, "scoped_complete"))
            self.assertEqual(summary["request_scope"]["base_url"], DEFAULT_BASE_URL)
            self.assertFalse(summary["request_scope"]["fetch_details"])
            self.assertFalse(any(url.endswith("/entry_page") for url, _, _ in transport.calls))

    def test_listing_pagination_signal_is_saved_and_blocks_followup_requests(self):
        class PaginationTransport:
            def __init__(self):
                self.calls = []

            def get(self, url, *, params=(), headers=()):
                params = dict(params)
                self.calls.append((url, params, dict(headers)))
                if url.endswith("/home/map"):
                    body = discovery(channel(43), channel(25))
                elif url.endswith("/home/content/list"):
                    body = discovery(channel(43, rows=[{"content_id": 501157}], next_cursor="cursor-2"))
                else:
                    raise AssertionError(url)
                return Response(200, body, {}, url)

        with tempfile.TemporaryDirectory() as directory:
            transport = PaginationTransport()
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport)
            manifest = collector.run()
            listing_calls = [call for call in transport.calls if call[0].endswith("/home/content/list")]
            detail_calls = [call for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertEqual([call[1]["channel_id"] for call in listing_calls], ["43"])
            self.assertEqual(detail_calls, [])
            self.assertEqual(manifest["status"], "partial")
            self.assertIn("root.data.list[0].next_cursor", manifest["failures"][0]["error"])
            self.assertEqual(manifest["channels"][0]["pagination"], "pagination_signal_detected")
            self.assertEqual(manifest["listing_responses_saved"], 0)
            metadata, = attempt_paths(collector.store.root, "listings")
            self.assertTrue(metadata.with_name("response.json").exists())

    def test_listing_content_record_pagination_fields_do_not_trigger_guard(self):
        class ContentFieldTransport:
            def __init__(self):
                self.calls = []

            def get(self, url, *, params=(), headers=()):
                params = dict(params)
                self.calls.append((url, params, dict(headers)))
                if url.endswith("/home/map"):
                    body = discovery(channel(43))
                elif url.endswith("/home/content/list"):
                    body = discovery(channel(43, rows=[{"content_id": 501157, "page": 1, "next": "business-value"}]))
                else:
                    raise AssertionError(url)
                return Response(200, body, {}, url)

        with tempfile.TemporaryDirectory() as directory:
            transport = ContentFieldTransport()
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                fetch_details=False
            )
            self.assertEqual(manifest["status"], "scoped_complete")
            self.assertEqual(manifest["listing_responses_saved"], 1)
            self.assertEqual(manifest["failures"], [])

    def test_listing_channel_filter_selects_only_requested_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                fetch_details=False, channel_ids=["43"]
            )
            listing_calls = [call for call in transport.calls if call[0].endswith("/home/content/list")]
            self.assertEqual([call[1]["channel_id"] for call in listing_calls], ["43"])
            self.assertEqual(manifest["listing_responses_saved"], 1)
            self.assertEqual(manifest["inventory_count"], 2)

    def test_live_map_id_nodes_and_children_feed_channel_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures, map_name="map-live-id.json")
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                fetch_details=False, channel_ids=["43"]
            )
            listing_calls = [call for call in transport.calls if call[0].endswith("/home/content/list")]
            self.assertEqual([call[1]["channel_id"] for call in listing_calls], ["43"])
            self.assertEqual(manifest["recognized_channel_count"], 2)
            self.assertEqual(manifest["listing_responses_saved"], 1)

    def test_live_listing_wrapper_is_not_reported_as_missing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(
                self.fixtures,
                listing_names={"43": "listing-live-wrapper-43.json"},
            )
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                fetch_details=False, channel_ids=["43"]
            )
            self.assertEqual(manifest["listing_records_discovered"], 2)
            self.assertEqual(manifest["failures"], [])
            self.assertEqual(manifest["channels"][0]["pagination"], "single_response_verified")

    def test_map_without_recognizable_channels_is_partial(self):
        class UnrecognizedMapTransport:
            def __init__(self):
                self.calls = []

            def get(self, url, *, params=(), headers=()):
                self.calls.append((url, dict(params), dict(headers)))
                return Response(200, b'{"retcode":0,"message":"OK","data":{"list":[{"name":"unknown"}]}}', {}, url)

        with tempfile.TemporaryDirectory() as directory:
            transport = UnrecognizedMapTransport()
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport)
            manifest = collector.run(
                fetch_details=False
            )
            self.assertEqual(manifest["recognized_channel_count"], 0)
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["failures"][0]["kind"], "map")
            metadata, = attempt_paths(collector.store.root, "map")
            self.assertIn("channel node identity", json.loads(metadata.read_bytes())["error"])

    def test_listing_without_filter_keeps_all_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                fetch_details=False
            )
            listing_calls = [call for call in transport.calls if call[0].endswith("/home/content/list")]
            self.assertEqual({call[1]["channel_id"] for call in listing_calls}, {"43", "25", "233", "81"})
            self.assertEqual(manifest["listing_responses_saved"], 4)

    def test_detail_limit_preserves_full_inventory_and_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                detail_limit=1
            )
            detail_calls = [call for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertEqual(len(detail_calls), 1)
            self.assertEqual(manifest["unique_detail_responses_expected"], 2)
            self.assertEqual(manifest["successful_detail_fetches"], 1)
            self.assertEqual(manifest["status"], "scoped_complete")
            inventory_path = Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "metadata" / "inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual({item["content_id"] for item in inventory}, {"501157", "509653"})
            self.assertEqual({item["content_id"]: item["channels"] for item in inventory}["501157"], ["43", "25"])

    def test_detail_limit_none_keeps_all_detail_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run()
            detail_calls = [call for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertEqual(len(detail_calls), 2)
            self.assertEqual(manifest["successful_detail_fetches"], 2)

    def test_detail_limit_twenty_caps_requests_without_truncating_inventory(self):
        class LargeInventoryTransport:
            def __init__(self):
                self.calls = []

            def get(self, url, *, params=(), headers=()):
                params = dict(params)
                self.calls.append((url, params, dict(headers)))
                if url.endswith("/home/map"):
                    body = discovery(channel(43))
                elif url.endswith("/home/content/list"):
                    items = [{"content_id": f"content-{index:02d}"} for index in range(25)]
                    body = discovery(channel(43, rows=items))
                elif url.endswith("/entry_page"):
                    body = detail(params["entry_page_id"])
                else:
                    raise AssertionError(url)
                return Response(200, body, {}, url)

        with tempfile.TemporaryDirectory() as directory:
            transport = LargeInventoryTransport()
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                detail_limit=20
            )
            detail_calls = [call for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertEqual(len(detail_calls), 20)
            self.assertEqual(manifest["unique_detail_responses_expected"], 25)
            self.assertEqual(manifest["successful_detail_fetches"], 20)
            inventory_path = Path(directory) / "mihoyo_obc" / "zh-cn" / "run-1" / "metadata" / "inventory.json"
            self.assertEqual(len(json.loads(inventory_path.read_text(encoding="utf-8"))), 25)

    def test_detail_limit_round_robins_selected_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            manifest = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport).run(
                detail_limit=1
            )
            self.assertEqual(manifest["detail_selection"]["strategy"], "channel_round_robin")
            self.assertEqual(manifest["detail_selection"]["limit"], 1)
            self.assertEqual(len(manifest["detail_selection"]["selected_content_ids"]), 1)

    def test_cli_staged_options_are_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FakeTransport(self.fixtures)
            code, summary = self.cli(["--output-root", directory, "--run-id", "run-1", "--details",
                                      "--channel-id", "43", "--channel-id", "25", "--detail-limit", "20"], transport)
            self.assertEqual((code, summary["status"]), (0, "scoped_complete"))
            self.assertEqual(summary["request_scope"]["channel_ids"], ["25", "43"])
            self.assertEqual(summary["request_scope"]["detail_limit"], 20)
            self.assertEqual(summary["successful_detail_fetches"], 2)

    def test_listing_anomaly_retains_raw_bytes_without_promoting_partial_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), AnomalyTransport())
            manifest = collector.run()
            metadata, = attempt_paths(collector.store.root, "listings")
            rows = json.loads(metadata.with_name("response.json").read_bytes())["data"]["list"][0]["list"]
            self.assertEqual(rows[0]["unknown"], "kept")
            self.assertEqual(rows[1]["content_id"], 501157)
            self.assertEqual(manifest["successful_detail_fetches"], 0)
            self.assertFalse(collector.store.inventory_path.exists())

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
                collector = self.collect(
                    CollectorConfig(output_root=root, run_id="run-1"),
                    HostileTransport(content_id=content_id),
                )
                manifest = collector.run()
                self.assertEqual(manifest["successful_detail_fetches"], 1)
                entry = manifest["paths"][f"details:{content_id}"]
                raw, meta = (collector.store.root / entry[field] for field in ("raw", "metadata"))
                self.assertTrue(raw.is_relative_to(collector.store.root / "responses/details"))
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
                collector = self.collect(
                    CollectorConfig(output_root=root, run_id="run-1"),
                    HostileTransport(channel_id=channel_id),
                )
                manifest = collector.run(fetch_details=False)
                self.assertEqual(manifest["listing_responses_saved"], 1)
                entry = manifest["paths"][f"listings:{channel_id}"]
                raw, meta = (collector.store.root / entry[field] for field in ("raw", "metadata"))
                self.assertTrue(raw.is_relative_to(collector.store.root / "responses/listings"))
                self.assertTrue(raw.exists())
                self.assertEqual(json.loads(meta.read_text(encoding="utf-8"))["key"], channel_id)
                self.assertFalse((root / "outside-channel.json").exists())

    def test_failure_is_recorded_and_detail_header_is_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = FailingDetailTransport(self.fixtures)
            collector = self.collect(CollectorConfig(output_root=Path(directory), run_id="run-1"), transport)
            manifest = collector.run()
            self.assertEqual(manifest["failed_detail_fetches"], 1)
            self.assertEqual(manifest["failures"][0]["status"], 403)
            detail_headers = [call[2] for call in transport.calls if call[0].endswith("/entry_page")]
            self.assertTrue(any(headers.get("x-rpc-wiki_app") == "genshin" for headers in detail_headers))
            receipts = attempt_paths(collector.store.root, "details")
            denied, = (path for path in receipts if json.loads(path.read_bytes())["status"] == 403)
            self.assertEqual(denied.with_name("response.json").read_bytes(), b"access denied")

    def test_429_retry_after_and_5xx_backoff_are_bounded(self):
        clock = mock.Mock(return_value=1000.0)
        access = SourceAccess(self.access_root, clock=clock)
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

        def http_error(code, headers=None):
            return urllib.error.HTTPError("https://example.test/api", code, "error", headers or {}, io.BytesIO(b""))

        def advance(wait):
            clock.return_value += wait

        for responses, waits in (
            ([http_error(429, {"Retry-After": "7"}), FakeResponse()], [7.0]),
            ([http_error(502), http_error(502), FakeResponse()], [0.5, 1.0]),
            ([urllib.error.URLError("temporary"), urllib.error.URLError("temporary"), FakeResponse()], [0.5, 1.0]),
        ):
            with (
                mock.patch.object(transport.opener, "open", side_effect=responses) as open_mock,
                mock.patch("genshin_corpus.collector.transport.time.sleep", side_effect=advance) as sleep,
                mock.patch("genshin_corpus.collector.transport.random.uniform", return_value=0.0),
            ):
                response = transport.get("https://example.test/api", access=access)
            self.assertEqual(response.status, 200)
            self.assertEqual(open_mock.call_count, len(responses))
            self.assertEqual([call.args[0] for call in sleep.call_args_list], waits)

        with (
            mock.patch.object(transport.opener, "open", side_effect=http_error(429, {"Retry-After": "9999"})) as opened,
            mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep,
            self.assertRaises(AccessPaused) as paused,
        ):
            transport.get("https://example.test/api", access=access)
        self.assertEqual(paused.exception.restriction["not_before"], clock.return_value + 9999)
        opened.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
