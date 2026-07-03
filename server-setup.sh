#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

AUTO_YES=0
HOST_VALUE="0.0.0.0"
PORT_VALUE="12400"
MEMORY_CHOICE=""
SERVICE_CHOICE=""

usage() {
  cat <<'HELP'
White Salary - Linux / Server Setup Wizard

Usage:
  ./server-setup.sh
  ./server-setup.sh --with-memory
  WS_API_KEY=sk-... ./server-setup.sh --yes

What it does:
  1. Runs install.sh to create .venv and install backend dependencies.
  2. Creates conf.yaml if missing.
  3. Optionally writes your SiliconFlow API key into conf.yaml.
  4. Sets server.host / server.port for server use.
  5. Optionally installs a systemd service.

Options:
  --with-memory       Install ChromaDB extra and enable memory.long_term_provider=chroma.
  --no-memory         Keep long-term vector memory disabled.
  --host HOST         Backend listen host. Default: 0.0.0.0.
  --port PORT         Backend listen port. Default: 12400.
  --install-service   Install and start a systemd service without asking.
  --no-service        Do not install a systemd service.
  --yes               Non-interactive mode. Uses defaults and WS_API_KEY if provided.
  --help, -h          Show this help.

Notes:
  - Windows desktop users should use 安装.bat, not this script.
  - This script is for Linux servers that only run the backend.
HELP
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
      HOST_VALUE="${2:-}"
      shift 2
      ;;
    --port)
      PORT_VALUE="${2:-}"
      shift 2
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
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1"
      echo
      usage
      exit 1
      ;;
  esac
done

case "$(uname -s 2>/dev/null || printf unknown)" in
  MINGW*|MSYS*|CYGWIN*)
    echo "[ERROR] This is the Linux/server setup script."
    echo "        Windows desktop users should double-click 安装.bat instead."
    exit 1
    ;;
esac

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
echo "This script is for Linux servers that only run the backend."
echo "Windows desktop users should use 安装.bat instead."
echo

if [[ -z "$MEMORY_CHOICE" ]]; then
  MEMORY_CHOICE="$(ask_yes_no "Install ChromaDB long-term vector memory? New users can choose no." "no")"
fi

HOST_VALUE="$(ask_value "Backend listen host" "$HOST_VALUE")"
PORT_VALUE="$(ask_value "Backend listen port" "$PORT_VALUE")"

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
  INSTALL_ARGS=(--with-memory)
else
  INSTALL_ARGS=()
fi

echo
echo "[1/4] Installing backend runtime"
chmod +x install.sh
./install.sh "${INSTALL_ARGS[@]}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "[ERROR] .venv/bin/python was not created. Check the install output above."
  exit 1
fi

echo
echo "[2/4] Writing server config"
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

if not port.isdigit():
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
print(f"  wrote conf.yaml: server {host}:{port}")
if api_key:
    print("  wrote SiliconFlow key into llm and llm_vision")
else:
    print("  skipped API key; edit conf.yaml before starting real chat")
print(f"  long-term vector memory: {'chroma' if memory == 'yes' else 'none'}")
PY

echo
echo "[3/4] Checking project health"
.venv/bin/python scripts/first_run_check.py || true

SERVICE_FILE="white-salary.service"
if [[ -z "$SERVICE_CHOICE" ]]; then
  SERVICE_CHOICE="$(ask_yes_no "Create and install a systemd service now? Choose no if you are not sure." "no")"
fi

if [[ "$SERVICE_CHOICE" == "yes" ]]; then
  echo
  echo "[4/4] Installing systemd service"
  RUN_USER="${SUDO_USER:-$(id -un)}"
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=White Salary backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$(pwd)
Environment=PYTHONPATH=src
ExecStart=$(pwd)/.venv/bin/python run_server.py --host $HOST_VALUE --port $PORT_VALUE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    if [[ "$(id -u)" == "0" ]]; then
      cp "$SERVICE_FILE" /etc/systemd/system/white-salary.service
      systemctl daemon-reload
      systemctl enable --now white-salary.service
    elif command -v sudo >/dev/null 2>&1; then
      sudo cp "$SERVICE_FILE" /etc/systemd/system/white-salary.service
      sudo systemctl daemon-reload
      sudo systemctl enable --now white-salary.service
    else
      echo "  sudo not found. Service file was generated at: $SERVICE_FILE"
      echo "  Copy it to /etc/systemd/system/white-salary.service manually."
    fi
  else
    echo "  systemctl not found. Service file was generated at: $SERVICE_FILE"
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
echo "  source .venv/bin/activate"
echo "  PYTHONPATH=src python run_server.py --host $HOST_VALUE --port $PORT_VALUE"
echo
echo "Health check:"
echo "  curl http://127.0.0.1:$PORT_VALUE/health"
echo "  # from another machine: http://YOUR_SERVER_IP:$PORT_VALUE/health"
echo
echo "If you installed systemd:"
echo "  sudo systemctl status white-salary --no-pager"
echo "  sudo journalctl -u white-salary -f"
echo
echo "Need to change the API key later? Edit conf.yaml, then restart the backend."
