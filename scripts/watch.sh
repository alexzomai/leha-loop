#!/bin/bash
# usage: watch.sh [minutes]   — the Boss's watchdog: run it with Bash run_in_background and restart it
#                               after every notification. Checks the whole project every N minutes (10)
#                               and exits only when it finds a problem it has not reported yet.
#        watch.sh --once      — check now and print every current problem (also old ones).
# What counts as a problem: a worker, reviewer, merger or dev server that is gone, blocked or in error;
# a review verdict the worker has not picked up; a worker idle for 10+ minutes (an unanswered question,
# READY FOR REVIEW or REVIEW APPROVED nobody acted on); a merge that did not finish; an agent "working"
# with a screen that has not changed for 3 checks in a row (stuck).
. "$(dirname "$0")/common.sh"
once=""; mins=10
case $1 in --once) once=1;; '') ;; *) mins=$1;; esac
seen=$STATE/watch-reported
now_ms() { echo $(( $(date +%s) * 1000 )); }
idle_min() { local ms; ms=$(hc get "$1" 2>/dev/null | jq -r '.status_ms // .created_ms // empty'); [ -n "$ms" ] || { echo 0; return; }; echo $(( ($(now_ms) - ms) / 60000 )); }
# Last line that matters on an agent's screen (a marker from our roles), for the report
last_mark() { hc read "$1" --lines 80 2>/dev/null | grep -E 'READY FOR REVIEW|REVIEW APPROVED|QUESTION:|NOT MERGED|MERGED [0-9a-f]' | tail -1 | sed 's/^[^A-Z]*//' | cut -c1-160; }
has_shells() { hc read "$1" --lines 0 2>/dev/null | grep -v '^\s*$' | tail -4 | grep -q -E '[0-9]+ shells? still running|· [0-9]+ shells? ·'; }
# Same screen as at the previous check while "working"? Counts checks in a row in a small file.
stalled() { # terminal -> true after 3 identical screens
  local f=$STATE/watch-screen-$1 h old n
  h=$(hc read "$1" --lines 25 2>/dev/null | grep -v -E 'Working \(|esc to interrupt|[0-9]+s •|⏵⏵' | cksum | cut -d' ' -f1)
  read old n < "$f" 2>/dev/null
  if [ "$h" = "$old" ]; then n=$(( ${n:-0} + 1 )); else n=0; fi
  echo "$h $n" > "$f"; [ "$n" -ge 3 ]
}

check() { # prints "key<TAB>message" lines
  local b w ph st r rst sha v rsha im told
  for b in $(jq -r 'keys[]' "$STATE_FILE"); do
    told=
    w=$(fb_state_get "$b" worker); ph=$(fb_state_get "$b" phase)
    [ -n "$w" ] || continue
    if ! fb_live "$w"; then printf '%s\t%s\n' "$b:worker-gone" "$b: worker $w is gone (phase $ph)"; continue; fi
    st=$(fb_status "$w")
    case $st in
      blocked) printf '%s\t%s\n' "$b:worker-blocked" "$b: worker $w is BLOCKED, waiting for a decision on its screen";;
      error)   printf '%s\t%s\n' "$b:worker-error" "$b: worker $w is in error (API?); babysit-hc.sh $w resumes it";;
      working) stalled "$w" && printf '%s\t%s\n' "$b:worker-stalled" "$b: worker $w is \"working\" but its screen has not changed for $((3*mins)) min";;
    esac
    if [ "$ph" = review ]; then
      r=$(fb_state_get "$b" reviewer); sha=$(fb_state_get "$b" review_sha)
      read v rsha < <(fb_report_verdict "$b"; echo)
      if [ -n "$v" ] && fb_same_commit "$rsha" "$sha"; then
        [ "$st" != working ] && [ "$(idle_min "$w")" -ge 5 ] && told=1 && \
          printf '%s\t%s\n' "$b:verdict-unread:$sha" "$b: review of $sha is done ($v) but worker $w has not picked it up for $(idle_min "$w") min; tell it with send.sh"
      elif [ -n "$r" ]; then
        if ! fb_live "$r"; then printf '%s\t%s\n' "$b:reviewer-gone:$sha" "$b: reviewer $r is gone without a verdict for $sha"
        else
          rst=$(fb_status "$r")
          [ "$rst" = blocked ] && printf '%s\t%s\n' "$b:reviewer-blocked" "$b: reviewer $r is BLOCKED (hc read $r)"
          [ "$rst" = working ] && stalled "$r" && printf '%s\t%s\n' "$b:reviewer-stalled:$sha" "$b: reviewer $r is \"working\" but its screen has not changed for $((3*mins)) min, no verdict yet"
          [ "$rst" != working ] && [ "$(idle_min "$r")" -ge 5 ] && printf '%s\t%s\n' "$b:reviewer-noverdict:$sha" "$b: reviewer $r stopped $(idle_min "$r") min ago without a verdict for $sha"
        fi
      fi
    fi
    if [ "$ph" = merging ] && ! git -C "$REPO" merge-base --is-ancestor "$b" "$BASE" 2>/dev/null; then
      m=${PREFIX}merger
      [ "$(fb_status "$m")" != working ] && [ "$(idle_min "$m")" -ge 5 ] && \
        printf '%s\t%s\n' "$b:merge-stuck" "$b: merge not done; merger says: $(last_mark "$m")"
    fi
    if [ -z "$told" ] && [ "$st" != working ] && [ "$st" != blocked ] && ! has_shells "$w"; then
      im=$(idle_min "$w")
      [ "$im" -ge 10 ] && printf '%s\t%s\n' "$b:worker-idle:$(fb_turn "$w")" "$b: worker $w idle for $im min (phase $ph). Last: $(last_mark "$w")"
    fi
  done
  m=${PREFIX}merger
  if hc get "$m" >/dev/null 2>&1 && ! fb_live "$m"; then printf '%s\t%s\n' "team:merger-gone" "merger $m is gone (team-up.sh restarts it)"; fi
  [ "$(fb_status "$m")" = blocked ] && printf '%s\t%s\n' "team:merger-blocked" "merger $m is BLOCKED (hc read $m)"
  if [ ${#DEV_CMD[@]} -gt 0 ] && ! fb_live "$DEV_NAME"; then printf '%s\t%s\n' "team:dev-gone" "dev server $DEV_NAME is down (restart-dev.sh)"; fi
}

if [ -n "$once" ]; then
  out=$(check); [ -n "$out" ] && cut -f2 <<<"$out" || echo "all fine"; exit 0
fi
while true; do
  out=$(check)
  touch "$seen"
  new=$(cut -f1 <<<"$out" | grep -v '^$' | grep -vxF -f "$seen")
  # Remember only problems that still exist, so a solved one is reported again if it comes back
  cut -f1 <<<"$out" | grep -v '^$' > "$seen.tmp"; mv "$seen.tmp" "$seen"
  if [ -n "$new" ]; then
    echo "== watch.sh: $(date '+%H:%M') problems in $PROJ (new ones marked *):"
    while IFS=$'\t' read -r k msg; do
      [ -n "$k" ] || continue
      if grep -qxF "$k" <<<"$new"; then echo "* $msg"; else echo "  $msg"; fi
    done <<<"$out"
    echo "Handle them, then start watch.sh again in the background."
    exit 0
  fi
  sleep $((mins * 60))
done
