# stack-ensure.ps1 — brengt de volledige bridge-stack up, idempotent.
# Draait bij logon (scheduled task Bridge-Stack-Autostart) en is veilig om
# op elk moment handmatig te herdraaien: een service die al luistert wordt
# overgeslagen, er start nooit een tweede instance.
#
#   poort  service            start
#   8317   cli-proxy-api      cliproxy\cli-proxy-api.exe -config config.yaml
#   3457   grok-auth-proxy    node .claude-code-router\grok-auth-proxy.js
#   3456   claude-code-router ccr start (Codex-app model-menu praat hiermee)
#   4000   litellm            litellm --config litellm-grok.yaml (tekst-workers)
#   11435  embed-server       pythonw wiki-rag\embed_server.py (dense recall)
#
# Nooit fataal: elke sectie faalt open, de rest gaat door. Log: stack-ensure.log
$ErrorActionPreference = 'SilentlyContinue'
$home_ = $env:USERPROFILE
$log = Join-Path $home_ 'orchestrate\stack-ensure.log'

function Log([string]$m) {
  Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}

function Test-Port([int]$p) {
  $c = New-Object Net.Sockets.TcpClient
  try {
    if ($c.ConnectAsync('127.0.0.1', $p).Wait(800)) { return $c.Connected }
    return $false
  } catch { return $false }
  finally { $c.Close() }
}

function Ensure([int]$port, [string]$name, [scriptblock]$start) {
  if (Test-Port $port) { return }
  Log "$name (:$port) down, start"
  & $start
  for ($i = 0; $i -lt 20; $i++) {
    if (Test-Port $port) { Log "$name up"; return }
    Start-Sleep -Milliseconds 500
  }
  Log "$name kwam niet up binnen 10s"
}

Ensure 8317 'cli-proxy-api' {
  Start-Process -FilePath (Join-Path $home_ 'cliproxy\cli-proxy-api.exe') `
    -ArgumentList @('-config', (Join-Path $home_ 'cliproxy\config.yaml')) `
    -WorkingDirectory (Join-Path $home_ 'cliproxy') -WindowStyle Hidden
}

Ensure 3457 'grok-auth-proxy' {
  Start-Process -FilePath 'C:\Program Files\nodejs\node.exe' `
    -ArgumentList @((Join-Path $home_ '.claude-code-router\grok-auth-proxy.js')) `
    -WorkingDirectory (Join-Path $home_ '.claude-code-router') -WindowStyle Hidden
}

Ensure 3456 'claude-code-router' {
  # ccr start draait als daemon; de npm-shim heeft een shell nodig.
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'ccr', 'start' -WindowStyle Hidden
}

Ensure 4000 'litellm' {
  Start-Process -FilePath (Join-Path $home_ '.claude-code-router\lllm311\Scripts\litellm.exe') `
    -ArgumentList @('--config', (Join-Path $home_ '.claude-code-router\litellm-grok.yaml'), '--port', '4000') `
    -WorkingDirectory (Join-Path $home_ '.claude-code-router') -WindowStyle Hidden
}

Ensure 11435 'embed-server' {
  Start-Process -FilePath (Join-Path $home_ 'AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe') `
    -ArgumentList @((Join-Path $home_ '.claude\scripts\wiki-rag\embed_server.py')) `
    -WindowStyle Hidden
}

Log 'stack-ensure klaar'
