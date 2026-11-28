#!/bin/bash
# What works on this TV box: run it on the box (as root, over SSH with a maker's key, tvbox/README.md) and keep
# what it prints. It changes nothing.
#
#   sudo /usr/local/lib/companion-tvbox/check-on-box.sh
#
# The same facts, and the logs, are in the support report from the settings page (Device tab, Help).
set -u
line() { printf '\n== %s\n' "$*"; }
show() { "$@" 2>&1 | sed 's/^/   /'; }

line "The box"
show cat /proc/device-tree/model; echo
show uname -r
show grep -E '^(board|dtb)=' /etc/companion-tvbox
show sh -c "grep -E '^FDT=' /boot/uEnv.txt"
show free -m
for zone in /sys/class/thermal/thermal_zone*/temp; do [ -r "$zone" ] && echo "   temperature: $(( $(cat "$zone") / 1000 )) C"; done

line "Graphics (Panfrost, Mali-G31) and the screen"
show sh -c "lsmod | grep -E '^(panfrost|meson_drm)' || echo 'panfrost not loaded'"
show sh -c "ls -l /dev/dri/"
show sh -c "journalctl -b -u companion-stage --no-pager | grep -iE 'Output|mode|kiosk|GL|renderer|fps|error' | tail -n 25"

line "Services"
for unit in companion-core companion-stage companion-tvbox-sound companion-tvbox-cec NetworkManager; do
    echo "   $unit: $(systemctl is-active "$unit" 2>/dev/null)"
done

line "Sound cards (a USB speaker and microphone are used first)"
show cat /proc/asound/cards
show sh -c "aplay -l | grep '^card'"
show sh -c "arecord -l | grep '^card' || echo 'no microphone'"

line "Buttons: input devices with OK, Select, Enter or Play/Pause"
show /opt/companion/venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "/opt/companion/core")
from companion.__main__ import find_inputs_with, KEY_OK, KEY_SELECT, KEY_ENTER, KEY_KPENTER, KEY_PLAYPAUSE
names = {}
try:
    block = open("/proc/bus/input/devices").read().split("\n\n")
    for b in block:
        name = next((l.split("=", 1)[1].strip('"') for l in b.splitlines() if l.startswith("N: Name=")), "")
        for l in b.splitlines():
            if l.startswith("H: Handlers="):
                for h in l.split("=", 1)[1].split():
                    if h.startswith("event"):
                        names["/dev/input/" + h] = name
except OSError:
    pass
found = find_inputs_with({KEY_OK, KEY_SELECT, KEY_ENTER, KEY_KPENTER, KEY_PLAYPAUSE})
print("\n".join("%s  %s" % (p, names.get(p, "")) for p in found) or "none: plug in a USB button or keyboard, or check the remote")
PY

line "Network"
show nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device
show sh -c "ip -4 -brief address | grep -v '^lo'"
show sh -c "readlink -f /sys/class/net/wlan0/device/driver 2>/dev/null | xargs -r basename || echo 'no Wi-Fi interface'"

line "Software"
show cat /opt/companion/core/companion/VERSION
show cat /etc/companion-image
