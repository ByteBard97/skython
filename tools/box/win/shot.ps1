# shot.ps1: run by our scheduled task SkyrimPythonShot (interactive, as geoff), not over ssh: an ssh session
# cannot see the desktop. Saves a screenshot of the whole virtual screen to C:\dev\skyrim-python-box\shot.png,
# then the processes that have a main window (name, id, title, Responding) to windows.txt in the same folder.
# windows.txt is written last, so box_shot.sh treats a new windows.txt as "shot.png is complete". It writes
# only those two files.
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b=[System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height
$g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size)
$bmp.Save('C:\dev\skyrim-python-box\shot.png',[System.Drawing.Imaging.ImageFormat]::Png)
Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object ProcessName,Id,MainWindowTitle,Responding | Format-Table -AutoSize | Out-String -Width 300 | Set-Content C:\dev\skyrim-python-box\windows.txt
