#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

AUTO_YES=0
CHECK_ONLY=0
HOST_VALUE="0.0.0.0"
PORT_VALUE="12400"
MEMORY_CHOICE=""
SERVICE_CHOICE=""
PYTHON_OVERRIDE=""
RECREATE_VENV=0
INSTALL_SYSTEM_DEPS=1

usage() {
  cat <<'HELP'
White Salary - Linux / Server Setup Wizard

Usage:
  ./server-setup.sh
  ./server-setup.sh --check
  WS_API_KEY=sk-... ./server-setup.sh --yes --install-service

What it does:
  1. Creates an isolated project .venv and installs backend dependencies.
  2. Creates or updates conf.yaml for server use.
  3. Optionally enables ChromaDB long-term vector memory.
  4. Optionally installs and verifies a systemd service.

Options:
  --with-memory       Install ChromaDB and set memory.long_term_provider=chroma.
  --no-memory         Keep long-term vector memory disabled.
  --host HOST         Backend listen host. Default: 0.0.0.0.
  --port PORT         Backend listen port. Default: 12400.
  --python PATH       Use one specific Python 3.10-3.12 executable.
  --recreate-venv     Recreate the project .venv.
  --install-system-deps
                      Install a missing Debian/Ubuntu pythonX.Y-venv package.
  --no-system-deps    Never install OS packages automatically.
  --install-service   Install and start a systemd service without asking.
  --no-service        Do not install a systemd service.
  --yes, -y           Non-interactive mode. Uses defaults and WS_API_KEY.
  --check             Check prerequisites without changing any files.
  --help, -h          Show this help.

Notes:
  - Windows desktop users should double-click the Windows installer instead.
  - The server setup installs only the backend. Electron desktop features and
    Windows-only local tools are not started on Linux.
  - Binding to 0.0.0.0 exposes the backend to the network. Configure a firewall,
    reverse proxy, and authentication before exposing it to the internet.
HELP
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "[ERROR] $option requires a value."
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-memory)
      MEMORY_CHOICE="yes"
      shift
      ;;
    --no-memory)
      MEMORY_CHOICE="no"
      shift
      ;;
    --host)
      require_value "$1" "${2:-}"
      HOST_VALUE="$2"
      shift 2
      ;;
    --port)
      require_value "$1" "${2:-}"
      PORT_VALUE="$2"
      shift 2
      ;;
    --python)
      require_value "$1" "${2:-}"
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
    --no-system-deps)
      INSTALL_SYSTEM_DEPS=0
      shift
      ;;
    --install-service)
      SERVICE_CHOICE="yes"
      shift
      ;;
    --no-service)
      SERVICE_CHOICE="no"
      shift
      ;;
    --yes|-y)
      AUTO_YES=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
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
    echo "[ERROR] server-setup.sh is for Linux/macOS server installations."
    echo "        Windows desktop users should use the Windows installer."
    exit 1
    ;;
esac

if [[ -z "$HOST_VALUE" || "$HOST_VALUE" =~ [[:space:]] ]]; then
  echo "[ERROR] Invalid host value: $HOST_VALUE"
  exit 2
fi
if [[ ! "$PORT_VALUE" =~ ^[0-9]+$ ]] || ((PORT_VALUE < 1 || PORT_VALUE > 65535)); then
  echo "[ERROR] Port must be an integer from 1 to 65535: $PORT_VALUE"
  exit 2
fi

INSTALL_ARGS=()
if [[ -n "$PYTHON_OVERRIDE" ]]; then
  INSTALL_ARGS+=(--python "$PYTHON_OVERRIDE")
fi
if [[ "$RECREATE_VENV" == "1" ]]; then
  INSTALL_ARGS+=(--recreate-venv)
fi
if [[ "$INSTALL_SYSTEM_DEPS" == "1" ]]; then
  INSTALL_ARGS+=(--install-system-deps)
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "============================================================"
  echo "  White Salary - Server Readiness Check"
  echo "============================================================"
  echo
  CHECK_ARGS=()
  if [[ -n "$PYTHON_OVERRIDE" ]]; then
    CHECK_ARGS+=(--python "$PYTHON_OVERRIDE")
  fi
  if [[ "$RECREATE_VENV" == "1" ]]; then
    CHECK_ARGS+=(--recreate-venv)
  fi
  if ! ./install.sh --check "${CHECK_ARGS[@]}"; then
    echo "[ERROR] Installer prerequisite check failed."
    exit 1
  fi
  if [[ -f conf.yaml ]]; then
    if [[ -x .venv/bin/python ]]; then
      PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path

import yaml

