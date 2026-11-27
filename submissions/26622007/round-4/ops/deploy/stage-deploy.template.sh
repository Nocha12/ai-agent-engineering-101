#!/bin/bash
# deploy-drain template (11/27, branch ax-r4-deploy-drain-1127, 대표님 11/26-27: restarts have no count limit, reviewed fixes
#   go live right away): copy of ~/.ax-fu-stage/stage-deploy.sh where only the quiet-window loop and the check right
#   before the stop call ~/.ax-deploy-common/drain_gate.py instead of ~/.ax-v6-51422de9/activity.py: wait = phase 1
#   the activity.py rule for QUIET_TRIES x QUIET_SLEEP s, then phase 2 drain (no message unanswered in 10 min, no turn
#   or wallet send in flight; issued unpaid checkouts do not block, see drain_gate.py) up to AXD_GATE_DRAIN_MAX s,
#   else exit 24 with nothing changed; right before the stop = one drain check. Future stages copy this file and
#   change only the stage parts (header, files, INPUTS_SHA, lock line, TOOLS4, forge/notes steps).
# forum-unit v1 (11/27 more-rca A-2 #15 05fcd1d5, 업무 W-20261124-1650-AXRound4준비): copied from ~/.ax-cotext-stage/stage-deploy.sh,
#   only these changed: files = the 5 in SHA256SUMS from commit 4c3c4ee8 (branch ax-r4-forum-unit-1127, base origin/prod
#   3d88e9f2); BASE.SHA256 = the prod files now (= origin/prod 3d88e9f2 blobs) + every file of tools/check_item_inventory
#   (v12); SHA256SUMS = PROD.SHA256 = commit blobs; INPUTS_SHA below; lock line "forum-unit"; TOOLS4 =
#   check_item_inventory, forged to v13 by install_tools.py --only check_item_inventory after the stop, forged
#   logic.py/test.py must equal ops/tools_src. notes: ONE line appended (notes-lines.json via notes-patch.py apply, as in
#   ~/.ax-cashout-stage; restore = the backup taken right before the stop). forum_menu runs as a thread inside the kit,
#   so it and the tool are live only after the one kit restart this script does inside ~/.ax-runtime.lock.
#   NOTE: ~/.ax-cashout-stage (d464baf8) changes forum_menu.py, test_forum_menu.py and check_item_inventory too; whichever
#   goes second stops at baseline drift (exit 21, nothing changed) and needs a rebased commit and a new stage.
#   Run: AXD_SESSION=<key> nohup /bin/bash ~/.ax-fu-stage/stage-deploy.sh >/dev/null 2>&1 &   then read deploy.log;
#   release the kept lock after checks: rm ~/.ax-runtime.lock/owner && rmdir ~/.ax-runtime.lock
#   Rollback: the script restores backup/src.tar, backup/tool-check_item_inventory and notes itself on a failed start;
#   by hand later = inside the lock tar xf backup/src.tar, copy backup/tool-check_item_inventory (v12) back,
#   /usr/bin/python3 -I notes-patch.py restore notes.md backup/notes.md.untouched (or delete just the one appended line
#   if the shop wrote notes since), restart the kit the same way. logs/forum-menu-units.json may stay (only read by the
#   new tool) or be deleted.
# --- cotext header follows
# checkout-text v1 (11/27 more-rca A-1 #10 0b2461b6 / #13 01daff51): copied from ~/.ax-menulead-stage/stage-deploy.sh,
#   only these changed: files = the 6 below from commit 4a62cf8e (branch ax-r4-checkout-text-1127, base origin/prod
#   8cd8d3ca); BASE.SHA256 = the prod files now (= origin/prod 8cd8d3ca blobs) + every file of tools/check_item_inventory
#   (v11); SHA256SUMS = PROD.SHA256 = commit blobs; INPUTS_SHA below; lock line "checkout-text"; TOOLS4 =
#   check_item_inventory, forged to v12 by install_tools.py --only check_item_inventory after the stop (the s2 forge
#   step, one tool), forged logic.py/test.py must equal ops/tools_src. No notes change. The wrapper
#   (agent_kit_records.py) and the tool are live only after the one kit restart this script does inside ~/.ax-runtime.lock.
#   Run: AXD_SESSION=<key> nohup /bin/bash ~/.ax-cotext-stage/stage-deploy.sh >/dev/null 2>&1 &   then read deploy.log;
#   release the kept lock after checks: rm ~/.ax-runtime.lock/owner && rmdir ~/.ax-runtime.lock
#   Rollback: the script restores backup/src.tar and backup/tool-check_item_inventory itself on a failed start; by hand
#   later = inside the lock copy back the origin/prod 8cd8d3ca blobs of the 6 files and backup/tool-check_item_inventory
#   (v11), then restart the kit the same way (or rerun a copy of this script with SHA256SUMS = those blobs).
# --- menulead header follows
# forum-menu-lead v1 (11/27 12:40 check, 5절 1번): copied from ~/.ax-codata-stage/stage-deploy.sh, only these changed:
#   files = ops/forum_menu.py + ops/test_forum_menu.py from commit 8cd8d3ca (branch ax-r4-menu-lead-1127, base origin/prod
#   0ac45918); BASE.SHA256 = the prod files now (forum_menu f68d56b1…, test 9555ed28…); SHA256SUMS = PROD.SHA256 = commit
#   blobs; INPUTS_SHA below; lock line "forum-menu-lead". No tools forged (TOOLS4 empty), no notes change.
#   forum_menu runs as a thread inside the kit (agent_kit_records._menu_hook imports it once at shop join), so the
#   new file is live only after the one kit restart this script does inside ~/.ax-runtime.lock.
#   Run: AXD_SESSION=<key> nohup /bin/bash ~/.ax-menulead-stage/stage-deploy.sh >/dev/null 2>&1 &   then read deploy.log;
#   release the kept lock after checks: rm ~/.ax-runtime.lock/owner && rmdir ~/.ax-runtime.lock
#   Rollback: the script restores backup/src.tar itself on a failed start; by hand later = copy the origin/prod 0ac45918
#   blobs back (sha f68d56b1… / 9555ed28…) inside the lock and restart the kit the same way.
# --- original header (codata r3) follows
# checkout-data r3 (W-20261124-1650-AXRound4준비, 대표님 #13669 "고쳐"): sales-2 flow unchanged; ops/agent_kit_records.py,
# ops/checkout_delivery.py + 2 tests only (data part + token-free marker on every other answer). No notes, no tool forge.
# step-2 display deploy (v3-s2 = v3-s1 with four tools): the shared display name block (대표님 11/27 01:26) in
# forum_menu.py, check_item_inventory, write_shop_card, refresh_full_card and restock_shoutout, from origin/prod
# (review C0 I0). No notes change; three tools forged by install_tools.py --only, restock_shoutout by
# install_restock_shoutout.py. Run on market from ~/.ax-s2-stage. One restart inside ~/.ax-runtime.lock.
#
# v2 (20:3x, review C0/I4 on v1 60ee30e1):
#   - no `set -e`: every comparison/step is an explicit `if`/`||` that returns or exits non-zero
#   - baseline = BASE.SHA256: every file the deploy touches (each deploy path + every file of the 4 tool dirs),
#     absent paths pinned as ABSENT; any single mismatch/missing/extra stops before anything changes
#   - deploy inputs pinned: INPUTS.SHA256 (SHA256SUMS, BASE.SHA256, notes-append.txt) checked first, and INPUTS.SHA256
#     itself is pinned below (INPUTS_SHA); the wrapper's own hash is the reviewed one in README/REVIEW
#   - candidate start/readiness failure -> stop OUR candidate wrapper and restore inside the lock; restore failures
#     are summed; old start is checked for readiness
#   - exit codes: 0 deployed+ready, 20 lock busy, 10-13 inputs, 21 baseline drift (nothing changed),
#     22-24 process/activity precheck (nothing changed), 25 old kit did not stop (nothing changed, state printed),
#     26 rolled back and old kit ready, 29 RESTORE INCOMPLETE / manual (state printed)
# v2d (21:3x, review C0/I1/M7 on 3254f122):
#   - start() keeps the launched pid ($!) and waits for the pid file to name it; rollback stops that pid even when
#     the pid file is empty or names something else
#   - every kit of this round dir (python …/round-4/ops/agent_kit_records.py run) is counted: exactly the old one
#     before the stop, none before restore/restart (orphans left by a dead wrapper are stopped), exactly the new
#     wrapper's child at readiness
#   - stop_wrapper re-reads the kit pids after the wait and handles each pid
#   - activity re-checked right before the stop; notes.md must not already carry the clearance tag
#   - notes restore = backup + whatever followed backup+notes-append (no grep -v; empty backup works)
#   - exits before the lock also go to deploy.log; the EXIT trap is set right after mkdir
# v2c (re-review C1 on bb5fe824): start() resets HUP/INT/TERM/PIPE in a subshell so the new run_round4.sh and kit
#   do not inherit the ignores (their leave() trap works).
#   Run detached: AXD_SESSION=<key> nohup /bin/bash ~/.ax-clr-stage/stage-deploy.sh >/dev/null 2>&1 & ; then read deploy.log.
#   Release the kept lock after checks: rm ~/.ax-runtime.lock/owner && rmdir ~/.ax-runtime.lock
# v2b (review C0/I1/M6 on 653d1225): all output goes to $W/deploy.log right after the lock (no tty/pipe death);
#   HUP/INT/TERM/PIPE ignored from the stop on; lock KEPT on exit 0 (operator verifies, then releases) and on 25/29
#   (owner gets a BROKEN line); inputs re-checked after the wait; notes backup after the stop; exact path match.
set -u
W="$(cd "$(dirname "$0")" && pwd)" || exit 10
R="$HOME/ax-round4/submissions/26622007/round-4"
PY="$HOME/ax-round4-venv/bin/python"
NPY=/usr/bin/python3
GATE="$HOME/.ax-deploy-common/drain_gate.py"
LOCK="$HOME/.ax-runtime.lock"
APP_URL="${AXD_APP_URL:-http://127.0.0.1:8800/}"
QUIET_TRIES="${AXD_QUIET_TRIES:-45}"     # activity re-check every QUIET_SLEEP s (default 15 min) before giving up
QUIET_SLEEP="${AXD_QUIET_SLEEP:-20}"
READY_TRIES="${AXD_READY_TRIES:-48}"     # x5 s (kit start->app up was 81-89 s in 12 restarts)
PIDFILE_TRIES="${AXD_PIDFILE_TRIES:-20}" # x1 s for a launched run_round4.sh to write its pid file
TOOLS4="check_item_inventory"
srcdir() { [ "$1" = restock_shoutout ] && echo ops/restock_shoutout || echo "ops/tools_src/$1"; }
INPUTS_SHA=4e69386978ef87f8fcd45171b75737fee3ef4bc8e3b7268edf6d68354f241c70

