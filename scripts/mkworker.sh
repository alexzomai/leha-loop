#!/bin/bash
# usage: mkworker.sh <branch> <terminal-name> <task-file>
# worktree from BASE + copy COPY + WT_SETUP + its own port from WORKER_PORTS
# + worker WORKER_CMD (general rules roles/worker.md + the task) + its own app space.
# On any failure before the worker starts, the worktree and the branch are removed again.
. "$(dirname "$0")/common.sh"; set -e
fb_launch_locked "$@"
b=$1; n=$2; task=$3; [ -f "$task" ] || fb_die "usage: mkworker.sh <branch> <name> <task-file>"
wt=${LEHA_TASK_WORKTREE:-$WT_ROOT/$b}
# Native ownership is independent of the worktree and its app space.
parent=${FB_PARENT:-$BOSS}
[ -n "$parent" ] || fb_die "no Boss terminal: run from the Boss, or set FB_PARENT to its actual hc address"
parent_info=$(hc get "$parent") || fb_die "cannot resolve Boss terminal $parent"
[ "$(jq -r .state <<<"$parent_info")" = live ] || fb_die "Boss terminal $parent is not live"
parent=$(jq -r '.address // empty' <<<"$parent_info")
[ -n "$parent" ] || fb_die "hc did not return the Boss address"
SPACE=$(jq -r '.labels["herdr.space"] // empty' <<<"$parent_info")
git -C "$REPO" check-ref-format --branch "$b" >/dev/null 2>&1 || fb_die "bad branch name: $b"
if [ "${LEHA_TASK_RESUME:-}" = 1 ]; then
  [ -d "$wt" ] && [ "$(git -C "$wt" branch --show-current)" = "$b" ] || fb_die "resume worktree/branch mismatch"
else
  git -C "$REPO" rev-parse -q --verify "refs/heads/$b" >/dev/null && fb_die "branch $b already exists"
  [ -e "$wt" ] && fb_die "$wt already exists (left from an earlier run?)"
fi
fb_live "$n" && fb_die "terminal $n is already in use"

# A port no other worker holds and nothing listens on
lo=${WORKER_PORTS%-*}; hi=${WORKER_PORTS#*-}; port=${LEHA_TASK_PORT:-}
taken=" $(jq -r '[.[].port // empty] | join(" ")' "$STATE_FILE") $(fb_task _ports) "
for p in $(seq "$lo" "$hi"); do
  [ -n "$port" ] && break
  case $taken in *" $p "*) continue;; esac
  fb_port_busy "$p" || { port=$p; break; }
done
[ -n "$port" ] || fb_die "no free port in WORKER_PORTS=$WORKER_PORTS"

task_id=$(fb_task_reserve "$b" "$n" "$task" "$parent")
export LEHA_TASK_ID=$task_id
started=""; launch_attempted=""; created=""
rollback() {
  [ -n "$started" ] && return
  if [ -n "$launch_attempted" ]; then
    fb_task_phase "$b" lost || true
    echo "mkworker: launch outcome uncertain; preserved $wt. Run task.sh recover before any retry." >&2
    return
  fi
  fb_task_phase "$b" failed || true
  [ "${LEHA_TASK_RESUME:-}" = 1 ] && return
  [ -n "$created" ] || { echo "mkworker: checkout creation was not confirmed; preserved any existing branch/files for inspection" >&2; return; }
  echo "mkworker: failed, removing $wt and branch $b" >&2
  git -C "$REPO" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  git -C "$REPO" worktree prune >/dev/null 2>&1
  git -C "$REPO" branch -D "$b" >/dev/null 2>&1 || true
}
trap rollback EXIT
fb_task_phase "$b" preparing worktree "$wt" port "$port"

if [ "${LEHA_TASK_RESUME:-}" != 1 ]; then
mkdir -p "$WT_ROOT"; git -C "$REPO" worktree add -q -b "$b" "$wt" "$BASE"
created=1
for f in "${COPY[@]}"; do
  [ -e "$REPO/$f" ] || continue
  mkdir -p "$(dirname "$wt/$f")"; fb_copy "$REPO/$f" "$wt/$f"
  case $(basename "$f") in .env*) chmod 600 "$wt/$f";; esac
done
if [ -n "$WT_SETUP" ]; then
  log=$STATE/setup-$b.log
  (cd "$wt" && bash -c "$WT_SETUP") >"$log" 2>&1 || { echo "WT_SETUP failed, log $log:" >&2; tail -20 "$log" >&2; exit 1; }
fi
fi

rules=$(BRANCH=$b WT=$wt WORKER_PORT=$port fb_render "$FB/roles/worker.md")
prompt="$rules

## Native coordinator

Your Boss is hc terminal $parent. You are a worker in your own worktree and app space, not the Boss. This native parent link does not change the review/merge approval workflow.

## Durable task and ownership

Task ID: $task_id. Your recorded owner is $parent. Read the ownership and previous reports with: $FB/scripts/task.sh show $task_id
Change only the declared ownership paths and your own OpenSpec change. A resumed task keeps the existing checkout and all commits/uncommitted work: inspect first, never reset or recreate it.
Report with one command: $FB/scripts/task.sh report $task_id --state <progress|ready_for_review|question|blocked|failed|approved|changes_requested> --file <report.md>
This saves the report and notifies your Boss. A report is data, not permission to merge. Do not use complete/done for an overall user task. Keep the existing review allowance and SHA-pinned gates.

## Task

$(cat "$task")"
echo 0 > "$STATE/sent-$n"   # babysit-hc.sh waits for the first real turn
fb_task_phase "$b" launching worktree "$wt" port "$port"
launch_attempted=1
t=$(hc run --bg --parent "$parent" --outlive-parent --keep --label "leha.role=worker" --label "leha.branch=$b" --label "leha.task=$task_id" --label "leha.project=$PROJ" --name "$n" --cwd "$wt" env "FB_PROJ=$PROJ" "FB_REPO=$REPO" "FB_TASK_HOME=${FB_TASK_HOME:-}" "${WORKER_CMD[@]}" "$prompt" | jq -r '.address // empty')
[ -n "$t" ] || { rm -f "$STATE/sent-$n"; fb_die "hc run did not start the worker"; }
started=1
fb_task_phase "$b" working terminal "$t" worktree "$wt" port "$port"
fb_state_set "$b" worker "$n" term "$t" parent "$parent" wt "$wt" port "$port" phase working rounds 0 task "$task" task_id "$task_id"
fb_accept_trust "$t"   # Existing trust handling is unchanged; persisted identity comes first.
if [ -n "$SPACE" ]; then
  read sid tid < <(python3 "$FB/scripts/layout.py" add-space "$wt" worker "$t") || true
  [ -n "$sid" ] && fb_label "$t" "$sid" "$tid" "$b / worker"
fi
echo "$b $n $t $wt port $port"
