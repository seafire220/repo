#!/bin/bash
# Turn a fresh Raspberry Pi OS Lite (64-bit) into the character device.
#
#   sudo ./device/install.sh [--stage-binary PATH] [--no-model] [--image] [--version V]
#
# --image prepares a ready-made device image (device/build-image.sh uses it):
# no login prompt, no cloud-init, hostname "companion". --version names the
# software's version (default: core/companion/VERSION); sold devices compare it
# with updates (docs/32-updates.md).
#
# Run it from a copy of this repository on the Pi. It installs the packages
# the device needs, the core service, the stage program and the system
# settings, then asks you to reboot. Running it again updates the software
# and leaves your characters, memories, keys and settings alone.
#
# The stage program is built for the Pi on a Linux PC (see stage/README.md)
# and copied to stage/target/aarch64-unknown-linux-gnu/release/stage, or
# given with --stage-binary. If it is missing and Rust is installed on the
# Pi, it is built here (slow on a 1 GB Pi).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/companion
DATA=/var/lib/companion
STAGE_BINARY="$REPO/stage/target/aarch64-unknown-linux-gnu/release/stage"
GET_MODEL=1
IMAGE=0
VERSION=""
# On-device speech recognition model: Moonshine tiny, English, 8-bit (MIT
# licence, Useful Sensors), packaged for sherpa-onnx. About 120 MB.
MODEL_URL="https://huggingface.co/csukuangfj/sherpa-onnx-moonshine-tiny-en-int8/resolve/main"
MODEL_FILES="preprocess.onnx encode.int8.onnx uncached_decode.int8.onnx cached_decode.int8.onnx tokens.txt LICENSE"
# On-device voice: Piper "VCTK" medium (109 speakers with British, Irish, American, Australian and
# other accents), trained on the CSTR VCTK Corpus (CC BY 4.0, University of Edinburgh) from a base voice
# trained on NVIDIA's Hi-Fi TTS (CC BY 4.0): no research-only data anywhere in it (docs/34-licences.md).
# Piper's own files at a fixed commit, checked by their SHA-256, then prepared for sherpa-onnx
# (core/companion/piper_voice.py). About 80 MB. Phonemes come from espeak-ng (an operating-system
# package, run as a separate program).
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/e9c8a39444359388adaa94255becb37a5a2b8e51/en/en_GB/vctk/medium"
VOICE_MODEL="en_GB-vctk-medium.onnx"
VOICE_SHA256="fc3d92d266b1a6c03af2a38215e0eee430ca7fb360f641d7c3dcb17c7670c0f3"
VOICE_FILES="$VOICE_MODEL $VOICE_MODEL.json MODEL_CARD"
# Matching memories by meaning: Model2Vec potion-base-8M (MIT, Minish Lab; distilled from BAAI's
# bge-base-en-v1.5, MIT). A table of word-piece vectors read with numpy; about 30 MB.
MEANING_URL="https://huggingface.co/minishlab/potion-base-8M/resolve/main"
MEANING_FILES="model.safetensors tokenizer.json config.json README.md"
# The wake word: sherpa-onnx's English keyword-spotting model (zipformer, 3.3 million parameters, trained
# on GigaSpeech by the k2-fsa project; Apache-2.0). About 5 MB of it is kept.
WAKE_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2"

while [ $# -gt 0 ]; do
    case "$1" in
        --stage-binary) STAGE_BINARY="$2"; shift 2 ;;
        --no-model) GET_MODEL=0; shift ;;
        --image) IMAGE=1; shift ;;
        --version) VERSION="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
if [ "$(uname -m)" != "aarch64" ]; then
    echo "note: this is not a 64-bit ARM system; continuing for testing only" >&2
fi

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
    cage python3 python3-venv python3-numpy python3-aiohttp python3-pil \
    alsa-utils network-manager dnsmasq-base polkitd fonts-dejavu-core python3-qrcode avahi-daemon libnss-mdns \
    libegl1 libgles2 libgl1-mesa-dri libwayland-client0 libwayland-egl1 libxkbcommon0 \
    espeak-ng-data espeak-ng ca-certificates curl

