#!/usr/bin/env python3
"""Juno: comandos de voz sem as mãos, com reconhecimento offline (Vosk).

"juno"   -> aperta o atalho de ditado do FluidVoice (config.json, padrão ⌥⌘.).
"câmbio" (no fim da fala, seguido de silêncio) -> se o FluidVoice estiver gravando, para o
            ditado (o Spoken Send dele tira "câmbio" do texto) e espera colar; depois aperta
            Enter (e, com --cmd-enter, também ⌘Enter).
"desliga" (sozinha) -> aperta Esc.
Atalhos extras ("shortcuts" no config.json; frase dita sozinha, fora do ditado) -> aperta um
            atalho de teclado, abre um app, roda um comando ou, no módulo OpenBot, abre um agente
            ou o próximo chat com respostas não lidas (bin/next-agent).

Uso: .venv/bin/python juno.py [--test] [--cmd-enter] [--verbose]
     .venv/bin/python juno.py stats [N]   # resumo dos últimos N eventos (padrão 500)
     .venv/bin/python juno.py migrate-config   # config.json antigo (agente/agents) -> atalhos

Eventos (JSON por linha) em ~/Library/Logs/Juno/events.jsonl: cada trecho reconhecido, com
confiança e volume, se virou ação ou por que foi ignorado, e quanto cada ação levou. Só ficam
registradas as palavras de comando (o resto vira "[unk]"); nenhum texto ditado é gravado.
"""
import array
import json
import math
import os
import queue
import re
import subprocess
import sys
import time
from pathlib import Path

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

HERE = Path(__file__).resolve().parent
FLUID_LOG = Path.home() / "Library" / "Logs" / "Fluid" / "Fluid.log"
CONFIG_FILE = HERE / "config.json"  # criado pelo install.sh a partir de config.example.<idioma>.json
_config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
LEGACY_AGENTS_FILE = HERE / "agentes.tsv"  # formato antigo; o install.sh migra para o config.json

# Idioma -> modelo Vosk. As palavras padrão de cada idioma ficam em config.example.<idioma>.json.
MODELS = {"pt": "vosk-model-small-pt-0.3", "en": "vosk-model-small-en-us-0.15"}
LANGUAGE = _config.get("language", "pt")  # config.json sem "language" (instalação antiga) é português
if LANGUAGE not in MODELS:
    raise SystemExit(f'config.json: language "{LANGUAGE}" não existe; use "pt" ou "en"')
MODEL = HERE / "models" / MODELS[LANGUAGE]
_defaults = json.loads((HERE / f"config.example.{LANGUAGE}.json").read_text(encoding="utf-8"))


def _setting(key):
    """Valor do config.json ou, se faltar, o padrão do idioma."""
    return _config.get(key, _defaults[key])


def _word_list(value):
    """Uma palavra ou uma lista no config.json, ex.: "atlas" ou ["atlas", "ata"]."""
    return [v.lower() for v in (value if isinstance(value, list) else [value])]


