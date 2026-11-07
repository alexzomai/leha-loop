#!/usr/bin/env bash
# Watches an agent in hc (herdr-core): waits for the turn to end, asks it to continue when the API drops, prints the screen tail at the end.
# usage: babysit-hc.sh <terminal> [lines]
t=$1; lines=${2:-40}; n=0
while true; do
  hc wait "$t" --timeout 3600 >/dev/null 2>&1
  st=$(hc get "$t" 2>/dev/null | jq -r '.status.state // "-"')
  [ "$st" = working ] && continue
  tail_txt=$(hc read "$t" --lines 0 2>/dev/null | grep -v '^\s*$' | tail -12)
  if grep -q -E "API Error|Usage limit|Connection refused|ECONNREFUSED|stream disconnected|reconnecting" <<<"$tail_txt"; then
    n=$((n+1)); [ $n -gt 15 ] && { echo "$t: too many retries"; exit 1; }
    grep -q "Usage limit" <<<"$tail_txt" && { sleep 300; continue; }
    sleep 30
    hc send "$t" "Your answer was cut off by a dropped API connection. Continue from where you stopped, following the original task. The original task's constraints still apply. Give a brief report at the end." >/dev/null 2>&1
    sleep 20
  else
    echo "== $t: $st after $n resumes"
    hc read "$t" --lines "$lines" 2>/dev/null | grep -v '^\s*$' | grep -v '⣿\|⠿\|⠛' | tail -"$lines" | cut -c1-260
    exit 0
  fi
done
