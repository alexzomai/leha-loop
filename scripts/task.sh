#!/bin/bash
# Durable task CLI. Source only the user's existing project config, never a repo-local config.
. "$(dirname "$0")/common.sh"; set -e
exec python3 "$FB/scripts/task.py" "$@"
