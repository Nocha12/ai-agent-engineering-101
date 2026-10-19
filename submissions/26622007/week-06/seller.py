"""A furniture seller exposed as an A2A v1.0 server (JSON-RPC). Claude Code decides the prices.

    python seller.py --port 9999

The A2A side comes from the lab's seller_agent.py: the code keeps each open
negotiation in self.open (task id -> item, reserve, history) and moves the task
between INPUT_REQUIRED, COMPLETED and REJECTED. Every price decision goes to the
harness. The code only guards the reserve: it never asks or sells below it,
whatever the harness answers, and logs each time it had to step in.
"""
import argparse
import asyncio
import re
import sys
import time

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import HarnessError, ask

NAME = "Nocha Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
MAX_ROUNDS = 10   # hard stop in code; the harness normally ends the talk earlier

SYSTEM = """You are the owner of a small furniture shop, negotiating the price of one item with a buyer.
Your goal is to sell at a good price. Rules:
- Your reserve is the lowest price you may accept. Never ask for or accept less than the reserve.
- Any sale at or above the reserve is better than no sale. Do not lose a buyer who is already at or above your reserve.
- Come down step by step. If the buyer keeps repeating the same offer and it is at or above the reserve, accept it.
- Reject only when the buyer has stayed below the reserve for several rounds and is not moving closer.
- message is one short sentence to the buyer in English, without any numbers. The code appends your price."""

OPEN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ask", "message"],
               "properties": {"ask": {"type": "integer"}, "message": {"type": "string"}}}
TURN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "price", "message"],
               "properties": {"action": {"type": "string", "enum": ["counter", "accept", "reject"]},
                              "price": {"type": "integer"},
                              "message": {"type": "string"}}}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] seller {msg}", file=sys.stderr, flush=True)


def history_text(deal):
    return "\n".join(f"- {who}: {price}" for who, price in deal["history"]) or "- (none yet)"


def decide_open(item, reserve):
    prompt = (f"A buyer wants a {item}. Your reserve is {reserve}.\n"
              f"Choose your opening asking price.")
    return ask(prompt, OPEN_SCHEMA, SYSTEM, tag="seller-harness")


def decide_turn(deal, offer):
    prompt = (f"Item: {deal['item']}. Your reserve: {deal['reserve']}.\n"
              f"Round {deal['rounds']} of at most {MAX_ROUNDS}.\n"
              f"Prices so far, oldest first:\n{history_text(deal)}\n"
              f"The buyer now offers {offer}.\n"
              f"action: accept (sell at {offer}), counter (price = your new asking price), or reject (end the talk).")
    return ask(prompt, TURN_SCHEMA, SYSTEM, tag="seller-harness")


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "rounds", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log(f"task {task.id[:8]} <- {text!r}")

        if deal is None:  # first message: which item?
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            reserve = RESERVE[item]
            try:
                d = await asyncio.to_thread(decide_open, item, reserve)
            except HarnessError as e:
                log(f"harness failed: {e}")
                await up.failed(say("The shop cannot answer right now."))
                return
            ask_price = d["ask"]
            if ask_price < reserve:
                log(f"GUARD opening ask {ask_price} < reserve {reserve} -> {reserve}")
                ask_price = reserve
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask_price, "rounds": 0,
                                  "history": [("you asked", ask_price)]}
            await up.requires_input(say(f"{d['message'].strip()} {item}, my price: {ask_price}."))
            log(f"task {task.id[:8]} -> INPUT_REQUIRED ask {ask_price}")
            return

        nums = re.findall(r"\d+", text)
        if not nums:
            await up.requires_input(say(f"Send your offer as a number. My price: {deal['ask']}."))
            return
        offer = int(nums[-1])
        deal["rounds"] += 1
        try:
            d = await asyncio.to_thread(decide_turn, deal, offer)
        except HarnessError as e:
            log(f"harness failed: {e}")
            await up.failed(say("The shop cannot answer right now."))
            del self.open[task.id]
            return
        deal["history"].append(("buyer offered", offer))
        action, price, reserve = d["action"], d["price"], deal["reserve"]

        if action == "accept" and offer < reserve:
            log(f"GUARD accept at {offer} < reserve {reserve} -> counter at {max(price, reserve)}")
            action, price = "counter", max(price, reserve)
        if action == "counter" and price < reserve:
            log(f"GUARD counter {price} < reserve {reserve} -> {reserve}")
            price = reserve
        if action == "counter" and price <= offer:   # asking no more than the buyer offers: take the offer
            log(f"GUARD counter {price} <= offer {offer} (>= reserve) -> accept")
            action = "accept"
        if action == "counter" and deal["rounds"] >= MAX_ROUNDS:
            log(f"GUARD round limit {MAX_ROUNDS} -> reject")
            action = "reject"

        if action == "accept":
            receipt = {"seller": NAME, "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} at {offer}."))
            log(f"task {task.id[:8]} -> COMPLETED at {offer}")
            del self.open[task.id]
        elif action == "reject":
            await up.reject(say(f"{d['message'].strip()} No deal."))
            log(f"task {task.id[:8]} -> REJECTED")
            del self.open[task.id]
        else:
            deal["ask"] = price
            deal["history"].append(("you asked", price))
            await up.requires_input(say(f"{d['message'].strip()} My price: {price}."))
            log(f"task {task.id[:8]} -> INPUT_REQUIRED ask {price}")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        log(f"task {task.id[:8]} -> CANCELED")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Name an item, then send offers as numbers. Each answer ends with the shop's current "
                        "price, or closes the deal with a receipt artifact (data part with a numeric price).",
            tags=["shopping", "negotiation", "furniture"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    log(f"listening on 127.0.0.1:{args.port}")
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
