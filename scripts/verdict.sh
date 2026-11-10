#!/bin/bash
# usage: verdict.sh <branch> — may this branch be merged right now?
# Prints one line and exits with:
#   0 APPROVED  the report approves the branch's current HEAD, and BASE did not change the same files since
#   3 STALE     approved, but BASE has since changed files this branch changes too: the worker merges
#               BASE into the branch (git merge BASE), then the new HEAD is reviewed again
#   4 CHANGES   the report for the current HEAD asks for changes
#   5 NONE      no report for the current HEAD (none at all, or it is for an older commit)
. "$(dirname "$0")/common.sh"
b=$1; [ -n "$b" ] || fb_die "usage: verdict.sh <branch>"
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null || fb_die "no branch $b"
head=$(git -C "$REPO" rev-parse --short "$b")
read v sha < <(fb_report_verdict "$b"; echo)
if [ -z "$v" ] || ! fb_same_commit "$sha" "$b"; then
  echo "NONE — no review report for $b@$head${sha:+ (the report is for $sha)}"; exit 5
fi
[ "$v" = CHANGES ] && { echo "CHANGES — $REVIEWS/review-$b.md"; exit 4; }
mb=$(git -C "$REPO" merge-base "$BASE" "$b")
if [ "$mb" != "$(git -C "$REPO" rev-parse "$BASE")" ]; then
  both=$(comm -12 <(git -C "$REPO" diff --name-only "$mb" "$BASE" | sort -u) <(git -C "$REPO" diff --name-only "$mb" "$b" | sort -u))
  if [ -n "$both" ]; then
    echo "STALE — $BASE changed the same files since the review: $(echo $both)"; exit 3
  fi
fi
echo "APPROVED — $b@$head"
