"""Write a windowed buyer study switch: .market/buyer_final_m1.json, buyer_moss_first.json or buyer_our_ask.json.

ax-pro-cycle step 5 (main instruction 10, 11/10 12:55): a timed study has a finite start and end both where it is treated
(oneshot.final_m1_on, buyer_moss_first._switch, buyer_our_ask._switch) and where its switch is made, so no one writes a
window by hand that has no end, starts in the past, or runs past the study's maximum. This writer:
- takes a finite start and end inside the study's bounds (final_m1 0 < end - start <= 12 h; moss_first and our_ask 9-12 h),
  a salt for the A/B studies (none for the final_m1 shadow), and a start after now: the file is written before its
  window, so its mtime is before the start, as the readers require. now is the real clock, never an earlier one the
  caller gives (independent review 7b17f3f I);
- never writes over a window that is running (started, not over): a running window is not moved, cut or extended; one
  not yet started can be replaced by another future one;
- keeps every window it writes and every off in HISTORY, append-only, so what ran is known whatever the switch file now
  says (review 77cdbef I1, M1: a record kept only in the switch was lost when the next window was written). A window
  ran if it started before it was stopped, replaced or over. Then a later window of the same study:
  - an A/B study (moss_first, our_ask) never reuses the salt of a window that ran: the same arms again would be the same
    study made longer; with a new salt it may start before the old end only if new_study (--new-study) says so;
  - final_m1 starts only after the end of every window that ran, and then only with new_study (review 77cdbef M2: off
    and on again gave a 24 h shadow);
- writes a temporary file in the same directory and renames it over the switch, then reads it back with the study's
  own reader at the start; if that reader would not take it, or HISTORY cannot take the line, the old file is put back
  and it is refused. The file keeps the switch's mode (0644 when new) so the buyer reads it as before (review 7b17f3f M3);
- off writes {"enabled": false} (with the window it stopped), always allowed: stopping a study is never refused, and
  its HISTORY line is written if it can be.

  python3 buyer_switch_write.py [--market-dir .market] final_m1 --start EPOCH --end EPOCH
  python3 buyer_switch_write.py [--market-dir .market] moss_first --start EPOCH --end EPOCH --salt SALT
  python3 buyer_switch_write.py [--market-dir .market] our_ask --off
  python3 buyer_switch_write.py [--market-dir .market] moss_first --start EPOCH --end EPOCH --salt NEW --new-study

It prints the JSON written and its mtime; exit 2 when refused.
"""
import argparse
import json
import math
import os
import re
import sys
import tempfile
import time

import buyer_moss_first
import buyer_our_ask
import oneshot

HISTORY = os.path.join("buyer_switch_write", "history.jsonl")
STUDIES = {
    "final_m1": {"file": oneshot.FINAL_M1_SWITCH, "min_hours": 0, "max_hours": oneshot.FINAL_M1_MAX_HOURS, "salt": False,
                 "reads": lambda md, start: oneshot.final_m1_on(md, env={}, now=start)},
    # the A/B readers check the window and mtime <= start themselves (no now): assign checks now
    "moss_first": {"file": buyer_moss_first.SWITCH, "min_hours": buyer_moss_first.MIN_HOURS,
                   "max_hours": buyer_moss_first.MAX_HOURS, "salt": True,
                   "reads": lambda md, _start: buyer_moss_first._switch(md) is not None},
    "our_ask": {"file": buyer_our_ask.SWITCH, "min_hours": buyer_our_ask.MIN_HOURS, "max_hours": buyer_our_ask.MAX_HOURS,
                "salt": True, "reads": lambda md, _start: buyer_our_ask._switch(md) is not None},
}


class Refused(ValueError):
    pass


