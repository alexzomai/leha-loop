#!/bin/bash
# usage: review.sh [branch] — run by the worker in its own worktree (the branch defaults to the worktree's).
# Starts a NEW Codex reviewer (REVIEWER_CMD + the newest sol model) in the worktree, in a pane right of
# the worker; a previous reviewer of this branch is closed and the new one takes its pane.
# Only for a HEAD the Boss allowed with review-ok.sh, and only once per allowance.
# Report: $REVIEWS/review-<branch>.md (the previous one is kept as review-<branch>.<sha>.md).
. "$(dirname "$0")/common.sh"; set -e
b=${1:-$(fb_cwd_branch)}
[ -n "$b" ] || fb_die "usage: review.sh [branch] (run it in the worker's worktree)"
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null || fb_die "no branch $b"
wt=$(fb_state_get "$b" wt); wt=${wt:-$WT_ROOT/$b}
[ -d "$wt" ] || fb_die "no worktree $wt for $b"
head=$(git -C "$REPO" rev-parse --short "$b")
ok=$(fb_state_get "$b" review_ok_sha)
fb_same_commit "$ok" "$b" || fb_die "the Boss has not allowed a review of $b@$head${ok:+ (allowed: $ok)}. Report to the Boss that the branch is ready for review and wait."
dirty=$(git -C "$wt" status --porcelain | head -5)
[ -z "$dirty" ] || fb_die "uncommitted changes in $wt; commit them, report to the Boss and wait for a new allowance:
$dirty"

rounds=$(fb_state_get "$b" rounds); rounds=$(( ${rounds:-0} + 1 ))
name=${PREFIX}review-$b
old=$(fb_state_get "$b" reviewer_term)
# Close the previous reviewer; the new one takes its place in the layout
if hc get "$name" >/dev/null 2>&1; then
  old=${old:-$(hc get "$name" | jq -r '.address // empty')}
  hc kill "$name" >/dev/null 2>&1 || true; hc rm "$name" >/dev/null 2>&1 || true
fi
report=$REVIEWS/review-$b.md
if [ -f "$report" ]; then
  read v osha < <(fb_report_verdict "$b"; echo)
  mv "$report" "$REVIEWS/review-$b.${osha:-$(date +%s)}.md"
fi

model=$(fb_reviewer_model)
rules=$(REVIEWER=$name fb_render "$FB/roles/reviewer.md")
task=$(BRANCH=$b WT=$wt HEAD_SHA=$head REPORT=$report fb_render "$FB/roles/review-task.md")
echo 0 > "$STATE/sent-$name"
# --add-dir: the report folder is outside the worktree, the sandbox must let Codex write there
t=$(hc run --bg --no-parent --outlive-parent --keep --name "$name" --cwd "$wt" \
     "${REVIEWER_CMD[@]}" -m "$model" --add-dir "$REVIEWS" "$rules

$task" | jq -r '.address // empty')
[ -n "$t" ] || { rm -f "$STATE/sent-$name"; fb_die "hc run did not start the reviewer"; }
fb_state_set "$b" phase review reviewer "$name" reviewer_term "$t" review_sha "$head" rounds "$rounds" review_ok_sha ""

# Pane: the old reviewer's place, else right of the worker; labels: the worker's space and tab
w=$(fb_state_get "$b" term); w=${w:-$BOSS}
placed=""
[ -n "$old" ] && [ "$(python3 "$FB/scripts/layout.py" swap "$old" "$t")" = ok ] && placed=1
[ -z "$placed" ] && [ -n "$w" ] && python3 "$FB/scripts/layout.py" add-right "$w" "$t" >/dev/null || true
lbl=$(hc get "$w" 2>/dev/null | jq -c '.labels // {}')
sp=$(jq -r '.["herdr.space"] // empty' <<<"$lbl"); tb=$(jq -r '.["herdr.tab"] // empty' <<<"$lbl")
[ -n "$sp" ] && fb_label "$t" "$sp" "$tb" "$b / review"

# Codex asks to trust the new worktree (and remembers the answer in ~/.codex/config.toml)
fb_accept_trust "$t"
echo "$b@$head -> $name ($t, $model, round $rounds), report $report"
echo "next: babysit-hc.sh $name in the background, then verdict.sh $b"
