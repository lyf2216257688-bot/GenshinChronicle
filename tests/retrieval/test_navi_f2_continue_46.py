"""Provider-free tests for one-shot continuation and fail-closed accounting."""
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import httpx

from genshin_corpus.retrieval import navi_f2_continue_46 as c


def result(state, validation, *, stream='PASS', output=20):
    return {'state': state, 'full_stream_status': stream, 'protocol_warning': None,
            'reasoning_id_conflicts': [], 'validation': {'status': validation},
            'usage': {'input_tokens': 10, 'output_tokens': output, 'total_tokens': 10+output,
                      'output_tokens_details': {'reasoning_tokens': 5}}}


RECORDED = {'state': 'COMPLETED', 'calls': 1, 'http_status': 200, 'retry': False,
            'returned_model': c.run.c1.MODEL, 'message_text': '{}'}


class ContinuationTests(unittest.TestCase):
    def test_content_failures_continue_but_execution_uncertainty_stops(self):
        self.assertTrue(c.may_continue(result('CONTENT_INVALID', 'SCHEMA_FAILURE'), RECORDED))
        self.assertTrue(c.may_continue(result('CONTENT_INVALID', 'CONTEXT_CONTRACT_FAILURE'), RECORDED))
        self.assertTrue(c.may_continue(result('CONTENT_VERIFIED', 'VALIDATED_MECHANICAL_ONLY'), RECORDED))
        self.assertFalse(c.may_continue(result('CONTENT_INVALID', 'LOCAL_VALIDATOR_UNAVAILABLE'), RECORDED))
        self.assertFalse(c.may_continue(result('CONTENT_VERIFIED_PROTOCOL_WARNING', 'VALIDATED_MECHANICAL_ONLY', stream='FAIL'), RECORDED))
        self.assertFalse(c.may_continue(result('CONTENT_VERIFIED', 'VALIDATED_MECHANICAL_ONLY', output=30001), RECORDED))
        self.assertFalse(c.may_continue({'state': 'UNKNOWN', 'usage': 'UNKNOWN'}, RECORDED))
        self.assertFalse(c.may_continue(result('CONTENT_INVALID', 'JSON_PARSE_FAILURE'), dict(RECORDED, message_text='')))

    def test_validator_bug_is_not_a_content_failure(self):
        def bad():
            raise KeyError('bug')
        with self.assertRaises(c.ValidatorFault):
            c.guard_validator(bad)()

    def test_one_content_failure_then_protocol_fault_stops(self):
        rows = [{'request_id': 'slot-'+str(i), 'wire_fingerprint': 'fixture'} for i in range(1, 49)]
        manifest = {'records': rows, 'version': 'fixture'}
        seen = []
        def execute(row, *args):
            seen.append(row['request_id'])
            if len(seen) == 1:
                return result('CONTENT_INVALID', 'SCHEMA_FAILURE')
            return result('CONTENT_VERIFIED_PROTOCOL_WARNING', 'VALIDATED_MECHANICAL_ONLY', stream='FAIL')
        written = []
        with patch('sys.argv', ['runner', '--execute-authorized', '--ack-slots-3-through-48']), \
             patch.object(c, 'verify_prior', return_value=manifest), \
             patch.object(c.Path, 'exists', return_value=False), patch.object(c.Path, 'mkdir'), \
             patch.object(c, 'environment', return_value={'JIZHI_API_KEY': 'fixture', 'JIZHI_BASE_URL': c.run.c1.BASE_URL}), \
             patch.object(c, 'read', return_value=RECORDED), patch.object(c, 'sha', return_value='fixture'), \
             patch.object(c.run.pre, 'native_legacy_validator', return_value=lambda *a: None), \
             patch.object(c, 'put', side_effect=lambda path, obj: written.append((path, obj))), \
             patch.object(c.run, 'execute_one', side_effect=execute):
            c.main()
        self.assertEqual(seen, ['slot-3', 'slot-4'])
        stop = next(value for path, value in written if path.name == 'STOP.json')
        self.assertEqual(stop['remaining_not_sent'], 44)
        self.assertTrue(stop['no_retry'])

    def test_historical_tls_proof_and_changed_history_guard(self):
        manifest = c.verify_prior()
        row = manifest['records'][2]
        folder = c.HISTORICAL / row['request_id']
        recorded = c.read(folder / 'result.json')
        self.assertTrue(c.proven_pre_send(folder, row, recorded))
        self.assertFalse(c.proven_pre_send(folder, row, dict(recorded, state='UNKNOWN')))
        self.assertFalse(c.proven_pre_send(folder, dict(row, wire_fingerprint='tampered'), recorded))
        real_read = c.read
        def conflicting_trace(path):
            if path.name == 'trace.json':
                return {'events': ['connection.start_tls.failed', 'http11.send_request_headers.started']}
            return real_read(path)
        with patch.object(c, 'read', side_effect=conflicting_trace):
            self.assertFalse(c.proven_pre_send(folder, row, recorded))
        def incomplete_trace(path):
            if path.name == 'trace.json':
                return {'events': ['connection.start_tls.failed', 'http11.send_request_headers.complete']}
            return real_read(path)
        with patch.object(c, 'read', side_effect=incomplete_trace):
            self.assertFalse(c.proven_pre_send(folder, row, recorded))
        with patch.object(c, 'sha', return_value='tampered'):
            with self.assertRaisesRegex(ValueError, 'audit changed'):
                c.verify_historical_continuation(manifest)

    def test_pre_send_retries_are_bounded_and_individually_archived(self):
        from genshin_corpus.retrieval.navi_f1_prepare import put, value_hash, sha
        wire = {'model': 'fixture'}
        rows = [{'request_id': 'slot-'+str(i), 'wire_fingerprint': value_hash(wire)} for i in range(1, 49)]
        manifest = {'records': rows, 'version': 'fixture'}
        attempts = []
        def fake_one(row, *args):
            root = c.run.OUT
            attempts.append(root.name)
            folder = root / row['request_id']
            folder.mkdir()
            put(folder / 'request.json', wire)
            (folder / 'request.body.bin').write_text('{"model":"fixture"}', encoding='utf8')
            put(folder / 'request-metadata.json', {'retries': 0, 'method': 'POST', 'url': c.run.c1.BASE_URL + '/responses'})
            put(folder / 'reservation.json', {'fingerprint': value_hash(wire), 'state': 'RESERVED_NOT_RETRYABLE', 'stream': True})
            put(folder / 'trace.json', {'events': ['connection.start_tls.failed']})
            artifacts = {p.name: sha(p) for p in folder.iterdir()}
            recorded = {'state': 'NOT_SENT', 'http_status': None, 'calls': 1,
                        'retry': False, 'artifacts': artifacts}
            put(folder / 'result.json', recorded)
            put(root / 'validation' / (row['request_id'] + '.json'), {'state': 'NOT_SENT', 'usage': 'UNKNOWN'})
            return {'state': 'NOT_SENT', 'usage': 'UNKNOWN'}
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp, \
             patch.object(c, 'OUT', Path(temp) / 'new-run'), \
             patch('sys.argv', ['runner', '--execute-authorized', '--ack-slots-3-through-48']), \
             patch.object(c, 'verify_prior', return_value=manifest), \
             patch.object(c, 'environment', return_value={'JIZHI_API_KEY': 'fixture', 'JIZHI_BASE_URL': c.run.c1.BASE_URL}), \
             patch.object(c.run.pre, 'native_legacy_validator', return_value=lambda *a: None), \
             patch.object(c.run, 'execute_one', side_effect=fake_one), patch.object(c.time, 'sleep') as sleep:
            c.main()
            self.assertEqual(attempts, ['attempt-002', 'attempt-003'])
            self.assertEqual(sleep.call_count, 1)
            root = Path(temp) / 'new-run' / 'attempts' / 'slot-3'
            self.assertTrue(c.read(root / 'attempt-002/retry-decision.json')['retry'])
            self.assertFalse(c.read(root / 'attempt-003/retry-decision.json')['retry'])
            self.assertEqual(c.read(Path(temp) / 'new-run/STOP.json')['attempt'], 3)

    def test_unknown_never_retries(self):
        rows = [{'request_id': 'slot-'+str(i), 'wire_fingerprint': 'fixture'} for i in range(1, 49)]
        manifest = {'records': rows, 'version': 'fixture'}
        unknown = {'state': 'UNKNOWN', 'usage': 'UNKNOWN'}
        with patch('sys.argv', ['runner', '--execute-authorized', '--ack-slots-3-through-48']), \
             patch.object(c, 'verify_prior', return_value=manifest), \
             patch.object(c.Path, 'exists', return_value=False), patch.object(c.Path, 'mkdir'), \
             patch.object(c, 'environment', return_value={'JIZHI_API_KEY': 'fixture', 'JIZHI_BASE_URL': c.run.c1.BASE_URL}), \
             patch.object(c, 'read', return_value={'state': 'UNKNOWN'}), patch.object(c, 'sha', return_value='fixture'), \
             patch.object(c.run.pre, 'native_legacy_validator', return_value=lambda *a: None), \
             patch.object(c, 'put') as put, patch.object(c.run, 'execute_one', return_value=unknown) as execute, \
             patch.object(c.time, 'sleep') as sleep:
            c.main()
        self.assertEqual(execute.call_count, 1)
        sleep.assert_not_called()
        stop = next(call.args[1] for call in put.call_args_list if call.args[0].name == 'STOP.json')
        self.assertEqual(stop['state'], 'UNKNOWN')
        self.assertTrue(stop['no_retry'])

    def test_sdk_transport_mock_proves_presend_and_unknown(self):
        manifest = c.verify_prior()
        row = manifest['records'][2]
        for sent in (False, True):
            with self.subTest(headers_started=sent), tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
                root = Path(temp)
                (root / 'validation').mkdir()
                def fail(request):
                    trace = request.extensions['trace']
                    trace('connection.start_tls.failed', {})
                    if sent:
                        trace('http11.send_request_headers.started', {})
                    raise httpx.ConnectError('synthetic failure', request=request)
                mock_transport = httpx.MockTransport(fail)
                with patch.object(c.run, 'OUT', root), \
                     patch.object(c.run.recorder.httpx, 'HTTPTransport', return_value=mock_transport):
                    actual = c.run.execute_one(row, 30000, 'offline-fixture', {}, None, {})
                folder = root / row['request_id']
                recorded = c.read(folder / 'result.json')
                self.assertEqual(actual['state'], 'UNKNOWN' if sent else 'NOT_SENT')
                self.assertEqual(c.proven_pre_send(folder, row, recorded), not sent)
                self.assertEqual(recorded['calls'], 1)


if __name__ == '__main__':
    unittest.main()
