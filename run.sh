#!/usr/bin/env bash
# Mac/Linux: `bash run.sh` installs what's needed (first time) and starts the bot.
set -e
cd "$(dirname "$0")"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Python 3.11 or newer is needed. Get it from https://www.python.org/downloads/ and try again."
  exit 1
fi
# (Re)create the environment if it is missing or broken (e.g. a first install that stopped halfway).
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
  python3 -m venv .venv || { rm -rf .venv; echo "Could not create a Python environment."; exit 1; }
fi
# Fast no-op when everything is installed; repairs a half-finished first install.
.venv/bin/python -m pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
if [ -x /usr/bin/caffeinate ]; then   # Mac: stay awake while the bot runs
  exec /usr/bin/caffeinate -i .venv/bin/python -m tracker run
fi
exec .venv/bin/python -m tracker run
