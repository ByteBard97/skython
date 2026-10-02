#!/usr/bin/env bash
# One-time (re-runnable) setup: copy tools/box/win/*.ps1 to C:\dev\skyrim-python-box, check each parses as
# PowerShell on the box before it is put in place, then register the scheduled tasks SkyrimPythonGame and
# SkyrimPythonShot (setup_task.ps1; an existing task is left as it is, EXISTS). Touches nothing outside
# C:\dev\skyrim-python-box except creating those two tasks.
# It also writes C:\dev\skyrim-python-box\box.config from tools/box/box.local.env: the Windows user the tasks run
# as and the folder of each game install (win/config_lib.ps1 reads it). Re-run it whenever box.local.env changes.
# Last it runs config.ps1 (read-only): each install's folder, its SkyrimSE.exe version and the version required.
# BOX_SETUP_PRINT_CONFIG=1 prints box.config's text and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
config_text() {
  local k
  printf 'user\t%s\n' "$BOX_WIN_USER"
  for k in "${BOX_GAMES[@]}"; do printf 'game\t%s\t%s\n' "$k" "$(box_game_dir "$k")"; done
}
if [ "${BOX_SETUP_PRINT_CONFIG:-0}" = 1 ]; then config_text; exit 0; fi
psv="$(bssh 'Write-Output "PSV=$($PSVersionTable.PSVersion.Major)"' | tr -d '\r')" || box_die "cannot ssh to $BOX (check that the box is on and that ssh $BOX works)"
case "$psv" in *PSV=5*) ;; *) box_die "the box's ssh shell is not Windows PowerShell 5 (answered '$psv')" ;; esac
bssh "New-Item -ItemType Directory -Force '$BOX_DIR\\incoming','$BOX_DIR\\stage' | Out-Null" || box_die "could not create $BOX_DIR"
bscp "$BOX_ROOT"/tools/box/win/*.ps1 "$BOX:$BOX_DIR_FWD/incoming/" || box_die "scp of the scripts failed"
# Parse every script with PowerShell's own parser; only then copy it into place.
bssh "\$bad = 0; Get-ChildItem '$BOX_DIR\\incoming' -Filter *.ps1 | ForEach-Object { \$e = \$null; [void][System.Management.Automation.Language.Parser]::ParseFile(\$_.FullName, [ref]\$null, [ref]\$e); if (\$e.Count) { \$bad++; 'PARSE_ERROR ' + \$_.Name + ': ' + (\$e | % { \$_.Message + ' line ' + \$_.Extent.StartLineNumber }) -join '; ' } else { 'parsed ' + \$_.Name } }; if (\$bad) { exit 1 }; Copy-Item '$BOX_DIR\\incoming\\*.ps1' '$BOX_DIR' -Force; 'INSTALLED'" \
  | tr -d '\r' || box_die "a script does not parse; nothing was installed"
bssh "[IO.File]::WriteAllText('$BOX_DIR\\box.config', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$(b64 "$(config_text)")')))" \
  || box_die "could not write $BOX_DIR\\box.config"
echo "wrote $BOX_DIR\\box.config"
bps 60 setup_task.ps1 | tr -d '\r'
bps 60 config.ps1 | tr -d '\r'
