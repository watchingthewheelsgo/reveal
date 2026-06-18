#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONTAINER_NAME="${CONTAINER_NAME:-reveal}"
IMAGE_NAME="${IMAGE_NAME:-reveal:latest}"
ENV_FILE="${ENV_FILE:-.env}"
DATA_DIR="${DATA_DIR:-data}"
APP_PORT="${APP_PORT:-10000}"
HOST_PORT="${HOST_PORT:-8000}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${HOST_PORT}/health}"
PULL_MODE="${PULL_MODE:---ff-only}"
LONGBRIDGE_TOKEN_FILE="${LONGBRIDGE_TOKEN_FILE:-reveal-oauth.json}"
LONGBRIDGE_HOST_DIR="${LONGBRIDGE_HOST_DIR:-/opt/reveal/secrets/longbridge}"
LONGBRIDGE_CONTAINER_DIR="${LONGBRIDGE_CONTAINER_DIR:-/app/secrets/longbridge}"
LONGBRIDGE_OAUTH_TOKEN_PATH="$LONGBRIDGE_CONTAINER_DIR/$LONGBRIDGE_TOKEN_FILE"
LONGBRIDGE_API_BASE="${LONGBRIDGE_API_BASE:-https://openapi.longbridge.cn}"

log() {
  printf '\n==> %s\n' "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

need_cmd git
need_cmd docker

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Missing env file: %s\n' "$ENV_FILE" >&2
  printf 'Create it first or run with ENV_FILE=/path/to/.env\n' >&2
  exit 1
fi

log "Pull latest code"
git pull "$PULL_MODE"

log "Build Docker image: ${IMAGE_NAME}"
if [[ "${NO_CACHE:-}" == "1" ]]; then
  docker build --no-cache -t "$IMAGE_NAME" .
else
  docker build -t "$IMAGE_NAME" .
fi

log "Remove old container if it exists: ${CONTAINER_NAME}"
if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER_NAME"
else
  printf 'No existing container named %s\n' "$CONTAINER_NAME"
fi

mkdir -p "$DATA_DIR"

LONGBRIDGE_DOCKER_ARGS=()
if [[ -f "${LONGBRIDGE_HOST_DIR}/${LONGBRIDGE_TOKEN_FILE}" ]]; then
  chmod 700 "$LONGBRIDGE_HOST_DIR" || true
  chmod 600 "${LONGBRIDGE_HOST_DIR}/${LONGBRIDGE_TOKEN_FILE}" || true
  LONGBRIDGE_DOCKER_ARGS=(
    -e "LONGBRIDGE_ENABLED=true"
    -e "LONGBRIDGE_API_BASE=${LONGBRIDGE_API_BASE}"
    -e "LONGBRIDGE_OAUTH_TOKEN_PATH=${LONGBRIDGE_OAUTH_TOKEN_PATH}"
    -v "${LONGBRIDGE_HOST_DIR}:${LONGBRIDGE_CONTAINER_DIR}"
  )
  log "Longbridge token will be mounted: ${LONGBRIDGE_HOST_DIR}/${LONGBRIDGE_TOKEN_FILE} -> ${LONGBRIDGE_OAUTH_TOKEN_PATH}"
else
  log "Longbridge token not found; skipping token mount"
  printf 'Expected token file: %s\n' "${LONGBRIDGE_HOST_DIR}/${LONGBRIDGE_TOKEN_FILE}"
fi

log "Run new container"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -e PORT="$APP_PORT" \
  -p "${HOST_PORT}:${APP_PORT}" \
  -v "${ROOT_DIR}/${DATA_DIR}:/app/data" \
  "${LONGBRIDGE_DOCKER_ARGS[@]}" \
  "$IMAGE_NAME"

log "Container status"
docker ps --filter "name=^/${CONTAINER_NAME}$"

if command -v curl >/dev/null 2>&1; then
  log "Health check: ${HEALTH_URL}"
  for _ in {1..20}; do
    if curl -fsS "$HEALTH_URL" >/dev/null; then
      printf 'Health check passed.\n'
      exit 0
    fi
    sleep 1
  done
  printf 'Health check failed. Recent logs:\n' >&2
else
  log "curl not found; printing recent logs"
fi

docker logs --tail 120 "$CONTAINER_NAME" >&2
exit 1
