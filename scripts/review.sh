#!/bin/bash
# usage: review.sh [--force] <branch> <reviewer> [extra instructions]
# Hands a branch's current HEAD to a free reviewer, with a fresh reviewer context
# (REVIEWER_RESET, then roles/reviewer.md + roles/review-task.md). Report: $REVIEWS/review-<branch>.md;
# the previous report is kept as review-<branch>.<sha>.md.
# Refuses after MAX_REVIEW_ROUNDS rounds for one branch: ask the user, then pass --force.
. "$(dirname "$0")/common.sh"; set -e
force=""; [ "$1" = --force ] && { force=1; shift; }
b=$1; r=$2; extra=${3:-}
[ -n "$b" ] && [ -n "$r" ] || fb_die "usage: review.sh [--force] <branch> <reviewer> [extra]"
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null || fb_die "no branch $b"
wt=$(fb_state_get "$b" wt); wt=${wt:-$WT_ROOT/$b}
[ -d "$wt" ] || fb_die "no worktree $wt for $b"
fb_live "$r" || fb_die "$r is not live (team-up.sh starts it)"
[ "$(fb_status "$r")" = working ] && fb_die "$r is busy — pick another reviewer"
# Unfinished review = phase review and no verdict for the commit that was sent
unfinished() { local v sha; read v sha < <(fb_report_verdict "$1"; echo); ! fb_same_commit "$sha" "$(fb_state_get "$1" review_sha)"; }
for ob in $(jq -r --arg r "$r" 'to_entries[] | select(.value.phase == "review" and .value.reviewer == $r) | .key' "$STATE_FILE"); do
  [ "$ob" != "$b" ] && unfinished "$ob" && fb_die "$r is still reviewing $ob — pick another reviewer"
done
if [ -z "$force" ] && [ "$(fb_state_get "$b" phase)" = review ] && unfinished "$b"; then
  fb_die "$b is already being reviewed by $(fb_state_get "$b" reviewer) (no verdict yet). If that review died, check its screen and resend with --force."
fi
rounds=$(fb_state_get "$b" rounds); rounds=$(( ${rounds:-0} + 1 ))
if [ $rounds -gt "$MAX_REVIEW_ROUNDS" ] && [ -z "$force" ]; then
  echo "$b: this would be review round $rounds (limit $MAX_REVIEW_ROUNDS). Tell the user what keeps failing; after they agree, run with --force." >&2; exit 3
fi

head=$(git -C "$REPO" rev-parse --short "$b"); report=$REVIEWS/review-$b.md
if [ -f "$report" ]; then
  read v old < <(fb_report_verdict "$b"; echo)
  mv "$report" "$REVIEWS/review-$b.${old:-$(date +%s)}.md"
fi
rules=$(REVIEWER=$r fb_render "$FB/roles/reviewer.md")
task=$(BRANCH=$b WT=$wt HEAD_SHA=$head REPORT=$report fb_render "$FB/roles/review-task.md")
msg="$rules

$task"
[ -n "$extra" ] && msg="$msg

Also: $extra"
fb_reset "$r" "$REVIEWER_RESET"
fb_send "$r" "$msg"
fb_state_set "$b" phase review reviewer "$r" review_sha "$head" rounds "$rounds"
echo "$b@$head -> $r (round $rounds), report $report"
