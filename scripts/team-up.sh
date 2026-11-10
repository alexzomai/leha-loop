#!/bin/bash
# usage: team-up.sh — start the project team: reviewers + merger (TEAM_TAB tab) and the dev server (DEV_TAB tab).
# Leaves live terminals with the same names alone. A terminal it recreates takes the old one's place in the layout.
. "$(dirname "$0")/common.sh"; set -e
[ -n "$SPACE" ] || echo "The Boss is not in Herdr app (no HC_TERMINAL_ID or no herdr.space label): terminals will start, but without a layout" >&2
start() { # name cmd... (for agents the last argument is the first message)
  local n=$1 old new; shift
  if fb_live "$n"; then hc get "$n" | jq -r .address; return; fi
  old=$(hc get "$n" 2>/dev/null | jq -r '.address // empty')
  hc rm "$n" >/dev/null 2>&1 || true
  new=$(hc run --bg --no-parent --outlive-parent --keep --name "$n" --cwd "$REPO" "$@" | jq -r '.address // empty')
  [ -n "$new" ] || fb_die "hc run did not start $n"
  [ -n "$old" ] && [ -n "$SPACE" ] && python3 "$FB/scripts/layout.py" swap "$old" "$new" >/dev/null 2>&1 || true
  echo "$new"
}
revs=(); for i in $(seq 1 "$REVIEWERS"); do
  n=${PREFIX}reviewer$i
  rules=$(REVIEWER=$n fb_render "$FB/roles/reviewer.md")
  revs+=("$(start "$n" "${REVIEWER_CMD[@]}" "$rules")")
done
rules=$(fb_render "$FB/roles/merger.md")
merger=$(start "${PREFIX}merger" "${MERGER_CMD[@]}" "$rules")
dev=""; [ ${#DEV_CMD[@]} -gt 0 ] && dev=$(start "$DEV_NAME" "${DEV_CMD[@]}")
if [ -n "$SPACE" ]; then
  spec="$(IFS=,; echo "${revs[*]}")/$merger"
  tab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$TEAM_TAB" "$spec")
  for t in "${revs[@]}" "$merger"; do fb_label "$t" "$SPACE" "$tab" "$PROJ / $TEAM_TAB"; done
  if [ -n "$dev" ]; then
    dtab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$DEV_TAB" "$dev")
    fb_label "$dev" "$SPACE" "$dtab" "$PROJ / $DEV_TAB"
  fi
  # A terminal that was alive the whole time but is missing from an existing tab: report it
  shown=" $(python3 "$FB/scripts/layout.py" show | tr -s ' \n' '  ') "
  for t in "${revs[@]}" "$merger" $dev; do case $shown in *" $t "*) ;; *) echo "note: $t is not in the layout" >&2;; esac; done
fi
echo "reviewers: ${revs[*]}  merger: $merger  dev: ${dev:--}"
echo "On first start in a folder Codex asks for trust: check hc read <reviewer> and, if needed, hc keys <reviewer> enter"
