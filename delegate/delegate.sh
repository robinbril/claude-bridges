#!/usr/bin/env bash
# delegate.sh — draai de volledige claude-harness headless op een andere rail
# (brein). Tools, MCP en repo-regels blijven gelijk; alleen het model wisselt.
#
# Gebruik:  delegate.sh <rail> "<allowedTools>" <promptfile> [model]
#
#   rail        model-keten (default)         effort   fallback
#   auto        gewogen keuze uit AUTO_SPLIT  per rail n.v.t.
#   grok        grok-4.6 via de bridge        xhigh    openrouter
#   codex       gpt-5.6-terra > luna          high     grok
#   openrouter  deepseek                      default  geen
#   cursor      cursor-agent CLI              n.v.t.   geen (eigen agent-loop)
#   <seat>      native claude-seat            seat     geen (SEAT_<NAAM> in conf)
#
# Env per call: DELEGATE_CWD (doel-repo), DELEGATE_EFFORT, DELEGATE_MCP=1,
# DELEGATE_CONTEXT (extra context-packs), DELEGATE_DRY=1 (print rail, stop).
# Machine-instellingen: ~/.delegate.conf (zie delegate.conf.example).
set -euo pipefail

DELEGATE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Conf buiten git; defaults werken out-of-the-box met de standaard bridge.
DELEGATE_CONF="${DELEGATE_CONF:-$HOME/.delegate.conf}"
[ -f "$DELEGATE_CONF" ] && . "$DELEGATE_CONF"

BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8317}"
BRIDGE_PORT="${BRIDGE_URL##*:}"
AUTO_SPLIT="${AUTO_SPLIT:-grok:65 codex:35}"
GROK_MODEL="${GROK_MODEL:-grok}"
GROK_EFFORT="${GROK_EFFORT:-xhigh}"
CODEX_CHAIN="${CODEX_CHAIN:-gpt-5.6-terra gpt-5.6-luna}"
CODEX_EFFORT="${CODEX_EFFORT:-high}"
CODEX_SMALL_FAST="${CODEX_SMALL_FAST:-gpt-5.4-mini}"
OPENROUTER_MODEL="${OPENROUTER_MODEL:-deepseek}"
CURSOR_MODEL="${CURSOR_MODEL:-grok}"
WSL_DISTRO="${WSL_DISTRO:-Ubuntu}"
STATE_DIR="${DELEGATE_STATE_DIR:-$DELEGATE_HOME/state}"
mkdir -p "$STATE_DIR"

rail="${1:-auto}"
tools="${2:?allowedTools, bv \"Read Grep Glob Bash\"}"
promptfile="${3:?promptfile}"
model="${4:-}"

[ -f "$promptfile" ] || { echo "promptfile niet gevonden: $promptfile" >&2; exit 2; }
prompt="$(cat "$promptfile")"

# Start in de doel-repo: anders laadt de harness diens CLAUDE.md en skills
# niet en draait de agent zonder repo-regels.
workdir="${DELEGATE_CWD:-$PWD}"
[ -d "$workdir" ] || { echo "DELEGATE_CWD bestaat niet: $workdir" >&2; exit 2; }
cd "$workdir"
echo "[delegate] rail=$rail cwd=$workdir" >&2
if [ ! -f "$workdir/CLAUDE.md" ]; then
  echo "[delegate] WAARSCHUWING: geen CLAUDE.md in $workdir, agent draait zonder repo-regels" >&2
fi

# Context-injectie vooraan de prompt. De conf mag een delegate_context-functie
# definieren die per workdir bestandspaden echoot (een per regel);
# DELEGATE_CONTEXT levert extra packs (dubbelepunt-gescheiden paden).
ctx_files=""
if declare -F delegate_context >/dev/null; then
  while IFS= read -r _cf; do
    [ -n "$_cf" ] && ctx_files="$ctx_files:$_cf"
  done < <(delegate_context "$workdir")
fi
if [ -n "${DELEGATE_CONTEXT:-}" ]; then
  ctx_files="$ctx_files:${DELEGATE_CONTEXT}"
fi
ctx_text=""
IFS=':' read -ra _ctx <<< "$ctx_files"
for f in "${_ctx[@]}"; do
  # Windows-paden hebben een dubbelepunt na de driveletter; plak die terug.
  case "$f" in [A-Za-z]) _drive="$f"; continue;; esac
  [ -n "${_drive:-}" ] && { f="$_drive:$f"; unset _drive; }
  [ -f "$f" ] || continue
  ctx_text="${ctx_text}
