"""Public ask boundary with offline HTTP fixtures; no provider or trade calls."""
import json
import unittest
from unittest import mock
import httpx
import harness

CANARY = "SYNTHETIC_PRIVATE_CANARY_8731"
SCHEMA = {"type":"object","required":["price"],"properties":{"price":{"type":"integer"}}}
GOOD = '{"price":100}'
def envelope(content):
    return json.dumps({"choices":[{"message":{"content":content}}]}).encode()

class ParseDiagnostics(unittest.TestCase):
    def call(self, payloads, sink_failure=False):
        messages, records, requests = [], [], []
        client = httpx.Client
        def handler(request):
            requests.append(1)
            return httpx.Response(200, content=payloads[min(len(requests)-1,len(payloads)-1)])
        def capture(tag, message):
            if sink_failure:raise OSError("synthetic sink failure")
            messages.append(message)
        with mock.patch.object(harness,"BACKEND","openrouter"), mock.patch.object(harness,"_openrouter_key",return_value="offline"), mock.patch.object(harness,"RETRIES",len(payloads)), mock.patch.object(harness,"OR_MAX_CALLS_PER_HOUR",0), mock.patch.object(harness,"_log_wire",return_value=lambda request:None), mock.patch.object(harness,"log",side_effect=capture), mock.patch.object(harness,"record_call",side_effect=lambda *a,**k:records.append(a[4])), mock.patch.object(harness.httpx,"Client",side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
            try:
                value=harness.ask("offline",SCHEMA,"offline",tag="restock-agent")
                error=None
            except harness.HarnessError as exc:
                value=None
                error=str(exc)
        return value,error,messages,records,len(requests)

    def test_inner_failure_reports_stage_without_accepting_second_object(self):
        value,error,messages,records,n=self.call([envelope(GOOD+" {}")])
        self.assertIsNone(value)
        self.assertEqual(records,[False])
        self.assertEqual(n,1)
        meta=[json.loads(m[11:]) for m in messages if m.startswith("parse_diag ")]
        self.assertEqual(len(meta),1)
        self.assertEqual(meta[0]["stage"],"inner_json")
        self.assertEqual(meta[0]["kind"],"json_decode")
        self.assertEqual((meta[0]["pos"],meta[0]["lineno"],meta[0]["colno"],meta[0]["chars"]),(14,1,15,16))
        self.assertEqual(meta[0]["tail_kind"],"object")
        self.assertNotIn("price",error)

    def test_outer_inner_schema_and_secret_rejection_remain_separate(self):
        cases=[
            (envelope(GOOD)+b" {}", "outer_json"),
            (CANARY.encode(), "outer_json"),
            (b'{"choices":[]}', "outer_json"),
            (envelope(CANARY), "inner_json"),
            (envelope('{"price":"'+CANARY+'"}'), "schema_validation"),
        ]
        allowed={"stage","role","kind","pos","lineno","colno","chars","sha256","tail_kind","exc","body_keys","other_keys","error_code","choices"}
        for payload,stage in cases:
            with self.subTest(stage=stage):
                value,error,messages,records,n=self.call([payload])
                self.assertIsNone(value)
                self.assertEqual(records,[False])
                self.assertEqual(n,1)
                self.assertNotIn(CANARY,error)
                self.assertTrue(all(CANARY not in m for m in messages))
                meta=[json.loads(m[11:]) for m in messages if m.startswith("parse_diag ")]
                self.assertEqual(len(meta),1)
                self.assertEqual(meta[0]["stage"],stage)
                self.assertEqual(meta[0]["role"],"restock-agent")
                self.assertLessEqual(set(meta[0]),allowed)
                if meta[0]["kind"]=="json_decode":
                    self.assertRegex(meta[0]["sha256"],r"^[0-9a-f]{64}$")

    def test_strict_retry_and_success_values_are_unchanged(self):
        value,error,messages,records,n=self.call([envelope(GOOD+" {}"),envelope(" \n"+GOOD+"\t ")])
        self.assertEqual(value,{"price":100})
        self.assertIsNone(error)
        self.assertEqual(records,[False,True])
        self.assertEqual(n,2)

    def test_diagnostic_sink_failure_still_rejects(self):
        value,error,messages,records,n=self.call([envelope(GOOD+" {}")],sink_failure=True)
        self.assertIsNone(value)
        self.assertIsNotNone(error)
        self.assertEqual(records,[False])
        self.assertEqual(n,1)


class OuterShape(unittest.TestCase):
    """11/10 07:30-08:28: 45 OpenRouter answers were HTTP 200 without a usable choices[0].message.content (outer_json,
    kind shape) and the log could not say why. The diagnostic names the body's shape, never its text."""
    call=ParseDiagnostics.call

    def meta(self,payload):
        value,error,messages,records,n=self.call([payload])
        self.assertIsNone(value)
        self.assertEqual((records,n),([False],1))
        self.assertTrue(all(CANARY not in m for m in messages))
        meta=[json.loads(m[11:]) for m in messages if m.startswith("parse_diag ")]
        self.assertEqual(len(meta),1)
        self.assertEqual(meta[0]["stage"],"outer_json")
        return meta[0]

    def test_provider_error_in_a_200_names_its_code_not_its_message(self):
        m=self.meta(json.dumps({"error":{"code":502,"message":CANARY,"metadata":{"provider_name":CANARY,"raw":CANARY}},"user_id":CANARY}).encode())
        self.assertEqual((m["kind"],m["exc"],m["body_keys"],m["other_keys"],m["error_code"]),("shape","KeyError",["error"],1,502))
        self.assertNotIn("choices",m)

    def test_empty_choices_counts_them(self):
        m=self.meta(b'{"id":"gen-1","choices":[]}')
        self.assertEqual((m["exc"],m["body_keys"],m["choices"],m["other_keys"]),("IndexError",["choices","id"],0,0))
        self.assertNotIn("error_code",m)

    def test_odd_error_codes_and_unknown_keys_stay_out(self):
        for code in ("502",True,10**6,CANARY,None):
            with self.subTest(code=code):
                m=self.meta(json.dumps({"error":{"code":code},CANARY:1}).encode())
                self.assertEqual((m["body_keys"],m["other_keys"]),(["error"],1))
                self.assertNotIn("error_code",m)

    def test_a_body_that_is_not_an_object_is_only_its_kind(self):
        m=self.meta(b'["x"]')
        self.assertEqual((m["kind"],m["exc"]),("shape","TypeError"))
        self.assertNotIn("body_keys",m)

    def test_retry_count_and_answer_unchanged_after_a_provider_error(self):
        value,error,messages,records,n=self.call([b'{"error":{"code":502}}',envelope(GOOD)])
        self.assertEqual((value,error,records,n),({"price":100},None,[False,True],2))
