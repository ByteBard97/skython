# setup_task.ps1: one-time. Registers the scheduled task SkyrimPythonGame (interactive, runs as the logged-on
# user geoff, limited rights, on demand only: no trigger) that runs launch.ps1. If the task exists it prints
# its action and changes nothing. It never modifies or removes any other task.
$ErrorActionPreference = 'Stop'
$name = 'SkyrimPythonGame'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) { "EXISTS $name -> " + ($existing.Actions | % { "$($_.Execute) $($_.Arguments)" }); exit 0 }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\dev\skyrim-python-box\launch.ps1'
$principal = New-ScheduledTaskPrincipal -UserId 'geoff' -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings `
  -Description 'skyrim-python: start Steam if needed, wait for logon, start skse64_loader (C:\dev\skyrim-python-box\launch.ps1)' | Out-Null
$t = Get-ScheduledTask -TaskName $name
"REGISTERED $name -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) + " as $($t.Principal.UserId) $($t.Principal.LogonType)"
