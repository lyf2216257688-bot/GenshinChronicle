from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from genshin_corpus.clean.pipeline import RawRun, build_clean_run
from genshin_corpus.clean.validation import validate_clean_run
from genshin_corpus.collector import Collector, CollectorConfig
from genshin_corpus.collector.__main__ import main
from genshin_corpus.collector.access import SourceAccess
from genshin_corpus.collector.contract import RawIntegrityError, json_bytes
from genshin_corpus.collector.run_store import create_bytes
from genshin_corpus.collector.storage import sha256
from genshin_corpus.collector.transport import RequestError, Response
from genshin_corpus.model_calls import locked
from genshin_corpus.parser.obc.pipeline import OBCParsedRunPipeline
from tests.collector.test_collector import FakeTransport, attempt_paths, detail


class Interrupted(BaseException):
    pass


class Overrides(FakeTransport):
    def __init__(self, fixtures, **replies):
        super().__init__(fixtures)
        self.replies = replies

    def get(self, url, *, params=(), headers=()):
        key = params.get("entry_page_id", params.get("channel_id", "map"))
        if key not in self.replies:
            return super().get(url, params=params, headers=headers)
        self.calls.append((url, dict(params), dict(headers)))
        value = self.replies[key]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, bytes):
            return Response(200, value, {}, url)
        status, body, fields = value
        return Response(status, body, fields, url)


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, sha256(path.read_bytes()))
            for path in root.rglob("*") if path.is_file()}


_CHILD = """
from pathlib import Path
import sys
from genshin_corpus.collector.__main__ import main
from genshin_corpus.collector.access import SourceAccess

class NeverDispatch:
    def get(self, *args, **kwargs):
        raise AssertionError("unexpected request from a second process")

state = SourceAccess(Path(sys.argv[1]), clock=lambda: float(sys.argv[2]))
raise SystemExit(main(sys.argv[3:], access=state, transport=NeverDispatch()))
"""


class CollectorReliabilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixtures = Path(__file__).parents[1] / "fixtures/mihoyo_obc"
        self.config = CollectorConfig(output_root=self.root / "raw", run_id="raw-test", max_retries=0)
        self.clock = mock.Mock(return_value=1000.0)
        self.access_root = self.root / "access"
        self.access = SourceAccess(self.access_root, clock=self.clock)

    def collector(self, transport=None, **overrides):
        return Collector(replace(self.config, **overrides), transport or FakeTransport(self.fixtures), access=self.access)

    def invoke(self, options, *, transport=None):
        with redirect_stdout(io.StringIO()) as stdout:
            code = main(options, transport=transport, access=self.access)
        return code, json.loads(stdout.getvalue())

    def child(self, options):
        project = Path(__file__).parents[2]
        result = subprocess.run(
            [sys.executable, "-B", "-c", _CHILD, str(self.access_root), str(self.clock.return_value), *options],
            cwd=project, env={**os.environ, "PYTHONPATH": str(project / "src")}, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_run_scope_mismatch_rejected_before_reusing_cache_or_dispatch(self):
        options = {"channel_ids": ["43", "25", "43"], "detail_limit": 1}
        for phase in ("partial", "scoped_complete"):
            first = self.collector(Overrides(self.fixtures, **{"43": (503, b"temporary", {})})
                                   if phase == "partial" else None)
            self.assertEqual(first.run(**options)["status"], phase)
            original = snapshot(first.store.root)
            for changed, scope in (
                ({"base_url": "https://different.test"}, options),
                ({"app_sn": "other"}, options),
                ({"listing_params": {"filter": "value"}}, options),
                ({}, {**options, "channel_ids": ["43"]}),
                ({}, {**options, "detail_limit": 2}),
                ({}, {"channel_ids": options["channel_ids"], "fetch_details": False}),
            ):
                with self.subTest(phase=phase, changed=changed, scope=scope):
                    transport = FakeTransport(self.fixtures)
                    with self.assertRaisesRegex(RawIntegrityError, "identity"):
                        self.collector(transport, **changed).run(**scope)
                    self.assertEqual(transport.calls, [])
                    self.assertEqual(snapshot(first.store.root), original)
        replay = self.collector(base_url=self.config.base_url + "/", max_retries=1)
        self.assertEqual(replay.run(channel_ids=[25, 43], detail_limit=1)["status"], "scoped_complete")
        self.assertEqual(replay.transport.calls, [])

    def test_normalized_roots_and_locale_are_distinct_cache_namespaces(self):
        self.collector().run()
        for options in (
            {"locale": "en-us"}, {"output_root": self.root / "another-root"}, {"run_id": "second"},
        ):
            collector = self.collector(**options)
            manifest = collector.run()
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(collector.transport.calls), 7)
        with self.assertRaisesRegex(ValueError, "override"):
            self.collector(listing_params={"channel_id": "43"})
        with self.assertRaises(ValueError):
            self.collector().run(detail_limit=0, fetch_details=False)

    def test_run_lock_blocks_a_second_process_before_dispatch(self):
        collector = self.collector()
        with locked(collector.store.lock_path):
            code, summary = self.child(["--output-root", str(self.config.output_root),
                                        "--run-id", self.config.run_id, "--details"])
            self.assertEqual((code, summary["status"]), (2, "rejected"))
            self.assertFalse(collector.store.root.exists())
        self.assertEqual(collector.run()["status"], "complete")

    def test_write_once_path_never_replaces_an_existing_file(self):
        path = self.root / "evidence.json"
        create_bytes(path, b"old observation")
        with self.assertRaises(FileExistsError):
            create_bytes(path, b"another observation")
        self.assertEqual(path.read_bytes(), b"old observation")

    def test_binding_material_and_finished_bytes_survive_publication_interruptions(self):
        for boundary, after, expected_retry in (
            ("response.json", True, True), ("response.meta.json", False, True),
            ("response.meta.json", True, False), ("accepted.json", True, False),
            ("inventory.json", True, False), ("discovery.json", True, False),
            ("finalizing.json", False, False), ("finalizing.json", True, False),
            ("manifest.json", False, False), ("manifest.json", True, False),
        ):
            with self.subTest(boundary=boundary, after=after):
                collector = self.collector(run_id=f"{boundary}-{after}")
                hit = False

                def fail_at(path, body):
                    nonlocal hit
                    target = path.name == boundary and (
                        path.name in {"inventory.json", "discovery.json", "finalizing.json", "manifest.json"}
                        or "details" in path.parts
                    )
                    if target and not after:
                        hit = True
                        raise Interrupted
                    create_bytes(path, body)
                    if target:
                        hit = True
                        raise Interrupted

                with mock.patch("genshin_corpus.collector.run_store.create_bytes", side_effect=fail_at):
                    with self.assertRaises(Interrupted):
                        collector.run()
                self.assertTrue(hit)
                self.assertEqual(json.loads(collector.store.checkpoint_path.read_bytes())["status"], "partial")
                bodies = {path: path.read_bytes() for path in collector.store.root.glob("responses/*/*/*/response.json")}
                prepared = (json.loads(collector.store.prepared_path.read_bytes())
                            if collector.store.prepared_path.exists() else None)
                if boundary == "manifest.json":
                    self.assertEqual(collector.store.manifest_path.exists(), after)
                transport = Overrides(self.fixtures, **{"501157": detail("501157") + b" "})
                recovered = self.collector(transport, run_id=collector.run_id).run()
                self.assertEqual(recovered["status"], "complete")
                self.assertEqual(any(query.get("entry_page_id") == "501157" for _, query, _ in transport.calls),
                                 expected_retry or boundary in {"inventory.json", "discovery.json"})
                self.assertEqual({path: path.read_bytes() for path in bodies}, bodies)
                if prepared is not None:
                    self.assertEqual(transport.calls, [])
                    self.assertEqual(json_bytes(prepared["manifest"]), collector.store.manifest_path.read_bytes())
                    self.assertEqual(prepared["sha256"], sha256(collector.store.manifest_path.read_bytes()))

    def test_missing_sidecar_cannot_replace_an_unaccepted_body(self):
        collector = self.collector()

        def interrupt(path, body):
            if "details" in path.parts and path.name == "response.meta.json":
                raise Interrupted
            create_bytes(path, body)

        with mock.patch("genshin_corpus.collector.run_store.create_bytes", side_effect=interrupt):
            with self.assertRaises(Interrupted):
                collector.run()
        old, = collector.store.root.glob("responses/details/*/*/response.json")
        original = old.read_bytes()
        replacement = detail("501157") + b" "
        transport = Overrides(self.fixtures, **{"501157": replacement})
        manifest = self.collector(transport).run()
        selected = collector.store.root / manifest["paths"]["details:501157"]["raw"]
        self.assertNotEqual(selected, old)
        self.assertEqual(selected.read_bytes(), replacement)
        self.assertEqual(old.read_bytes(), original)
        self.assertTrue(manifest["recovery_issues"])

    def test_multiple_valid_attempts_choose_first_and_keep_accepted_reference(self):
        collector = self.collector()
        original = create_bytes

        def interrupt(path, body):
            if "details" in path.parts and path.name == "accepted.json":
                raise Interrupted
            original(path, body)

        with mock.patch("genshin_corpus.collector.run_store.create_bytes", side_effect=interrupt):
            with self.assertRaises(Interrupted):
                collector.run()
        kind, key = "details", "501157"
        request = collector._request(kind, key)
        collector.store.record_attempt(kind, key, request["receipt"],
                                       Response(200, detail(key) + b" ", {}, request["url"]), None)
        first, second = attempt_paths(collector.store.root, kind)
        transport = FakeTransport(self.fixtures)
        manifest = self.collector(transport).run()
        self.assertEqual(collector.store.root / manifest["paths"][f"{kind}:{key}"]["metadata"], first)
        self.assertEqual(second.with_name("response.json").read_bytes(), detail(key) + b" ")
        self.assertEqual([query["entry_page_id"] for _, query, _ in transport.calls], ["509653"])
        original = snapshot(collector.store.root)
        self.collector().run()
        self.assertEqual(snapshot(collector.store.root), original)

    def test_known_accepted_reference_loss_or_corruption_blocks_partial_resume(self):
        for damage in ("missing", "invalid", "outside_path"):
            with self.subTest(damage=damage):
                collector = self.collector(Overrides(self.fixtures, **{"509653": (503, b"temporary", {})}), run_id=damage)
                partial = collector.run()
                ref = collector.store.root / partial["paths"]["details:501157"]["metadata"]
                accepted = ref.parent.parent / "accepted.json"
                if damage == "missing":
                    accepted.unlink()
                elif damage == "invalid":
                    accepted.write_bytes(b"{")
                else:
                    bad = json.loads(ref.read_bytes())
                    bad["raw_path"] = str(self.root / "outside.json")
                    ref.write_bytes(json_bytes(bad))
                original = snapshot(collector.store.root)
                transport = FakeTransport(self.fixtures)
                with self.assertRaises(RawIntegrityError):
                    self.collector(transport, run_id=damage).run()
                self.assertEqual(transport.calls, [])
                self.assertEqual(snapshot(collector.store.root), original)

    def test_partial_discovery_resumes_before_freezing_sample(self):
        collector = self.collector(Overrides(self.fixtures, **{"43": (503, b"temporary listing", {})}))
        partial = collector.run(detail_limit=1)
        self.assertEqual(partial["status"], "partial")
        self.assertFalse(collector.store.inventory_path.exists())
        self.assertFalse(collector.store.manifest_path.exists())
        self.assertFalse(any("entry_page_id" in query for _, query, _ in collector.transport.calls))
        original = collector.store.root / partial["paths"]["listings:25"]["raw"]
        body = original.read_bytes()
        transport = FakeTransport(self.fixtures)
        finished = self.collector(transport).run(detail_limit=1)
        self.assertEqual(finished["status"], "scoped_complete")
        self.assertEqual([query.get("channel_id", query.get("entry_page_id")) for _, query, _ in transport.calls],
                         ["43", "501157"])
        self.assertEqual(finished["details"][0]["channels"], ["43", "25"])
        self.assertEqual(original.read_bytes(), body)
        receipts = [json.loads(path.read_bytes()) for path in attempt_paths(collector.store.root, "listings")]
        self.assertTrue(any(receipt["status"] == 503 for receipt in receipts))
        self.assertEqual(finished["failures"], [])

    def test_scope_business_and_http_results_drive_cli(self):
        cases = (
            ("complete", ["--details"], {}, 0, "complete", 2),
            ("empty-sample", ["--details", "--detail-limit", "0"], {}, 0, "scoped_complete", 0),
            ("entire-sample", ["--details", "--detail-limit", "2"], {}, 0, "scoped_complete", 2),
            ("missing", ["--details", "--channel-id", "999"], {}, 1, "partial", 0),
            ("partly-missing", ["--details", "--channel-id", "43", "--channel-id", "999"], {}, 1, "partial", 0),
            ("map-retcode", ["--details"], {"map": b'{"retcode":-1}'}, 1, "partial", 0),
            ("listing-retcode", ["--details"], {"43": b'{"retcode":-1}'}, 1, "partial", 0),
            ("detail-retcode", ["--details"], {"501157": b'{"retcode":-1}'}, 1, "partial", 1),
            ("detail-id", ["--details"], {"501157": detail("wrong")}, 1, "partial", 1),
            ("malformed-json", ["--details"], {"501157": b'<'}, 1, "partial", 1),
            ("http-503", ["--details"], {"map": (503, b"unavailable", {})}, 1, "partial", 0),
        )
        for name, args, replies, code, state, count in cases:
            with self.subTest(name=name):
                options = ["--output-root", str(self.config.output_root), "--run-id", name, "--max-retries", "0"]
                transport = Overrides(self.fixtures, **replies)
                result, summary = self.invoke([*options, *args], transport=transport)
                self.assertEqual((result, summary["status"], summary["successful_detail_fetches"]), (code, state, count))
                self.assertEqual(summary["full_coverage"], state == "complete")
                reported = Path(summary["manifest_path"])
                expected_name = "checkpoint.json" if code else "manifest.json"
                self.assertEqual(reported.name, expected_name)
                self.assertTrue(reported.is_file())
                if name in {"missing", "partly-missing"}:
                    self.assertEqual(summary["coverage"]["missing_channel_ids"], ["999"])
                if replies:
                    value, = replies.values()
                    body = value if isinstance(value, bytes) else value[1]
                    self.assertIn(body, [path.read_bytes() for path in
                                         reported.parent.parent.glob("responses/*/*/*/response.json")])
        result, summary = self.invoke(["--detail-limit", "2"], transport=FakeTransport(self.fixtures))
        self.assertEqual((result, summary["status"]), (2, "rejected"))

    def test_403_stops_source_including_other_requests_and_other_processes(self):
        first = self.collector(Overrides(self.fixtures, **{"43": (403, b"access denied", {})}))
        partial = first.run()
        self.assertEqual([params.get("channel_id") for _, params, _ in first.transport.calls], [None, "43"])
        event = partial["access_restriction"]
        code, other = self.child(["--output-root", str(self.root / "next"), "--run-id", "second",
                                  "--locale", "en-us", "--base-url", "https://other.test"])
        self.assertEqual((code, other["status"]), (1, "partial"))
        self.assertEqual(other["access_restriction"]["event_id"], event["event_id"])
        code, summary = self.invoke(["--acknowledge-access", event["event_id"], "--reason", "Verified source restoration"],
                                    transport=mock.Mock())
        self.assertEqual((code, summary["status"]), (0, "access_acknowledged"))
        self.assertTrue((self.access.root / "acknowledgments" / f'{event["event_id"]}.json').is_file())
        self.assertEqual(self.collector().run()["status"], "complete")

    def test_cooldown_survives_restart_and_cannot_be_acknowledged_away(self):
        first = self.collector(Overrides(self.fixtures, **{"43": (429, b"rate limited", {"retry-after": "120"})}),
                               max_retries=3)
        with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
            partial = first.run()
        sleep.assert_not_called()
        self.assertEqual(len(first.transport.calls), 2)
        event = partial["access_restriction"]
        self.assertEqual(event["not_before"], 1120)
        self.clock.return_value = 1119
        code, other = self.child(["--output-root", str(self.root / "other"), "--run-id", "other"])
        self.assertEqual((code, other["status"], other["access_restriction"]["not_before"]), (1, "partial", 1120))
        code, _ = self.invoke(["--acknowledge-access", event["event_id"], "--reason", "cannot shorten a cooldown"])
        self.assertEqual(code, 2)
        self.clock.return_value = 1120
        resumed = self.collector()
        self.assertEqual(resumed.run()["status"], "complete")
        self.assertEqual(resumed.transport.calls[0][1]["channel_id"], "43")

    def test_interrupted_in_flight_attempt_is_blocked_across_processes(self):
        with self.assertRaises(Interrupted):
            self.collector(Overrides(self.fixtures, map=Interrupted())).run()
        code, other = self.child(["--output-root", str(self.root / "other"), "--run-id", "other"])
        self.assertEqual((code, other["access_restriction"]["reason"]), (1, "previous_dispatch_status_unknown"))
        event = other["access_restriction"]["event_id"]
        self.assertEqual(self.invoke(["--acknowledge-access", event, "--reason", "Verified interrupted request"])[0], 0)
        self.assertEqual(self.collector().run()["status"], "complete")

    def test_503_and_network_failure_use_bounded_attempts(self):
        for key, reply in (("http", (503, b"service unavailable", {})),
                           ("network", RequestError("connection error", retryable=True))):
            with self.subTest(key=key):
                collector = self.collector(Overrides(self.fixtures, map=reply), run_id=key, max_retries=2)
                with mock.patch("genshin_corpus.collector.transport.time.sleep") as sleep:
                    result = collector.run()
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["retry_summary"]["attempts"], 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(result["http_responses_saved"], 3 if key == "http" else 0)

    def build_both(self, raw_root, output, run="derivative"):
        with redirect_stdout(io.StringIO()):
            clean = build_clean_run(raw_run_root=raw_root, output_root=output / "clean", clean_run_id=run, workers=1)
        parsed = OBCParsedRunPipeline(raw_run_root=raw_root, output_root=output / "parsed", parsed_run_id=run).run()
        return clean, parsed

    def test_versioned_full_input_and_relocated_archive_can_be_read_without_writes(self):
        collector = self.collector()
        manifest = collector.run()
        clean, parsed = self.build_both(collector.store.root, self.root / "derived")
        self.assertEqual(clean["status"], "complete")
        self.assertEqual(parsed["accounted_detail_count"], 2)
        self.assertEqual(parsed["counts"]["blocked_integrity"], 0)
        digest = sha256(collector.store.manifest_path.read_bytes())
        self.assertEqual(clean["binding"]["raw_manifest_sha256"], digest)
        self.assertEqual(parsed["raw_manifest_sha256"], digest)
        moved = self.root / "moves" / "mihoyo_obc" / "zh-cn" / collector.run_id
        shutil.copytree(collector.store.root, moved)
        with self.assertRaisesRegex(RawIntegrityError, "identity"):
            self.collector(output_root=self.root / "moves").run()
        for name in ("identity.json", "checkpoint.json", "discovery.json", "finalizing.json"):
            (moved / "metadata" / name).unlink()
        original = snapshot(moved)
        clean_path = self.root / "derived/clean/mihoyo_obc/zh-cn/derivative/metadata/manifest.json"
        self.assertEqual(validate_clean_run(clean_path, raw_run_root=moved)["records"], 2)
        self.assertEqual(RawRun(moved).response("details:501157")["sha256"], manifest["paths"]["details:501157"]["sha256"])
        self.assertEqual(OBCParsedRunPipeline(raw_run_root=moved, output_root=self.root / "derived/parsed",
                                              parsed_run_id="derivative").run(), parsed)
        with self.assertRaises(RawIntegrityError):
            self.collector(output_root=self.root / "moves").run()
        with self.assertRaisesRegex(ValueError, "separate"):
            OBCParsedRunPipeline(raw_run_root=moved, output_root=self.root / "moves", parsed_run_id=collector.run_id).run()
        self.assertEqual(snapshot(moved), original)

    def test_both_downstream_entries_reject_scoped_partial_and_forged_coverage(self):
        scoped = self.collector(run_id="scoped")
        scoped.run(detail_limit=2)
        partial = self.collector(Overrides(self.fixtures, map=b'{"retcode":-1}'), run_id="partial")
        partial.run()
        forged = self.collector(run_id="forged")
        manifest = forged.run()
        for ids in ("recognized_channel_ids", "target_channel_ids"):
            manifest["coverage"][ids].remove("233")
        manifest["channels"] = [row for row in manifest["channels"] if row["channel_id"] != "233"]
        manifest["paths"].pop("listings:233")
        manifest["recognized_channel_count"] -= 1
        manifest["listing_responses_saved"] -= 1
        forged.store.manifest_path.write_bytes(json_bytes(manifest))
        for source in (scoped, partial, forged):
            output = self.root / source.run_id
            with self.subTest(source=source.run_id):
                with self.assertRaises(ValueError):
                    build_clean_run(raw_run_root=source.store.root, output_root=output, clean_run_id="out", workers=1)
                with self.assertRaises(ValueError):
                    OBCParsedRunPipeline(raw_run_root=source.store.root, output_root=output, parsed_run_id="out").run()
                self.assertFalse(output.exists())

    def test_legacy_archives_keep_original_binding_and_known_samples_cannot_be_promoted(self):
        source = self.collector()
        manifest = source.run()
        old = self.config.output_root / "mihoyo_obc/zh-cn/legacy"
        shutil.copytree(source.store.root / "responses", old / "responses")
        (old / "metadata").mkdir()
        shutil.copyfile(source.store.inventory_path, old / "metadata/inventory.json")
        manifest.pop("schema_version")
        manifest["run_id"] = "legacy"
        for entry in manifest["paths"].values():
            for field in ("raw", "metadata"):
                entry[field] = str(old / entry[field])
        manifest_path = old / "metadata/manifest.json"
        manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
        original = snapshot(old)
        clean, parsed = self.build_both(old, self.root / "official")
        self.assertEqual(clean["binding"]["raw_manifest_sha256"], sha256(manifest_path.read_bytes()))
        self.assertEqual(parsed["raw_manifest_sha256"], sha256(manifest_path.read_bytes()))
        with self.assertRaises(RawIntegrityError):
            self.collector(run_id="legacy").run()
        self.assertEqual(snapshot(old), original)
        moved = self.root / "moved-legacy"
        shutil.copytree(old, moved)
        self.assertEqual(RawRun(moved).response("map:home-map")["path"],
                         Path(manifest["paths"]["map:home-map"]["raw"]).relative_to(old).as_posix())
        self.assertEqual(OBCParsedRunPipeline(raw_run_root=moved, output_root=self.root / "official/parsed",
                                              parsed_run_id="derivative").run(), parsed)
        # Reproduce a historical output that predates the scoped-source admission guard.
        manifest["detail_selection"]["limit"] = 1
        moved_path = moved / "metadata/manifest.json"
        moved_path.write_bytes(json_bytes(manifest))
        with (
            mock.patch("genshin_corpus.clean.pipeline.has_legacy_coverage_gap", return_value=False),
            mock.patch("genshin_corpus.parser.obc.pipeline.has_legacy_coverage_gap", return_value=False),
        ):
            already_bound = self.build_both(moved, self.root / "already-bound")
        self.assertEqual(self.build_both(moved, self.root / "already-bound"), already_bound)
        with self.assertRaisesRegex(ValueError, "Historical Raw coverage"):
            build_clean_run(raw_run_root=moved, output_root=self.root / "new", clean_run_id="out", workers=1)
        with self.assertRaisesRegex(ValueError, "Historical Raw coverage"):
            OBCParsedRunPipeline(raw_run_root=moved, output_root=self.root / "new", parsed_run_id="out").run()
        self.assertFalse((self.root / "new").exists())
