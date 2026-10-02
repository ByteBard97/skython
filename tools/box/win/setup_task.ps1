# setup_task.ps1: one-time. Registers our two scheduled tasks (interactive, run as the logged-on user geoff,
# limited rights, on demand only: no trigger):
#   SkyrimPythonGame -> launch.ps1 (box_launch.sh)
#   SkyrimPythonShot -> shot.ps1   (box_shot.sh: screenshot and window titles, e.g. of a modal SKSE dialog)
# The task names keep their pre-Skython names: the tasks already exist on the box and renaming would orphan them.
# A task that exists is printed (EXISTS) and left as it is. It never modifies or removes any other task.
$ErrorActionPreference = 'Stop'
function Register-Ours([string]$name, [string]$script, [int]$minutes, [string]$desc) {
  $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($existing) { "EXISTS $name -> " + ($existing.Actions | % { "$($_.Execute) $($_.Arguments)" }); return }
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\dev\skyrim-python-box\$script"
  $principal = New-ScheduledTaskPrincipal -UserId 'geoff' -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes $minutes) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Description $desc | Out-Null
  $t = Get-ScheduledTask -TaskName $name
  "REGISTERED $name -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) + " as $($t.Principal.UserId) $($t.Principal.LogonType)"
}
Register-Ours 'SkyrimPythonGame' 'launch.ps1' 15 'skyrim-python: start Steam if needed, wait for logon, start skse64_loader (C:\dev\skyrim-python-box\launch.ps1)'
Register-Ours 'SkyrimPythonShot' 'shot.ps1' 2 'skyrim-python: screenshot and window titles to C:\dev\skyrim-python-box\shot.png and windows.txt (shot.ps1)'
