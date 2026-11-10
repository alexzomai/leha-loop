#!/bin/bash
# usage: send.sh <terminal> <text> — send a message to an agent (findings for a worker, an answer
# to its question, a command for the merger) so that babysit-hc.sh then waits for the reply turn.
. "$(dirname "$0")/common.sh"; set -e
[ $# -eq 2 ] || fb_die "usage: send.sh <terminal> <text>"
fb_live "$1" || fb_die "$1 is not live"
[ "$(fb_status "$1")" = working ] && fb_die "$1 is working right now; wait for the turn to end (babysit-hc.sh $1)"
fb_send "$1" "$2"
echo "sent to $1"
