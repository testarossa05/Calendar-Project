#!/usr/bin/env bash
#
# One-time setup on a Mac.
#
#   ./scripts/install-macos.sh
#
# Creates a virtualenv, installs dependencies including the macOS-only EventKit
# bindings, copies the example configuration, and runs the diagnostics.
# Safe to re-run: nothing here overwrites an existing config.yaml.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m!  %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this script is for macOS. On Linux, install with pip directly."

say "1/5  Checking Python"
PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    major="${version%%.*}"; minor="${version##*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then PYTHON="$candidate"; break; fi
  fi
done
[ -n "$PYTHON" ] || die "Python 3.11+ not found. Install it with:  brew install python@3.12"
echo "   using $PYTHON ($("$PYTHON" --version))"

say "2/5  Creating the virtualenv"
if [ -d .venv ]; then
  echo "   .venv already exists, reusing it"
else
  "$PYTHON" -m venv .venv
  echo "   created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

say "3/5  Installing calhub and its dependencies"
# Editable install puts the `calhub` command on PATH inside the venv, so no
# PYTHONPATH juggling is needed for any later command.
python -m pip install --quiet -e .
echo "   done (including pyobjc-framework-EventKit for the local calendar route)"

say "4/5  Preparing the configuration"
if [ -f config.yaml ]; then
  echo "   config.yaml already exists, leaving it untouched"
else
  cp config.example.yaml config.yaml
  echo "   copied config.example.yaml -> config.yaml"
  warn "Edit config.yaml before the first sync. It is git-ignored."
fi
mkdir -p secrets && chmod 700 secrets

say "5/5  Diagnostics"
# The first EventKit read triggers the macOS permission prompt; --no-probe keeps
# this step from blocking on it before the user has edited the config.
calhub doctor --no-probe || true

cat <<EOF

Next steps
  1. Edit config.yaml and enable the sources you want.
  2. Verify everything is reachable:
       cd "$ROOT" && source .venv/bin/activate
       calhub doctor
     macOS will ask for calendar access on the first EventKit read. Approve it.
  3. Preview the merge without writing anything:
       calhub agenda -d 7
  4. Schedule it:
       ./scripts/install-launchd.sh

  Full step-by-step verification order: docs/VERIFY-ko.md
EOF
