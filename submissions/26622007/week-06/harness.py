"""Calls Claude Code non-interactively and returns a JSON answer that matches a schema.

The seller and the buyer both decide through ask(). Every call runs in a fresh
empty directory so Claude Code does not pick up this repository's CLAUDE.md, and
passes --json-schema so the answer comes back as structured output. The answer is
validated again here (fields, types, enums) before anyone uses it.
"""
import json
import subprocess
import sys
import tempfile
import time

MODEL = "haiku"
BASE_CMD = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "json"]
TIMEOUT = 90
RETRIES = 2


class HarnessError(RuntimeError):
    pass


def log(tag, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {tag} {msg}", file=sys.stderr, flush=True)


def validate(answer, schema):
    """Local check of the structured output: required fields, types, enums."""
    if not isinstance(answer, dict):
        raise HarnessError(f"answer is not an object: {answer!r}")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in answer:
            raise HarnessError(f"missing field {key!r} in {answer!r}")
    for key, value in answer.items():
        spec = props.get(key)
        if spec is None:
            raise HarnessError(f"unexpected field {key!r}")
        kind = spec.get("type")
        if kind == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise HarnessError(f"{key}={value!r} is not an integer")
        if kind == "string" and not isinstance(value, str):
            raise HarnessError(f"{key}={value!r} is not a string")
        if "enum" in spec and value not in spec["enum"]:
            raise HarnessError(f"{key}={value!r} not in {spec['enum']}")
    return answer


def ask(prompt, schema, system, tag="harness"):
    """One decision. Returns the validated structured output as a dict."""
    cmd = BASE_CMD + ["--system-prompt", system, "--json-schema", json.dumps(schema)]
    last = None
    for attempt in range(1, RETRIES + 1):
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="harness-") as empty:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      cwd=empty, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                last = f"timeout after {TIMEOUT}s"
                log(tag, f"attempt {attempt}: {last}")
                continue
        took = time.time() - started
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = f"exit {proc.returncode}, stdout not JSON: {proc.stdout[:200]!r} {proc.stderr[:200]!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        if out.get("is_error"):
            last = f"harness error: {out.get('result')!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        try:
            answer = validate(out.get("structured_output"), schema)
        except HarnessError as e:
            last = str(e)
            log(tag, f"attempt {attempt}: invalid answer: {last}")
            continue
        log(tag, f"claude -p --model {MODEL} --json-schema(set) {took:.1f}s -> {json.dumps(answer, ensure_ascii=False)}")
        return answer
    raise HarnessError(f"no valid answer after {RETRIES} attempts: {last}")


if __name__ == "__main__":
    demo = {"type": "object", "additionalProperties": False, "required": ["action", "price"],
            "properties": {"action": {"type": "string", "enum": ["counter", "accept", "reject"]},
                           "price": {"type": "integer"}}}
    print(ask("Lamp, reserve 100. The buyer offered 80. What do you do?", demo, "You are a shop owner."))
