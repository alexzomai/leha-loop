#!/bin/bash
# usage: restart-dev.sh — restart the BASE dev server in the same place in the layout.
. "$(dirname "$0")/common.sh"; set -e
[ ${#DEV_CMD[@]} -gt 0 ] || { echo "DEV_CMD is not set in the project config" >&2; exit 1; }
old=$(hc get "$DEV_NAME" 2>/dev/null | jq -r '.address // empty')
labels=$(hc get "$DEV_NAME" 2>/dev/null | jq -c '.labels // {}')
hc kill "$DEV_NAME" >/dev/null 2>&1 || true; hc rm "$DEV_NAME" >/dev/null 2>&1 || true
new=$(hc run --bg --no-parent --outlive-parent --keep --name "$DEV_NAME" --cwd "$REPO" "${DEV_CMD[@]}" | jq -r .address)
[ -n "$old" ] && python3 "$FB/scripts/layout.py" swap "$old" "$new" >/dev/null
sp=$(jq -r '.["herdr.space"] // empty' <<<"$labels"); tb=$(jq -r '.["herdr.tab"] // empty' <<<"$labels")
[ -n "$sp" ] && fb_label "$new" "$sp" "$tb" "$(jq -r '.where // empty' <<<"$labels")"
echo "$DEV_NAME: ${old:--} -> $new"
