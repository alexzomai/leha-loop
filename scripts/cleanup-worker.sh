#!/bin/bash
# usage: cleanup-worker.sh [--abandon] <branch> <terminal-name>
# Cleans up a worker: its dev server, terminal, its reviewer, app space, worktree, branch.
# Every check runs before anything is deleted. Without --abandon the branch must be merged into
# BASE and the worktree clean. --abandon (only after the user agreed to throw the work away)
# deletes an unmerged branch and uncommitted changes too.
. "$(dirname "$0")/common.sh"; set -e
abandon=""; [ "$1" = --abandon ] && { abandon=1; shift; }
b=$1; n=$2; [ -n "$b" ] && [ -n "$n" ] || fb_die "usage: cleanup-worker.sh [--abandon] <branch> <name>"
wt=$(fb_state_get "$b" wt); wt=${wt:-$WT_ROOT/$b}

if [ -z "$abandon" ]; then
  if git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null; then
    git -C "$REPO" merge-base --is-ancestor "$b" "$BASE" || fb_die "$b is not merged into $BASE (abandon it only with the user's consent: --abandon)"
  fi
  if [ -d "$wt" ]; then
    dirty=$(git -C "$wt" status --porcelain 2>/dev/null | head -5)
    [ -z "$dirty" ] || fb_die "$wt has uncommitted changes:
$dirty"
  fi
fi
if [ -d "$wt" ]; then
  for f in "${COPY[@]}"; do case $(basename "$f") in .env*)
    [ -f "$wt/$f" ] || continue
    new=$(comm -23 <(fb_env_keys "$wt/$f") <(fb_env_keys "$REPO/$f") | tr '\n' ' ')
    [ -z "${new// }" ] && continue
    if [ -n "$abandon" ]; then echo "note: the worker's $f had variables missing from the main one: ${new}(not moved)" >&2
    else fb_die "the worker's $f has variables missing from the main one: ${new}- move them over (without printing values) and run again"; fi
  ;; esac; done
fi

# Everything below deletes. The worker's own servers first: listeners on its port whose cwd is inside the worktree.
port=$(fb_state_get "$b" port)
if [ -n "$port" ] && ! command -v lsof >/dev/null; then
  fb_port_busy "$port" && echo "note: something still listens on the worker's port $port; stop it if it is the worker's server" >&2
elif [ -n "$port" ]; then
  for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    case $cwd in "$wt"|"$wt"/*) kill "$pid" 2>/dev/null && echo "stopped pid $pid on port $port";; esac
  done
fi
rv=$(fb_state_get "$b" reviewer)
for x in "$n" $rv; do hc kill "$x" >/dev/null 2>&1 || true; hc rm "$x" >/dev/null 2>&1 || true; rm -f "$STATE/sent-$x"; done
python3 "$FB/scripts/layout.py" rm-folder "$wt" >/dev/null || true
if [ -d "$wt" ]; then git -C "$REPO" worktree remove --force "$wt"; fi
git -C "$REPO" worktree prune
# -D: "merged" was checked against BASE above; -d would check against whatever the main checkout has out
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null && git -C "$REPO" branch -D "$b"
fb_state_del "$b"
echo "cleaned up $b ($n)"
