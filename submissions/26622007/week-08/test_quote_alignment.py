"""Authorized quote and offline measurement seams; no live services."""
import json
import os
import tempfile
import unittest
from unittest import mock

import buyer
import market_buyer as mb
import oneshot
from test_buyer_reply import task

SKU = "G12345678"
TEXT = (f"완도 미역 ({SKU}), grade A, market certificate included, "
        "21 hours of shelf life left, list price 10640. Send the ask or say ok to buy. Our ask: 8510")


class QuoteAlignment(unittest.TestCase):
    def test_treatment_reads_observed_quote_and_preserves_first_turn_guard(self):
        import buyer_quote_alignment as align
        with tempfile.TemporaryDirectory() as md:
            env = {"BUYER_QUOTE_ALIGN": "1", "BUYER_QUOTE_ALIGN_START": "1000",
                   "BUYER_QUOTE_ALIGN_STUDY": "local-test"}
            with mock.patch.dict(os.environ, env), mock.patch.object(align.time, "time", return_value=1001):
                # Find a treatment reservation through the public assignment boundary.
                for i in range(30):
                    order = {"order": f"o{i}", "kind": "food", "_buyer_started_at": 1001}
                    context = align.reserve(md, order)
                    if context["arm"] == "B":
                        break
                self.assertEqual(context["arm"], "B")
                order["_quote_alignment"] = context
                shop = {"name": "fixture", "url": "https://fixture.invalid",
                        "_offer": {"sku": SKU, "list_price": 10640}}
                card = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "fixture"}]}
                with mock.patch.object(mb, "serves_acp", return_value=True), \
                        mock.patch.object(mb.httpx, "get", return_value=mock.Mock(json=lambda: card)), \
                        mock.patch.object(mb, "buyer_send", return_value=task(TEXT)):
                    out = {}
                    mb.quote(shop, "미역", SKU, order, out, mb.quote_ctl())
                self.assertEqual(out["fixture"]["ask"], 8510)
                self.assertIsNone(buyer.text_ask(TEXT))
                self.assertEqual(oneshot.turn(None, TEXT, [], 7447, quoted=8510)["price"], 7447)
                self.assertIsNone(oneshot.turn(None, TEXT, [], 7447, quoted=None))
                rows = [json.loads(x) for x in open(os.path.join(md, align.JOURNAL))]
                self.assertTrue(any(r.get("event") == "quote" and r.get("applied") for r in rows))
