"""A buyer that negotiates with an A2A v1.0 seller. Claude Code decides every offer.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The code speaks A2A (Agent Card, SendMessage with the A2A-Version header, the same
taskId for every follow-up, CancelTask to walk away) and keeps the negotiation
history. The harness gets that history each turn and answers offer or walk_away.
The code guards the budget: it never offers more than the budget, and never more
than the seller's current ask.
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

from harness import HarnessError, ask

MAX_TURNS = 10
HEADERS = {"A2A-Version": "1.0"}

SYSTEM = """You are a careful shopper negotiating the price of one item with a shop.
Rules:
- Your budget is the most you may pay. Never offer more than the budget.
- Buying at or under the budget is the goal. Pay as little as you can, but a deal within budget beats no deal.
- To accept the shop's current price, offer exactly that price.
- Shops lose patience: if your offers stay far below their price for several rounds, they end the talk.
  Start low but raise in real steps toward the shop's price.
- If the shop's price stays above your budget after you have offered your full budget, walk away."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "price"],
          "properties": {"action": {"type": "string", "enum": ["offer", "walk_away"]},
                         "price": {"type": "integer"}}}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] buyer {msg}", file=sys.stderr, flush=True)


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        log(f"<- error {r['error'].get('code')} {r['error'].get('message', '')[:120]}")
        return None
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"-> SendMessage {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
    t = rpc(url, "SendMessage", {"message": msg})
    if t is not None:
        log(f"<- {state(t)} | {message_text(t)}")
    return t


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def message_text(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def asking_price(t):
    """The convention both sides agreed on outside A2A: the last number in the message is the price."""
    nums = re.findall(r"\d+(?:\.\d+)?", message_text(t))
    return round(float(nums[-1])) if nums else None


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def decide(item, budget, history, ask_price, turn):
    lines = "\n".join(f"- {who}: {price}" for who, price in history)
    prompt = (f"Item: {item}. Your budget: {budget}.\n"
              f"Turn {turn} of at most {MAX_TURNS}.\n"
              f"Prices so far, oldest first:\n{lines}\n"
              f"The shop's current price is {ask_price}.\n"
              f"action: offer (price = your offer) or walk_away.")
    return ask(prompt, SCHEMA, SYSTEM, tag="buyer-harness")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    card = httpx.get(args.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    iface = next((i for i in card.get("supportedInterfaces", [])
                  if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"), None)
    if iface is None:
        log(f"card {card.get('name')!r} has no JSON-RPC 1.0 interface; giving up")
        return 1
    url = iface["url"]
    log(f"card {card.get('name')!r} -> {url}; item {args.item}, budget {args.budget}")

    t = send(url, f"I want to buy a {args.item}")
    if t is None:
        return 1
    task_id, history = t["id"], []
    for turn in range(1, MAX_TURNS + 1):
        st = state(t)
        if st == "TASK_STATE_COMPLETED":
            r = receipt(t)
            log(f"DEAL {json.dumps(r)}")
            return 0
        if st != "TASK_STATE_INPUT_REQUIRED":
            log(f"END {st}, no deal")
            return 0
        ask_price = asking_price(t)
        if ask_price is None:
            log("no price in the shop's message; walking away")
            break
        history.append(("shop asked", ask_price))
        try:
            d = decide(args.item, args.budget, history, ask_price, turn)
        except HarnessError as e:
            log(f"harness failed: {e}; walking away")
            break
        if d["action"] == "walk_away":
            log("harness: walk away")
            break
        offer = d["price"]
        if offer > args.budget:
            log(f"GUARD offer {offer} > budget {args.budget} -> {args.budget}")
            offer = args.budget
        if offer > ask_price:
            log(f"GUARD offer {offer} > ask {ask_price} -> {ask_price}")
            offer = ask_price
        history.append(("you offered", offer))
        t = send(url, str(offer), task_id)
        if t is None:
            break

    if state(t) == "TASK_STATE_INPUT_REQUIRED":
        log(f"stopping after turn {turn}; walking away")
        log(f"-> CancelTask {task_id[:8]}")
        c = rpc(url, "CancelTask", {"id": task_id})
        log(f"<- {state(c) or 'error'}")
    elif state(t) == "TASK_STATE_COMPLETED":
        log(f"DEAL {json.dumps(receipt(t))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
