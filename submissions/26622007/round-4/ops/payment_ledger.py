#!/usr/bin/env python3
"""The shop's payment ledger: one line per money event, appended and never rewritten, and a check against the chain.

The kit v5 keeps no payment record of its own: incoming payments are not logged at all and the shop wallet's own
transfers leave one line in logs/agent-kit.log, a log that rotates. This script rebuilds the record from sources that
do not change after the fact:

- the market's hash-chained ledger (GET /api/ledger): the start capital (grant), every lot the shop paid for (supply,
  with its storage and forum fees), every sale (settle, with the shop's fee and the buyer's tx_hash) and every unit
  that broke after a sale (broken; in Round 4 the market refunds nothing);
- the shop wallet's transfers that the kit sent (the "[maroo] <tool> sent ... from the shop wallet: <tx_url>" lines),
  read back from the chain for the exact amount and the gas, so refunds and other transfers are recorded too.

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


def events(rows, name, sends, chain, address, seen=(), seen_tx=None):
    """The shop's new money events from the market ledger rows and the kit's logged transfers, in ledger order. Amounts
    are whole wei in delta_wei/gas_wei. seen: ids already in the file (skipped, not looked up again); seen_tx: tx_hash
    -> the line already written for it, so a lot whose transfer was first written as transfer_out is not counted twice."""
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
            amount, fee = int(dt.get("amount") or 0), int(dt.get("shop_fee") or 0)
            add(dict(base, kind="sale", id=eid, counterparty=dt.get("buyer"),
                     item=f"{dt.get('sku') or dt.get('item')} {dt.get('name') or ''}".strip(), qty=1, unit_price=amount,
                     amount=amount, fee=fee, storage=0, forum_fees=0, delta_wei=(amount - fee) * CREDIT_WEI, gas_wei=0,
                     tx_hash=(dt.get("tx_hash") or "").lower() or None, grade=dt.get("grade"), claim=dt.get("claim"),
                     warranty_hours=dt.get("warranty_hours"), unit=dt.get("unit")))
        elif k == "broken" and (dt.get("shop") or "").rstrip("/") in urls:
            eid = f"broken-{dt.get('order') or r['seq']}"
            if eid in seen:
                continue
            add(dict(base, kind="broken", id=eid, counterparty=dt.get("buyer"), item=dt.get("item"), qty=1, unit_price=None,
                     amount=0, fee=0, storage=0, forum_fees=0, delta_wei=-int(dt.get("refunded") or 0) * CREDIT_WEI,
                     gas_wei=0, tx_hash=None, warranty_hours=dt.get("warranty_hours"),
                     hours_after_sale=dt.get("hours_after_sale"), unit=dt.get("unit")))
    for hhmmss, tool, tx in sends:
        if tx in seen_tx or f"out-{tx}" in seen or chain is None:
            continue
        t = chain.transfer(tx)
        if t is None or t["from"] != me:
            continue                                 # not mined yet (the next sync finds it) or not the shop wallet's
        value = t["value_wei"] if t["ok"] else 0     # a failed transaction moves no value but still burns its gas
        add({"ts": t.get("ts"), "time": iso(t["ts"]) if t.get("ts") else hhmmss, "round": None, "source": f"kit:{tool}",
             "kind": "transfer_out", "id": f"out-{tx}", "counterparty": who.get(t["to"], t["to"]), "item": None,
             "qty": None, "unit_price": None, "amount": value / CREDIT_WEI, "fee": 0, "storage": 0, "forum_fees": 0,
             "delta_wei": -value, "gas_wei": t["gas_wei"], "tx_hash": tx, "ok": t["ok"]})
    return out


def sync(d, market, name, chain, address):
    """Append the new events to logs/payments.jsonl (call under Lock). Returns (new lines, balance in wei)."""
    path = d / "logs" / "payments.jsonl"
    old = read_lines(path)
    seen = {e["id"] for e in old}
    seen_tx = {e["tx_hash"]: e for e in old if e.get("tx_hash")}
    balance = old[-1]["balance_wei"] if old else 0
    new = []
    for e in events(market_rows(market), name, kit_sends(d), chain, address, seen, seen_tx):
        balance += e["delta_wei"] - e["gas_wei"]
        new.append(dict(e, delta=e["delta_wei"] / CREDIT_WEI, gas=e["gas_wei"] / CREDIT_WEI, balance_wei=balance,
                        balance_after=balance / CREDIT_WEI))
    append(path, new)
    return new, balance


def reconcile(d, market, name, chain, address, tolerance):
    new, ledger_wei = sync(d, market, name, chain, address)
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
    append(d / "logs" / "reconcile.jsonl", [rec])
    return rec


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
