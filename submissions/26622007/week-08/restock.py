"""The kit's restock hook (Round 3, every 60 s): prints {"buy": [{"sku", "qty"}]}, and "bankrupt": true when stuck.

    python restock.py --state .market

The first stock, before the ledger says what sells: every category gets a few units of the SKU nearest its median
price, since an order's acceptable SKUs lie around a reference price. Food only with a long shelf life (no storage
fee and it outlasts the round), goods only cheap ones (a tied-up sofa pays storage). A sold unit is bought again.

Once market_model.py has seen enough deals: SKUs ranked by the profit they should bring me per hour (reachable
deals per hour x my share x margin), stocked for cover_hours of expected sales, less for food that would not last.
A few lines still go to categories that sell where I have nothing, so a change in demand can be noticed.
If the ledger cannot be read, the shop keeps using what it has, or the first-stock rule.
pricing.json "restock" holds the numbers. They start cautious (one unit per new category, no food until the ledger
shows it sells, a cap on the stock held, goods that break often left out) and the meta-harness widens them as Round 3's
own numbers allow. guard() applies them to every line, the agent's too; hard_limits.py keeps the balance clear of the
bankruptcy line whatever the settings say.

That formula is a proposal. With a harness set, a Luna agent decides every agent_every_minutes: it gets the proposal,
looks at what it wants through the tools (agent_loop.py), and answers with what to buy and the calls it relied on. Its
lines wait in .market/restock_queue.json and go out max_lines per run. Without a decision the formula stands.
"""
import argparse
import collections
import json
import math
import os
import random
import sys
import time

import agent_loop
import agent_memory
import decision_memory
import forum
import hard_limits
import loss_watch
import measure
import market_model
import opening
import profit_price
import rival_vote
import shop_forum
import shop_memory
import tool_forge
from harness import HarnessError

WORST_COST = 0.75      # a unit's cost is at most this share of its list price (A 55-75%)
EXPECTED_COST = {"food": 0.50, "goods": 0.59}      # a unit's expected cost share, from the published grade odds
FOOD_SELL_WITHIN = 0.45                            # share of shelf life to sell in: below it a unit is grade C
MAX_LOT = 20                                       # the most one supply line can buy
# Patch 3.2 surge: past 50 units of a product bought by the whole market in an hour, each unit costs 0.2% more, at most
# +50%. inventory.json's surge lists the products under it with the factor the next unit pays. On 11/05 49 of our units
# cost more than 0.75 x list (the most 0.926), so the worst cost the caps reserve follows the factor (GPT Pro 11/05 §2).
SURGE_STEP, SURGE_MAX = 0.002, 1.5
SURGE_SLACK = 0.05        # other shops' units bought between this run and the supply: 25 units at 0.2%
SURGE_HALF, SURGE_SKIP = 1.20, 1.30   # from 1.20 a SKU off the proven / priority lists buys half; from 1.30 none
DEFAULTS = {"keep_cash": 1500000, "target_per_category": 2, "min_shelf_hours": 720, "goods_max_price": 50000,
            "max_lines": 10, "cover_hours": 12, "max_units_per_sku": 20, "max_sku_share": 0.1, "explore_lines": 2,
            "min_expected": 1.0, "agent_every_minutes": 10, "agent_max_lines": 30, "round_ends_at": 1794927540, "no_explore_hours": 48, "end_cover": 0.5,
            "band_low": 0, "band_high": float("inf"), "food_max_price": float("inf")}


def surge(inv):
    """{sku: factor the next unit pays} from inventory.json's surge; a SKU not listed pays the plain cost."""
    rows = inv.get("surge") if isinstance(inv, dict) else None
    out = {}
    for r in rows if isinstance(rows, list) else ():
        if isinstance(r, dict) and isinstance(r.get("sku"), str) and isinstance(r.get("factor"), (int, float)) \
                and not isinstance(r.get("factor"), bool):
            out[r["sku"]] = min(max(float(r["factor"]), 1.0), SURGE_MAX)
    return out


def worst(sku, qty, catalog, factors=None):
    """The most one unit of a qty-unit line can cost: grade A's 0.75 x list, times the surge factor its last unit pays
    with the slack for other shops' buys, at most 1.5."""
    price = (catalog.get(sku) or {}).get("list_price") or 0
    f = (factors or {}).get(sku)
    return price * WORST_COST * (1.0 if f is None else min(SURGE_MAX, f + SURGE_STEP * max(qty - 1, 0) + SURGE_SLACK))


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def wanted(model, catalog, have_sku, balance, cfg, hours_left=float("inf"), factors=None):
    """[(profit per hour per won of stock, sku, qty)] the demand model asks for, best first. The stock-cost caps in guard()
    are the binding limit, so lines are ranked by what each won tied up in stock earns: ranked by profit per hour alone,
    three mattresses filled the whole cap and the shop held 6 units in 5 categories while buyers see only shops that
    stock the ordered category (shops.json offers). Near the end of the round it stocks only what should sell in time:
    profit counts the balance, so a unit left on the last day is a loss."""
    out = []
    for sku, m in model["skus"].items():
        x = catalog.get(sku) or {}
        price = x.get("list_price")
        if not price:
            continue
        rate = m["rate"] * m["my_share"]
        cover = min(cfg["cover_hours"], hours_left * cfg["end_cover"])
        # expected net per unit sold (GPT Pro §1.5): price x (1 - refund chance under my 6 h warranty) - cost - storage
        cost = price * EXPECTED_COST.get(x.get("kind"), EXPECTED_COST["goods"]) * (factors or {}).get(sku, 1.0)
        refund = (cfg.get("prior_break") or 0) if x.get("kind") == "goods" else 0
        storage = cost * 0.01 * cover / 24 if x.get("kind") == "goods" else 0
        margin = price * m["price_ratio"] * (1 - refund) - cost - storage
        if x.get("kind") == "food" and x.get("shelf_hours"):
            cover = min(cover, x["shelf_hours"] * FOOD_SELL_WITHIN)
        if margin <= 0 or rate * cover < cfg["min_expected"]:
            continue
        target = min(cfg["max_units_per_sku"], math.ceil(rate * cover),
                     int(balance * cfg["max_sku_share"] / (worst(sku, 1, catalog, factors) or price * WORST_COST)))
        if target > have_sku[sku]:
            out.append((rate * margin / cost, sku, min(MAX_LOT, target - have_sku[sku])))
    return sorted(out, reverse=True)


