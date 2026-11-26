"""One-shot local delivery watch. No wallet lock, RPC, or reconciliation entry point."""
import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

from checkout_delivery import overdue, unbound
from payment_ledger import append, iso, jsonl, read_lines


def checkout_delivery_alerts(d, now=None):
    """Called only under the checkout-specific watch lock."""
    logs = d / "logs"
    now = time.time() if now is None else now
    bound = [r for _, r in jsonl(logs / "checkouts.jsonl")]
    sends = [r for _, r in jsonl(logs / "checkout-sends.jsonl")]
    # recover_sealed records the exact digest from the verified on-chain transfer.
    # Old rows without it cannot establish identity; never fall back to order alone.
    sealed = [{"event": "settled", "order": r.get("checkout_order"),
               "digest_hex": r.get("checkout_digest_hex"), "ts": r.get("ts")}
              for _, r in jsonl(logs / "sales.jsonl")
              if r.get("sealed") is True and isinstance(r.get("checkout_digest_hex"), str)]
    late = list(overdue(bound + sealed, sends, now))
    missing_binding = list(unbound(bound, now))
    if not late and not missing_binding:
        return 0
    # Chain alerts as before, then write the marker. A crash between the two
    # writes must not duplicate the alert on the next cron run.
    path = logs / "checkout-alerts.jsonl"
    known = {r.get("id") for r in read_lines(path)}
    alerts, markers = [], []
    for r in late:
        ident = f"checkout-overdue-{r['task']}-{r['message_id']}-{r['digest_hex']}"
        if ident not in known:
            detail = ("receipt unconfirmed after HTTP handoff" if r["delivery_status"] == "receipt_unconfirmed"
                      else "no timely HTTP handoff")
            alerts.append({"kind": "checkout_delivery_overdue", "id": ident, "ts": now, "time": iso(now),
                           "text": f"checkout {r['order']}: {detail} within 10 minutes"})
        markers.append({k: r[k] for k in ("task", "message_id", "order", "digest_hex", "issued_at", "delivery_status")}
                       | {"event": "delivery_overdue", "ts": now})
    for r in missing_binding:
        ident = f"checkout-unbound-{r['order']}-{r['digest_hex']}"
        if ident not in known:
            alerts.append({"kind": "checkout_delivery_unbound", "id": ident, "ts": now, "time": iso(now),
                           "text": f"checkout {r['order']} has no inquiry binding after 10 minutes"})
        markers.append({"event": "delivery_unbound", "ts": now, "order": r["order"], "digest_hex": r["digest_hex"]})
    if alerts:
        append(path, alerts)
        for alert in alerts:
            # Local stderr only; the scheduler captures it separately from ledger-cron.log.
            print(f"[checkout warning] {alert['text']}", file=sys.stderr)
    with open(logs / "checkouts.jsonl", "a", encoding="utf-8") as f:
        for row in markers:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(alerts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not (args.dir / 'logs' / 'checkouts.jsonl').is_file():
            return 0
        with (args.dir / 'logs' / '.checkout-watch.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0  # independent watch already running
            checkout_delivery_alerts(args.dir)
    except Exception as error:
        # Never disclose log contents or exception arguments in the scheduler.
        print(f"[checkout warning] delivery audit unavailable ({type(error).__name__})", file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
