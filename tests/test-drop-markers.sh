#!/usr/bin/env bash
# Borgt de DROP_MARKER-regex uit delegate/delegate.sh (run_met_retry).
# Een regressie in die regex mag niet stil passeren: mid-stream drops met
# exit 0 worden alleen herkend als deze markers matchen.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DELEGATE="$REPO/delegate/delegate.sh"

# Regex extraheren uit de bron, zodat de test tegen de echte waarde draait.
DROP_MARKER="$(grep -m1 '^DROP_MARKER=' "$DELEGATE" | sed "s/^DROP_MARKER='//; s/'\$//")"
if [ -z "$DROP_MARKER" ]; then
  echo "FAIL: geen DROP_MARKER gevonden in $DELEGATE"
  exit 1
fi

fails=0

check_match() {
  # $1 = verwacht gedrag (match|nomatch), $2 = testregel
  local expect="$1" regel="$2" label
  if echo "$regel" | grep -qiE "$DROP_MARKER"; then
    label="match"
  else
    label="nomatch"
  fi
  if [ "$label" = "$expect" ]; then
    echo "PASS [$expect] $regel"
  else
    echo "FAIL [kreeg $label, verwacht $expect] $regel"
    fails=$((fails + 1))
  fi
}

# Echte foutregels: moeten matchen.
check_match match "API Error: Server error mid-response"
check_match match "API Error: Internal server error"
check_match match "API Error: Overloaded"
check_match match "API Error: Connection error"
check_match match "unknown provider for model grok"
check_match match "Request timed out"
check_match match "upstream connect error or disconnect"
check_match match "All credentials for model gpt-5.6-terra are cooling down via provider codex"

# Onschuldige regels: mogen niet matchen.
check_match nomatch "de server error rate is laag"
check_match nomatch "klaar met de taak, alles groen"
check_match nomatch "connection established"

exit "$fails"