trap '' PIPE
early() { echo "$1"; printf '%s %s (exit %s, before lock)\n' "$(date '+%F %T')" "$1" "$2" >>"$W/deploy.log" 2>/dev/null; exit "$2"; }
cd "$W" || exit 10
[ "$(shasum -a 256 INPUTS.SHA256 2>/dev/null | cut -d' ' -f1)" = "$INPUTS_SHA" ] || early 'INPUTS.SHA256 is not the reviewed one' 11
shasum -a 256 -c INPUTS.SHA256 >/dev/null 2>&1 || early 'deploy inputs drift' 11
shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 || early 'candidate drift' 12
FILES="$(awk '{print $2}' SHA256SUMS)" || early 'no file list' 12
[ -n "$FILES" ] || early 'empty file list' 12
cd "$R" || early 'no round dir' 13

line="W-20261124-1650-AXRound4준비 forum-unit $(date '+%F %T') pid=$$ session=${AXD_SESSION:-unknown} observe-until=$(date -v+60M '+%F %T' 2>/dev/null)"
mkdir "$LOCK" 2>/dev/null || early "lock busy: $(cat "$LOCK/owner" 2>/dev/null)" 20
release() {
  local rc=$?
  if [ ! -e "$LOCK/owner" ]; then rmdir "$LOCK" 2>/dev/null; return; fi   # died between mkdir and the owner line
  [ "$(head -1 "$LOCK/owner" 2>/dev/null)" = "$line" ] || return
  case "$rc" in
    0)    printf '%s\n' "deployed, operator verifying; release after checks" >>"$LOCK/owner";;
    25|29) printf '%s\n' "BROKEN exit $rc: production needs manual check (see $W/deploy.log)" >>"$LOCK/owner";;
    *)    mv "$LOCK/owner" "$W/lock.released" && rmdir "$LOCK";;
  esac
}
trap release EXIT
printf '%s\n' "$line" >"$LOCK/owner" || exit 20
exec >>"$W/deploy.log" 2>&1
echo "=== $line"
trap 'echo "signal before the stop: nothing changed"; exit 30' HUP INT TERM

