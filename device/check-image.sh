#!/bin/bash
# Check that a device image is set up to work out of the box.
#
#   sudo ./device/check-image.sh companion.img [--packages DIR]
#
# Mounts the image read-only and checks: the software and speech model are
# installed, the services are enabled, nothing asks for a login on the
# screen, cloud-init and SSH are off, no API keys or owner data are inside,
# and the boot settings for the buttons and display are present. An image with
# power-cut protection (a read-only system and a data partition) is checked for
# that too. Exit status 0 means every check passed.
#
# --packages DIR also copies out what fetch-sources.py needs to download the source code of the image's
# operating-system packages: packages.tsv (each package, its version, its source package and version),
# the image's package sources and its archive keys.
set -uo pipefail

IMG="${1:?usage: check-image.sh IMAGE [--packages DIR]}"
PACKAGES_OUT=""
if [ "${2:-}" = "--packages" ]; then PACKAGES_OUT="${3:?--packages needs a folder}"; fi
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
WORK="$(mktemp -d)"
ROOT="$WORK/root"
LOOP=""
DLOOP=""
cleanup() {
    mountpoint -q "$WORK/data" && umount "$WORK/data"; [ -n "$DLOOP" ] && losetup -d "$DLOOP"
    mountpoint -q "$ROOT" && umount "$ROOT"; [ -n "$LOOP" ] && losetup -d "$LOOP"; rm -rf "$WORK"
}
trap cleanup EXIT

part() {
    sfdisk -J "$IMG" | python3 -c "import json,sys; p=json.load(sys.stdin)['partitiontable']['partitions'][$1-1]; print(p['start']*512, p['size']*512)"
}
read -r BOOT_START _ < <(part 1)
read -r ROOT_START ROOT_SIZE < <(part 2)
mkdir -p "$ROOT"
LOOP="$(losetup -f --show -r -o "$ROOT_START" --sizelimit "$ROOT_SIZE" "$IMG")"
mount -o ro "$LOOP" "$ROOT"
# With power-cut protection, what the device saves (and the first setup screen) is on partition 3.
PARTS="$(sfdisk -J "$IMG" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['partitiontable']['partitions']))")"
DATA="$ROOT/var/lib/companion"
if [ "$PARTS" -ge 3 ]; then
    read -r DATA_START DATA_SIZE < <(part 3)
    mkdir -p "$WORK/data"
    DLOOP="$(losetup -f --show -r -o "$DATA_START" --sizelimit "$DATA_SIZE" "$IMG")"
    mount -o ro "$DLOOP" "$WORK/data"
    DATA="$WORK/data/companion"
