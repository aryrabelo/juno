#!/usr/bin/env python3
"""Juno: comandos de voz sem as mãos, com reconhecimento offline (Vosk).

"juno"   -> aperta o atalho do FluidVoice (⌥⌘.) para começar o ditado.
"câmbio" (no fim da fala, seguido de silêncio) -> se o FluidVoice estiver gravando, para o
            ditado (o Spoken Send dele tira "câmbio" do texto) e espera colar; depois aperta
            Enter (e, com --cmd-enter, também ⌘Enter).

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
SEND_SILENCE_S = 1.5  # silêncio exigido depois da frase antes de enviar
PASTE_WAIT_S = 1.5

FLUID_SHORTCUT = 'key code 47 using {command down, option down}'  # ⌥⌘.
ENTER = 'key code 36'
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
    return 0 <= age < MAX_RECORDING_S


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
        press(FLUID_SHORTCUT, "⌥⌘. (parar FluidVoice)")
        time.sleep(PASTE_WAIT_S)
    press(ENTER, "Enter")
    if cmd_enter:
        press(CMD_ENTER, "⌘Enter")


def main():
    test_only = "--test" in sys.argv
    cmd_enter = "--cmd-enter" in sys.argv
    SetLogLevel(-1)
    grammar = json.dumps([WAKE_WORD, *SEND_PHRASE, "[unk]"], ensure_ascii=False)
    rec = KaldiRecognizer(Model(str(MODEL)), 16000, grammar)
    rec.SetWords(True)
    audio = queue.Queue()

    def callback(indata, frames, t, status):
        audio.put(bytes(indata))

    last = 0.0
    pending_send = None  # frase de envio ouvida; só envia se vier silêncio depois
    send_text = " ".join(SEND_PHRASE)
    with sd.RawInputStream(samplerate=16000, blocksize=4000, dtype="int16", channels=1, callback=callback):
        log(f'ouvindo "{WAKE_WORD}" e "{send_text}"...')
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
            elif words[-1]["word"] == WAKE_WORD:
                last = time.time()
                log(f'ouvi "{WAKE_WORD}" conf={words[-1]["conf"]:.2f}')
                on_wake(test_only)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
