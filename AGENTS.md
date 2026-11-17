# AGENTS.md

## Project overview

Call Recorder is a local macOS app for recording calls, capturing each side separately, transcribing them, and turning the transcript plus your notes into structured meeting notes. The project is built around a Python package in `recorder/` with a local web UI/API in `recorder/server.py`, a CLI entry point in `recorder/__main__.py`, and a native macOS capture helper under `swift/`.

This repo is a reconstructed version of the original app from the supplied ZIP. It is not a generic web app; it is a privacy-focused local recorder that runs on macOS and stores recordings and notes in a user-managed folder.

## Important constraints

- Keep consent and privacy in mind: this app records people on calls and should never do so silently or outside the user's intent.
- The macOS helper and the Swift app are not buildable or testable in Linux; validate native changes on a supported Mac only.
- The Python web app is the main code path for development and testing in this environment.
- Do not add or remove project-level API surface unless required by the task. Keep changes surgical and aligned with the existing `recorder` package patterns.

## Repository layout

- `recorder/__main__.py` — CLI entrypoint (`./callrec ...`).
- `recorder/server.py` — local web UI, API routes, event loop, recording orchestration.
- `recorder/session.py` — recording session lifecycle, audio capture, persistence, resume/pause handling.
- `recorder/capture.py` — native capture integration and helper invocation.
- `recorder/transcribe.py` — transcription, Whisper integration, and live transcript logic.
- `recorder/search.py` — full-text/search behavior over calls, notes, transcripts, and metadata.
- `recorder/ask.py` / `recorder/mcp_server.py` — Claude/AI-assisted question answering and read-only call search tools.
- `recorder/config.py` — user config and secrets (`~/.callrec` plus Keychain env overrides).
- `recorder/calendar_src.py` / `recorder/meetings.py` — calendar integration and call metadata.
- `recorder/static/` — browser UI assets (HTML, JS, CSS).
- `swift/` — native macOS menu bar app and capture helper source.
- `chrome-extension/` — Google Meet microphone detection/metadata helper.
- `extension/` — Claude Desktop extension packaging.
- `tests/` — Python tests for logic like Google Calendar auth and OAuth handling.

## Local development workflow

Use the project entry point rather than ad hoc commands:

```sh
cd /workspaces/call_recorder
python3 -m venv .venv
.venv/bin/pip install -e .
./callrec ui
```

For the macOS app and helper:

```sh
.venv/bin/pip install -e '.[macos]'
./callrec login
./swift/build_app.sh
open "$HOME/Applications/Call Recorder.app"
```

For a local UI-only test without audio permissions:

```sh
CALLREC_SIMULATE=/path/to/testdir ./callrec ui
```

## Validation

Diagnose and fix any changes before finishing: if a change introduces a bug, regression, or validation failure, trace it to the affected code path, repair the root cause, and re-run the relevant checks.

Run the smallest relevant Python tests before finishing. The repo’s test suite is built around `unittest` and is usually run as:

```sh
cd /workspaces/call_recorder
python -m unittest discover -s tests -q
```

For non-native Python-only changes, prefer targeted test runs or specific modules over broad suites. Do not rely on macOS-only helpers in Linux validation.

## Coding expectations for agents

- Prefer small, surgical edits in the existing Python module layout.
- Keep naming consistent with current code: use existing config keys and helper functions before introducing new ones.
- Reuse existing `App`, `Session`, `Config`, `Meeting`, and search APIs instead of creating parallel abstractions.
- Preserve local-first behavior: config data lives under `~/.callrec`, recordings remain local, and browser/API access is limited to the local server.
- If you add a CLI command, update the command parser in `recorder/__main__.py` and keep behavior consistent with the CLI design.
- If you add or adjust config keys, update `DEFAULTS` in `recorder/config.py` and maintain compatibility with `load()`/`update()` merge semantics.
- If you change the web UI or API contract, keep the browser client and server API in sync.
- If you touch transcription or audio logic, think about pauses/resume, partial recording handling, and the `meeting.status` flow.
- If you touch calendar or meeting detection, preserve event-based recording and avoid changing the user’s selected calendar sources or auto-record behavior without updating tests or docs.

## Documentation and repo conventions

- The authoritative user-facing summary is in [README.md](./README.md).
- Respect the design described there: the app is menu-bar based, opens at login, and exposes a local web UI at `http://127.0.0.1:8765`.
- The `recorder` package intentionally bundles static browser assets from `recorder/static/`; do not split these into unrelated folders.
- Keep any code comments minimal and only where behavior is non-obvious.

## Recommended change checklist

Before finishing a task, verify:

1. The change matches the project’s local, privacy-first architecture.
2. Python entrypoints still run in the repo checkout.
3. The relevant tests or validation commands still pass.
4. Native Swift/Mac-specific code was not silently altered without a Mac validation path.
5. The README or inline docs stay accurate if behavior changed.

## Example commands

```sh
# Start the web UI
./callrec ui

# Run the app CLI for list/calendar/doctor entrypoints
./callrec list
./callrec calendar
./callrec doctor

# Validate Python tests
python -m unittest discover -s tests -q
```

## Summary

Treat this repo as a real application with a macOS-native recorder, a Python orchestration layer, and a browser front end. Build changes to preserve the product’s privacy guarantees, local-first behavior, and the existing call-recording workflow rather than adding unrelated abstractions or frameworks.
