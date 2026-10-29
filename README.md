<div align="center">

# Juno

[![Latest release](https://img.shields.io/github/v/tag/aryrabelo/juno?sort=semver&label=release)](https://github.com/aryrabelo/juno/tags)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS-555)
![Built with Python + Vosk](https://img.shields.io/badge/built%20with-Python%20%2B%20Vosk-3776AB)

**Hands-free voice commands for macOS — say a name, dictate, say a word to send. Offline, in Brazilian Portuguese.**

⭐ Star Juno on GitHub — it's a one-person project and every star helps.

</div>

## Table of contents

- [💡 The idea](#-the-idea)
- [✨ Features](#-features)
- [🎙️ Voice commands](#️-voice-commands)
- [📦 Install](#-install)
- [⚙️ Configuration](#️-configuration)
- [🛠️ How it's built](#️-how-its-built)
- [🗂️ Project layout](#️-project-layout)
- [✅ Verification](#-verification)
- [🚫 Non-goals](#-non-goals)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

## 💡 The idea

Dictation apps turn your voice into text, but you still reach for the keyboard to
start and to send. Juno closes that loop. It listens for a few command words,
and only those: say the bot's name to start dictating in
[FluidVoice](https://github.com/altic-dev/FluidVoice), talk, then say the send
word and Juno presses Enter for you.

Recognition is **offline** with [Vosk](https://alphacephei.com/vosk/) and a small
**Brazilian Portuguese** model (`vosk-model-small-pt-0.3`). The audio never
leaves your Mac, and only the command words are recognized — everything else
is ignored as unknown.

## ✨ Features

- **Start dictation by name** — say `juno` (or any name you pick) and Juno presses
  FluidVoice's dictation shortcut.
- **Send by voice** — end with `câmbio` and a short pause: Juno stops dictation,
  waits for FluidVoice to paste the text, and presses Enter — in any app:
  terminals, browsers, and Electron apps included.
- **Cancel by voice** — say `desliga` on its own and Juno presses Esc.
- **Several words per command** — give the bot name or the send word as a list,
  e.g. `["atlas", "ata"]`, when the model mishears your first choice.
- **Safe send** — the send word only counts at the end of what you say, followed
  by silence; if you keep talking, nothing is sent.
- **Switch agents in [OpenBot](https://openbot.run)** — `agente <nickname>` brings
  OpenBot to the front and opens that agent; `agente próximo` (or `próximo agente`)
  opens the next chat with unread replies.
- **Your words** — the bot name, the send word, the dictation shortcut, and the
  agent nicknames all live in one `config.json`.
- **Start at login** — the installer can open Juno in a Terminal window on login.

> [!NOTE]
> Juno only understands **Brazilian Portuguese** today. Command words must exist in
> the Vosk model's vocabulary; the installer and `juno.py check-words` reject words
> it doesn't know.

## 🎙️ Voice commands

| You say | Juno does |
| --- | --- |
| **bot name** (default `juno`) | Presses the FluidVoice dictation shortcut |
| **send word** (default `câmbio`), at the end, then silence | Stops dictation, waits for the paste, presses Enter |
| **stop word** (default `desliga`), on its own | Presses Esc |
| **`agente <nickname>`**, on its own | Opens that agent in OpenBot (⌘K, paste name, Enter) |
| **`agente próximo`** or **`próximo agente`**, on its own | Opens the next OpenBot chat with unread replies and focuses its message box |

Agent commands are ignored while dictation is running.
The send word presses Enter even when nothing is being dictated, in whatever
window has focus.

## 📦 Install

### Prerequisites

- macOS with the Command Line Tools (`xcode-select --install`), which provide
  `/usr/bin/python3` and `swiftc`
- [FluidVoice](https://github.com/altic-dev/FluidVoice) with a dictation shortcut
  and **Spoken Send** set to the same send word (it removes the word from the text)

### Install Juno

```sh
git clone https://github.com/aryrabelo/juno.git
cd juno
./install.sh
```

The installer creates a Python environment, downloads the Portuguese model, asks
for the bot name, the send word and the FluidVoice shortcut, and offers to start
Juno at login. Running it again is safe: it updates without touching your
`config.json`.

### Try it

```sh
.venv/bin/python juno.py --test --verbose   # prints what it hears, presses nothing
.venv/bin/python juno.py                    # the real thing
```

> [!TIP]
> Grant permissions to the app that runs Juno (Terminal, if you use the login
> option): **Microphone** is asked on start, **Accessibility** is under System
> Settings › Privacy & Security, and **Automation › System Events** is asked on
> the first key press.

## ⚙️ Configuration

Everything is in `config.json` (created from `config.example.json`). Restart Juno
after editing it.

```json
{
  "wake_word": "juno",
  "send_word": "câmbio",
  "stop_word": "desliga",
  "dictation_shortcut": "option+command+.",
  "agent_word": "agente",
  "next_word": "próximo",
  "app": "OpenBot",
  "agents": { "vendas": "Vendas · OPS" }
}
```

| Key | What it is |
| --- | --- |
| `wake_word` | The bot's name; starts dictation. A word or a list, e.g. `["atlas", "ata"]` |
| `send_word` | Sends the message. A word or a list; FluidVoice removes only its own Spoken Send word from the text |
| `stop_word` | Presses Esc |
| `dictation_shortcut` | FluidVoice's shortcut, e.g. `control+option+.` |
| `agent_word` / `next_word` | The words in `agente <nickname>` and `agente próximo` |
| `app` | App whose ⌘K search opens agents |
| `agents` | Spoken nickname (one word) → agent name as typed in search |

Check a word before using it:

```sh
.venv/bin/python juno.py check-words sexta
```

### Advanced

- `juno.py stats` summarizes recent events: what was heard, accepted or ignored
  (and why), key timings. Events go to `~/Library/Logs/Juno/events.jsonl`; only
  command words are logged, never your dictation.
- `--cmd-enter` also presses ⌘Enter after Enter, for apps that send that way.

## 🛠️ How it's built

- **`juno.py`** — one Python script. A Vosk recognizer with a tiny grammar (just
  the command words) runs on 16 kHz audio from `sounddevice`. A command counts
  only as the last word before a pause, with high confidence.
- **Keys** — sent through `osascript` / System Events. Agent names are pasted
  (⌘V) instead of typed, so `·` and accents survive any keyboard layout.
- **FluidVoice state** — read from FluidVoice's own log, to know whether it is
  recording and when it finished pasting.
- **`next-agent.swift`** — a small helper compiled at install that reads the
  OpenBot sidebar through the Accessibility API, opens the first chat showing
  "N new replies", and focuses its message box so the next dictation lands there.

## 🗂️ Project layout

```
juno.py               the listener
next-agent.swift      "agente próximo" helper (compiled to bin/)
install.sh            setup, configuration, start at login
config.example.json   configuration template
tests/                unit tests for the pure parts
```

## ✅ Verification

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python juno.py check-words juno câmbio
bash -n install.sh
```

## 🚫 Non-goals

- No languages other than Brazilian Portuguese (yet)
- No dictation of its own — Juno drives FluidVoice
- No platforms other than macOS
- No cloud: no audio or text leaves the Mac

## 🤝 Contributing

Bug reports, feature ideas, and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, the checks a change must pass, and
the scope this project keeps (see **Non-goals** above). Security issues: please
follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## 📄 License

[MIT](LICENSE) © Ary Rabelo
