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
                txs[tx] = {"order": order, "purpose": purpose, "source": f"{name}:{n}"}
                intents = [i for i in intents if i["key"] != key]
            elif any(k in r for k in ("nonce", "amount", "amount_wei", "wei")):
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
            add(dict(base, kind="grant", id=eid, counterparty="market", item=dt.get("why"), qty=None, unit_price=None,
                     amount=c, fee=0, storage=0, forum_fees=0, delta_wei=c * CREDIT_WEI, gas_wei=0, tx_hash=dt["tx_hash"].lower()))
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
                        pending.append({"order": dt.get("order"), "ts": r["ts"]})
                    continue                         # written once the agent's sale record has it
                dt = dict(dt, amount=rec["amount"], shop_fee=rec["shop_fee"], tx_hash=rec.get("tx_hash"),
                          warranty_hours=rec.get("warranty_hours"))
            amount, fee = int(dt.get("amount") or 0), int(dt.get("shop_fee") or 0)
            tx = (dt.get("tx_hash") or "").lower() or None
            payer = None
            if tx and chain:
                t = chain.transfer(tx)
                payer = t["from"] if t else None
            add(dict(base, kind="sale", id=eid, counterparty=dt.get("buyer"),
                     item=f"{dt.get('sku') or dt.get('item')} {dt.get('name') or ''}".strip(), qty=1, unit_price=amount,
                     amount=amount, fee=fee, storage=0, forum_fees=0, delta_wei=(amount - fee) * CREDIT_WEI, gas_wei=0,
                     tx_hash=tx, grade=dt.get("grade"), claim=dt.get("claim"), payer=payer, sealed=sealed or None,
                     warranty_hours=dt.get("warranty_hours"), unit=dt.get("unit")))
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
        return sorted(i[5:] for i, e in found.items() if e.get("kind") == "sale" and e.get("payer") == addr)

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
        order = (hint.get("order") if hint.get("order") in paid else None) or (paid[0] if len(paid) == 1 else None)
        add({"ts": t.get("ts"), "time": iso(t["ts"]) if t.get("ts") else hhmmss, "round": None, "source": source,
             "kind": "refund" if refund else "transfer_out", "id": f"{'refund' if refund else 'out'}-{tx}",
             "counterparty": who.get(t["to"], t["to"]), "item": None, "order": order,
             "orders_of_payer": paid or None, "said_refund": said_refund or None, "qty": None, "unit_price": None, "amount": value / CREDIT_WEI, "fee": 0,
             "storage": 0, "forum_fees": 0, "delta_wei": -value, "gas_wei": t["gas_wei"], "tx_hash": tx, "ok": t["ok"]})
    return out


def sync(d, market, name, chain, address, pending=None):
    """Append the new events to logs/payments.jsonl (call under Lock). Returns (new lines, balance in wei).
    pending: a list that gets our sealed sales the agent has not recorded yet (they wait, and reconcile tells)."""
    path = d / "logs" / "payments.jsonl"
    old = read_lines(path)
    seen = {e["id"] for e in old}
    seen_tx = {e["tx_hash"]: e for e in old if e.get("tx_hash")}
    balance = old[-1]["balance_wei"] if old else 0
    new = []
    for e in events(market_rows(market), name, kit_sends(d), chain, address, seen, seen_tx, agent_sends(d)[0],
                    agent_sales(d), pending):
        balance += e["delta_wei"] - e["gas_wei"]
        new.append(dict(e, delta=e["delta_wei"] / CREDIT_WEI, gas=e["gas_wei"] / CREDIT_WEI, balance_wei=balance,
                        balance_after=balance / CREDIT_WEI))
    append(path, new)
    return new, balance


def reconcile(d, market, name, chain, address, tolerance):
    pending = []
    new, ledger_wei = sync(d, market, name, chain, address, pending)
    wallet_wei = chain.balance_wei(address)
    diff = (wallet_wei - ledger_wei) / CREDIT_WEI
    lines = read_lines(d / "logs" / "payments.jsonl")
    rec = {"ts": round(time.time(), 3), "time": iso(time.time()), "wallet": address, "chain_credits": wallet_wei / CREDIT_WEI,
           "ledger_credits": ledger_wei / CREDIT_WEI, "diff": diff, "ok": abs(diff) <= tolerance, "new_lines": len(new),
           "lines": len(lines), "payments_tail": lines[-1]["hash"] if lines else GENESIS,
           "note": None if abs(diff) <= tolerance else
           ("the wallet holds more than the ledger: a transfer in without a settlement (a customer paid but the sale did "
            "not settle yet, or a direct transfer)" if diff > 0 else
            "the wallet holds less than the ledger: a transfer out the kit did not log, or a supply/settle not on the market ledger yet")}
    rec["alerts"] = alerts(d, lines, new, rec, pending=pending)
    append(d / "logs" / "reconcile.jsonl", [rec])
    if rec["alerts"]:
        append(d / "logs" / "alerts.jsonl", [{"ts": rec["ts"], "time": rec["time"], **a} for a in rec["alerts"]])
    return rec


def alerts(d, lines, new, rec, now=None, pending=()):
    """What the operator should hear about: [{kind, id, text}]. id is stable, so a watcher tells each one once."""
    now, out = now or time.time(), []
    if not rec["ok"]:                                # one id per day and amount: a standing gap is told once a day
        out.append({"kind": "reconcile_mismatch", "id": f"mismatch-{time.strftime('%Y%m%d', time.localtime(now))}-{round(rec['diff'])}",
                    "text": f"chain {rec['chain_credits']:g} vs ledger {rec['ledger_credits']:g} credits (diff {rec['diff']:g}): {rec['note']}"})
    by_order = {}
    for e in lines:
        if e.get("kind") == "refund" and e.get("order"):
            by_order.setdefault(e["order"], []).append(e["tx_hash"])
    for order, txs in sorted(by_order.items()):
        if len(txs) > 1:
            out.append({"kind": "duplicate_refund", "id": f"dup-{order}-{len(txs)}",
                        "text": f"order {order} was refunded {len(txs)} times: {', '.join(txs)}"})
    for e in new:
        if e.get("kind") == "refund" and not e.get("order"):
            out.append({"kind": "refund_unmatched", "id": f"unmatched-{e['tx_hash']}",
                        "text": f"refund {e['tx_hash']} to a wallet that paid orders {e.get('orders_of_payer')}: no single order, so a second refund would go unseen"})
        if e.get("kind") == "transfer_out" and e.get("said_refund"):
            out.append({"kind": "refund_to_unknown", "id": f"unknown-{e['tx_hash']}",
                        "text": f"the agent called {e['tx_hash']} a refund, but {e.get('counterparty')} paid none of our sales"})
    for p in pending:
        if now - p["ts"] > INTENT_GRACE:
            out.append({"kind": "sealed_unrecorded", "id": f"sealed-{p['order']}",
                        "text": f"sealed sale {p['order']} has no line in logs/sales.jsonl after {int((now - p['ts']) // 60)} min: "
                                "the ledger cannot count it (expect a balance gap until the agent's sale tool records it)"})
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
