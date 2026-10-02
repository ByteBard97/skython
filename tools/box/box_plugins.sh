#!/usr/bin/env bash
# The box's plugins.txt (owner decision 7, Task 45): back it up once, add the one line *SkythonTest.esp, show it, put the
# backup back. Only win/plugins.ps1 writes the file; this script sends SHA-256 values, never bytes, and the box re-checks.
# Usage: box_plugins.sh backup | show | add | restore | restore-if-added
#        box_plugins.sh compose BAK_FILE | classify CUR_FILE BAK_FILE     (local, offline; classify: BACKUP|ADDED|OTHER)
# backup and add refuse unless Gate 0 is recorded (out/gate0-accepted, need_gate0 in box_env.sh); restore never needs it.
# add/restore refuse while the game runs and record added/restored in $SKYTHON_STATE/box-plugins.state; box_quit.sh and
# box_stop.sh run restore-if-added once the game has ended. A plugins.txt that is neither the backup nor the backup plus
# our line is a STOP (exit 3, diff printed, nothing written).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
STATE_FILE="$SKYTHON_STATE/box-plugins.state"
WORK="$SKYTHON_STATE/box-plugins"   # this run's copies (overwritten each run)

compose() {  # compose <bak file> [inactive]: backup + [EOL] + "*SkythonTest.esp" EOL on stdout (EOL: the backup's own,
             # CRLF if it has a CR, else LF; R64); with "inactive" the line has no "*" (R71: the game appends that line
             # itself when the ESP is in Data but not enabled); refuses a backup that lists it
  python3 - "$1" "${2:-}" <<'PY'
import sys
b = open(sys.argv[1], "rb").read()
star = b"" if sys.argv[2] == "inactive" else b"*"
if b"skythontest.esp" in [l.strip().lstrip(b"*").lower() for l in b.splitlines()]:
    sys.exit("box_plugins: SkythonTest.esp is already listed in the backup; ask the owner")
eol = b"\r\n" if b"\r" in b else b"\n"
sep = eol if b and not b.endswith(b"\n") else b""
sys.stdout.buffer.write(b + sep + star + b"SkythonTest.esp" + eol)
PY
}
classify() {  # classify <cur> <bak>: BACKUP (identical), ADDED (backup + our line, byte for byte), INACTIVE (backup +
              # "SkythonTest.esp" without "*", written by the game, R71) or OTHER
  local t="$WORK/composed"
  mkdir -p "$WORK"
  if cmp -s "$1" "$2"; then echo BACKUP
  elif compose "$2" > "$t" 2>/dev/null && cmp -s "$1" "$t"; then echo ADDED
  elif compose "$2" inactive > "$t" 2>/dev/null && cmp -s "$1" "$t"; then echo INACTIVE
  else echo OTHER; fi
}
sha() { sha256sum "$1" | cut -d' ' -f1; }
field() { echo "$1" | awk -v k="$2" -v n="$3" '$1 == k { print $n }'; }
read_box() {  # sets CUR_SHA, BAK_SHA, GAME and writes $WORK/cur, $WORK/bak
  local out
  mkdir -p "$WORK"
  out="$(bps 60 plugins.ps1 -Mode read | tr -d '\r')" || box_die "plugins.ps1 read failed: $out"
  CUR_SHA="$(field "$out" CUR 2)"; BAK_SHA="$(field "$out" BAK 2)"; GAME="$(field "$out" GAME 2)"
  [ -n "$CUR_SHA" ] && [ -n "$BAK_SHA" ] && [ -n "$GAME" ] || box_die "unexpected plugins.ps1 read output: $out"
  [ "$CUR_SHA" != NONE ] || box_die "no plugins.txt at the verified path on the box; STOP and ask the owner"
  field "$out" CUR 3 | base64 -d > "$WORK/cur"
  [ "$(sha "$WORK/cur")" = "$CUR_SHA" ] || box_die "plugins.txt arrived damaged (SHA-256 mismatch)"
  if [ "$BAK_SHA" != NONE ]; then
    field "$out" BAK 3 | base64 -d > "$WORK/bak"
    [ "$(sha "$WORK/bak")" = "$BAK_SHA" ] || box_die "the backup arrived damaged (SHA-256 mismatch)"
  fi
}
need_idle_and_backup() {
  [ "$GAME" = IDLE ] || box_die "the game is running on the box; plugins.txt is changed only while it is not"
  [ "$BAK_SHA" != NONE ] || box_die "no backup yet: run tools/box/box_plugins.sh backup first"
}
stop_other() {
  echo "STOP: plugins.txt on the box is neither the backup nor the backup plus *SkythonTest.esp. Diff (backup -> current):" >&2
  diff <(tr -d '\r' < "$WORK/bak") <(tr -d '\r' < "$WORK/cur") >&2 || true
  echo "Nothing was written. Ask the owner." >&2
  exit 3
}
mark() { mkdir -p "$SKYTHON_STATE"; echo "$1 $(date -Is)" > "$STATE_FILE"; }

