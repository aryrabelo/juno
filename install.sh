#!/usr/bin/env bash
# Instala o Juno: ambiente Python, modelo de voz (Vosk) do idioma escolhido (português ou
# inglês), config.json e, se você quiser, o início automático no login. Rodar de novo é
# seguro: atualiza sem apagar a sua configuração.
# Installs Juno in Portuguese or English; safe to run again.
set -euo pipefail
cd "$(dirname "$0")"

LABEL=com.aryrabelo.juno
SUPPORT="$HOME/Library/Application Support/Juno"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

interactive() { [ -t 0 ]; }

# Idioma das mensagens e das palavras padrão. Até ler o config.json (ou perguntar), vale o
# idioma do macOS: português se ele começar com "pt", inglês nos outros casos.
lang="$(defaults read -g AppleLanguages 2>/dev/null | grep -m1 -o '[a-z][a-z]' | head -n 1 || true)"
[ "$lang" = pt ] || lang=en
# t "texto em português" "text in English"
t() { if [ "$lang" = pt ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }

# Python estável do sistema: um python3 do PATH pode vir de um lugar que some depois
# (ex.: /nix/store, que a coleta de lixo do Nix apaga) e quebrar o .venv.
PYTHON="${JUNO_PYTHON:-/usr/bin/python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "$(t "Precisa de Python 3.9+ em $PYTHON. Instale as Command Line Tools (xcode-select --install)" \
            "Needs Python 3.9+ at $PYTHON. Install the Command Line Tools (xcode-select --install)")" >&2
  echo "$(t "ou aponte outro: JUNO_PYTHON=/caminho/python3 ./install.sh" \
            "or point to another one: JUNO_PYTHON=/path/python3 ./install.sh")" >&2
  exit 1
fi

if ! .venv/bin/python -c 'import vosk, sounddevice' 2>/dev/null; then
  rm -rf .venv
  "$PYTHON" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

# Instalação existente: o idioma é o do config.json (sem "language" = português).
# Instalação nova: JUNO_LANGUAGE=pt|en, ou a resposta à pergunta.
if [ -f config.json ]; then
  lang="$("$PYTHON" -c 'import json; print(json.load(open("config.json", encoding="utf-8")).get("language", "pt"))')"
elif [ -n "${JUNO_LANGUAGE:-}" ]; then
  lang="$JUNO_LANGUAGE"
elif interactive; then
  while true; do
    read -r -p "Idioma / Language (pt/en) [$lang]: " answer
    answer="$(printf '%s' "${answer:-$lang}" | tr '[:upper:]' '[:lower:]')"
    case "$answer" in pt|en) lang="$answer"; break ;; esac
    echo "  pt = português, en = English" >&2
  done
fi
case "$lang" in
  pt|en) ;;
  *) echo "Idioma \"$lang\" não existe; use pt ou en. / Language \"$lang\" doesn't exist; use pt or en." >&2; exit 1 ;;
esac

MODEL="$(.venv/bin/python -W ignore juno.py model-name "$lang")"
if [ ! -d "models/$MODEL" ]; then
  echo "$(t "Baixando o modelo de voz ($MODEL)..." "Downloading the voice model ($MODEL)...")"
  mkdir -p models
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/model.zip" "https://alphacephei.com/vosk/models/$MODEL.zip"
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
    if .venv/bin/python -W ignore juno.py check-words --language "$lang" $word >/dev/null; then
      printf '%s' "$word"; return
    fi
    echo "  $(t "O modelo de voz não conhece \"$word\". Escolha outra palavra." \
                "The voice model doesn't know \"$word\". Pick another word.")" >&2
  done
}

if [ ! -f config.json ]; then
  example="config.example.$lang.json"
  { read -r wake; read -r send; read -r shortcut; } < <("$PYTHON" -c '
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
print(c["wake_word"]); print(c["send_word"]); print(c["dictation_shortcut"])' "$example")
  if interactive; then
    echo "$(t "Configuração (Enter aceita o padrão):" "Setup (Enter keeps the default):")"
    wake="$(ask_word "$(t "Nome do bot, a palavra que começa o ditado" "Bot name, the word that starts dictation")" "$wake")"
    send="$(ask_word "$(t "Palavra que envia a mensagem" "Word that sends the message")" "$send")"
    read -r -p "$(t "Atalho de ditado do FluidVoice" "FluidVoice dictation shortcut") [$shortcut]: " answer
    shortcut="${answer:-$shortcut}"
  fi
  "$PYTHON" - "$example" "$wake" "$send" "$shortcut" <<'PY'
import json, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
config["wake_word"], config["send_word"], config["dictation_shortcut"] = sys.argv[2:5]
json.dump(config, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
fi

# Formato antigo: "agente <apelido>" (agents no config.json ou agentes.tsv) vira atalho do
# módulo OpenBot em "shortcuts". Não faz nada num config.json já no formato novo.
.venv/bin/python -W ignore juno.py migrate-config

# Ajudante do módulo OpenBot ({"openbot": "next"}): lê a barra lateral pela Acessibilidade.
if command -v swiftc >/dev/null; then
  mkdir -p bin
  swiftc -O -o bin/next-agent next-agent.swift
else
  echo "$(t "Aviso: sem swiftc (xcode-select --install); \"agente próximo\" fica desligado." \
            "Warning: no swiftc (xcode-select --install); \"next agent\" stays off.")" >&2
fi

# Início no login: um LaunchAgent abre o Juno numa janela do Terminal. Assim as permissões
# de Microfone e Acessibilidade ficam com o Terminal, as mesmas que você deu ao testar.
if interactive && [ ! -f "$PLIST" ]; then
  read -r -p "$(t "Abrir o Juno sozinho no login? [s/N]: " "Start Juno at login? [y/N]: ")" answer
  if [[ "$answer" =~ ^[sSyY] ]]; then
    mkdir -p "$SUPPORT" "$(dirname "$PLIST")"
    cat > "$SUPPORT/juno.command" <<EOF
#!/bin/zsh
# Aberto no login pelo LaunchAgent $LABEL. Feche a janela para parar o Juno.
cd "$(pwd)" || exit 1
while true; do
  .venv/bin/python -W ignore juno.py
  echo "$(t "O Juno parou (código \$?). Volta em 10 s." "Juno stopped (exit code \$?). Restarting in 10 s.")"
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
    echo "$(t "Pronto: o Juno abre no próximo login. Para desligar: rm \"$PLIST\"" \
              "Done: Juno opens at your next login. To turn it off: rm \"$PLIST\"")"
  fi
fi

echo "$(t "Pronto. Teste com: .venv/bin/python juno.py --test --verbose" \
          "Done. Try it: .venv/bin/python juno.py --test --verbose")"
