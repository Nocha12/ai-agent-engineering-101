"""Write a windowed buyer study switch: .market/buyer_final_m1.json, buyer_moss_first.json or buyer_our_ask.json.

ax-pro-cycle step 5 (main instruction 10, 11/10 12:55): a timed study has a finite start and end both where it is treated
(oneshot.final_m1_on, buyer_moss_first._switch, buyer_our_ask._switch) and where its switch is made, so no one writes a
window by hand that has no end, starts in the past, or runs past the study's maximum. This writer:
- takes a finite start and end inside the study's bounds (final_m1 0 < end - start <= 12 h; moss_first and our_ask 9-12 h),
  a salt for the A/B studies (none for the final_m1 shadow), and a start after now: the file is written before its
  window, so its mtime is before the start, as the readers require;
- never writes over a window that is running (started, not over): a running window is not moved, cut or extended; one
  not yet started can be replaced by another future one, and one that is over can be followed;
- writes a temporary file in the same directory and renames it over the switch, then reads it back with the study's
  own reader at the start; if that reader would not take it, the old file is put back and it is refused;
- off writes {"enabled": false}, always allowed: stopping a study is never refused.

  python3 buyer_switch_write.py [--market-dir .market] final_m1 --start EPOCH --end EPOCH
  python3 buyer_switch_write.py [--market-dir .market] moss_first --start EPOCH --end EPOCH --salt SALT
  python3 buyer_switch_write.py [--market-dir .market] our_ask --off

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

STUDIES = {
    "final_m1": {"file": oneshot.FINAL_M1_SWITCH, "min_hours": 0, "max_hours": oneshot.FINAL_M1_MAX_HOURS, "salt": False,
                 "reads": lambda md, start: oneshot.final_m1_on(md, env={}, now=start)},
    "moss_first": {"file": buyer_moss_first.SWITCH, "min_hours": buyer_moss_first.MIN_HOURS,
                   "max_hours": buyer_moss_first.MAX_HOURS, "salt": True,
                   "reads": lambda md, start: buyer_moss_first._switch(md) is not None},
    "our_ask": {"file": buyer_our_ask.SWITCH, "min_hours": buyer_our_ask.MIN_HOURS, "max_hours": buyer_our_ask.MAX_HOURS,
                "salt": True, "reads": lambda md, start: buyer_our_ask._switch(md) is not None},
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
    """The switch's bytes and, if it is an enabled window, (start, end); (None, None) when there is no file."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, None
    try:
        d = json.loads(raw)
    except ValueError:
        return raw, None
    if isinstance(d, dict) and d.get("enabled") is True and _epoch(d.get("start")) and _epoch(d.get("end")):
        return raw, (d["start"], d["end"])
    return raw, None


def _replace(path, data):
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write(md, name, start, end, salt=None, now=None):
    """Write the study's window; {"path", "body", "written"}, or Refused (nothing changed)."""
    s = _study(name)
    now = time.time() if now is None else now
    if not _epoch(start) or not _epoch(end) or not _epoch(now):
        raise Refused("start, end and now must be finite epochs")
    hours = (end - start) / 3600
    if not 0 < start:
        raise Refused("the start must be a positive epoch")
    if not (hours > 0 if s["min_hours"] == 0 else hours >= s["min_hours"]):
        raise Refused(f"window {hours:.4f} h is empty or under the study's minimum {s['min_hours']} h")
    if hours > s["max_hours"]:
        raise Refused(f"window {hours:.4f} h is over the study's maximum {s['max_hours']} h")
    if not start > now:
        raise Refused("the start must be after now: the file is written before its window")
    if s["salt"]:
        if not isinstance(salt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", salt):
            raise Refused("this study needs a salt of 1-64 [A-Za-z0-9_-]")
    elif salt is not None:
        raise Refused("this study takes no salt")
    path = os.path.join(md, s["file"])
    old, window = _current(path)
    if window is not None and window[0] <= now < window[1]:
        raise Refused(f"a window is running ({window[0]} to {window[1]}): it is not moved, cut or extended")
    d = {"enabled": True, **({"salt": salt} if s["salt"] else {}), "start": start, "end": end}
    _replace(path, (json.dumps(d) + "\n").encode())
    written = os.stat(path).st_mtime
    if not (written <= start and s["reads"](md, start)):
        if old is None:
            os.unlink(path)
        else:
            _replace(path, old)
        raise Refused(f"the study's reader would not take it (written {written}, start {start}); put back as it was")
    return {"path": path, "body": d, "written": written}


def off(md, name):
    """{"enabled": false} over the study's switch, at once."""
    s = _study(name)
    path = os.path.join(md, s["file"])
    _replace(path, b'{"enabled": false}\n')
    return {"path": path, "body": {"enabled": False}, "written": os.stat(path).st_mtime}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--market-dir", default=".market")
    p.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("study", choices=sorted(STUDIES))
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--salt")
    p.add_argument("--off", action="store_true")
    a = p.parse_args(argv)
    try:
        out = off(a.market_dir, a.study) if a.off else write(a.market_dir, a.study, a.start, a.end, salt=a.salt,
                                                             now=a.now)
    except Refused as e:
        print(json.dumps({"refused": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