WAKE_WORDS = _word_list(_setting("wake_word"))  # o "nome do bot" (e variantes)
WAKE_WORD = WAKE_WORDS[0]
SEND_PHRASES = [p.split() for p in _word_list(_setting("send_word"))]
SEND_PHRASE = SEND_PHRASES[0]
COOLDOWN_S = 2.0
# Réguas de confiança: na dúvida, o Juno prefere agir (um falso começo se desfaz com "desliga").
MIN_CONF = 0.80  # "desliga", atalhos e câmbio com o ditado gravando
# Câmbio sem ditado aperta Enter no app da frente, em qualquer coisa: régua alta.
SEND_IDLE_MIN_CONF = 0.95
# O nome do bot só liga o ditado (nunca envia nem apaga). Ele dispara já no resultado parcial,
# sem esperar a pausa; esta régua vale quando ele só aparece no resultado final.
WAKE_MIN_CONF = 0.50
MAX_RECORDING_S = 300
# Silêncio a mais depois do câmbio. O resultado final do Vosk já chega após ~0,9 s de pausa.
SEND_SILENCE_S = 0.3
PASTE_TIMEOUT_S = 10.0  # espera máxima pelo FluidVoice colar (volta antes, assim que ele cola)
# Sem áudio do microfone por este tempo, o aparelho sumiu (fone desconectado, troca de
# entrada no macOS): o fluxo fica mudo para sempre, então o Juno reabre no microfone padrão.
MIC_STALL_S = 5.0
STOP_WORD = _setting("stop_word").lower()  # falada sozinha: aperta Esc
# Formato antigo, quando o OpenBot tinha palavras próprias; o install.sh converte em "shortcuts".
LEGACY_KEYS = ("agent_word", "next_word", "agents", "app", "agent_app")
LEGACY_OPENBOT_WORDS = {"pt": ("agente", "próximo"), "en": ("agent", "next")}
SHORTCUT_ACTIONS = ("keys", "open", "run", "openbot")
OPENBOT_APP = "OpenBot"
NEXT_AGENT = HERE / "bin" / "next-agent"  # compilado pelo install.sh a partir de next-agent.swift

DICTATION_SHORTCUT = _setting("dictation_shortcut")  # atalho do FluidVoice
FLUID_SHORTCUT = None  # AppleScript do atalho; montado em main() a partir de DICTATION_SHORTCUT
ENTER = 'key code 36'
ESCAPE = 'key code 53'
SEARCH = 'keystroke "k" using {command down}'
CMD_ENTER = 'key code 36 using {command down}'

CHUNK_S = 0.1  # 1600 amostras a 16 kHz: o resultado chega até 0,15 s antes que com 0,25 s
SEND_SILENCE_CHUNKS = max(1, round(SEND_SILENCE_S / CHUNK_S))
EVENTS = Path.home() / "Library" / "Logs" / "Juno" / "events.jsonl"
EVENTS_MAX_BYTES = 5_000_000
VERBOSE = "--verbose" in sys.argv


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def event(kind, **fields):
    """Grava um evento estruturado; com --verbose também mostra no terminal."""
    record = {"ts": round(time.time(), 3), "event": kind, **fields}
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        if EVENTS.exists() and EVENTS.stat().st_size > EVENTS_MAX_BYTES:
            os.replace(EVENTS, EVENTS.with_suffix(".jsonl.1"))
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log(f"não consegui gravar evento: {exc}")
    if VERBOSE:
        log(f"[{kind}] " + " ".join(f"{k}={v}" for k, v in fields.items()))


def press(keys, label):
    script = f'tell application "System Events" to {keys}'
    started = time.time()
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    ms = round((time.time() - started) * 1000)
    if result.returncode != 0:
        log(f"falha ao apertar {label}: {result.stderr.strip()}")
        event("key", label=label, ok=False, ms=ms, error=result.stderr.strip())
    else:
        log(f"apertei {label}")
        event("key", label=label, ok=True, ms=ms)


def run_script(script, *args):
    return subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)


def type_text(text):
    """Cola `text` (⌘V) em vez de digitar: keystroke perde "·" e acentos em alguns layouts.
    Restaura a área de transferência depois."""
    old = subprocess.run(["pbpaste"], capture_output=True).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"))
    press('keystroke "v" using {command down}', f'"{text}" (⌘V)')
    time.sleep(0.3)
    subprocess.run(["pbcopy"], input=old)


def fluid_log_size():
    try:
        return FLUID_LOG.stat().st_size
    except OSError:
        return 0


