#!/usr/bin/env bash
# aidriven helper: numbered menu, or direct use: ./run.sh <number|name> (e.g. ./run.sh 4, ./run.sh run)
# Works on Windows (Git Bash) and Linux/macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv"
PORT="${AIDRIVEN_PORT:-8731}"
DATA_DIR="${AIDRIVEN_DATA_DIR:-data}"

if [[ -x "$VENV/Scripts/python.exe" ]]; then
  PY="$VENV/Scripts/python.exe"
else
  PY="$VENV/bin/python"
fi

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

need_venv() { [[ -x "$PY" ]] || fail "No virtual environment. Run option 1 (create environment) first."; }

system_python() {
  if command -v py >/dev/null 2>&1; then
    for v in 3.14 3.13 3.12; do
      if py -"$v" -c "import sys" >/dev/null 2>&1; then echo "py -$v"; return; fi
    done
  fi
  for c in python3.14 python3.13 python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(sys.version_info < (3, 12))" 2>/dev/null; then
      echo "$c"; return
    fi
  done
  fail "Python >= 3.12 not found."
}

# ------------------------------------------------------------------------------------------ actions

create_env() {
  local sys_py; sys_py="$(system_python)"
  if [[ -d "$VENV" ]]; then
    warn "Virtual environment already exists at .venv (use option 2 to update dependencies)."
  else
    info "Creating .venv with: $sys_py"
    $sys_py -m venv "$VENV"
    if [[ -x "$VENV/Scripts/python.exe" ]]; then PY="$VENV/Scripts/python.exe"; else PY="$VENV/bin/python"; fi
  fi
  install_deps
  [[ -f .env ]] || { cp .env.example .env; info "Created .env from .env.example"; }
  info "Environment ready. Next: option 4 (run app)."
}

install_deps() {
  need_venv
  info "Installing/updating dependencies (editable + dev)"
  "$PY" -m pip install --upgrade pip -q
  "$PY" -m pip install -e ".[dev]" -q
  info "Dependencies installed."
}

install_browser() {
  need_venv
  info "Installing Playwright + Chromium for browser checks without Docker (process sandbox)"
  "$PY" -m pip install -e ".[dev,html]" -q
  "$PY" -m playwright install chromium
}

clean_ssl_env() {
  # A conda `base` env (conda init in ~/.bash_profile) may export SSL_CERT_FILE pointing to a missing
  # cacert.pem: Python SDKs then fail with FileNotFoundError. Drop such variables (the app does it too).
  local var
  for var in SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE; do
    if [[ -n "${!var:-}" && ! -e "${!var}" ]]; then
      warn "Ignoring $var=${!var} (file does not exist)"
      unset "$var"
    fi
  done
}

free_port() {
  # Stop any previous aidriven instance (normal or dev reloader) before starting a new one.
  if port_busy || app_processes_running; then
    warn "aidriven is already running on port $PORT: stopping it first"
    stop_app
  fi
  port_busy && fail "Port $PORT is used by another program. Free it or set AIDRIVEN_PORT."
  return 0
}

run_app() {
  need_venv
  clean_ssl_env
  free_port
  info "Starting aidriven on http://127.0.0.1:$PORT  (Ctrl+C to stop)"
  AIDRIVEN_RELOAD=false "$PY" -m aidriven
}

run_dev() {
  need_venv
  clean_ssl_env
  free_port
  info "Starting aidriven in dev mode (auto-reload on code changes) on http://127.0.0.1:$PORT"
  # NiceGUI only auto-reloads a script launched by path (not `python -m`)
  AIDRIVEN_RELOAD=true "$PY" "$ROOT/src/aidriven/__main__.py"
}

open_browser() {
  local url="http://127.0.0.1:$PORT"
  if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$url" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1
  elif command -v open >/dev/null 2>&1; then open "$url"
  else echo "$url"; fi
}