case "${1:-}" in
  compose)  [ $# -eq 2 ] || box_die "usage: box_plugins.sh compose BAK_FILE"; compose "$2" ;;
  classify) [ $# -eq 3 ] || box_die "usage: box_plugins.sh classify CUR_FILE BAK_FILE"; classify "$2" "$3" ;;
  backup)
    need_gate0
    r="$(bps 60 plugins.ps1 -Mode backup | tr -d '\r')" || true
    echo "$r"
    case "$r" in
      BACKED_UP*|BACKUP_SAME*) ;;
      *) echo "STOP: no usable backup (above); nothing was overwritten. Ask the owner." >&2; exit 3 ;;
    esac ;;
  show)
    read_box
    echo "plugins.txt sha256 $CUR_SHA; backup $BAK_SHA; game $GAME"
    [ "$BAK_SHA" = NONE ] || echo "state: $(classify "$WORK/cur" "$WORK/bak")"
    tr -d '\r' < "$WORK/cur" ;;
  add)
    need_gate0
    read_box; need_idle_and_backup
    case "$(classify "$WORK/cur" "$WORK/bak")" in
      ADDED)  mark added; echo "ALREADY: *SkythonTest.esp is already the one added line" ;;
      INACTIVE|BACKUP)
              if [ "$(classify "$WORK/cur" "$WORK/bak")" = INACTIVE ]; then  # R71: put the backup back first
                r="$(bps 60 plugins.ps1 -Mode restore -ExpectCur "$CUR_SHA" | tr -d '\r')" || true
                [ "$r" = "RESTORED $BAK_SHA" ] || box_die "add (restoring the inactive line first): '$r' (expected RESTORED $BAK_SHA)"
                echo "$r (the game's inactive SkythonTest.esp line)"; CUR_SHA="$BAK_SHA"
              fi
              compose "$WORK/bak" > "$WORK/new"; new="$(sha "$WORK/new")"
              r="$(bps 60 plugins.ps1 -Mode add -ExpectCur "$CUR_SHA" -ExpectNew "$new" | tr -d '\r')" || true
              [ "$r" = "ADDED $new" ] || box_die "add: '$r' (expected ADDED $new)"
              mark added; echo "$r" ;;
      *) stop_other ;;
    esac ;;
  restore|restore-if-added)
    if [ "$1" = restore-if-added ] && [ "$(cut -d' ' -f1 "$STATE_FILE" 2>/dev/null || true)" != added ]; then
      echo "plugins.txt: nothing to restore (no add recorded this session)"; exit 0
    fi
    read_box; need_idle_and_backup
    case "$(classify "$WORK/cur" "$WORK/bak")" in
      BACKUP) mark restored; echo "ALREADY_RESTORED: plugins.txt equals the backup" ;;
      ADDED|INACTIVE)  r="$(bps 60 plugins.ps1 -Mode restore -ExpectCur "$CUR_SHA" | tr -d '\r')" || true
              [ "$r" = "RESTORED $BAK_SHA" ] || box_die "restore: '$r' (expected RESTORED $BAK_SHA)"
              mark restored; echo "$r" ;;
      *) stop_other ;;
    esac ;;
  *) box_die "usage: box_plugins.sh backup | show | add | restore | restore-if-added | compose BAK | classify CUR BAK" ;;
esac
