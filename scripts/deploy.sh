#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/rpg-rules-search}
REPO_URL=${REPO_URL:-}
BRANCH=${BRANCH:-main}
SERVICE_NAME=${SERVICE_NAME:-rpg-rules-search}
USER_NAME=${USER_NAME:-$(id -un)}

if [ -z "$REPO_URL" ]; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    REPO_URL="$(git rev-parse --show-toplevel)"
    echo "Usando o checkout local em $REPO_URL" >&2
  else
    echo "Defina REPO_URL com a URL real do repositório GitHub, por exemplo:" >&2
    echo "REPO_URL=https://github.com/<usuario>/<repositorio>.git bash scripts/deploy.sh" >&2
    echo "Ou rode o script a partir de um clone local do repositório." >&2
    exit 1
  fi
fi

sudo apt-get update
sudo apt-get install -y git python3-pip python3-venv

sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR"
  git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL" 2>/dev/null || true
  git fetch origin "$BRANCH" 2>/dev/null || true
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH" 2>/dev/null || true
fi

cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e '.[dev]'

sudo install -m 0644 "$APP_DIR/deploy/rpg-rules-search.service" "/etc/systemd/system/$SERVICE_NAME.service"
sudo sed -i "s|YOUR_USERNAME|$USER_NAME|g" "/etc/systemd/system/$SERVICE_NAME.service"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME" || true
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager || true