def wait_paste(since):
    """Espera o FluidVoice colar depois de `since` bytes do log.

    Retorna "pasted" (colou), "no_field" (ele guardou o texto só no histórico), "empty" (não
    sobrou texto: só se disse "câmbio", que o Spoken Send tira) ou "timeout" (sem resposta em
    PASTE_TIMEOUT_S). O 1.6.13+ registra PASTE_VERIFY; todas as versões fecham o ditado com
    PIPELINE_SUMMARY ... outcome=<inserted|history_only>, ou só DICTATION_SUMMARY ... outcome=empty
    quando não há o que colar."""
    started = time.time()
    deadline = started + PASTE_TIMEOUT_S
    outcome = "timeout"
    while time.time() < deadline:
        if fluid_log_size() < since:  # o FluidVoice girou o log (Fluid.log -> Fluid.log.1)
            since = 0
        try:
            with FLUID_LOG.open("rb") as f:
                f.seek(since)
                new = f.read()
        except OSError:
            break
        if b"PASTE_VERIFY" in new or b"outcome=inserted" in new:
            outcome = "pasted"
            break
        if b"outcome=history_only" in new:
            outcome = "no_field"
            break
        if re.search(rb"outcome=empty\b", new):
            outcome = "empty"
            break
        time.sleep(0.1)
    event("paste", outcome=outcome, waited_ms=round((time.time() - started) * 1000))
    return outcome


MODIFIERS = {"command": "command", "cmd": "command", "⌘": "command",
             "option": "option", "opt": "option", "alt": "option", "⌥": "option",
             "control": "control", "ctrl": "control", "⌃": "control",
             "shift": "shift", "⇧": "shift"}
KEY_CODES = {".": 47, ",": 43, "/": 44, ";": 41, "'": 39, "-": 27, "=": 24, "space": 49,
             "return": 36, "enter": 36, "tab": 48, "escape": 53, "esc": 53}


def shortcut_script(shortcut, where="dictation_shortcut"):
    """'control+option+.' -> trecho AppleScript do System Events ('key code 47 using {...}')."""
    parts = [p.strip().lower() for p in shortcut.split("+")]
    *mods, key = parts
    try:
        using = sorted({MODIFIERS[m] for m in mods})
    except KeyError as exc:
        raise SystemExit(f"config.json: modificador desconhecido em {where}: {exc}")
    if key in KEY_CODES:
        action = f"key code {KEY_CODES[key]}"
    elif len(key) == 1 and key.isalnum():
        action = f'keystroke "{key}"'
    else:
        raise SystemExit(f"config.json: tecla desconhecida em {where}: {key!r}")
    if using:
        action += " using {" + ", ".join(f"{m} down" for m in using) + "}"
    return action


def clean_agents(raw, source):
    """Apelido de uma palavra -> nome do agente; descarta entradas inválidas com aviso."""
    agents = {}
    for alias, target in raw.items():
        alias = str(alias).strip().lower()
        target = "".join(c for c in str(target).strip() if c.isprintable())
        if not alias or " " in alias or not target:
            log(f"{source}: ignorei {alias!r} -> {target!r} (apelido de uma palavra e nome obrigatórios)")
            continue
        agents[alias] = target
    return agents


def read_legacy_agents(path):
    """agentes.tsv (formato antigo): <apelido> TAB <nome>. Linhas com # são comentário."""
    raw = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "\t" in line:
            alias, target = line.split("\t", 1)
            raw[alias] = target
    return raw


