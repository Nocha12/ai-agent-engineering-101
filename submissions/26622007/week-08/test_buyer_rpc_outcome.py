"""Unreadable remote replies never erase an offer or cause a second send."""
import contextlib
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import buyer
import oneshot


class RpcOutcome(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 502
        self.payload = b"<html>fixture-private-body-do-not-log</html>"
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.requests.append(body)
                self.send_response(case.status)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(case.payload)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = "http://127.0.0.1:%s/" % self.server.server_port
        self.task = {"id": "fixture-task-85324533", "status": {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "message": {"parts": [{"text": "Asking 11352. Send your offer as a number."}]}}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def run_trade(self):
        log = io.StringIO()
        with mock.patch.object(buyer, "decide", side_effect=AssertionError("model forbidden")), contextlib.redirect_stderr(log):
            out = buyer.negotiate(self.url, "fixture food", 14672, resume=(self.url, self.task),
                ladder=lambda meta, text, history: oneshot.code_turn(11352, history, 14672, (.9,)))
        return out, log.getvalue()

    def test_non_json_reply_keeps_the_sent_offer_and_unknown_outcome_without_resend(self):
        out, log = self.run_trade()
        self.assertEqual(out["state"], "UNKNOWN_RPC_OUTCOME")
        self.assertEqual(out["task"], "fixture-task-85324533")
        self.assertEqual(out["history"], [("shop asked", 11352), ("you offered", 10216)])
        self.assertIsNone(out["receipt"])
        self.assertIsNone(out["agreed"])
        self.assertEqual(out["rpc_reply_error"]["status"], 502)
        self.assertEqual(out["rpc_reply_error"]["method"], "SendMessage")
        self.assertEqual(out["rpc_reply_error"]["json_pos"], 0)
        self.assertTrue(out["rpc_reply_error"]["stack"])
        self.assertNotIn("fixture-private-body", log)
        self.assertEqual([r["method"] for r in self.requests], ["SendMessage"])
        message = self.requests[0]["params"]["message"]
        self.assertEqual(message["taskId"], "fixture-task-85324533")
        self.assertEqual(message["parts"], [{"text": "10216"}])

    def test_unknown_remote_result_gets_neutral_factual_review_not_no_price(self):
        import market_buyer
        out, _ = self.run_trade()
        stars, review = market_buyer.rate(out, [], "fixture food", 20000, limit=14672)
        self.assertEqual(stars, 3)
        self.assertIn("unconfirmed", review)
        self.assertNotIn("No price offered", review)

    def test_http_200_with_empty_body_is_still_unknown_without_retry(self):
        self.status, self.payload = 200, b""
        out, _ = self.run_trade()
        self.assertEqual(out["state"], "UNKNOWN_RPC_OUTCOME")
        self.assertEqual(out["rpc_reply_error"]["status"], 200)
        self.assertEqual(out["rpc_reply_error"]["bytes"], 0)
        self.assertEqual(out["history"][-1], ("you offered", 10216))
        self.assertEqual(len(self.requests), 1)

    def test_valid_completed_reply_keeps_normal_receipt_and_no_error_fields(self):
        self.status = 200
        self.payload = json.dumps({"jsonrpc": "2.0", "result": {
            "id": "fixture-task-85324533", "status": {"state": "TASK_STATE_COMPLETED",
                "message": {"parts": [{"text": "Deal"}]}},
            "artifacts": [{"parts": [{"data": {"price": 10216, "item": "G3e1d2f55"}}]}]}}).encode()
        out, _ = self.run_trade()
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["receipt"]["price"], 10216)
        self.assertEqual(out["agreed"], 10216)
        self.assertNotIn("rpc_reply_error", out)
        self.assertEqual(len(self.requests), 1)

    def test_unreadable_cancel_reply_does_not_retry_or_create_an_offer(self):
        with mock.patch.object(buyer, "decide", side_effect=AssertionError("model forbidden")), \
                contextlib.redirect_stderr(io.StringIO()):
            out = buyer.negotiate(self.url, "fixture food", 14672, resume=(self.url, self.task),
                                  stop_if=lambda text, turn: "fixture stop before offer")
        self.assertEqual(out["state"], "UNKNOWN_RPC_OUTCOME")
        self.assertEqual(out["rpc_reply_error"]["method"], "CancelTask")
        self.assertEqual(out["history"], [])
        self.assertIsNone(out["receipt"])
        self.assertEqual([r["method"] for r in self.requests], ["CancelTask"])

    def test_order_record_keeps_unknown_task_offer_and_safe_response_metadata(self):
        import market_buyer
        from types import SimpleNamespace
        out, _ = self.run_trade()
        row = market_buyer.order_record(
            {"order": "fixture-order", "kind": "food"}, SimpleNamespace(budget=14672),
            [], {}, "fixture-shop", "rule", {"name": "fixture-shop"}, "rule", {},
            14672, 0, out, None, {"receipt": None}, [], [], False, None, {}, {})
        self.assertEqual(row["state"], "UNKNOWN_RPC_OUTCOME")
        self.assertEqual(row["task"], "fixture-task-85324533")
        self.assertEqual(row["our_offers"], [10216])
        self.assertEqual(row["asks"], [11352])
        self.assertFalse(row["passed_to_kit"])
        self.assertEqual(row["rpc_reply_error"], out["rpc_reply_error"])
        self.assertNotIn("fixture-private-body", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
