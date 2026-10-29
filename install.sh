#!/usr/bin/env bash
# Instala o Juno: ambiente Python, modelo de voz em português (Vosk) e config.json.
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

# Pergunta uma palavra e só aceita se o modelo de voz a conhece.
ask_word() {
  local question="$1" default="$2" word
  while true; do
    read -r -p "$question [$default]: " word
    word="$(printf '%s' "${word:-$default}" | tr '[:upper:]' '[:lower:]')"
    if .venv/bin/python -W ignore juno.py check-words $word >/dev/null; then
      printf '%s' "$word"; return
    fi
    echo "  O modelo de voz não conhece \"$word\". Escolha outra palavra." >&2
  done
}

if [ ! -f config.json ]; then
  if [ -t 0 ]; then
    echo "Configuração (Enter aceita o padrão):"
    wake="$(ask_word "Nome do bot, a palavra que começa o ditado" juno)"
    send="$(ask_word "Palavra que envia a mensagem" câmbio)"
    .venv/bin/python - "$wake" "$send" <<'PY'
import json, sys
config = json.load(open("config.example.json", encoding="utf-8"))
config["wake_word"], config["send_word"] = sys.argv[1], sys.argv[2]
json.dump(config, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
  else
    cp config.example.json config.json
  fi
fi

[ -f agentes.tsv ] || cp agentes.example.tsv agentes.tsv
echo "Pronto. Teste com: .venv/bin/python juno.py --test --verbose"