def clean_shortcuts(raw):
    """"frase" -> (tipo, valor), de "shortcuts" no config.json; descarta entradas inválidas com aviso.

    Ação: {"keys": "command+s"}, {"open": "Safari"}, {"run": "comando"} ou, no módulo OpenBot,
    {"openbot": "next"} (próximo chat com respostas não lidas) e {"openbot": "<nome do agente>"}."""
    shortcuts = {}
    for phrase, action in raw.items():
        words = str(phrase).lower().split()
        phrase = " ".join(words)
        if not isinstance(action, dict) or len(action) != 1 or next(iter(action)) not in SHORTCUT_ACTIONS:
            log(f"config.json: ignorei o atalho {phrase!r}: a ação é uma só, entre "
                f"{', '.join(SHORTCUT_ACTIONS)} (ex.: {{\"keys\": \"command+s\"}})")
            continue
        kind, value = next(iter(action.items()))
        value = "".join(c for c in str(value).strip() if c.isprintable())
        if not words or not value:
            log(f"config.json: ignorei o atalho {phrase!r}: frase e valor obrigatórios")
            continue
        # Essas frases nunca chegariam ao atalho: o nome do bot, o envio e o Esc vêm antes.
        if words == [STOP_WORD] or words[-1] in WAKE_WORDS or any(words[-len(p):] == p for p in SEND_PHRASES):
            log(f"config.json: ignorei o atalho {phrase!r}: termina no nome do bot ou na palavra de envio, "
                f"ou é a palavra que aperta Esc")
            continue
        if kind == "keys":
            try:
                shortcut_script(value, f"atalho {phrase!r}")
            except SystemExit as exc:
                log(str(exc))
                continue
        shortcuts[phrase] = (kind, value)
    return shortcuts


def migrate_config(config, legacy_agents=None):
    """Formato antigo (agent_word, next_word, agents, app; ou agentes.tsv) -> atalhos do módulo
    OpenBot em "shortcuts". Devolve (config, mudou?).

    "agente <apelido>" vira {"openbot": "<nome>"}; "agente próximo" e "próximo agente" viram
    {"openbot": "next"}. Um atalho que já existe com a mesma frase não é trocado."""
    if not legacy_agents and not any(k in config for k in LEGACY_KEYS):
        return config, False
    config = dict(config)
    agent_word, next_word = LEGACY_OPENBOT_WORDS.get(config.get("language", "pt"), LEGACY_OPENBOT_WORDS["pt"])
    agent_word = str(config.pop("agent_word", agent_word)).lower()
    next_word = str(config.pop("next_word", next_word)).lower()
    agents = {**clean_agents(legacy_agents or {}, "agentes.tsv"), **clean_agents(config.pop("agents", {}), "config.json")}
    config.pop("app", None)
    config.pop("agent_app", None)
    shortcuts = dict(config.get("shortcuts", {}))
    for phrase, target in [(f"{agent_word} {next_word}", "next"), (f"{next_word} {agent_word}", "next"),
                           *((f"{agent_word} {alias}", name) for alias, name in agents.items())]:
        shortcuts.setdefault(phrase, {"openbot": target})
    config["shortcuts"] = shortcuts
    return config, True


def rms(chunk):
    """Volume (RMS) de áudio int16 mono; substitui audioop.rms, que saiu do Python 3.13."""
    samples = array.array("h", chunk)
    if not samples:
        return 0
    return int(math.sqrt(sum(x * x for x in samples) / len(samples)))


def fluid_recording():
    """Lê o fim do log do FluidVoice para saber se o ditado está gravando."""
    try:
        with FLUID_LOG.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode("utf-8", "ignore")
    except OSError:
        return False
    start = tail.rfind("START() called")
    # O FluidVoice nem sempre registra "STOP() called" (ex.: app reaberto no meio de uma gravação).
    stop = max(tail.rfind(m) for m in ("STOP() called", "Stopping recording", "Application launched"))
    if start <= stop:
        return False
    # Rede de segurança: uma gravação "aberta" há muito tempo provavelmente já terminou.
    line_start = tail.rfind("\n", 0, start) + 1
    try:
        started = time.strptime(tail[line_start + 1:line_start + 9], "%H:%M:%S")
    except ValueError:
        return True
    now = time.localtime()
    age = (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec) - (started.tm_hour * 3600 + started.tm_min * 60 + started.tm_sec)
    return age % 86400 < MAX_RECORDING_S  # % 86400: gravação que atravessa a meia-noite


def on_wake(test_only):
    if fluid_recording():
        log("FluidVoice já está gravando; ignorei")
        return
    if not test_only:
        press(FLUID_SHORTCUT, f"{DICTATION_SHORTCUT} (FluidVoice)")


