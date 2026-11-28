#!/usr/bin/env bash
# Run the device's software on a computer (a Mac, or a Linux PC with a desktop) to try it with a phone on
# the same Wi-Fi: the settings page, the character in a window, recording a gesture with the phone, the
# live camera and face-tracking apps (VTube Studio, MeowFace, iFacialMocap). See docs/35-try-on-a-computer.md.
#
#   device/try-on-a-computer.sh            set up (the first time takes a few minutes), then run until Ctrl-C
#   device/try-on-a-computer.sh --check    set up, start, check that both halves answer, then stop
#
# Needs Python 3.9 or newer, Rust (https://rustup.rs) and, on a Mac, Apple's command line tools
# (xcode-select --install). Everything it makes goes in ~/.companion-try (characters and settings in its
# data folder); delete that folder to start again. There is no microphone or speaker (they are the
# device's), and it never looks for device updates.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TRY="${COMPANION_TRY:-$HOME/.companion-try}"
DATA="$TRY/data"
VENV="$TRY/venv"
PORT="${PORT:-8080}"
TLS_PORT="${TLS_PORT:-8443}"
SOCKET="/tmp/companion-try-$(id -u).sock"          # short: a Mac allows 104 characters in a socket's path
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

say() { printf '\n== %s\n' "$*"; }

# ----- the tools it needs
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for candidate in python3.13 python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    done
fi
if [ -z "$PY" ]; then
    echo "Python 3.9 or newer is needed. On a Mac: xcode-select --install (or https://www.python.org/downloads/)."
    exit 1
fi
export PATH="$HOME/.cargo/bin:$PATH"
if ! command -v cargo >/dev/null 2>&1; then
    echo "Rust is needed to build the screen's program. Install it from https://rustup.rs, then run this again:"
    echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
    echo "note: no openssl here, so the live camera (it needs the device's own HTTPS) cannot be turned on"
fi

# ----- Python libraries, in their own environment
say "Python libraries ($("$PY" --version 2>&1))"
mkdir -p "$TRY" "$DATA"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install -q --disable-pip-version-check aiohttp numpy pillow qrcode

# ----- the screen's program (the stage)
say "Building the screen's program (the first time takes a few minutes)"
export CARGO_TARGET_DIR="$TRY/stage-build"
if ! (cd "$REPO/stage" && cargo build --release --quiet) >"$TRY/build.log" 2>&1; then
    tail -n 40 "$TRY/build.log"
    echo "The build failed; the whole log is in $TRY/build.log"
    exit 1
fi
STAGE="$CARGO_TARGET_DIR/release/stage"

# ----- the phone's tracking files (MediaPipe, checked against fixed hashes)
if [ ! -d "$DATA/models/mocap" ]; then
    say "The phone's tracking files"
    PYTHONPATH="$REPO/core" "$VENV/bin/python" -m companion.mocap_assets "$DATA/models/mocap" \
        || echo "note: not fetched now; the device fetches them when a phone first asks"
fi

# ----- run both halves
LAN="$(PYTHONPATH="$REPO/core" "$VENV/bin/python" -c 'from companion.__main__ import lan_address; print(lan_address())')"
GL=auto
[ "$(uname -s)" = Darwin ] && GL=desktop                # a Mac has desktop OpenGL only
rm -f "$SOCKET"
CORE_PID=""
LOOP_PID=""
stop() {
    trap - EXIT
    if [ -n "$LOOP_PID" ]; then
        pkill -P "$LOOP_PID" 2>/dev/null || true         # the screen's program, then what restarts it
        kill "$LOOP_PID" 2>/dev/null || true
    fi
    [ -z "$CORE_PID" ] || kill "$CORE_PID" 2>/dev/null || true
    rm -f "$SOCKET"
}
trap stop EXIT
trap 'exit 130' INT TERM

say "Starting"
PYTHONPATH="$REPO/core" "$VENV/bin/python" -m companion --data "$DATA" --host 0.0.0.0 --port "$PORT" \
    --tls-port "$TLS_PORT" --stage-socket "$SOCKET" --no-audio --button none --no-updates \
    >"$TRY/core.log" 2>&1 &
CORE_PID=$!

(   # The screen's program shows what the core points it at, and starts again when asked to (as on the device).
    while true; do
        if [ -e "$DATA/current.inp" ]; then
            "$STAGE" "$DATA/current.inp" --socket "$SOCKET" --gl "$GL" --no-stdin --size 450x800 \
                --map "$DATA/current-map.json" --settings "$DATA/current-stage.json" || true
        fi
        sleep 1
    done
) >"$TRY/stage.log" 2>&1 &
LOOP_PID=$!

if [ "$CHECK" = 1 ]; then
    ok=0
    for _ in $(seq 1 60); do
        if curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && curl -fs -o /dev/null "http://127.0.0.1:$PORT/r" \
            && grep -q '"event": *"ready"' "$TRY/stage.log"; then
            ok=1
            break
        fi
        sleep 1
    done
    if [ "$ok" = 1 ]; then
        echo "PASS  the settings page and the phone's page answer, and the screen's program is drawing"
        grep -o '"opengl": *"[^"]*"' "$TRY/stage.log" | head -n 1
        exit 0
    fi
    echo "FAIL  it did not come up within a minute"
    echo "--- core"; tail -n 40 "$TRY/core.log"
    echo "--- stage"; tail -n 40 "$TRY/stage.log"
    exit 1
fi

cat <<EOF

  Running. On this computer, open:   http://localhost:$PORT
  On a phone on the same Wi-Fi:       http://${LAN:-THIS-COMPUTER'S-ADDRESS}:$PORT

  The character's window opens once there is something to show. Logs: $TRY/core.log and stage.log.
  Press Ctrl-C here to stop.
EOF
wait
