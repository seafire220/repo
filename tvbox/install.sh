#!/bin/bash
# Turn a TV box running Linux (Armbian for Amlogic S905X3 boxes, such as the X96 Max Plus) into the character
# device: the same software as the Raspberry Pi device, set up for a TV box (tvbox/README.md).
#
#   sudo ./tvbox/install.sh [--stage-binary PATH] [--no-model] [--image] [--version V] [--ssh-key FILE]
#                           [--rotate DEGREES] [--installer MODEL_ID]
#
# device/install.sh does everything the two have in common, unchanged. Then, for a TV box:
#   - the screen: Weston's kiosk shell instead of cage, so the picture is the screen's own size but never more
#     than 1920x1080 (4K is too much for the box's graphics), with no screen blanking, and turned for a screen
#     on its side with --rotate 90 (/etc/companion/weston.ini, bin/companion-tvbox-screen);
#   - the button: the remote's OK button, a USB button or keyboard (Enter), the desk device's button board
#     (button-board/), or the TV's own remote through HDMI-CEC once turned on; the remote's volume keys change
#     the volume, its mute key the microphone;
#   - sound: a USB speaker and microphone (the ReSpeaker Lite), as on the Pi; or the TV's speakers by HDMI,
#     whose route on the box's own sound card is set at each start;
#   - NetworkManager looks after the Ethernet socket too, and the USB connection to a computer is off;
#   - with --image: root's password is locked (Armbian's images come with root/1234), remote login stays off
#     unless --ssh-key gives a public key to let in (and then only with that key), and Armbian's first-login
#     questions are skipped;
#   - with --installer MODEL_ID: an installer card, which copies itself onto each box's own memory and switches
#     it off (bin/companion-tvbox-install; MODEL_ID is the box's number in the community installer's list,
#     boards.tsv).
#
# Running it again updates the software and leaves characters, memories, keys and settings alone.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERE="$REPO/tvbox"
LIB=/usr/local/lib/companion-tvbox
ARGS=()
IMAGE=0
SSH_KEY=""
ROTATE=""
INSTALLER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --rotate) ROTATE="$2"; shift 2 ;;
        --installer) INSTALLER="$2"; shift 2 ;;
        --image) IMAGE=1; ARGS+=("$1"); shift ;;
        --stage-binary|--version) ARGS+=("$1" "$2"); shift 2 ;;
        --no-model) ARGS+=("$1"); shift ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
say() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
case "$ROTATE" in ''|0|90|180|270) ;; *) echo "--rotate takes 0, 90, 180 or 270" >&2; exit 2 ;; esac
case "$INSTALLER" in ''|[0-9]|[0-9][0-9]|[0-9][0-9][0-9]|[0-9][0-9][0-9][0-9]) ;; *) echo "--installer takes the box's number (boards.tsv)" >&2; exit 2 ;; esac
if [ -n "$SSH_KEY" ] && ! grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+' "$SSH_KEY"; then
    echo "$SSH_KEY is not a public key (ssh-ed25519 AAAA..., from a .pub file)" >&2
    exit 2
fi

"$REPO/device/install.sh" "${ARGS[@]}"

say "TV box: the screen"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q --no-install-recommends weston v4l-utils
install -d /etc/companion "$LIB" /etc/systemd/system/companion-stage.service.d
# The settings an earlier version installed, never edited, are replaced: they fixed the picture at 1920x1080.
OLD_DEFAULT=0b98a6a24f04c08c283d4d82727ac1422005d05229f3b177ddd78be0b7751a08
if [ -f /etc/companion/weston.ini ] && [ "$(sha256sum < /etc/companion/weston.ini | cut -d' ' -f1)" = "$OLD_DEFAULT" ]; then
    rm -f /etc/companion/weston.ini
fi
[ -f /etc/companion/weston.ini ] || install -m 0644 "$HERE/config/weston.ini" /etc/companion/weston.ini
install -m 0755 "$HERE/bin/stage-in-weston" "$HERE/bin/companion-tvbox-screen" "$HERE/bin/companion-tvbox-sound" \
    "$HERE/bin/companion-tvbox-cec" "$HERE/bin/companion-tvbox-install" "$HERE/check-on-box.sh" "$LIB/"
install -m 0644 "$HERE/config/companion-stage-tvbox.conf" /etc/systemd/system/companion-stage.service.d/tvbox.conf

say "TV box: the remote, sound and the network"
if ! grep -q "TV box" /etc/default/companion; then
    printf '\n' >> /etc/default/companion
    cat "$HERE/config/companion-tvbox.default" >> /etc/default/companion
fi
if [ -n "$ROTATE" ]; then
    # A screen standing on its side (the desk device): the picture turned to match.
    sed -i '/^#\{0,1\}COMPANION_SCREEN_ROTATE=/d' /etc/default/companion
    printf 'COMPANION_SCREEN_ROTATE=%s\n' "$ROTATE" >> /etc/default/companion
fi
install -m 0644 "$HERE/config/companion-tvbox-sound.service" "$HERE/config/companion-tvbox-cec.service" \
    "$HERE/config/companion-tvbox-install.service" /etc/systemd/system/
if [ -d /etc/netplan ]; then
    install -m 0600 "$HERE/config/90-companion-networkmanager.yaml" /etc/netplan/90-companion-networkmanager.yaml
fi
systemctl daemon-reload || true
# The installer's service does nothing unless the card is an installer (below).
systemctl enable companion-tvbox-sound.service companion-tvbox-cec.service companion-tvbox-install.service
systemctl disable companion-usb.service companion-usb-dhcp.service 2>/dev/null || true

if [ "$IMAGE" = 1 ]; then
    say "TV box: locking the image"
    # Armbian's images come with root's password set to 1234: nobody logs in to a sold unit.
    passwd -l root >/dev/null
    rm -f /root/.not_logged_in_yet
    rm -f /etc/ssh/sshd_config.d/companion.conf
    if [ -n "$SSH_KEY" ]; then
        # A maker's way in, for testing: that key only, never a password.
        install -d -m 0700 /root/.ssh
        install -m 0600 "$SSH_KEY" /root/.ssh/authorized_keys
        install -d /etc/ssh/sshd_config.d
        printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
            > /etc/ssh/sshd_config.d/companion.conf
        systemctl enable ssh.service 2>/dev/null || true
    else
        rm -f /root/.ssh/authorized_keys
        # Neither the service nor its socket (which would start it on the first connection).
        systemctl disable ssh.service ssh.socket 2>/dev/null || true
    fi
fi

if [ -n "$INSTALLER" ]; then
    say "TV box: an installer card (box number $INSTALLER)"
    # What the community installer needs, so it never has to fetch anything.
    apt-get install -y -q --no-install-recommends tar bsdextrautils dosfstools e2fsprogs btrfs-progs parted fdisk \
        util-linux mount
    printf '# An installer card: it copies itself onto the memory of each box (tvbox/bin/companion-tvbox-install).\nMODEL_ID=%s\n' \
        "$INSTALLER" > /etc/companion/installer
fi

say "Done. Restart the box with: sudo reboot"
echo "After it restarts, the TV shows how to reach the settings page. The remote's OK button is the listen button."
