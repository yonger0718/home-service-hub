#!/bin/sh
# Operator gate evidence (design 5.5 / 16 C7, ruling 16). The checks live in worker.parser.host_evidence;
# this is only the wrapper the deploy notes reference. It prints the JSON report and exits 0 only when
# every check passed (which also clears the parser latch).
# usage: deploy/statements/gate.sh   (env: STATEMENT_* as for the worker; STATEMENT_PARSER_SANDBOX must be true)
set -eu
if [ -f "$HOME/.config/homehub-statements.env" ]; then
  set -a
  . "$HOME/.config/homehub-statements.env"
  set +a
fi
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m worker gate