def _epoch(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e12 and math.isfinite(v)


def _study(name):
    if name not in STUDIES:
        raise Refused(f"unknown study {name!r}")
    return STUDIES[name]


def _current(path):
    """(the switch's bytes or None, its enabled window as a dict or None)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, None
    try:
        d = json.loads(raw)
    except (ValueError, RecursionError):
        return raw, None
    if isinstance(d, dict) and d.get("enabled") is True and _epoch(d.get("start")) and _epoch(d.get("end")):
        return raw, d
    return raw, None


def _history(md, name):
    """This study's HISTORY events in order; Refused if the file is there but cannot be read (fail closed)."""
    p = os.path.join(md, HISTORY)
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise Refused(f"history unreadable: {e}") from e
    out = []
    for l in lines:
        try:
            e = json.loads(l)
        except ValueError as err:
            raise Refused("history has a line that is not JSON") from err
        if isinstance(e, dict) and e.get("study") == name:
            out.append(e)
    return out


def _ran(events, current, now):
    """The windows that ran: [{"start", "end", "salt", "stop"}], from HISTORY and the switch as it is now (a window
    written by hand counts once it started)."""
    wins, last = [], None
    for e in events:
        if e.get("k") == "write" and _epoch(e.get("start")) and _epoch(e.get("end")) and _epoch(e.get("at")):
            if last is not None:
                last["stop"] = min(last["stop"], e["at"])
            last = {"start": e["start"], "end": e["end"], "salt": e.get("salt"), "stop": e["end"]}
            wins.append(last)
        elif e.get("k") == "off" and _epoch(e.get("at")) and last is not None:
            last["stop"] = min(last["stop"], e["at"])
            last = None
    if current is not None and not any((w["start"], w["end"], w["salt"]) == (current["start"], current["end"],
                                                                             current.get("salt")) for w in wins):
        wins.append({"start": current["start"], "end": current["end"], "salt": current.get("salt"),
                     "stop": current["end"]})
    return [w for w in wins if w["start"] < min(w["stop"], now)]


def _guard(md, name, path, start, salt, new_study):
    """The old switch bytes, or Refused: a running window, or a later window that would make one that ran longer."""
    old, window = _current(path)
    now = time.time()
    if window is not None and window["start"] <= now < window["end"]:
        raise Refused(f"a window is running ({window['start']} to {window['end']}): it is not moved, cut or extended")
    for w in _ran(_history(md, name), window, now):
        if STUDIES[name]["salt"]:
            if salt == w["salt"]:
                raise Refused(f"salt {salt!r} is a window that ran ({w['start']} to {w['end']}): the same arms again "
                              "would make that study longer; a new study needs a new salt")
            if start < w["end"] and not new_study:
                raise Refused(f"a window that ran ends at {w['end']}: a new study before then needs --new-study")
        else:
            if start < w["end"]:
                raise Refused(f"a window that ran ends at {w['end']}: the shadow starts again only after it")
            if not new_study:
                raise Refused("a window ran before: another shadow window is another study (--new-study)")
    return old


def _replace(path, data):
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path) or ".")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _log(md, event):
    """One HISTORY line; whether it was written."""
    try:
        os.makedirs(os.path.join(md, os.path.dirname(HISTORY)), exist_ok=True)
        with open(os.path.join(md, HISTORY), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except (OSError, TypeError, ValueError):
        return False


def write(md, name, start, end, salt=None, new_study=False):
    """Write the study's window; {"path", "body", "written"}, or Refused (nothing changed)."""
    s = _study(name)
    if not _epoch(start) or not _epoch(end):
        raise Refused("start and end must be finite epochs")
    hours = (end - start) / 3600
    if not 0 < start:
        raise Refused("the start must be a positive epoch")
    if not (hours > 0 if s["min_hours"] == 0 else hours >= s["min_hours"]):
        raise Refused(f"window {hours:.4f} h is empty or under the study's minimum {s['min_hours']} h")
    if hours > s["max_hours"]:
        raise Refused(f"window {hours:.4f} h is over the study's maximum {s['max_hours']} h")
    if s["salt"]:
        if not isinstance(salt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", salt):
            raise Refused("this study needs a salt of 1-64 [A-Za-z0-9_-]")
    elif salt is not None:
        raise Refused("this study takes no salt")
    path = os.path.join(md, s["file"])
    old = _guard(md, name, path, start, salt, new_study)
    if not start > time.time():
        raise Refused("the start must be after now: the file is written before its window")
    d = {"enabled": True, **({"salt": salt} if s["salt"] else {}), "start": start, "end": end}
    _replace(path, (json.dumps(d) + "\n").encode())
    written = os.stat(path).st_mtime
    ok = written <= start and s["reads"](md, start)
    if ok and not _log(md, {"k": "write", "study": name, "at": time.time(), "start": start, "end": end,
                            "salt": salt, "new_study": bool(new_study)}):
        ok = False
    if not ok:
        if old is None:
            os.unlink(path)
        else:
            _replace(path, old)
        raise Refused(f"the study's reader or the history would not take it (written {written}, start {start}); "
                      "put back as it was")
    return {"path": path, "body": d, "written": written}


def off(md, name):
    """{"enabled": false} over the study's switch, at once, with the window it stops."""
    s = _study(name)
    path = os.path.join(md, s["file"])
    _, window = _current(path)
    at = time.time()
    d = {"enabled": False}
    if window is not None:
        d["stopped"] = {"start": window["start"], "end": window["end"], "at": at,
                        **({"salt": window["salt"]} if isinstance(window.get("salt"), str) else {})}
    _replace(path, (json.dumps(d) + "\n").encode())
    logged = _log(md, {"k": "off", "study": name, "at": at, **({"window": d["stopped"]} if window is not None else {})})
    return {"path": path, "body": d, "written": os.stat(path).st_mtime, "history": logged}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--market-dir", default=".market")
    p.add_argument("study", choices=sorted(STUDIES))
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--salt")
    p.add_argument("--off", action="store_true")
    p.add_argument("--new-study", action="store_true", help="the window is another study after one that ran")
    a = p.parse_args(argv)
    try:
        out = off(a.market_dir, a.study) if a.off else write(a.market_dir, a.study, a.start, a.end, salt=a.salt,
                                                             new_study=a.new_study)
    except Refused as e:
        print(json.dumps({"refused": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
