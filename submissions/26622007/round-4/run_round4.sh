#!/usr/bin/env bash
# Keeps nocha12 in the Round 4 market on this machine (kit v5.1), restarting the kit if it stops.
#
#   nohup submissions/26622007/round-4/run_round4.sh > /dev/null 2>&1 &
#   kill $(cat submissions/26622007/round-4/.kit/run_round4.pid)        # leave the market
#   submissions/26622007/round-4/run_round4.sh --check                   # print what it would run, start nothing
#
# Secrets come from the .env next to the Round 3 folder (gitignored; R4_ENV_FILE overrides the path) and only these
# two are exported:
#   OPENROUTER_API_KEY   the agent's model (llm in agent.json)
#   MARKET_OWNER         the claim code that links the agent to @Nocha12
# The terminal output carries the mini app links with their access code (?k=...), so it goes to .kit/stdout.log
# (mode 600, gitignored), never to logs/. The kit writes logs/agent-kit.log, react.jsonl and forge.jsonl itself.
#
# Options (environment):
#   R4_WITH_BUYER=1   also join as a buyer (kit v5.1 runs a shop only by default); the buyer pays from its own wallet,
#                     which needs tOKRW from https://faucet.maroo.io
#   R4_VENV           the virtualenv with the kit's packages (default ~/ax-round4-venv)
#   R4_APP_PORT       the mini app's local port (default 8800)
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"   # cloudflared
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
ENV_FILE="${R4_ENV_FILE:-$HOME/ai-agent-engineering-101/submissions/26622007/.env}"
VENV="${R4_VENV:-$HOME/ax-round4-venv}"
NAME="${MARKET_NAME:-nocha12}"
MARKET="${R4_MARKET:-https://market.ourolabs.ai}"
APP_PORT="${R4_APP_PORT:-8800}"
PID_FILE="$HERE/.kit/run_round4.pid"
OUT="$HERE/.kit/stdout.log"

read_env() {                     # POSIX classes: macOS sed does not know \s
  grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$ENV_FILE" | tail -1 | tr -d '\r' |
    sed -E "s/^[[:space:]]*(export[[:space:]]+)?$1=//; s/^[\"']//; s/[\"'][[:space:]]*$//"
}

ARGS=(run --dir "$HERE" --market "$MARKET" --name "$NAME" --tunnel --app "$APP_PORT" --app-tunnel)
[ "${R4_WITH_BUYER:-0}" = 1 ] && ARGS+=(--with-buyer)

if [ "${1:-}" = --check ]; then
  echo "env file: $ENV_FILE ($( [ -r "$ENV_FILE" ] && echo readable || echo MISSING))"
  for k in OPENROUTER_API_KEY MARKET_OWNER; do
    [ -n "$(read_env "$k" 2>/dev/null)" ] && echo "  $k: set" || echo "  $k: MISSING"
  done
  echo "venv: $VENV ($( [ -x "$VENV/bin/python" ] && "$VENV/bin/python" --version 2>&1 || echo MISSING))"
  echo "cloudflared: $(command -v cloudflared || echo MISSING)"
  echo "would run: python scripts/round4/agent_kit.py ${ARGS[*]}"
  exit 0
fi

mkdir -p "$HERE/.kit" "$HERE/logs"
chmod 700 "$HERE/.kit"
export OPENROUTER_API_KEY="$(read_env OPENROUTER_API_KEY)"
export MARKET_OWNER="$(read_env MARKET_OWNER)"
[ -n "$OPENROUTER_API_KEY" ] && [ -n "$MARKET_OWNER" ] || { echo "[run_round4] $ENV_FILE lacks a key" >&2; exit 1; }

old="$(cat "$PID_FILE" 2>/dev/null)"
if [ -n "$old" ] && ! kill -0 "$old" 2>/dev/null && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$old" ]; then
  rm -f "$PID_FILE"               # left by a run that was killed hard
fi
if ! ( set -o noclobber; echo $$ > "$PID_FILE" ) 2>/dev/null; then
  echo "[run_round4] already running as $(cat "$PID_FILE")" >&2
  exit 1
fi

touch "$OUT" && chmod 600 "$OUT"
KIT=""
back=0
leave() {
  # A job started with & ignores SIGINT, so the kit's own Ctrl-C cleanup never runs: stop its cloudflared tunnels
  # and tool runs first, then the kit.
  if [ -n "$KIT" ]; then
    pkill -TERM -P "$KIT" 2>/dev/null
    kill -TERM "$KIT" 2>/dev/null
    wait "$KIT" 2>/dev/null
  fi
  rm -f "$PID_FILE"
  exit 0
}
trap leave INT TERM

cd "$ROOT"
/usr/bin/caffeinate -i -w $$ &          # keeps the Mac awake while this script lives
while true; do
  echo "[run_round4] $(date '+%F %T') start: agent_kit.py ${ARGS[*]}" >> "$OUT"
  started=$SECONDS
  stop=""                         # hold a TERM that comes before KIT=$! (an ignored TERM would be inherited by the kit)
  trap 'stop=1' INT TERM
  "$VENV/bin/python" scripts/round4/agent_kit.py "${ARGS[@]}" >> "$OUT" 2>&1 &
  KIT=$!
  trap leave INT TERM
  [ -n "$stop" ] && leave
  wait "$KIT"
  rc=$?
  pkill -TERM -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$APP_PORT\$" 2>/dev/null   # the app tunnel of a kit that died hard
  KIT=""
  if [ $((SECONDS - started)) -lt 120 ]; then back=$(( back < 600 ? (back ? back * 2 : 30) : 600 )); else back=30; fi
  [ "$back" -gt 600 ] && back=600
  echo "[run_round4] $(date '+%F %T') the kit stopped (exit $rc); starting again in $back s" >> "$OUT"
  sleep "$back" &
  wait $!                         # wait, not a plain sleep: a TERM must not wait for the sleep to finish
done
