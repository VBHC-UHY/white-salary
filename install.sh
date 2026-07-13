#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

WITH_MEMORY=0
CHECK_ONLY=0
RECREATE_VENV=0
INSTALL_SYSTEM_DEPS=0
PYTHON_OVERRIDE="${PYTHON:-}"

usage() {
  cat <<'HELP'
White Salary - Linux / Server Installer

Usage:
  ./install.sh
  ./install.sh --with-memory
  ./install.sh --check
  ./install.sh --python /path/to/python3.11

Options:
  --with-memory       Install the ChromaDB long-term-memory extra.
  --check             Check prerequisites without changing any files.
  --python PATH       Use one specific Python executable.
  --recreate-venv     Recreate the project .venv even when it is valid.
  --install-system-deps
                      Install a missing Debian/Ubuntu pythonX.Y-venv package.
  --help, -h          Show this help.

Python resolution order:
  1. --python PATH or the PYTHON environment variable
  2. python3.12, python3.11, python3.10 on PATH
  3. uv-managed Python 3.12, 3.11, or 3.10
  4. compatible python3 or python on PATH

The installer only writes inside this project. Python dependencies are installed
into .venv and never into the system interpreter.
HELP
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-memory)
      WITH_MEMORY=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --python)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "[ERROR] --python requires an executable path."
        exit 2
      fi
      PYTHON_OVERRIDE="$2"
      shift 2
      ;;
    --recreate-venv)
      RECREATE_VENV=1
      shift
      ;;
    --install-system-deps)
      INSTALL_SYSTEM_DEPS=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1"
      echo
      usage
      exit 2
      ;;
  esac
done

case "$(uname -s 2>/dev/null || printf unknown)" in
  MINGW*|MSYS*|CYGWIN*)
    echo "[ERROR] install.sh is for Linux/macOS server installations."
    echo "        Windows desktop users should double-click 安装.bat."
    exit 1
    ;;
esac

echo "============================================================"
echo "  White Salary - Linux / Server Installer"
echo "============================================================"
echo

is_supported_python() {
  local executable="$1"
  [[ -n "$executable" ]] || return 1
  PYTHONDONTWRITEBYTECODE=1 "$executable" \
    -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info < (3, 13) else 1)' \
    >/dev/null 2>&1
}

python_version() {
  PYTHONDONTWRITEBYTECODE=1 "$1" -c 'import platform; print(platform.python_version())'
}

python_minor_version() {
  PYTHONDONTWRITEBYTECODE=1 "$1" \
    -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

python_has_venv_module() {
  PYTHONDONTWRITEBYTECODE=1 "$1" -m venv --help >/dev/null 2>&1
}

python_can_seed_venv() {
  python_has_venv_module "$1" \
    && PYTHONDONTWRITEBYTECODE=1 "$1" -m ensurepip --version >/dev/null 2>&1
}

venv_is_valid() {
  local executable="$1"
  [[ -x "$executable" ]] \
    && is_supported_python "$executable" \
    && python_has_venv_module "$executable" \
    && PYTHONDONTWRITEBYTECODE=1 "$executable" -m pip --version >/dev/null 2>&1
}

uv_can_seed_venv() {
  command -v uv >/dev/null 2>&1 \
    && uv venv --help >/dev/null 2>&1
}

run_as_root() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "[ERROR] Root access is required to install the missing OS package."
    echo "        Install it manually, then run this installer again."
    return 1
  fi
}

install_venv_support() {
  local version package
  version="$(python_minor_version "$SELECTED_PYTHON")"
  package="python${version}-venv"

  if command -v apt-get >/dev/null 2>&1; then
    echo "[INFO] Installing missing OS package: $package"
    if ! run_as_root apt-get update; then
      echo "[ERROR] apt-get update failed. No OS package was installed."
      return 1
    fi
    if ! run_as_root apt-get install -y "$package"; then
      echo "[ERROR] Could not install $package."
      return 1
    fi
    return 0
  fi

  echo "[ERROR] Automatic venv package installation currently supports apt-based systems."
  echo "        Install the venv/ensurepip package for Python $version, then retry."
  return 1
}

