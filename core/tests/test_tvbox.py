"""The TV box variant (tvbox/README.md): the remote's keys and USB buttons, the desk device's button board, the
address on an Ethernet cable, and the box's own files (the kiosk's settings and the screen's size and turn, the
screen service's drop-in, the helpers, the installer card, the boards list)."""
import asyncio
import configparser
import hashlib
import importlib.util
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from companion import __main__ as main  # noqa: E402
from companion import network  # noqa: E402
from companion.__main__ import EVENT, find_inputs_with, key_codes, read_inputs, watch_inputs  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from fakes import FakeStage  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
TVBOX = os.path.join(REPO, "tvbox")
BOX_KEYS = [352, 353, 28, 96, 164]


class Recorder:
    """Stands in for the device: what the keys asked of it."""

    def __init__(self):
        self.calls = []

    def button_down(self):
        self.calls.append("down")

    def button_up(self, held):
        self.calls.append(("up", round(held, 1)))

    def set_switch(self, on):
        self.calls.append(("switch", on))

    def change_volume(self, step):
        self.calls.append(("volume", step))

    def toggle_microphone(self):
        self.calls.append("microphone")


def events(path, items):
    with open(path, "wb") as f:
        for t, key, value in items:
            f.write(EVENT.pack(int(t), int(round((t % 1) * 1e6)), 1, key, value))
            f.write(EVENT.pack(int(t), 0, 0, 0, 0))            # a sync event, ignored


class RemoteTest(unittest.IsolatedAsyncioTestCase):
    async def read(self, items, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "event")
            events(path, items)
            device = Recorder()
            await asyncio.wait_for(read_inputs(path, device, BOX_KEYS, retry=False, **kwargs), 5)
            return device.calls

    async def test_ok_enter_or_play_is_the_button_and_a_hold_is_timed(self):
        calls = await self.read([(10.0, 352, 1), (10.2, 352, 2), (10.4, 352, 0),          # OK, with a repeat
                                 (20.0, 28, 1), (23.0, 28, 0),                             # Enter, held 3 s
                                 (30.0, 164, 1), (30.1, 352, 1), (30.2, 352, 0), (30.5, 164, 0),   # Play, OK inside
                                 (40.0, 30, 1), (40.1, 30, 0)])                            # "a": not a button
        self.assertEqual(calls, ["down", ("up", 0.4), "down", ("up", 3.0), "down", ("up", 0.5)],
                         "one press at a time; other keys and repeats are ignored")

    async def test_the_remote_s_volume_and_mute_keys(self):
        calls = await self.read([(10.0, 115, 1)] + [(10.0 + 0.11 * k, 115, 2) for k in range(1, 10)] + [(11.1, 115, 0),
                                (12.0, 114, 1), (12.1, 114, 0), (13.0, 113, 1), (13.1, 113, 0)], remote=True)
        self.assertEqual(calls, [("volume", 0.1)] * 4 + [("volume", -0.1), "microphone"],
                         "held, a volume key steps every quarter of a second, not with every repeat")
        quiet = await self.read([(10.0, 115, 1), (10.1, 115, 0), (11.0, 113, 1)])
        self.assertEqual(quiet, [], "without remote keys turned on (a Pi), they do nothing")

    async def test_devices_are_found_by_their_keys_and_watched_as_they_come(self):
        bits = {"/dev/input/event0": {352, 115}, "/dev/input/event1": {30, 28}, "/dev/input/event2": {116}}

        def key_bits(path):
            if path == "/dev/input/event3":
                raise OSError("gone")
            out = bytearray(96)
            for key in bits[path]:
                out[key // 8] |= 1 << (key % 8)
            return bytes(out)
        paths = sorted(bits) + ["/dev/input/event3"]
        self.assertEqual(find_inputs_with({352, 28}, paths, key_bits), ["/dev/input/event0", "/dev/input/event1"])
        self.assertEqual(find_inputs_with({115}, paths, key_bits), ["/dev/input/event0"])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "event")
            events(path, [(5.0, 352, 1), (5.3, 352, 0)])
            device, asked = Recorder(), []

            def find(keys):
                asked.append(keys)
                return [path]
            task = asyncio.ensure_future(watch_inputs(device, BOX_KEYS, remote=True, every=0.05, find=find))
            await asyncio.sleep(0.3)
            task.cancel()
        self.assertIn(("up", 0.3), device.calls)
        self.assertEqual(asked[0], set(BOX_KEYS) | {113, 114, 115, 248},
                         "it looks for the volume and mute keys too, and the button board's mute switch")

    async def test_the_button_board_s_switch_is_held_and_another_device_cannot_undo_it(self):
        class Device(Recorder):
            mic_switch = False

            def set_switch(self, on):
                super().set_switch(on)
                self.mic_switch = on
        with tempfile.TemporaryDirectory() as d:
            board, headset = os.path.join(d, "board"), os.path.join(d, "headset")
            events(board, [(10.0, 248, 1), (10.5, 164, 1), (10.7, 164, 0)])     # the switch on, then listen
            events(headset, [(11.0, 248, 1), (11.1, 248, 0)])                    # a headset's mute button, pressed
            device, switches = Device(), {}
            await asyncio.wait_for(read_inputs(board, device, BOX_KEYS, retry=False, switches=switches), 5)
            self.assertEqual(device.calls, [("switch", True), "down", ("up", 0.2)])
            self.assertEqual(switches, {}, "a device that has gone is forgotten")
            switches[board] = True                                               # (as if it were still there)
            await asyncio.wait_for(read_inputs(headset, device, BOX_KEYS, retry=False, switches=switches), 5)
        self.assertEqual(device.calls[3:], [], "the headset's press and release leave the board's switch on")
        self.assertTrue(device.mic_switch)
        lone = Recorder()
        main._switch(lone, None, "gpio", False)
        self.assertEqual(lone.calls, [("switch", False)], "a Pi's switch alone is told as it is, as before")
        later = Device()
        later.mic_switch = True
        main._switch(later, {}, "board", False)
        self.assertEqual(later.calls, [("switch", False)], "and the board's switch turned off turns the microphone on")

    def test_key_codes(self):
        self.assertEqual(key_codes("352, 353,28"), [352, 353, 28])
        self.assertEqual(key_codes("148"), [148])
        self.assertEqual(key_codes(""), [148])


