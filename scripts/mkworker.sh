#!/bin/bash
# usage: mkworker.sh <branch> <terminal-name> <task-file>
# worktree from BASE + copy COPY + WT_SETUP + its own port from WORKER_PORTS
# + worker WORKER_CMD (general rules roles/worker.md + the task) + its own app space.
# On any failure before the worker starts, the worktree and the branch are removed again.
. "$(dirname "$0")/common.sh"; set -e
b=$1; n=$2; task=$3; [ -f "$task" ] || fb_die "usage: mkworker.sh <branch> <name> <task-file>"
wt=$WT_ROOT/$b
git -C "$REPO" check-ref-format --branch "$b" >/dev/null 2>&1 || fb_die "bad branch name: $b"
git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null && fb_die "branch $b already exists"
[ -e "$wt" ] && fb_die "$wt already exists (left from an earlier run?)"
fb_live "$n" && fb_die "terminal $n is already in use"

# A port no other worker holds and nothing listens on
lo=${WORKER_PORTS%-*}; hi=${WORKER_PORTS#*-}; port=""
taken=" $(jq -r '[.[].port // empty] | join(" ")' "$STATE_FILE") "
for p in $(seq "$lo" "$hi"); do
  case $taken in *" $p "*) continue;; esac
  fb_port_busy "$p" || { port=$p; break; }
done
[ -n "$port" ] || fb_die "no free port in WORKER_PORTS=$WORKER_PORTS"

started=""
rollback() {
  [ -n "$started" ] && return
  echo "mkworker: failed, removing $wt and branch $b" >&2
  git -C "$REPO" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  git -C "$REPO" worktree prune >/dev/null 2>&1
  git -C "$REPO" branch -D "$b" >/dev/null 2>&1 || true
}
trap rollback EXIT

mkdir -p "$WT_ROOT"; git -C "$REPO" worktree add -q -b "$b" "$wt" "$BASE"
for f in "${COPY[@]}"; do
  [ -e "$REPO/$f" ] || continue
  mkdir -p "$(dirname "$wt/$f")"; fb_copy "$REPO/$f" "$wt/$f"
  case $(basename "$f") in .env*) chmod 600 "$wt/$f";; esac
done
if [ -n "$WT_SETUP" ]; then
  log=$STATE/setup-$b.log
  (cd "$wt" && bash -c "$WT_SETUP") >"$log" 2>&1 || { echo "WT_SETUP failed, log $log:" >&2; tail -20 "$log" >&2; exit 1; }
fi

rules=$(BRANCH=$b WT=$wt WORKER_PORT=$port fb_render "$FB/roles/worker.md")
prompt="$rules

## Task

$(cat "$task")"
echo 0 > "$STATE/sent-$n"   # babysit-hc.sh waits for the first real turn
t=$(hc run --bg --no-parent --outlive-parent --keep --name "$n" --cwd "$wt" "${WORKER_CMD[@]}" "$prompt" | jq -r '.address // empty')
[ -n "$t" ] || { rm -f "$STATE/sent-$n"; fb_die "hc run did not start the worker"; }
started=1
fb_state_set "$b" worker "$n" term "$t" wt "$wt" port "$port" phase working rounds 0 task "$task"
if [ -n "$SPACE" ]; then
  read sid tid < <("$PY" "$FB/scripts/layout.py" add-space "$wt" worker "$t") || true
  [ -n "$sid" ] && fb_label "$t" "$sid" "$tid" "$b / worker"
fi
echo "$b $n $t $wt port $port"