SELECTED_PYTHON=""
SELECTED_SOURCE=""
SELECTED_CREATOR=""
FALLBACK_PYTHON=""
FALLBACK_SOURCE=""

consider_python() {
  local executable="$1"
  local source="$2"

  if ! is_supported_python "$executable"; then
    return 1
  fi

  if python_can_seed_venv "$executable"; then
    SELECTED_PYTHON="$executable"
    SELECTED_SOURCE="$source"
    SELECTED_CREATOR="native"
    return 0
  fi

  if [[ -z "$FALLBACK_PYTHON" ]]; then
    FALLBACK_PYTHON="$executable"
    FALLBACK_SOURCE="$source"
  fi
  return 1
}

resolve_python() {
  local candidate version found

  if [[ -n "$PYTHON_OVERRIDE" ]]; then
    if ! is_supported_python "$PYTHON_OVERRIDE"; then
      echo "[ERROR] The configured Python is missing or unsupported: $PYTHON_OVERRIDE" >&2
      return 1
    fi
    SELECTED_PYTHON="$PYTHON_OVERRIDE"
    SELECTED_SOURCE="explicit override"
    if python_can_seed_venv "$SELECTED_PYTHON"; then
      SELECTED_CREATOR="native"
    elif uv_can_seed_venv; then
      SELECTED_CREATOR="uv"
    else
      SELECTED_CREATOR="missing"
    fi
    return 0
  fi

  for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if consider_python "$(command -v "$candidate")" "$candidate on PATH"; then
        return 0
      fi
    fi
  done

  if command -v uv >/dev/null 2>&1; then
    for version in 3.12 3.11 3.10; do
      found=""
      if found="$(uv python find --no-python-downloads --no-cache "$version" 2>/dev/null)" \
        && [[ -n "$found" ]]; then
        found="${found%%$'\n'*}"
        if consider_python "$found" "uv-managed Python $version"; then
          return 0
        fi
      fi
    done
  fi

  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if consider_python "$(command -v "$candidate")" "$candidate on PATH"; then
        return 0
      fi
    fi
  done

  if [[ -n "$FALLBACK_PYTHON" ]]; then
    SELECTED_PYTHON="$FALLBACK_PYTHON"
    SELECTED_SOURCE="$FALLBACK_SOURCE"
    if uv_can_seed_venv; then
      SELECTED_CREATOR="uv"
    else
      SELECTED_CREATOR="missing"
    fi
    return 0
  fi

  return 1
}

ensure_selected_can_create_venv() {
  local version

  case "$SELECTED_CREATOR" in
    native|uv)
      return 0
      ;;
  esac

  version="$(python_minor_version "$SELECTED_PYTHON")"
  if [[ "$CHECK_ONLY" == "1" ]]; then
    echo "[ERROR] Python $version cannot create a virtualenv with pip."
    echo "        Its venv/ensurepip support is missing or unusable."
    echo "        Debian/Ubuntu: sudo apt install python${version}-venv"
    echo "        Or install uv, then run this check again."
    return 1
  fi

  if [[ "$INSTALL_SYSTEM_DEPS" != "1" ]]; then
    echo "[ERROR] Python $version cannot create a virtualenv with pip."
    echo "        Debian/Ubuntu: sudo apt install python${version}-venv"
    echo "        Or rerun: ./install.sh --install-system-deps"
    return 1
  fi

  if ! install_venv_support; then
    return 1
  fi
  if python_can_seed_venv "$SELECTED_PYTHON"; then
    SELECTED_CREATOR="native"
    return 0
  fi
  if uv_can_seed_venv; then
    SELECTED_CREATOR="uv"
    return 0
  fi

  echo "[ERROR] Python still cannot create a virtualenv after installing prerequisites."
  return 1
}