path = Path("conf.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
if not isinstance(data, dict):
    raise SystemExit("[ERROR] conf.yaml must contain a YAML mapping.")
print("[OK] conf.yaml is valid YAML.")
PY
    else
      echo "[INFO] conf.yaml exists; YAML validation will run after .venv is installed."
    fi
  else
    echo "[INFO] conf.yaml does not exist yet; setup would create it."
  fi
  exit 0
fi

ask_yes_no() {
  local prompt="$1"
  local default="$2"
  local suffix answer

  if [[ "$default" == "yes" ]]; then
    suffix="Y/n"
  else
    suffix="y/N"
  fi

  if [[ "$AUTO_YES" == "1" || ! -t 0 ]]; then
    printf '%s\n' "$default"
    return
  fi

  while true; do
    read -r -p "$prompt [$suffix] " answer || answer=""
    answer="${answer:-$default}"
    case "${answer,,}" in
      y|yes) printf 'yes\n'; return ;;
      n|no) printf 'no\n'; return ;;
      *) echo "Please answer y or n." >&2 ;;
    esac
  done
}

ask_value() {
  local prompt="$1"
  local default="$2"
  local answer

  if [[ "$AUTO_YES" == "1" || ! -t 0 ]]; then
    printf '%s\n' "$default"
    return
  fi

  read -r -p "$prompt [$default] " answer || answer=""
  printf '%s\n' "${answer:-$default}"
}

echo "============================================================"
echo "  White Salary - Linux / Server Setup Wizard"
echo "============================================================"
echo
echo "This setup installs the backend into the project .venv."
echo "It does not change the system Python environment."
echo

if [[ -z "$MEMORY_CHOICE" ]]; then
  MEMORY_CHOICE="$(ask_yes_no "Install ChromaDB long-term vector memory? New users can choose no." "no")"
fi

HOST_VALUE="$(ask_value "Backend listen host" "$HOST_VALUE")"
PORT_VALUE="$(ask_value "Backend listen port" "$PORT_VALUE")"
if [[ -z "$HOST_VALUE" || "$HOST_VALUE" =~ [[:space:]] ]]; then
  echo "[ERROR] Invalid host value: $HOST_VALUE"
  exit 2
fi
if [[ ! "$PORT_VALUE" =~ ^[0-9]+$ ]] || ((PORT_VALUE < 1 || PORT_VALUE > 65535)); then
  echo "[ERROR] Port must be an integer from 1 to 65535: $PORT_VALUE"
  exit 2
fi

API_KEY="${WS_API_KEY:-}"
if [[ -z "$API_KEY" && "$AUTO_YES" != "1" && -t 0 ]]; then
  echo
  echo "Paste a SiliconFlow API key if you have one."
  echo "Leave it empty to configure conf.yaml manually later."
  read -r -s -p "SiliconFlow API key (starts with sk-): " API_KEY || API_KEY=""
  echo
