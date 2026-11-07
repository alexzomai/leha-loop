#!/bin/bash
# usage: team-up.sh — start the project team: reviewers + merger (TEAM_TAB tab) and the dev server (DEV_TAB tab).
# Leaves live terminals with the same names alone.
. "$(dirname "$0")/common.sh"; set -e
[ -n "$SPACE" ] || echo "The Boss is not in Herdr app (no herdr.space label): terminals will start, but without a layout" >&2
start() { # name cmd... (the last argument is the first message)
  local n=$1; shift
  if fb_live "$n"; then hc get "$n" | jq -r .address; return; fi
  hc rm "$n" >/dev/null 2>&1 || true
  hc run --bg --no-parent --outlive-parent --keep --name "$n" --cwd "$REPO" "$@" | jq -r .address
}
revs=(); for i in $(seq 1 "$REVIEWERS"); do
  n=${PREFIX}reviewer$i
  revs+=("$(start "$n" "${REVIEWER_CMD[@]}" "$(REVIEWER=$n fb_render "$FB/roles/reviewer.md")")")
done
merger=$(start "${PREFIX}merger" "${MERGER_CMD[@]}" "$(fb_render "$FB/roles/merger.md")")
dev=""; [ ${#DEV_CMD[@]} -gt 0 ] && dev=$(start "$DEV_NAME" "${DEV_CMD[@]}")
if [ -n "$SPACE" ]; then
  spec="$(IFS=,; echo "${revs[*]}")/$merger"
  tab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$TEAM_TAB" "$spec")
  for t in "${revs[@]}" "$merger"; do fb_label "$t" "$SPACE" "$tab" "$PROJ / $TEAM_TAB"; done
  if [ -n "$dev" ]; then
    dtab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$DEV_TAB" "$dev")
    fb_label "$dev" "$SPACE" "$dtab" "$PROJ / $DEV_TAB"
  fi
fi
echo "reviewers: ${revs[*]}  merger: $merger  dev: ${dev:--}"
echo "On first start in a folder Codex asks for trust: check hc read <reviewer> and, if needed, hc keys <reviewer> enter"
