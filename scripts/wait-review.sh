#!/bin/bash
# usage: wait-review.sh [branch] — run by the worker (with Bash run_in_background) after review.sh.
# Waits for the review's RESULT, not for the reviewer's status: Codex can stay "working" in hc long
# after it has written its report. Done when the report's verdict line is for the reviewed HEAD.
# Exit codes: 0 verdict written (prints verdict.sh's line and the report path),
#   2 the reviewer is blocked (prints its screen), 1 it died, finished without a verdict, or timed out.
. "$(dirname "$0")/common.sh"
b=${1:-$(fb_cwd_branch)}
[ -n "$b" ] || fb_die "usage: wait-review.sh [branch] (run it in the worker's worktree)"
r=$(fb_state_get "$b" reviewer); sha=$(fb_state_get "$b" review_sha)
[ -n "$r" ] && [ -n "$sha" ] || fb_die "no review of $b in progress (review.sh starts one)"
report=$REVIEWS/review-$b.md

screen() { hc read "$r" --lines 30 2>/dev/null | grep -v '^\s*$' | tail -20 | cut -c1-260; }
idle=0
for i in $(seq 1 360); do   # 360 x 15 s = 90 minutes
  read v rsha < <(fb_report_verdict "$b"; echo)
  if [ -n "$v" ] && fb_same_commit "$rsha" "$sha"; then
    sleep 5   # let the reviewer finish writing the file
    echo "== review of $b@$sha finished: $("$FB/scripts/verdict.sh" "$b")"
    echo "report: $report"
    rm -f "$STATE/sent-$r"
    exit 0
  fi
  fb_live "$r" || { echo "== $r is gone and wrote no verdict for $sha"; exit 1; }
  case $(fb_status "$r") in
    blocked) echo "== $r is BLOCKED:"; screen; exit 2 ;;
    working) idle=0 ;;
    *) idle=$((idle+1)); [ $idle -ge 8 ] && { echo "== $r stopped 2 minutes ago without a verdict for $sha:"; screen; exit 1; } ;;
  esac
  sleep 15
done
echo "== no verdict from $r after 90 minutes:"; screen; exit 1