fi
API_KEY="${API_KEY#"${API_KEY%%[![:space:]]*}"}"
API_KEY="${API_KEY%"${API_KEY##*[![:space:]]}"}"

if [[ "$MEMORY_CHOICE" == "yes" ]]; then
  INSTALL_ARGS+=(--with-memory)
fi

echo
echo "[1/4] Installing backend runtime"
if ! chmod +x install.sh; then
  echo "[ERROR] Could not make install.sh executable."
  exit 1
fi
if ! ./install.sh "${INSTALL_ARGS[@]}"; then
  echo "[ERROR] Backend runtime installation failed."
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "[ERROR] .venv/bin/python was not created. Check the install output above."
  exit 1
fi

echo
echo "[2/4] Writing server configuration"
WS_SETUP_API_KEY="$API_KEY" \
WS_SETUP_HOST="$HOST_VALUE" \
WS_SETUP_PORT="$PORT_VALUE" \
WS_SETUP_MEMORY="$MEMORY_CHOICE" \
.venv/bin/python - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

conf_path = Path("conf.yaml")
if not conf_path.exists():
    default_path = Path("conf.default.yaml")
    if not default_path.exists():
        raise SystemExit("[ERROR] conf.default.yaml not found.")
    conf_path.write_text(default_path.read_text(encoding="utf-8"), encoding="utf-8")


def quote_yaml(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_yaml_scalar(
    text: str,
    section: str,
    key: str,
    value: str,
    *,
    quote: bool = True,
) -> str:
    lines = text.splitlines()
    rendered = quote_yaml(value) if quote else value

    section_idx = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped == f"{section}:" or (
            stripped.startswith(f"{section}:")
            and (
                stripped[len(section) + 1 :].strip() == ""
                or stripped[len(section) + 1 :].lstrip().startswith("#")
            )
        ):
            if not line[:1].isspace():
                section_idx = i
                break

    if section_idx == -1:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{section}:")
        lines.append(f"  {key}: {rendered}")
        return "\n".join(lines) + "\n"

    end_idx = len(lines)
    for i in range(section_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line[:1].isspace():
            end_idx = i
            break

    for i in range(section_idx + 1, end_idx):
        stripped = lines[i].lstrip()
        if stripped.startswith(f"{key}:"):
            indent = lines[i][: len(lines[i]) - len(stripped)]
            lines[i] = f"{indent}{key}: {rendered}"
            return "\n".join(lines) + "\n"

    lines.insert(section_idx + 1, f"  {key}: {rendered}")
    return "\n".join(lines) + "\n"


api_key = os.environ.get("WS_SETUP_API_KEY", "").strip()
host = os.environ.get("WS_SETUP_HOST", "0.0.0.0").strip() or "0.0.0.0"
port = os.environ.get("WS_SETUP_PORT", "12400").strip() or "12400"
memory = os.environ.get("WS_SETUP_MEMORY", "no").strip().lower()

if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit(f"[ERROR] Invalid port: {port}")

text = conf_path.read_text(encoding="utf-8")
text = set_yaml_scalar(text, "server", "host", host)
text = set_yaml_scalar(text, "server", "port", port, quote=False)
text = set_yaml_scalar(
    text,
    "memory",
    "long_term_provider",
    "chroma" if memory == "yes" else "none",
)

if api_key:
    text = set_yaml_scalar(text, "llm", "provider", "siliconflow")
    text = set_yaml_scalar(text, "llm", "api_key", api_key)
    text = set_yaml_scalar(text, "llm", "model", "deepseek-ai/DeepSeek-V3.2")
    text = set_yaml_scalar(text, "llm", "base_url", "https://api.siliconflow.cn/v1")
    text = set_yaml_scalar(text, "llm_vision", "api_key", api_key)

conf_path.write_text(text, encoding="utf-8")
print(f"  configured server at {host}:{port}")
if api_key:
    print("  configured SiliconFlow for text and vision")
else:
    print("  API key left unchanged; edit conf.yaml before using cloud chat")
print(f"  long-term vector memory: {'chroma' if memory == 'yes' else 'none'}")
PY
if ! chmod 600 conf.yaml; then
  echo "[ERROR] Could not restrict conf.yaml permissions."
  exit 1
fi

repair_sudo_ownership() {
  local owner owner_uid owner_group

  if [[ "$(id -u)" != "0" ]]; then
    return 0
  fi
  owner="${SUDO_USER:-}"
  if [[ -z "$owner" ]]; then
    echo "[WARN] Setup is running directly as root; project files remain root-owned."
    echo "       A system service will not be created without a non-root service account."
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

  if ! chown -R "$owner:$owner_group" .venv; then
    echo "[ERROR] Could not restore .venv ownership to $owner."
    return 1
  fi
  if ! chown "$owner:$owner_group" conf.yaml; then
    echo "[ERROR] Could not restore conf.yaml ownership to $owner."
    return 1
  fi
}

if ! repair_sudo_ownership; then
  exit 1
fi

echo
echo "[3/4] Checking project health"
if .venv/bin/python scripts/first_run_check.py; then
  :
else
  check_result=$?
  echo "[ERROR] Project health check reported a blocking problem."
  exit "$check_result"
fi

if [[ -z "$SERVICE_CHOICE" ]]; then
  SERVICE_CHOICE="$(ask_yes_no "Create and install a systemd service now? Choose no if you are not sure." "no")"
fi

systemd_path() {
  local value="$1"
  case "$value" in
    *$'\n'*|*$'\r'*) return 1 ;;
  esac
  [[ "$value" == /* ]] || return 1
  value="${value//\\/\\x5c}"
  value="${value// /\\x20}"
  value="${value//$'\t'/\\x09}"
  value="${value//\%/%%}"
  printf '%s' "$value"
}

systemd_quote() {
  local value="$1"
  case "$value" in
    *$'\n'*|*$'\r'*) return 1 ;;
  esac
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\%/%%}"
  printf '"%s"' "$value"
}

write_service_file() {
  local run_user="$1"
  local root_escaped python_quoted server_quoted host_quoted port_quoted

  if ! root_escaped="$(systemd_path "$PROJECT_ROOT")" \
    || ! python_quoted="$(systemd_quote "$PROJECT_ROOT/.venv/bin/python")" \
    || ! server_quoted="$(systemd_quote "$PROJECT_ROOT/run_server.py")" \
    || ! host_quoted="$(systemd_quote "$HOST_VALUE")" \
    || ! port_quoted="$(systemd_quote "$PORT_VALUE")"; then
    echo "[ERROR] Paths and service arguments must not contain newlines."
    return 1
  fi

  if ! cat > white-salary.service <<EOF
[Unit]
Description=White Salary backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$run_user
WorkingDirectory=$root_escaped
Environment="PYTHONPATH=src"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$python_quoted $server_quoted --host $host_quoted --port $port_quoted
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  then
    echo "[ERROR] Could not write white-salary.service."
    return 1
  fi
}

determine_service_user() {
  local current_uid candidate candidate_uid

  current_uid="$(id -u)"
  if [[ "$current_uid" == "0" ]]; then
    candidate="${SUDO_USER:-}"
    if [[ -z "$candidate" ]]; then
      echo "[ERROR] Refusing to create a systemd service that runs as root."
      echo "        Run setup from a regular account (sudo is used only for systemd steps)."
      return 1
    fi
  else
    candidate="$(id -un)"
  fi

  if ! candidate_uid="$(id -u "$candidate" 2>/dev/null)"; then
    echo "[ERROR] Service account does not exist: $candidate"
    return 1
  fi
  if [[ "$candidate_uid" == "0" ]]; then
    echo "[ERROR] Refusing to create a systemd service that runs as root."
    return 1
  fi

  RUN_USER="$candidate"
}

verify_service_file() {
  if ! command -v systemd-analyze >/dev/null 2>&1; then
    echo "  systemd-analyze is unavailable; skipping local unit verification."
    return 0
  fi
  if ! systemd-analyze verify "$PROJECT_ROOT/white-salary.service"; then
    echo "[ERROR] systemd rejected the generated service file."
    return 1
  fi
}

install_systemd_service() {
  local prefix=()
  if [[ ! -d /run/systemd/system ]]; then
    echo "  systemd is not running in this environment."
    echo "  Generated: $PROJECT_ROOT/white-salary.service"
    return 2
  fi
  if [[ "$(id -u)" != "0" ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "  sudo is unavailable. Install the generated service file manually."
      return 2
    fi
    prefix=(sudo)
  fi

  if ! "${prefix[@]}" install -m 0644 white-salary.service /etc/systemd/system/white-salary.service; then
    echo "[ERROR] Could not install white-salary.service into /etc/systemd/system."
    return 1
  fi
  if ! "${prefix[@]}" systemctl daemon-reload; then
    echo "[ERROR] systemctl daemon-reload failed."
    return 1
  fi
  if ! "${prefix[@]}" systemctl enable --now white-salary.service; then
    echo "[ERROR] Could not enable and start white-salary.service."
    return 1
  fi
  if ! "${prefix[@]}" systemctl --no-pager --full status white-salary.service; then
    echo "[ERROR] The systemd service did not start successfully."
    echo "        Inspect logs with: sudo journalctl -u white-salary -n 100 --no-pager"
    return 1
  fi
  return 0
}

SERVICE_INSTALLED=0
if [[ "$SERVICE_CHOICE" == "yes" ]]; then
  echo
  echo "[4/4] Installing systemd service"
  RUN_USER=""
  if ! determine_service_user; then
    exit 1
  fi
  if ! write_service_file "$RUN_USER"; then
    exit 1
  fi
  if ! verify_service_file; then
    exit 1
  fi
  if install_systemd_service; then
    SERVICE_INSTALLED=1
  else
    install_result=$?
    if [[ "$install_result" == "1" ]]; then
      exit 1
    fi
  fi
else
  echo
  echo "[4/4] systemd service skipped"
fi

echo
echo "============================================================"
echo "  Server setup complete"
echo "============================================================"
echo
echo "Start in the foreground:"
echo "  PYTHONPATH=src .venv/bin/python run_server.py --host $HOST_VALUE --port $PORT_VALUE"
echo
echo "Health check:"
echo "  curl http://127.0.0.1:$PORT_VALUE/health"
echo "  # from another machine: http://YOUR_SERVER_IP:$PORT_VALUE/health"
echo
if [[ "$SERVICE_INSTALLED" == "1" ]]; then
  echo "systemd commands:"
  echo "  sudo systemctl status white-salary --no-pager"
  echo "  sudo journalctl -u white-salary -f"
  echo
elif [[ "$SERVICE_CHOICE" == "yes" ]]; then
  echo "A service file was generated but not installed automatically:"
  echo "  $PROJECT_ROOT/white-salary.service"
  echo
fi
echo "Need to change the API key later? Edit conf.yaml, then restart the backend."
