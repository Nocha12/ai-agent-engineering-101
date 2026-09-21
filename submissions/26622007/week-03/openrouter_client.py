"""Small OpenRouter transport using only Python's standard library."""
import json
import os
from pathlib import Path
import re
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class ConfigurationError(Exception):
    pass


class CallError(Exception):
    pass


def read_key(env_file=None):
    """An explicit local file wins; never reuse an inherited generic OpenAI key."""
    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise ConfigurationError("Selected .env file does not exist.")
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if not sep or key.strip() not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
                continue
            try:
                parts = shlex.split(value, comments=True)
            except ValueError:
                raise ConfigurationError("Invalid quoting in the selected .env file.") from None
            values[key.strip()] = parts[0] if len(parts) == 1 else ""
        candidate = values.get("OPENROUTER_API_KEY", values.get("OPENAI_API_KEY", ""))
    else:
        candidate = os.environ.get("OPENROUTER_API_KEY", "")
    if not candidate.startswith("sk-or-") or len(candidate) < 25:
        raise ConfigurationError("OpenRouter key is not configured. No API request was sent.")
    return candidate


def redact(text, key=""):
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"sk-(?:or-v1-|proj-|ant-)[A-Za-z0-9_-]+", "[REDACTED]", text)


class OpenRouterClient:
    def __init__(self, key, config, opener=urlopen, sleeper=time.sleep):
        self.key, self.config = key, config
        self.opener, self.sleeper = opener, sleeper
        self.request_count = 0

    def complete(self, messages, emit, task_id, contractor, *, response_format=None):
        # Fail before logging a request or touching the network; never drop an unsupported format.
        if not isinstance(response_format, dict) or response_format.get("type") not in ("json_object", "json_schema"):
            raise ConfigurationError("response_format must explicitly select json_object or json_schema")
        if response_format["type"] == "json_schema":
            spec = response_format.get("json_schema")
            if (not isinstance(spec, dict) or spec.get("strict") is not True
                    or not isinstance(spec.get("name"), str) or not spec["name"].strip()
                    or not isinstance(spec.get("schema"), dict) or spec["schema"].get("type") != "object"):
                raise ConfigurationError("json_schema requires a name, strict=true and an object schema")
        if self.config.get("provider", {}).get("require_parameters") is not True:
            raise ConfigurationError("response_format requires provider.require_parameters=true")
        payload = {key: self.config[key] for key in
                   ("model", "temperature", "max_tokens", "reasoning", "provider")}
        payload.update(messages=messages, stream=False, response_format=response_format)
        # Snapshot once so every retry and the recorded payload match the serialized request.
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        payload = json.loads(encoded)
        tags = {"task_id": task_id, "contractor": contractor}
        for attempt in range(1, self.config["max_attempts"] + 1):
            if self.request_count >= self.config["max_http_requests"]:
                raise CallError("HTTP request budget reached")
            self.request_count += 1
            emit("request", **tags, attempt=attempt, payload=payload)
            request = Request(ENDPOINT, data=encoded,
                              headers={"Authorization": "Bearer " + self.key,
                                       "Content-Type": "application/json",
                                       "X-OpenRouter-Title": "AX Week 03 Contract Net"})
            started = time.monotonic()
            deadline = started + self.config.get("response_deadline_seconds", 90)
            retryable, error = False, ""
            try:
                with self.opener(request, timeout=self.config["timeout_seconds"]) as response:
                    chunks, size = [], 0
                    while True:
                        # A stream of heartbeat bytes must not keep read-all blocked indefinitely.
                        chunk = response.read1(min(65536, 2_000_001 - size))
                        if time.monotonic() > deadline:
                            raise TimeoutError("response body deadline exceeded")
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > 2_000_000:
                            raise CallError("response body size limit exceeded")
                    raw = b"".join(chunks).decode("utf-8")
            except HTTPError as exc:
                status = exc.code
                with exc:
                    body = redact(exc.read().decode("utf-8", errors="replace"), self.key)
                error, retryable = f"HTTP {status}", status in (408, 429, 500, 502, 503, 504)
                emit("http_error", **tags, attempt=attempt, status=status, response=body)
            except (URLError, TimeoutError, OSError):
                error, retryable = "transport error or timeout", True
                emit("transport_error", **tags, attempt=attempt, error=error)
            else:
                # Keep the provider's original response text, including parse failures.
                emit("response", **tags, attempt=attempt, elapsed_seconds=round(time.monotonic() - started, 3),
                     raw_response=redact(raw, self.key))
                try:
                    data = json.loads(raw)
                    if "error" in data:
                        raise ValueError("provider returned an error envelope")
                    choice = data["choices"][0]
                    content = choice["message"].get("content")
                except (ValueError, KeyError, IndexError, TypeError):
                    raise CallError("Malformed API response; original response recorded") from None
                emit("usage", **tags, usage=data.get("usage", {}),
                     model=data.get("model"), provider=data.get("provider"),
                     finish_reason=choice.get("finish_reason"))
                # Empty or truncated model content is counted by the bid parser, never repaired/retried.
                return content if isinstance(content, str) else ""
            if not retryable or attempt == self.config["max_attempts"]:
                raise CallError(error)
            delay = self.config["retry_delay_seconds"] * (2 ** (attempt - 1))
            emit("retry", **tags, attempt=attempt, delay_seconds=delay)
            self.sleeper(delay)
