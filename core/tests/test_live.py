"""Copying a person live: the device's own HTTPS for a phone's live camera, the live engine, face-tracking
apps on a phone, and the routes and WebSocket that tie them together."""
import asyncio
import datetime
import json
import math
import os
import socket
import ssl
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import aiohttp  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import live_motion, mocap, motions, package, phone_apps, tls  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeStage  # noqa: E402
from test_package import good_files, manifest  # noqa: E402
from test_phone_record import body_points, face_points  # noqa: E402

H = {"X-Companion": "1"}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Stage:
    def __init__(self):
        self.sent = []

    def send(self, command):
        self.sent.append(command)
        return True

    def live(self):
        return [c["live"] for c in self.sent if "live" in c]


@unittest.skipUnless(tls.available(), "openssl is not installed")
class CertificateTest(unittest.TestCase):
    def test_made_once_to_the_rules_phones_apply(self):
        with tempfile.TemporaryDirectory() as d:
            cert, key, made = tls.ensure(d, addresses=["192.168.1.23"])
            self.assertTrue(made)
            self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600, "the key is the service's alone")
            self.assertFalse(tls.ensure(d)[2], "kept while it has more than a month to run")
            self.assertRegex(tls.fingerprint(cert), r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
            info = ssl._ssl._test_decode_cert(cert)
            self.assertIn(("DNS", "companion.local"), info["subjectAltName"])
            self.assertIn(("IP Address", "192.168.1.23"), info["subjectAltName"])
            expires = datetime.datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z")
            self.assertLessEqual((expires - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).days, 825,
                                 "phones refuse server certificates that last longer")
            tls.server_context(cert, key)
            later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=810)
            self.assertTrue(tls.ensure(d, now=later)[2], "made again in its last month")
            self.assertEqual(sorted(os.listdir(d)), sorted([tls.CERT, tls.KEY]), "nothing left behind")


def frame(turn=0.0, smile=0.0, arm=0.0):
    """One frame's controls, as mocap makes them from a phone's points."""
    face = {i: (x * 720, y * 1280) for i, (x, y) in zip(mocap.FACE_POINTS, face_points(turn=turn))}
    blend = {name: 0.0 for name in mocap.BLENDSHAPES}
    blend["mouthSmileLeft"] = blend["mouthSmileRight"] = smile
    pose = {name: (p[0] * 720, p[1] * 1280, p[2], p[3]) for name, p in zip(mocap.POSE, body_points(arm_up=arm))}
    return mocap.frame_controls(face, blend, pose)