def listed(cfg):
    """high_price_proven_skus then priority_skus, in order, strings only (a malformed setting lists nothing, never fails)."""
    out = []
    for key in ("high_price_proven_skus", "priority_skus"):
        v = cfg.get(key)
        for s in v if isinstance(v, (list, tuple)) else ():
            if isinstance(s, str) and s not in out:
                out.append(s)
    return out


def fill_listed(ranked, model, catalog, have_sku, cfg, factors=None, hours_left=float("inf"), breaks=None):
    """restock.fill_listed: each listed SKU with a sku_target_units target is asked up to it, not to the demand model's
    rate x my_share x cover. The model's share is our own past share, so a shop that lost quotes on price asked for
    1-5 units of goods the market sells 25-56 an hour (G8ec51935: target 10, the model 5, 7 held, 11/06 19:01) and
    held 53M of stock under a 120M cap with 60M cash idle. Only SKUs whose expected margin at the market's price is
    positive (refunds at the SKU's ledger break rate when there is one, else prior_break: at 0.15 G8ec51935, 9% in the
    ledger, came out at -0.4% of list); guard() still applies every cap."""
    if cfg.get("fill_listed") is not True or not isinstance(cfg.get("sku_target_units"), dict) \
            or hours_left * cfg.get("end_cover", 0.5) < cfg.get("cover_hours", 6):    # the last day: the model's own targets
        return ranked
    want = {sku: q for _, sku, q in ranked}
    for sku in listed(cfg):
        t, x, m = cfg["sku_target_units"].get(sku), catalog.get(sku) or {}, (model.get("skus") or {}).get(sku)
        if not isinstance(t, int) or isinstance(t, bool) or not x.get("list_price") or not m:
            continue
        cost = x["list_price"] * EXPECTED_COST.get(x.get("kind"), EXPECTED_COST["goods"]) * (factors or {}).get(sku, 1.0)
        refund = (cfg.get("prior_break") or 0) if x.get("kind") == "goods" else 0
        measured = market_model.break_chance(breaks, sku) if breaks and x.get("kind") == "goods" else None
        refund = measured if measured is not None else refund
        if x["list_price"] * m["price_ratio"] * (1 - refund) - cost <= 0:
            continue
        need = min(MAX_LOT, t - have_sku[sku])
        if need > want.get(sku, 0):
            want[sku] = need
    order = [sku for _, sku, _ in ranked] + [s for s in want if s not in {sku for _, sku, _ in ranked}]
    return [(0, sku, want[sku]) for sku in order if want[sku] > 0]