class DeviceTest(unittest.TestCase):
    def test_volume_and_microphone_from_the_remote(self):
        with tempfile.TemporaryDirectory() as d:
            stage = FakeStage()
            device = Device(d, speaker=Speaker(command=None, realtime=False), stage=stage)
            device.store.update_settings({"volume": 0.95})
            self.assertEqual(device.change_volume(0.1), 1.0, "no louder than full")
            self.assertEqual(device.change_volume(-0.1), 0.9)
            self.assertEqual((device.store.settings()["volume"], device.speaker.volume), (0.9, 0.9))
            hints = [c["overlay"] for c in stage.commands if isinstance(c, dict) and "overlay" in c]
            self.assertEqual(hints[-1]["slot"], "hint")
            device.toggle_microphone()
            self.assertTrue(device.muted)
            device.toggle_microphone()
            self.assertFalse(device.muted)


class SupportTest(unittest.TestCase):
    def test_the_report_says_what_the_box_has_and_names_no_bluetooth_device(self):
        from companion import support
        with tempfile.TemporaryDirectory() as d:
            proc, sys_class, etc = (os.path.join(d, n) for n in ("proc", "sys", "etc"))
            for folder in (os.path.join(proc, "bus", "input"), os.path.join(proc, "asound"), os.path.join(sys_class, "net", "eth0"),
                           os.path.join(sys_class, "net", "lo"), etc):
                os.makedirs(folder)
            with open(os.path.join(proc, "bus", "input", "devices"), "w") as f:
                f.write('I: Bus=0019 Vendor=0001\nN: Name="meson-ir"\nH: Handlers=kbd event0\n\n'
                        'I: Bus=0005 Vendor=0002\nN: Name="Sam\'s keyboard"\nH: Handlers=kbd event1\n')
            with open(os.path.join(proc, "asound", "cards"), "w") as f:
                f.write(" 0 [X96MAX         ]: axg-sound-card - X96-MAX\n")
            with open(os.path.join(etc, "companion-tvbox"), "w") as f:
                f.write("board=x96-max-plus\ndtb=meson-sm1-x96-max-plus.dtb\n")
            device = Device(d, speaker=Speaker(command=None, realtime=False), stage=FakeStage())
            facts = support.hardware(device, proc, sys_class, etc)
        self.assertEqual(facts["inputs"], ["meson-ir", "a Bluetooth device"])
        self.assertIn("axg-sound-card", facts["sound_cards"])
        self.assertEqual(facts["network"], ["eth0"])
        self.assertTrue(facts["tv_box"].startswith("board=x96-max-plus"))