class LiveMotionTest(unittest.TestCase):
    def setUp(self):
        self.clock, self.stage = Clock(), Stage()
        self.speaking = False
        self.live = live_motion.LiveMotion(self.stage, clock=self.clock, speaking=lambda: self.speaking)

    def feed(self, seconds, fps=30, kind="browser", **kw):
        for _ in range(int(seconds * fps)):
            self.clock.t += 1.0 / fps
            self.live.frame(frame(**kw), kind)

    def test_it_takes_the_resting_pose_then_copies_what_changes(self):
        self.live.begin("browser", mirror=False)
        self.feed(0.9, turn=0.02)
        self.assertEqual(self.stage.live(), [], "the first second only gives the resting pose")
        self.assertTrue(self.live.status()["calibrating"])
        self.feed(0.3, turn=0.02)
        still = self.stage.live()[-1]
        self.assertLess(abs(still["head_yaw"]), 0.02, "at rest it is the character's rest: %s" % still["head_yaw"])
        self.feed(1.0, turn=0.14, smile=0.8)
        moved = self.stage.live()[-1]
        self.assertGreater(moved["head_yaw"], 0.5, "turned towards the picture's right")
        self.assertGreater(moved["smile"], 0.5)
        self.assertFalse(self.live.status()["calibrating"])

    def test_like_a_mirror_turns_the_other_way(self):
        self.live.begin("browser", mirror=True)
        self.feed(1.2)
        self.feed(1.0, turn=0.14, arm=1.0)
        moved = self.stage.live()[-1]
        self.assertLess(moved["head_yaw"], -0.5)
        self.assertGreater(moved["arm_left"], 0.5, "the picture's right arm is the reflection's left")
        self.live.set_mirror(False)
        self.feed(1.0, turn=0.14, arm=1.0)
        self.assertGreater(self.stage.live()[-1]["head_yaw"], 0.5)

    def test_no_more_than_thirty_updates_a_second(self):
        self.live.begin("browser")
        self.feed(1.0, fps=60)
        before = len(self.stage.live())
        self.feed(1.0, fps=60)
        self.assertLessEqual(len(self.stage.live()) - before, 31)

    def test_while_the_character_talks_its_mouth_is_its_own(self):
        self.live.begin("browser")
        self.feed(1.2)
        self.speaking = True
        self.feed(0.2)
        self.assertNotIn("mouth_open", self.stage.live()[-1])

    def test_one_source_at_a_time_and_it_hands_back(self):
        self.live.begin("app", "MeowFace")
        self.assertFalse(self.live.frame(frame(), "browser"), "frames from another source are not used")
        self.live.begin("browser")
        self.assertIn({"live": None}, self.stage.sent, "a new source hands back the old one's controls first")
        self.live.end("app")
        self.assertTrue(self.live.status()["on"], "ending another kind ends nothing")
        self.live.end("browser")
        self.assertEqual(self.stage.sent[-1], {"live": None})
        self.assertFalse(self.live.status()["on"])

    def test_recording_what_it_copies_makes_a_gesture(self):
        self.live.begin("browser", mirror=False)
        with self.assertRaisesRegex(ValueError, "hold still"):
            self.live.start_recording()
        self.feed(1.2)
        self.live.start_recording()
        self.assertEqual(self.live.status()["recording"], 0.0)
        self.feed(0.4, fps=20)
        for i in range(40):                       # a turn and back, frames arriving unevenly
            self.clock.t += 0.03 + 0.04 * (i % 3)
            self.live.frame(frame(turn=0.14 * math.sin(math.pi * i / 39)), "browser")
        self.feed(0.4, fps=20)
        tracks = self.live.stop_recording()
        self.assertIn("head_yaw", tracks)
        self.assertGreater(max(v for _, v in tracks["head_yaw"]), 0.4)
        entry = motions.clean("copied", {"kind": "gesture", "needs": [], "tracks": tracks})
        self.assertLessEqual(max(k[-1][0] for k in entry["tracks"].values()), 20)
        with self.assertRaisesRegex(ValueError, "nothing"):
            self.live.stop_recording()
        self.live.start_recording()
        self.feed(0.5)
        with self.assertRaisesRegex(ValueError, "too short"):
            self.live.stop_recording()

    def test_a_recording_stops_at_twenty_seconds(self):
        self.live.begin("browser")
        self.feed(1.2)
        self.live.start_recording()
        for i in range(210):
            self.clock.t += 0.1
            self.live.frame(frame(turn=0.14 * math.sin(i / 10.0)), "browser")
        self.assertIsNone(self.live.status()["recording"])
        self.assertTrue(self.live.status()["recorded"], "waiting for whoever asks for it")
        self.assertIn("head_yaw", self.live.stop_recording())
        self.assertFalse(self.live.status()["recorded"], "given once")
        self.live.start_recording()
        self.feed(21, fps=10)
        with self.assertRaisesRegex(ValueError, "nothing moved"):
            self.live.stop_recording()

    def test_resampling_to_a_steady_rate(self):
        frames = [(0.0, {"a": 0}), (0.04, {"a": 1}), (0.21, {"a": 2}), (0.25, {"a": 3})]
        self.assertEqual([f["a"] for f in live_motion.resample(frames, 10)], [0, 1, 2])


VTS_PACKET = {"Timestamp": 1, "Hotkey": -1, "FaceFound": True, "Rotation": {"x": 10.0, "y": 350.0, "z": -5.0},
              "Position": {"x": 0, "y": 0, "z": 0}, "EyeLeft": {"x": 0, "y": 0, "z": 0}, "EyeRight": {"x": 0, "y": 0, "z": 0},
              "BlendShapes": [{"k": "JawOpen", "v": 0.5}, {"k": "MouthSmileLeft", "v": 0.6},
                              {"k": "MouthSmileRight", "v": 0.6}, {"k": "EyeBlinkLeft", "v": 1.2}]}
