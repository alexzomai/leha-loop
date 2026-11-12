# Shared leha-loop settings. Source it with: . "$(dirname "$0")/common.sh"
# Runs on macOS (system bash 3.2: no associative arrays, no mapfile) and Linux.
FB=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fb_die() { echo "leha-loop: $*" >&2; exit 1; }
REPO=${FB_REPO:-$(git worktree list --porcelain 2>/dev/null | awk 'NR==1{print $2}')}
[ -n "$REPO" ] || fb_die "run it from the project's git repository (or set FB_REPO)"
# FB_PROJ separates two repositories that have the same folder name.
PROJ=${FB_PROJ:-$(basename "$REPO")}
# The config is bash and gets executed, so it is read only from the user's own folder,
# never from the repository (a cloned repo must not be able to run code through it).
CONF=$HOME/.config/leha-loop/$PROJ.env
if [ -f "$CONF" ]; then . "$CONF"; else CONF=""; fi
if [ -n "${REPO_PATH:-}" ] && [ "$REPO_PATH" != "$REPO" ]; then
  fb_die "$CONF belongs to $REPO_PATH, not $REPO. Set FB_PROJ=<other-name> for this repository."
fi
NOTES_FILE=${NOTES_FILE:-$HOME/.config/leha-loop/$PROJ.notes.md}

