#!/usr/bin/env bash
# Explicit model selection; task lifecycle and budgets live in run_task.py.
set -euo pipefail
DELEGATE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DELEGATE_CONF="${DELEGATE_CONF:-$HOME/.delegate.conf}"
if [ -f "$DELEGATE_CONF" ]; then
  set -a
  source "$DELEGATE_CONF"
  set +a
fi
if [ "$#" -lt 3 ]; then
  echo 'gebruik: delegate.sh <rail> "<allowedTools>" <promptfile> [model]' >&2
  exit 2
fi
context_args=()
if declare -F delegate_context >/dev/null; then
  context_paths="$(delegate_context "${DELEGATE_CWD:-$PWD}")"
  while IFS= read -r context_path; do
    [ -z "$context_path" ] || context_args+=(--context "$context_path")
  done <<< "$context_paths"
fi
# One path per line, including Windows drive letters and spaces.
while IFS= read -r context_path; do
  [ -z "$context_path" ] || context_args+=(--context "$context_path")
done <<< "${DELEGATE_CONTEXT:-}"
exec "${DELEGATE_PYTHON:-python}" "$DELEGATE_HOME/run_task.py" "$@" "${context_args[@]}"
