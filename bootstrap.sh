#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$root"
if [[ $# -gt 1 ]]; then
    echo 'Usage: bash bootstrap.sh [python-executable]' >&2
    exit 1
fi
python="${1:-python3}"
venv_python="$root/.venv/bin/python"
if [[ -d "$root/.venv/Scripts" ]]; then
    venv_python="$root/.venv/Scripts/python.exe"
fi
if [[ -e "$root/.venv" ]]; then
    if [[ ! -x "$venv_python" ]]; then
        echo 'Existing .venv is invalid. Repair it or choose a fresh checkout.' >&2
        exit 1
    fi
else
    "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
    "$python" -m venv .venv
    if [[ -d "$root/.venv/Scripts" ]]; then
        venv_python="$root/.venv/Scripts/python.exe"
    fi
fi
"$venv_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
"$venv_python" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)'
"$venv_python" -m pip install -e '.[dev]'
"$venv_python" -m pip check
"$venv_python" -m ruff check .
"$venv_python" -m ruff format --check .
"$venv_python" -m pytest -q
"$venv_python" -m keyword_searcher --help
echo 'Bootstrap completed. Activate with: source .venv/bin/activate'
echo 'Run keyword-searcher with --queries, --run-dir and an external --output-dir.'
