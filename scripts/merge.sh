#!/bin/bash
# usage: merge.sh <branch> — hand an approved branch to the merger, with a fresh merger context.
# Refuses unless verdict.sh says APPROVED for the current HEAD. The merger replies MERGED <hash> or NOT MERGED — <reason>.
. "$(dirname "$0")/common.sh"; set -e
b=$1; [ -n "$b" ] || fb_die "usage: merge.sh <branch>"
m=${PREFIX}merger
v=$("$FB/scripts/verdict.sh" "$b") || fb_die "not merging $b: $v"
fb_live "$m" || fb_die "$m is not live (team-up.sh starts it)"
[ "$(fb_status "$m")" = working ] && fb_die "$m is busy"
fb_task_phase "$b" merging
rules=$(fb_render "$FB/roles/merger.md")
fb_reset "$m" "$MERGER_RESET"
fb_send "$m" "$rules

Command: merge $b (approved HEAD $(git -C "$REPO" rev-parse --short "$b"))."
fb_state_set "$b" phase merging
echo "$b -> $m; watch it with babysit-hc.sh $m"
