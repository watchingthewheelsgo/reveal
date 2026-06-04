#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENV_FILE="${ENV_FILE:-.env}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-reveal}"
INIT_ONLY=false
PULL_LATEST=false
NO_BUILD=false
SHOW_LOGS=false

usage() {
  cat <<'EOF'
Usage: scripts/deploy-self-host.sh [options]

Options:
  --init-only   Create .env and data directory, then exit.
  --pull        Run git pull --ff-only before deploying.
  --no-build    Start existing image without rebuilding.
  --logs        Tail service logs after deploy.
  --help        Show this help.

Examples:
  scripts/deploy-self-host.sh
  scripts/deploy-self-host.sh --pull
  scripts/deploy-self-host.sh --init-only
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-only)
      INIT_ONLY=true
      ;;
    --pull)
      PULL_LATEST=true
      ;;
    --no-build)
      NO_BUILD=true
      ;;
    --logs)
      SHOW_LOGS=true
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

env_value() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi
  awk -F= -v key="$key" '$1 == key { value = substr($0, index($0, "=") + 1) } END { print value }' \
    "$ENV_FILE" | tr -d '"' | tr -d "'"
}

init_files() {
  mkdir -p data
  if [[ ! -f "$ENV_FILE" ]]; then
    cp .env.example "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example."
    echo "Edit $ENV_FILE, then rerun: scripts/deploy-self-host.sh"
    echo
    echo "Minimum useful config:"
    echo "  FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_ADMIN_CHAT_ID"
    echo "  or TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID"
    echo "  DEEPSEEK_API_KEY"
    echo "  FINNHUB_API_KEY"
    echo "  DATABASE_URL if you want hosted Postgres instead of local SQLite"
    if [[ "$INIT_ONLY" == true ]]; then
      exit 0
    fi
    exit 2
  fi
}

validate_env() {
  local warnings=0
  local feishu_app_id
  local feishu_app_secret
  local feishu_chat
  local telegram_token
  local telegram_chat
  local deepseek_key
  local finnhub_key

  feishu_app_id="$(env_value FEISHU_APP_ID)"
  feishu_app_secret="$(env_value FEISHU_APP_SECRET)"
  feishu_chat="$(env_value FEISHU_ADMIN_CHAT_ID)"
  telegram_token="$(env_value TELEGRAM_BOT_TOKEN)"
  telegram_chat="$(env_value TELEGRAM_ADMIN_CHAT_ID)"
  deepseek_key="$(env_value DEEPSEEK_API_KEY)"
  finnhub_key="$(env_value FINNHUB_API_KEY)"

  if [[ -z "$feishu_app_id" || -z "$feishu_app_secret" || -z "$feishu_chat" ]]; then
    if [[ -z "$telegram_token" || -z "$telegram_chat" ]]; then
      echo "Warning: no complete Feishu or Telegram bot admin config found." >&2
      warnings=$((warnings + 1))
    fi
  fi
  if [[ -z "$deepseek_key" && -z "$(env_value ANTHROPIC_AUTH_TOKEN)" ]]; then
    echo "Warning: DEEPSEEK_API_KEY/ANTHROPIC_AUTH_TOKEN is empty; research agent will not work." >&2
    warnings=$((warnings + 1))
  fi
  if [[ -z "$finnhub_key" ]]; then
    echo "Warning: FINNHUB_API_KEY is empty; quote/news quality will be limited." >&2
    warnings=$((warnings + 1))
  fi

  if [[ "$warnings" -gt 0 ]]; then
    echo "Continuing anyway. You can update $ENV_FILE and rerun this script."
  fi
}

wait_for_health() {
  local host_port
  host_port="$(env_value REVEAL_HOST_PORT)"
  host_port="${host_port:-10000}"

  echo "Waiting for health check on http://127.0.0.1:${host_port}/health ..."
  for _ in $(seq 1 60); do
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS "http://127.0.0.1:${host_port}/health" >/dev/null 2>&1; then
        echo "Reveal is healthy: http://127.0.0.1:${host_port}"
        return 0
      fi
    else
      local health
      health="$(docker inspect --format='{{.State.Health.Status}}' reveal 2>/dev/null || true)"
      if [[ "$health" == "healthy" ]]; then
        echo "Reveal container is healthy."
        return 0
      fi
    fi
    sleep 2
  done

  echo "Reveal did not become healthy in time. Recent logs:" >&2
  compose logs --tail=120 reveal >&2
  exit 1
}

require_command docker
docker compose version >/dev/null

init_files
if [[ "$INIT_ONLY" == true ]]; then
  echo "Initialization complete."
  exit 0
fi

validate_env

if [[ "$PULL_LATEST" == true ]]; then
  require_command git
  git pull --ff-only
fi

if [[ "$NO_BUILD" == true ]]; then
  compose up -d
else
  compose up -d --build
fi

wait_for_health

echo
echo "Useful commands:"
echo "  docker compose -p $PROJECT_NAME -f $COMPOSE_FILE logs -f reveal"
echo "  docker compose -p $PROJECT_NAME -f $COMPOSE_FILE ps"
echo "  docker compose -p $PROJECT_NAME -f $COMPOSE_FILE down"

if [[ "$SHOW_LOGS" == true ]]; then
  compose logs -f reveal
fi
