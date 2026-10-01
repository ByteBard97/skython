# console.ps1 -Tool <devbench tool> -JsonB64 <request body> [-TimeoutSec 15] | -List
# Calls DevBench's REST API on the box's loopback (POST /api/tool/<tool>, GET /api/tools). DevBench runs inside
# the game (SKSE plugin), so it answers only while the game runs.
param([string]$Tool = 'console', [string]$JsonB64 = '', [int]$TimeoutSec = 15, [switch]$List)
$base = 'http://127.0.0.1:8920'
# Reply bytes are written as UTF-8 unchanged (Content would decode JSON without a charset as Latin-1, and the
# console code page would mangle it again).
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
function Emit($r) { [Console]::Out.Write([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())); [Console]::Out.WriteLine() }
try {
  if ($List) { Emit (Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec "$base/api/tools"); exit 0 }
  $body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($JsonB64))
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Method Post -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) "$base/api/tool/$Tool"
  Emit $r; exit 0
} catch {
  $detail = ''
  if ($_.ErrorDetails) { $detail = $_.ErrorDetails.Message }
  "ERR $($_.Exception.Message) $detail"; exit 1
}
