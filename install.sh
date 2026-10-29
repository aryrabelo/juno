#!/usr/bin/env bash
# Instala o Juno: ambiente Python, dependências e o modelo de voz em português (Vosk).
set -euo pipefail
cd "$(dirname "$0")"

MODEL=vosk-model-small-pt-0.3
MODEL_URL="https://alphacephei.com/vosk/models/$MODEL.zip"

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -d "models/$MODEL" ]; then
  mkdir -p models
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/model.zip" "$MODEL_URL"
  unzip -q "$tmp/model.zip" -d "$tmp"   # extrai fora de models/: falha no meio não deixa modelo quebrado
  mv "$tmp/$MODEL" "models/$MODEL"
  rm -rf "$tmp"
fi

[ -f agentes.tsv ] || cp agentes.example.tsv agentes.tsv
echo "Pronto. Rode: .venv/bin/python juno.py --test"