port_busy() {
  if command -v powershell.exe >/dev/null 2>&1; then
    # Locale-independent (netstat prints "ESCUCHANDO" on Spanish Windows)
    local n
    n="$(powershell.exe -NoProfile -Command "@(Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue).Count" | tr -d $'\r')"
    [[ "${n:-0}" -gt 0 ]]
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti tcp:"$PORT" >/dev/null 2>&1
  else
    return 1
  fi
}

app_processes_running() {
  if command -v powershell.exe >/dev/null 2>&1; then
    local n
    n="$(powershell.exe -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
      Where-Object { \$_.CommandLine -match '-m aidriven|aidriven[\\/]__main__\.py' }).Count" | tr -d $'\r')"
    [[ "${n:-0}" -gt 0 ]]
  else
    pgrep -f "python.* (-m aidriven|.*aidriven/__main__\.py)" >/dev/null 2>&1
  fi
}

stop_app() {
  info "Stopping aidriven (port $PORT)"
  if command -v powershell.exe >/dev/null 2>&1; then
    # Kill the listener AND the dev-mode reloader parent (otherwise it respawns the server).
    powershell.exe -NoProfile -Command "
      \$ids = @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
        Where-Object { \$_.CommandLine -match '-m aidriven|aidriven[\\/]__main__\.py|multiprocessing' -and \$_.CommandLine -match 'aidriven|testaidriven' } | Select-Object -ExpandProperty ProcessId)
      \$ids += @(Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess)
      \$ids | Sort-Object -Unique | ForEach-Object { taskkill /F /T /PID \$_ 2>\$null | Out-Null; \"Killed PID \$_\" }
    " | tr -d '\r' | sed 's/^/  /'
  elif command -v lsof >/dev/null 2>&1; then
    pkill -f "python.* (-m aidriven|.*aidriven/__main__\.py)" 2>/dev/null || true
    local pids; pids="$(lsof -ti tcp:"$PORT" || true)"
    [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
  else
    fail "Cannot find powershell or lsof to stop the app."
  fi
  for _ in $(seq 1 10); do port_busy || { info "Port $PORT is free."; return; }; sleep 1; done
  warn "Port $PORT is still in use by another program."
}

tests_unit() {
  need_venv
  info "Unit tests (no Docker / Firestore required)"
  "$PY" -m pytest tests/unit "$@"
}

tests_all() {
  need_venv
  info "All tests including Docker integration (skipped automatically if Docker is down)"
  "$PY" -m pytest "$@"
}

tests_fast() {
  need_venv
  info "Fast unit tests (skips the ~70 s seed-reference validation)"
  "$PY" -m pytest tests/unit --deselect tests/unit/test_seed_reference.py "$@"
}

quality() {
  need_venv
  info "ruff format + ruff check --fix + mypy"
  "$PY" -m ruff format src tests tools
  "$PY" -m ruff check src tests tools --fix
  "$PY" -m mypy src
  info "Code quality OK."
}

coverage() {
  need_venv
  "$PY" -m pytest --cov=aidriven --cov-report=term-missing --cov-report=html
  info "HTML report: htmlcov/index.html"
}

docker_status() {
  if ! command -v docker >/dev/null 2>&1; then warn "Docker CLI not installed: the process sandbox will be used."; return; fi
  if docker info >/dev/null 2>&1; then
    info "Docker daemon is running."
    docker image ls --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E "python:3.12-slim|aidriven-browser|playwright/python" || warn "No sandbox images yet (option 11)."
  else
    warn "Docker is installed but the daemon is not running: start Docker Desktop for full isolation."
  fi
}

build_images() {
  need_venv
  docker info >/dev/null 2>&1 || fail "Docker daemon is not running."
  info "Pulling/building sandbox images (python + headless browser). First time can take several minutes."
  "$PY" - <<'PY'
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
from aidriven.adapters.sandbox import DockerSandbox
from aidriven.config import SandboxConfig
cfg = SandboxConfig()
sb = DockerSandbox(cfg)
for image in (cfg.python_image, cfg.playwright_image):
    sb._ensure_image(image)
    print(f"ready: {image}")
PY
}

backup_db() {
  local db="$DATA_DIR/aidriven.db"
  [[ -f "$db" ]] || fail "No local database at $db"
  mkdir -p "$DATA_DIR/backups"
  local dest; dest="$DATA_DIR/backups/aidriven-$(date +%Y%m%d-%H%M%S).db"
  cp "$db" "$dest"
  info "Backup written to $dest"
}

reset_data() {
  warn "This deletes $DATA_DIR/ (local SQLite, artifacts, uploaded credentials, logs, config)."
  warn "Firestore data and API keys in the OS keyring are NOT touched."
  read -r -p "Type 'reset' to confirm: " answer
  [[ "$answer" == "reset" ]] || { info "Cancelled."; return; }
  [[ -f "$DATA_DIR/aidriven.db" ]] && backup_db
  find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name backups -exec rm -rf {} +
  info "Local data reset (backups kept). The demo data is recreated on next start."
}

seed_assets() {
  need_venv
  info "Regenerating seed assets (deterministic, byte-for-byte)"
  "$PY" tools/gen_seed_assets.py
}

show_logs() {
  local log="$DATA_DIR/logs/aidriven.log"
  [[ -f "$log" ]] || fail "No log file yet at $log"
  tail -n 200 -f "$log"
}

show_info() {
  echo "  Project : $ROOT"
  echo "  Python  : $( [[ -x "$PY" ]] && "$PY" --version || echo 'no .venv yet')"
  echo "  URL     : http://127.0.0.1:$PORT"
  echo "  Data    : $DATA_DIR/ (SQLite, artifacts, logs, config.json, secrets/)"
  echo "  Specs   : specs/ (start with specs/00-overview/vision.md)"
  docker_status
}

# ------------------------------------------------------------------------------------------ menu

MENU=(
  "create-env|Create environment (.venv + dependencies + .env)|create_env"
  "install|Install / update dependencies|install_deps"
  "browser|Install Playwright + Chromium (browser checks without Docker)|install_browser"
  "run|Run app|run_app"
  "dev|Run app in dev mode (auto-reload)|run_dev"
  "open|Open the app in the browser|open_browser"
  "stop|Stop the app (free port $PORT)|stop_app"
  "test|Run unit tests|tests_unit"
  "test-fast|Run fast unit tests (skip seed validation)|tests_fast"
  "test-all|Run all tests (incl. Docker integration)|tests_all"
  "images|Build / pull Docker sandbox images|build_images"
  "docker|Docker status|docker_status"
  "quality|Format + lint + type-check|quality"
  "coverage|Tests with coverage report|coverage"
  "backup|Back up local database|backup_db"
  "reset|Reset local data (asks for confirmation)|reset_data"
  "assets|Regenerate seed assets|seed_assets"
  "logs|Follow the application log|show_logs"
  "info|Show environment info|show_info"
)

print_menu() {
  echo
  echo "  aidriven — LLM test bench"
  echo "  ─────────────────────────"
  local i=1
  for entry in "${MENU[@]}"; do
    IFS='|' read -r name label _ <<<"$entry"
    printf '  %2d) %-48s [%s]\n' "$i" "$label" "$name"
    i=$((i + 1))
  done
  printf '   0) Exit\n\n'
}

dispatch() {
  local choice="$1"; shift || true
  local i=1
  for entry in "${MENU[@]}"; do
    IFS='|' read -r name _ fn <<<"$entry"
    if [[ "$choice" == "$i" || "$choice" == "$name" ]]; then
      "$fn" "$@"
      return 0
    fi
    i=$((i + 1))
  done
  return 1
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help|help) print_menu; echo "  Usage: ./run.sh [number|name] [extra args, e.g. pytest flags]"; exit 0 ;;
  esac
  dispatch "$@" || { print_menu; fail "Unknown option: $1"; }
  exit 0
fi

while true; do
  print_menu
  read -r -p "  Choose an option: " choice
  [[ "$choice" == "0" || "$choice" == "q" || -z "$choice" ]] && exit 0
  # Run each option in a fresh copy of the script, so an open menu always uses the latest version of run.sh.
  bash "$ROOT/run.sh" "$choice" || true
  read -r -p "  Press Enter to return to the menu..." _
done
