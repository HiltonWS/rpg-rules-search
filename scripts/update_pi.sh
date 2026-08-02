#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/rpg-rules-search}
BRANCH=${BRANCH:-main}
SERVICE_NAME=${SERVICE_NAME:-rpg-rules-search}
USER_NAME=${USER_NAME:-}

if [ "$(id -u)" -ne 0 ]; then
  echo "O atualizador precisa ser executado como root." >&2
  exit 1
fi

if [ -z "$USER_NAME" ]; then
  USER_NAME=$(stat -c '%U' "$APP_DIR")
fi

if [ ! -d "$APP_DIR/.git" ] || ! id "$USER_NAME" >/dev/null 2>&1; then
  echo "Instalação inválida em $APP_DIR para o usuário $USER_NAME." >&2
  exit 1
fi

USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
run_as_user() {
  runuser -u "$USER_NAME" -- env HOME="$USER_HOME" "$@"
}

exec 9>"/run/lock/rpg-rules-search-update.lock"
flock -n 9 || {
  echo "Outra atualização do Arquivo Arcano já está em execução."
  exit 0
}

if [ -n "$(run_as_user git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]; then
  echo "A atualização foi cancelada porque há alterações locais versionadas em $APP_DIR." >&2
  exit 1
fi

run_as_user git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
LOCAL_COMMIT=$(run_as_user git -C "$APP_DIR" rev-parse HEAD)
REMOTE_COMMIT=$(run_as_user git -C "$APP_DIR" rev-parse "origin/$BRANCH")

if [ "$LOCAL_COMMIT" = "$REMOTE_COMMIT" ]; then
  echo "Arquivo Arcano já está atualizado em $LOCAL_COMMIT."
else
  if ! run_as_user git -C "$APP_DIR" merge-base --is-ancestor "$LOCAL_COMMIT" "$REMOTE_COMMIT"; then
    echo "A branch local divergiu de origin/$BRANCH; atualização automática cancelada." >&2
    exit 1
  fi

  run_as_user git -C "$APP_DIR" merge --ff-only "$REMOTE_COMMIT"
  run_as_user uv pip install --python "$APP_DIR/.venv/bin/python" -e "$APP_DIR"
fi

sed \
  -e "s|APP_DIRECTORY|$APP_DIR|g" \
  -e "s|YOUR_USERNAME|$USER_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search.service" \
  >"/etc/systemd/system/$SERVICE_NAME.service"

sed \
  -e "s|APP_DIRECTORY|$APP_DIR|g" \
  -e "s|YOUR_USERNAME|$USER_NAME|g" \
  -e "s|SERVICE_NAME_PLACEHOLDER|$SERVICE_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search-update.service" \
  >"/etc/systemd/system/$SERVICE_NAME-update.service"

sed \
  -e "s|SERVICE_NAME_PLACEHOLDER|$SERVICE_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search-update.timer" \
  >"/etc/systemd/system/$SERVICE_NAME-update.timer"

mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/arquivo-arcano.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Restart=always
RestartSec=5
EOF

systemctl daemon-reload
systemctl enable --now ollama.service
systemctl restart ollama.service
if ! curl --retry 10 --retry-all-errors --retry-delay 1 --fail --silent \
  http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "O Ollama não respondeu em http://127.0.0.1:11434." >&2
  systemctl status ollama.service --no-pager >&2 || true
  exit 1
fi
systemctl enable --now "$SERVICE_NAME-update.timer"
systemctl restart "$SERVICE_NAME.service"

echo "Arquivo Arcano atualizado de $LOCAL_COMMIT para $REMOTE_COMMIT."
