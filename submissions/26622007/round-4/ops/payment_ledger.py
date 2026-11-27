#!/usr/bin/env python3
"""The shop's payment ledger: one line per money event, appended and never rewritten, and a check against the chain.

The kit v5 keeps no payment record of its own: incoming payments are not logged at all and the shop wallet's own
transfers leave one line in logs/agent-kit.log, a log that rotates. This script rebuilds the record from sources that
do not change after the fact:

- the market's hash-chained ledger (GET /api/ledger): the start capital (grant), every lot the shop paid for (supply,
  with its storage and forum fees), every sale (settle, with the shop's fee and the buyer's tx_hash) and every unit
  that broke after a sale (broken; in Round 4 the market refunds nothing);
- the shop wallet's transfers that the kit sent (the "[maroo] <tool> sent ... from the shop wallet: <tx_url>" lines),
  read back from the chain for the exact amount and the gas, so refunds and other transfers are recorded too;
- the agent's own send records (logs/sends.jsonl, logs/refunds.jsonl: an intent line written before each kit.pay and
  the tx hash after it), so a transfer whose receipt wait timed out (no kit log line) is still found;
- the agent's sale records (logs/sales.jsonl: order, amount, shop_fee, tx_hash, warranty_hours from the settle
  receipt), because a sealed sale (Patch 4.1) shows only its order and shop on the public ledger.

A shop-wallet transfer to the wallet that paid one of our sales, or one the agent's records tie to an order, is written
as kind "refund" with that order. reconcile raises alerts (logs/alerts.jsonl, chained like the others) for a balance
that does not match, a second refund for one order, a refund to a wallet that paid none of our sales or to no single
order, a new "broken" row for our shop, a sealed sale with no sale record, and an intent with no tx hash after 10
minutes; a watcher outside the market account reads that file and tells the operator.

    python3 -I payment_ledger.py sync      --dir <round-4 dir> [--market URL] [--name nocha12]
    python3 -I payment_ledger.py reconcile --dir <round-4 dir> [--market URL] [--name nocha12]
    python3 -I payment_ledger.py verify    --dir <round-4 dir>

sync appends new events to logs/payments.jsonl. reconcile runs sync, then compares the ledger's balance with the shop
wallet's balance on the chain (in Round 4 the market keeps no balance of its own: the wallet is the shop's credits,
and GET /api/inventory reports the same number) and appends the result to logs/reconcile.jsonl; it exits 2 when they
differ by more than --tolerance credits. verify checks the hash chain of both files. Standard library only.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

MARKET = "https://market.ourolabs.ai"
RPC = "https://rpc-testnet.maroo.io"
CREDIT_WEI = 10 ** 15                       # 1,000 credits = 1 tOKRW
GENESIS = "0" * 64
HEX_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")
INTENT_GRACE = 600                          # seconds an intent may wait for its tx hash before it is an alert
SCAN_BLOCKS = 1500                          # blocks read per reconcile while the wallet holds more than the ledger
SEALED_WAIT = 7200                          # seconds a payment waits on its buyer's unrecorded sealed sale (recover_sealed: 6 x 20 min)
INFLOW_STATE = ".inflow-scan.json"          # logs/: where that scan resumes, and the payments it is still waiting on
SENT = re.compile(r"^(\d\d:\d\d:\d\d) \[maroo\] (\S+) sent \S+ tOKRW from the shop wallet: \S*/tx/(0x[0-9a-fA-F]{64})")


def http_json(url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", "user-agent": "nocha12-ledger"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Chain:
    """Maroo JSON-RPC, read only."""
    def __init__(self, rpc=RPC):
        self.rpc = rpc

    def call(self, method, params):
        out = http_json(self.rpc, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if out.get("error"):
            raise RuntimeError(f"{method}: {out['error']}")
        return out.get("result")

    def balance_wei(self, address):
        return int(self.call("eth_getBalance", [address, "latest"]), 16)

    def transfer(self, tx_hash):
        """{from, to, value_wei, gas_wei, ok} of a mined transaction, or None while it is not mined."""
        tx, rc = self.call("eth_getTransactionByHash", [tx_hash]), self.call("eth_getTransactionReceipt", [tx_hash])
        if not tx or not rc:
            return None
        price = int(rc.get("effectiveGasPrice") or tx.get("gasPrice") or "0x0", 16)
        block = self.call("eth_getBlockByNumber", [rc["blockNumber"], False]) if rc.get("blockNumber") else None
        return {"from": (tx.get("from") or "").lower(), "to": (tx.get("to") or "").lower(), "value_wei": int(tx["value"], 16),
                "gas_wei": int(rc["gasUsed"], 16) * price, "ok": rc.get("status") == "0x1",
                "ts": int(block["timestamp"], 16) if block and block.get("timestamp") else None}


    def receipt_ok(self, tx_hash):
        """True / False by the receipt status of a mined tx, None while the node has no receipt for it."""
        rc = self.call("eth_getTransactionReceipt", [tx_hash])
        return None if not rc else rc.get("status") == "0x1"

    def block_at(self, ts):
        """The first block at or after ts (binary search over block timestamps)."""
        hi = int(self.call("eth_blockNumber", []), 16)
        lo = max(0, hi - 2_000_000)
        while lo < hi:
            m = (lo + hi) // 2
            if int(self.call("eth_getBlockByNumber", [hex(m), False])["timestamp"], 16) < ts:
                lo = m + 1
            else:
                hi = m
        return lo

    def batch(self, calls):
        """Several JSON-RPC calls in one request, results in call order (an error result raises)."""
        out = http_json(self.rpc, [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)], timeout=60)
        if not isinstance(out, list):
            raise RuntimeError(f"batch: {out}")
        by = {r.get("id"): r for r in out}
        res = []
        for i in range(len(calls)):
            r = by.get(i) or {}
            if r.get("error") or "result" not in r:
                raise RuntimeError(f"{calls[i][0]}: {r.get('error') or 'no result'}")
            res.append(r["result"])
        return res

    def inflows(self, address, start, max_blocks, step=50, budget=300.0):
        """Transfers to address in blocks start .. start+max_blocks-1 (not past the head, at most budget seconds):
        ([(tx_hash, from, value_wei, input hex without 0x, ok, block time)], next block to read, head).
        ok is None while the receipt is not readable yet (the caller waits). A block the node does not return yet
        ends the step there, so the next call reads it again; an error after some blocks keeps what was read."""
        me, out = address.lower(), []
        head = int(self.call("eth_blockNumber", []), 16)
        end = min(head, start + max_blocks - 1)
        n, t0 = start, time.time()
        while n <= end and time.time() - t0 < budget:
            try:
                blocks = self.batch([("eth_getBlockByNumber", [hex(b), True]) for b in range(n, min(end, n + step - 1) + 1)])
                if None in blocks:
                    blocks = blocks[:blocks.index(None)]
                    stop = True
                else:
                    stop = False
                mine = [(tx, int(b["timestamp"], 16)) for b in blocks for tx in b.get("transactions") or []
                        if isinstance(tx, dict) and (tx.get("to") or "").lower() == me]
                rcs = self.batch([("eth_getTransactionReceipt", [tx["hash"]]) for tx, _ in mine]) if mine else []
            except Exception:
                if n == start:
                    raise
                break
            for (tx, ts), rc in zip(mine, rcs):
                status = (rc or {}).get("status")
                out.append((tx["hash"].lower(), (tx.get("from") or "").lower(), int(tx["value"], 16),
                            (tx.get("input") or "")[2:].lower(), None if not rc else status == "0x1", ts))
            n += len(blocks)
            if stop:
                break
        return out, n, head

    def payments_to(self, address, t0, t1, limit=400):
        """[(tx_hash, from, value_wei, input hex without 0x, ok, block time)] sent to address in blocks with t0 <= time <= t1."""
        me, out, n = address.lower(), [], self.block_at(t1 + 1) - 1
        for _ in range(limit):
            b = self.call("eth_getBlockByNumber", [hex(n), True]) if n >= 0 else None
            if not b or int(b["timestamp"], 16) < t0:
                break
            for tx in b.get("transactions") or []:
                if isinstance(tx, dict) and (tx.get("to") or "").lower() == me:
                    rc = self.call("eth_getTransactionReceipt", [tx["hash"]]) or {}
                    out.append((tx["hash"].lower(), (tx.get("from") or "").lower(), int(tx["value"], 16),
                                (tx.get("input") or "")[2:].lower(), rc.get("status") == "0x1", int(b["timestamp"], 16)))
            n -= 1
        return out


def recover_sealed(d, rows, chain, address, pending):
    """A buyer can settle our sealed checkout with the market without the agent (it never hears of it), so the sale has
    no line in logs/sales.jsonl. For each such order, find the buyer's transfer to our wallet between the order row and
    the settle row whose data is the digest of a checkout we issued (logs/checkouts.jsonl), and write the sale line the
    agent would have written. Returns the orders recovered.
    The payment must come from the wallet the order's buyer joined with (join row pubkey): several customers can pay
    sealed checkouts within the same minute, and the checkout digest alone does not say which order paid it (11/26
    20:05 three orders in 45 s took each other's payments). Among that buyer's matching payments the one closest to
    the order row wins. An order whose buyer key is unknown is left for the agent (no guess). Two names joined with
    one wallet are not told apart by the key; the time to the order row decides between them."""
    issued = {}
    for _, r in jsonl(d / "logs" / "checkouts.jsonl"):
        if r.get("event") == "issued" and r.get("digest_hex"):
            issued[str(r["digest_hex"]).lower()] = r
    if not issued or not pending:
        return []
    order_ts = {r["data"].get("order"): r["ts"] for r in rows if r["kind"] == "order"}
    order_buyer = {r["data"].get("order"): r["data"].get("buyer") for r in rows if r["kind"] == "order"}
    keys = {}
    for r in rows:
        if r["kind"] == "join" and r["data"].get("pubkey") and r["data"].get("name"):
            keys.setdefault(r["data"]["name"], set()).add(str(r["data"]["pubkey"]).lower())
    have = agent_sales(d)
    used = {str(s.get("tx_hash") or "").lower() for s in have.values()}
    for _, r in jsonl(d / "logs" / "checkouts.jsonl"):
        if r.get("event") == "unsettled" and r.get("tx_hash"):
            used.add(str(r["tx_hash"]).lower())          # a payment we owe back, not a sale
    for name in ("sends.jsonl", "refunds.jsonl"):
        for _, r in jsonl(d / "logs" / name):
            if r.get("paid_tx"):
                used.add(str(r["paid_tx"]).lower())
    tried_path = d / "logs" / ".sealed-tried.json"
    try:
        tried = json.loads(tried_path.read_text())
    except (OSError, ValueError):
        tried = {}
    now = time.time()
    done = []
    for p in pending:
        o = p.get("order")
        if not o or p.get("why") or str(o) in have:
            continue
        buyer_keys = keys.get(order_buyer.get(o)) or set()
        if not buyer_keys:
            continue                                 # no join key for this buyer yet: no guess, and no try used up
        last, n = tried.get(str(o), [0, 0])
        if n >= 6 or now - last < 1200:
            continue
        tried[str(o)] = [now, n + 1]
        anchor = order_ts.get(o, p["ts"])
        t0 = order_ts.get(o, p["ts"] - 900) - 30
        try:
            pays = chain.payments_to(address, t0, p["ts"] + 30)
        except Exception:
            continue
        found = []
        for pay in pays:
            tx, frm, wei, data, ok = pay[:5]
            c = issued.get(data)
            if not ok or not c or tx in used or str(frm).lower() not in buyer_keys:
                continue
            price = int(c.get("price") or 0)
            fee = int(price * 0.01) + 210
            if wei != (price - fee) * CREDIT_WEI:
                continue
            found.append((abs(pay[5] - anchor) if len(pay) > 5 and pay[5] is not None else 0, len(found), tx, frm, data, c, price, fee))
        for _, _, tx, frm, data, c, price, fee in sorted(found)[:1]:
            rec = {"ts": int(p["ts"]), "order": o, "checkout_order": c.get("order"), "sku": c.get("sku"),
                   "unit": c.get("unit"), "grade": c.get("grade"), "warranty_hours": c.get("warranty_hours"),
                   "amount": price, "shop_fee": fee, "tx_hash": tx, "payer": frm, "sealed": True, "ledger": p.get("seq"),
                   "checkout_digest_hex": data,
                   "source": "payment_ledger: the buyer settled our sealed checkout with the market directly"}
            with open(d / "logs" / "sales.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            used.add(tx)
            done.append(o)
            break
    try:
        tried_path.write_text(json.dumps(tried))
    except OSError:
        pass
    return done


def market_rows(market, after=0):
    rows = []
    while True:
        page = http_json(f"{market}/api/ledger?after={after}")
        if not page:
            return rows
        rows += page
        after = page[-1]["seq"]
        if len(page) < 500:
            return rows


class TornTail(Exception):
    """The file's last line was cut off (a crash mid-write): it must be looked at before anything is appended."""


def read_lines(path):
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise TornTail(f"{path.name}: the last line has no newline (cut off mid-write); move it aside by hand")
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def line_hash(prev, rec):
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256((prev + json.dumps(body, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def append(path, recs):
    """Append records to an append-only JSONL file, each chained to the one before by sha256."""
    old = read_lines(path)
    prev = old[-1]["hash"] if old else GENESIS
    out = []
    for rec in recs:
        rec = dict(rec, prev=prev)
        rec["hash"] = prev = line_hash(prev, rec)
        out.append(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    if not out:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:          # one write, then fsync: a crash leaves at most a torn tail
        f.write("".join(out))
        f.flush()
        os.fsync(f.fileno())


class Lock:
    """One sync at a time (cron and a manual run may overlap): an exclusive flock on logs/.payments.lock."""
    def __init__(self, d):
        (d / "logs").mkdir(parents=True, exist_ok=True)
        self.f = open(d / "logs" / ".payments.lock", "w")

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.close()
            raise
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def verify(path):
    """Problems in the hash chain of one append-only file ([] when it is intact)."""
    prev, bad = GENESIS, []
    try:
        lines = read_lines(path)
    except (TornTail, ValueError) as e:
        return [f"{path.name}: {e}"]
    for i, rec in enumerate(lines, 1):
        if rec.get("prev") != prev or rec.get("hash") != line_hash(prev, rec):
            bad.append(f"{path.name}:{i} does not follow the line before it (edited, removed or reordered)")
        prev = rec.get("hash", "")
    return bad


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def kit_sends(d):
    """Shop-wallet transfers the kit logged, oldest first: [(hh:mm:ss, tool, tx_hash)] from the log and its rotation."""
    out = []
    for name in ("agent-kit.log.1", "agent-kit.log"):
        p = d / "logs" / name
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = SENT.match(line)
                if m:
                    out.append((m.group(1), m.group(2), m.group(3).lower()))
    return out


def jsonl(path):
    """The JSON objects of a file the agent writes (loosely: bad lines are skipped), with their line numbers."""
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append((n, r))
    return out


def epoch(v, fallback):
    """Seconds since the epoch from a number or an ISO time; fallback when it is neither."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            from datetime import datetime
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def agent_sales(d):
    """{order: the agent's record of that sale} from logs/sales.jsonl (the last line for an order wins)."""
    return {str(r.get("order")): r for _, r in jsonl(d / "logs" / "sales.jsonl") if r.get("order")}


def agent_sends(d):
    """What the agent wrote around its own transfers: ({tx_hash: {order, purpose, source}}, [intents with no tx hash]).
    The agent writes these files itself, so the shape is read loosely: one JSON object a line; tx_hash or tx; order,
    order_id or id; purpose or kind. A line with a nonce or an amount and no tx hash is an intent; it is open until a
    later line names the same intent id (or the same order and purpose) with a tx hash."""
    txs, intents = {}, []
    for name in ("sends.jsonl", "refunds.jsonl"):
        p = d / "logs" / name
        mtime = p.stat().st_mtime if p.exists() else 0
        for n, r in jsonl(p):
            tx = str(r.get("tx_hash") or r.get("tx") or "").lower()
            order = r.get("order") or r.get("order_id")
            purpose = str(r.get("purpose") or r.get("kind") or ("refund" if name == "refunds.jsonl" else "")).lower()
            key = (r.get("intent") or r.get("id") or order, purpose)
            if HEX_TX.match(tx):
                # refund_send writes order = our checkout id and market_order = the market's order it refunds (11/28:
                # one checkout, two market orders from one wallet); both are kept so the refund can name its order
                txs[tx] = {"order": order, "market_order": r.get("market_order") or r.get("market_order_id"), "purpose": purpose,
                           "source": f"{name}:{n}"}
                intents = [i for i in intents if i["key"] != key]
            elif any(k in r for k in ("nonce", "amount", "amount_wei", "wei")):
                # no time on the line: the file's time (goal (9) asks for ts; a later append makes this one look newer)
                intents.append({"key": key, "order": order, "purpose": purpose, "source": f"{name}:{n}",
                                "ts": epoch(r.get("ts") or r.get("time"), mtime)})
    return txs, intents


def events(rows, name, sends, chain, address, seen=(), seen_tx=None, hints=None, sales=None, pending=None):
    """The shop's new money events from the market ledger rows and the kit's logged transfers, in ledger order. Amounts
    are whole wei in delta_wei/gas_wei. seen: ids already in the file (skipped, not looked up again); seen_tx: tx_hash
    -> the line already written for it, so a lot whose transfer was first written as transfer_out is not counted twice.
    hints: agent_sends()[0]; its tx hashes are read like the kit's lines, and its orders name refunds.
    sales: agent_sales(); fills in a sealed sale. pending: a list that gets the sealed sales of ours with no record yet."""
    sales = sales or {}
    seen_tx = dict(seen_tx or {})
    me = address.lower()
    urls, who = set(), {}
    for r in rows:
        dt = r["data"]
        if r["kind"] == "join":
            if dt.get("pubkey"):
                who[dt["pubkey"].lower()] = dt.get("name")
            if dt.get("name") == name and dt.get("role") == "shop" and dt.get("url"):
                urls.add(dt["url"].rstrip("/"))
    out = []

    def add(e):
        out.append(e)
        if e.get("tx_hash"):
            seen_tx[e["tx_hash"].lower()] = e

    for r in rows:
        dt, k = r["data"], r["kind"]
        base = {"ts": r["ts"], "time": iso(r["ts"]), "round": r.get("round"), "source": f"market#{r['seq']}"}
        if k == "grant" and dt.get("shop") == name:
            if not dt.get("tx_hash"):
                continue                             # a grant still waiting: the market writes a new row with its tx_hash when sent
            eid = f"grant-{dt['tx_hash'].lower()}"
            if eid in seen:
                continue
            c = int(dt.get("credits") or 0)
            prior, delta, note = seen_tx.get(dt["tx_hash"].lower()), c * CREDIT_WEI, None
            if prior is not None and prior.get("kind") == "transfer_in":
                delta -= prior["delta_wei"]          # the inflow scan wrote the grant's transfer before the market row came
                note = f"reclassifies {prior['id']}: the market recorded the grant later"
            add(dict(base, kind="grant", id=eid, counterparty="market", item=dt.get("why"), qty=None, unit_price=None,
                     amount=c, fee=0, storage=0, forum_fees=0, delta_wei=delta, gas_wei=0, tx_hash=dt["tx_hash"].lower(),
                     note=note))
        elif k == "supply" and dt.get("shop") == name:
            tx = (dt.get("tx_hash") or "").lower() or None
            eid = f"supply-{tx or r['seq']}"
            if eid in seen:
                continue
            goods, storage, fees, qty = int(dt.get("total") or 0), int(dt.get("storage") or 0), int(dt.get("fees") or 0), int(dt.get("qty") or 0)
            delta, gas, note = -(goods + storage + fees) * CREDIT_WEI, 0, None
            prior = seen_tx.get(tx) if tx else None
            if prior is not None and prior.get("kind") == "transfer_out":
                delta -= prior["delta_wei"]          # that line already took the transfer (and its gas) off the balance
                note = f"reclassifies {prior['id']}: the lot's payment was written as a transfer before the market recorded the lot"
            elif tx and chain:
                t = chain.transfer(tx)
                if t is None:
                    continue                         # not readable yet: the next sync writes it
                gas = t["gas_wei"] if t["from"] == me else 0
            add(dict(base, kind="supply", id=eid, counterparty="market wholesale", item=dt.get("sku"), qty=qty,
                     unit_price=round(goods / qty, 2) if qty else None, amount=goods, fee=0, storage=storage,
                     forum_fees=fees, delta_wei=delta, gas_wei=gas, tx_hash=tx, units=dt.get("units"), note=note))
        elif k == "settle" and (dt.get("shop") or "").rstrip("/") in urls:
            eid = f"sale-{dt.get('order') or r.get('hash') or r['seq']}"
            if eid in seen:
                continue
            sealed = "amount" not in dt
            if sealed:                               # Patch 4.1: the public row names the order and the shop only
                rec = sales.get(str(dt.get("order")))
                if not rec or rec.get("amount") is None or rec.get("shop_fee") is None:
                    if pending is not None:
                        pending.append({"order": dt.get("order"), "ts": r["ts"], "seq": r.get("seq")})
                    continue                         # written once the agent's sale record has it
                dt = dict(dt, amount=rec["amount"], shop_fee=rec["shop_fee"], tx_hash=rec.get("tx_hash"),
                          warranty_hours=rec.get("warranty_hours"))
            amount, fee = int(dt.get("amount") or 0), int(dt.get("shop_fee") or 0)
            tx = (dt.get("tx_hash") or "").lower() or None
            payer = None
            if tx and chain:
                t = chain.transfer(tx)
                if t is None:
                    if pending is not None:
                        pending.append({"order": dt.get("order") or r.get("hash") or r["seq"], "ts": r["ts"], "why": "unreadable"})
                    continue                         # not readable yet: the next sync writes it with its payer
                payer = t["from"]
            delta, note = (amount - fee) * CREDIT_WEI, None
            prior = seen_tx.get(tx) if tx else None
            if prior is not None and prior.get("kind") in ("paid_unsettled", "transfer_in"):
                delta -= prior["delta_wei"]          # that line already put the payment on the balance
                note = f"reclassifies {prior['id']}: the market settled the payment later"
                payer = prior.get("payer") or payer
            add(dict(base, kind="sale", id=eid, counterparty=dt.get("buyer"),
                     item=f"{dt.get('sku') or dt.get('item')} {dt.get('name') or ''}".strip(), qty=1, unit_price=amount,
                     amount=amount, fee=fee, storage=0, forum_fees=0, delta_wei=delta, gas_wei=0,
                     tx_hash=tx, grade=dt.get("grade"), claim=dt.get("claim"), payer=payer, sealed=sealed or None,
                     warranty_hours=dt.get("warranty_hours"), unit=dt.get("unit"), note=note))
        elif k == "broken" and (dt.get("shop") or "").rstrip("/") in urls:
            eid = f"broken-{dt.get('order') or r['seq']}"
            if eid in seen:
                continue
            sale = sales.get(str(dt.get("order"))) or {}
            add(dict(base, kind="broken", id=eid, counterparty=dt.get("buyer"), item=dt.get("item"), qty=1, unit_price=None,
                     amount=0, fee=0, storage=0, forum_fees=0, delta_wei=-int(dt.get("refunded") or 0) * CREDIT_WEI,
                     gas_wei=0, tx_hash=None, warranty_hours=dt.get("warranty_hours", sale.get("warranty_hours")),
                     hours_after_sale=dt.get("hours_after_sale"), unit=dt.get("unit")))
    hints = hints or {}

    def orders_paid_by(addr):
        """Our sale orders paid from this wallet: the payer is kept on each sale line, so the chain is read once a sale."""
        found = {e["id"]: e for e in list(seen_tx.values()) + out}      # add() puts a new line in both
        return sorted({i[5:] if e.get("kind") == "sale" else str(e.get("order") or e.get("checkout_order")) for i, e in found.items()
                       if e.get("payer") == addr and (e.get("kind") == "sale"
                                                      or (e.get("kind") == "paid_unsettled" and (e.get("order") or e.get("checkout_order"))))})

    def owed_of_checkout(addr, checkout):
        """The market orders of this wallet's paid_unsettled lines on our checkout id: the shop's refund_send names only the
        checkout id (order), and one checkout can carry two market orders from one wallet (11/28 9,290 and 2,632)."""
        found = list(seen_tx.values()) + out
        return sorted({str(e["order"]) for e in found if e.get("kind") == "paid_unsettled" and e.get("payer") == addr
                       and e.get("order") and str(e.get("checkout_order")) == checkout})

    queue = [(hhmmss, f"kit:{tool}", tx) for hhmmss, tool, tx in sends]
    queue += [(None, h["source"], tx) for tx, h in hints.items() if tx not in {q[2] for q in queue}]
    for hhmmss, source, tx in queue:
        if tx in seen_tx or f"out-{tx}" in seen or f"refund-{tx}" in seen or chain is None:
            continue
        t = chain.transfer(tx)
        if t is None or t["from"] != me:
            continue                                 # not mined yet (the next sync finds it) or not the shop wallet's
        value = t["value_wei"] if t["ok"] else 0     # a failed transaction moves no value but still burns its gas
        hint = hints.get(tx) or {}
        paid = orders_paid_by(t["to"])
        said_refund = "refund" in (hint.get("purpose") or "")
        refund = t["ok"] and bool(paid)              # only a transfer to a wallet that paid one of our sales
        said = [str(o) for o in (hint.get("market_order"), hint.get("order")) if o not in (None, "")]   # market order first
        if said and said[0] not in paid and not hint.get("market_order"):
            owed = owed_of_checkout(t["to"], said[0])     # a checkout id alone: its one owed market order of this wallet
            said = owed[:1] + said if len(owed) == 1 else said
        order = next((o for o in said if o in paid), None) or (paid[0] if len(paid) == 1 else None)
        disagrees = bool(said) and refund and order != said[0]   # the most exact id the agent gave is not the order found
        add({"ts": t.get("ts"), "time": iso(t["ts"]) if t.get("ts") else hhmmss, "round": None, "source": source,
             "kind": "refund" if refund else "transfer_out", "id": f"{'refund' if refund else 'out'}-{tx}",
             "counterparty": who.get(t["to"], t["to"]), "item": None, "order": order,
             "orders_of_payer": paid or None, "said_refund": said_refund or None,
             "said_order": said[0] if disagrees else None, "qty": None, "unit_price": None, "amount": value / CREDIT_WEI, "fee": 0,
             "storage": 0, "forum_fees": 0, "delta_wei": -value, "gas_wei": t["gas_wei"], "tx_hash": tx, "ok": t["ok"]})
    return out


def sync(d, market, name, chain, address, pending=None):
    """Append the new events to logs/payments.jsonl (call under Lock). Returns (new lines, balance in wei).
    pending: a list that gets our sealed sales the agent has not recorded yet (they wait, and reconcile tells)."""
    path = d / "logs" / "payments.jsonl"
    old = read_lines(path)
    seen = {e["id"] for e in old}
    seen_tx = {}
    fixed = {str(e["order"]): e for e in old if e.get("kind") == "sale_correction" and e.get("order")}
    for e in old:
        if e.get("kind") == "sale" and e["id"][5:] in fixed:   # rca1126: the sale's payer/amount as corrected
            f = fixed[e["id"][5:]]
            e = dict(e, payer=f.get("payer"), amount=f.get("amount"), fee=f.get("fee"),
                     tx_hash=f.get("corrected_tx") or e.get("tx_hash"), corrected_by=f["id"])
        if e.get("tx_hash"):
            seen_tx[e["tx_hash"]] = e
    balance = old[-1]["balance_wei"] if old else 0
    new = []
    rows = market_rows(market)
    probe = []
    list(events(rows, name, kit_sends(d), chain, address, seen, seen_tx, agent_sends(d)[0], agent_sales(d), probe))
    if chain is not None and hasattr(chain, "payments_to") and any(not q.get("why") for q in probe):
        recover_sealed(d, rows, chain, address, probe)
    for e in events(rows, name, kit_sends(d), chain, address, seen, seen_tx, agent_sends(d)[0],
                    agent_sales(d), pending):
        balance += e["delta_wei"] - e["gas_wei"]
        new.append(dict(e, delta=e["delta_wei"] / CREDIT_WEI, gas=e["gas_wei"] / CREDIT_WEI, balance_wei=balance,
                        balance_after=balance / CREDIT_WEI))
    append(path, new)
    return new, balance


def _last_ok_ts(d, fallback):
    for r in reversed(read_lines(d / "logs" / "reconcile.jsonl")):
        if r.get("ok"):
            return float(r["ts"])
    old = read_lines(d / "logs" / "payments.jsonl")
    return float(old[-1]["ts"]) - 60 if old and isinstance(old[-1].get("ts"), (int, float)) else fallback


def scan_inflows(d, market, name, chain, address, now, scan_blocks, from_ts):
    """balance-diff-rca-1127: money that came into the wallet with no line in the ledger. While the wallet holds more
    than the ledger, read the wallet's incoming transfers (a bounded step a run, resuming where the last run stopped)
    and write each unaccounted one:
    - paid_unsettled: a customer paid one of our checkouts (its data is the digest of a checkout we issued) and its
      order expired with no settle at our shop. The money is ours to hold and owed back or to settle; nothing is sent.
      (11/27 11:19 김상용-d1c2 paid 13,126 for checkout 7c30754a; its kit never called /api/settle.)
    - transfer_in: a transfer with no checkout of ours, after INTENT_GRACE.
    A payment whose order is still open, or that settled at our shop (the sale line counts it), waits.
    Returns (new events without balances, [waiting (tx, why)])."""
    if not hasattr(chain, "inflows"):
        return [], []
    path = d / "logs" / INFLOW_STATE
    try:
        st = json.loads(path.read_text())
    except (OSError, ValueError):
        st = {}
    start = st.get("next")
    if not isinstance(start, int):
        start = chain.block_at(int(from_ts) - 120)
    got, nxt, _ = chain.inflows(address, start, scan_blocks)
    cand = {p[0]: list(p) for p in st.get("waiting") or []}
    for p in got:
        cand[p[0]] = list(p)                         # a fresh read wins over a stored one
    for p in cand.values():
        if p[4] is None and hasattr(chain, "receipt_ok"):
            try:
                p[4] = chain.receipt_ok(p[0])        # its block is behind the cursor: read the receipt again
            except Exception:
                pass
    known = set()
    for e in read_lines(d / "logs" / "payments.jsonl"):
        for k in ("tx_hash", "corrected_tx"):
            if e.get(k):
                known.add(str(e[k]).lower())
    have = agent_sales(d)
    for _, r in jsonl(d / "logs" / "sales.jsonl"):
        for k in ("tx_hash", "corrected_tx"):
            if r.get(k):
                known.add(str(r[k]).lower())
    issued = {str(r["digest_hex"]).lower(): r for _, r in jsonl(d / "logs" / "checkouts.jsonl")
              if r.get("event") == "issued" and r.get("digest_hex")}
    rows = market_rows(market)
    urls, who, orders = set(), {}, {}
    for r in rows:
        dt = r["data"]
        if r["kind"] == "join":
            if dt.get("pubkey") and dt.get("name"):
                who[str(dt["pubkey"]).lower()] = dt["name"]           # the last name joined with a key, as events() does
            if dt.get("name") == name and dt.get("role") == "shop" and dt.get("url"):
                urls.add(dt["url"].rstrip("/"))
    order_buyer, unrecorded = {}, {}
    for r in rows:
        dt = r["data"]
        if r["kind"] == "order" and dt.get("buyer"):
            orders.setdefault(dt["buyer"], []).append((r["ts"], dt.get("order"), dt.get("expires")))
            order_buyer[dt.get("order")] = dt["buyer"]
    for r in rows:
        dt = r["data"]
        if r["kind"] == "settle" and (dt.get("shop") or "").rstrip("/") in urls:
            if dt.get("tx_hash"):
                known.add(str(dt["tx_hash"]).lower())                # a settle that names its payment counts it
            elif str(dt.get("order")) not in have:                   # a sealed sale with no record yet
                b = dt.get("buyer") or order_buyer.get(dt.get("order"))
                if b:
                    unrecorded.setdefault(b, []).append((r["ts"], dt.get("order")))
    out, waiting = [], []
    for tx, p in sorted(cand.items(), key=lambda kv: kv[1][5] or 0):
        tx, frm, wei, data, ok, ts = p[0], str(p[1]).lower(), int(p[2]), str(p[3] or "").lower(), p[4], p[5]
        if tx in known or ok is False or wei <= 0:
            continue                                 # counted by a line already (by its tx hash), failed, or nothing moved
        if ok is None:
            waiting.append((p, "receipt not readable yet"))
            continue
        c = issued.get(data)
        buyer = who.get(frm)
        base = {"ts": ts, "time": iso(ts), "round": None, "source": "chain:inflow", "qty": None, "unit_price": None,
                "fee": 0, "storage": 0, "forum_fees": 0, "delta_wei": wei, "gas_wei": 0, "tx_hash": tx, "payer": frm,
                "amount": wei / CREDIT_WEI}
        if c is not None:
            mine = sorted(o for o in orders.get(buyer, []) if o[0] <= ts + 5)
            still_open = [o for o in mine if isinstance(o[2], (int, float)) and now <= o[2]]
            held = [o for t, o in unrecorded.get(buyer, []) if t >= ts - 30]     # recover_sealed reads to settle + 30 s
            if held and now - ts <= SEALED_WAIT:
                waiting.append((p, f"{buyer} has a sealed sale at our shop ({held[-1]}) with no sale record yet"))
                continue
            if still_open:
                waiting.append((p, f"order {still_open[-1][1]} still open"))
                continue
            if not mine and now - ts <= INTENT_GRACE:
                waiting.append((p, "no order of the payer yet"))
                continue
            o = mine[-1] if mine else None
            out.append(dict(base, kind="paid_unsettled", id=f"owed-{tx}", counterparty=buyer or frm, item=c.get("sku"),
                            order=o[1] if o else None, checkout_order=c.get("order"), unit=c.get("unit"),
                            held_order=held[-1] if held else None,
                            note="a customer paid our checkout and no settle names this payment: owed back or to be "
                                 "settled; this script sends nothing"
                                 + (f" (waited {SEALED_WAIT // 60} min on the unrecorded sealed sale {held[-1]}: if that "
                                    "sale gets this payment, it is reclassified)" if held else "")))
        elif now - ts > INTENT_GRACE:
            out.append(dict(base, kind="transfer_in", id=f"in-{tx}", counterparty=buyer or frm, item=None,
                            order=None, note="money in with no checkout of ours"))
        else:
            waiting.append((p, "a transfer with no checkout of ours, inside the grace time"))
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"next": nxt, "waiting": [w[0] for w in waiting]}))
        os.replace(tmp, path)
    except OSError:
        pass
    return out, [(w[0][0], w[1]) for w in waiting]


def reconcile(d, market, name, chain, address, tolerance, now=None, scan_blocks=SCAN_BLOCKS):
    now = now or time.time()
    pending = []
    new, ledger_wei = sync(d, market, name, chain, address, pending)
    wallet_wei = chain.balance_wei(address)
    diff = (wallet_wei - ledger_wei) / CREDIT_WEI
    waiting = []
    if diff > tolerance:
        try:
            found, waiting = scan_inflows(d, market, name, chain, address, now, scan_blocks, _last_ok_ts(d, now - 3600))
        except TornTail:
            raise
        except Exception as e:                     # the scan only explains a gap: a failure leaves the plain mismatch
            found, waiting = [], [("-", f"inflow scan failed: {type(e).__name__}: {str(e)[:120]}")]
        more = []
        for e in found:
            ledger_wei += e["delta_wei"] - e["gas_wei"]
            more.append(dict(e, delta=e["delta_wei"] / CREDIT_WEI, gas=e["gas_wei"] / CREDIT_WEI, balance_wei=ledger_wei,
                             balance_after=ledger_wei / CREDIT_WEI))
        append(d / "logs" / "payments.jsonl", more)
        new = list(new) + more
        diff = (wallet_wei - ledger_wei) / CREDIT_WEI
    elif (d / "logs" / INFLOW_STATE).exists():   # matched: the next gap starts its scan from the last match
        try:
            st = json.loads((d / "logs" / INFLOW_STATE).read_text())
            tmp = d / "logs" / (INFLOW_STATE + ".tmp")
            tmp.write_text(json.dumps({"waiting": st.get("waiting") or []}))
            os.replace(tmp, d / "logs" / INFLOW_STATE)
        except (OSError, ValueError):
            pass
    lines = read_lines(d / "logs" / "payments.jsonl")
    rec = {"ts": round(now, 3), "time": iso(now), "wallet": address, "chain_credits": wallet_wei / CREDIT_WEI,
           "ledger_credits": ledger_wei / CREDIT_WEI, "diff": diff, "ok": abs(diff) <= tolerance, "new_lines": len(new),
           "lines": len(lines), "payments_tail": lines[-1]["hash"] if lines else GENESIS,
           "note": None if abs(diff) <= tolerance else
           ("the wallet holds more than the ledger: a transfer in without a settlement (a customer paid but the sale did "
            "not settle yet, or a direct transfer)" if diff > 0 else
            "the wallet holds less than the ledger: a transfer out the kit did not log, or a supply/settle not on the market ledger yet")}
    if rec["note"] and waiting:
        rec["note"] += "; waiting: " + ", ".join(f"{tx} ({why})" for tx, why in waiting[:5])
    rec["alerts"] = alerts(d, lines, new, rec, now=now, pending=pending)
    append(d / "logs" / "reconcile.jsonl", [rec])
    if rec["alerts"]:
        append(d / "logs" / "alerts.jsonl", [{"ts": rec["ts"], "time": rec["time"], **a} for a in rec["alerts"]])
    return rec


def alerts(d, lines, new, rec, now=None, pending=()):
    """What the operator should hear about: [{kind, id, text}]. id is stable, so a watcher tells each one once."""
    now, out = now or time.time(), []
    if not rec["ok"]:                                # one id per day, direction and hundreds: a standing gap is told once a day
        out.append({"kind": "reconcile_mismatch",
                    "id": f"mismatch-{time.strftime('%Y%m%d', time.localtime(now))}-{'+' if rec['diff'] > 0 else '-'}{round(abs(rec['diff']), -2):.0f}",
                    "text": f"chain {rec['chain_credits']:g} vs ledger {rec['ledger_credits']:g} credits (diff {rec['diff']:g}): {rec['note']}"})
    by_order = {}
    for e in lines:
        if e.get("kind") == "refund" and e.get("order"):
            by_order.setdefault(e["order"], []).append(e["tx_hash"])
    for order, txs in sorted(by_order.items()):
        if len(txs) > 1:
            out.append({"kind": "duplicate_refund", "id": f"dup-{order}-{len(txs)}",
                        "text": f"order {order} was refunded {len(txs)} times: {', '.join(txs)}"})
    sale_amount = {}
    for e in lines:                                  # a sale_correction line (rca1126) replaces its sale's amount
        if e.get("kind") == "sale":
            sale_amount[e["id"][5:]] = e.get("amount")
        elif e.get("kind") == "sale_correction" and e.get("order"):
            sale_amount[str(e["order"])] = e.get("amount")
        elif e.get("kind") == "paid_unsettled" and e.get("order"):
            sale_amount.setdefault(str(e["order"]), e.get("amount"))
    for e in new:
        if e.get("kind") in ("supply", "transfer_out") and not (e.get("note") or "").startswith("reclassifies"):
            out.append({"kind": "money_out", "id": f"out-{e['id']}",   # not a refund: the operator checks the hold
                        "text": f"{e['kind']} {e.get('tx_hash') or e['id']}: {-e.get('delta', 0):g} credits"
                                f"{' for ' + str(e['item']) if e.get('item') else ''} ({e.get('source')})"})
        if e.get("kind") == "refund" and e.get("order") and (sale_amount.get(e["order"]) or 0) < (e.get("amount") or 0):
            out.append({"kind": "refund_over_amount", "id": f"over-{e['tx_hash']}",
                        "text": f"refund {e['tx_hash']} of {e['amount']:g} for order {e['order']} is more than the sale ({sale_amount.get(e['order'])})"})
        if e.get("said_order"):
            out.append({"kind": "refund_order_disagrees", "id": f"disagree-{e['tx_hash']}",
                        "text": f"the agent said {e['tx_hash']} refunds {e['said_order']}, but that wallet paid {e.get('orders_of_payer')}"})
        if e.get("kind") == "refund" and not e.get("order"):
            out.append({"kind": "refund_unmatched", "id": f"unmatched-{e['tx_hash']}",
                        "text": f"refund {e['tx_hash']} to a wallet that paid orders {e.get('orders_of_payer')}: no single order, so a second refund would go unseen"})
        if e.get("kind") == "transfer_out" and e.get("said_refund"):
            out.append({"kind": "refund_to_unknown", "id": f"unknown-{e['tx_hash']}",
                        "text": f"the agent called {e['tx_hash']} a refund, but the receiving wallet paid none of our sales"})
    for p in pending:
        if now - p["ts"] > INTENT_GRACE and p.get("why") == "unreadable":
            out.append({"kind": "sale_unreadable", "id": f"unreadable-{p['order']}",
                        "text": f"sale {p['order']}: its payment tx cannot be read from the chain after {int((now - p['ts']) // 60)} min "
                                "(a wrong tx_hash in logs/sales.jsonl, or the RPC node is behind); it is not counted yet"})
        elif now - p["ts"] > INTENT_GRACE:
            out.append({"kind": "sealed_unrecorded", "id": f"sealed-{p['order']}",
                        "text": f"sealed sale {p['order']} has no line in logs/sales.jsonl after {int((now - p['ts']) // 60)} min: "
                                "the ledger cannot count it (expect a balance gap until the agent's sale tool records it)"})
    for e in new:
        if e.get("kind") == "paid_unsettled" and e.get("held_order"):
            out.append({"kind": "paid_unsettled", "id": f"owed-{e['tx_hash']}",
                        "text": f"{e.get('counterparty')} paid {e['amount']:g} on chain ({e['tx_hash']}) for our checkout "
                                f"{e.get('checkout_order')} ({e.get('item')}) and no settle names it, but the same buyer's "
                                f"sealed sale {e['held_order']} at our shop still has no sale record: this may be that "
                                "sale's payment: do not refund until that sale is recorded or ruled out (an operator decides)"})
        elif e.get("kind") == "paid_unsettled":
            out.append({"kind": "paid_unsettled", "id": f"owed-{e['tx_hash']}",
                        "text": f"{e.get('counterparty')} paid {e['amount']:g} on chain ({e['tx_hash']}) for our checkout "
                                f"{e.get('checkout_order')} ({e.get('item')}), order {e.get('order')}, and the market never "
                                "settled it: no sale, the unit is still ours; settle it or refund it (an operator decides)"})
        elif (e.get("note") or "").startswith("reclassifies owed-"):
            out.append({"kind": "owed_settled_later", "id": f"settled-later-{e['id']}",
                        "text": f"{e['id']} settled later with the payment {e.get('tx_hash')} that was written as owed: "
                                "it is a sale now; do not refund it (check that no refund was sent)"})
        elif e.get("kind") == "transfer_in":
            out.append({"kind": "money_in_unknown", "id": f"in-{e['tx_hash']}",
                        "text": f"{e['amount']:g} credits came in from {e.get('counterparty')} ({e['tx_hash']}) with no checkout of ours"})
    for e in new:
        if e.get("kind") == "broken":
            out.append({"kind": "broken", "id": e["id"], "text": f"a unit we sold broke: order {e['id'][7:]}, "
                        f"{e.get('item')}, {e.get('hours_after_sale')} h after the sale, warranty {e.get('warranty_hours')} h"})
    for i in agent_sends(d)[1]:
        ts = i.get("ts")
        if now - ts > INTENT_GRACE:
            out.append({"kind": "intent_without_tx", "id": f"intent-{i['source']}",
                        "text": f"{i['source']}: a transfer intent ({i['purpose'] or '?'}, order {i['order']}) has no tx hash "
                                f"after {int((now - ts) // 60)} min; money may have left: check the shop wallet's nonce before any retry"})
    return out


def shop_address(d, explicit):
    if explicit:
        return explicit
    for name in ("agent-kit.log", "agent-kit.log.1"):
        p = d / "logs" / name
        if p.exists():
            m = re.search(r"\[maroo\] shop wallet (0x[0-9a-fA-F]{40})", p.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(1)
    sys.exit("no shop wallet address: pass --address or start the kit once")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("sync", "reconcile", "verify"))
    ap.add_argument("--dir", required=True)
    ap.add_argument("--market", default=MARKET)
    ap.add_argument("--rpc", default=RPC)
    ap.add_argument("--name", default="nocha12")
    ap.add_argument("--address")
    ap.add_argument("--tolerance", type=float, default=1.0, help="credits of difference still counted as equal")
    a = ap.parse_args(argv)
    d = Path(a.dir).resolve()
    if a.cmd == "verify":
        bad = verify(d / "logs" / "payments.jsonl") + verify(d / "logs" / "reconcile.jsonl")
        print(json.dumps({"ok": not bad, "problems": bad}, ensure_ascii=False))
        return 0 if not bad else 2
    chain, address = Chain(a.rpc), shop_address(d, a.address)
    try:
        with Lock(d):
            if a.cmd == "sync":
                new, bal = sync(d, a.market.rstrip("/"), a.name, chain, address)
                print(json.dumps({"new_lines": len(new), "ledger_credits": bal / CREDIT_WEI}, ensure_ascii=False))
                return 0
            rec = reconcile(d, a.market.rstrip("/"), a.name, chain, address, a.tolerance)
    except BlockingIOError:
        print(json.dumps({"skipped": "another sync holds logs/.payments.lock"}))
        return 0
    except TornTail as e:
        print(json.dumps({"ok": False, "problems": [str(e)]}, ensure_ascii=False))
        return 2
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if rec["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
