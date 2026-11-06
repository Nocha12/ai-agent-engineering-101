"""The lines the meta-harness cannot move. Everything else starts cautious in pricing.json and is widened or narrowed by
Terra and Astra from Round 3's own numbers, a bounded step at a time.

- Bankruptcy: no stock is bought that could take the balance under STOP_BUYING_BELOW (the market's line is 500,000;
  a bankruptcy's 5,000,000 counts against profit). The kit files only when nothing is left to sell.
- pricing.json: each numeric setting moves at most MAX_STEP of its value per cycle (MAX_STEP_STRONG once the log holds
  STRONG_SAMPLE settled deals, so the shop can move fast when its own data is solid; a fall at most MAX_STEP_DOWN),
  inside BOUNDS where listed, and the meta-harness may not take a META_FLOOR setting under its line.
- Tools the agents run get no keys, no network and no writes outside their folders (agent_tools.py builds the sandbox
  from SANDBOX below).

meta_harness.py rejects a change that touches a PROTECTED file, removes a line naming this module, or moves a
pricing.json number further than this file allows. Only a person changes this file.
"""
import math
import re

BANKRUPT_LINE = 500000
STOP_BUYING_BELOW = 1000000          # a supply that could leave less than this is not sent
PROTECTED = ("hard_limits.py", "test_hard_limits.py", "run_market.sh", "run_meta.sh", "ensure_running.sh")
MAX_STEP = 1.0                       # a number may rise by at most its value (double) per cycle (and by 1 when it is small)
# ... or by four times its value (five times it) once the evidence is a real sample, so the stock cap can go from 15M
# toward the top ten's 78M average in two cycles. A fall stays at MAX_STEP_DOWN whatever the sample: Patch 3.3 scores
# book (cash + unsold stock at cost), the shop held 3.2M of stock against the top ten's 24-158M (11/05 11:09 board),
# and a strong step of 100% let one cycle cut a cap to nothing (a person, 11/05 11:48).
MAX_STEP_STRONG = 4.0
MAX_STEP_UNBOUNDED = 2.0             # a setting with no BOUNDS (food_max_stock_cost, sku_target_units) still at most triples
MAX_STEP_DOWN = 0.5
STRONG_SAMPLE = 40                   # settled deals (shop + buyer) in the Round 3 log that make a sample
# The meta-harness may not take these under the line (a person may, in pricing.json): scale is the strategy now and a
# bankruptcy is out of reach (balance 51.7M against the 0.5M line, 11/05 11:50). Narrow per SKU instead. keep_cash: with
# the stock cap open to 150M, the cash a restock leaves is what pays the 6 h warranty refunds of goods already sold and
# the storage fee (1%/day of stock cost) after the buy; 3M covers the dearest listed unit's refund (2.3M) and a day of
# fees on 70M of stock.
META_FLOOR = {"restock.max_stock_cost": 10000000, "restock.keep_cash": 3000000}
# Settings the meta-harness may not change at all (a person may): unlisted dear goods stay out of the explore lines,
# whose price bound is now 10M.
FIXED = {"restock.high_price_listed_only": True}

