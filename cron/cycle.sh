#!/usr/bin/env bash
# 3-hourly refresh cycle for the viral-ads engine.
#
# Default stages are the INTELLIGENCE refresh (harvest -> enrich -> trends -> cluster).
# `prompts` is deliberately NOT in the 3-hourly cycle: it generates ~198 prompts per run,
# so every 3h would be ~1,600 LLM generations a day for trend data that moves far slower.
# It runs once daily instead (see PROMPTS_HOUR). Override either with env vars.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

STAGES="${STAGES:-harvest enrich trends cluster}"
PROMPTS_HOUR="${PROMPTS_HOUR:-4}"          # run the prompt sweep on the cycle nearest this hour
KEEP_DAYS="${KEEP_DAYS:-3}"                # prune renders older than this
LOG="$ROOT/logs/cycle-$(date +%Y%m%d).log"
LOCK="$ROOT/logs/.cycle.lock"
STATE="$ROOT/data/.schedule.json"
PY="${PYTHON:-python3}"

mkdir -p "$ROOT/logs" "$ROOT/data"
exec 9>"$LOCK"
if ! flock -n 9 2>/dev/null; then
  # macOS has no flock(1); fall back to a pid file check
  if [ -f "$ROOT/logs/.cycle.pid" ] && kill -0 "$(cat "$ROOT/logs/.cycle.pid" 2>/dev/null)" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) previous cycle still running, skipping" >> "$LOG"
    exit 0
  fi
fi
echo $$ > "$ROOT/logs/.cycle.pid"
trap 'rm -f "$ROOT/logs/.cycle.pid"' EXIT

[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a

HOUR=$(date +%-H)
if [ "$HOUR" -ge "$PROMPTS_HOUR" ] && [ "$HOUR" -lt "$((PROMPTS_HOUR+3))" ]; then
  STAGES="$STAGES prompts"
fi

START=$(date -u +%FT%TZ); T0=$(date +%s)
{
  echo "=============================================================="
  echo "cycle start $START   stages: $STAGES"
  echo "=============================================================="
} >> "$LOG"

"$PY" -u run.py --only $STAGES >> "$LOG" 2>&1
RC=$?
T1=$(date +%s)

# prune renders past the retention window (the UI hides them anyway; this reclaims disk)
find "$ROOT/data/renders" -type f \( -name '*.mp4' -o -name '*.mov' \) -mtime +"$KEEP_DAYS" -delete 2>/dev/null
find "$ROOT/logs" -name 'cycle-*.log' -mtime +14 -delete 2>/dev/null

"$PY" - "$STATE" "$START" "$RC" "$((T1-T0))" "$STAGES" <<'PYEOF'
import json, sys, os, datetime
state_fp, start, rc, secs, stages = sys.argv[1:6]
prev = {}
if os.path.exists(state_fp):
    try: prev = json.load(open(state_fp))
    except Exception: prev = {}
hist = (prev.get("history") or [])[-47:]
entry = {"start": start, "rc": int(rc), "seconds": int(secs), "stages": stages.split(),
         "status": "ok" if int(rc) == 0 else "failed"}
hist.append(entry)
json.dump({"last_run": start, "last_status": entry["status"], "last_rc": int(rc),
           "last_seconds": int(secs), "last_stages": entry["stages"],
           "next_hint": "every 3 hours", "history": hist},
          open(state_fp, "w"), indent=1)
PYEOF

echo "cycle end rc=$RC in $((T1-T0))s" >> "$LOG"
exit $RC