# Defaults (the project config overrides them)
BASE=${BASE:-main}
PREFIX=${PREFIX-$PROJ-}
WT_ROOT=${WT_ROOT:-$HOME/.herdr/worktrees/$PROJ}
STATE=$HOME/.cache/leha-loop/$PROJ
REVIEWS=${REVIEWS:-$STATE/reviews}
MAX_REVIEW_ROUNDS=${MAX_REVIEW_ROUNDS:-3}
MERGE_TAB=${MERGE_TAB:-merge}
DEV_TAB=${DEV_TAB:-dev}
DEV_NAME=${DEV_NAME:-${PREFIX}dev}
DEV_PORT=${DEV_PORT:-}
WORKER_PORTS=${WORKER_PORTS:-3100-3199}
[ ${#WORKER_CMD[@]} -gt 0 ] || WORKER_CMD=(claude --model claude-opus-5-5 --effort medium --permission-mode auto)
[ ${#MERGER_CMD[@]} -gt 0 ] || MERGER_CMD=("${WORKER_CMD[@]}")
# Reviewer: a new Codex for every review; the model is added by review.sh (REVIEWER_MODEL)
[ ${#REVIEWER_CMD[@]} -gt 0 ] || REVIEWER_CMD=(codex --no-daemon -s workspace-write -a never)
REVIEWER_MODEL=${REVIEWER_MODEL:-latest-sol}
[ ${#COPY[@]} -gt 0 ] || COPY=(node_modules .env .env.local)
WT_SETUP=${WT_SETUP:-}
# Slash command that gives the merger a fresh context before each merge (empty: keep the context)
MERGER_RESET=${MERGER_RESET-/clear}
mkdir -p "$STATE" "$REVIEWS"
STATE_FILE=$STATE/state.json
python3 "$FB/scripts/state.py" "$STATE_FILE" init || fb_die "cannot initialize runtime state safely"
FB_TASK_HOME=${FB_TASK_HOME:-$HOME/.local/share/leha-loop/$PROJ}
MAX_PARALLEL_WORKERS=${MAX_PARALLEL_WORKERS:-3}
export FB FB_STATE=$STATE STATE_FILE PROJ REPO BASE REVIEWS DEV_PORT WORKER_PORTS PREFIX WT_ROOT FB_TASK_HOME MAX_PARALLEL_WORKERS
fb_task() { python3 "$FB/scripts/task.py" "$@"; }
# Serialize port/worktree allocation, including direct legacy launcher invocations.
fb_launch_locked() {
  [ "${LEHA_LAUNCH_LOCKED:-}" = "$(cd "$REPO" && pwd -P)" ] || exec python3 "$FB/scripts/task.py" _locked-launch "$@"
}
fb_task_reserve() { fb_task _reserve "$@"; }
fb_task_phase() { fb_task _phase "$@"; }

# The Boss's space in Herdr app (from its own terminal's labels). Without HC_TERMINAL_ID
# we do not guess: another terminal's space would get our tabs.
BOSS=${HC_TERMINAL_ID:-}
SPACE=""
[ -n "$BOSS" ] && SPACE=$(hc get "$BOSS" 2>/dev/null | jq -r '.labels["herdr.space"] // empty')

# Templates are UTF-8 whatever the locale (a Linux C locale would make Python read them as ASCII)
export PYTHONIOENCODING=utf-8

# Codex model for reviewers: REVIEWER_MODEL, where "latest-sol" means the newest gpt-<version>-sol
# in Codex's model cache (falls back to gpt-6.1-sol if the cache has none)
fb_reviewer_model() {
  [ "$REVIEWER_MODEL" = latest-sol ] || { echo "$REVIEWER_MODEL"; return; }
  local m
  m=$(jq -r '.models[]? | .slug // .id // empty' "$HOME/.codex/models_cache.json" 2>/dev/null \
      | grep -E '^gpt-[0-9]+(\.[0-9]+)*-sol$' | sed -E 's/^gpt-([0-9.]+)-sol$/\1 &/' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1 | cut -d' ' -f2)
  echo "${m:-gpt-6.1-sol}"
}
# The branch of the worktree we are in (a worker runs scripts from its own worktree)
fb_cwd_branch() { case $PWD/ in "$WT_ROOT"/*) git rev-parse --abbrev-ref HEAD 2>/dev/null;; esac; }

# A new worktree is a new folder for the agent, and both Claude Code and Codex first ask whether to trust it.
# It is a worktree we just created, so accept: Claude "Yes, I trust this folder" (the cursor starts on
# "No, exit", so move to it first), Codex "Trust and continue". Gives up after ~40 s or once the agent works.
fb_accept_trust() {
  local t=$1 scr i
  for i in $(seq 1 20); do
    sleep 2
    [ "$(fb_status "$t")" = working ] && return 0
    scr=$(hc read "$t" --lines 30 2>/dev/null)
    if grep -q 'Yes, I trust this folder' <<<"$scr"; then
      if grep -q '❯ *Yes, I trust this folder' <<<"$scr"; then hc keys "$t" enter >/dev/null 2>&1; return 0; fi
      hc keys "$t" down >/dev/null 2>&1
    elif grep -q 'Trust this folder' <<<"$scr"; then
      hc keys "$t" enter >/dev/null 2>&1; return 0
    fi
  done
}

fb_live() { [ "$(hc get "$1" 2>/dev/null | jq -r .state)" = live ]; }
fb_status() { hc get "$1" 2>/dev/null | jq -r '.status.state // "-"'; }
fb_turn() { hc get "$1" 2>/dev/null | jq -r '.turn // 0'; }
fb_label() { hc label "$1" herdr.space="$2" herdr.tab="$3" "where=$4" >/dev/null 2>&1 || true; }

# Branch state: state.json = {"<branch>": {worker, term, wt, port, phase, reviewer, review_sha, rounds, ...}}
fb_state_get() { jq -r --arg b "$1" --arg k "$2" '.[$b][$k] // empty' "$STATE_FILE"; }
fb_state_set() { python3 "$FB/scripts/state.py" "$STATE_FILE" set "$@"; }
fb_state_del() { python3 "$FB/scripts/state.py" "$STATE_FILE" del "$1"; }

# Remember an agent's turn before we send it something: babysit-hc.sh then waits for a newer turn
# instead of reporting the previous one.
fb_mark_sent() { fb_turn "$1" > "$STATE/sent-$1"; }
fb_send() { # terminal text
  fb_mark_sent "$1"; hc send "$1" "$2" >/dev/null
}
# Fresh context: slash command, then a short pause so the agent applies it before the next message
fb_reset() { [ -n "$2" ] || return 0; hc send "$1" "$2" >/dev/null 2>&1 || true; sleep 3; }

# Copy that clones where the file system can (APFS: cp -c, Linux: --reflink), and falls back to a plain copy
fb_copy() {
  case $(uname) in
    Darwin) cp -ac "$1" "$2" 2>/dev/null || { rm -rf "$2"; cp -a "$1" "$2"; } ;;
    *) cp -a --reflink=auto "$1" "$2" ;;
  esac
}
# Variable names from a .env file (KEY=… and export KEY=…)
fb_env_keys() { sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$1" 2>/dev/null | sort -u; }
# Something listens on the port? (a connect attempt: no lsof needed, many Linux systems lack it)
# (IPv4 and IPv6: Node dev servers often listen on ::1 only)
fb_port_busy() { python3 -c '
import socket,sys
def up(fam, host):
    try:
        s=socket.socket(fam); s.settimeout(0.3); return s.connect_ex((host, int(sys.argv[1]))) == 0
    except OSError: return False
sys.exit(0 if up(socket.AF_INET, "127.0.0.1") or up(socket.AF_INET6, "::1") else 1)' "$1"; }

# Last verdict in a branch's review report: "APPROVED <sha>" / "CHANGES <sha>", or nothing.
# Verdict line: "<branch>@<sha>: APPROVED — …" or "<branch>@<sha>: CHANGES REQUESTED — …"
fb_report_verdict() {
  local r=$REVIEWS/review-$1.md line
  [ -f "$r" ] || return 0
  line=$(grep -v '^[[:space:]]*$' "$r" | tail -1 | sed 's/^[[:space:]`*]*//')
  case $line in
    "$1@"*": APPROVED"*) v=APPROVED ;;
    "$1@"*": CHANGES REQUESTED"*) v=CHANGES ;;
    *) return 0 ;;
  esac
  line=${line#"$1@"}; echo "$v ${line%%:*}"
}
# Same commit? (short and full SHAs both work)
fb_same_commit() { [ "$(git -C "$REPO" rev-parse -q --verify "$1^{commit}" 2>/dev/null)" = "$(git -C "$REPO" rev-parse -q --verify "$2^{commit}" 2>/dev/null)" ] && [ -n "$1" ]; }

# Role template: {{VAR}} -> environment variable value, {{NOTES}} -> project notes.
# An unknown {{VAR}} is an error, so a typo in a template does not silently become an empty string.
fb_render() {
  NOTES=$( [ -f "$NOTES_FILE" ] && cat "$NOTES_FILE" ) python3 -c '
import os,re,sys
t=open(sys.argv[1], encoding="utf-8").read()
miss=sorted({m for m in re.findall(r"\{\{(\w+)\}\}", t) if m not in os.environ})
if miss: sys.exit("leha-loop: %s: unknown template variables: %s" % (sys.argv[1], ", ".join(miss)))
print(re.sub(r"\{\{(\w+)\}\}", lambda m: os.environ[m[1]], t))' "$1"
}