def on_send(test_only, cmd_enter):
    if test_only:
        return
    if fluid_recording():
        # Para o ditado; o FluidVoice tira "câmbio" do texto ao colar.
        size = fluid_log_size()
        press(FLUID_SHORTCUT, f"{DICTATION_SHORTCUT} (parar FluidVoice)")
        outcome = wait_paste(size)
        if outcome != "pasted":
            # Envia em qualquer app (terminal, navegador, Electron), sem exceção.
            log(f"não vi o FluidVoice colar ({outcome}); envio mesmo assim")
    press(ENTER, "Enter")
    if cmd_enter:
        press(CMD_ENTER, "⌘Enter")


def bring_app_front():
    """Traz o OpenBot para a frente e confere: ⌘K + Enter em outro app (navegador, Slack,
    editor) faria outra coisa."""
    result = run_script(f'tell application "{OPENBOT_APP}" to activate')
    if result.returncode != 0:
        log(f"não consegui abrir o {OPENBOT_APP}: {result.stderr.strip()}")
        return False
    time.sleep(0.4)
    front = run_script('tell application "System Events" to get name of first process whose frontmost is true')
    if front.stdout.strip() != OPENBOT_APP:
        log(f"{OPENBOT_APP} não ficou na frente ({front.stdout.strip() or '?'}); não troquei de agente")
        return False
    return True


def openbot_next(test_only):
    """Módulo OpenBot: abre o primeiro chat com respostas não lidas e foca a caixa de mensagem."""
    if not NEXT_AGENT.exists():
        log(f"falta {NEXT_AGENT}: rode ./install.sh (precisa do swiftc)")
        return
    args = [str(NEXT_AGENT), OPENBOT_APP] if not test_only else [str(NEXT_AGENT), "--dry-run", OPENBOT_APP]
    if not test_only and not bring_app_front():
        return
    started = time.time()
    result = subprocess.run(args, capture_output=True, text=True)
    ms = round((time.time() - started) * 1000)
    name = result.stdout.strip()
    outcome = {0: "found" if test_only else "opened", 1: "none"}.get(result.returncode, "error")
    event("next_agent", outcome=outcome, chat=name or None, ms=ms, error=result.stderr.strip() or None)
    if outcome in ("opened", "found"):
        log(f"abri o próximo com não lidas: {name}" + (" (teste: não abri)" if test_only else ""))
    elif outcome == "none":
        log("nenhum agente com respostas não lidas")
    else:
        log(f"falha ao procurar o próximo agente: {result.stderr.strip()}")


def openbot_open(target):
    """Módulo OpenBot: abre o agente `target` pela busca (⌘K)."""
    if not bring_app_front():
        return
    press(SEARCH, "⌘K (busca)")
    time.sleep(0.4)
    type_text(target)
    time.sleep(0.8)
    press(ENTER, "Enter")


