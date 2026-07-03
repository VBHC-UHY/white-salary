#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "============================================================"
echo "  White Salary - Linux / Server Installer"
echo "============================================================"
echo

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage:
  ./install.sh              Install backend runtime dependencies
  ./install.sh --with-memory Install backend plus ChromaDB memory extra

Notes:
  - Use Python 3.10, 3.11, or 3.12. Python 3.13 is not selected by this script yet.
  - Windows desktop users should keep using 安装.bat.
HELP
  exit 0
fi

pick_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return
  fi

  for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  done
}

PYTHON_BIN="$(pick_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[ERROR] Python 3.10/3.11/3.12 was not found."
  echo "        Install one of them, or run: PYTHON=/path/to/python3.11 ./install.sh"
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if not ((3, 10) <= sys.version_info < (3, 13)):
    raise SystemExit(
        "[ERROR] White Salary currently supports Python 3.10-3.12. "
        f"Detected: {sys.version.split()[0]}"
    )
PY

echo "[1/4] Using Python: $("$PYTHON_BIN" --version)"

echo "[2/4] Creating virtualenv: .venv"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[3/4] Installing backend dependencies"
python -m pip install --upgrade pip setuptools wheel
if [[ "${1:-}" == "--with-memory" ]]; then
  python -m pip install -e ".[memory-vector]"
else
  python -m pip install -e .
fi

echo "[4/4] Preparing local config files"
if [[ ! -f conf.yaml ]]; then
  cp conf.default.yaml conf.yaml
  echo "  created conf.yaml from conf.default.yaml"
else
  echo "  kept existing conf.yaml"
fi

if [[ ! -f prompts/system_prompt.txt && -f prompts/system_prompt.example.txt ]]; then
  cp prompts/system_prompt.example.txt prompts/system_prompt.txt
  echo "  created prompts/system_prompt.txt"
fi

cat <<'DONE'

Install complete.

Next:
  1. Edit conf.yaml or open the control panel from the desktop app later.
  2. Start the backend:
       source .venv/bin/activate
       PYTHONPATH=src python run_server.py --host 0.0.0.0 --port 12400

Optional extras:
  pip install -e ".[bilibili]"       # Bilibili live / QR login support
  pip install -e ".[memory-vector]"  # ChromaDB semantic memory
  pip install -e ".[all]"            # Broad optional set, excludes RVC due numpy conflict
DONE
