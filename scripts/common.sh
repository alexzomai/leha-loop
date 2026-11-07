# Shared leha-loop settings. Source it with: . "$(dirname "$0")/common.sh"
FB=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO=${FB_REPO:-$(git worktree list --porcelain 2>/dev/null | awk 'NR==1{print $2}')}
[ -n "$REPO" ] || { echo "leha-loop: run it from the project's git repository (or set FB_REPO)" >&2; exit 1; }
PROJ=$(basename "$REPO")
CONF=""
for c in "$REPO/.leha-loop.env" "$HOME/.config/leha-loop/$PROJ.env"; do [ -f "$c" ] && { CONF=$c; . "$c"; break; }; done
NOTES_FILE=${NOTES_FILE:-$HOME/.config/leha-loop/$PROJ.notes.md}

# Defaults (the project config overrides them)
BASE=${BASE:-main}
PREFIX=${PREFIX-$PROJ-}
WT_ROOT=${WT_ROOT:-$HOME/.herdr/worktrees/$PROJ}
REVIEWS=${REVIEWS:-/tmp/leha-loop/$PROJ}
REVIEWERS=${REVIEWERS:-2}
TEAM_TAB=${TEAM_TAB:-review & merge}
DEV_TAB=${DEV_TAB:-dev}
DEV_NAME=${DEV_NAME:-${PREFIX}dev}
DEV_PORT=${DEV_PORT:-}
WORKER_PORTS=${WORKER_PORTS:-3100-3199}
[ ${#WORKER_CMD[@]} -gt 0 ] || WORKER_CMD=(claude --model claude-opus-5-5 --effort medium)
[ ${#MERGER_CMD[@]} -gt 0 ] || MERGER_CMD=("${WORKER_CMD[@]}")
[ ${#REVIEWER_CMD[@]} -gt 0 ] || REVIEWER_CMD=(codex --no-daemon -m gpt-6.1-sol -s workspace-write -a never)
[ ${#COPY[@]} -gt 0 ] || COPY=(node_modules .env .env.local)
WT_SETUP=${WT_SETUP:-}
STATE=$HOME/.cache/leha-loop/$PROJ; mkdir -p "$STATE" "$REVIEWS"
export FB_STATE=$STATE PROJ REPO BASE REVIEWS DEV_PORT WORKER_PORTS PREFIX

# The Boss's space in Herdr app (from its terminal's labels)
BOSS=${HC_TERMINAL_ID:-t1}
SPACE=$(hc get "$BOSS" 2>/dev/null | jq -r '.labels["herdr.space"] // empty')

fb_live() { [ "$(hc get "$1" 2>/dev/null | jq -r .state)" = live ]; }
fb_label() { hc label "$1" herdr.space="$2" herdr.tab="$3" "where=$4" >/dev/null 2>&1 || true; }
# Role template: {{VAR}} -> environment variable value, {{NOTES}} -> project notes
fb_render() {
  NOTES=$( [ -f "$NOTES_FILE" ] && cat "$NOTES_FILE" ) python3 -c '
import os,re,sys
t=open(sys.argv[1]).read()
print(re.sub(r"\{\{(\w+)\}\}", lambda m: os.environ.get(m[1], ""), t))' "$1"
}
export -f fb_render 2>/dev/null