fi
if [ -n "$PACKAGES_OUT" ]; then
    mkdir -p "$PACKAGES_OUT/sources" "$PACKAGES_OUT/keyrings"
    dpkg-query --admindir="$ROOT/var/lib/dpkg" -W \
        -f='${db:Status-Abbrev}\t${Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}\n' |
        awk -F '\t' 'BEGIN {OFS = "\t"} $1 ~ /^.i/ {print $2, $3, $4, $5, $6}' > "$PACKAGES_OUT/packages.tsv"
    cp -a "$ROOT"/etc/apt/sources.list.d/. "$PACKAGES_OUT/sources/" 2>/dev/null || true
    [ -s "$ROOT/etc/apt/sources.list" ] && cp "$ROOT/etc/apt/sources.list" "$PACKAGES_OUT/sources/sources.list"
    for dir in "$ROOT/usr/share/keyrings" "$ROOT/etc/apt/keyrings" "$ROOT/etc/apt/trusted.gpg.d"; do
        [ -d "$dir" ] || continue
        for key in "$dir"/*; do
            src="$key"
            if [ -L "$key" ]; then      # a link inside the image: follow it inside the image, not on this computer
                target="$(readlink "$key")"
                case "$target" in /*) src="$ROOT$target" ;; *) src="$dir/$target" ;; esac
            fi
            [ -f "$src" ] && cp "$src" "$PACKAGES_OUT/keyrings/$(basename "$key")"
        done
    done
    echo "the image's $(wc -l < "$PACKAGES_OUT/packages.tsv") packages are listed in $PACKAGES_OUT/packages.tsv"
fi
model() {   # a model file the device comes with: on the read-only system, or in the data folder
    test -s "$ROOT/opt/companion/models/$1" || test -s "$DATA/models/$1"
}

PASS=0
FAIL=0
check() {   # description, then a command that succeeds when the check passes
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "PASS  $what"; PASS=$((PASS + 1)); else echo "FAIL  $what"; FAIL=$((FAIL + 1)); fi
}
enabled() { [ -L "$ROOT/etc/systemd/system/$1.wants/$2" ]; }
masked() { [ "$(readlink "$ROOT/etc/systemd/system/$1")" = /dev/null ]; }
absent_or_disabled() { ! ls "$ROOT"/etc/systemd/system/*.wants/"$1" >/dev/null 2>&1; }

check "core service installed" test -f "$ROOT/opt/companion/core/companion/__main__.py"
check "settings page installed" test -f "$ROOT/opt/companion/core/companion/console/index.html"
check "character program installed (64-bit ARM)" sh -c "file -b '$ROOT/opt/companion/bin/stage' | grep -q aarch64"
check "full-body animations for 3D avatars installed" sh -c "ls '$ROOT/opt/companion/share/vrma/idle.vrma'"
check "software in its version's folder (releases/, current)" sh -c "readlink '$ROOT/opt/companion/current' | grep -q '^releases/' && test -L '$ROOT/opt/companion/core'"
check "the software knows its version" sh -c "grep -qE '^[0-9]' '$ROOT/opt/companion/core/companion/VERSION'"
check "updates can be checked (the owner's update key)" test -s "$ROOT/opt/companion/share/update-key.pem"
check "the updater is installed" test -x "$ROOT/opt/companion/updater/companion-update.py"
check "update requests start the updater" enabled multi-user.target companion-update.path
check "an update the power cut short is finished or undone at start" enabled multi-user.target companion-update-boot.service
check "Python environment with on-device speech recognition" sh -c "ls '$ROOT'/opt/companion/venv/lib/python3*/site-packages | grep -q sherpa_onnx"
check "speech model shipped" model stt/encode.int8.onnx
check "on-device voice shipped (VCTK, prepared for sherpa-onnx)" sh -c "{ test -s '$ROOT/opt/companion/models/tts/en_GB-vctk-medium.onnx' && grep -q \"\$(printf '\\356\\200\\200')\" '$ROOT/opt/companion/models/tts/tokens.txt'; } || { test -s '$DATA/models/tts/en_GB-vctk-medium.onnx' && grep -q \"\$(printf '\\356\\200\\200')\" '$DATA/models/tts/tokens.txt'; }"
check "model for matching memories by meaning shipped" model meaning/model.safetensors
check "wake-word model shipped" model wake/bpe.model
check "tracking for recording gestures with a phone shipped (MediaPipe, with its notice)" sh -c "{ test -s '$ROOT/opt/companion/models/mocap/vision_wasm_internal.wasm' && test -s '$ROOT/opt/companion/models/mocap/pose_landmarker_lite.task' && test -s '$ROOT/opt/companion/models/mocap/LICENSE'; } || { test -s '$DATA/models/mocap/vision_wasm_internal.wasm' && test -s '$DATA/models/mocap/pose_landmarker_lite.task' && test -s '$DATA/models/mocap/LICENSE'; }"
check "the wake word can spell names (sentencepiece)" sh -c "ls '$ROOT'/opt/companion/venv/lib/python3*/site-packages | grep -q sentencepiece"
check "espeak-ng installed for the voice" test -x "$ROOT/usr/bin/espeak-ng"
check "data folder owned by the service user" sh -c "[ \"\$(stat -c %u '$DATA')\" = \"\$(grep '^companion:' '$ROOT/etc/passwd' | cut -d: -f3)\" ]"
check "core service starts at boot" enabled multi-user.target companion-core.service
check "character starts at boot" enabled graphical.target companion-stage.service
check "boots to the character screen (graphical target)" sh -c "readlink '$ROOT/etc/systemd/system/default.target' | grep -q graphical.target"
check "no login prompt on the screen (getty on tty1 off)" absent_or_disabled getty@tty1.service
check "no first-boot user prompt (userconfig masked)" masked userconfig.service
check "cloud-init disabled" test -f "$ROOT/etc/cloud/cloud-init.disabled"
check "SSH not enabled" absent_or_disabled ssh.service
check "hostname is companion" grep -qx companion "$ROOT/etc/hostname"
check "Wi-Fi and time zone permission for the service" test -f "$ROOT/etc/polkit-1/rules.d/50-companion.rules"
check "small logs" test -f "$ROOT/etc/systemd/journald.conf.d/companion.conf"
check "hardware watchdog" test -f "$ROOT/etc/systemd/system.conf.d/companion.conf"
check "a setup screen is ready for the first start" sh -c "test -s '$DATA/card.inp' && test -L '$DATA/current.inp'"
check "the screen restarts forever if it fails" grep -q "StartLimitIntervalSec=0" "$ROOT/etc/systemd/system/companion-stage.service"
check "no API keys inside" sh -c "! test -e '$DATA/config/secrets.json'"
check "no settings-page password inside" sh -c "! test -e '$DATA/config/console-password'"
check "no characters or memories inside" sh -c "! ls '$DATA/characters/'* '$DATA/memory/'* >/dev/null 2>&1"
check "no saved Wi-Fi networks inside" sh -c "! ls '$ROOT'/etc/NetworkManager/system-connections/* '$WORK'/data/system/nm-connections/* >/dev/null 2>&1"
check "no build leftovers" sh -c "! test -e '$ROOT/tmp/companion-src' && ! test -e '$ROOT/usr/local/share/ca-certificates/build-extra-ca.crt'"
check "buttons and display in the boot settings" sh -c "mtype -i '$IMG@@$BOOT_START' ::config.txt | grep -q 'gpio-key,gpio=17'"
check "the USB-C port can be a USB device (dwc2)" sh -c "mtype -i '$IMG@@$BOOT_START' ::config.txt | grep -q '^dtoverlay=dwc2,dr_mode=peripheral'"
check "the USB connection starts at boot" enabled multi-user.target companion-usb.service
check "a computer on USB gets an address" sh -c "ls '$ROOT'/etc/systemd/system/sys-subsystem-net-devices-usb0.device.wants/companion-usb-dhcp.service && test -x '$ROOT/usr/sbin/dnsmasq'"
check "the device's end of the USB connection (readable by root only)" sh -c "[ \"\$(stat -c %a '$ROOT/usr/lib/NetworkManager/system-connections/companion-usb.nmconnection')\" = 600 ]"
check "the gadget program is installed" test -x "$ROOT/opt/companion/bin/companion-usb-gadget"
check "the screen's brightness can be set by the service" test -f "$ROOT/etc/udev/rules.d/99-companion-backlight.rules"
check "quiet boot" sh -c "mtype -i '$IMG@@$BOOT_START' ::cmdline.txt | grep -q logo.nologo"
check "no closed-source remote access (Raspberry Pi Connect)" sh -c "! dpkg-query --admindir='$ROOT/var/lib/dpkg' -W -f='\${db:Status-Abbrev}\n' rpi-connect-lite rpi-connect 2>/dev/null | grep -q '^.i'"
check "licences and notices on board (the stage's libraries, the device's notice, the image's version)" sh -c "test -s '$ROOT/opt/companion/share/licenses/stage.txt' && test -s '$ROOT/opt/companion/core/companion/notices/device.txt' && test -s '$ROOT/etc/companion-image'"
if [ "$PARTS" -ge 3 ]; then
    fstab="$ROOT/etc/fstab"
    check "power cuts: the system partition is read-only" sh -c "grep -E '^[^#]+ +/ +ext4 +[^ ]*\\bro\\b' '$fstab' && mtype -i '$IMG@@$BOOT_START' ::cmdline.txt | grep -qw ro"
    check "power cuts: the data partition is labelled and mounted at /data" sh -c "blkid -o value -s LABEL '$DLOOP' | grep -qx companion-data && grep -Eq '^LABEL=companion-data +/data ' '$fstab'"
    check "power cuts: the device's data, Wi-Fi and /var go where they can be written" sh -c "grep -Eq '^/data/companion +/var/lib/companion ' '$fstab' && grep -Eq '^/data/system/nm-connections +/etc/NetworkManager/system-connections ' '$fstab' && grep -Eq '^overlay +/var +overlay' '$fstab'"
    check "power cuts: the data partition is checked, and grows at the first start" sh -c "ls '$ROOT'/etc/systemd/system/multi-user.target.wants/companion-data-check.service '$ROOT'/etc/systemd/system/multi-user.target.wants/companion-firstboot.service && test -x '$ROOT/opt/companion/bin/companion-data-check' && test -x '$ROOT/opt/companion/bin/companion-firstboot'"
    check "power cuts: Raspberry Pi OS's own first-start resize is off" sh -c "! mtype -i '$IMG@@$BOOT_START' ::cmdline.txt | grep -qw resize && [ \"\$(readlink '$ROOT/etc/systemd/system/rpi-resize.service')\" = /dev/null ]"
    check "power cuts: logs in memory, name servers in /run, time zone on the data partition" sh -c "grep -q 'Storage=volatile' '$ROOT'/etc/systemd/journald.conf.d/zz-companion-readonly.conf && [ \"\$(readlink '$ROOT/etc/resolv.conf')\" = /run/NetworkManager/resolv.conf ] && [ \"\$(readlink '$ROOT/etc/localtime')\" = /data/system/time/localtime ] && test -L '$WORK/data/system/time/localtime'"
    check "power cuts: the models the device comes with are on the read-only system" test -s "$ROOT/opt/companion/models/stt/encode.int8.onnx"
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
