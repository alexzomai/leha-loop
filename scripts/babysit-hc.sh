#!/bin/bash
# Watches an agent in hc (herdr-core) until its turn ends, then prints the screen tail.
# usage: babysit-hc.sh <terminal> [lines]
# - If a leha-loop script just sent the agent something, waits for that newer turn
#   (no instant exit with the previous turn's screen).
# - Asks the agent to continue when the API connection dropped (status "error", or an API
#   error on the very last lines of the screen); waits out usage limits.
# - Exit codes: 0 the turn ended, 2 the agent is blocked (a permission or a question
#   on screen: read it, decide or ask the user), 1 it gave up (gone, too many retries).
. "$(dirname "$0")/common.sh"
t=$1; lines=${2:-40}; [ -n "$t" ] || fb_die "usage: babysit-hc.sh <terminal> [lines]"
retries=0; limits=0
since=$(cat "$STATE/sent-$t" 2>/dev/null || echo -1)

out() { # code header
  echo "== $t: $2 (turn $(fb_turn "$t"), $retries resumes)"
  hc read "$t" --lines "$lines" 2>/dev/null | grep -v '^\s*$' | grep -v '⣿\|⠿\|⠛' | tail -"$lines" | cut -c1-260
  [ "$1" = 2 ] || rm -f "$STATE/sent-$t"
  exit "$1"
}

# The message has not reached the agent yet: wait up to 3 minutes for its turn to start.
if [ "$since" -ge 0 ]; then
  for _ in $(seq 1 90); do
    [ "$(fb_turn "$t")" -gt "$since" ] && break
    [ "$(fb_status "$t")" = working ] && break
    fb_live "$t" || out 1 "not live"
    sleep 2
  done
fi

while true; do
  hc wait "$t" --timeout 3600 >/dev/null 2>&1
  fb_live "$t" || out 1 "not live"
  st=$(fb_status "$t")
  case $st in
    working) continue ;;
    blocked) out 2 "BLOCKED — waiting for a decision on screen" ;;
  esac
  last=$(hc read "$t" --lines 0 2>/dev/null | grep -v '^\s*$' | tail -4)
  # A Claude agent that ended its turn while its own background task runs (e.g. a worker waiting
  # for its reviewer) is not finished: wait for the turn that task's notification starts.
  if grep -q -E '[0-9]+ shells? still running|· [0-9]+ shells? ·' <<<"$last"; then
    # (A dev server left running would also show here: after 2 hours report anyway.)
    tn=$(fb_turn "$t"); w=0
    while [ "$(fb_turn "$t")" = "$tn" ] && [ "$(fb_status "$t")" != working ]; do
      fb_live "$t" || out 1 "not live"; w=$((w+1)); [ $w -gt 720 ] && out 0 "done, background shells still running after 2 h"; sleep 10
    done
    continue
  fi
  if grep -q "Usage limit" <<<"$last"; then
    limits=$((limits+1)); [ $limits -gt 24 ] && out 1 "usage limit for 2 hours, giving up"
    sleep 300
  elif [ "$st" = error ] || grep -q -E "API Error|stream disconnected|ECONNREFUSED.*(api|anthropic|openai)" <<<"$last"; then
    retries=$((retries+1)); [ $retries -gt 15 ] && out 1 "too many API retries"
    sleep 30
  else
    out 0 "$st"
  fi
  fb_send "$t" "Your answer was cut off by a dropped API connection. Continue from where you stopped, following the original task. The original task's constraints still apply. Give a brief report at the end."
  sleep 20
done