# The example on iFacialMocap's specification page, as printed there (stray brackets and all).
IFM_EXAMPLE = ("mouthSmile_R-0(eyeLookOut_L-0)mouthUpperUp_L-11(eyeWide_R-0)mouthClose-8|mouthPucker-4|"
               "mouthRollLower-9|eyeBlink_R-7|eyeLookDown_L-1|?cheekSquint_R-11|eyeBlink_L-7|tongueOut-0|jawOpen-12|"
               "browInnerUp-20|mouthSmile_L-0|=head#-21.488958,-6.038993,-6.6019735,-0.030653415,-0.10287084,"
               "-0.6584072|rightEye#6.0297494,2.4403017,0.25649446|leftEye#6.034903,-1.6660284,-0.17520553|")


class AppProtocolTest(unittest.TestCase):
    def test_vtube_studio_packets(self):
        got = phone_apps.parse_vts(json.dumps(VTS_PACKET).encode())
        self.assertEqual((got["pitch"], got["yaw"], got["roll"]), (10.0, -10.0, -5.0), "350 degrees is -10")
        self.assertEqual(got["blend"]["jawOpen"], 0.5)
        self.assertEqual(got["blend"]["eyeBlinkLeft"], 1.0, "scores are 0 to 1")
        self.assertEqual(phone_apps.parse_vts(json.dumps(dict(VTS_PACKET, FaceFound=False)).encode()), {"face": False})
        for bad in (b"not json", b"[1]", json.dumps(dict(VTS_PACKET, Rotation={"x": "up"})).encode(),
                    json.dumps(dict(VTS_PACKET, BlendShapes=[{"k": "JawOpen", "v": float("nan")}])).encode()):
            self.assertIsNone(phone_apps.parse_vts(bad), bad)

    def test_ifacialmocap_packets(self):
        got = phone_apps.parse_ifm(IFM_EXAMPLE.encode())
        self.assertAlmostEqual(got["pitch"], -21.488958)
        self.assertAlmostEqual(got["yaw"], -6.038993)
        self.assertEqual(got["blend"]["eyeBlinkRight"], 0.07, "eyeBlink_R, 0 to 100")
        self.assertEqual(got["blend"]["browInnerUp"], 0.2)
        self.assertIn("cheekSquintRight", got["blend"], "stray characters are dropped")
        v2 = phone_apps.parse_ifm(b"mouthSmile_R&45|jawOpen&10|=head#1,2,3,0,0,0|rightEye#0,0,0|")
        self.assertEqual((v2["blend"]["mouthSmileRight"], v2["yaw"]), (0.45, 2.0))
        self.assertIsNone(phone_apps.parse_ifm(b"jawOpen&10|"), "no head: not a frame")
        self.assertIsNone(phone_apps.parse_ifm("jawOpen&10|=head#1,2,3".encode("utf-16")))

    def test_an_apps_frame_as_controls(self):
        c = phone_apps.app_controls({"face": True, "blend": {"mouthSmileLeft": 0.6, "mouthSmileRight": 0.6, "jawOpen": 0.5},
                                     "pitch": 15.0, "yaw": 30.0, "roll": 10.0})
        self.assertAlmostEqual(c["head_yaw"], 1.0, msg="turned to their own left: towards the picture's right")
        self.assertAlmostEqual(c["head_pitch"], -0.5, msg="looking down")
        self.assertAlmostEqual(c["head_roll"], -0.5, msg="towards their right shoulder")
        self.assertGreater(c["smile"], 0.5)
        self.assertAlmostEqual(c["mouth_open"], 0.6)
        self.assertEqual(phone_apps.app_controls({"face": False}), {})

    def test_only_addresses_on_the_home_network(self):
        self.assertEqual(phone_apps.phone_address(" 192.168.1.40 "), "192.168.1.40")
        self.assertEqual(phone_apps.phone_address("fe80::1%wlan0"), "fe80::1")
        for bad in ("8.8.8.8", "example.com", "", "224.0.0.1", "192.168.1.400"):
            with self.assertRaises(ValueError):
                phone_apps.phone_address(bad)


