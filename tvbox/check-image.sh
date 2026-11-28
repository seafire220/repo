#!/bin/bash
# Check that a TV box image (tvbox/build-image.sh) is set up to work out of the box.
#
#   sudo ./tvbox/check-image.sh companion-tvbox.img [--board KEY] [--with-ssh-key]
#
# Mounts the image read-only and checks: the software and models are installed, the services are enabled,
# the screen runs under Weston's kiosk shell at 1920x1080, the remote and USB buttons are listened to, root's
# password is locked and remote login is off (or, with --with-ssh-key, open to a key only), no API keys or
# owner data are inside, and the card's boot settings name the board's device tree. Exit status 0 means every
# check passed.
set -uo pipefail

IMG="${1:?usage: check-image.sh IMAGE [--board KEY] [--with-ssh-key]}"
shift
BOARD=x96-max-plus
WITH_KEY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --board) BOARD="$2"; shift 2 ;;
        --with-ssh-key) WITH_KEY=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DTB="$(awk -F '\t' -v key="$BOARD" '$1 == key {print $2}' "$HERE/boards.tsv")"
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
WORK="$(mktemp -d)"
ROOT="$WORK/root"
LOOP=""
cleanup() { mountpoint -q "$ROOT" && umount "$ROOT"; [ -n "$LOOP" ] && losetup -d "$LOOP"; rm -rf "$WORK"; }
trap cleanup EXIT

part() {
    sfdisk -J "$IMG" | python3 -c "import json,sys; p=json.load(sys.stdin)['partitiontable']['partitions'][$1-1]; print(p['start']*512, p['size']*512)"
}
read -r BOOT_START _ < <(part 1)
read -r ROOT_START ROOT_SIZE < <(part 2)
BOOT="$IMG@@$BOOT_START"
mkdir -p "$ROOT"
LOOP="$(losetup -f --show -r -o "$ROOT_START" --sizelimit "$ROOT_SIZE" "$IMG")"
mount -o ro "$LOOP" "$ROOT"
DATA="$ROOT/var/lib/companion"

