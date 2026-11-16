#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="$ROOT/dist/Call Recorder.mcpb"
mkdir -p "$ROOT/dist"

python3 - "$ROOT/extension" "$OUTPUT" <<'PY'
import sys
import zipfile
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source))
print(destination)
PY