class AddressTest(unittest.IsolatedAsyncioTestCase):
    async def test_on_an_ethernet_cable_the_address_in_use_is_told(self):
        async def run(*args, timeout=40):
            return "IP4.ADDRESS[1]:\n" if "wlan0" in args else ""
        original = network.outgoing_address
        network.outgoing_address = lambda: "192.168.1.77"
        try:
            self.assertEqual(await network.WiFi(run=run).address(), "192.168.1.77")

            async def wifi(*args, timeout=40):
                return "IP4.ADDRESS[1]:192.168.1.20/24\n"
            self.assertEqual(await network.WiFi(run=wifi).address(), "192.168.1.20", "Wi-Fi's own first")
        finally:
            network.outgoing_address = original


class FilesTest(unittest.TestCase):
    def test_the_kiosk_settings(self):
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(os.path.join(TVBOX, "config", "weston.ini"))
        self.assertEqual(ini["core"]["shell"], "kiosk-shell.so")
        self.assertEqual((ini["core"]["idle-time"], ini["core"]["require-input"]), ("0", "false"))
        self.assertEqual((ini["output"]["name"], ini["output"]["mode"]), ("HDMI-A-1", "auto"))
        self.assertNotIn("transform", ini["output"], "not turned unless asked")
        self.assertEqual(ini["autolaunch"]["path"], "/usr/local/lib/companion-tvbox/stage-in-weston")
        self.assertEqual(ini["autolaunch"]["watch"], "true")
        with open(os.path.join(TVBOX, "config", "companion-stage-tvbox.conf")) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.assertEqual(lines, ["[Service]", "EnvironmentFile=-/etc/default/companion",
                                 "ExecStartPre=+/usr/local/lib/companion-tvbox/companion-tvbox-screen",
                                 "ExecStart=", "ExecStart=/usr/bin/weston --config=/run/companion-tvbox/weston.ini",
                                 "MemoryMax=450M"])
        with open(os.path.join(TVBOX, "install.sh")) as f:
            install = f.read()
        old = re.search(r"OLD_DEFAULT=([0-9a-f]{64})", install).group(1)
        with open(os.path.join(TVBOX, "config", "weston.ini"), "rb") as f:
            self.assertNotEqual(hashlib.sha256(f.read()).hexdigest(), old,
                                "the earlier settings are replaced only when they are the earlier version's own")

    def test_the_character_s_program_runs_with_the_screen_service_s_own_arguments(self):
        unit = os.path.join(REPO, "device", "systemd", "companion-stage.service")
        with open(unit) as f:
            expected = re.search(r"^ExecStart=.* -- (/.*)$", f.read(), re.M).group(1)
        script = os.path.join(TVBOX, "bin", "stage-in-weston")
        out = subprocess.run(["sh", script], env=dict(os.environ, COMPANION_DRY_RUN="1", COMPANION_STAGE_UNIT=unit),
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, expected)
        self.assertTrue(out.startswith("/opt/companion/bin/stage /var/lib/companion/current.inp --fullscreen"))
        missing = subprocess.run(["sh", script], env=dict(os.environ, COMPANION_DRY_RUN="1", COMPANION_STAGE_UNIT="/nope"),
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(missing, expected, "the same arguments, if that file cannot be read")

    def test_the_box_s_sound_goes_to_hdmi(self):
        with tempfile.TemporaryDirectory() as d:
            cards = os.path.join(d, "cards")
            with open(cards, "w") as f:
                f.write(" 0 [X96MAX         ]: axg-sound-card - X96-MAX\n                      X96-MAX\n"
                        " 1 [Lite           ]: USB-Audio - ReSpeaker Lite\n")
            script = os.path.join(TVBOX, "bin", "companion-tvbox-sound")
            env = dict(os.environ, COMPANION_DRY_RUN="1", COMPANION_ASOUND_CARDS=cards)
            out = subprocess.run(["sh", script], env=env, capture_output=True, text=True, check=True).stdout
            self.assertIn("amixer -c X96MAX sset 'TOHDMITX I2S SRC' 'I2S B'", out)
            self.assertIn("amixer -c X96MAX sset 'FRDDR_A SINK 1 SEL' 'OUT 1'", out)
            with open(cards, "w") as f:
                f.write(" 0 [Lite           ]: USB-Audio - ReSpeaker Lite\n")
            out = subprocess.run(["sh", script], env=env, capture_output=True, text=True, check=True).stdout
            self.assertIn("nothing to do", out)

    def test_the_box_s_settings_are_ones_the_core_reads(self):
        with open(os.path.join(TVBOX, "config", "companion-tvbox.default")) as f:
            settings = dict(l.strip().split("=", 1) for l in f if re.match(r"^[A-Z_]+=", l))
        self.assertEqual(settings["COMPANION_BUTTON"], "any")
        self.assertEqual(key_codes(settings["COMPANION_BUTTON_KEYS"]),
                         [main.KEY_OK, main.KEY_SELECT, main.KEY_ENTER, main.KEY_KPENTER, main.KEY_PLAYPAUSE])
        self.assertEqual((settings["COMPANION_REMOTE_KEYS"], settings["COMPANION_USB"]), ("1", "off"))
        with open(os.path.join(REPO, "core", "companion", "__main__.py")) as f:
            source = f.read()
        for name in ("COMPANION_BUTTON", "COMPANION_BUTTON_KEYS", "COMPANION_REMOTE_KEYS", "COMPANION_MUTE_KEY"):
            self.assertIn('os.environ.get("%s"' % name, source)
        with open(os.path.join(TVBOX, "config", "companion-tvbox.default")) as f:
            text = f.read()
        self.assertIn("#COMPANION_SCREEN_ROTATE=90", text)
        self.assertIn("#COMPANION_SCREEN_MODE=", text)
        with open(os.path.join(TVBOX, "bin", "companion-tvbox-screen")) as f:
            script = f.read()
        for name in ("COMPANION_SCREEN_MODE", "COMPANION_SCREEN_ROTATE"):
            self.assertIn(name, script)

    def test_the_boards_list_and_the_workflow_agree(self):
        with open(os.path.join(TVBOX, "boards.tsv")) as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
        keys = [r[0] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(r) == 4 and re.fullmatch(r"meson-sm1-[a-z0-9-]+\.dtb", r[1]) for r in rows), rows)
        numbers = [r[2] for r in rows]
        self.assertTrue(all(re.fullmatch(r"5[0-9]{2}", n) for n in numbers), "the installer's numbers for S905X3 boxes")
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(rows[0][2], "502", "the gigabit X96 Max Plus is 502 in the community installer's list")
        self.assertEqual(keys[0], "x96-max-plus")
        with open(os.path.join(REPO, ".github", "workflows", "make-tvbox-image.yml")) as f:
            workflow = f.read()
        options = re.search(r"options: \[(x96-max-plus,.*?)\]", workflow, re.S).group(1)
        self.assertEqual([o.strip() for o in options.split(",")], keys, "the workflow offers every board in the list")
        with open(os.path.join(TVBOX, "build-image.sh")) as f:
            self.assertIn("BOARD=x96-max-plus", f.read())

    def test_the_scripts_are_well_formed(self):
        for name in ("install.sh", "build-image.sh", "check-image.sh", "check-on-box.sh", "bin/companion-tvbox-cec",
                     "bin/companion-tvbox-sound", "bin/stage-in-weston", "bin/companion-tvbox-screen",
                     "bin/companion-tvbox-install"):
            path = os.path.join(TVBOX, name)
            self.assertTrue(os.access(path, os.X_OK), name)
            with open(path) as f:
                shell = "bash" if "bash" in f.readline() else "sh"
            subprocess.run([shell, "-n", path], check=True)


