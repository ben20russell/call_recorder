#!/bin/bash
set -o pipefail

claude="$(command -v claude || true)"
if [[ -z "$claude" ]]; then
  for candidate in \
    "$HOME/.local/bin/claude" \
    "$HOME/.claude/local/claude" \
    "$HOME"/Library/Application\ Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude; do
    if [[ -x "$candidate" ]]; then
      claude="$candidate"
      break
    fi
  done
fi

if [[ -z "$claude" ]]; then
  echo "Installing Claude Code from Anthropic..."
  if ! curl -fsSL https://claude.ai/install.sh | bash; then
    echo "Claude Code installation failed. See https://code.claude.com/docs/en/setup"
    read -r -p "Press Return to close this window."
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
  claude="$(command -v claude || true)"
fi

if [[ -z "$claude" ]]; then
  echo "Claude Code was installed but could not be found at ~/.local/bin/claude."
  read -r -p "Press Return to close this window."
  exit 1
fi

echo "Signing in to Claude. Choose your Claude account in the browser."
"$claude" auth login
result=$?
if [[ $result -eq 0 ]]; then
  echo "Sign-in finished. You can close this window."
else
  echo "Sign-in did not complete (exit status $result). You can try again from Call Recorder settings."
fi
read -r -p "Press Return to close this window."
exit "$result"
