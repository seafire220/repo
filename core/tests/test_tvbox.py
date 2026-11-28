"""The TV box variant (tvbox/README.md): the remote's keys and USB buttons, the address on an Ethernet cable,
and the box's own files (the kiosk's settings, the screen service's drop-in, the helpers, the boards list)."""
import asyncio
import configparser
import os
import re
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
        self.assertEqual(asked[0], set(BOX_KEYS) | {113, 114, 115}, "it looks for the volume and mute keys too")

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
        self.assertEqual((ini["output"]["name"], ini["output"]["mode"]), ("HDMI-A-1", "1920x1080@60"))
        self.assertEqual(ini["autolaunch"]["path"], "/usr/local/lib/companion-tvbox/stage-in-weston")
        self.assertEqual(ini["autolaunch"]["watch"], "true")
        with open(os.path.join(TVBOX, "config", "companion-stage-tvbox.conf")) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.assertEqual(lines[:3], ["[Service]", "ExecStart=", "ExecStart=/usr/bin/weston --config=/etc/companion/weston.ini"])

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
        for name in ("COMPANION_BUTTON", "COMPANION_BUTTON_KEYS", "COMPANION_REMOTE_KEYS"):
            self.assertIn('os.environ.get("%s"' % name, source)

    def test_the_boards_list_and_the_workflow_agree(self):
        with open(os.path.join(TVBOX, "boards.tsv")) as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
        keys = [r[0] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(r) == 3 and re.fullmatch(r"meson-sm1-[a-z0-9-]+\.dtb", r[1]) for r in rows), rows)
        self.assertEqual(keys[0], "x96-max-plus")
        with open(os.path.join(REPO, ".github", "workflows", "make-tvbox-image.yml")) as f:
            workflow = f.read()
        options = re.search(r"options: \[(x96-max-plus,.*?)\]", workflow, re.S).group(1)
        self.assertEqual([o.strip() for o in options.split(",")], keys, "the workflow offers every board in the list")
        with open(os.path.join(TVBOX, "build-image.sh")) as f:
            self.assertIn("BOARD=x96-max-plus", f.read())

    def test_the_scripts_are_well_formed(self):
        for name in ("install.sh", "build-image.sh", "check-image.sh", "check-on-box.sh", "bin/companion-tvbox-cec",
                     "bin/companion-tvbox-sound", "bin/stage-in-weston"):
            path = os.path.join(TVBOX, name)
            self.assertTrue(os.access(path, os.X_OK), name)
            shell = "bash" if name.endswith(".sh") else "sh"
            subprocess.run([shell, "-n", path], check=True)


if __name__ == "__main__":
    unittest.main()
