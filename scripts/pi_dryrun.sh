#!/usr/bin/env bash
# Runs hexa on the Pi in a tmux session (it survives your ssh session closing): dry-run body,
# phone page on the LAN, no microphone, no speaker, scripted chat. Moves nothing. Touches only
# ~/hexa. Nothing is enabled at boot.
#
# Usage (on the Pi, in ~/hexa):
#   scripts/pi_dryrun.sh start [PIN]   start it (PIN: 4-12 digits; default a random 6-digit one)
#   scripts/pi_dryrun.sh log           watch logs/dryrun.log (what the gait would send to the servos)
#   scripts/pi_dryrun.sh status        is it running, and the page address(es)
#   scripts/pi_dryrun.sh stop          clean stop (Ctrl-C inside tmux: stop, silence, join, exit 0)
#   scripts/pi_dryrun.sh attach        look at the live console (detach again with Ctrl-b d)
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
session="hexa"
cd "${root}"
mkdir -p logs .tmp

case "${1:-}" in
  start)
    tmux has-session -t "${session}" 2>/dev/null && { echo "already running (status / stop)"; exit 1; }
    pin="${2:-$(( RANDOM % 900000 + 100000 ))}"
    [[ "${pin}" =~ ^[0-9]{4,12}$ ]] || { echo "PIN must be 4-12 digits" >&2; exit 2; }
    : > logs/dryrun.log
    tmux new-session -d -s "${session}" \
      "cd '${root}' && TMPDIR='${root}/.tmp' HEXA_WEB_PIN='${pin}' \
       .venv/bin/python main.py --backend dryrun --lan --no-mic --no-speak --chat fake; \
       echo; echo 'hexa has stopped (press Enter to close)'; read -r _"
    sleep 4
    echo "PIN: ${pin}"
    "${BASH_SOURCE[0]}" status ;;
  status)
    if tmux has-session -t "${session}" 2>/dev/null; then
      echo "running (tmux session '${session}')"
      tmux capture-pane -p -t "${session}" | grep -E "web page" || true
    else echo "not running"; fi ;;
  log) exec tail -n 30 -F logs/dryrun.log ;;
  attach) exec tmux attach -t "${session}" ;;
  stop)
    tmux has-session -t "${session}" 2>/dev/null || { echo "not running"; exit 0; }
    tmux send-keys -t "${session}" C-c   # SIGINT: a clean shutdown, the body is stopped first
    for _ in $(seq 1 20); do
      tmux capture-pane -p -t "${session}" | grep -q "hexa has stopped" && break
      sleep 0.5
    done
    tmux kill-session -t "${session}" 2>/dev/null || true
    echo "stopped" ;;
  *) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
