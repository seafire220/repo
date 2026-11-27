"""Run the device's core service.

    python -m companion --data /var/lib/companion --port 80

The button is read from a Linux input device: the Pi's `gpio-key` overlay
turns a GPIO press into a key press (see docs/13-build-and-install.md). A mute
switch, if fitted, is a second one (key 248). On a desktop, any keyboard device
and key code can be used instead, and the console's "button" does the same job.
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


async def read_inputs(path, device, button_key=KEY_PROG1, mute_key=KEY_MICMUTE):
    """The button and the mute switch, from a Linux input device: the button going down and up (the
    device tells a press from a hold, device.button_down and button_up), and the switch's position, read
    when the device is opened and then as it moves (device.set_switch). Reopens the device if it goes away."""
    log = logging.getLogger("companion.button")
    while True:
        try:
            with open(path, "rb", buffering=0) as f:
                log.info("reading the button and switch from %s", path)
                if _has_bit(_key_bits(f.fileno(), EVIOCGBIT_KEY), mute_key):
                    device.set_switch(_has_bit(_key_bits(f.fileno(), EVIOCGKEY), mute_key))
                loop = asyncio.get_running_loop()
                down = None
                while True:
                    data = await loop.run_in_executor(None, f.read, EVENT.size)
                    if len(data) < EVENT.size:
                        break
                    sec, usec, kind, key, value = EVENT.unpack(data)
                    if kind != EV_KEY or value == 2:          # 2 is a key repeating while held
                        continue
                    when = sec + usec / 1e6          # the kernel's own time stamp
                    if key == mute_key:
                        device.set_switch(value == 1)
                    elif key == button_key:
                        if value == 1:
                            down = when
                            device.button_down()
                        elif down is not None:
                            held, down = when - down, None
                            device.button_up(held)
        except OSError as error:
            log.warning("button device unavailable (%s); retrying", error)
        await asyncio.sleep(2.0)


def find_inputs():
    """The input devices the gpio-key overlays create (the button, and a mute switch if fitted)."""
    paths = sorted(glob.glob("/dev/input/by-path/*button*-event")) + sorted(glob.glob("/dev/input/by-path/*gpio*-event"))
    return list(dict.fromkeys(paths))


def main():
    parser = argparse.ArgumentParser(description="The character device's core service.")
    parser.add_argument("--data", default="/var/lib/companion")
    parser.add_argument("--stage-socket", default="/run/companion/stage.sock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--tls-port", type=int, default=443,
                        help="the device's own HTTPS, for a phone's live camera when the owner turns it on (0: never)")
    parser.add_argument("--no-audio", action="store_true", help="no microphone or speaker (testing)")
    parser.add_argument("--button", default="auto", help="input device for the button, 'auto' or 'none'")
    parser.add_argument("--button-key", type=int, default=148, help="key code the button sends (148 = KEY_PROG1)")
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
        device.start()
        inputs = find_inputs() if args.button == "auto" else ([] if args.button == "none" else [args.button])
        for path in inputs:
            asyncio.ensure_future(read_inputs(path, device, args.button_key))
        runner = web.AppRunner(make_app(device), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        logging.getLogger("companion").info("console on port %d", args.port)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