def plan(inv, catalog, demand, cfg, model=None, breaks=None, waiting=()):
    """The hook's answer for this state."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        return {"bankrupt": True, "buy": []}
    now = time.time()
    live = [u for u in units if u.get("grade") != "expired" and not (u.get("expires_at") and u["expires_at"] <= now)]
    have = collections.Counter(u.get("category") for u in live)
    have_sku = collections.Counter(u.get("sku") for u in live)
    active = bool(model and model.get("active"))
    hours_left = (cfg["round_ends_at"] - now) / 3600 if cfg.get("round_ends_at") else float("inf")
    picks = []
    if active:      # the caps first, over the whole ranked list, then the line limit: a line the food cap empties is
        ranked = wanted(model, catalog, have_sku, balance, cfg, hours_left, surge(inv))   # refilled by the next one that fits
        ranked = fill_listed(ranked, model, catalog, have_sku, cfg, surge(inv), hours_left, breaks)
        # proven / priority SKUs first, so the guard gives them the stock-cost room and the line limit cannot cut them:
        # ranked per won a mattress came below the cut (04:59 run left out G1515d951 that the guard allowed 3 of, and
        # G75f11c19 got 0 room in every run 04:44-05:02), and proven_first() only sees the lines plan() returns
        first = listed(cfg)
        ranked.sort(key=lambda r: first.index(r[1]) if r[1] in first else len(first))     # stable: the rest keep their order
        fit = guard([{"sku": sku, "qty": qty} for _, sku, qty in ranked], inv, catalog, cfg, breaks, waiting)
        picks = [(0, b["sku"], b["qty"]) for b in fit[:cfg["max_lines"] - cfg["explore_lines"]]]
    picked = {sku for _, sku, _ in picks}
    skus = collections.defaultdict(list)
    medians = {d["category"]: d.get("median_price") for d in demand}
    for sku, x in catalog.items():          # GPT Pro §5.1: caps by kind, and near the category's median price
        if opening.fits(sku, x, {**opening.DEFAULTS, **{k: cfg[k] for k in ("food_max_price", "goods_max_price", "band_low",
                                                                               "band_high", "min_shelf_hours") if k in cfg}},
                        medians.get(x.get("category"))):
            skus[x["category"]].append((sku, x))
    rows = [d for d in demand if skus.get(d["category"]) and have[d["category"]] < cfg["target_per_category"]]
    if active:      # demand is known: only categories that really sell and where I have nothing at all
        rows = [d for d in rows if d["category"] in model["cats"] and not have[d["category"]]]
    if cfg.get("food_explore_qty") == 0:       # cautious start: food is bought only once the ledger shows it sells
        rows = [d for d in rows if d.get("kind") != "food"]
    random.shuffle(rows)
    rows.sort(key=lambda d: have[d["category"]])       # the emptiest categories first
    room = 0 if hours_left < cfg["no_explore_hours"] else cfg["explore_lines"] if active else cfg["max_lines"]
    for d in rows[:room]:
        sku, x = min(skus[d["category"]], key=lambda s: abs(s[1]["list_price"] - d.get("median_price", 0)))
        if sku not in picked:
            qty = cfg["target_per_category"] - have[d["category"]]
            if x.get("kind") == "food" and cfg.get("food_explore_qty") is not None:
                qty = min(qty, cfg["food_explore_qty"])
            if qty > 0:
                picks.append((0, sku, qty))
    return {"buy": guard([{"sku": sku, "qty": qty} for _, sku, qty in picks], inv, catalog, cfg, breaks, waiting)}


PENDING_SECONDS = 240      # a line sent this recently whose units have not shown up yet still counts as bought
SENT = "restock_sent.json"


def pending(md, inv, now=None):
    """[{sku, qty, worst}] sent by this hook in the last PENDING_SECONDS and not yet in inventory.json (the file can lag
    the supply): counted as stock already bought, so the next run does not buy it again."""
    now = now or time.time()
    out = []
    sent = load(os.path.join(md, SENT), []) if md else []
    for s in sent if isinstance(sent, list) else []:      # a damaged file counts as nothing sent
        if not (isinstance(s, dict) and s.get("sku") and isinstance(s.get("qty"), int)
                and isinstance(s.get("at"), (int, float)) and now - s["at"] < PENDING_SECONDS):
            continue
        came = sum(1 for u in inv.get("units") or [] if u.get("sku") == s["sku"] and (u.get("supplied_at") or 0) >= s["at"] - 5)
        if s["qty"] > came:
            out.append({"sku": s["sku"], "qty": s["qty"] - came, "worst": s.get("worst", 0)})
    return out


def note_sent(md, buy, catalog, now=None, factors=None):
    now = now or time.time()
    old = load(os.path.join(md, SENT), [])
    keep = [s for s in old if isinstance(s, dict) and isinstance(s.get("at"), (int, float))
            and now - s["at"] < PENDING_SECONDS] if isinstance(old, list) else []
    keep += [{"at": now, "sku": b["sku"], "qty": b["qty"], "worst": worst(b["sku"], b["qty"], catalog, factors)} for b in buy]
    tmp = os.path.join(md, SENT + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f)
    os.replace(tmp, os.path.join(md, SENT))


def guard(buy, inv, catalog, cfg, breaks=None, waiting=()):
    """The lines that keep to the settings, whoever proposed them (formula, opening, next_window, the agent): food per line,
    goods that break too often, and totals over the stock held + lines still on their way (waiting, see pending()) + these
    new lines: units per SKU (max_units_per_sku; high_price_max_units for goods listed above high_price_goods, high_price_proven_max_units for the
    high_price_proven_skus whose extra units sold out between supply windows), the cost of all stock
    (max_stock_cost) and of food (food_max_stock_cost), the cash kept, and hard_limits' line above bankruptcy. New and
    waiting lines count at worst cost (0.75 x list x the surge factor, worst()). A line that does not fit is shrunk or
    dropped; a SKU under surge from SURGE_HALF buys half unless it is proven / priority, from SURGE_SKIP not at all."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    now = time.time()
    live = [u for u in units if not (u.get("expires_at") and u["expires_at"] <= now)]
    kind = lambda sku: (catalog.get(sku) or {}).get("kind")
    count = collections.Counter(u.get("sku") for u in live)
    held = sum(u.get("cost") or 0 for u in live)
    food = sum(u.get("cost") or 0 for u in live if u.get("kind") == "food")
    for w in waiting:
        count[w["sku"]] += w["qty"]
        held += w["worst"] * w["qty"]
        food += w["worst"] * w["qty"] if kind(w["sku"]) == "food" else 0
    room = cfg.get("max_stock_cost", float("inf")) - held
    food_room = cfg.get("food_max_stock_cost", float("inf")) - food
    spare = min(balance - cfg["keep_cash"], hard_limits.spendable(balance, cfg.get("cash_buffer", hard_limits.STOP_BUYING_BELOW))) \
        - sum(w["worst"] * w["qty"] for w in waiting)
    out, factors = [], surge(inv)
    for b in buy:
        x = catalog.get(b["sku"]) or {}
        qty = min(b["qty"], MAX_LOT)
        f, proven_or_priority = factors.get(b["sku"], 1.0), b["sku"] in listed(cfg)
        if f >= SURGE_SKIP and not proven_or_priority:
            print(f"[restock] guard: {b['sku']} surge {f} >= {SURGE_SKIP}, left out until it falls", file=sys.stderr)
            continue
        if b["sku"] in (cfg.get("skip_skus") or ()):
            print(f"[restock] guard: {b['sku']} is in skip_skus, left out", file=sys.stderr)
            continue
        if x.get("kind") == "food" and cfg.get("food_max_qty") is not None:
            qty = min(qty, cfg["food_max_qty"])
        if x.get("kind") == "goods" and breaks and cfg.get("max_break") is not None:
            chance = market_model.break_chance(breaks, b["sku"])
            if chance is not None and chance > cfg["max_break"]:
                print(f"[restock] guard: {b['sku']} breaks {chance:.0%} > {cfg['max_break']:.0%}, left out", file=sys.stderr)
                continue
        cap = cfg.get("max_units_per_sku", MAX_LOT)
        high = x.get("kind") == "goods" and (x.get("list_price") or 0) > cfg.get("high_price_goods", float("inf"))
        if high and cfg.get("high_price_listed_only") is True and b["sku"] not in listed(cfg):
            # 04:43-04:45 the agent bought 2 jacquard mattresses, 2 bed frames, 4 toppers no list had: unsold at 05:00
            print(f"[restock] guard: {b['sku']} high price, not in high_price_proven_skus / priority_skus, left out", file=sys.stderr)
            continue
        target = (cfg.get("sku_target_units") or {}).get(b["sku"]) if isinstance(cfg.get("sku_target_units"), dict) else None
        if isinstance(target, int) and not isinstance(target, bool):
            cap = min(cap, target)          # units held + waiting + new <= target (G6b9db161 3 while proven ones get 4)
        if high:
            proven = b["sku"] in listed({"high_price_proven_skus": cfg.get("high_price_proven_skus")})
            cap = min(cap, cfg.get("high_price_proven_max_units", cfg.get("high_price_max_units", 1)) if proven
                      else cfg.get("high_price_max_units", 1))
        if f >= SURGE_SKIP:         # a proven / priority SKU keeps one unit on the shelf, no more, deep in a surge
            cap = min(cap, 1)
        elif f >= SURGE_HALF and not proven_or_priority and cap > 1:
            cap //= 2               # half the units held + waiting + new, not half of each run's line
        asked, capped = qty, min(qty, cap - count[b["sku"]])
        # priced on the capped line, not the line the cash leaves: a few won high at most, never low
        unit = worst(b["sku"], capped, catalog, factors) if x.get("list_price") else float("inf")
        limit = min(spare, room, food_room if x.get("kind") == "food" else float("inf"))
        qty = min(capped, int(limit // unit) if unit < float("inf") else 0)
        if qty < asked:     # which limits cut the line, so a stock-out can be traced to a cap or the cash
            why = (["units per SKU"] if capped < asked else []) + ([
                "no list price" if unit == float("inf") else "cash kept" if limit == spare else "stock cost" if limit == room
                else "food stock cost"] if qty < capped else [])
            print(f"[restock] guard: {b['sku']} {asked} -> {max(qty, 0)} ({', '.join(why)})", file=sys.stderr)
            if qty < 1 and "stock cost" in why and x.get("kind") == "food" and proven_or_priority and count[b["sku"]] <= 0 \
                    and unit < float("inf"):
                # measured only, nothing reserved: whether keeping 1 unit for an empty priority food would change a real
                # window's lines (GPT Pro 11/05 13:30 §2; a person decides the guard after the windows are compared)
                print(f"[restock] food-reserve sim: {b['sku']} none held or waiting, cut by stock cost; 1 unit would take "
                      f"{unit:.0f} of {room:.0f} room", file=sys.stderr)
        if qty < 1:
            continue
        out.append({"sku": b["sku"], "qty": qty})
        count[b["sku"]] += qty
        spare, room = spare - unit * qty, room - unit * qty
        if x.get("kind") == "food":
            food_room -= unit * qty
    return out


def read_breaks(state, catalog):
    try:
        return market_model.break_stats(market_model.load_rows(state), catalog)
    except Exception:   # noqa: BLE001 - without break rates the guard simply does not use them
        return None


SALES_TAIL_BYTES = 15000000      # the ledger's last ~9 h (11/06: 30 MB held 17 h); refunds come within BREAK_HOURS


def warranted_sales(state, catalog, now=None):
    """[(time, price)] of this shop's warranted goods sales in the last hard_limits.BREAK_HOURS, from the tail of the
    ledger rows (a result row names the seller, the settle row the price and the warranty); None when unreadable, so
    cash_buffer() keeps its UNKNOWN_EXPOSURE."""
    now, mine = now or time.time(), market_model.my_shops()
    since = now - hard_limits.BREAK_HOURS * 3600
    try:
        with open(os.path.join(state, market_model.ROWS), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - SALES_TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except FileNotFoundError:       # no ledger yet (a new or reset shop): no sales of ours to refund
        return []
    except OSError:
        return None
    ours, sold = set(), []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get("k") == "result" and r.get("seller") in mine:
            ours.add(r.get("order"))
        elif r.get("k") == "settle" and isinstance(r.get("ts"), (int, float)) and r["ts"] >= since and r.get("warranty") \
                and (catalog.get(r.get("sku")) or {}).get("kind") == "goods" and isinstance(r.get("amount"), (int, float)):
            sold.append(r)
    return [(r["ts"], r["amount"]) for r in sold if r.get("order") in ours or str(r.get("shop") or "").rstrip("/") in mine]


def read_model(state, catalog, cfg):
    """The demand model, or None when it cannot be built: a restock must never fail because of the ledger."""
    me = os.environ.get("SHOP_MARKET_NAME", "nocha12")
    shops = market_model.my_shops()
    try:
        added = market_model.sync(state, max_pages=cfg.get("max_pages", market_model.DEFAULTS["max_pages"]))
        model = market_model.estimate(market_model.load_rows(state), catalog, {me}, shops, cfg=cfg)
    except Exception as e:      # noqa: BLE001 - any ledger trouble means the same thing here
        print(f"[restock] demand model unavailable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None
    top = sorted(model["skus"].items(), key=lambda kv: -kv[1]["rate"])[:5]
    print(f"[restock] model {'on' if model['active'] else 'warming up'}: {model['n']} reachable deals (+{added} rows), "
          f"{len(model['skus'])} SKUs, {len(model['captive'])} captive buyers, top "
          + ", ".join(f"{k} {v['rate']:.2f}/h" for k, v in top), file=sys.stderr, flush=True)
    with open(os.path.join(state, "demand_model.json"), "w", encoding="utf-8") as f:
        json.dump({**model, "skus": dict(top)}, f, ensure_ascii=False)
    tmp = os.path.join(state, "briefing.json.tmp")     # what the seller reads at each turn of a talk, when it cannot wait for tools
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"at": int(time.time()), "skus": model["skus"]}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(state, "briefing.json"))
    return model


NEXT_WINDOW_STATE = "next_window_state.json"


def gone(u, now):
    return u.get("grade") == "expired" or bool(u.get("expires_at") and u["expires_at"] <= now)


def arrived(sku, at, inv, memory=os.path.join("memory", "seller_memory_r3.json")):
    """True when a line for this SKU sent at `at` was supplied: a unit of it came in since, or the shop has sold or agreed
    one since (a unit can sell within a minute of arriving)."""
    if any(u.get("sku") == sku and (u.get("supplied_at") or 0) >= at - 5 for u in inv.get("units") or []):
        return True
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at - 5))
    m = load(memory, {})
    rows = m.get("negotiations") if isinstance(m, dict) else None
    return any(isinstance(n, dict) and n.get("sku") == sku and str(n.get("time", "")) >= since
               and n.get("result") in ("agreed", "deal") for n in rows or [])


