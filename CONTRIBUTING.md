# Contributing to Juno

Thanks for taking the time to contribute. Juno is a small, focused voice
command tool, and keeping it small is a feature — please read the **Non-goals**
in the [README](README.md#-non-goals) before proposing large additions.

## Ways to help

- **Report a bug** — open an issue with steps to reproduce, your macOS version,
  and the output of `.venv/bin/python juno.py --test --verbose` or `juno.py stats`.
- **Suggest a feature** — open an issue describing the problem you're trying to
  solve, not just the solution.
- **Send a pull request** — fixes, small improvements, and docs are all welcome.
  For anything large, open an issue first so we can agree on the approach.

## Development setup

Prerequisites: macOS with the Command Line Tools (`xcode-select --install`).

```sh
./install.sh
.venv/bin/python juno.py --test --verbose   # listens and logs, presses no keys
```

## Before you open a pull request

Every change must pass:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile juno.py
bash -n install.sh
swiftc -O -o /tmp/next-agent next-agent.swift   # if you changed the helper
```

## Project layout

```
juno.py            the listener (one file, on purpose)
next-agent.swift   OpenBot module helper ({"openbot": "next"})
install.sh         setup and configuration
tests/             unit tests for the pure parts
```

## Pull request guidelines

- Keep PRs focused — one logical change per PR.
- Don't break existing installs: `install.sh` must keep working when run again
  over an older version (it migrates, it doesn't overwrite `config.json`).
- Update the README when behavior changes.
- Add or update tests when you change behavior.
- Write clear commit messages explaining the *why*, not just the *what*.

## Code of conduct

Be respectful and constructive. We're here to build a good tool together.
