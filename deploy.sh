#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/opt/paymentTelebot"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

BACKEND_SERVICE="paymenttelebot-backend"
LISTENER_SERVICE="paymenttelebot-listener"
FRONTEND_SERVICE="paymenttelebot-frontend"

BACKEND_HEALTH_URL="http://127.0.0.1:8002/health"
FRONTEND_URL="http://127.0.0.1:3002/login"

log() {
  printf '\n== %s ==\n' "$1"
}

fail() {
  echo
  echo "DEPLOY FAILED: $1" >&2
  exit 1
}

trap 'fail "Command failed on line $LINENO"' ERR

log "Payment Telebot Deploy"

cd "$PROJECT_DIR"

log "Checking Git state"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Local tracked changes detected:"
  git status --short
  fail "Commit or discard local changes before deploying"
fi

CURRENT_BRANCH="$(git branch --show-current)"

if [[ -z "$CURRENT_BRANCH" ]]; then
  fail "Unable to determine current Git branch"
fi

echo "Branch: $CURRENT_BRANCH"

log "Pulling latest code"

git fetch origin "$CURRENT_BRANCH"
git pull --ff-only origin "$CURRENT_BRANCH"

log "Updating backend"

cd "$BACKEND_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  fail "Backend virtual environment not found at $BACKEND_DIR/.venv"
fi

.venv/bin/python -m pip install .

log "Running database migrations"

.venv/bin/alembic upgrade head

log "Updating frontend"

cd "$FRONTEND_DIR"

if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

npm run build

log "Restarting services"

systemctl restart "$BACKEND_SERVICE"
systemctl restart "$LISTENER_SERVICE"
systemctl restart "$FRONTEND_SERVICE"

log "Waiting for services"

sleep 3

log "Checking service status"

systemctl is-active --quiet "$BACKEND_SERVICE" \
  || fail "$BACKEND_SERVICE is not active"

systemctl is-active --quiet "$LISTENER_SERVICE" \
  || fail "$LISTENER_SERVICE is not active"

systemctl is-active --quiet "$FRONTEND_SERVICE" \
  || fail "$FRONTEND_SERVICE is not active"

log "Checking backend health"

curl --fail --silent --show-error "$BACKEND_HEALTH_URL"
echo

log "Checking frontend"

curl --fail --silent --show-error --output /dev/null "$FRONTEND_URL"

log "Deployment completed successfully"

echo
echo "Frontend: https://payment.youplatform.org"
echo "Health:   https://payment.youplatform.org/api/health"
