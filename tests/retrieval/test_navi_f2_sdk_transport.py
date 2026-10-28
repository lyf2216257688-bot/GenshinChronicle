"""SDK/httpx MockTransport only; fixtures never dispatch network requests."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.retrieval import navi_f2_sdk_transport as sdk
from tests.retrieval.test_navi_f2_c1 import checker, events
from tests.retrieval.test_navi_f2_preflight import case


def encode(data):
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in data).encode()


class Pieces(httpx.SyncByteStream):
    def __init__(self, raw, fail=False):
        self.raw, self.fail = raw, fail
    def __iter__(self):
        cut = max(1, len(self.raw)//2)
        yield self.raw[:cut]
        if self.fail:
            raise httpx.ReadError('fixture disconnected; offline-fake')
        yield self.raw[cut:]


class SDKTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def run_fixture(self, handler, c=None):
        return sdk.run_case(c or case(), self.root, api_key='offline-fake', proxy=None,
            checker=checker(), transport=httpx.MockTransport(handler), send_authorized=True)
    def attempt(self):
        return next((self.root/'attempts').iterdir())

    def test_full_stream_raw_bytes_before_sdk_events_and_no_key(self):
        raw = encode(events())
        def handler(req):
            body = json.loads(req.content)
            self.assertEqual(str(req.url), 'https://jizhiapi.site/v1/responses')
            self.assertEqual(body['max_output_tokens'], 30000)
            self.assertTrue(body['stream'])
            self.assertEqual(body['input'], case()['messages'][1]['content'])
            self.assertTrue(req.headers['user-agent'].startswith('OpenAI/Python'))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Pieces(raw))
        result = self.run_fixture(handler)
        self.assertEqual(result['status'], 'COMPLETE_STREAM_VALIDATED')
        self.assertEqual(result['usage']['total_tokens'], 5)
        self.assertEqual((self.attempt()/'response.body.raw').read_bytes(), raw)
        self.assertNotEqual((self.attempt()/'sdk-events.jsonl').read_bytes(), raw)
        self.assertFalse(any(b'offline-fake' in p.read_bytes() for p in self.root.rglob('*') if p.is_file()))

    def test_compressed_body_remains_original(self):
        raw = gzip.compress(encode(events()))
        result = self.run_fixture(lambda req: httpx.Response(200,
            headers={'content-type': 'text/event-stream', 'content-encoding': 'gzip'}, stream=Pieces(raw)))
        self.assertEqual(result['status'], 'COMPLETE_STREAM_VALIDATED')
        self.assertEqual((self.attempt()/'response.body.raw').read_bytes(), raw)

    def test_403_raw_preserved_single_attempt_no_redirect_or_retry(self):
        calls=[]
        def handler(req):
            calls.append(1)
            return httpx.Response(403, headers={'content-type':'text/html','cf-ray':'fixture-ray'}, stream=Pieces(b'Access denied 1010'))
        result=self.run_fixture(handler)
        self.assertEqual(result['status'],'HTTP_REJECTED')
        self.assertEqual(len(calls),1)
        self.assertEqual((self.attempt()/'response.body.raw').read_bytes(),b'Access denied 1010')
        with self.assertRaises(ValueError): self.run_fixture(handler)
        self.assertEqual(len(calls),1)

    def test_timeout_cause_chain_saved_unknown_not_retried(self):
        calls=[]
        def handler(req):
            calls.append(1)
            raise httpx.ReadTimeout('contains offline-fake',request=req)
        result=self.run_fixture(handler)
        self.assertEqual(result['status'],'EXECUTION_UNKNOWN')
        self.assertEqual(len(calls),1)
        self.assertTrue(any(x['type'].endswith('ReadTimeout') for x in result['diagnostics']))
        self.assertNotIn('offline-fake',json.dumps(result))

    def test_traced_connect_failure_is_not_sent_but_never_retried(self):
        def handler(req):
            req.extensions['trace']('connection.connect_tcp.failed',{})
            raise httpx.ConnectTimeout('fixture',request=req)
        result=self.run_fixture(handler)
        self.assertEqual(result['status'],'NOT_SENT')
        self.assertFalse(result['retry_allowed'])

    def test_disconnect_and_missing_terminal_preserve_partial_and_block(self):
        for raw,fail in [(encode(events()),True),(encode(events()[:-1]),False)]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                result=sdk.run_case(case(),tmp,api_key='offline-fake',proxy=None,checker=checker(),
                    transport=httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(raw,fail))),send_authorized=True)
                self.assertEqual(result['status'],'STREAM_OR_ENVELOPE_FAILURE')
                self.assertTrue(result['pause_batch'])
                self.assertEqual(result['usage'],'UNKNOWN')

    def test_output_truncation_not_success_even_if_json_complete(self):
        data=deepcopy(events());data[-1]['type']='response.incomplete'
        data[-1]['response']['status']='incomplete'
        data[-1]['response']['incomplete_details']={'reason':'max_output_tokens'}
        result=self.run_fixture(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(data))))
        self.assertEqual(result['status'],'OUTPUT_TRUNCATED')
        self.assertTrue(result['pause_batch'])

    def test_quarantine_renamed_job_and_success_cannot_reissue(self):
        handler=lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(events())))
        self.assertEqual(self.run_fixture(handler)['status'],'COMPLETE_STREAM_VALIDATED')
        changed=deepcopy(case());changed['request_id']='renamed'
        with self.assertRaises(FileExistsError): self.run_fixture(handler,changed)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            sdk.run_case(case(),tmp,api_key='offline-fake',proxy=None,checker=checker(),
                transport=httpx.MockTransport(handler),send_authorized=True,
                quarantined_wires=[sdk.c1.render_stream(case(),30000)])

    def test_env_proxy_not_inherited_and_auth_required(self):
        with patch.dict('os.environ',{'HTTPS_PROXY':'http://invalid:9999','HTTP_PROXY':'http://invalid:9999'}):
            result=self.run_fixture(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(events()))))
        self.assertEqual(result['status'],'COMPLETE_STREAM_VALIDATED')
        meta=json.loads((self.attempt()/'request-metadata.json').read_text())
        self.assertFalse(meta['trust_env']);self.assertIsNone(meta['proxy'])
        with self.assertRaises(ValueError):
            sdk.run_case(case(),self.root,api_key='offline-fake',proxy=None,checker=checker())

    def test_resume_checks_artifact_hashes(self):
        handler=lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(events())))
        self.run_fixture(handler)
        sdk.safe_resume(self.root)
        (self.attempt()/'response.body.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError):sdk.safe_resume(self.root)


if __name__=='__main__': unittest.main()
