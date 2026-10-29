#!/usr/bin/env bash
# Instala o Juno: ambiente Python, modelo de voz em português (Vosk), config.json e,
# se você quiser, o início automático no login. Rodar de novo é seguro: atualiza sem
# apagar a sua configuração.
set -euo pipefail
cd "$(dirname "$0")"

MODEL=vosk-model-small-pt-0.3
MODEL_URL="https://alphacephei.com/vosk/models/$MODEL.zip"
LABEL=com.aryrabelo.juno
SUPPORT="$HOME/Library/Application Support/Juno"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# Python estável do sistema: um python3 do PATH pode vir de um lugar que some depois
# (ex.: /nix/store, que a coleta de lixo do Nix apaga) e quebrar o .venv.
PYTHON="${JUNO_PYTHON:-/usr/bin/python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "Precisa de Python 3.9+ em $PYTHON. Instale as Command Line Tools (xcode-select --install)" >&2
  echo "ou aponte outro: JUNO_PYTHON=/caminho/python3 ./install.sh" >&2
  exit 1
fi

if ! .venv/bin/python -c 'import vosk, sounddevice' 2>/dev/null; then
  rm -rf .venv
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if [ ! -d "models/$MODEL" ]; then
  mkdir -p models
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/model.zip" "$MODEL_URL"
  unzip -q "$tmp/model.zip" -d "$tmp"   # extrai fora de models/: falha no meio não deixa modelo quebrado
  mv "$tmp/$MODEL" "models/$MODEL"
  rm -rf "$tmp"
fi

interactive() { [ -t 0 ]; }

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
  wake=juno send=câmbio shortcut="option+command+."
  if interactive; then
    echo "Configuração (Enter aceita o padrão):"
    wake="$(ask_word "Nome do bot, a palavra que começa o ditado" "$wake")"
    send="$(ask_word "Palavra que envia a mensagem" "$send")"
    read -r -p "Atalho de ditado do FluidVoice [$shortcut]: " answer
    shortcut="${answer:-$shortcut}"
  fi
  .venv/bin/python - "$wake" "$send" "$shortcut" <<'PY'
import json, sys
config = json.load(open("config.example.json", encoding="utf-8"))
config["wake_word"], config["send_word"], config["dictation_shortcut"] = sys.argv[1:4]
config["agents"] = {}
json.dump(config, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
fi

# Uma configuração só: leva os agentes do agentes.tsv antigo para o config.json.
if [ -f agentes.tsv ]; then
  .venv/bin/python - <<'PY'
import json, subprocess, sys
config = json.load(open("config.json", encoding="utf-8"))
old = json.loads(subprocess.run([sys.executable, "-W", "ignore", "juno.py", "migrate-agents", "agentes.tsv"],
                                capture_output=True, text=True, check=True).stdout)
config["agents"] = {**old, **config.get("agents", {})}
if "agent_app" in config:
    config.setdefault("app", config.pop("agent_app"))
json.dump(config, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"agentes.tsv migrado para config.json ({len(old)} agentes); o antigo ficou em agentes.tsv.migrated")
PY
  mv agentes.tsv agentes.tsv.migrated
fi

# Ajudante do "agente próximo": lê a barra lateral do app pela Acessibilidade.
if command -v swiftc >/dev/null; then
  mkdir -p bin
  swiftc -O -o bin/next-agent next-agent.swift
else
  echo "Aviso: sem swiftc (xcode-select --install); \"agente próximo\" fica desligado." >&2
fi

# Início no login: um LaunchAgent abre o Juno numa janela do Terminal. Assim as permissões
# de Microfone e Acessibilidade ficam com o Terminal, as mesmas que você deu ao testar.
if interactive && [ ! -f "$PLIST" ]; then
  read -r -p "Abrir o Juno sozinho no login? [s/N]: " answer
  if [[ "$answer" =~ ^[sSyY] ]]; then
    mkdir -p "$SUPPORT" "$(dirname "$PLIST")"
    cat > "$SUPPORT/juno.command" <<EOF
#!/bin/zsh
# Aberto no login pelo LaunchAgent $LABEL. Feche a janela para parar o Juno.
cd "$(pwd)" || exit 1
while true; do
  .venv/bin/python -W ignore juno.py
  echo "O Juno parou (código \$?). Volta em 10 s."
  sleep 10
done
EOF
    chmod +x "$SUPPORT/juno.command"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/usr/bin/open</string><string>-a</string><string>Terminal</string><string>$SUPPORT/juno.command</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
    echo "Pronto: o Juno abre no próximo login. Para desligar: rm \"$PLIST\""
  fi
fi

echo "Pronto. Teste com: .venv/bin/python juno.py --test --verbose"