===== VERPLICHTE CONTEXT: $(basename "$f") =====
$(cat "$f")
"
  echo "[delegate] context geladen: $(basename "$f")" >&2
done
if [ -n "$ctx_text" ]; then
  prompt="${ctx_text}
===== EINDE CONTEXT. Bovenstaande regels winnen van de opdracht hieronder. =====

${prompt}"
fi

MAX_POGINGEN="${MAX_POGINGEN:-${GROK_MAX_POGINGEN:-4}}"
DROP_MARKER='Server error mid-response|API Error: (Internal server error|Overloaded|Connection error)|unknown provider for model|Request timed out|upstream connect error|are cooling down'

# De bridge sluit soms mid-stream en geeft TOCH exit 0. Daarom: output live
# teeen en scannen op drop-markers, en bij een drop opnieuw tot MAX_POGINGEN.
# Retourneert de exit-code van de laatste poging, of sentinel 97 als de rail
# bleef droppen. Alleen 97 triggert een fallback: een echte taakfout herhaalt
# zich toch en propageert dus gewoon. Prompts horen idempotent te zijn.
run_met_retry() {
  local poging=1 tmp rc
  tmp="$(mktemp)"
  while :; do
    set +e
    "$@" 2>&1 | tee "$tmp"
    rc=${PIPESTATUS[0]}
    set -e
    if grep -qiE "$DROP_MARKER" "$tmp"; then
      if [ "$poging" -lt "$MAX_POGINGEN" ]; then
        echo "[delegate] stream-drop (poging $poging/$MAX_POGINGEN), retry over 3s" >&2
        poging=$((poging + 1)); sleep 3; : > "$tmp"; continue
      fi
      echo "[delegate] rail bleef droppen na $MAX_POGINGEN pogingen" >&2
      rm -f "$tmp"; return 97
    fi
    rm -f "$tmp"; return "$rc"
  done
}

# Een run via de bridge. $1 model, $2 optioneel small-fast model (providers
# zonder claude-ID-aliassen moeten de interne haiku-calls omleiden), $3
# optionele reasoning-effort. De prompt gaat via stdin (geen arg-length-limiet);
# BRIDGE_CONFIG_DIR houdt settings/history van de hoofd-seat gescheiden.
bridge_call() {
  printf %s "$prompt" | env \
    ${BRIDGE_CONFIG_DIR:+CLAUDE_CONFIG_DIR="$BRIDGE_CONFIG_DIR"} \
    ANTHROPIC_BASE_URL="$BRIDGE_URL" \
    ANTHROPIC_API_KEY="${MODEL_ROUTER_KEY:?zet MODEL_ROUTER_KEY (router-key uit cliproxy config.yaml)}" \
    ${2:+ANTHROPIC_SMALL_FAST_MODEL="$2"} \
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    claude -p --model "$1" --allowedTools "$tools" ${3:+--effort "$3"} ${DELEGATE_MCP:+ } ${DELEGATE_MCP:---strict-mcp-config}
}
run_bridge_rail() { run_met_retry bridge_call "$1" "${2:-}" "${3:-}"; }