def next_window(md, inv, catalog, cfg, waiting=(), keep=None, now=None):
    """pricing.json "next_window": a one-off buy for the first supply window it meets (GPT Pro review §2-a). Each target is a
    ceiling: what is in stock or still on its way is subtracted. (lines, why) while it runs, (None, why) once done, off or
    expired; [] means buy nothing now (no window yet, or the plan's window is done). It ends when its window has closed."""
    now = now or time.time()
    targets = cfg.get("targets") or {}
    if not targets or not cfg.get("id"):
        return None, "no next_window plan"
    path = os.path.join(md, NEXT_WINDOW_STATE)
    st = load(path, {})
    if st.get("id") != cfg["id"]:
        st = {"id": cfg["id"], "created": now, "sent": [], "done": False, "notes": []}
    if st["done"]:
        return None, f"next_window {cfg['id']} done"

    def finish(why):
        st["done"], st["notes"] = True, st["notes"] + [why]
        opening.save(path, st)
        return None, f"next_window {cfg['id']}: {why}"

    if now - st["created"] > cfg.get("expires_hours", 2) * 3600:
        return finish("expired before a window")
    if st["sent"] and opening.window_closed(inv, now):
        at = st["sent"][-1]["at"]
        missed = [b["sku"] for b in st["sent"][-1]["lines"] if not arrived(b["sku"], at, inv)]
        if missed and st.get("retries", 0) < 1:      # the market refused them (e.g. 429 at the window's edge): once more
            st.update(retries=st.get("retries", 0) + 1, only=missed, sent=[], notes=st["notes"] + [f"missed {missed}"])
            opening.save(path, st)
            return [], f"next_window {cfg['id']}: {missed} never arrived, retried in the next window"
        return finish(f"bought in the window of {time.strftime('%H:%M', time.localtime(at))}"
                      + (f", never arrived: {missed}" if missed else ""))
    if st["sent"]:                           # sent once; the rest of this window is the plan's: nothing else is bought
        return [], f"next_window {cfg['id']}: sent, nothing else in this window"
    if opening.window_closed(inv, now, lead=0, need=opening.SECONDS_PER_LINE * len(targets) + 15):
        opening.save(path, st)               # lead 0: a line sent before the window opens is refused (429) and lost
        return [], f"next_window {cfg['id']} waits for the supply window"
    have = collections.Counter(u.get("sku") for u in inv.get("units") or [] if not gone(u, now))
    for w in waiting:
        have[w["sku"]] += w["qty"]
    only = set(st.get("only") or targets)
    want = [{"sku": sku, "qty": q - have[sku]} for sku, q in targets.items()
            if sku in catalog and sku in only and q - have[sku] > 0]
    out = keep(want) if keep and want else want
    if not out:
        return finish(f"nothing to buy (wanted {want}; stock and limits leave none)")
    st["sent"].append({"at": now, "lines": out})
    opening.save(path, st)
    cut = {b["sku"]: b["qty"] for b in out}
    notes = [f"{b['sku']} {b['qty']}->{cut.get(b['sku'], 0)}" for b in want if cut.get(b["sku"], 0) != b["qty"]]
    return out, (f"next_window {cfg['id']}: {len(out)} lines, {sum(cut.values())} units"
                 + (f" (guard: {', '.join(notes)})" if notes else ""))


