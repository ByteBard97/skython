# plugins.ps1 -Mode read|backup|add|restore [-ExpectCur <sha256>] [-ExpectNew <sha256>]   (owner decision 7, Task 45)
# The box's plugins.txt (path verified in Task 45) changes only by one added line "*SkythonTest.esp" and by putting the
# backup back. read: "CUR <sha|NONE> <b64|->", "BAK <sha|NONE> <b64|->", "GAME IDLE|RUNNING". backup: copies plugins.txt
# to plugins.txt.bak once ("BACKED_UP"/"BACKUP_SAME <sha>"; a differing backup is never overwritten: "BACKUP_DIFFERS",
# exit 3). add: game idle, plugins.txt == backup (sha -ExpectCur), backup + [CRLF] + "*SkythonTest.esp" CRLF hashes to
# -ExpectNew -> "ADDED <sha>". restore: game idle, plugins.txt (sha -ExpectCur) == backup + that line -> copies the
# backup back, "RESTORED <sha>". Anything else: "REFUSED <why>", exit 3. Nothing is ever deleted.
param([string]$Mode, [string]$ExpectCur = '', [string]$ExpectNew = '')
$ErrorActionPreference = 'Stop'
$PluginsTxt = 'C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt'
$Backup = 'C:\dev\skyrim-python-box\plugins.txt.bak'
$Line = [Text.Encoding]::ASCII.GetBytes("*SkythonTest.esp`r`n")
function Sha([byte[]]$b) { ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace('-', '').ToLower() }
function Bytes([string]$p) { if (Test-Path -LiteralPath $p) { return ,[IO.File]::ReadAllBytes($p) } else { return $null } }
function Refuse([string]$why) { "REFUSED $why"; exit 3 }
function GameRunning { return [bool](Get-Process -Name SkyrimSE,skse64_loader,SkyrimSELauncher,ModOrganizer -ErrorAction SilentlyContinue) }
function Listed([byte[]]$b) {
  foreach ($l in ([Text.Encoding]::UTF8.GetString($b) -split "`r?`n")) { if ($l.Trim().TrimStart('*').ToLower() -eq 'skythontest.esp') { return $true } }
  return $false
}
function Compose([byte[]]$bak) {
  $sep = [byte[]]@()
  if ($bak.Length -gt 0 -and $bak[$bak.Length - 1] -ne 10) { $sep = [byte[]](13, 10) }
  return ,([byte[]]($bak + $sep + $Line))
}
switch ($Mode) {
  'read' {
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c) { 'CUR NONE -' } else { "CUR $(Sha $c) $([Convert]::ToBase64String($c))" }
    if ($null -eq $b) { 'BAK NONE -' } else { "BAK $(Sha $b) $([Convert]::ToBase64String($b))" }
    if (GameRunning) { 'GAME RUNNING' } else { 'GAME IDLE' }
    exit 0
  }
  'backup' {
    $c = Bytes $PluginsTxt
    if ($null -eq $c) { Refuse "no $PluginsTxt" }
    $b = Bytes $Backup
    if ($null -eq $b) {
      Copy-Item -LiteralPath $PluginsTxt -Destination $Backup
      $b = Bytes $Backup
      if ((Sha $b) -ne (Sha $c)) { Refuse 'the backup copy does not match plugins.txt' }
      "BACKED_UP $(Sha $b)"; exit 0
    }
    if ((Sha $b) -eq (Sha $c)) { "BACKUP_SAME $(Sha $b)"; exit 0 }
    "BACKUP_DIFFERS bak=$(Sha $b) cur=$(Sha $c)"; exit 3
  }
  'add' {
    if (GameRunning) { Refuse 'the game is running' }
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c -or $null -eq $b) { Refuse 'plugins.txt or its backup is missing (run backup first)' }
    if ((Sha $c) -ne $ExpectCur) { Refuse "plugins.txt has sha $(Sha $c), Linux expected $ExpectCur" }
    if ((Sha $c) -ne (Sha $b)) { Refuse 'plugins.txt is not identical to the backup' }
    if (Listed $b) { Refuse 'SkythonTest.esp is already listed in the backup' }
    $n = Compose $b
    if ((Sha $n) -ne $ExpectNew) { Refuse "backup + line has sha $(Sha $n), Linux expected $ExpectNew" }
    [IO.File]::WriteAllBytes($PluginsTxt, $n)
    $w = Bytes $PluginsTxt
    if ((Sha $w) -ne $ExpectNew) { Refuse "after the write plugins.txt has sha $(Sha $w)" }
    "ADDED $(Sha $w)"; exit 0
  }
  'restore' {
    if (GameRunning) { Refuse 'the game is running' }
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c -or $null -eq $b) { Refuse 'plugins.txt or its backup is missing' }
    if ((Sha $c) -ne $ExpectCur) { Refuse "plugins.txt has sha $(Sha $c), Linux expected $ExpectCur" }
    if ((Sha $c) -ne (Sha (Compose $b))) { Refuse 'plugins.txt is not exactly the backup plus *SkythonTest.esp' }
    Copy-Item -LiteralPath $Backup -Destination $PluginsTxt -Force
    $w = Bytes $PluginsTxt
    if ((Sha $w) -ne (Sha $b)) { Refuse "after the copy plugins.txt has sha $(Sha $w)" }
    "RESTORED $(Sha $w)"; exit 0
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
