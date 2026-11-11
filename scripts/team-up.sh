#!/bin/bash
# usage: team-up.sh — start the project team: the merger (MERGE_TAB tab) and the dev server (DEV_TAB tab).
# Reviewers are not part of the team: each worker starts a new one per review (review.sh).
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
rules=$(fb_render "$FB/roles/merger.md")
fresh=""; fb_live "${PREFIX}merger" || fresh=1
merger=$(start "${PREFIX}merger" "${MERGER_CMD[@]}" "$rules")
[ -n "$fresh" ] && fb_accept_trust "$merger"   # Claude Code may ask to trust the main checkout folder
dev=""; [ ${#DEV_CMD[@]} -gt 0 ] && dev=$(start "$DEV_NAME" "${DEV_CMD[@]}")
if [ -n "$SPACE" ]; then
  tab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$MERGE_TAB" "$merger")
  fb_label "$merger" "$SPACE" "$tab" "$PROJ / $MERGE_TAB"
  if [ -n "$dev" ]; then
    dtab=$(python3 "$FB/scripts/layout.py" add-tab "$SPACE" "$DEV_TAB" "$dev")
    fb_label "$dev" "$SPACE" "$dtab" "$PROJ / $DEV_TAB"
  fi
  # A terminal that was alive the whole time but is missing from an existing tab: report it
  shown=" $(python3 "$FB/scripts/layout.py" show | tr -s ' \n' '  ') "
  for t in "$merger" $dev; do case $shown in *" $t "*) ;; *) echo "note: $t is not in the layout" >&2;; esac; done
fi
echo "merger: $merger  dev: ${dev:--}"
