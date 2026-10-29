#!/usr/bin/env python3
"""Juno: comandos de voz sem as mãos, com reconhecimento offline (Vosk).

"juno"   -> aperta o atalho do FluidVoice (⌥⌘.) para começar o ditado.
"câmbio" (no fim da fala, seguido de silêncio) -> se o FluidVoice estiver gravando, para o
            ditado (o Spoken Send dele tira "câmbio" do texto) e espera colar; depois aperta
            Enter (e, com --cmd-enter, também ⌘Enter).
"agente <apelido>" (fala sozinha, fora do ditado) -> abre a busca global do app (⌘K), digita o
            nome do agente cadastrado em agentes.tsv e aperta Enter.

Uso: .venv/bin/python juno.py [--test] [--cmd-enter] [--verbose]
     .venv/bin/python juno.py stats [N]   # resumo dos últimos N eventos (padrão 500)

Eventos (JSON por linha) em ~/Library/Logs/Juno/events.jsonl: cada trecho reconhecido, com
confiança e volume, se virou ação ou por que foi ignorado, e quanto cada ação levou. Só ficam
registradas as palavras de comando (o resto vira "[unk]"); nenhum texto ditado é gravado.
"""
import audioop
import json
import os
import queue
import subprocess
import sys
import time
from pathlib import Path

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "vosk-model-small-pt-0.3"
FLUID_LOG = Path.home() / "Library" / "Logs" / "Fluid" / "Fluid.log"
CONFIG_FILE = HERE / "config.json"  # criado pelo install.sh; ver config.example.json
_config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
WAKE_WORD = _config.get("wake_word", "juno").lower()  # o "nome do bot"
SEND_PHRASE = _config.get("send_word", "câmbio").lower().split()
COOLDOWN_S = 2.0
MIN_CONF = 0.95
MAX_RECORDING_S = 300
SEND_SILENCE_S = 0.8  # silêncio exigido depois da frase antes de enviar
PASTE_TIMEOUT_S = 10.0  # espera máxima pelo FluidVoice colar (volta antes, assim que ele cola)
NO_FIELD_GRACE_S = 1.5  # sem campo em foco, quanto ainda esperar por uma colagem
AGENT_WORD = _config.get("agent_word", "agente").lower()
AGENTS_FILE = HERE / "agentes.tsv"  # <apelido falado> TAB <texto da busca>
AGENT_APP = _config.get("agent_app", "OpenBot")  # app cuja busca (⌘K) abre o agente

FLUID_SHORTCUT = 'key code 47 using {command down, option down}'  # ⌥⌘.
ENTER = 'key code 36'
SEARCH = 'keystroke "k" using {command down}'
CMD_ENTER = 'key code 36 using {command down}'

CHUNK_S = 0.25  # blocksize 4000 a 16 kHz
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

    Retorna "pasted" (colou), "no_field" (não havia campo de texto em foco: ele não cola) ou
    "timeout" (sem resposta em PASTE_TIMEOUT_S)."""
    started = time.time()
    deadline = started + PASTE_TIMEOUT_S
    outcome = "timeout"
    while time.time() < deadline:
        try:
            with FLUID_LOG.open("rb") as f:
                f.seek(since)
                new = f.read()
        except OSError:
            break
        if b"PASTE_VERIFY" in new:
            outcome = "pasted"
            break
        # Sem campo editável o FluidVoice quase nunca cola; dá uma margem curta e desiste.
        if b"FOCUS_ASSESS unknown" in new and time.time() - started > NO_FIELD_GRACE_S:
            outcome = "no_field"
            break
        time.sleep(0.1)
    event("paste", outcome=outcome, waited_ms=round((time.time() - started) * 1000))
    return outcome


def load_agents():
    """agentes.tsv: <apelido> TAB <texto digitado na busca>. Linhas com # são comentário."""
    agents = {}
    if AGENTS_FILE.exists():
        for line in AGENTS_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "\t" in line:
                alias, target = line.split("\t", 1)
                alias = alias.strip().lower()
                target = "".join(c for c in target.strip() if c.isprintable())
                if not alias or " " in alias or not target:
                    log(f"agentes.tsv: ignorei a linha {line!r} (apelido de uma palavra e nome obrigatórios)")
                    continue
                agents[alias] = target
    return agents


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
        press(FLUID_SHORTCUT, "⌥⌘. (FluidVoice)")


def on_send(test_only, cmd_enter):
    if test_only:
        return
    if fluid_recording():
        # Para o ditado; o FluidVoice tira "câmbio" do texto ao colar.
        size = fluid_log_size()
        press(FLUID_SHORTCUT, "⌥⌘. (parar FluidVoice)")
        outcome = wait_paste(size)
        if outcome == "no_field":
            log("o FluidVoice não achou campo de texto em foco; não apertei Enter")
            return
        if outcome == "timeout":
            log("não vi o FluidVoice colar; envio mesmo assim")
    press(ENTER, "Enter")
    if cmd_enter:
        press(CMD_ENTER, "⌘Enter")


