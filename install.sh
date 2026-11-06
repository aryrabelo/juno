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

# Início no login, em segundo plano e sem janela: o LaunchAgent abre o Juno.app no login e de
# novo se ele parar. O Juno.app (juno-app.swift) é um app de verdade, então o macOS pede
# Microfone, Acessibilidade e Automação em nome de "Juno" e o juno.py, filho dele, herda.
# Instalação antiga (o LaunchAgent abria juno.command numa janela do Terminal): vira esta.
APP="$SUPPORT/Juno.app"
LOGS="$HOME/Library/Logs/Juno"
DOMAIN="gui/$(id -u)"

# Monta o Juno.app. Só recompila quando o código ou o Info.plist mudam: o swiftc não gera
# sempre o mesmo binário, e um binário novo faz o macOS pedir as permissões de novo.
build_app() {
  local tmp; tmp="$(mktemp -d)"
  "$PYTHON" - "$tmp/Info.plist" \
    "$(t "O Juno ouve só as suas palavras de comando, sem nada sair do Mac." \
         "Juno listens only for your command words; nothing leaves the Mac.")" \
    "$(t "O Juno aperta as teclas dos seus comandos de voz pelo System Events." \
         "Juno presses the keys for your voice commands through System Events.")" <<'PY'
import plistlib, sys
plistlib.dump({
    "CFBundleIdentifier": "com.aryrabelo.juno", "CFBundleName": "Juno", "CFBundleExecutable": "Juno",
    "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "1",
    "LSUIElement": True, "NSMicrophoneUsageDescription": sys.argv[2], "NSAppleEventsUsageDescription": sys.argv[3],
}, open(sys.argv[1], "wb"))
PY
  if [ -x "$APP/Contents/MacOS/Juno" ] && cat juno-app.swift "$tmp/Info.plist" | cmp -s - "$SUPPORT/Juno.app.src"; then
    rm -rf "$tmp"; return 0
  fi
  mkdir -p "$tmp/Juno.app/Contents/MacOS"
  cp "$tmp/Info.plist" "$tmp/Juno.app/Contents/Info.plist"
  swiftc -O -o "$tmp/Juno.app/Contents/MacOS/Juno" juno-app.swift
  codesign --force --sign - "$tmp/Juno.app" 2>/dev/null   # "replacing existing signature": o ld já assina
  rm -rf "$APP"
  mv "$tmp/Juno.app" "$APP"
  cat juno-app.swift "$tmp/Info.plist" > "$SUPPORT/Juno.app.src"
  rm -rf "$tmp"
  app_built=true
}

# O LaunchAgent: Juno.app <pasta do Juno>, no login (RunAtLoad) e de novo 10 s depois de parar
# (KeepAlive). Leva o PATH e o LANG deste terminal: os atalhos "run" acham os mesmos
# comandos, e o pbcopy cola acentos e "·" certo (sem LANG UTF-8 ele os estraga).
write_plist() {
  local lang_env="${LANG:-}"
  [[ "$lang_env" == *UTF-8* ]] || lang_env=en_US.UTF-8
  "$PYTHON" - "$PLIST" "$LABEL" "$APP/Contents/MacOS/Juno" "$JUNO_DIR" "$LOGS/juno.log" "$PATH" "$lang_env" <<'PY'
import plistlib, sys
plist, label, exe, here, logfile, path, lang = sys.argv[1:]
plistlib.dump({
    "Label": label, "ProgramArguments": [exe, here],
    "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10, "ProcessType": "Interactive",
    "StandardOutPath": logfile, "StandardErrorPath": logfile,
    "EnvironmentVariables": {"PATH": path, "LANG": lang},
    "AssociatedBundleIdentifiers": ["com.aryrabelo.juno"],
}, open(plist, "wb"))
PY
}

# (Re)carrega o LaunchAgent agora, sem esperar o próximo login.
start_agent() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  local _
  for _ in 1 2 3 4 5 6 7 8 9 10; do   # o bootout termina o Juno antigo; espera ele sair
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
    sleep 0.5
  done
  launchctl bootstrap "$DOMAIN" "$PLIST"
}

app_built=false
was_on=false      # já rodava em segundo plano: rodar o install.sh de novo só atualiza e reinicia
[ -f "$PLIST" ] && ! grep -q 'juno\.command' "$PLIST" && was_on=true
old_login=false   # o jeito antigo: LaunchAgent abrindo juno.command no Terminal
[ -f "$PLIST" ] && grep -q 'juno\.command' "$PLIST" && old_login=true
want_login=false
if [ -f "$PLIST" ]; then
  want_login=true   # já estava ligado: atualiza (e reinicia com o código novo)
elif interactive; then
  read -r -p "$(t "Abrir o Juno sozinho no login, em segundo plano (sem janela)? [s/N]: " \
                  "Start Juno at login, in the background (no window)? [y/N]: ")" answer
  [[ "$answer" =~ ^[sSyY] ]] && want_login=true
fi

# De que pasta o Juno do login roda. Rodar o install.sh noutro clone ou worktree não o leva
# para lá sem perguntar: sem terminal, ou sem um "sim", fica na pasta de antes.
JUNO_DIR="$(pwd)"
saved_dir=""
if $was_on; then
  saved_dir="$("$PYTHON" -c 'import plistlib, sys
