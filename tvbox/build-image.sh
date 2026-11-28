#!/bin/bash
# Build a ready-to-flash card image of the character device for a TV box: an Amlogic S905X3 box such as the
# X96 Max Plus (tvbox/README.md).
#
#   sudo ./tvbox/build-image.sh ARMBIAN_S905X3.img.gz OUT.img [options]
#
# Starts from the community Armbian image for S905X3 boxes (ophub/amlogic-s9xxx-armbian's releases: the
# "Armbian_..._amlogic_s905x3_trixie_..._server_....img.gz" file), installs everything inside it with
# tvbox/install.sh (packages, the core, the stage, the speech models, the screen, the remote) and writes
# OUT.img, for a microSD card or a USB stick. Each unit makes its own hotspot password and the buyer sets the
# settings-page password, so nothing secret is shared between units.
#
# Options:
#   --board KEY          which box (tvbox/boards.tsv; default x96-max-plus): its device-tree file goes in the
#                        card's uEnv.txt, where it can be changed later from any computer
#   --stage-binary PATH  the stage built for 64-bit ARM (default: the cross-build in
#                        stage/target/aarch64-unknown-linux-gnu/release/stage)
#   --grow SIZE          extra space for the software (default 2G)
#   --version V          the software's version (default: core/companion/VERSION)
#   --ssh-key FILE       let this public key log in as root over SSH: a maker's test image, never one to sell
#   --rotate DEGREES     the picture turned for a screen standing on its side (90, or 270): the desk device
#                        (docs/38-tv-box-desk-device.md). Without it the picture is as the screen has it (a TV)
#   --installer          an installer card: at its first start in a box it copies itself onto the box's own
#                        memory, erasing Android, and switches the box off (bin/companion-tvbox-install), so
#                        each unit needs no card of its own
#   --compress           also write OUT.img.xz and its SHA-256 sum
#
# The system stays writable: power-cut protection, as the Pi image has, is not done for TV boxes yet
# (tvbox/README.md, "Not yet"). Runs on a Linux PC as root. Needs: gzip and xz, sfdisk, losetup, e2fsck,
# resize2fs, mtools, and ARM64 emulation on a PC that is not 64-bit ARM (qemu-user-static, binfmt registered).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_BINARY="$REPO/stage/target/aarch64-unknown-linux-gnu/release/stage"
GROW=2G
COMPRESS=0
VERSION=""
BOARD=x96-max-plus
SSH_KEY=""
ROTATE=""
INSTALLER=0
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --board) BOARD="$2"; shift 2 ;;
        --stage-binary) STAGE_BINARY="$2"; shift 2 ;;
        --grow) GROW="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --rotate) ROTATE="$2"; shift 2 ;;
        --installer) INSTALLER=1; shift ;;
        --compress) COMPRESS=1; shift ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
[ ${#ARGS[@]} -eq 2 ] || { sed -n '2,32p' "$0"; exit 2; }
SOURCE="${ARGS[0]}"
OUT="${ARGS[1]}"

say() { printf '\n== %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
for tool in gzip xz sfdisk losetup e2fsck resize2fs mtype mcopy mdir; do
    command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done
DTB="$(awk -F '\t' -v key="$BOARD" '$1 == key {print $2}' "$REPO/tvbox/boards.tsv")"
[ -n "$DTB" ] || { echo "unknown board: $BOARD (see tvbox/boards.tsv)" >&2; exit 2; }
MODEL_ID="$(awk -F '\t' -v key="$BOARD" '$1 == key {print $3}' "$REPO/tvbox/boards.tsv")"
case "$ROTATE" in ''|0|90|180|270) ;; *) echo "--rotate takes 0, 90, 180 or 270" >&2; exit 2 ;; esac
[ -x "$STAGE_BINARY" ] || { echo "no stage program at $STAGE_BINARY (see stage/README.md)" >&2; exit 1; }
if [ -n "$SSH_KEY" ] && [ ! -f "$SSH_KEY" ]; then echo "no public key at $SSH_KEY" >&2; exit 2; fi
if [ "$(uname -m)" != "aarch64" ] && [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    echo "ARM64 emulation is not registered (install qemu-user-static)" >&2
    exit 1
fi

WORK="$(mktemp -d)"
ROOT="$WORK/root"
LOOP=""
cleanup() {
    set +e
    for p in /proc/[0-9]*; do
        [ "$(readlink "$p/root" 2>/dev/null)" = "$ROOT" ] && kill -9 "${p#/proc/}" 2>/dev/null
    done
    for m in dev/pts dev proc sys; do mountpoint -q "$ROOT/$m" && umount -l "$ROOT/$m"; done
    mountpoint -q "$ROOT" && umount "$ROOT"
    [ -n "$LOOP" ] && losetup -d "$LOOP"
    rm -rf "$WORK"
}
trap cleanup EXIT

say "Unpacking $SOURCE"
case "$SOURCE" in
    *.gz) gzip -dc "$SOURCE" > "$OUT" ;;
    *.xz) xz -dc "$SOURCE" > "$OUT" ;;
    *) cp "$SOURCE" "$OUT" ;;
