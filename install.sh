#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
VIBE_HOME="${VIBE_HOME:-$HOME/.vibe}"
PY="$REPO/server/.venv/bin/python"                 # the Python written into config.toml and hooks.toml
RUN_PY="${TN_PYTHON:-$PY}"                         # the Python that runs the installer (the tests use their own)
if [ -z "${TN_SKIP_SYNC:-}" ]; then (cd "$REPO/server" && uv sync --quiet); fi
cfg() { PYTHONPATH="$REPO/server" "$RUN_PY" -m traceable.install_config "$@"; }
mkdir -p "$VIBE_HOME"
cfg config "$VIBE_HOME/config.toml" "$PY"
mkdir -p "$VIBE_HOME/skills"; rm -rf "$VIBE_HOME/skills/traceable-numbers"
cp -R "$REPO/skill/traceable-numbers" "$VIBE_HOME/skills/"
for d in "$REPO/demo/finance" "$REPO/demo/logistics" "$REPO/demo/filings"; do
  cfg hooks "$d/.vibe/hooks.toml" "$PY" "$REPO/hooks/check_numbers.py"
done
echo "Installed. Start Vibe with the wrapper: cd demo/finance && ../../receipts"
echo "  receipts runs vibe --legacy-harness and, when Vibe exits, prints the check's verdict or a loud NOT CHECKED."
echo "  Vibe 2.25.7 can switch to its unified harness by a remote rollout; that harness does not pass the transcript"
echo "  to hooks, so the check cannot run and the report says NOT CHECKED."
echo "Trust the demo folders once: cd demo/finance && ../../receipts (answer 'trust'), same for demo/logistics and demo/filings."
echo "On a demo machine, also set enable_auto_update = false in $VIBE_HOME/config.toml to keep the tested Vibe version."