say "Creating the service user"
if ! id companion >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin companion
fi
for group in video render audio input systemd-journal; do
    getent group "$group" >/dev/null && usermod -aG "$group" companion
done
install -d -o companion -g companion -m 0750 "$DATA"

[ -n "$VERSION" ] || VERSION="$(cat "$REPO/core/companion/VERSION" 2>/dev/null || echo 0)"
say "Installing the software (version $VERSION) in $APP"
# Each version lives in releases/<version>; current points at the running one, previous at the one
# before, and core, bin and share point into current. Updates switch these links (docs/32-updates.md).
REL="$APP/releases/$VERSION"
STAGING="$REL.new"
install -d "$APP/releases"
rm -rf "$STAGING"
install -d "$STAGING/bin" "$STAGING/share/vrma" "$STAGING/system"
cp -r "$REPO/core" "$STAGING/core"
rm -rf "$STAGING/core/tests"
find "$STAGING/core" -name __pycache__ -prune -exec rm -rf {} +
printf '%s\n' "$VERSION" > "$STAGING/core/companion/VERSION"

if [ ! -x "$STAGE_BINARY" ]; then
    if command -v cargo >/dev/null; then
        say "Building the stage program (this takes a while)"
        (cd "$REPO/stage" && cargo build --release)
        STAGE_BINARY="$REPO/stage/target/release/stage"
    else
        echo "No stage program found at $STAGE_BINARY. Build it on a PC (see stage/README.md) and pass --stage-binary." >&2
        exit 1
    fi