# path in pricing.json -> (lowest, highest). Outside these the shop's own data has not earned the risk.
BOUNDS = {
    # The floor is STOP_BUYING_BELOW, the least that keeps supplies going: restock never spends under it whatever
    # keep_cash says (spendable), so a lower floor would change nothing. The meta-harness stops at META_FLOOR (3M).
    "restock.keep_cash": (STOP_BUYING_BELOW, 20000000),
    # 150M: Patch 3.3 scores book (unsold stock at cost); the 4M cap was what cut our lines (847 of the last 2,000 guard
    # cuts, 11/05 11:1x) while the 60만+ goods that bring the top ten 82% of their revenue sell in 0.37 h, and the top
    # ten hold 78M of stock on average, 150M at most (review/W-20261105-0953-AX패치3.3/scale-analysis.md). A person set
    # the bound there (11/05 11:48: "크게크게"); pricing.json holds the working value, grown inside it as sales follow.
    "restock.max_stock_cost": (100000, 150000000),
    "restock.target_per_category": (0, 10),
    "restock.max_lines": (1, 10),     # the kit sends the first 10 lines of a restock (scripts/market_kit.py), no more
    "restock.max_units_per_sku": (1, 100),
    "restock.cover_hours": (1, 72),
    "restock.food_explore_qty": (0, 20),
    "restock.food_max_qty": (0, 20),  # units per food line: MAX_LOT (20) cuts a line anyway
    "restock.max_break": (0.0, 0.5),
    # 10M / 1M: over twice the dearest goods (4,045,800) and food (435,000) in the 11/05 catalog. Both only pick the
    # two explore lines; goods over high_price_goods off the proven / priority lists stay out (high_price_listed_only).
    "restock.goods_max_price": (5000, 10000000),
    "restock.food_max_price": (5000, 1000000),
    "restock.band_low": (0.3, 1.0),
    "restock.band_high": (1.0, 3.0),
    "restock.prior_break": (0.0, 0.5),
    "opening.explore_budget_worst": (0, 10000000),
    "opening.judge_settles": (10, 1000),
    "opening.phase_gap_minutes": (5, 120),
    "restock.max_sku_share": (0.01, 0.4),
    # What counts as dear goods (listed only, FIXED above) and how many of each: unbounded, a few cycles of doubling
    # would lift high_price_goods past every price and switch the listed-only rule off.
    "restock.high_price_goods": (50000, 1000000),
    "restock.high_price_max_units": (0, 20),
    "restock.high_price_proven_max_units": (0, 20),
    "warranty.on_request_hours": (0, 6),
    "warranty.max_hours": (0, 72),
    "warranty.max_break": (0.0, 0.5),
    "warranty.prior_break": (0.0, 0.5),
    "buyer.max_price_share": (0.3, 1.0),
    "buyer.quote_shops": (1, 5),
    "markdown.min_floor_share": (0.0, 1.0),
    "forum.max_votes": (0, 30),
    "forum.every_minutes": (5, 240),
}
MARKDOWN_REASONS = ("food_expiry", "stale_goods", "round_end")
# Goods break up to 6 hours after the sale (agent.md). My buyer pays for goods only with a warranty that covers all of it,
# and my shop sells goods with one by default (its reserve carries the break chance). Fixed: not a setting.
# The shop's is the market's longest, 72 h (/rules): a unit breaks only within 6 h of the sale, so 72 h costs what 6 h
# does, and inextro and 25620027 (revenue 1st and 3rd, 11/06) quote 72 h to buyers comparing shops.
BUYER_GOODS_WARRANTY_HOURS = 6
SELLER_GOODS_WARRANTY_HOURS = 72
# The buyer spends the market's money, not the shop's: each order brings its own budget (agent.md: no buyer wallet), and
# the order budget, the list price, the 6 h goods warranty and the food shelf-life check bound every payment. A cap per
# deal only gave up the dear goods most of the top buyers' surplus comes from (80만: 1 of 113 such orders settled), so
# there is none, and a pricing.json value for it is dropped here whoever wrote it (a person decided this, 2026-11-04).
UNCAPPED = {"buyer": ("max_spend_per_deal",)}
# The cautious start, used when a bounded setting is missing a number.
SAFE = {"restock": {"keep_cash": 3000000, "max_stock_cost": 1000000, "target_per_category": 1, "max_lines": 5,
                    "max_units_per_sku": 5, "cover_hours": 6, "food_explore_qty": 0, "food_max_qty": 2, "max_break": 0.15,
                    "goods_max_price": 30000, "max_sku_share": 0.1, "food_max_price": 30000, "band_low": 0.8,
                    "band_high": 1.2, "prior_break": 0.15, "high_price_goods": 100000, "high_price_max_units": 1,
                    "high_price_proven_max_units": 2},
        "warranty": {"on_request_hours": 3, "max_hours": 72, "max_break": 0.25, "prior_break": 0.15},
        "buyer": {"max_price_share": 0.9, "quote_shops": 3},
        "markdown": {"min_floor_share": 0.1, "below_cost": ["food_expiry"]},
        "forum": {"max_votes": 5, "every_minutes": 15},
        "opening": {"explore_budget_worst": 350000, "judge_settles": 50, "phase_gap_minutes": 15}}

# What a tool may touch. Paths come from agent_tools.py at run time: {work} is a fresh folder for this one call, {reads}
# the repository and Python's own folders. Denials repeat the operation they override (file-read-data): a broader
# "file-read*" deny does not win over a later, narrower allow. .market/ is read only: a tool cannot write the forum outbox, its own selftest
# result or the files the kit reads. Other processes (the restock or seller that started it, with the keys in their
# environment) are invisible to it, and it can start no program but Python.
SANDBOX = """(version 1)
(allow default)
(deny network*)
(allow network* (local unix))
(deny process-info*)
(allow process-info* (target self))
(deny process-exec*)
(allow process-exec* {exe})
(deny file-write*)
(allow file-write* (subpath "{work}") (literal "/dev/null"))
(deny file-read-data (subpath "{home}") (subpath "/Volumes"))
(allow file-read-data {reads})
(deny file-read-data (regex #"/\\.env") (regex #"\\.pem$") (regex #"/\\.git/") (subpath "{home}/.codex") (subpath "{home}/.ssh"))
"""
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "HOME")
ENV_PASS_PREFIX = ("BUYER_", "SHOP_", "AGENT_", "MARKET_")      # the agents' own settings (file paths, names)
ENV_SECRET = r"KEY|TOKEN|SECRET|PASS|OWNER|AUTH|COOKIE|CRED"     # never passed, whatever the prefix


