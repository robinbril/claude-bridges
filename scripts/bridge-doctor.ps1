# bridge-doctor.ps1 — checkt de README-valkuilen tegen de lokale stack.
# Elke check faalt open; de rest draait door. Exit-code = aantal FAILs.
#   powershell -NoProfile -File scripts/bridge-doctor.ps1
#   powershell -NoProfile -File scripts/bridge-doctor.ps1 -Ping
param([switch]$Ping)

$fails = 0

function Write-Check([string]$name, [bool]$ok, [string]$reason) {
  $tag = if ($ok) { 'OK  ' } else { 'FAIL' }
  Write-Host ("{0} {1}: {2}" -f $tag, $name, $reason)
  if (-not $ok) { $script:fails++ }
}

function Test-BridgePort([int]$p) {
  # Async connect met 800ms-cap: synchrone Connect wacht op de OS-timeout.
  $c = New-Object Net.Sockets.TcpClient
  try {
    if ($c.ConnectAsync('127.0.0.1', $p).Wait(800)) { return $c.Connected }
    return $false
  } catch { return $false }
  finally { $c.Close() }
}

function ConvertFrom-Base64Url([string]$s) {
  $s = $s.Replace('-', '+').Replace('_', '/')
  switch ($s.Length % 4) {
    1 { $s += '===' }
    2 { $s += '==' }
    3 { $s += '=' }
  }
  [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
}

function Format-Age([TimeSpan]$age) {
  if ($age.TotalMinutes -lt 90) { return ('{0:n0} min' -f $age.TotalMinutes) }
  if ($age.TotalHours -lt 48) { return ('{0:n0} u' -f $age.TotalHours) }
  return ('{0:n1} d' -f $age.TotalDays)
}

function Check-Bridge {
  try {
    if (Test-BridgePort 8317) {
      Write-Check 'Bridge up' $true '127.0.0.1:8317 luistert'
    } else {
      Write-Check 'Bridge up' $false '127.0.0.1:8317 reageert niet (800ms)'
    }
  } catch {
    Write-Check 'Bridge up' $false $_.Exception.Message
  }
}

function Check-GrokJwt {
  try {
    $auth = Join-Path $env:USERPROFILE '.grok\auth.json'
    if (-not (Test-Path -LiteralPath $auth)) {
      Write-Check 'Grok-JWT' $false '~/.grok/auth.json ontbreekt'
      return
    }
    $raw = [IO.File]::ReadAllText($auth)
    # Eerste JWT (eyJ.header.payload.sig); exp zit in de payload.
    $m = [regex]::Match($raw, 'eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+')
    if (-not $m.Success) {
      Write-Check 'Grok-JWT' $false 'geen eyJ-token in ~/.grok/auth.json'
      return
    }
    $payload = ConvertFrom-Base64Url $m.Groups[1].Value | ConvertFrom-Json
    if ($null -eq $payload.exp) {
      Write-Check 'Grok-JWT' $false 'token heeft geen exp'
      return
    }
    $exp = [int64]$payload.exp
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $remainingMin = [int][math]::Floor(($exp - $now) / 60.0)
    if ($remainingMin -lt 30) {
      if ($remainingMin -lt 0) {
        Write-Check 'Grok-JWT' $false ("verlopen ({0} min geleden)" -f (-$remainingMin))
      } else {
        Write-Check 'Grok-JWT' $false ("nog {0} min (drempel 30)" -f $remainingMin)
      }
    } else {
      Write-Check 'Grok-JWT' $true ("nog {0} min" -f $remainingMin)
    }
  } catch {
    Write-Check 'Grok-JWT' $false $_.Exception.Message
  }
}

function Check-Cooldown {
  try {
    $cfg = Join-Path $env:USERPROFILE 'cliproxy\config.yaml'
    if (-not (Test-Path -LiteralPath $cfg)) {
      Write-Check 'Cooldown-config' $false 'cliproxy\config.yaml ontbreekt'
      return
    }
    $hits = @(Select-String -LiteralPath $cfg -SimpleMatch 'disable-cooling: true')
    if ($hits.Count -lt 2) {
      Write-Check 'Cooldown-config' $false ("{0} hits (minimaal 2)" -f $hits.Count)
    } else {
      Write-Check 'Cooldown-config' $true ("{0}x disable-cooling: true" -f $hits.Count)
    }
  } catch {
    Write-Check 'Cooldown-config' $false $_.Exception.Message
  }
}

function Check-ModelRouting {
  try {
    $cfg = Join-Path $env:USERPROFILE 'cliproxy\config.yaml'
    if (-not (Test-Path -LiteralPath $cfg)) {
      Write-Check 'Model-routing' $false 'cliproxy\config.yaml ontbreekt'
      return
    }
    $broadAliases = @(Select-String -LiteralPath $cfg -Pattern 'alias:\s*["'']?claude-(opus|haiku|sonnet|fable)-')
    Write-Check 'Model-routing' ($broadAliases.Count -eq 0) 'Algemene Claude-ID-aliassen horen niet in de expliciete task-runnerconfig; controleer andere consumers voor aanpassen.'
  } catch {
    Write-Check 'Model-routing' $false $_.Exception.Message
  }
}
function Check-CodexAuth {
  try {
    $dir = Join-Path $env:USERPROFILE '.cli-proxy-api'
    if (-not (Test-Path -LiteralPath $dir)) {
      Write-Check 'Codex-auth' $false '~/.cli-proxy-api ontbreekt'
      return
    }
    $files = @(Get-ChildItem -LiteralPath $dir -Filter 'codex-*.json' -File)
    if ($files.Count -eq 0) {
      Write-Check 'Codex-auth' $false 'geen codex-*.json in ~/.cli-proxy-api'
      return
    }
    $newest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $age = Format-Age ((Get-Date) - $newest.LastWriteTime)
    Write-Check 'Codex-auth' $true ("{0} bestand(en), jongste {1} (auto-refresh 15 min)" -f $files.Count, $age)
  } catch {
    Write-Check 'Codex-auth' $false $_.Exception.Message
  }
}

function Check-Ping {
  try {
    $key = $env:MODEL_ROUTER_KEY
    if ([string]::IsNullOrWhiteSpace($key)) {
      Write-Check 'Live ping' $false 'MODEL_ROUTER_KEY ontbreekt'
      return
    }
    $ProgressPreference = 'SilentlyContinue'
    $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8317/v1/models' -Method Get `
      -Headers @{ Authorization = "Bearer $key" } `
      -UseBasicParsing -TimeoutSec 8
    $code = [int]$resp.StatusCode
    if ($code -ge 200 -and $code -lt 300) {
      Write-Check 'Live ping' $true ("HTTP {0}" -f $code)
    } else {
      Write-Check 'Live ping' $false ("HTTP {0}" -f $code)
    }
  } catch {
    $code = $null
    $resp = $_.Exception.Response
    if ($resp -and $resp.StatusCode) { $code = [int]$resp.StatusCode }
    if ($code) {
      Write-Check 'Live ping' $false ("HTTP {0}" -f $code)
    } else {
      Write-Check 'Live ping' $false $_.Exception.Message
    }
  }
}

Check-Bridge
Check-GrokJwt
Check-Cooldown
Check-ModelRouting
Check-CodexAuth
if ($Ping) { Check-Ping }

exit $fails
