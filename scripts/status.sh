#!/bin/bash
# usage: status.sh — summary: config, team, workers, worktrees, unarchived changes, unpushed commits.
. "$(dirname "$0")/common.sh"
echo "Project: $PROJ ($REPO), base $BASE, config: ${CONF:-none — see config.example.env}"
echo "Notes: $( [ -f "$NOTES_FILE" ] && echo "$NOTES_FILE" || echo none)"
echo; echo "== Project terminals"
hc ls 2>/dev/null | jq -r --arg p "$PROJ / " --arg w "$WT_ROOT/" --arg r "$REPO" \
  '.terminals[] | select(if .where then (.where | startswith($p)) or ((.cwd // "") | startswith($w)) else ((.cwd // "") | startswith($r)) end) | "\(.address)\t\(.name // "-")\t\(.status // "-")\t\(.where // .cwd)"'
echo; echo "== Worktree"; git -C "$REPO" worktree list
echo; echo "== Not merged into $BASE"; git -C "$REPO" branch --no-merged "$BASE"
if [ -d "$REPO/openspec" ] && command -v openspec >/dev/null; then echo; echo "== OpenSpec changes (to check / in progress)"; (cd "$REPO" && openspec list 2>/dev/null); fi
up=$(git -C "$REPO" rev-list --count "$BASE@{u}..$BASE" 2>/dev/null) && { echo; echo "== $BASE is $up commits ahead of origin"; }
