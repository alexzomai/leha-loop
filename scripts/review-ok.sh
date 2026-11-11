#!/bin/bash
# usage: review-ok.sh [--force] <branch> [note for the worker]
# The Boss allows one review of the branch's current HEAD and tells the worker to start it.
# The worker then runs review.sh itself, which refuses any HEAD the Boss has not allowed.
# Refuses after MAX_REVIEW_ROUNDS rounds for one branch: ask the user, then pass --force.
. "$(dirname "$0")/common.sh"; set -e
force=""; [ "$1" = --force ] && { force=1; shift; }
b=$1; note=${2:-}
[ -n "$b" ] || fb_die "usage: review-ok.sh [--force] <branch> [note]"
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null || fb_die "no branch $b"
w=$(fb_state_get "$b" worker); wt=$(fb_state_get "$b" wt)
[ -n "$w" ] && [ -d "$wt" ] || fb_die "$b has no worker in state.json (mkworker.sh starts one)"
dirty=$(git -C "$wt" status --porcelain | head -5)
[ -z "$dirty" ] || fb_die "$wt has uncommitted changes; the worker commits first:
$dirty"
rounds=$(fb_state_get "$b" rounds); rounds=$(( ${rounds:-0} + 1 ))
if [ $rounds -gt "$MAX_REVIEW_ROUNDS" ] && [ -z "$force" ]; then
  echo "$b: this would be review round $rounds (limit $MAX_REVIEW_ROUNDS). Tell the user what keeps failing; after they agree, run with --force." >&2; exit 3
fi
head=$(git -C "$REPO" rev-parse --short "$b")
fb_state_set "$b" review_ok_sha "$head" phase review-ok
msg="REVIEW ALLOWED for $b@$head (round $rounds). Start it now as your rules say: $FB/scripts/review.sh"
[ -n "$note" ] && msg="$msg
Boss: $note"
"$FB/scripts/send.sh" "$w" "$msg" >/dev/null
echo "$b@$head: review allowed (round $rounds), told $w"