PRICE_LOG_STATE = "price_log_state.json"


def log_prices(md, inv, breaks, pricing):
    """A [price] line for each unit not logged before: its floor and first ask from its real cost, as the seller computes
    them at the first message of a talk (profit_price)."""
    path = os.path.join(md, PRICE_LOG_STATE)
    seen = set(load(path, {}).get("seen", []))
    units = inv.get("units") or []
    briefing = load(os.path.join(md, "briefing.json"), {}).get("skus") or {}
    memory = os.path.join("memory", "seller_memory_r3.json")
    for u in units:
        if u["unit"] in seen or not u.get("cost"):
            continue
        ratio = (briefing.get(u["sku"]) or {}).get("price_ratio")
        median = ratio * u["list_price"] if ratio and u.get("list_price") else None
        measured = market_model.unit_break_chance(breaks, u["sku"], u.get("grade")) if breaks and u.get("kind") == "goods" else None
        q = profit_price.quote(u, median, measured, shop_memory.paid_prices(u["sku"], memory=memory), pricing)
        print(f"[price] {profit_price.line(u, q)}", file=sys.stderr, flush=True)
    opening.save(path, {"seen": [u["unit"] for u in units]})


GOAL = """You decide what stock a shop buys now. Book (balance plus what the unsold stock cost, Patch 3.3: a restock
does not lower it; storage, refunds and spoiled food do), revenue and stars are what count. Round ends at unix time {ends}, now is {now}. A unit costs 25-75% of list price by grade;
food past its date is thrown away at cost; goods in stock cost 1% of cost per day. A shop with no stock cannot sell at all,
and other shops' deals show in the ledger whether you stock or not. Up to {lines} lines, qty 1-20 each.
c0 is the formula's proposal for this state; follow it, change it or ignore it, as the evidence says. Your lines go
first; the code then fills the room the limits leave with the formula's lines, because buyers only see shops that stock
the ordered category and a shop's visits follow how much it stocks. c1 is how your own earlier
stock decisions turned out: what sold, what is left, what is gone unsold. Learn from it: a judgment that failed should not repeat.
c2 is the shop's own memory: long-term notes, lessons from earlier mistakes that match this state, and recent days. The
code keeps every line inside the shop's current limits (pricing.json), which start cautious and widen as results earn it."""