def load_button_board():
    spec = importlib.util.spec_from_file_location("button_board", os.path.join(TVBOX, "button-board", "buttons.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hid_items(descriptor):
    """The short items of a HID report descriptor: (type, tag, value)."""
    items, i = [], 0
    while i < len(descriptor):
        prefix = descriptor[i]
        size = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(descriptor[i + 1:i + 1 + size], "little")
        items.append(((prefix >> 2) & 3, prefix >> 4, value))
        i += 1 + size
    return items


# How Linux's HID driver names these usages (drivers/hid/hid-input.c): (usage page, usage) -> key code.
LINUX_KEYS = {(0x0C, 0xCD): 164, (0x0C, 0x30): 116, (0x0B, 0x2F): 248}


class ButtonBoardTest(unittest.TestCase):
    """tvbox/button-board: the desk device's buttons and mute switch on a USB board."""

    def test_the_report_is_one_byte_of_keys_linux_already_knows(self):
        board = load_button_board()
        page, usages, inputs, bits, size, count, report_ids, depth = None, [], [], 0, 0, 0, [], 0
        for kind, tag, value in hid_items(board.REPORT_DESCRIPTOR):
            if (kind, tag) == (1, 0):
                page = value                      # Usage Page
            elif (kind, tag) == (1, 7):
                size = value                      # Report Size
            elif (kind, tag) == (1, 9):
                count = value                     # Report Count
            elif (kind, tag) == (1, 8):
                report_ids.append(value)          # Report ID
            elif (kind, tag) == (2, 0):
                usages.append((page, value))      # Usage
            elif (kind, tag) == (0, 10):
                depth += 1                        # Collection
            elif (kind, tag) == (0, 12):
                depth -= 1                        # End Collection
            elif (kind, tag) == (0, 8):           # Input
                constant = value & 1
                if not constant:
                    inputs.extend(usages[-count:] if depth else [])
                bits += size * count
                usages = [u for u in usages if u not in inputs]
        self.assertEqual(depth, 0)
        self.assertEqual(report_ids, [board.REPORT_ID])
        self.assertEqual(bits, 8, "one byte, as boot.py tells CircuitPython (in_report_lengths=(1,))")
        self.assertEqual(hid_items(board.REPORT_DESCRIPTOR)[:2], [(1, 0, board.USAGE_PAGE), (2, 0, board.USAGE)])
        keys = [LINUX_KEYS[u] for u in inputs]
        self.assertEqual(keys, [main.KEY_PLAYPAUSE, 116, main.KEY_MICMUTE],
                         "bit 0 the listen button, bit 1 power (KEY_POWER), bit 2 the mute switch (the Pi's key)")
        self.assertIn(main.KEY_PLAYPAUSE, BOX_KEYS, "the listen button is already one of the box's button keys")
        self.assertNotIn(0x07, {u[0] for u in inputs}, "no keyboard keys: Linux would repeat a held switch")
        self.assertEqual([board.LISTEN_BIT, board.POWER_BIT, board.MUTE_BIT], [1, 2, 4])

    def test_the_buttons_and_the_switch(self):
        board = load_button_board()
        b = board.Buttons()
        self.assertEqual(b.report(0.0), 0, "the state goes out at once, so the box knows where the switch is")
        b.sent(0, 0.0)
        self.assertIsNone(b.report(0.1))
        b.event(board.LISTEN, True, 0.2)
        self.assertEqual((b.report(0.2), b.colour()), (1, board.LISTENING))
        b.sent(1, 0.2)
        b.event(board.LISTEN, False, 0.5)
        self.assertEqual(b.report(0.5), 0)
        b.sent(0, 0.5)
        b.event(board.MUTE, True, 1.0)
        self.assertEqual((b.report(1.0), b.colour()), (4, board.MUTED))
        b.sent(4, 1.0)
        self.assertIsNone(b.report(1.5))
        self.assertEqual(b.report(2.0), 4, "and again every second: a box that has just started learns it")
        b.sent(4, 2.0)
        # The power button: nothing for a knock; held a second, Power goes; let go, it is released.
        b.event(board.POWER, True, 3.0)
        self.assertEqual(b.colour(), board.POWERING)
        self.assertEqual(b.report(3.0), 4, "(sent again: a second since the last)")
        b.sent(4, 3.0)
        self.assertIsNone(b.report(3.5), "not yet held a second")
        self.assertEqual(b.report(4.0), 4 | 2)
        b.sent(6, 4.0)
        b.event(board.POWER, False, 4.2)
        self.assertEqual(b.report(4.2), 4)
        b.sent(4, 4.2)
        b.event(board.POWER, True, 4.5)
        b.event(board.POWER, False, 4.9)
        self.assertIsNone(b.report(4.9), "a knock on the power button sends nothing")
        self.assertEqual(b.report(5.3), 4, "and the state sent again holds no Power")
        b.event(board.MUTE, False, 6.0)
        self.assertEqual((b.report(6.0), b.colour()), (0, board.DARK))

    def test_the_board_s_files(self):
        folder = os.path.join(TVBOX, "button-board")
        for name in ("boot.py", "code.py", "buttons.py"):
            with open(os.path.join(folder, name)) as f:
                compile(f.read(), name, "exec")           # CircuitPython's Python is a subset of this one
        with open(os.path.join(folder, "boot.py")) as f:
            boot = f.read()
        for needed in ("usb_cdc.disable()", "storage.disable_usb_drive()", "in_report_lengths=(1,)",
                       "report_descriptor=buttons.REPORT_DESCRIPTOR", 'product="Button board"'):
            self.assertIn(needed, boot)
        with open(os.path.join(folder, "code.py")) as f:
            code = f.read()
        self.assertIn("READ = (board.GP0, board.GP2, board.GP4)", code)
        self.assertIn("ZERO = (board.GP1, board.GP3, board.GP5)", code)
        with open(os.path.join(folder, "README.md")) as f:
            guide = f.read()
        for pins in ("**GP0** and **GP1**", "**GP2** and **GP3**", "**GP4** and **GP5**"):
            self.assertIn(pins, guide)


def run_screen(drm=None, template=None, **env):
    script = os.path.join(TVBOX, "bin", "companion-tvbox-screen")
    variables = dict(os.environ, COMPANION_DRY_RUN="1", COMPANION_DRM=drm or "/nonexistent",
                     COMPANION_WESTON_TEMPLATE=template or os.path.join(TVBOX, "config", "weston.ini"))
    for name in ("COMPANION_SCREEN_MODE", "COMPANION_SCREEN_ROTATE"):
        variables.pop(name, None)
    variables.update(env)
    done = subprocess.run(["sh", script], env=variables, capture_output=True, text=True, check=True)
    ini = configparser.ConfigParser(interpolation=None)
    ini.read_string(done.stdout)
    return ini, done.stderr


class ScreenTest(unittest.TestCase):
    """bin/companion-tvbox-screen: the picture at the screen's own size, never more than 1080p, turned if asked."""

    def drm(self, d, modes, status="connected", name="card1-HDMI-A-1"):
        folder = os.path.join(d, name)
        os.makedirs(folder)
        with open(os.path.join(folder, "status"), "w") as f:
            f.write(status + "\n")
        with open(os.path.join(folder, "modes"), "w") as f:
            f.write("".join(m + "\n" for m in modes))
        return d

    def test_a_small_screen_gets_its_own_size_and_a_4k_tv_gets_1080p(self):
        with tempfile.TemporaryDirectory() as d:
            self.drm(d, [], status="disconnected", name="card0-HDMI-A-1")
            self.drm(d, ["1024x600", "1920x1080", "1280x720", "800x600"])
            ini, _ = run_screen(d)
            self.assertEqual(ini["output"]["mode"], "1024x600",
                             "the 7-inch screen's own size, not the 1080p it accepts")
            self.assertEqual(ini["output"]["name"], "HDMI-A-1")
            self.assertEqual(ini["core"]["shell"], "kiosk-shell.so")
            self.assertEqual(ini["autolaunch"]["path"], "/usr/local/lib/companion-tvbox/stage-in-weston")
        with tempfile.TemporaryDirectory() as d:
            self.drm(d, ["3840x2160", "4096x2160", "2560x1440", "1920x1080", "1920x1080i", "1280x720"])
            self.assertEqual(run_screen(d)[0]["output"]["mode"], "1920x1080")
        with tempfile.TemporaryDirectory() as d:
            self.drm(d, ["2560x1440", "1600x900", "1280x720"])
            self.assertEqual(run_screen(d)[0]["output"]["mode"], "1600x900", "no 1080p: the biggest that fits")
        with tempfile.TemporaryDirectory() as d:
            self.drm(d, ["1080x1920", "720x1280"])
            self.assertEqual(run_screen(d)[0]["output"]["mode"], "1080x1920", "a tall screen of 1080p's size is fine")
        self.assertEqual(run_screen()[0]["output"]["mode"], "1920x1080", "no screen yet: 1080p, as before")

    def test_the_owner_s_choices(self):
        with tempfile.TemporaryDirectory() as d:
            self.drm(d, ["1024x600", "1920x1080"])
            self.assertEqual(run_screen(d, COMPANION_SCREEN_MODE="1280x720")[0]["output"]["mode"], "1280x720")
            template = os.path.join(d, "weston.ini")
            with open(os.path.join(TVBOX, "config", "weston.ini")) as f:
                text = f.read()
            with open(template, "w") as f:
                f.write(text.replace("mode=auto", "mode=800x600")
                        .replace("#transform=rotate-90", "transform=rotate-270"))
            ini, _ = run_screen(d, template)
            self.assertEqual((ini["output"]["mode"], ini["output"]["transform"]), ("800x600", "rotate-270"),
                             "a size and a turn written in /etc/companion/weston.ini are kept")
            ini, _ = run_screen(d, template, COMPANION_SCREEN_ROTATE="90")
            self.assertEqual(ini["output"]["transform"], "rotate-90", "the setting wins over the file")
            ini, _ = run_screen(d, COMPANION_SCREEN_ROTATE="90")
            self.assertEqual((ini["output"]["mode"], ini["output"]["transform"]), ("1024x600", "rotate-90"))
            ini, warning = run_screen(d, COMPANION_SCREEN_ROTATE="45")
            self.assertNotIn("transform", ini["output"])
            self.assertIn("not 0, 90, 180 or 270", warning)
            ini, warning = run_screen(d, COMPANION_SCREEN_MODE="1280x720\n[autolaunch]\npath=/bin/false")
            self.assertEqual((ini["output"]["mode"], ini["autolaunch"]["path"]),
                             ("1024x600", "/usr/local/lib/companion-tvbox/stage-in-weston"), "nothing else gets in")
            self.assertIn("not a mode Weston knows", warning)
            ini, _ = run_screen(d, os.path.join(d, "missing.ini"), COMPANION_SCREEN_ROTATE="270")
            self.assertEqual((ini["core"]["shell"], ini["output"]["mode"], ini["output"]["transform"]),
                             ("kiosk-shell.so", "1024x600", "rotate-270"),
                             "without the file, the settings it would have")

    def test_it_writes_the_settings_weston_reads(self):
        with tempfile.TemporaryDirectory() as d:
            self.drm(os.path.join(d, "drm"), ["1024x600"])
            out = os.path.join(d, "run", "companion-tvbox", "weston.ini")
            env = dict(os.environ, COMPANION_DRM=os.path.join(d, "drm"), COMPANION_WESTON_CONFIG=out,
                       COMPANION_WESTON_TEMPLATE=os.path.join(TVBOX, "config", "weston.ini"),
                       COMPANION_SCREEN_ROTATE="90")
            env.pop("COMPANION_DRY_RUN", None)
            done = subprocess.run(["sh", os.path.join(TVBOX, "bin", "companion-tvbox-screen")], env=env,
                                  capture_output=True, text=True, check=True)
            self.assertEqual(done.stdout.strip(), "the screen: 1024x600, rotate-90")
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o644, "Weston runs as the service's user")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(out)).st_mode), 0o755)
            ini = configparser.ConfigParser(interpolation=None)
            ini.read(out)
            self.assertEqual(ini["output"]["mode"], "1024x600")