args = plistlib.load(open(sys.argv[1], "rb")).get("ProgramArguments", [])
print(args[1] if len(args) > 1 else "")' "$PLIST" 2>/dev/null || true)"
elif $old_login && [ -f "$SUPPORT/juno.command" ]; then
  saved_dir="$(sed -n 's/^cd "\(.*\)" || exit 1$/\1/p' "$SUPPORT/juno.command" | head -n 1)"
fi
if $want_login && [ -n "$saved_dir" ] && [ "$saved_dir" != "$JUNO_DIR" ] && [ -f "$saved_dir/juno.py" ]; then
  answer=n
  if interactive; then
    read -r -p "$(t "O Juno do login roda de $saved_dir. Passar para esta pasta ($JUNO_DIR)? [s/N]: " \
                    "Juno at login runs from $saved_dir. Switch it to this folder ($JUNO_DIR)? [y/N]: ")" answer
  fi
  if [[ ! "$answer" =~ ^[sSyY] ]]; then
    JUNO_DIR="$saved_dir"
    echo "$(t "O Juno do login continua rodando de $saved_dir." "Juno at login keeps running from $saved_dir.")"
  fi
fi

if $want_login && ! command -v swiftc >/dev/null; then
  echo "$(t "Aviso: sem swiftc (xcode-select --install), não dá para abrir o Juno em segundo plano no login." \
            "Warning: no swiftc (xcode-select --install), so Juno can't start in the background at login.")" >&2
  $old_login && echo "$(t "O início antigo, na janela do Terminal, continua como estava." \
                          "The old start, in a Terminal window, stays as it was.")" >&2
elif $want_login; then
  mkdir -p "$SUPPORT" "$LOGS" "$(dirname "$PLIST")"
  build_app
  write_plist
  if $old_login; then
    rm -f "$SUPPORT/juno.command"
    echo "$(t "O Juno não abre mais numa janela do Terminal no login: agora roda em segundo plano." \
              "Juno no longer opens in a Terminal window at login: it now runs in the background.")"
    # O Juno antigo ainda aberto no Terminal apertaria as teclas junto com o novo: para os dois
    # (o laço do juno.command primeiro, senão ele abre o python de novo).
    for pid in $(pgrep -f -- "$SUPPORT/juno.command" || true); do
      kids="$(pgrep -P "$pid" || true)"
      kill "$pid" $kids 2>/dev/null || true
      echo "$(t "Parei o Juno antigo que rodava numa janela do Terminal; pode fechar a janela." \
                "Stopped the old Juno running in a Terminal window; you can close that window.")"
    done
  fi
  if ! start_agent; then
    echo "$(t "Não consegui ligar o Juno agora. Veja se ele está permitido em Ajustes do Sistema › Geral › Itens de Início e Extensões; ele abre no próximo login." \
              "Couldn't start Juno now. Check that it's allowed in System Settings › General › Login Items & Extensions; it starts at your next login.")" >&2
  elif $was_on; then
    echo "$(t "O Juno reiniciou em segundo plano, com o código de $JUNO_DIR." \
              "Juno restarted in the background, with the code in $JUNO_DIR.")"
  else
    echo "$(t "Pronto: o Juno está rodando em segundo plano e volta a cada login (e sozinho, se parar)." \
              "Done: Juno is running in the background and starts at every login (and again by itself if it stops).")"
  fi
  # Um Juno.app novo é outro app para o macOS: as permissões são pedidas de novo.
  if $app_built; then
    echo "$(t "O macOS vai pedir, para \"Juno\": permita o Microfone, ligue o Juno em Acessibilidade e permita controlar o System Events. Se o Juno já estava na lista de Acessibilidade, tire-o (−) e ligue de novo." \
              "macOS will ask, for \"Juno\": allow the Microphone, turn Juno on in Accessibility, and allow it to control System Events. If Juno was already in the Accessibility list, remove it (−) and turn it on again.")"
  fi
  echo "  $(t "Log:" "Log:")             tail -f \"$LOGS/juno.log\""
  echo "  $(t "Parar agora:" "Stop for now:")    launchctl bootout $DOMAIN/$LABEL   $(t "(volta no próximo login)" "(comes back at next login)")"
  echo "  $(t "Ligar de novo:" "Start again:")   launchctl bootstrap $DOMAIN \"$PLIST\""
  echo "  $(t "Desligar de vez:" "Turn it off:")  launchctl bootout $DOMAIN/$LABEL; rm \"$PLIST\""
fi
# Sobra de uma instalação antiga desligada (rm do plist): o juno.command não serve mais.
if [ -f "$SUPPORT/juno.command" ] && ! grep -qs 'juno\.command' "$PLIST"; then
  rm -f "$SUPPORT/juno.command"
fi

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "$(t "Para testar à mão, pare o de segundo plano antes (launchctl bootout $DOMAIN/$LABEL) e rode: .venv/bin/python juno.py --test --verbose" \
            "To try it by hand, stop the background one first (launchctl bootout $DOMAIN/$LABEL), then run: .venv/bin/python juno.py --test --verbose")"
else
  echo "$(t "Pronto. Teste com: .venv/bin/python juno.py --test --verbose" \
            "Done. Try it: .venv/bin/python juno.py --test --verbose")"
fi
