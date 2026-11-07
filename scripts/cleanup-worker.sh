#!/bin/bash
# usage: cleanup-worker.sh <branch> <terminal-name>
# Cleans up a merged worker: terminal, app space, worktree, branch (merged only: git branch -d).
. "$(dirname "$0")/common.sh"; set -e
b=$1; n=$2; wt=$WT_ROOT/$b
for f in "${COPY[@]}"; do case $(basename "$f") in .env*)
  [ -f "$wt/$f" ] || continue
  new=$(comm -23 <(grep -o '^[A-Za-z_][A-Za-z0-9_]*=' "$wt/$f" | sort -u) <(grep -o '^[A-Za-z_][A-Za-z0-9_]*=' "$REPO/$f" 2>/dev/null | sort -u))
  [ -n "$new" ] && { echo "The worker's $f has variables missing from the main one: $new — move them over (without printing values) and run again" >&2; exit 1; };;
esac; done
hc kill "$n" >/dev/null 2>&1 || true; hc rm "$n" >/dev/null 2>&1 || true
python3 "$FB/scripts/layout.py" rm-folder "$wt"
git -C "$REPO" worktree remove --force "$wt"
git -C "$REPO" branch -d "$b"