def run_shortcut(test_only, phrase, action):
    kind, value = action
    if fluid_recording():
        log(f'ditado em andamento; ignorei o atalho "{phrase}"')
        return
    if kind == "openbot" and value == "next":
        openbot_next(test_only)  # no --test só procura, sem abrir
        return
    if test_only:
        log(f'teste: o atalho "{phrase}" faria {kind} {value!r}')
        return
    if kind == "keys":
        press(shortcut_script(value), value)
    elif kind == "open":
        result = subprocess.run(["open", "-a", value], capture_output=True, text=True)
        event("open", app=value, ok=result.returncode == 0, error=result.stderr.strip() or None)
        if result.returncode != 0:
            log(f"não consegui abrir {value}: {result.stderr.strip()}")
    elif kind == "run":
        # Sem esperar: um comando demorado não pode travar a escuta.
        subprocess.Popen(value, shell=True, cwd=Path.home(), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        event("run", command=value)
    else:
        log(f"-> {value}")
        openbot_open(value)


def stats(n):
    """Resumo dos últimos n eventos: o que foi ouvido, aceito, ignorado e por quê."""
    if not EVENTS.exists():
        print(f"sem eventos em {EVENTS}")
        return
    lines = EVENTS.read_text(encoding="utf-8").splitlines()[-n:]
    records = [json.loads(line) for line in lines if line.strip()]
    heard = [r for r in records if r["event"] == "heard"]
    print(f"{len(records)} eventos, {len(heard)} trechos com palavra de comando ({EVENTS})")
    by = {}
    for r in heard:
        key = (r.get("command") or "-", r.get("decision"))
        by.setdefault(key, []).append(r)
    for (command, decision), rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        confs = [r["conf"] for r in rs if "conf" in r]  # o parcial não traz confiança
        conf = f"{sum(confs) / len(confs):.2f}" if confs else "  - "
        level = sum(r["peak"] for r in rs) / len(rs)
        print(f"  {command:<16} {decision:<28} {len(rs):>4}x  conf média {conf}  pico médio {level:.0f}")
    keys = [r for r in records if r["event"] == "key"]
    if keys:
        failed = sum(1 for r in keys if not r["ok"])
        print(f"  teclas: {len(keys)} ({failed} falharam), média {sum(r['ms'] for r in keys) / len(keys):.0f} ms")
    pastes = [r for r in records if r["event"] == "paste"]
    if pastes:
        missed = sum(1 for r in pastes if r.get("outcome", "pasted" if r.get("seen") else "timeout") != "pasted")
        print(f"  colagens: {len(pastes)} ({missed} sem confirmação do FluidVoice)")
    for r in records[-10:]:
        rest = {k: v for k, v in r.items() if k not in ("ts", "event")}
        print(f"  {time.strftime('%H:%M:%S', time.localtime(r['ts']))} {r['event']} {rest}")


def check_words(words, model=MODEL):
    """Palavras que o modelo Vosk não conhece (essas nunca seriam reconhecidas).

    O Vosk só avisa no stderr nativo, então o teste roda num processo à parte."""
    code = (
        "import json, sys\n"
        "from vosk import KaldiRecognizer, Model, SetLogLevel\n"
        "SetLogLevel(0)\n"
        "m = Model(sys.argv[1])\n"
        "for w in sys.argv[2:]:\n"
        "    KaldiRecognizer(m, 16000, json.dumps([w, '[unk]'], ensure_ascii=False))\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(model), *words], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"não consegui carregar o modelo em {model}: rode ./install.sh")
    return [w for w in words if f"missing in vocabulary: {json.dumps(w)[1:-1]!r}" in result.stderr
            or f"missing in vocabulary: '{w}'" in result.stderr]


def parse_command(said, shortcuts):
    """Palavras de um trecho (antes de uma pausa) -> comando, ou None. Só vale como fim da fala."""
    if any(said[-len(p):] == p for p in SEND_PHRASES):
        return " ".join(SEND_PHRASE)
    if said == [STOP_WORD]:
        return STOP_WORD  # só sozinha: no meio do ditado, um Esc cancelaria o texto
    if " ".join(said) in shortcuts:
        return " ".join(said)  # atalho: só a frase sozinha, nunca no meio de outra fala
    if said[-1] in WAKE_WORDS:
        return WAKE_WORD
    return None


def min_conf(command, recording):
    """Confiança mínima para agir; recording = o FluidVoice está gravando agora."""
    if command == WAKE_WORD:
        return WAKE_MIN_CONF
    if command == " ".join(SEND_PHRASE) and not recording:
        return SEND_IDLE_MIN_CONF
    return MIN_CONF


def load_shortcuts():
    """Atalhos válidos do config.json, sem os que têm palavra que o modelo de voz não conhece."""
    shortcuts = clean_shortcuts(_config.get("shortcuts", {}))
    unknown = set(check_words(sorted({w for p in shortcuts for w in p.split()}))) if shortcuts else set()
    for phrase in [p for p in shortcuts if unknown.intersection(p.split())]:
        missing = ", ".join(sorted(unknown.intersection(phrase.split())))
        log(f'ignorei o atalho "{phrase}": o modelo de voz não conhece {missing}')
        del shortcuts[phrase]
    return shortcuts


def open_mic(callback):
    """Abre o microfone padrão de agora. O PortAudio guarda a lista de aparelhos de quando
    começou; reiniciá-lo antes faz um microfone novo (ou o padrão trocado) aparecer."""
    sd._terminate()
    sd._initialize()
    stream = sd.RawInputStream(samplerate=16000, blocksize=round(16000 * CHUNK_S), dtype="int16", channels=1,
                               callback=callback)
    stream.start()
    return stream, sd.query_devices(kind="input")["name"]


def main():
    global FLUID_SHORTCUT
    if sys.argv[1:2] == ["migrate-config"]:  # usado pelo install.sh
        legacy = read_legacy_agents(LEGACY_AGENTS_FILE) if LEGACY_AGENTS_FILE.exists() else None
        config, changed = migrate_config(_config, legacy)
        if changed:
            CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if legacy is not None:
                LEGACY_AGENTS_FILE.rename(LEGACY_AGENTS_FILE.with_name("agentes.tsv.migrated"))
            print(f'config.json: "agente ..." agora são atalhos do módulo OpenBot em "shortcuts" '
                  f'({len(config["shortcuts"])} atalhos)')
        return
    if sys.argv[1:2] == ["model-name"]:  # usado pelo install.sh: juno.py model-name en
        print(MODELS[sys.argv[2]])
        return
    if sys.argv[1:2] == ["check-words"]:  # juno.py check-words [--language en] palavra...
        words, model = sys.argv[2:], MODEL
        if words[:1] == ["--language"]:
            model, words = HERE / "models" / MODELS[words[1]], words[2:]
        missing = check_words([w.lower() for w in words], model)
        for w in missing:
            print(f"desconhecida: {w}")
        sys.exit(1 if missing else 0)
    if sys.argv[1:2] == ["stats"]:
        stats(int(sys.argv[2]) if len(sys.argv) > 2 else 500)
        return
    FLUID_SHORTCUT = shortcut_script(DICTATION_SHORTCUT)
    test_only = "--test" in sys.argv
    cmd_enter = "--cmd-enter" in sys.argv
    SetLogLevel(-1)
    if any(k in _config for k in LEGACY_KEYS) or LEGACY_AGENTS_FILE.exists():
        log('config.json no formato antigo (agente/agents): rode ./install.sh para virar atalhos em "shortcuts"')
    shortcuts = load_shortcuts()
    send_words = [w for p in SEND_PHRASES for w in p]
    shortcut_words = sorted({w for p in shortcuts for w in p.split()})
    grammar = json.dumps([*WAKE_WORDS, *send_words, STOP_WORD, *shortcut_words, "[unk]"], ensure_ascii=False)
    rec = KaldiRecognizer(Model(str(MODEL)), 16000, grammar)
    rec.SetWords(True)
    audio = queue.Queue()

    def callback(indata, frames, t, status):
        audio.put(bytes(indata))

    last = 0.0
    pending_send = None  # trechos de silêncio contados depois de "câmbio"; None = nada pendente
    peak = 0  # maior volume (RMS) do trecho atual, para saber se é preciso falar mais alto
    woke = False  # o nome do bot já disparou no resultado parcial deste trecho
    send_text = " ".join(SEND_PHRASE)
    commands = {*WAKE_WORDS, *send_words, STOP_WORD, *shortcut_words}
    stream, mic = open_mic(callback)
    wake_list = "/".join(WAKE_WORDS)
    send_list = "/".join(" ".join(p) for p in SEND_PHRASES)
    log(f'ouvindo "{wake_list}", "{send_list}", "{STOP_WORD}" e {len(shortcuts)} atalhos extras '
        f'no microfone "{mic}"...')
    event("start", test=test_only, shortcuts=len(shortcuts), min_conf=MIN_CONF, wake_min_conf=WAKE_MIN_CONF,
          send_idle_min_conf=SEND_IDLE_MIN_CONF, send_silence_s=SEND_SILENCE_S, mic=mic)
    while True:
        try:
            data = audio.get(timeout=MIC_STALL_S)
        except queue.Empty:
            if stream is not None:
                log(f'o microfone "{mic}" parou de mandar áudio; reabrindo no microfone padrão')
                stream.close(ignore_errors=True)
                stream, pending_send, peak, woke = None, None, 0, False
                rec.Reset()
            try:
                stream, mic = open_mic(callback)
            except sd.PortAudioError as exc:  # sem microfone nenhum: tenta de novo em MIC_STALL_S
                if mic is not None:
                    event("mic", outcome="sem microfone", mic=mic, error=str(exc))
                    mic = None
                continue
            event("mic", outcome="reaberto", mic=mic)
            log(f'ouvindo de novo no microfone "{mic}"')
            continue
        peak = max(peak, rms(data))
        final = rec.AcceptWaveform(data)
        words = json.loads(rec.Result()).get("result", []) if final else []
        partial = "" if final else json.loads(rec.PartialResult()).get("partial", "")
        if pending_send is not None:
            # Contado em áudio (não em relógio): só envia depois de N trechos sem fala.
            if words or partial:
                log(f'"{send_text}" no meio da fala; não enviei')
                event("send_cancelled", reason="fala depois de câmbio")
                pending_send = None
            else:
                pending_send += 1
                if pending_send >= SEND_SILENCE_CHUNKS:
                    pending_send = None
                    on_send(test_only, cmd_enter)
        if not final:
            # O nome do bot age já no parcial, ~0,7 s antes do final; o parcial não traz confiança.
            if partial and not woke and parse_command(partial.split(), shortcuts) == WAKE_WORD:
                woke = True
                recording = fluid_recording()
                heard = {"words": partial.split(), "peak": peak, "recording": recording}
                if recording:
                    event("heard", command=WAKE_WORD, decision="ignorado: já gravando", **heard)
                elif time.time() - last < COOLDOWN_S:
                    event("heard", command=WAKE_WORD, decision="ignorado: cooldown", **heard)
                else:
                    last = time.time()
                    event("heard", command=WAKE_WORD, decision="aceito (parcial)", **heard)
                    log(f'ouvi "{WAKE_WORD}" (parcial) volume={peak}')
                    on_wake(test_only)
            continue
        level, peak = peak, 0
        woke_in_segment, woke = woke, False
        if not words:
            continue
        said = [w["word"] for w in words]
        if not commands.intersection(said):
            continue
        conf = words[-1]["conf"]
        command = parse_command(said, shortcuts)
        if command == WAKE_WORD and woke_in_segment:
            continue  # já tratado no parcial
        recording = fluid_recording()
        heard = {"words": said, "conf": round(conf, 2), "peak": level, "recording": recording}
        if command is None:
            event("heard", command=None, decision="não é comando no fim da fala", **heard)
            continue
        if conf < min_conf(command, recording):
            event("heard", command=command, decision="ignorado: confiança baixa", **heard)
            continue
        if time.time() - last < COOLDOWN_S:
            event("heard", command=command, decision="ignorado: cooldown", **heard)
            continue
        last = time.time()
        event("heard", command=command, decision="aceito", **heard)
        log(f'ouvi "{command}" conf={conf:.2f} volume={level}')
        if command == send_text:
            pending_send = 0
        elif command == WAKE_WORD:
            on_wake(test_only)
        elif command == STOP_WORD:
            if not test_only:
                press(ESCAPE, "Esc")
        else:
            run_shortcut(test_only, command, shortcuts[command])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
