#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # Load the project virtualenv so checks match the deployed app environment.
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
export OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
export SEMANTIC_MODEL_NAME="${SEMANTIC_MODEL_NAME:-sentence-transformers/all-MiniLM-L6-v2}"

echo "Project: $PROJECT_DIR"
echo "Python: $(python --version 2>&1)"
echo "OLLAMA_HOST: $OLLAMA_HOST"
echo "OLLAMA_EMBED_MODEL: $OLLAMA_EMBED_MODEL"
echo "SEMANTIC_MODEL_NAME: $SEMANTIC_MODEL_NAME"

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet ollama; then
    echo "ollama service: active"
  else
    echo "ollama service: inactive or not installed"
  fi
fi

if command -v curl >/dev/null 2>&1; then
  echo "Ollama tags endpoint:"
  if ! curl --silent --show-error --fail "$OLLAMA_HOST/api/tags"; then
    echo
    echo "Could not reach $OLLAMA_HOST/api/tags"
  else
    echo
  fi
fi

if command -v ollama >/dev/null 2>&1; then
  echo "Ensuring Ollama embedding model is present..."
  ollama pull "$OLLAMA_EMBED_MODEL"
else
  echo "ollama CLI not found; skipping model pull"
fi

echo "Running Django embedding backend check..."
python manage.py check_embedding_backend --require-embedding --skip-checks
