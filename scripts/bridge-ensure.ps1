# bridge-ensure.ps1 — zorgt dat de cliproxy-bridge (:8317) luistert voor er
# gedelegeerd wordt. Best-effort: herstart een dode bridge exact een keer, op
# dezelfde manier als hij nu draait (cli-proxy-api.exe -config config.yaml).
# Nooit fataal, nooit een tweede instance als er al een proces leeft.
# Adresseert de ECONNREFUSED-klasse gemeten in de stocktake van 2026-08-18.
param([int]$Port = 8317, [int]$WaitSec = 6)
$ErrorActionPreference = 'SilentlyContinue'

function Test-Bridge([int]$p) {
  # Async connect met harde 800ms-cap: een synchrone Connect blokkeert tot de
  # OS-timeout op een niet-luisterende poort, wat de healthcheck zelf traag maakt.
  $c = New-Object Net.Sockets.TcpClient
  try {
    if ($c.ConnectAsync('127.0.0.1', $p).Wait(800)) { return $c.Connected }
    return $false
  } catch { return $false }
  finally { $c.Close() }
}

# Al up: klaar, niks doen.
if (Test-Bridge $Port) { exit 0 }

# Draait de exe al maar luistert nog niet (mid-boot of wedged)? Dan wachten,
# geen tweede instance starten.
$proc = Get-Process cli-proxy-api -ErrorAction SilentlyContinue
if (-not $proc) {
  $dir = Join-Path $env:USERPROFILE 'cliproxy'
  $exe = Join-Path $dir 'cli-proxy-api.exe'
  $cfg = Join-Path $dir 'config.yaml'
  if (-not (Test-Path $exe)) { Write-Error '[bridge-ensure] cli-proxy-api.exe ontbreekt'; exit 2 }
  Start-Process -FilePath $exe -ArgumentList @('-config', $cfg) `
    -WorkingDirectory $dir -WindowStyle Hidden
  Write-Host '[bridge-ensure] bridge lag plat, cli-proxy-api herstart'
}

# Wachten tot hij luistert.
for ($i = 0; $i -lt ($WaitSec * 2); $i++) {
  if (Test-Bridge $Port) { exit 0 }
  Start-Sleep -Milliseconds 500
}
Write-Error "[bridge-ensure] bridge kwam niet up binnen ${WaitSec}s"
exit 1
