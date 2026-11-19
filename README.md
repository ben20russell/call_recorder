# Call Recorder

This project contains the Python call-notes application and web interface from the supplied
`recorder.zip`, plus a reconstructed macOS menu bar app and native capture helper.

Tell everyone on a call that it is being recorded and get their agreement where required. The
recorder does not announce itself to other participants.

## Run the web app

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
./callrec ui
```

The web app is served only on `127.0.0.1:8765`. For development and UI testing without audio
permissions, set `CALLREC_SIMULATE` to a directory containing `mic.wav` and/or `system.wav`.

## Google Calendar

Run Call Recorder on your Mac for Google sign-in. In Settings → Calendar, choose **Google
Calendar**. In Google Cloud, enable the Google Calendar API, configure the OAuth consent screen
(add `ben20russell@gmail.com` as a test user if the app is in Testing mode), and create an OAuth
client for a web application. Add the redirect URI shown in Settings to the client's authorized
redirect URIs. Paste its client ID and secret, then choose **Sign in with Google** and select
`ben20russell@gmail.com`. The Codespaces forwarding domain may not satisfy Google's domain
verification requirements. Call Recorder requests read-only calendar access; the refresh token is
stored in macOS Keychain (or a user-only `~/.callrec/secrets.json` on systems without Keychain).

## OpenAI API

In Settings → OpenAI, enter your key and select an OpenAI model. The key is stored in macOS Keychain (or a user-only
`~/.callrec/secrets.json` on systems without Keychain); `OPENAI_API_KEY` can also be used instead.

## macOS app

On macOS 14.2 or newer with Xcode Command Line Tools installed:

```sh
.venv/bin/pip install -e '.[macos]'
./callrec login
./swift/build_app.sh
open "$HOME/Applications/Call Recorder.app"
```

The app starts the local server from this checkout and adds itself to Login Items. Install Python
dependencies into the checkout's `.venv` first, as shown above.

## Native capture

The supplied ZIPs did not include the original Swift app or `bin/` capture helper, so the native
components have been reconstructed. On macOS 14.2 or newer, the helper records the selected
microphone to `mic.pcm` and attempts to capture system audio to `system.pcm`; if system capture is
unavailable, it reports a warning and continues with microphone audio. System audio capture
requires permission in System Settings → Privacy & Security → Screen & System Audio Recording.
Microphone-use detection requires macOS 14.4 or newer. Calendar access and interactive desktop
prompts are also handled by the helper.

The Swift code cannot be built or exercised on Linux. Build and verify the native app on a
supported Mac using the commands above, then grant the requested permissions in System Settings.

The `macos` extra installs `mlx-whisper` and `sherpa-onnx`; transcription and voice identification
are intended for Apple Silicon Macs. MCP support requires the optional `mcp` dependency.

## Optional integrations

The supplied Meet extension is in [chrome-extension/](./chrome-extension/).
After starting the app, use Settings → Google Meet names to find the installed copy, then load it
unpacked from `~/Library/Application Support/Call Recorder/Chrome Extension` in Chrome.

The Claude Desktop extension is in [extension/](./extension/). Build its
installer with `./extension/build.sh`; this writes `dist/Call Recorder.mcpb`. The local Call Recorder
app must be running for its read-only tools to work. Install the server dependency with
`.venv/bin/pip install -e '.[mcp]'`.
