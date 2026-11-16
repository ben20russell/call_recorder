#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/swift/build.sh"

APP="$HOME/Applications/Call Recorder.app"
CONTENTS="$APP/Contents"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Helpers" "$CONTENTS/Resources"

swiftc -O \
    -framework SwiftUI \
    -framework WebKit \
    -framework ServiceManagement \
    "$ROOT/swift/MenuBarApp.swift" \
    -o "$CONTENTS/MacOS/Call Recorder"
cp "$ROOT/bin/callrec-capture" "$CONTENTS/Helpers/callrec-capture"

cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>Call Recorder</string>
    <key>CFBundleIdentifier</key><string>local.callrec.app</string>
    <key>CFBundleName</key><string>Call Recorder</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>14.2</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Call Recorder records your microphone during calls.</string>
    <key>NSAudioCaptureUsageDescription</key><string>Call Recorder records audio played by call apps.</string>
    <key>NSCalendarsFullAccessUsageDescription</key><string>Call Recorder reads your calendar to show upcoming calls.</string>
    <key>CALLREC_ROOT</key><string>$ROOT</string>
</dict>
</plist>
EOF

codesign --force --deep --sign - --identifier local.callrec.app "$APP"
printf 'Built %s\n' "$APP"
