#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/rpg-rules-search}
REPO_URL=${REPO_URL:-https://github.com/HiltonWS/rpg-rules-search.git}
BRANCH=${BRANCH:-main}
SERVICE_NAME=${SERVICE_NAME:-rpg-rules-search}
USER_NAME=${USER_NAME:-${SUDO_USER:-}}

if [ -z "$USER_NAME" ] || [ "$USER_NAME" = "root" ]; then
  CURRENT_USER=$(id -un)
  if [ "$CURRENT_USER" != "root" ]; then
    USER_NAME=$CURRENT_USER
  else
    USER_NAME=$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 { print $1; exit }')
  fi
fi

if [ -z "$USER_NAME" ] || [ "$USER_NAME" = "root" ] || ! id "$USER_NAME" >/dev/null 2>&1; then
  echo "Não foi possível identificar automaticamente o usuário do Raspberry Pi." >&2
  echo "Execute novamente com USER_NAME=seu_usuario." >&2
  exit 1
fi

USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || {
    echo "O comando sudo é necessário para instalar o serviço." >&2
    exit 1
  }
  SUDO=(sudo)
fi

run_as_user() {
  if [ "$(id -un)" = "$USER_NAME" ]; then
    "$@"
  else
    "${SUDO[@]}" -u "$USER_NAME" env HOME="$USER_HOME" "$@"
  fi
}

echo "Instalando o Arquivo Arcano para o usuário $USER_NAME..."
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y ca-certificates curl git libreoffice-writer

if ! command -v ollama >/dev/null 2>&1; then
  OLLAMA_INSTALLER=$(mktemp)
  trap 'rm -f "${UV_INSTALLER:-}" "${OLLAMA_INSTALLER:-}"' EXIT
  curl -fsSL https://ollama.com/install.sh -o "$OLLAMA_INSTALLER"
  "${SUDO[@]}" sh "$OLLAMA_INSTALLER"
fi

"${SUDO[@]}" mkdir -p /etc/systemd/system/ollama.service.d
"${SUDO[@]}" tee /etc/systemd/system/ollama.service.d/arquivo-arcano.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Restart=always
RestartSec=5
EOF
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now ollama
"${SUDO[@]}" systemctl restart ollama
if ! curl --retry 10 --retry-all-errors --retry-delay 1 --fail --silent \
  http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "O Ollama não respondeu em http://127.0.0.1:11434." >&2
  "${SUDO[@]}" systemctl status ollama --no-pager >&2 || true
  exit 1
fi
if ! run_as_user ollama show gemma3:1b >/dev/null 2>&1; then
  echo "Baixando o modelo de texto gemma3:1b..."
  run_as_user ollama pull gemma3:1b
fi

if ! command -v uv >/dev/null 2>&1; then
  UV_INSTALLER=$(mktemp)
  trap 'rm -f "${UV_INSTALLER:-}" "${OLLAMA_INSTALLER:-}"' EXIT
  curl -fsSL https://astral.sh/uv/install.sh -o "$UV_INSTALLER"
  "${SUDO[@]}" env UV_INSTALL_DIR=/usr/local/bin sh "$UV_INSTALLER"
fi

"${SUDO[@]}" mkdir -p "$APP_DIR"
"${SUDO[@]}" chown -R "$USER_NAME:$USER_NAME" "$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
  if [ -n "$(ls -A "$APP_DIR")" ]; then
    echo "$APP_DIR não está vazio e não contém um repositório Git." >&2
    exit 1
  fi
  run_as_user git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  run_as_user git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  run_as_user git -C "$APP_DIR" fetch origin "$BRANCH"
  run_as_user git -C "$APP_DIR" checkout "$BRANCH"
  run_as_user git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
fi

run_as_user uv python install 3.12
run_as_user uv venv --python 3.12 --clear "$APP_DIR/.venv"
run_as_user uv pip install --python "$APP_DIR/.venv/bin/python" -e "$APP_DIR"

sed \
  -e "s|APP_DIRECTORY|$APP_DIR|g" \
  -e "s|YOUR_USERNAME|$USER_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search.service" \
  | "${SUDO[@]}" tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null

sed \
  -e "s|APP_DIRECTORY|$APP_DIR|g" \
  -e "s|YOUR_USERNAME|$USER_NAME|g" \
  -e "s|SERVICE_NAME_PLACEHOLDER|$SERVICE_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search-update.service" \
  | "${SUDO[@]}" tee "/etc/systemd/system/$SERVICE_NAME-update.service" >/dev/null

sed \
  -e "s|SERVICE_NAME_PLACEHOLDER|$SERVICE_NAME|g" \
  "$APP_DIR/deploy/rpg-rules-search-update.timer" \
  | "${SUDO[@]}" tee "/etc/systemd/system/$SERVICE_NAME-update.timer" >/dev/null

"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable --now "$SERVICE_NAME"
"${SUDO[@]}" systemctl enable --now "$SERVICE_NAME-update.timer"
"${SUDO[@]}" systemctl restart "$SERVICE_NAME"
"${SUDO[@]}" systemctl status "$SERVICE_NAME" --no-pager

echo "Arquivo Arcano instalado em http://$(hostname -I | awk '{print $1}'):8765"