def spendable(balance):
    """The most a restock may spend now without risking the bankruptcy line."""
    return max(0, (balance or 0) - STOP_BUYING_BELOW)


def flat(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_policy_change(old, new, step=MAX_STEP):
    """Problems with moving pricing.json from old to new (dicts). None of them: the change may stand."""
    problems, a, b = [], flat(old), flat(new)
    for key, v in b.items():
        lo, hi = BOUNDS.get(key, (-math.inf, math.inf))
        if number(v) and not lo <= v <= hi:
            problems.append(f"pricing.json {key}={v} is outside [{lo}, {hi}] (hard_limits.BOUNDS)")
        was = a.get(key)
        step = min(step, MAX_STEP_STRONG)
        rise = number(v) and number(was) and was >= 0 and v >= was
        move = (step if key in BOUNDS else min(step, MAX_STEP_UNBOUNDED)) if rise else min(step, MAX_STEP_DOWN)
        if number(v) and number(was) and v != was and abs(v - was) > max(abs(was) * move, 1 if isinstance(was, int) else 0):
            problems.append(f"pricing.json {key} {was} -> {v} moves more than {move:.0%} in one cycle")
        if key in META_FLOOR and number(v) and number(was) and was >= META_FLOOR[key] > v:
            problems.append(f"pricing.json {key} {was} -> {v} goes under {META_FLOOR[key]} (hard_limits.META_FLOOR, a person's line)")
        if key in FIXED and key in a and not (type(v) is type(was) and v == was):     # true -> 1 is a change too
            problems.append(f"pricing.json {key} {was!r} -> {v!r} is a person's setting (hard_limits.FIXED)")
        if key == "markdown.below_cost" and (not isinstance(v, list) or any(r not in MARKDOWN_REASONS for r in v)):
            problems.append(f"pricing.json markdown.below_cost {v} is not a list of {MARKDOWN_REASONS}")
        if key in BOUNDS and not number(v):
            problems.append(f"pricing.json {key}={v!r} must stay a number")
    for key in (*BOUNDS, *FIXED, "markdown.below_cost"):
        if key in a and key not in b:
            problems.append(f"pricing.json {key} was removed (bounded settings stay)")
    return problems


def clamp_cfg(section, cfg):
    """A section of pricing.json as the code may use it: bounded numbers inside their bounds, and a value that is not a
    number (null, text) replaced by the cautious start (SAFE). Applied at every run, so even a settings file edited
    outside the meta-harness's checks cannot take the shop past these lines."""
    out = {k: v for k, v in cfg.items() if k not in UNCAPPED.get(section, ())}
    for key, (lo, hi) in BOUNDS.items():
        sec, _, name = key.partition(".")
        if sec != section or (name not in out and name not in SAFE.get(section, {})):
            continue
        v = out.get(name)
        out[name] = min(max(v, lo), hi) if number(v) else SAFE[section][name]
    if section == "markdown":
        r = out.get("below_cost")
        out["below_cost"] = [x for x in r if x in MARKDOWN_REASONS] if isinstance(r, list) else list(SAFE["markdown"]["below_cost"])
    return out


def final_buy(buy, balance, catalog, worst=0.75):
    """The last word on a restock's lines: none that could take the balance under STOP_BUYING_BELOW (at worst cost)."""
    left, out = spendable(balance), []
    for b in buy if isinstance(buy, list) else []:
        if not isinstance(b, dict) or not isinstance(b.get("qty"), int) or isinstance(b.get("qty"), bool):
            continue
        unit = ((catalog.get(b.get("sku")) or {}).get("list_price") or 0) * worst
        qty = min(b["qty"], 20, int(left // unit) if unit > 0 else 0)
        if qty >= 1:
            out.append({"sku": b["sku"], "qty": qty})
            left -= unit * qty
    return out


def policy_diff(old, new):
    """'key a -> b' for each setting that changed: what a commit message says about the change."""
    a, b = flat(old), flat(new)
    return [f"{k} {a.get(k)!r} -> {b.get(k)!r}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def guard_problems(changed_paths, diff_text):
    """A change may not edit the protected files or drop a line that uses this module."""
    out = [f"{p} is protected (hard_limits.PROTECTED)" for p in changed_paths if p.split("/")[-1] in PROTECTED]
    removed = [ln for ln in diff_text.splitlines() if ln.startswith("-") and not ln.startswith("---") and "hard_limits" in ln]
    if removed:
        out.append(f"the change removes {len(removed)} line(s) using hard_limits: {removed[0][:120]}")
    rebinding = [ln for ln in diff_text.splitlines() if re.match(r"\+(?!\+\+).*\b(hard_limits|hl)\.\w+\s*=(?!=)", ln)
                 or re.match(r"\+(?!\+\+).*(setattr\(\s*(hard_limits|hl)\b|import hard_limits as (?!hl\b))", ln)]
    if rebinding:
        out.append(f"the change rebinds hard_limits: {rebinding[0][:120]}")
    return out
