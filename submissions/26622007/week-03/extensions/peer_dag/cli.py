"""Run a scripted demo, one bounded live experiment, or exact-input replay."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time
import uuid

from core import Limits, Outcome, Runtime, Task, evaluate, fingerprint
from fixtures import DemoModel
from models import BASE, LiveModel, ReplayModel, redact
from openrouter_client import ConfigurationError, read_key

ROOT = Path(__file__).resolve().parent


def load_config():
    config = json.loads((ROOT / "config.json").read_text())
    limits = Limits(**{k: v for k, v in config.items() if k != "transport"})
    transport = config["transport"]
    for key in ("max_tokens", "max_attempts", "max_http_requests"):
        if type(transport.get(key)) is not int or transport[key] < 1:
            raise ValueError(f"invalid transport {key}")
    for key in ("timeout_seconds", "retry_delay_seconds", "temperature"):
        value = transport.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid transport {key}")
    if (transport["timeout_seconds"] == 0 or transport["max_attempts"] > 2
            or transport["timeout_seconds"] > 60 or transport["max_tokens"] > 4096
            or transport["temperature"] > 2
            or transport["max_http_requests"] != transport["max_attempts"]):
        raise ValueError("transport must have a positive timeout and at most two attempts per call")
    return config, limits


class Recorder:
    def __init__(self, stream, key=""):
        self.stream, self.key, self.sequence = stream, key, 0

    def emit(self, event, **fields):
        self.sequence += 1
        record = {"seq": self.sequence, "at": datetime.now(timezone.utc).isoformat(),
                  "event": event, **fields}
        self.stream.write(redact(json.dumps(record, ensure_ascii=False, allow_nan=False), self.key) + "\n")
        self.stream.flush()


def committed_inputs():
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    # Freeze the evaluation contract before the first live request; never transmit it to a Worker.
    for name in ("case.json", "expected.json", "config.json"):
        path = ROOT / name
        saved = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(repo).as_posix()}"], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError(f"commit {name} before live execution")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


async def run(args):
    config, limits = load_config()
    if args.parallel is not None:
        limits = Limits(**dict(asdict(limits), max_parallel=args.parallel))
    task = Task.parse(json.loads((ROOT / "case.json").read_text()), root=True)
    expected = json.loads((ROOT / "expected.json").read_text())
    key, sha = "", ""
    if args.mode == "live":
        sha = committed_inputs()
        env_file = args.env_file or BASE.parent / ".env"
        key = read_key(env_file if env_file.exists() or args.env_file else None)
    state = {"mode": args.mode, "requester": args.requester, "limits": asdict(limits),
             "transport": config["transport"], "case_sha": fingerprint(asdict(task)),
             "expected_sha": fingerprint(expected),
             "selection_policy": "score, confidence, fixed per-child rotation v2",
             "sources": {p.name: fingerprint(p.read_text()) for p in sorted(ROOT.glob("*.py"))},
             "base_sources": {name: fingerprint((BASE / name).read_text())
                              for name in ("contract_net.py", "openrouter_client.py")},
             "git_commit": sha}
    experiment_id = fingerprint(state)[:16]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + args.mode + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / f"{run_id}.jsonl"
    started = time.monotonic()
    with log_path.open("x", encoding="utf-8") as stream:
        recorder = Recorder(stream, key)
        model = (LiveModel(key, config["transport"], recorder.emit) if args.mode == "live" else
                 ReplayModel(args.replay, args.replay_delay_ms / 1000, config["transport"])
                 if args.mode == "replay" else DemoModel())
        runtime = Runtime(model, limits, recorder.emit)
        recorder.emit("run_start", run_id=run_id, experiment_id=experiment_id, settings=state,
                      replay_source=str(args.replay) if args.replay else None,
                      replay_delay_ms=args.replay_delay_ms)
        try:
            outcome = await runtime.run(task, args.requester)
        except asyncio.CancelledError:
            outcome = Outcome("cancelled", error="interrupted; partial outputs retained")
        evaluation = evaluate(outcome, expected)
        replay_complete = model.complete() if isinstance(model, ReplayModel) else None
        result = {"run_id": run_id, "experiment_id": experiment_id, "mode": args.mode,
                  "status": outcome.status, "error": outcome.error,
                  "evaluation": evaluation, "calls": runtime.calls, "tasks": runtime.tasks,
                  "peak_calls": runtime.peak_calls, "elapsed_seconds": round(time.monotonic() - started, 4),
                  "http_requests": getattr(model, "http_requests", 0),
                  "reported_cost_usd": getattr(model, "cost", 0),
                  "cost_missing_responses": getattr(model, "cost_missing", 0),
                  "replay_complete": replay_complete, "log": str(log_path), "output": str(output)}
        for path, item in runtime.outcomes.items():
            target = (output / "artifacts").joinpath(*path.split("/")).with_suffix(".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact(json.dumps(asdict(item), ensure_ascii=False, indent=2), key) + "\n")
        (output / "result.json").write_text(redact(json.dumps(result, ensure_ascii=False, indent=2), key) + "\n")
        recorder.emit("run_end", result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if evaluation["passed"] and replay_complete is not False else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("demo", "live", "replay", "plan"))
    parser.add_argument("--requester", choices=("A", "B", "C"), default="A")
    parser.add_argument("--parallel", type=int, choices=(1, 2, 3))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--replay-delay-ms", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "replay" and args.replay is None:
        parser.error("replay mode requires --replay")
    if not 0 <= args.replay_delay_ms <= 1000:
        parser.error("replay delay must be between 0 and 1000 ms")
    if args.mode == "plan":
        config, limits = load_config()
        if args.parallel is not None:
            limits = Limits(**dict(asdict(limits), max_parallel=args.parallel))
        print(json.dumps({"limits": asdict(limits), "transport": config["transport"],
                          "max_http_requests": limits.max_calls * config["transport"]["max_attempts"],
                          "live_execution": "provided-data analysis artifacts only"}, ensure_ascii=False, indent=2))
        return 0
    return asyncio.run(run(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, ValueError, OSError) as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(2)
