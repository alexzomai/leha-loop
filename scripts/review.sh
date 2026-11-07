#!/bin/bash
# usage: review.sh <branch> <reviewer> [extra instructions]
# Hands a branch to a reviewer using roles/review-task.md. Report: $REVIEWS/review-<branch>.md
. "$(dirname "$0")/common.sh"; set -e
b=$1; r=$2; extra=${3:-}
st=$(hc get "$r" | jq -r '.status.state // "-"')
[ "$st" = working ] && { echo "$r is busy — pick another reviewer" >&2; exit 1; }
head=$(git -C "$REPO" rev-parse --short "$b")
msg="$(BRANCH=$b WT=$WT_ROOT/$b HEAD_SHA=$head REPORT=$REVIEWS/review-$b.md fb_render "$FB/roles/review-task.md")"
[ -n "$extra" ] && msg="$msg

Also: $extra"
hc send "$r" "$msg" >/dev/null
echo "$b@$head -> $r, report $REVIEWS/review-$b.md"