esac
part() {   # start and size in bytes of partition $1
    sfdisk -J "$OUT" | python3 -c "import json,sys; p=json.load(sys.stdin)['partitiontable']['partitions'][$1-1]; print(p['start']*512, p['size']*512)"
}
COUNT="$(sfdisk -J "$OUT" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['partitiontable']['partitions']))")"
[ "$COUNT" = 2 ] || { echo "expected an Armbian image with two partitions (BOOT and ROOTFS), found $COUNT" >&2; exit 1; }
truncate -s +"$GROW" "$OUT"
echo ", +" | sfdisk --no-reread --quiet -N 2 "$OUT"
read -r BOOT_START _ < <(part 1)
read -r ROOT_START ROOT_SIZE < <(part 2)
BOOT="$OUT@@$BOOT_START"
mtype -i "$BOOT" ::uEnv.txt > "$WORK/uEnv.txt" || { echo "the image's BOOT partition has no uEnv.txt" >&2; exit 1; }
mdir -i "$BOOT" "::dtb/amlogic/$DTB" >/dev/null || { echo "the image has no $DTB for $BOARD" >&2; exit 1; }

say "Growing the system partition"
LOOP="$(losetup -f --show -o "$ROOT_START" --sizelimit "$ROOT_SIZE" "$OUT")"
e2fsck -fy "$LOOP" >/dev/null || true
resize2fs "$LOOP" >/dev/null
mkdir -p "$ROOT"
mount "$LOOP" "$ROOT"
mount -t proc proc "$ROOT/proc"
mount -t sysfs sys "$ROOT/sys"
mount --bind /dev "$ROOT/dev"
mount --bind /dev/pts "$ROOT/dev/pts"
RESOLV_BACKUP="$WORK/resolv.conf"
cp -a "$ROOT/etc/resolv.conf" "$RESOLV_BACKUP" 2>/dev/null || true
rm -f "$ROOT/etc/resolv.conf"
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"

say "Copying the software into the image"
SRC="$ROOT/tmp/companion-src"
mkdir -p "$SRC"
(cd "$REPO" && tar --exclude='__pycache__' --exclude='*.pyc' -cf - core device tvbox stage/motions stage/THIRD_PARTY_LICENSES.txt THIRD_PARTY.md) | tar -xf - -C "$SRC"
install -m 0755 "$STAGE_BINARY" "$SRC/stage-binary"
KEY_ARG=()
if [ -n "$SSH_KEY" ]; then
    install -m 0644 "$SSH_KEY" "$SRC/ssh-key.pub"
    KEY_ARG=(--ssh-key /tmp/companion-src/ssh-key.pub)
fi
[ -z "$ROTATE" ] || KEY_ARG+=(--rotate "$ROTATE")
[ "$INSTALLER" = 0 ] || KEY_ARG+=(--installer "$MODEL_ID")

say "Installing inside the image (this takes a while)"
chroot "$ROOT" env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 \
    http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
    HTTP_PROXY="${HTTP_PROXY:-}" HTTPS_PROXY="${HTTPS_PROXY:-}" \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    /tmp/companion-src/tvbox/install.sh --image --stage-binary /tmp/companion-src/stage-binary \
    ${VERSION:+--version "$VERSION"} "${KEY_ARG[@]}"
printf 'board=%s\ndtb=%s\nmodel_id=%s\n' "$BOARD" "$DTB" "$MODEL_ID" > "$ROOT/etc/companion-tvbox"

say "Tidying up"
rm -rf "$SRC"
rm -f "$ROOT/etc/resolv.conf"
if [ -e "$RESOLV_BACKUP" ] || [ -L "$RESOLV_BACKUP" ]; then cp -a "$RESOLV_BACKUP" "$ROOT/etc/resolv.conf"; fi
rm -rf "$ROOT/root/.cache" "$ROOT/var/lib/apt/lists/"*
for m in dev/pts dev proc sys; do umount -l "$ROOT/$m"; done
umount "$ROOT"
losetup -d "$LOOP"
LOOP=""

say "Boot settings: $BOARD ($DTB), quiet start"
sed -i -E "s#^FDT=.*#FDT=/dtb/amlogic/$DTB#" "$WORK/uEnv.txt"
grep -q '^FDT=' "$WORK/uEnv.txt" || printf 'FDT=/dtb/amlogic/%s\n' "$DTB" >> "$WORK/uEnv.txt"
if ! grep -q 'logo.nologo' "$WORK/uEnv.txt"; then
    sed -i -E 's#^(APPEND=.*)$#\1 quiet loglevel=3 logo.nologo vt.global_cursor_default=0#' "$WORK/uEnv.txt"
fi
tr -d '\r' < "$WORK/uEnv.txt" > "$WORK/uEnv.txt.new"
mcopy -o -i "$BOOT" "$WORK/uEnv.txt.new" ::uEnv.txt

if [ "$COMPRESS" = 1 ]; then
    say "Compressing"
    xz -T0 -k -f "$OUT"
    sha256sum "$OUT.xz" > "$OUT.xz.sha256"
fi
say "Done: $OUT"
echo "Write it to a microSD card with balenaEtcher or Raspberry Pi Imager (\"Use custom\"), then start the box from"
echo "it: tvbox/README.md, \"Starting the box from the card\"."