def keys_for(proposal, inv, catalog):
    """What this decision is about, for picking memories: the SKUs and categories proposed or held, and food/goods."""
    skus = {b["sku"] for b in proposal.get("buy", [])} | {u.get("sku") for u in inv.get("units") or []}
    out = set()
    for sku in skus:
        x = catalog.get(sku) or {}
        out |= {sku, x.get("category") or "", x.get("kind") or ""}
    return sorted(k for k in out if k)

FINAL = {"type": "object", "additionalProperties": False, "required": ["buy", "why"],
         "properties": {"why": {"type": "string"},
                        "buy": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False, "required": ["sku", "qty"],
                            "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}}}}}


def clean_lines(buy, catalog, limit):
    """Local check of the agent's answer: known SKUs, a lot size the market takes, each SKU once."""
    out, seen = [], set()
    for b in buy:
        if b["sku"] in catalog and b["sku"] not in seen and 1 <= b["qty"] <= MAX_LOT:
            out.append({"sku": b["sku"], "qty": b["qty"]})
            seen.add(b["sku"])
    return out[:limit]


def agent_turn(state, inv, catalog, demand, cfg, model, decide=agent_loop.decide):
    """The lines to send now: from the agent's queue, after a new decision when one is due. None: use the formula."""
    path = os.path.join(state, "restock_queue.json")
    q = load(path, {"at": 0, "lines": []})
    if time.time() - q["at"] >= cfg["agent_every_minutes"] * 60:
        proposal = plan(inv, catalog, demand, {**cfg, "max_lines": cfg["agent_max_lines"]}, model)
        results = decision_memory.outcomes(state, inv)
        agent_memory.learn_from_outcomes(state, results, catalog)
        try:
            final = decide(GOAL.format(ends=cfg.get("round_ends_at"), now=int(time.time()), lines=cfg["agent_max_lines"]), FINAL,
                           state, "restock", {"c0": json.dumps(proposal, ensure_ascii=False),
                                       "c1": json.dumps(results, ensure_ascii=False),
                                       "c2": agent_memory.select(state, keys_for(proposal, inv, catalog))})
        except HarnessError as e:
            print(f"[restock] agent failed: {e}", file=sys.stderr, flush=True)
            return None
        if final is None:
            print("[restock] agent gave no evidenced decision", file=sys.stderr, flush=True)
            return None
        q = {"at": time.time(), "lines": clean_lines(final["buy"], catalog, cfg["agent_max_lines"])}
        agent_memory.daily(state, "restock", f"bought {q['lines']} because {final.get('why', '')[:300]}")
    now, q["lines"] = q["lines"][:cfg["max_lines"]], q["lines"][cfg["max_lines"]:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, path)
    return now


def proven_first(agent_lines, formula_lines, cfg):
    """The high_price_proven_skus, then priority_skus, that either the agent or the formula proposes go first, at the
    larger of their two quantities (guard still caps them). At 03:40 the agent's 'G1515d951 1' replaced the formula's 3
    and max_lines cut G9c02e68e; at 04:00 they came behind five food lines and the 180 s window closed before G1515d951
    was bought (kit log 04:03)."""
    order = []
    for s in list(cfg.get("high_price_proven_skus") or ()) + list(cfg.get("priority_skus") or ()):
        if isinstance(s, str) and s not in order:
            order.append(s)
    want = {}
    for b in list(agent_lines) + list(formula_lines):
        q = b.get("qty")
        if b.get("sku") in order and isinstance(q, int) and not isinstance(q, bool) and q > 0:
            want[b["sku"]] = max(want.get(b["sku"], 0), q)
    return [{"sku": s, "qty": want[s]} for s in order if s in want] + [b for b in agent_lines if b.get("sku") not in want]


def batch(agent_lines, fill_lines, formula_lines, keep, cfg):
    """This run's lines and the agent lines to queue again: proven / priority SKUs first (proven_first, from the
    formula's plan even while the agent's queue waits), then the agent's, then fill_lines (the formula's, once the
    queue is empty). Lines the guard drops are gone, as before; agent lines that only the cut at max_lines leaves out
    go back to the queue, so the proven lines never push an agent line out for good."""
    agent_skus = {b.get("sku") for b in agent_lines}
    first = set(cfg.get("high_price_proven_skus") or ()) | set(cfg.get("priority_skus") or ())
    kept = with_formula(proven_first(agent_lines, formula_lines, cfg), fill_lines, keep, None)
    n = cfg["max_lines"]
    return kept[:n], [b for b in kept[n:] if b["sku"] in agent_skus and b["sku"] not in first]


def requeue(state, lines):
    """Agent lines that did not fit this run's max_lines go back to the front of the queue, so none is lost."""
    if not lines:
        return
    path = os.path.join(state, "restock_queue.json")
    q = load(path, {})
    q["lines"] = list(lines) + (q.get("lines") or [])
    q.setdefault("at", time.time())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, path)


def with_formula(agent_lines, formula_lines, keep, limit):
    """The agent's lines first, then the formula's for the room the limits leave. Buyers see only shops that stock the
    ordered category (shops.json offers), and over Round 3's first three hours units stocked and visits ranked together
    (Spearman 0.80, 42 shops): the agent alone bought 2-3 lines a window and nothing between its decisions."""
    mine = {b["sku"] for b in agent_lines}
    return keep(list(agent_lines) + [b for b in formula_lines if b["sku"] not in mine])[:limit]


def with_trial(trial, buy, inv, catalog, cfg, breaks=None, waiting=(), hold=()):
    """next_window's one-off lines first, then the run's usual lines in the room the limits leave (the trial's units count
    as on their way), never more than max_lines. The trial used to replace the whole window: the 01:40 and 09:40 windows
    of 11/05 each bought the single trial unit while the windows around them bought 28-53 units (ledger supply rows).
    The usual lines leave out the trial's SKUs (hold: all its targets while it runs), so a unit of them that arrives is
    the trial's and next_window's arrived() check and retry stay true. A target the guard cut from the trial stays held too."""
    skip = set(hold) | {b["sku"] for b in trial or ()}
    rest = [b for b in buy if b["sku"] not in skip]
    if not trial:
        return rest
    sent = [{"sku": b["sku"], "qty": b["qty"], "worst": worst(b["sku"], b["qty"], catalog, surge(inv))} for b in trial]
    return (list(trial) + guard(rest, inv, catalog, cfg, breaks, list(waiting) + sent))[:max(cfg["max_lines"], len(trial))]


LATE_MARGIN = 5     # seconds the kit needs to read this run's output and make its first supply call


def late_lines(state, inv, buy, agent_skus=(), now=None):
    """The lines the kit can still start before an open window closes. A run reads inventory.json when it starts, and
    the agent turn and the hooks after it can take tens of seconds: on 11/05 a run that began at 16:25:13 handed four
    lines over at 16:25:24, nine seconds after the 16:25:15 close, and each was refused (429) and retried. Lines that
    cannot start go back to the queue when they are the agent's; the formula plans its own lines again next run."""
    r, now = inv.get("restock"), now or time.time()
    if not buy or not isinstance(r, dict) or r.get("open") is not True or not isinstance(r.get("closes_at"), (int, float)):
        return buy
    left = r["closes_at"] - now - LATE_MARGIN
    n = 0 if left < 0 else int(left // opening.SECONDS_PER_LINE) + 1
    if n >= len(buy):
        return buy
    send, late = list(buy[:n]), list(buy[n:])
    requeue(state, [b for b in late if b.get("sku") in agent_skus])
    print(f"[restock] window closes in {r['closes_at'] - now:.0f} s: {len(late)} line(s) held back "
          f"({', '.join(str(b.get('sku')) for b in late)})", file=sys.stderr, flush=True)
    return send


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    inv = load(os.path.join(args.state, "inventory.json"), {})
    pricing = load("pricing.json", {})
    cfg = hard_limits.clamp_cfg("restock", {**DEFAULTS, **pricing.get("restock", {})})
    catalog = load(os.path.join(args.state, "catalog.json"), {})
    demand, model = load(os.path.join(args.state, "demand.json"), []), read_model(args.state, catalog, cfg)
    loss_watch.check(args.state)           # after read_model: the ledger rows are fresh
    breaks = read_breaks(args.state, catalog)
    try:
        waiting = pending(args.state, inv)     # sent lines not yet in inventory.json count as stock in every guard below
    except Exception as e:   # noqa: BLE001 - an unreadable record of sent lines must not stop the restock
        print(f"[restock] pending lines unreadable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        waiting = []
    try:
        sales = warranted_sales(args.state, catalog)
    except Exception as e:   # noqa: BLE001 - the buffer then assumes hard_limits.UNKNOWN_EXPOSURE
        print(f"[restock] own sales unreadable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        sales = None
    stock = sum(u.get("cost") or 0 for u in inv.get("units") or [] if isinstance(u, dict))
    cfg["cash_buffer"] = hard_limits.cash_buffer(stock, sales)     # a person, 11/06 19:03: no bankruptcy, nothing else
    print(f"[restock] cash buffer {cfg['cash_buffer']} (balance {inv.get('balance')}, stock {stock:.0f}, "
          f"{'unknown' if sales is None else len(sales)} warranted goods sales in {hard_limits.BREAK_HOURS} h)",
          file=sys.stderr, flush=True)
    keep = lambda b: guard(b, inv, catalog, cfg, breaks, waiting)
    out = plan(inv, catalog, demand, cfg, model, breaks, waiting)
    ocfg = hard_limits.clamp_cfg("opening", {**opening.DEFAULTS, **pricing.get("opening", {})})
    first, why, trial = None, "not Round 3", None
    if inv.get("round", 0) >= market_model.ECON_ROUND and not out.get("bankrupt"):
        try:
            first, why = opening.lines(args.state, inv, catalog, demand, market_model.load_rows(args.state), ocfg, keep=keep)
        except Exception as e:   # noqa: BLE001 - a broken opening leaves the usual plan, not an idle shop
            first, why = None, f"opening failed: {type(e).__name__}: {e}"
        if first is None:       # next_window's lines join this run's (with_trial); once sent they no longer stop the window
            try:
                trial, more = next_window(args.state, inv, catalog, pricing.get("next_window") or {}, waiting, keep)
            except Exception as e:   # noqa: BLE001 - the same: the usual plan goes on
                trial, more = None, f"next_window failed: {type(e).__name__}: {e}"
            why = f"{why}; {more}"
        try:
            log_prices(args.state, inv, breaks, pricing)
        except Exception as e:   # noqa: BLE001 - a price log never stops a restock
            print(f"[restock] price log failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    print(f"[restock] {why}", file=sys.stderr, flush=True)
    trial_cfg = pricing.get("next_window") or {}
    hold = set(trial_cfg.get("targets") or {}) if trial is not None or trial_cfg.get("hold_after_done") is True else set()
    agent_skus = set()
    if first is not None:                 # the opening decides this run (already guarded); the agent waits
        out = {"buy": first}
    elif (opening.window_closed(inv) or trial == [] and opening.window_closed(inv, lead=0)) and not out.get("bankrupt"):
        out = {"buy": []}                  # nothing can be bought now; the agent's queue keeps its lines for the window.
        # A trial waiting for its window keeps the lead time empty, as before: a line sent before it opens is refused (429)
        # but would count as pending and make next_window find its target already bought
    elif trial:                            # this run sends the trial; the formula fills the room, the agent waits a run
        out = {"buy": with_trial(trial, out["buy"], inv, catalog, cfg, breaks, waiting, hold)}
    elif os.environ.get("HARNESS") and not out.get("bankrupt"):
        lines = agent_turn(args.state, inv, catalog, demand, cfg, model)
        if lines is not None:
            queued = load(os.path.join(args.state, "restock_queue.json"), {}).get("lines") or []
            # the formula fills only once the agent's queue is sent, except the proven / priority SKUs it proposes:
            # those go first in either case (proven_first); agent lines they push past max_lines are queued again
            send, back = batch(lines, [] if queued else out["buy"], out["buy"], keep, cfg)
            agent_skus = {x.get("sku") for x in lines}
            if hold and not trial:         # agent lines for the trial's SKUs wait in the queue until the trial ends
                back += [x for x in send if x["sku"] in hold and x["sku"] in agent_skus]
            requeue(args.state, back)
            out = {"buy": send}
    if hold and not trial:                 # the trial was sent earlier in this window: its SKUs stay the trial's
        out["buy"] = with_trial(None, out["buy"], inv, catalog, cfg, hold=hold)
    agent_memory.maybe_consolidate(args.state)
    if os.environ.get("HARNESS"):
        forum.maybe_run(args.state)     # the kit runs this hook once a minute in Round 3; forum.py keeps its own pace
        tool_forge.maybe_run(args.state)
        rival_vote.maybe_run(args.state)    # a free dislike stance on a tied Players rival's buyer, never a paid vote
    shop_forum.maybe_run(args.state)    # the shop's own posts, written by code from its ledger rows and stock
    # the last word, whatever came before. It keeps its own 0.75 x list (test_hard_limits pins it); the surge cost is
    # reserved by guard() and the waiting lines, and with the balance far above STOP_BUYING_BELOW the line does not bind
    if first is None:
        out["buy"] = late_lines(args.state, inv, out.get("buy"), agent_skus)
    owed = sum(w["worst"] * w["qty"] for w in waiting)      # lines already sent are bought out of the same cash
    out["buy"] = hard_limits.final_buy(out.get("buy"), inv.get("balance"), catalog, buffer=cfg["cash_buffer"] + owed)
    if out["buy"]:
        try:
            note_sent(args.state, out["buy"][:10], catalog, factors=surge(inv))    # the kit sends ten lines a run
        except OSError as e:
            print(f"[restock] could not note the lines sent: {e}", file=sys.stderr, flush=True)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    measure.tick(args.state, inv, out["buy"][:10], pricing)     # after the line the kit reads; local files only, never raises
    return 0


if __name__ == "__main__":
    sys.exit(main())