sha() { [ -f "$1" ] || { echo missing; return 0; }; shasum -a 256 "$1" | cut -d' ' -f1; }
baseline() {   # 0 only if every pinned path matches; prints each mismatch
  local rc=0 want path got n=0
  while read -r want path; do
    [ -n "$path" ] || continue
    n=$((n + 1))
    if [ "$want" = ABSENT ]; then
      if [ -e "$path" ]; then echo "baseline: $path should be absent"; rc=1; fi
    else
      got="$(sha "$path")"
      if [ "$got" != "$want" ]; then echo "baseline: $path $got"; rc=1; fi
    fi
  done <"$W/BASE.SHA256"
  [ "$n" -gt 0 ] || { echo 'baseline: empty manifest'; rc=1; }
  for t in $TOOLS4; do      # no unpinned extra file in a tool dir we replace
    for f in "tools/$t"/* "tools/$t"/.[!.]*; do
      [ -e "$f" ] || continue
      [ "$(basename "$f")" = __pycache__ ] && continue   # compiled cache of the tool, not a pinned source
      if ! awk -v f="$f" '$2==f{x=1} END{exit !x}' "$W/BASE.SHA256"; then echo "baseline: extra $f"; rc=1; fi
    done
  done
  return $rc
}
kids() { pgrep -P "$1" -f 'ops/agent_kit_records.py run' 2>/dev/null; }
kitprocs() {   # every python kit process of this round dir, whoever its parent is (one pid per line)
  ps -axo pid=,command= | awk -v p="$R/ops/agent_kit_records.py run" 'index($0, p) && $2 ~ /[Pp]ython/ {print $1}'
}
is_wrapper() { [ -n "$1" ] || return 1; case "$(ps -p "$1" -o command= 2>/dev/null)" in *run_round4.sh*) return 0;; esac; return 1; }
stop_wrapper() {   # TERM the wrapper; after 40 s KILL it and every kit of this round dir; 0 once all are gone
  local w="$1" i p
  kill -TERM "$w" 2>/dev/null
  for i in $(seq 1 40); do
    if ! kill -0 "$w" 2>/dev/null && [ -z "$(kitprocs)" ]; then break; fi
    sleep 1
  done
  if kill -0 "$w" 2>/dev/null || [ -n "$(kitprocs)" ]; then
    kill -KILL "$w" 2>/dev/null
    for p in $(kitprocs); do        # read again: a respawned or orphaned kit has a new pid
      echo "stop: KILL kit $p (wrapper $w)"
      pkill -KILL -P "$p" 2>/dev/null; kill -KILL "$p" 2>/dev/null
    done
    sleep 2
  fi
  if kill -0 "$w" 2>/dev/null || [ -n "$(kitprocs)" ]; then return 1; fi
  [ "$(cat .kit/run_round4.pid 2>/dev/null)" = "$w" ] && rm -f .kit/run_round4.pid   # a KILLed wrapper leaves it
  return 0
}
start() {   # sets NEWPID to the launched wrapper; 0 once the pid file names it and it runs run_round4.sh
  local i p=""
  ( trap - HUP INT TERM PIPE; exec nohup ./run_round4.sh </dev/null >/dev/null 2>&1 ) &   # v2c: no inherited ignores
  NEWPID=$!
  disown
  for i in $(seq 1 "$PIDFILE_TRIES"); do
    sleep 1
    p="$(cat .kit/run_round4.pid 2>/dev/null)"
    if [ "$p" = "$NEWPID" ] && is_wrapper "$NEWPID"; then return 0; fi
    kill -0 "$NEWPID" 2>/dev/null || { echo "start: launched wrapper $NEWPID exited (pidfile=${p:-none})"; return 1; }
  done
  echo "start: pid file (${p:-none}) never named launched wrapper $NEWPID"
  return 1
}
ready() {   # the only kit of this round dir is NEWPID's child, and the app answers 401
  local i kid all code
  for i in $(seq 1 "$READY_TRIES"); do
    kid="$(kids "$NEWPID")"; all="$(kitprocs)"
    code="$(curl -sS -m 3 -o /dev/null -w '%{http_code}' "$APP_URL" 2>/dev/null)"
    if [ -n "$kid" ] && [ "$all" = "$kid" ] && [ "$code" = 401 ]; then
      echo "wrapper=$NEWPID kit child=$kid app_http=$code"; return 0
    fi
    sleep 5
  done
  echo "not ready: wrapper=$NEWPID kid=${kid:-none} round kits=$(echo $all) app_http=${code:-none}"
  return 1
}
state() {
  echo "--- state"; echo "pidfile=$(cat .kit/run_round4.pid 2>/dev/null) launched=${NEWPID:-none}"
  pgrep -fl 'run_round4.sh|agent_kit_records.py run' 2>/dev/null
  echo "round kits: $(kitprocs | tr '\n' ' ')"
  baseline >/dev/null 2>&1 && echo 'files: baseline (old)' || echo 'files: NOT baseline'
}
restore() {   # 0 only if every step worked and the files are back to baseline
  local bad=0 f t
  for f in $FILES; do
    if ! grep -qxF "$f" "$W/backup/existing.txt"; then rm -f "$f" || bad=1; fi
    rm -f "$f.clr-$$"
  done
  tar xf "$W/backup/src.tar" || bad=1
  for t in $TOOLS4; do
    rm -rf "tools/$t" || bad=1
    cp -Rp "$W/backup/tool-$t" "tools/$t" || bad=1
  done
  if [ -f "$W/notes-patch.py" ] && [ -f "$W/backup/notes.md.untouched" ] && [ -f "$W/notes.patched" ]; then
    "$NPY" -I "$W/notes-patch.py" restore notes.md "$W/backup/notes.md.untouched" || bad=1
  fi
  baseline || bad=1
  return $bad
}
rollback() {   # $1 reason; $2 = 1 if our candidate wrapper may be running
  echo "rollback: $1"
  local p
  if [ "${2:-0}" = 1 ]; then
    for p in "$NEWPID" "$(cat .kit/run_round4.pid 2>/dev/null)"; do
      [ -n "$p" ] && kill -0 "$p" 2>/dev/null || continue
      if [ "$p" = "$OLDPID" ] || ! is_wrapper "$p"; then
        echo "RESTORE INCOMPLETE: running pid $p is not our candidate wrapper"; state; exit 29
      fi
      stop_wrapper "$p" || { echo "RESTORE INCOMPLETE: candidate $p did not stop"; state; exit 29; }
    done
  fi
  if [ -n "$(kitprocs)" ]; then echo "RESTORE INCOMPLETE: a kit of this round dir still runs"; state; exit 29; fi
  if ! restore; then echo "RESTORE INCOMPLETE: $1 (files)"; state; exit 29; fi
  NEWPID=""
  if start && ready; then echo "restored: $1"; exit 26; fi
  echo "RESTORE INCOMPLETE: $1 (old kit not ready)"; state; exit 29
}

baseline || { echo 'baseline drift'; exit 21; }
OLDPID="$(cat .kit/run_round4.pid 2>/dev/null)"
NEWPID=""
is_wrapper "$OLDPID" || { echo 'wrapper mismatch'; exit 22; }
OLDKID="$(kids "$OLDPID")"
[ "$(echo "$OLDKID" | grep -c '^[0-9]')" = 1 ] || { echo 'kit child count != 1'; exit 23; }
[ "$(kitprocs)" = "$OLDKID" ] || { echo "round kits ($(kitprocs | tr '\n' ' ')) != the old wrapper's child $OLDKID"; exit 23; }

AXD_GATE_QUIET_MAX=$((QUIET_TRIES * QUIET_SLEEP)) AXD_GATE_SLEEP="$QUIET_SLEEP" "$PY" -I "$GATE" wait ||
  { echo "no quiet window and no drain (gate exit $?)"; exit 24; }
[ "$(cat .kit/run_round4.pid 2>/dev/null)" = "$OLDPID" ] || { echo 'wrapper changed'; exit 24; }
( cd "$W" && shasum -a 256 -c INPUTS.SHA256 >/dev/null && shasum -a 256 -c SHA256SUMS >/dev/null ) || { echo 'inputs drift after wait'; exit 11; }
baseline || { echo 'baseline drift after wait'; exit 21; }

rm -rf "$W/backup" && mkdir "$W/backup" || exit 13
: >"$W/backup/existing.txt"
existing=""
for f in $FILES; do [ -e "$f" ] && { existing="$existing $f"; echo "$f" >>"$W/backup/existing.txt"; }; done
tar cf "$W/backup/src.tar" $existing || exit 13
for t in $TOOLS4; do cp -Rp "tools/$t" "$W/backup/tool-$t" || exit 13; done
cp -p notes.md "$W/backup/notes.md.untouched" 2>/dev/null
"$PY" -I "$GATE" drain || { echo 'not drained right before the stop (files unchanged)'; exit 24; }

trap '' HUP INT TERM PIPE
date '+stop %F %T'
if ! stop_wrapper "$OLDPID"; then echo 'old wrapper or kit did not stop (files unchanged)'; state; exit 25; fi

for f in $FILES; do
  mkdir -p "$(dirname "$f")" || rollback mkdir
  { cp -p "$W/$f" "$f.clr-$$" && mv "$f.clr-$$" "$f"; } || rollback "copy $f"
done
shasum -a 256 -c "$W/SHA256SUMS" >/dev/null || rollback hash
shasum -a 256 -c "$W/PROD.SHA256" >/dev/null || rollback 'not origin/prod blobs'
"$PY" -I ops/install_tools.py --kit "$HOME/ax-round4/scripts/round4" \
  --only check_item_inventory \
  >"$W/forge.out" 2>&1 || rollback forge
[ "$(grep -c 'passed its test' "$W/forge.out")" = 1 ] || rollback forge-out
for t in $TOOLS4; do
  [ "$(sha "tools/$t/logic.py")" = "$(sha "$(srcdir "$t")/logic.py")" ] || rollback "forged-hash $t"
  [ "$(sha "tools/$t/test.py")" = "$(sha "$(srcdir "$t")/test.py")" ] || rollback "forged-hash $t test.py"
done
if [ -f "$W/notes-patch.py" ]; then
  : >"$W/notes.patched"
  "$NPY" -I "$W/notes-patch.py" apply notes.md "$W/notes-lines.json" || rollback notes-patch
fi

start || rollback 'candidate start' 1
date '+started %F %T'
ready || rollback 'candidate readiness' 1
echo deployed
exit 0