# Auto-rail: gewogen verdeling over AUTO_SPLIT ("rail:gewicht ..."). Bresenham
# per rail spreidt de keuzes (65/35 valt om-en-om, niet in blokken) en de
# persistente teller maakt elke keuze deterministisch en toetsbaar in de log.
if [ "$rail" = auto ]; then
  rr_file="$STATE_DIR/.rail-rr"
  count=$(cat "$rr_file" 2>/dev/null || echo 0)
  case "$count" in *[!0-9]*|'') count=0;; esac
  total=0
  for pair in $AUTO_SPLIT; do total=$((total + ${pair##*:})); done
  echo "$(( (count + 1) % total ))" > "$rr_file" 2>/dev/null || true
  # Gewichten die optellen tot total geven precies een rail waarvan de
  # Bresenham-teller deze slot een stap maakt; die wint.
  chosen=""
  for pair in $AUTO_SPLIT; do
    w=${pair##*:}
    if [ $(( (count + 1) * w / total )) -gt $(( count * w / total )) ]; then
      chosen="${pair%%:*}"; break
    fi
  done
  rail="${chosen:-${AUTO_SPLIT%%:*}}"
  echo "[delegate] auto-rail -> $rail (slot $count/$total, verdeling $AUTO_SPLIT)" >&2
fi
if [ "${DELEGATE_DRY:-}" = 1 ]; then echo "$rail"; trap - EXIT 2>/dev/null; exit 0; fi

# Bridge-rails: probe de poort (goedkoop) en probeer een dode bridge best-effort
# op te brengen via BRIDGE_ENSURE. Nooit fataal; de run faalt anders vanzelf.
if [ "$rail" = grok ] || [ "$rail" = openrouter ] || [ "$rail" = codex ]; then
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$BRIDGE_PORT") 2>/dev/null; then
    if [ -n "${BRIDGE_ENSURE:-}" ]; then
      powershell -NoProfile -File "$BRIDGE_ENSURE" >&2 \
        || echo "[delegate] bridge-ensure kon de bridge niet opbrengen, poging gaat toch door" >&2
    else
      echo "[delegate] WAARSCHUWING: bridge ($BRIDGE_URL) luistert niet en BRIDGE_ENSURE is niet gezet" >&2
    fi
  fi
fi

# Elke gestarte run een jsonl-regel (rail, model, rc, duur, fallback): maakt
# verdeling en fallback-frequentie meetbaar. Geen promptinhoud in de log.
DELEGATE_LOG="${DELEGATE_LOG:-$STATE_DIR/delegate-log.jsonl}"
_START_TS=$SECONDS; _STARTED=0; _FALLBACK=0; _MODEL_USED=""
log_run() {
  local rc=$1
  [ "$_STARTED" = 1 ] || return 0
  printf '{"ts":"%s","rail":"%s","model":"%s","rc":%s,"dur_s":%s,"fallback":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rail" "$_MODEL_USED" "$rc" \
    "$((SECONDS - _START_TS))" "$_FALLBACK" >> "$DELEGATE_LOG" 2>/dev/null || true
}
trap 'log_run $?' EXIT

# Sommige modellen leveren structureel zwak frontend-werk. Waarschuwen, niet
# blokkeren: de orchestrator beslist. Conf: WARN_FRONTEND_RAILS="grok ...".
for _wr in ${WARN_FRONTEND_RAILS:-grok}; do
  if [ "$rail" = "$_wr" ] && grep -qiE '\.(vue|tsx|jsx|css|scss)\b|frontend|component[- ]|layout|styling|tailwind' "$promptfile"; then
    echo "[delegate] WAARSCHUWING: prompt lijkt frontend-werk; rail $rail levert daar zwak werk. Overweeg een Claude-seat of de hoofdsessie." >&2
  fi
done

# Optionele harde grendel: gevoelige data mag nooit stil naar een extern model.
# Zet SENSITIVE_RE in de conf (bv. "BSN|password|api[_-]?key|secret|klantgegeven")
# en EXTERNAL_RAILS (default "grok openrouter"). Matcht de prompt op zo'n rail,
# dan stopt de delegatie en wijst naar een Claude-seat. Leeg (default) = uit.
if [ -n "${SENSITIVE_RE:-}" ] && grep -qiE "$SENSITIVE_RE" "$promptfile"; then
  for _er in ${EXTERNAL_RAILS:-grok openrouter}; do
    if [ "$rail" = "$_er" ]; then
      echo "[delegate] GEBLOKKEERD: prompt matcht SENSITIVE_RE en rail '$rail' is een extern model. Draai dit op een Claude-seat (native seat of de hoofdsessie), nooit stil naar buiten." >&2
      exit 3
    fi
  done
fi

case "$rail" in
  grok)
    # Snelle start: updater/telemetry uit, geen MCP-discovery (DELEGATE_MCP=1
    # zet het aan). Fallback bij sentinel 97: openrouter, zelfde bridge en
    # zelfde sensitivity-regel. Nooit stil naar een native seat.
    _STARTED=1; _MODEL_USED="${model:-$GROK_MODEL}"
    rc=0; run_bridge_rail "${model:-$GROK_MODEL}" "" "${DELEGATE_EFFORT:-$GROK_EFFORT}" || rc=$?
    if [ "$rc" -eq 97 ]; then
      echo "[delegate] grok-rail gaf op, fallback naar openrouter ($OPENROUTER_MODEL)" >&2
      _FALLBACK=1; _MODEL_USED="$OPENROUTER_MODEL"
      rc=0; run_bridge_rail "$OPENROUTER_MODEL" || rc=$?
    fi
    exit "$rc"
    ;;
  openrouter)
    # Betaald per token; laatste goedkope rail, dus geen verdere fallback.
    _STARTED=1; _MODEL_USED="${model:-$OPENROUTER_MODEL}"
    run_bridge_rail "${model:-$OPENROUTER_MODEL}"
    ;;
  codex)
    # ChatGPT-subscription via de native CLIProxyAPI-provider (import: zie
    # scripts/codex_auth_import.py). CODEX_CHAIN loopt van groot naar klein;
    # sentinel 97 zakt een trede, daarna grok als fallback. Small-fast wordt
    # expliciet omgeleid zodat deze rail niet van de grok-aliassen afhangt.
    # Let op: gpt-5.6-sol werkt alleen met een echte API-key, niet via een
    # ChatGPT-account. Een expliciet 4e argument vervangt de keten.
    rc=0
    if [ -n "$model" ]; then
      _STARTED=1; _MODEL_USED="$model"
      run_bridge_rail "$model" "$CODEX_SMALL_FAST" "${DELEGATE_EFFORT:-$CODEX_EFFORT}" || rc=$?
    else
      for m in $CODEX_CHAIN; do
        _STARTED=1; _MODEL_USED="$m"
        rc=0; run_bridge_rail "$m" "$CODEX_SMALL_FAST" "${DELEGATE_EFFORT:-$CODEX_EFFORT}" || rc=$?
        [ "$rc" -eq 97 ] || break
        echo "[delegate] $m gaf op, keten zakt een trede" >&2
        _FALLBACK=1
      done
    fi
    if [ "$rc" -eq 97 ]; then
      echo "[delegate] codex-rail gaf op, fallback naar grok ($GROK_EFFORT)" >&2
      _FALLBACK=1; _MODEL_USED="$GROK_MODEL"
      rc=0; run_bridge_rail "$GROK_MODEL" "" "$GROK_EFFORT" || rc=$?
    fi
    exit "$rc"
    ;;
  cursor)
    # cursor-agent draait zijn eigen agent-loop: CLAUDE.md, skills en hooks
    # gelden hier NIET. Effort kent de CLI niet. Zonder native binary valt de
    # rail terug op WSL; de prompt gaat via een tempfile zodat quoting door de
    # wsl-grens geen rol speelt.
    _STARTED=1; _MODEL_USED="cursor/${model:-$CURSOR_MODEL}"
    if command -v cursor-agent >/dev/null 2>&1; then
      cursor-agent -p "$prompt" --model "${model:-$CURSOR_MODEL}" --output-format text
    else
      mkdir -p "$STATE_DIR/tmp"
      _cprompt="$STATE_DIR/tmp/cursor-prompt-$$.md"
      printf '%s' "$prompt" > "$_cprompt"
      wsl -d "$WSL_DISTRO" -e bash -lc "cursor-agent -p \"\$(cat \"\$(wslpath '$_cprompt')\")\" --model '${model:-$CURSOR_MODEL}' --output-format text"
      rc=$?; rm -f "$_cprompt"; exit "$rc"
    fi
    ;;
  *)
    # Onbekende rail-naam = native Claude-seat uit de conf:
    # SEAT_<NAAM>=<claude-config-dir>, of "-" voor de standaard seat.
    _seat_var="SEAT_$(printf '%s' "$rail" | tr '[:lower:]-' '[:upper:]_')"
    _seat_dir="${!_seat_var:-}"
    if [ -z "$_seat_dir" ]; then
      echo "onbekende rail: $rail (auto|grok|openrouter|codex|cursor|<seat uit conf>)" >&2; exit 2
    fi
    _STARTED=1; _MODEL_USED="${model:-opus}"
    if [ "$_seat_dir" = - ]; then
      printf %s "$prompt" | DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        claude -p --model "${model:-opus}" --allowedTools "$tools" ${DELEGATE_MCP:+ } ${DELEGATE_MCP:---strict-mcp-config}
    else
      printf %s "$prompt" | CLAUDE_CONFIG_DIR="$_seat_dir" \
        DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
        claude -p --model "${model:-opus}" --allowedTools "$tools" ${DELEGATE_MCP:+ } ${DELEGATE_MCP:---strict-mcp-config}
    fi
    ;;
esac