FAKES = {
    "findmnt": 'echo "${FAKE_ROOT_SOURCE:-/dev/mmcblk1p2}"\n',
    "armbian-install": 'echo "$*" > "$FAKE_LOG/args"\ncat > "$FAKE_LOG/answers"\n'
                       'if [ -n "${FAKE_FAIL:-}" ]; then echo "[ ERROR ] no space"; exit 1; fi\n'
                       'echo "[ SUCCESS ] Installation completed successfully."\n',
    "mount": 'echo "mount $*" >> "$FAKE_LOG/calls"\n'
             'for target; do :; done\n'
             'case "$*" in\n'
             '  *"-o ro"*) [ -n "${FAKE_INSTALLED:-}" ] || exit 32\n'
             '    mkdir -p "$target/etc"; : > "$target/etc/companion-tvbox" ;;\n'
             '  *) mkdir -p "$target/etc/companion"; : > "$target/etc/companion/installer" ;;\n'
             'esac\n',
    "umount": 'echo "umount $*" >> "$FAKE_LOG/calls"\n'
              'if [ -e "$1/etc/companion" ] && [ ! -e "$1/etc/companion/installer" ]; then\n'
              '    echo "copy is not an installer" >> "$FAKE_LOG/calls"\n'
              'fi\n',
    "systemctl": 'echo "systemctl $*" >> "$FAKE_LOG/calls"\n',
    "sleep": 'echo "sleep $*" >> "$FAKE_LOG/calls"\n',
}


