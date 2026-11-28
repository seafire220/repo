"""Run the device's core service.

    python -m companion --data /var/lib/companion --port 80

The button is read from a Linux input device: the Pi's `gpio-key` overlay
turns a GPIO press into a key press (see docs/13-build-and-install.md). A mute
switch, if fitted, is a second one (key 248). On a desktop, any keyboard device
and key code can be used instead, and the console's "button" does the same job.

On a TV box (tvbox/README.md) the button is any of several keys on any input device that has one: the box's
remote (its OK button), the TV's own remote through HDMI-CEC, a USB button or a keyboard (Enter), or the desk
device's USB button board (tvbox/button-board: Play/Pause). There the remote's volume keys change the volume and
its mute key turns the microphone off or on, and the button board's mute switch is held down as key 248, as the
Pi's is. /etc/default/companion sets these: COMPANION_BUTTON=any, COMPANION_BUTTON_KEYS=352,353,28,96,164 and
COMPANION_REMOTE_KEYS=1 (and COMPANION_MUTE_KEY, for a switch that sends another key).
"""
import argparse
import asyncio
import glob
import logging
import os
import struct

from aiohttp import web

from .audio import Microphone, Speaker, player_command
from .device import Device
from .web import make_app

EVENT = struct.Struct("llHHi")   # struct input_event on 64-bit Linux
EV_KEY = 1


KEY_PROG1 = 148         # the listen button (the gpio-key overlay on GPIO17)
KEY_MICMUTE = 248       # a mute switch, if the device has one (the gpio-key overlay on GPIO27)
# A remote's keys (a TV box): OK, the TV remote's Select (HDMI-CEC), Enter, the keypad's Enter, Play/Pause;
# and the volume and mute keys.
KEY_ENTER, KEY_KPENTER, KEY_PLAYPAUSE, KEY_OK, KEY_SELECT = 28, 96, 164, 352, 353
KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP = 113, 114, 115
REMOTE_KEYS = {KEY_VOLUMEUP: 0.1, KEY_VOLUMEDOWN: -0.1}
VOLUME_STEP_EVERY = 0.25     # seconds between volume steps while a volume key is held
EVIOCGBIT_KEY = 0x80604521   # which keys an input device has (96 bytes of bits)
EVIOCGKEY = 0x80604518       # which of its keys are down now


def _key_bits(fd, request):
    import fcntl
    try:
        return bytes(fcntl.ioctl(fd, request, bytearray(96)))
    except OSError:
        return b""


def _has_bit(bits, key):
    return len(bits) > key // 8 and bool(bits[key // 8] >> (key % 8) & 1)


def _switch(device, switches, path, held):
    """A mute switch's position. Alone (a Pi's switch), it is the switch. Among several devices that could
    send the switch's key (watch_inputs: the button board, and perhaps a headset's mute button), the
    microphone is off while any of them holds it down, and opening one that does not leaves another's alone."""
    if switches is None:
        device.set_switch(held)
        return
    switches[path] = held
    on = any(switches.values())
    if on != bool(getattr(device, "mic_switch", False)):
        device.set_switch(on)


async def read_inputs(path, device, button_key=KEY_PROG1, mute_key=KEY_MICMUTE, remote=False, retry=True,
                      switches=None):
    """The button and the mute switch, from a Linux input device: the button going down and up (the
    device tells a press from a hold, device.button_down and button_up), and the switch's position, read
    when the device is opened and then as it moves (device.set_switch). `button_key` is one key code or several
    (any of them is the button). With `remote`, a remote's volume keys step the volume (device.change_volume)
    and its mute key turns the microphone off or on (device.toggle_microphone). `switches`, shared between the
    devices watch_inputs reads, holds each one's switch (_switch). Reopens the device if it goes away (or,
    without `retry`, returns: watch_inputs looks for it again)."""
    log = logging.getLogger("companion.button")
    button_keys = set(button_key) if isinstance(button_key, (set, frozenset, list, tuple)) else {button_key}
    while True:
        try:
            with open(path, "rb", buffering=0) as f:
                log.info("reading the button and switch from %s", path)
                if _has_bit(_key_bits(f.fileno(), EVIOCGBIT_KEY), mute_key):
                    _switch(device, switches, path, _has_bit(_key_bits(f.fileno(), EVIOCGKEY), mute_key))
                loop = asyncio.get_running_loop()
                down, down_key, stepped = None, None, 0.0
                while True:
                    data = await loop.run_in_executor(None, f.read, EVENT.size)
                    if len(data) < EVENT.size:
                        break
                    sec, usec, kind, key, value = EVENT.unpack(data)
                    if kind != EV_KEY:
                        continue
                    when = sec + usec / 1e6          # the kernel's own time stamp
                    if remote and key in REMOTE_KEYS and value in (1, 2):
                        # A held volume key steps on, but not with every repeat (remotes repeat ten times a second).
                        if value == 1 or when - stepped >= VOLUME_STEP_EVERY:
                            stepped = when
                            device.change_volume(REMOTE_KEYS[key])
                        continue
                    if value == 2:                    # 2 is a key repeating while held
                        continue
                    if key == mute_key:
                        _switch(device, switches, path, value == 1)
                    elif remote and key == KEY_MUTE:
                        if value == 1:
                            device.toggle_microphone()
                    elif key in button_keys:
                        if value == 1 and down is None:
                            down, down_key = when, key
                            device.button_down()
                        elif value == 0 and down is not None and key == down_key:
                            held, down, down_key = when - down, None, None
                            device.button_up(held)
        except OSError as error:
            if not retry:
                log.info("input device %s went away (%s)", path, error)
                if switches is not None:
                    # Forgotten, but the microphone stays as it was: a board unplugged with its switch on
                    # leaves the microphone off until a switch says otherwise.
                    switches.pop(path, None)
                return
            log.warning("button device unavailable (%s); retrying", error)
        if not retry:
            if switches is not None:
                switches.pop(path, None)
            return
        await asyncio.sleep(2.0)


