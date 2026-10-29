#!/usr/bin/env python3
"""Juno: comandos de voz sem as mãos, com reconhecimento offline (Vosk).

"juno"   -> aperta o atalho do FluidVoice (⌥⌘.) para começar o ditado.
"câmbio" (no fim da fala, seguido de silêncio) -> se o FluidVoice estiver gravando, para o
            ditado (o Spoken Send dele tira "câmbio" do texto) e espera colar; depois aperta
            Enter (e, com --cmd-enter, também ⌘Enter).
"agente <apelido>" (fala sozinha, fora do ditado) -> abre a busca global do app (⌘K), digita o
            nome do agente cadastrado em agentes.tsv e aperta Enter.

Uso: .venv/bin/python juno.py [--test] [--cmd-enter]
"""
import json
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
WAKE_WORD = "juno"
SEND_PHRASE = ["câmbio"]
COOLDOWN_S = 2.0
MIN_CONF = 0.95
MAX_RECORDING_S = 300
SEND_SILENCE_S = 0.8  # silêncio exigido depois da frase antes de enviar
PASTE_TIMEOUT_S = 10.0  # espera máxima pelo FluidVoice colar (volta antes, assim que ele cola)
AGENT_WORD = "agente"
AGENTS_FILE = HERE / "agentes.tsv"  # <apelido falado> TAB <texto da busca>
AGENT_APP = "OpenBot"  # app cuja busca (⌘K) abre o agente

FLUID_SHORTCUT = 'key code 47 using {command down, option down}'  # ⌥⌘.
ENTER = 'key code 36'
SEARCH = 'keystroke "k" using {command down}'
CMD_ENTER = 'key code 36 using {command down}'


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def press(keys, label):
    script = f'tell application "System Events" to {keys}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"falha ao apertar {label}: {result.stderr.strip()}")
    else:
        log(f"apertei {label}")


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
    """Espera o FluidVoice registrar a colagem (PASTE_VERIFY) depois de `since` bytes do log."""
    deadline = time.time() + PASTE_TIMEOUT_S
    while time.time() < deadline:
        try:
            with FLUID_LOG.open("rb") as f:
                f.seek(since)
                if b"PASTE_VERIFY" in f.read():
                    return
        except OSError:
            break
        time.sleep(0.1)
    log("não vi o FluidVoice colar; envio mesmo assim")


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
        wait_paste(size)
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


def main():
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
    pending_send = None  # frase de envio ouvida; só envia se vier silêncio depois
    send_text = " ".join(SEND_PHRASE)
    with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=callback):
        log(f'ouvindo "{WAKE_WORD}", "{send_text}" e "{AGENT_WORD} <apelido>" ({len(agents)} agentes)...')
        while True:
            final = rec.AcceptWaveform(audio.get())
            if pending_send:
                if not final and json.loads(rec.PartialResult()).get("partial"):
                    log(f'"{send_text}" no meio da fala; não enviei')
                    pending_send = None
                elif time.time() - pending_send >= SEND_SILENCE_S:
                    pending_send = None
                    on_send(test_only, cmd_enter)
            if not final:
                continue
            words = json.loads(rec.Result()).get("result", [])
            if not words or words[-1]["conf"] < MIN_CONF or time.time() - last < COOLDOWN_S:
                continue
            # Só vale como fim da fala, antes de uma pausa.
            tail = [w["word"] for w in words[-len(SEND_PHRASE):]]
            if tail == SEND_PHRASE:
                last = time.time()
                log(f'ouvi "{send_text}"')
                pending_send = time.time()
            elif len(words) == 2 and words[0]["word"] == AGENT_WORD and words[1]["word"] in agents:
                # Só a frase sozinha ("agente pessoal"), nunca no meio de outra fala.
                last = time.time()
                target = agents[words[1]["word"]]
                log(f'ouvi "{AGENT_WORD} {words[1]["word"]}" -> {target}')
                on_agent(test_only, target)
            elif words[-1]["word"] == WAKE_WORD:
                last = time.time()
                log(f'ouvi "{WAKE_WORD}" conf={words[-1]["conf"]:.2f}')
                on_wake(test_only)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