def on_agent(test_only, target):
    if fluid_recording():
        log("ditado em andamento; não troquei de agente")
        return
    if test_only:
        return
    # Só no app certo: ⌘K + Enter em outro app (navegador, Slack, editor) faria outra coisa.
    result = run_script(f'tell application "{AGENT_APP}" to activate')
    if result.returncode != 0:
        log(f"não consegui abrir o {AGENT_APP}: {result.stderr.strip()}")
        return
    time.sleep(0.4)
    front = run_script('tell application "System Events" to get name of first process whose frontmost is true')
    if front.stdout.strip() != AGENT_APP:
        log(f"{AGENT_APP} não ficou na frente ({front.stdout.strip() or '?'}); não troquei de agente")
        return
    press(SEARCH, "⌘K (busca)")
    time.sleep(0.4)
    type_text(target)
    time.sleep(0.8)
    press(ENTER, "Enter")


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
        conf = sum(r["conf"] for r in rs) / len(rs)
        level = sum(r["peak"] for r in rs) / len(rs)
        print(f"  {command:<16} {decision:<28} {len(rs):>4}x  conf média {conf:.2f}  pico médio {level:.0f}")
    keys = [r for r in records if r["event"] == "key"]
    if keys:
        failed = sum(1 for r in keys if not r["ok"])
        print(f"  teclas: {len(keys)} ({failed} falharam), média {sum(r['ms'] for r in keys) / len(keys):.0f} ms")
    pastes = [r for r in records if r["event"] == "paste"]
    if pastes:
        missed = sum(1 for r in pastes if not r["seen"])
        print(f"  colagens: {len(pastes)} ({missed} sem confirmação do FluidVoice)")
    for r in records[-10:]:
        rest = {k: v for k, v in r.items() if k not in ("ts", "event")}
        print(f"  {time.strftime('%H:%M:%S', time.localtime(r['ts']))} {r['event']} {rest}")


def check_words(words):
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
    result = subprocess.run([sys.executable, "-c", code, str(MODEL), *words], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"não consegui carregar o modelo em {MODEL}: rode ./install.sh")
    return [w for w in words if f"missing in vocabulary: {json.dumps(w)[1:-1]!r}" in result.stderr
            or f"missing in vocabulary: '{w}'" in result.stderr]


def main():
    if sys.argv[1:2] == ["check-words"]:
        missing = check_words([w.lower() for w in sys.argv[2:]])
        for w in missing:
            print(f"desconhecida: {w}")
        sys.exit(1 if missing else 0)
    if sys.argv[1:2] == ["stats"]:
        stats(int(sys.argv[2]) if len(sys.argv) > 2 else 500)
        return
    test_only = "--test" in sys.argv
    cmd_enter = "--cmd-enter" in sys.argv
    SetLogLevel(-1)
    agents = load_agents()
    grammar = json.dumps([WAKE_WORD, *SEND_PHRASE, AGENT_WORD, *agents, "[unk]"], ensure_ascii=False)
    rec = KaldiRecognizer(Model(str(MODEL)), 16000, grammar)
    rec.SetWords(True)
    audio = queue.Queue()

    def callback(indata, frames, t, status):
        audio.put(bytes(indata))

    last = 0.0
    pending_send = None  # trechos de silêncio contados depois de "câmbio"; None = nada pendente
    peak = 0  # maior volume (RMS) do trecho atual, para saber se é preciso falar mais alto
    send_text = " ".join(SEND_PHRASE)
    commands = {WAKE_WORD, AGENT_WORD, *SEND_PHRASE, *agents}
    with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=callback):
        log(f'ouvindo "{WAKE_WORD}", "{send_text}" e "{AGENT_WORD} <apelido>" ({len(agents)} agentes)...')
        event("start", test=test_only, agents=len(agents), min_conf=MIN_CONF, send_silence_s=SEND_SILENCE_S)
        while True:
            data = audio.get()
            peak = max(peak, audioop.rms(data, 2))
            final = rec.AcceptWaveform(data)
            words = json.loads(rec.Result()).get("result", []) if final else []
            if pending_send is not None:
                # Contado em áudio (não em relógio): só envia depois de N trechos sem fala.
                speaking = words if final else json.loads(rec.PartialResult()).get("partial")
                if speaking:
                    log(f'"{send_text}" no meio da fala; não enviei')
                    event("send_cancelled", reason="fala depois de câmbio")
                    pending_send = None
                else:
                    pending_send += 1
                    if pending_send >= SEND_SILENCE_CHUNKS:
                        pending_send = None
                        on_send(test_only, cmd_enter)
            if not final:
                continue
            level, peak = peak, 0
            if not words:
                continue
            said = [w["word"] for w in words]
            if not commands.intersection(said):
                continue
            conf = words[-1]["conf"]
            heard = {"words": said, "conf": round(conf, 2), "peak": level}

            # Só vale como fim da fala, antes de uma pausa.
            if said[-len(SEND_PHRASE):] == SEND_PHRASE:
                command = send_text
            elif len(said) == 2 and said[0] == AGENT_WORD and said[1] in agents:
                command = f"{AGENT_WORD} {said[1]}"  # só a frase sozinha, nunca no meio de outra fala
            elif said[-1] == WAKE_WORD:
                command = WAKE_WORD
            else:
                event("heard", command=None, decision="não é comando no fim da fala", **heard)
                continue
            if conf < MIN_CONF:
                event("heard", command=command, decision="ignorado: confiança baixa", **heard)
                continue
            if time.time() - last < COOLDOWN_S:
                event("heard", command=command, decision="ignorado: cooldown", **heard)
                continue
            last = time.time()
            event("heard", command=command, decision="aceito", recording=fluid_recording(), **heard)
            log(f'ouvi "{command}" conf={conf:.2f} volume={level}')
            if command == send_text:
                pending_send = 0
            elif command == WAKE_WORD:
                on_wake(test_only)
            else:
                target = agents[said[1]]
                log(f"-> {target}")
                on_agent(test_only, target)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