def find_inputs():
    """The input devices the gpio-key overlays create (the button, and a mute switch if fitted)."""
    paths = sorted(glob.glob("/dev/input/by-path/*button*-event")) + sorted(glob.glob("/dev/input/by-path/*gpio*-event"))
    return list(dict.fromkeys(paths))


def _device_keys(path):
    with open(path, "rb", buffering=0) as f:
        return _key_bits(f.fileno(), EVIOCGBIT_KEY)


def find_inputs_with(keys, paths=None, key_bits=_device_keys):
    """Every input device that has one of `keys`: a TV box's remote, the TV's own remote through HDMI-CEC, a USB
    button or a keyboard."""
    found = []
    for path in sorted(glob.glob("/dev/input/event*") if paths is None else paths):
        try:
            bits = key_bits(path)
        except OSError:
            continue
        if any(_has_bit(bits, key) for key in keys):
            found.append(path)
    return found


async def watch_inputs(device, button_keys, remote=False, every=5.0, find=find_inputs_with, mute_key=KEY_MICMUTE):
    """Read every input device that has a button key or the mute switch's key (or, with `remote`, a volume or
    mute key), and the ones plugged in later (a USB button, a keyboard, the desk device's button board)."""
    keys = set(button_keys) | {mute_key} | ({KEY_MUTE} | set(REMOTE_KEYS) if remote else set())
    reading, switches = {}, {}
    while True:
        for path in find(keys):
            if path not in reading or reading[path].done():
                reading[path] = asyncio.ensure_future(read_inputs(path, device, button_keys, mute_key=mute_key,
                                                                  remote=remote, retry=False, switches=switches))
        await asyncio.sleep(every)


def key_codes(text):
    """Key codes from "352,353,28" (or one number)."""
    codes = []
    for piece in str(text or "").replace(" ", "").split(","):
        if piece:
            codes.append(int(piece))
    return codes or [KEY_PROG1]


def lan_address():
    """This computer's address on its home network (the one it would use to reach it), or ""."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))     # nothing is sent: this only picks the way out
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser(description="The character device's core service.")
    parser.add_argument("--data", default="/var/lib/companion")
    parser.add_argument("--stage-socket", default="/run/companion/stage.sock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--tls-port", type=int, default=443,
                        help="the device's own HTTPS, for a phone's live camera when the owner turns it on (0: never)")
    parser.add_argument("--no-audio", action="store_true", help="no microphone or speaker (testing)")
    parser.add_argument("--no-updates", action="store_true",
                        help="never look for device updates (the device's software running on a computer)")
    parser.add_argument("--button", default=os.environ.get("COMPANION_BUTTON", "auto"),
                        help="input device for the button: 'auto' (the Pi's button and switch), 'any' (every device "
                             "with a button key: a TV box's remote, a USB button or a keyboard), 'none', or a device")
    parser.add_argument("--button-key", default=os.environ.get("COMPANION_BUTTON_KEYS", str(KEY_PROG1)),
                        help="key codes the button sends, separated by commas (148 = KEY_PROG1; a TV box's: "
                             "352,353,28,96,164)")
    mute_key = os.environ.get("COMPANION_MUTE_KEY", "").strip()
    parser.add_argument("--mute-key", type=int, default=int(mute_key) if mute_key.isdigit() else KEY_MICMUTE,
                        help="the key a mute switch holds down while the microphone is off (248 = KEY_MICMUTE: the "
                             "Pi's switch and the TV box's button board)")
    parser.add_argument("--remote-keys", action="store_true",
                        default=os.environ.get("COMPANION_REMOTE_KEYS", "").lower() in ("1", "yes", "on", "true"),
                        help="a remote's volume keys change the volume, and its mute key turns the microphone off or on")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    async def run():
        os.makedirs(args.data, exist_ok=True)
        speaker = Speaker(command=None) if args.no_audio else Speaker(command=player_command)
        microphone = None if args.no_audio else Microphone()
        from .network import WiFi
        import shutil
        wifi = WiFi() if shutil.which("nmcli") else None
        device = Device(args.data, stage_socket=args.stage_socket, speaker=speaker, microphone=microphone, wifi=wifi)
        device.tls_port = args.tls_port or None     # web.py: the live camera's HTTPS
        device.web_host = args.host
        device.updates = not args.no_updates
        if wifi is None:                            # a computer, not the device: phones reach it here
            device.lan_address = lan_address()
            if device.lan_address:                  # and the screen says where, with no setup to wait for
                device.setup_link = device.lan_address + ("" if args.port == 80 else ":%d" % args.port)
        device.start()
        keys = key_codes(args.button_key)
        if args.button == "any":
            asyncio.ensure_future(watch_inputs(device, keys, remote=args.remote_keys, mute_key=args.mute_key))
        else:
            inputs = find_inputs() if args.button == "auto" else ([] if args.button == "none" else [args.button])
            for path in inputs:
                asyncio.ensure_future(read_inputs(path, device, keys, mute_key=args.mute_key, remote=args.remote_keys))
        runner = web.AppRunner(make_app(device), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        logging.getLogger("companion").info("console on port %d", args.port)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