fi
install -m 0755 "$STAGE_BINARY" "$STAGING/bin/stage"
# The USB connection to a computer (a network adapter on the USB-C port).
install -m 0755 "$REPO/device/usb/companion-usb-gadget" "$STAGING/bin/companion-usb-gadget"
# Power-cut protection (used by images built with it, device/build-image.sh): the data partition's check
# at each start, and its growth at the first.
install -m 0755 "$REPO/device/readonly/companion-data-check" "$REPO/device/readonly/companion-firstboot" "$STAGING/bin/"
# The built-in full-body animations for 3D (VRM) avatars, read by the stage when one is set to them.
install -m 0644 "$REPO"/stage/motions/vrma/*.vrma "$STAGING/share/vrma/"
# Where updates come from, and the key they must be signed with (the owner's: device/release.py keygen).
install -m 0644 "$REPO/device/config/update.json" "$STAGING/share/update.json"
if [ -f "$REPO/device/config/update-key.pem" ]; then
    install -m 0644 "$REPO/device/config/update-key.pem" "$STAGING/share/update-key.pem"
else
    echo "note: no device/config/update-key.pem, so this software cannot install updates (device/release.py keygen)" >&2
fi
[ -f "$REPO/THIRD_PARTY.md" ] && install -m 0644 "$REPO/THIRD_PARTY.md" "$STAGING/share/THIRD_PARTY.md"
# The licences of the libraries built into the stage program, for the settings page's "Licences and notices".
install -d "$STAGING/share/licenses"
install -m 0644 "$REPO/stage/THIRD_PARTY_LICENSES.txt" "$STAGING/share/licenses/stage.txt"
# This version's own system files: the updater installs a version's when it switches to it.
install -m 0644 "$REPO/device/systemd/companion-core.service" "$REPO/device/systemd/companion-stage.service" \
    "$REPO/device/systemd/companion-update.service" "$REPO/device/systemd/companion-update.path" \
    "$REPO/device/systemd/companion-update-boot.service" "$REPO/device/systemd/companion-usb.service" \
    "$REPO/device/systemd/companion-usb-dhcp.service" "$REPO/device/config/99-companion-backlight.rules" \
    "$REPO/device/readonly/companion-var-dirs.service" "$REPO/device/readonly/companion-data-check.service" \
    "$REPO/device/readonly/companion-firstboot.service" "$STAGING/system/"
install -m 0600 "$REPO/device/config/companion-usb.nmconnection" "$STAGING/system/"
install -m 0644 "$REPO/device/config/polkit-companion.rules" "$STAGING/system/50-companion.rules"
install -m 0755 "$REPO/device/updater/companion-update.py" "$STAGING/system/companion-update.py"

# Software installed before versions had their own folders becomes the version before this one.
if [ -d "$APP/core" ] && [ ! -L "$APP/core" ]; then
    install -d "$APP/releases/earlier"
    for part in core bin share; do
        if [ -d "$APP/$part" ] && [ ! -L "$APP/$part" ]; then mv "$APP/$part" "$APP/releases/earlier/$part"; fi
    done
    rm -rf "$APP/core.previous" "$APP/bin.previous"
    ln -sfn releases/earlier "$APP/current"
fi
OLD="$(readlink "$APP/current" 2>/dev/null || true)"
rm -rf "$REL"
mv "$STAGING" "$REL"
ln -sfn "releases/$VERSION" "$APP/current.new" && mv -T "$APP/current.new" "$APP/current"
for part in core bin share; do ln -sfn "current/$part" "$APP/$part"; done
if [ -n "$OLD" ] && [ "$OLD" != "releases/$VERSION" ]; then ln -sfn "$OLD" "$APP/previous"; fi
install -d "$APP/updater"
install -m 0755 "$REPO/device/updater/companion-update.py" "$APP/updater/companion-update.py"

say "Setting up Python"
if [ ! -x "$APP/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$APP/venv"
fi
"$APP/venv/bin/pip" install -q --upgrade pip
# sherpa-onnx: on-device speech recognition and the wake word; sentencepiece spells the wake words as
# the wake-word model's word pieces. anthropic: only loaded if Claude is chosen. sherpa-onnx from 2.0 on
# has no espeak-ng (GPL-3.0) built in: the voice's phonemes come from the espeak-ng program instead
# (core/companion/phonemes.py), so nothing under the GPL is linked into the device's own software.
"$APP/venv/bin/pip" install -q "sherpa-onnx>=2.0.0,<3" sentencepiece anthropic || echo "warning: optional Python packages did not install" >&2
# The speech library brings its own copy of ALSA's sound library (LGPL-2.1). Point it at the operating
# system's instead (the same library, newer): then every LGPL library on the device is the operating
# system's own, with its source in the operating system's archive (docs/34-licences.md).
SYS_ASOUND="$(ldconfig -p | awk '/libasound\.so\.2 / {print $NF; exit}')"
if [ -n "$SYS_ASOUND" ]; then
    for lib in "$APP"/venv/lib/python3*/site-packages/*.libs/libasound-*.so*; do
        if [ -f "$lib" ] && [ ! -L "$lib" ]; then ln -sf "$SYS_ASOUND" "$lib"; fi
    done
fi

if [ "$GET_MODEL" = 1 ] && [ ! -d "$DATA/models/stt" ]; then
    say "Downloading the on-device speech model"
    tmp="$(mktemp -d)"
    ok=1
    for f in $MODEL_FILES; do
        curl -fsSL "$MODEL_URL/$f" -o "$tmp/$f" || { ok=0; break; }
    done
    if [ "$ok" = 1 ]; then
        install -d -o companion -g companion "$DATA/models"
        mv "$tmp" "$DATA/models/stt"
        chown -R companion:companion "$DATA/models"
    else
        echo "warning: could not download the speech model; choose a cloud speech service in the settings page" >&2
        rm -rf "$tmp"
    fi
fi

if [ "$GET_MODEL" = 1 ] && [ ! -d "$DATA/models/tts" ]; then
    say "Downloading the on-device voice"
    tmp="$(mktemp -d)"
    ok=1
    for f in $VOICE_FILES; do
        curl -fsSL "$VOICE_URL/$f" -o "$tmp/$f" || { ok=0; break; }
    done
    if [ "$ok" = 1 ] && ! echo "$VOICE_SHA256  $tmp/$VOICE_MODEL" | sha256sum -c --quiet; then
        echo "warning: the voice downloaded is not the one checked (SHA-256 differs)" >&2
        ok=0
    fi
    if [ "$ok" = 1 ] && PYTHONPATH="$REPO/core" python3 -m companion.piper_voice "$tmp/$VOICE_MODEL" \
            "$tmp/$VOICE_MODEL.json" "$tmp/tts" >/dev/null; then
        mv "$tmp/MODEL_CARD" "$tmp/tts/MODEL_CARD"
        install -d -o companion -g companion "$DATA/models"
        mv "$tmp/tts" "$DATA/models/tts"
        rm -rf "$tmp"
        chown -R companion:companion "$DATA/models"
    else
        echo "warning: could not download the on-device voice; choose a voice service in the settings page" >&2
        rm -rf "$tmp"
    fi
fi

if [ "$GET_MODEL" = 1 ] && [ ! -d "$DATA/models/meaning" ]; then
    say "Downloading the model for matching memories by meaning"
    tmp="$(mktemp -d)"
    ok=1
    for f in $MEANING_FILES; do
        curl -fsSL "$MEANING_URL/$f" -o "$tmp/$f" || { ok=0; break; }
    done
    if [ "$ok" = 1 ]; then
        install -d -o companion -g companion "$DATA/models"
        mv "$tmp" "$DATA/models/meaning"
        chown -R companion:companion "$DATA/models"
    else
        echo "warning: could not download the meaning model; memory will match by words alone" >&2
        rm -rf "$tmp"
    fi
fi

if [ "$GET_MODEL" = 1 ] && [ ! -d "$DATA/models/wake" ]; then
    say "Downloading the wake-word model"
    tmp="$(mktemp -d)"
    if curl -fsSL "$WAKE_URL" -o "$tmp/wake.tar.bz2" && tar -xjf "$tmp/wake.tar.bz2" -C "$tmp"; then
        src="$(find "$tmp" -maxdepth 1 -type d -name 'sherpa-onnx-kws-*' | head -n 1)"
        install -d -o companion -g companion "$DATA/models"
        install -d "$tmp/wake"
        cp "$src"/encoder-*.int8.onnx "$src"/decoder-*.int8.onnx "$src"/joiner-*.int8.onnx \
            "$src/tokens.txt" "$src/bpe.model" "$src/README.md" "$tmp/wake/"
        mv "$tmp/wake" "$DATA/models/wake"
        chown -R companion:companion "$DATA/models"
    else
        echo "warning: could not download the wake-word model; the wake word will be unavailable" >&2
    fi
    rm -rf "$tmp"
fi

if [ "$GET_MODEL" = 1 ] && [ ! -d "$DATA/models/mocap" ]; then
    say "Downloading the tracking a phone uses to record gestures"
    # MediaPipe for the web and its face and body models (Apache-2.0), each file checked against its SHA-256
    # (core/companion/mocap_assets.py). A device without them fetches them when a phone first asks.
    install -d -o companion -g companion "$DATA/models"
    if PYTHONPATH="$REPO/core" python3 -m companion.mocap_assets "$DATA/models/mocap"; then
        chown -R companion:companion "$DATA/models"
    else
        echo "warning: could not download the tracking for phone recording; the device fetches it when first asked" >&2
    fi
fi

say "Preparing the first screen"
# The screen shows this setup card from the very first start, before the
# core service has run; the service replaces it as soon as it starts.
if [ ! -e "$DATA/current.inp" ]; then
    runuser -u companion -- "$APP/venv/bin/python" -c "
import sys; sys.path.insert(0, '$APP/core')
from companion import screen
open('$DATA/card.inp', 'wb').write(screen.card_puppet(screen.setup_card()))"
    runuser -u companion -- ln -sfn "$DATA/card.inp" "$DATA/current.inp"
fi

say "Installing system settings"
install -m 0644 "$REPO/device/systemd/companion-core.service" /etc/systemd/system/
install -m 0644 "$REPO/device/systemd/companion-stage.service" /etc/systemd/system/
install -m 0644 "$REPO/device/systemd/companion-update.service" "$REPO/device/systemd/companion-update.path" \
    "$REPO/device/systemd/companion-update-boot.service" /etc/systemd/system/
install -m 0644 "$REPO/device/systemd/companion-usb.service" "$REPO/device/systemd/companion-usb-dhcp.service" /etc/systemd/system/
# The device's end of the USB connection: a system connection, not one of the owner's saved networks.
install -d /usr/lib/NetworkManager/system-connections
install -m 0600 "$REPO/device/config/companion-usb.nmconnection" /usr/lib/NetworkManager/system-connections/
install -d /etc/udev/rules.d
install -m 0644 "$REPO/device/config/99-companion-backlight.rules" /etc/udev/rules.d/
install -m 0644 "$REPO/device/config/pam-companion-stage" /etc/pam.d/companion-stage
install -d /etc/polkit-1/rules.d /etc/systemd/journald.conf.d /etc/systemd/system.conf.d
install -m 0644 "$REPO/device/config/polkit-companion.rules" /etc/polkit-1/rules.d/50-companion.rules
install -m 0644 "$REPO/device/config/journald.conf" /etc/systemd/journald.conf.d/companion.conf
install -m 0644 "$REPO/device/config/system.conf" /etc/systemd/system.conf.d/companion.conf
[ -f /etc/default/companion ] || install -m 0644 "$REPO/device/config/default-companion" /etc/default/companion

BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
if [ -f "$BOOT/config.txt" ] && ! grep -q "Character device" "$BOOT/config.txt"; then
    cat "$REPO/device/config/boot-config.txt" >> "$BOOT/config.txt"
fi
if [ -f "$BOOT/cmdline.txt" ] && ! grep -q "logo.nologo" "$BOOT/cmdline.txt"; then
    sed -i '1 s/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$BOOT/cmdline.txt"
fi

say "Enabling the services"
systemctl daemon-reload || true
systemctl disable getty@tty1.service 2>/dev/null || true
systemctl enable NetworkManager.service avahi-daemon.service companion-core.service companion-stage.service \
    companion-update.path companion-update-boot.service companion-usb.service companion-usb-dhcp.service
systemctl set-default graphical.target

if [ "$IMAGE" = 1 ]; then
    say "Preparing a ready-made device image"
    # Which card image this is: the device's notice names it, with the address of its operating system's
    # source code (published with the image, docs/34-licences.md).
    printf '%s\n' "$VERSION" > /etc/companion-image
    # Raspberry Pi OS asks for a user name on the screen at first boot; the
    # device has no keyboard and needs no login, so that is switched off.
    systemctl mask userconfig.service 2>/dev/null || true
    # cloud-init could create a user with a default password from the boot
    # partition; the device does not use it.
    if [ -d /etc/cloud ]; then touch /etc/cloud/cloud-init.disabled; fi
    # No remote login on a sold unit.
    systemctl disable ssh.service sshswitch.service 2>/dev/null || true
    # Nor Raspberry Pi Connect (remote access through Raspberry Pi's servers): it is closed-source, and
    # without it every package on the card has its source code published with the image.
    for pkg in rpi-connect-lite rpi-connect; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then apt-get purge -y -q "$pkg"; fi
    done
    # http://companion.local reaches the settings page.
    echo companion > /etc/hostname
    if grep -q "^127.0.1.1" /etc/hosts; then
        sed -i 's/^127.0.1.1.*/127.0.1.1\tcompanion/' /etc/hosts
    else
        printf '127.0.1.1\tcompanion\n' >> /etc/hosts
    fi
    apt-get clean
fi

say "Done. Restart the device with: sudo reboot"
echo "After it restarts, the screen shows how to reach its settings page."