venv_has_safe_identity() {
  local parent_path project_path first_entry

  [[ "$VENV_DIR" == "$PROJECT_ROOT/.venv" ]] || return 1
  [[ "$(basename -- "$VENV_DIR")" == ".venv" ]] || return 1
  [[ ! -L "$VENV_DIR" ]] || return 1
  [[ -d "$VENV_DIR" ]] || return 1

  parent_path="$(cd -- "$(dirname -- "$VENV_DIR")" && pwd -P)" || return 1
  project_path="$(cd -- "$PROJECT_ROOT" && pwd -P)" || return 1
  [[ "$parent_path" == "$project_path" ]] || return 1

  if [[ ! -f "$VENV_DIR/pyvenv.cfg" ]]; then
    first_entry="$(find "$VENV_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)"
    [[ -z "$first_entry" ]]
    return
  fi

  grep -Eq '^home[[:space:]]*=' "$VENV_DIR/pyvenv.cfg" \
    && grep -Eq '^(version|version_info)[[:space:]]*=' "$VENV_DIR/pyvenv.cfg"
}

repair_sudo_ownership() {
  local owner owner_uid owner_group

  if [[ "$(id -u)" != "0" ]]; then
    return 0
  fi
  owner="${SUDO_USER:-}"
  if [[ -z "$owner" ]]; then
    echo "[WARN] The installer was run directly as root; .venv and conf.yaml remain root-owned."
    echo "       Run server services as a dedicated non-root account."
    return 0
  fi
  if ! owner_uid="$(id -u "$owner" 2>/dev/null)" || [[ "$owner_uid" == "0" ]]; then
    echo "[ERROR] SUDO_USER does not identify a valid non-root account: $owner"
    return 1
  fi
  if ! owner_group="$(id -gn "$owner" 2>/dev/null)"; then
    echo "[ERROR] Could not determine the primary group for $owner."
    return 1
  fi

  if ! chown -R "$owner:$owner_group" "$VENV_DIR"; then
    echo "[ERROR] Could not restore .venv ownership to $owner."
    return 1
  fi
  if [[ -e conf.yaml ]] && ! chown "$owner:$owner_group" conf.yaml; then
    echo "[ERROR] Could not restore conf.yaml ownership to $owner."
    return 1
  fi
}

VENV_DIR="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_VALID=0
if venv_is_valid "$VENV_PYTHON"; then
  VENV_VALID=1
fi

NEEDS_VENV=0
if [[ "$VENV_VALID" != "1" || "$RECREATE_VENV" == "1" ]]; then
  NEEDS_VENV=1
fi

if [[ -n "$PYTHON_OVERRIDE" || "$NEEDS_VENV" == "1" ]]; then
  if ! resolve_python; then
    echo "[ERROR] No compatible Python was found. White Salary requires Python 3.10-3.12."
    echo "        Install Python 3.11/3.12, install uv, or pass --python /path/to/python."
    exit 1
  fi
fi

if [[ "$NEEDS_VENV" == "1" ]] && ! ensure_selected_can_create_venv; then
  exit 1
fi

if [[ -n "$PYTHON_OVERRIDE" && "$NEEDS_VENV" != "1" && "$CHECK_ONLY" == "1" ]]; then
  if ! python_has_venv_module "$SELECTED_PYTHON"; then
    echo "[ERROR] The configured Python has no working venv module: $PYTHON_OVERRIDE"
    exit 1
  fi
fi

for required in pyproject.toml run_server.py conf.default.yaml; do
  if [[ ! -f "$required" ]]; then
    echo "[ERROR] Missing project file: $required"
    exit 1
  fi
done

