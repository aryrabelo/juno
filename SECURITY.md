# Security Policy

## Reporting a vulnerability

If you find a security issue in Juno, please report it privately rather than
opening a public issue.

- Use GitHub's [private vulnerability reporting](https://github.com/aryrabelo/juno/security/advisories/new)
  for this repository, or
- Email the maintainer at **aryrabelo@gmail.com** with the details.

Please include:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- the version or commit you tested.

You can expect an initial response within a few days. Once a fix is available,
we'll coordinate disclosure with you.

## Scope

Juno is a local macOS script. It listens to the microphone and recognizes a
few command words offline; it sends key presses to the frontmost app through
System Events and reads FluidVoice's log file. It makes no network requests at
runtime and has no telemetry — the installer downloads Python packages and the
Vosk model over HTTPS. The most relevant security surface is the key presses it
sends (Accessibility and Automation permissions), the local event log, and
`config.json`: a `run` shortcut executes its shell command as you, and an `open`
shortcut launches an app, so treat `config.json` like a script you run.

## Supported versions

Juno is pre-1.0. Security fixes target the latest tag and `main`. Older
versions are not maintained.