PASS=0
FAIL=0
check() {   # description, then a command that succeeds when the check passes
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "PASS  $what"; PASS=$((PASS + 1)); else echo "FAIL  $what"; FAIL=$((FAIL + 1)); fi
}
enabled() { [ -L "$ROOT/etc/systemd/system/$1.wants/$2" ]; }
absent_or_disabled() { ! ls "$ROOT"/etc/systemd/system/*.wants/"$1" >/dev/null 2>&1; }

check "core service installed" test -f "$ROOT/opt/companion/core/companion/__main__.py"
check "settings page installed" test -f "$ROOT/opt/companion/core/companion/console/index.html"
check "character program installed (64-bit ARM)" sh -c "file -b '$ROOT/opt/companion/bin/stage' | grep -q aarch64"
check "full-body animations for 3D avatars installed" test -s "$ROOT/opt/companion/share/vrma/idle.vrma"
check "software in its version's folder (releases/, current)" sh -c "readlink '$ROOT/opt/companion/current' | grep -q '^releases/'"
check "the software knows its version" sh -c "grep -qE '^[0-9]' '$ROOT/opt/companion/core/companion/VERSION'"
check "the updater is installed, and update requests start it" sh -c "test -x '$ROOT/opt/companion/updater/companion-update.py' && test -L '$ROOT/etc/systemd/system/multi-user.target.wants/companion-update.path'"
check "Python environment with on-device speech recognition" sh -c "ls '$ROOT'/opt/companion/venv/lib/python3*/site-packages | grep -q sherpa_onnx"
check "speech model shipped" test -s "$DATA/models/stt/encode.int8.onnx"
check "on-device voice shipped" test -s "$DATA/models/tts/en_GB-vctk-medium.onnx"
check "model for matching memories by meaning shipped" test -s "$DATA/models/meaning/model.safetensors"
check "wake-word model shipped" test -s "$DATA/models/wake/bpe.model"
check "espeak-ng installed for the voice" test -x "$ROOT/usr/bin/espeak-ng"
check "data folder owned by the service user" sh -c "[ \"\$(stat -c %u '$DATA')\" = \"\$(grep '^companion:' '$ROOT/etc/passwd' | cut -d: -f3)\" ]"
check "core service starts at boot" enabled multi-user.target companion-core.service
check "character starts at boot" enabled graphical.target companion-stage.service
check "boots to the character screen (graphical target)" sh -c "readlink '$ROOT/etc/systemd/system/default.target' | grep -q graphical.target"
check "no login prompt on the screen (getty on tty1 off)" absent_or_disabled getty@tty1.service
check "NetworkManager looks after the network" enabled multi-user.target NetworkManager.service
check "and the Ethernet socket too (netplan, where the image has it)" sh -c "! test -d '$ROOT/etc/netplan' || grep -q 'renderer: NetworkManager' '$ROOT/etc/netplan/90-companion-networkmanager.yaml'"
check "Weston and its kiosk shell installed" sh -c "test -x '$ROOT/usr/bin/weston' && ls '$ROOT'/usr/lib/*/weston/kiosk-shell.so"
check "the screen: Weston's kiosk shell instead of cage (a drop-in that updates leave alone)" sh -c "grep -q '^ExecStart=\$' '$ROOT/etc/systemd/system/companion-stage.service.d/tvbox.conf' && grep -q '^ExecStart=/usr/bin/weston --config=/etc/companion/weston.ini' '$ROOT/etc/systemd/system/companion-stage.service.d/tvbox.conf'"
check "the screen: 1920x1080, never blanking, the character launched" sh -c "grep -qx 'mode=1920x1080' '$ROOT/etc/companion/weston.ini' && grep -q '^idle-time=0' '$ROOT/etc/companion/weston.ini' && grep -q '^path=/usr/local/lib/companion-tvbox/stage-in-weston' '$ROOT/etc/companion/weston.ini'"
check "the character's program is run with the screen service's own arguments" sh -c "COMPANION_DRY_RUN=1 COMPANION_STAGE_UNIT='$ROOT/etc/systemd/system/companion-stage.service' sh '$ROOT/usr/local/lib/companion-tvbox/stage-in-weston' | grep -q '^/opt/companion/bin/stage /var/lib/companion/current.inp '"
check "the remote's OK, Enter on a USB button or keyboard, and the remote's volume and mute keys" sh -c "grep -q '^COMPANION_BUTTON=any' '$ROOT/etc/default/companion' && grep -q '^COMPANION_BUTTON_KEYS=352,353,28,96,164' '$ROOT/etc/default/companion' && grep -q '^COMPANION_REMOTE_KEYS=1' '$ROOT/etc/default/companion'"
check "the box's own sound card is routed to HDMI at each start" sh -c "test -x '$ROOT/usr/local/lib/companion-tvbox/companion-tvbox-sound' && test -L '$ROOT/etc/systemd/system/multi-user.target.wants/companion-tvbox-sound.service'"
check "HDMI-CEC ready for the TV's own remote (off until turned on)" sh -c "test -x '$ROOT/usr/bin/cec-ctl' && test -x '$ROOT/usr/local/lib/companion-tvbox/companion-tvbox-cec'"
check "no USB connection to a computer (the ports are for the microphone and button)" sh -c "grep -q '^COMPANION_USB=off' '$ROOT/etc/default/companion' && ! test -L '$ROOT/etc/systemd/system/multi-user.target.wants/companion-usb.service'"
check "root's password is locked (Armbian's images come with 1234)" sh -c "grep '^root:' '$ROOT/etc/shadow' | cut -d: -f2 | grep -q '^[!*]'"
check "no first-login questions" sh -c "! test -e '$ROOT/root/.not_logged_in_yet'"
if [ "$WITH_KEY" = 1 ]; then
    check "SSH for a maker's key only (no passwords)" sh -c "test -s '$ROOT/root/.ssh/authorized_keys' && grep -q '^PasswordAuthentication no' '$ROOT/etc/ssh/sshd_config.d/companion.conf'"
else
    check "SSH not enabled (neither the service nor its socket)" sh -c "! ls '$ROOT'/etc/systemd/system/*.wants/ssh.service '$ROOT'/etc/systemd/system/*.wants/ssh.socket >/dev/null 2>&1"
    check "no SSH keys let in" sh -c "! test -s '$ROOT/root/.ssh/authorized_keys'"
fi
check "hostname is companion" grep -qx companion "$ROOT/etc/hostname"
check "Wi-Fi and time zone permission for the service" test -f "$ROOT/etc/polkit-1/rules.d/50-companion.rules"
check "a setup screen is ready for the first start" sh -c "test -s '$DATA/card.inp' && test -L '$DATA/current.inp'"
check "no API keys inside" sh -c "! test -e '$DATA/config/secrets.json'"
check "no settings-page password inside" sh -c "! test -e '$DATA/config/console-password'"
check "no characters or memories inside" sh -c "! ls '$DATA/characters/'* '$DATA/memory/'* >/dev/null 2>&1"
check "no saved Wi-Fi networks inside" sh -c "! ls '$ROOT'/etc/NetworkManager/system-connections/*.nmconnection >/dev/null 2>&1"
check "no build leftovers" sh -c "! test -e '$ROOT/tmp/companion-src'"
check "licences and notices on board" sh -c "test -s '$ROOT/opt/companion/share/licenses/stage.txt' && test -s '$ROOT/opt/companion/core/companion/notices/device.txt' && test -s '$ROOT/etc/companion-image'"
check "the board is noted ($BOARD)" grep -qx "board=$BOARD" "$ROOT/etc/companion-tvbox"
check "the card starts with the board's device tree ($DTB)" sh -c "mtype -i '$BOOT' ::uEnv.txt | grep -qx 'FDT=/dtb/amlogic/$DTB' && mdir -i '$BOOT' '::dtb/amlogic/$DTB'"
check "quiet start" sh -c "mtype -i '$BOOT' ::uEnv.txt | grep '^APPEND=' | grep -q logo.nologo"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
