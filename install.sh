#!/bin/sh
# protean-lcm one-command install.
#
#   sh install.sh
#
# Installs the plugin into the interpreter Hermes runs in, enables it for the
# active Hermes home, and selects the LCM context engine. Every step can be
# overridden through the environment:
#
#   SRC     install source          (default: the git URL for the main branch)
#   PYTHON  interpreter to install into
#   PLUGIN  plugin name to enable   (default: protean-lcm)
#   ENGINE  context engine to select (default: lcm)
#
# Nothing is written outside the Python environment, the Hermes config, and
# the Hermes plugin-data directory.

set -eu

PLUGIN=${PLUGIN:-protean-lcm}
ENGINE=${ENGINE:-lcm}
REF=${REF:-main}
SRC=${SRC:-git+https://github.com/aska-digital/protean-lcm.git@$REF}

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v hermes >/dev/null 2>&1 || die "the 'hermes' command is not on PATH"

# Hermes normally runs from its own virtualenv. Installing anywhere else leaves
# the plugin invisible to it, so find that interpreter first.
if [ -z "${PYTHON:-}" ]; then
  for candidate in \
    "$HOME/.hermes/hermes-agent/venv/bin/python" \
    "$HOME/.hermes/hermes-agent/.venv/bin/python"
  do
    if [ -x "$candidate" ]; then PYTHON=$candidate; break; fi
  done
fi
if [ -z "${PYTHON:-}" ]; then
  PYTHON=$(command -v python3 || true)
fi
[ -n "${PYTHON:-}" ] || die "no Python interpreter found. Set PYTHON to the one Hermes runs in."

say "Python:   $PYTHON"
say "Installing $SRC"
"$PYTHON" -m pip install --upgrade "$SRC" || die "pip install failed"
"$PYTHON" -m pip show "$PLUGIN" >/dev/null 2>&1 \
  || die "the package installed but '$PLUGIN' is not visible to $PYTHON"

say "Enabling the plugin"
hermes plugins enable "$PLUGIN" || die "could not enable $PLUGIN"

say "Selecting the context engine"
hermes config set context.engine "$ENGINE" || die "could not set context.engine=$ENGINE"

say ""
say "Done. Start a new Hermes session to run on the $ENGINE context engine."
say "Undo with: hermes config set context.engine compressor && pip uninstall $PLUGIN"
