#!/bin/bash
# Build dist/MathShuttle.app (macOS) with PyInstaller.
#
#     ./build_app.sh                          # build (creates .venv-build on first run)
#     ./build_app.sh --zip                    # also dist/MathShuttle-<version>-macos-<arch>.zip for a release
#     PYTHON=/path/to/python3 ./build_app.sh  # build with that Python (default: python3; a build venv
#                                             # made from another Python is made again)
#
# The Python needs tkinter with Tk 8.6 or later (python.org, Homebrew's python-tk, or uv's builds;
# not Apple's /usr/bin/python3).  The app is not code-signed by a developer, only ad hoc: a copy
# built here opens normally; a downloaded one needs "Open Anyway" in System Settings > Privacy &
# Security the first time.  pandoc is not bundled: the app finds Homebrew's (brew install pandoc), and
# MacTeX's latex / dvipng for the preview.
set -euo pipefail
cd "$(dirname "$0")"

REQUESTED="${PYTHON:-}"
PYTHON="${PYTHON:-python3}"
ZIP=0
for arg in "$@"; do
    case "$arg" in
        --zip) ZIP=1 ;;
        *) echo "usage: $0 [--zip]" >&2; exit 2 ;;
    esac
done

if [ -n "$REQUESTED" ] && [ -x .venv-build/bin/python ]; then
    # PYTHON names the Python to build with: a build venv made from another one is made again
    want="$("$PYTHON" -c 'import sys; print(sys.base_prefix)')"
    have="$(.venv-build/bin/python -c 'import sys; print(sys.base_prefix)' 2>/dev/null || true)"
    if [ "$want" != "$have" ]; then
        echo "The build venv was made from ${have:-another Python}: making it again from $PYTHON..."
        rm -rf -- ./.venv-build
    fi
fi
if [ ! -x .venv-build/bin/python ]; then
    echo "Creating build venv from $PYTHON..."
    "$PYTHON" -m venv .venv-build
fi
PY=.venv-build/bin/python
"$PY" - <<'EOF'
import sys
import tkinter
if tkinter.TkVersion < 8.6:
    sys.exit("Tk %s is too old: build with a Python whose tkinter has Tk 8.6 or later "
             "(PYTHON=/path/to/python3 ./build_app.sh)" % tkinter.TkVersion)
tkinter.Tcl()  # the Tcl library must be found, or the app would not start either
EOF
"$PY" -m pip install --quiet --upgrade pip pyinstaller

VERSION="$("$PY" -c 'import mathshuttle; print(mathshuttle.__version__)')"
echo "Running PyInstaller (MathShuttle $VERSION)..."
"$PY" -m PyInstaller --noconfirm --clean --windowed --name MathShuttle \
    --icon assets/icon.icns --osx-bundle-identifier io.github.infinitr0us.mathshuttle \
    --distpath dist --workpath build \
    --exclude-module numpy --exclude-module PIL --exclude-module pytest \
    --add-data "mathshuttle/sv_theme:mathshuttle/sv_theme" \
    launcher.py

APP=dist/MathShuttle.app
PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "MIT licence; see LICENSE" "$PLIST"
plutil -replace LSApplicationCategoryType -string "public.app-category.productivity" "$PLIST"
codesign --force --deep --sign - "$APP"  # the Info.plist changed: sign (ad hoc) again
codesign --verify --deep --strict "$APP"
echo "Built $(cd dist && pwd)/MathShuttle.app"

if [ "$ZIP" = 1 ]; then
    "$PY" tools/make_release.py macos
fi
