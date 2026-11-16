#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT/bin"
swiftc -O \
    -framework AppKit \
    -framework AVFoundation \
    -framework AudioToolbox \
    -framework CoreAudio \
    -framework EventKit \
    "$ROOT/swift/capture_helper.swift" \
    "$ROOT/swift/system_audio.swift" \
    "$ROOT/swift/microphone_watch.swift" \
    "$ROOT/swift/popup.swift" \
    -o "$ROOT/bin/callrec-capture"
chmod 755 "$ROOT/bin/callrec-capture"
printf 'Built %s\n' "$ROOT/bin/callrec-capture"