if [[ "$CHECK_ONLY" == "1" ]]; then
  if [[ -n "$PYTHON_OVERRIDE" ]]; then
    echo "[OK] Explicit Python $(python_version "$SELECTED_PYTHON") is supported."
  fi
  if [[ "$VENV_VALID" == "1" && "$RECREATE_VENV" != "1" ]]; then
    echo "[OK] Existing project .venv uses Python $(python_version "$VENV_PYTHON")."
  else
    echo "[OK] Compatible Python $(python_version "$SELECTED_PYTHON") found ($SELECTED_SOURCE)."
    if [[ -d "$VENV_DIR" ]]; then
      echo "[INFO] Existing .venv is invalid and would be recreated."
    else
      echo "[INFO] A new project .venv would be created."
    fi
  fi
  if command -v systemctl >/dev/null 2>&1; then
    echo "[OK] systemctl is available (service installation remains optional)."
  else
    echo "[INFO] systemctl is unavailable; foreground/server-container use is still supported."
  fi
  echo "[CHECK] Done. No install actions were executed."
  exit 0
fi

if [[ "$NEEDS_VENV" == "1" ]]; then
  if [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]]; then
    if ! venv_has_safe_identity; then
      echo "[ERROR] Refusing to remove .venv because its identity could not be verified:"
      echo "        $VENV_DIR"
      echo "        Move or remove that path manually after confirming its contents."
      exit 1
    fi
    if ! rm -rf -- "$VENV_DIR"; then
      echo "[ERROR] Could not remove the old project .venv."
      exit 1
    fi
  fi
  echo "[1/4] Creating project virtualenv with Python $(python_version "$SELECTED_PYTHON") ($SELECTED_SOURCE)"
  if [[ "$SELECTED_CREATOR" == "native" ]]; then
    if ! "$SELECTED_PYTHON" -m venv "$VENV_DIR"; then
      echo "[ERROR] Could not create .venv with $SELECTED_PYTHON."
      exit 1
    fi
  elif [[ "$SELECTED_CREATOR" == "uv" ]]; then
    if ! uv venv --seed --python "$SELECTED_PYTHON" "$VENV_DIR"; then
      echo "[ERROR] uv could not create .venv with $SELECTED_PYTHON."
      exit 1
    fi
  else
    echo "[ERROR] No virtualenv creator is available."
    exit 1
  fi
else
  echo "[1/4] Reusing project .venv (Python $(python_version "$VENV_PYTHON"))"
fi

if ! venv_is_valid "$VENV_PYTHON"; then
  echo "[ERROR] .venv was created without a working Python and pip."
  exit 1
fi

echo "[2/4] Installing backend dependencies into .venv"
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$WITH_MEMORY" == "1" ]]; then
  "$VENV_PYTHON" -m pip install -e ".[memory-vector]"
else
  "$VENV_PYTHON" -m pip install -e .
fi

echo "[3/4] Verifying required imports"
"$VENV_PYTHON" -c 'import aiofiles, aiohttp, fastapi, httpx, loguru, multipart, openai, pydantic, uvicorn, yaml'

echo "[4/4] Preparing local configuration"
if [[ ! -f conf.yaml ]]; then
  cp conf.default.yaml conf.yaml
  if ! chmod 600 conf.yaml; then
    echo "[ERROR] Could not restrict conf.yaml permissions."
    exit 1
  fi
  echo "  created conf.yaml from conf.default.yaml"
else
  echo "  kept existing conf.yaml"
fi

if [[ ! -f prompts/system_prompt.txt && -f prompts/system_prompt.example.txt ]]; then
  cp prompts/system_prompt.example.txt prompts/system_prompt.txt
  echo "  created prompts/system_prompt.txt"
fi

if ! repair_sudo_ownership; then
  exit 1
fi

cat <<'DONE'

Install complete.

Next:
  1. Server beginners can run ./server-setup.sh for guided configuration.
  2. Or edit conf.yaml and fill at least llm.api_key.
  3. Start the backend:
       PYTHONPATH=src .venv/bin/python run_server.py --host 0.0.0.0 --port 12400

Optional extras:
  .venv/bin/python -m pip install -e ".[bilibili]"
  .venv/bin/python -m pip install -e ".[memory-vector]"
  .venv/bin/python -m pip install -e ".[all]"
DONE