class FakePhone(asyncio.DatagramProtocol):
    """A phone running a face-tracking app: once asked, it streams frames 30 times a second (VTube Studio's
    for as long as the request said), its head turning slowly from side to side."""

    def __init__(self, app):
        self.app, self.asked, self.transport, self.task, self.until, self.to = app, [], None, None, 0.0, None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.asked.append(data)
        loop = asyncio.get_running_loop()
        if self.app == "vts":
            request = json.loads(data)
            self.to, self.until = (addr[0], request["ports"][0]), loop.time() + request["time"]
        else:
            self.to, self.until = addr, loop.time() + 3600
        if self.task is None:
            self.task = asyncio.ensure_future(self.stream())

    async def stream(self):
        loop = asyncio.get_running_loop()
        start = loop.time()
        while self.transport is not None and not self.transport.is_closing():
            if loop.time() < self.until:
                yaw = 0.0 if loop.time() - start < 1.2 else 25.0 * math.sin(3.0 * (loop.time() - start))
                if self.app == "vts":
                    packet = json.dumps(dict(VTS_PACKET, Rotation={"x": 0, "y": yaw, "z": 0})).encode()
                else:
                    packet = b"jawOpen&30|mouthSmile_L&50|mouthSmile_R&50|=head#0,%.3f,0,0,0,0|" % yaw
                self.transport.sendto(packet, self.to)
            await asyncio.sleep(1 / 30.0)

    def close(self):
        if self.task:
            self.task.cancel()
        self.transport.close()


async def fake_phone(app):
    loop = asyncio.get_running_loop()
    transport, phone = await loop.create_datagram_endpoint(lambda: FakePhone(app), local_addr=("127.0.0.1", 0))
    return phone, transport.get_extra_info("sockname")[1]


class AppLinkTest(unittest.IsolatedAsyncioTestCase):
    async def test_vtube_studio_is_asked_and_its_frames_come_through(self):
        phone, port = await fake_phone("vts")
        frames = []
        link = await phone_apps.AppLink("vts", "127.0.0.1", frames.append, local_port=0, phone_port=port).start()
        try:
            for _ in range(100):
                if len(frames) >= 3:
                    break
                await asyncio.sleep(0.01)
            request = json.loads(phone.asked[0])
            self.assertEqual(request["messageType"], "iOSTrackingDataRequest")
            self.assertEqual(request["ports"], [link.port])
            self.assertTrue(0.5 <= request["time"] <= 10)
            self.assertEqual(frames[-1]["head_yaw"], 0.0, "still, for its first second")
        finally:
            await link.stop()
            phone.close()

    async def test_ifacialmocap_is_asked_and_its_frames_come_through(self):
        phone, port = await fake_phone("ifm")
        frames = []
        link = await phone_apps.AppLink("ifm", "127.0.0.1", frames.append, local_port=0, phone_port=port).start()
        try:
            for _ in range(100):
                if frames:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(phone.asked[0], phone_apps.IFM_HELLO)
            self.assertEqual(frames[0]["head_yaw"], 0.0)
            self.assertGreater(frames[0]["smile"], 0.5)
        finally:
            await link.stop()
            phone.close()

    async def test_other_senders_are_ignored(self):
        frames = []
        link = await phone_apps.AppLink("ifm", "192.168.1.40", frames.append, local_port=0, phone_port=9).start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(b"jawOpen&30|=head#0,15,0,0,0,0|", ("127.0.0.1", link.port))
            sock.close()
            await asyncio.sleep(0.05)
            self.assertEqual(frames, [], "only the phone the owner named")
        finally:
            await link.stop()


class LiveWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, device: None)
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.tls_port = 0                 # any free port
        self.device.web_host = "127.0.0.1"
        self.device.app_port = 0
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.dir.cleanup()

    def live(self):
        return [c["live"] for c in self.stage.commands if "live" in c]

    @unittest.skipUnless(tls.available(), "openssl is not installed")
    async def test_the_live_camera_serves_the_recorder_over_its_own_https_only_when_on(self):
        status = await (await self.client.get("/api/live")).json()
        self.assertFalse(status["camera"] or status["https"]["on"], "off unless the owner turns it on")
        r = await self.client.put("/api/live", json={"camera": True}, headers=H)
        status = await r.json()
        self.assertEqual(r.status, 200, status)
        self.assertTrue(status["https"]["on"])
        self.assertRegex(status["https"]["fingerprint"], "^[0-9A-F:]+$")
        port = status["https"]["port"]
        started = await (await self.client.post("/api/record", json={}, headers=H)).json()
        self.assertTrue(started["links"][0].startswith("https://"), started["links"])
        insecure = ssl.create_default_context()
        insecure.check_hostname, insecure.verify_mode = False, ssl.CERT_NONE   # as a phone after its warning
        async with aiohttp.ClientSession() as phone:
            base = "https://127.0.0.1:%d" % port
            async with phone.get(base + "/r/" + started["code"], ssl=insecure) as page:
                self.assertEqual(page.status, 200)
                self.assertIn("wss://127.0.0.1:%d" % port, page.headers["Content-Security-Policy"])
            for path in ("/", "/api/hello", "/api/settings", "/api/record"):
                async with phone.get(base + path, ssl=insecure) as other:
                    self.assertEqual(other.status, 404, "the settings page stays off it: " + path)
            async with phone.get(base + "/licences", ssl=insecure) as notices:
                self.assertEqual(notices.status, 200)
        r = await self.client.put("/api/live", json={"camera": False}, headers=H)
        self.assertFalse((await r.json())["https"]["on"])
        async with aiohttp.ClientSession() as phone:
            with self.assertRaises(aiohttp.ClientConnectionError):
                async with phone.get("https://127.0.0.1:%d/r" % port, ssl=insecure):
                    pass

    async def test_copy_me_from_the_phones_live_camera(self):
        code = (await (await self.client.post("/api/record", json={}, headers=H)).json())["code"]
        width, height = 720, 1280

        def message(turn):
            f = {"face": face_points(turn=turn), "blend": [0.0] * len(mocap.BLENDSHAPES), "pose": body_points()}
            return {"frame": f, "width": width, "height": height}
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self.client.ws_connect("/r/%s/live" % code, headers={"Origin": "http://elsewhere.example"})
        ws = await self.client.ws_connect("/r/%s/live" % code)
        await ws.send_json({"mirror": False})
        for i in range(45):
            await ws.send_json(message(0.0))
            await asyncio.sleep(0.03)
        for i in range(20):
            await ws.send_json(message(0.14))
            await asyncio.sleep(0.03)
        status = await ws.receive_json(timeout=2)
        self.assertTrue(status["on"])
        self.assertGreater(self.live()[-1]["head_yaw"], 0.3, "the character turns as the person does")
        await ws.send_json({"frame": {"face": [[0.5]]}, "width": width, "height": height})
        await ws.send_json({"frame": "rubbish"})
        await ws.send_json(message(0.14))
        await asyncio.sleep(0.05)
        await ws.close()
        await asyncio.sleep(0.05)
        self.assertIsNone(self.live()[-1], "closing it hands the character back")
        self.assertFalse(self.device.store.settings()["live"]["mirror"], "the choice is kept")

    async def test_copy_me_from_a_phone_app_and_record_a_gesture(self):
        phone, port = await fake_phone("vts")
        self.device.phone_port = port
        try:
            r = await self.client.post("/api/live/app", json={"app": "vts", "address": "8.8.8.8"}, headers=H)
            self.assertEqual(r.status, 400, "only an address on the home network")
            r = await self.client.post("/api/live/app", json={"app": "vts", "address": "127.0.0.1"}, headers=H)
            status = await r.json()
            self.assertEqual(r.status, 200, status)
            self.assertTrue(status["app"]["connected"])
            self.assertEqual(self.device.store.settings()["live"]["app_address"], "127.0.0.1", "kept for next time")
            for _ in range(300):
                if self.live():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.live(), "the app's frames move the character")
            for _ in range(300):                  # once it has the resting pose (the phone's first second)
                if self.live() and not (await (await self.client.get("/api/live")).json())["live"]["calibrating"]:
                    break
                await asyncio.sleep(0.01)
            r = await self.client.post("/api/live/record", json={"action": "start"}, headers=H)
            self.assertEqual(r.status, 200, await r.text())
            await asyncio.sleep(1.6)
            got = await (await self.client.post("/api/live/record", json={"action": "stop"}, headers=H)).json()
            self.assertEqual(motions.clean("from_the_app", got["entry"])["kind"], "gesture")
            self.assertIn({"gesture": motions.TRY}, self.stage.commands, "it plays the recording at once")
            await self.client.delete("/api/live/app", headers=H)
            status = await (await self.client.get("/api/live")).json()
            self.assertFalse(status["app"]["connected"] or status["live"]["on"])
            self.assertIsNone(self.live()[-1])
        finally:
            phone.close()

    async def test_only_the_owner(self):
        self.client.session.cookie_jar.clear()
        for method, path in (("GET", "/api/live"), ("PUT", "/api/live"), ("POST", "/api/live/app"),
                             ("POST", "/api/live/record")):
            r = await self.client.request(method, path, json={}, headers=H)
            self.assertEqual(r.status, 401, path)


if __name__ == "__main__":
    unittest.main()