class InstallerTest(unittest.TestCase):
    """bin/companion-tvbox-install: an installer card copies itself onto the box's own memory, once."""

    def run_installer(self, d, mark="MODEL_ID=502\n", emmc=True, **env):
        bin_dir, dev, root, log = (os.path.join(d, n) for n in ("bin", "dev", "root", "log"))
        for folder in (bin_dir, dev, log, os.path.join(root, "etc"), os.path.join(root, "var", "lib", "dbus"),
                       os.path.join(root, "var", "lib", "systemd"), os.path.join(root, "var", "lib", "NetworkManager")):
            os.makedirs(folder, exist_ok=True)
        for name, body in FAKES.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)
        if emmc:
            open(os.path.join(dev, "mmcblk2boot0"), "w").close()
        for name, text in (("etc/machine-id", "card-identity\n"), ("var/lib/dbus/machine-id", "card-identity\n"),
                           ("var/lib/systemd/random-seed", "seed"), ("var/lib/NetworkManager/eth0.lease", "lease")):
            with open(os.path.join(root, name), "w") as f:
                f.write(text)
        mark_path = os.path.join(d, "installer")
        if mark is not None:
            with open(mark_path, "w") as f:
                f.write("# An installer card\n" + mark)
        variables = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], COMPANION_INSTALLER=mark_path,
                         COMPANION_DEV=dev, COMPANION_ROOT=root, COMPANION_INSTALL_LOG=os.path.join(d, "install.log"),
                         COMPANION_INSTALL_MOUNT=os.path.join(d, "mnt"), COMPANION_INSTALL_WAIT="0",
                         COMPANION_INSTALL_NO_WAIT="1", FAKE_LOG=log)
        variables.update(env)
        done = subprocess.run(["bash", os.path.join(TVBOX, "bin", "companion-tvbox-install")], env=variables,
                              capture_output=True, text=True, timeout=30)

        def read(name):
            path = os.path.join(log, name)
            if not os.path.exists(path):
                return ""
            with open(path) as f:
                return f.read()
        return done, read, root

    def test_a_card_copies_itself_onto_the_box_s_memory_and_switches_the_box_off(self):
        with tempfile.TemporaryDirectory() as d:
            done, read, root = self.run_installer(d)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(read("args").split(), ["-m", "no", "-a", "yes"], "the box's own boot loader kept")
            self.assertEqual(read("answers"), "502\n1\n", "the box's number, then ext4")
            with open(os.path.join(root, "etc", "machine-id")) as f:
                self.assertEqual(f.read(), "", "each unit makes its own identity at its first start")
            for gone in ("var/lib/dbus/machine-id", "var/lib/systemd/random-seed", "var/lib/NetworkManager/eth0.lease"):
                self.assertFalse(os.path.exists(os.path.join(root, gone)), gone)
            calls = read("calls")
            self.assertIn("copy is not an installer", calls)
            self.assertTrue(calls.rstrip().endswith("systemctl poweroff"), calls)
            self.assertIn("Android on it is erased", done.stdout)
            self.assertIn("Then take the card out", done.stdout)

    def test_it_does_nothing_where_it_should_not(self):
        with tempfile.TemporaryDirectory() as d:
            done, read, root = self.run_installer(d, mark=None)
            self.assertEqual((done.returncode, done.stdout, read("args")), (0, "", ""), "not an installer card")
        with tempfile.TemporaryDirectory() as d:
            done, read, root = self.run_installer(d, FAKE_ROOT_SOURCE="/dev/mmcblk2p2")
            self.assertEqual((done.returncode, done.stdout, read("args")), (0, "", ""),
                             "a unit started from its own memory just starts")
            with open(os.path.join(root, "etc", "machine-id")) as f:
                self.assertEqual(f.read(), "card-identity\n")

    def test_it_stops_and_says_why(self):
        cases = (({"FAKE_INSTALLED": "1"}, {}, "already has the character device"),
                 ({"FAKE_FAIL": "1"}, {}, "The copy did not finish"),
                 ({}, {"emmc": False}, "no memory of its own"),
                 ({}, {"mark": "MODEL_ID=\n"}, "does not say which box"))
        for env, options, words in cases:
            with tempfile.TemporaryDirectory() as d:
                done, read, root = self.run_installer(d, **options, **env)
                self.assertEqual(done.returncode, 1, words)
                self.assertIn(words, done.stdout)
                self.assertNotIn("poweroff", read("calls"), "the box stays on, with the words on the screen")
                if "FAKE_FAIL" not in env:
                    self.assertEqual(read("args"), "", "nothing was copied")

    def test_the_service_and_the_card(self):
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(os.path.join(TVBOX, "config", "companion-tvbox-install.service"))
        self.assertEqual(ini["Unit"]["ConditionPathExists"], "/etc/companion/installer")
        self.assertIn("companion-core.service", ini["Unit"]["Before"])
        self.assertIn("companion-stage.service", ini["Unit"]["Before"])
        self.assertEqual((ini["Service"]["Type"], ini["Service"]["TimeoutStartSec"]), ("oneshot", "infinity"))
        self.assertEqual(ini["Service"]["ExecStart"], "/usr/local/lib/companion-tvbox/companion-tvbox-install")
        with open(os.path.join(TVBOX, "install.sh")) as f:
            install = f.read()
        self.assertIn("MODEL_ID=%s", install)
        self.assertIn("companion-tvbox-install.service", install)
        with open(os.path.join(TVBOX, "build-image.sh")) as f:
            build = f.read()
        self.assertIn('KEY_ARG+=(--installer "$MODEL_ID")', build)
        self.assertIn("$1 == key {print $3}", build)


if __name__ == "__main__":
    unittest.main()
