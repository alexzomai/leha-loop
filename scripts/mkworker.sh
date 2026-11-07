#!/bin/bash
# usage: mkworker.sh <branch> <terminal-name> <task-file>
# worktree from BASE + copy COPY + WT_SETUP + worker WORKER_CMD (general rules roles/worker.md + the task) + its own app space.
. "$(dirname "$0")/common.sh"; set -e
b=$1; n=$2; task=$3; [ -f "$task" ] || { echo "usage: mkworker.sh <branch> <name> <task-file>" >&2; exit 1; }
wt=$WT_ROOT/$b
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null && { echo "branch $b already exists" >&2; exit 1; }
fb_live "$n" && { echo "terminal $n is already in use" >&2; exit 1; }
mkdir -p "$WT_ROOT"; git -C "$REPO" worktree add -q -b "$b" "$wt" "$BASE"
for f in "${COPY[@]}"; do
  [ -e "$REPO/$f" ] || continue
  mkdir -p "$(dirname "$wt/$f")"; cp -a --reflink=auto "$REPO/$f" "$wt/$f"
  case $(basename "$f") in .env*) chmod 600 "$wt/$f";; esac
done
[ -n "$WT_SETUP" ] && (cd "$wt" && bash -c "$WT_SETUP") >/dev/null 2>&1 || true
prompt="$(BRANCH=$b WT=$wt fb_render "$FB/roles/worker.md")

## Task

$(cat "$task")"
t=$(hc run --bg --no-parent --outlive-parent --keep --name "$n" --cwd "$wt" "${WORKER_CMD[@]}" "$prompt" | jq -r .address)
if [ -n "$SPACE" ]; then
  read sid tid < <(python3 "$FB/scripts/layout.py" add-space "$wt" worker "$t")
  fb_label "$t" "$sid" "$tid" "$b / worker"
fi
echo "$b $n $t $wt"
