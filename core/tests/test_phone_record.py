"""Gestures recorded with a phone: the conversion shared with Character Studio, the one-time link, the landmarks
a phone sends, the routes, and fetching the tracking the phone runs."""
import asyncio
import hashlib
import io
import json
import math
import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import mocap, mocap_assets, motions, phone_record  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.web import RECORD_CSP, make_app  # noqa: E402
from fakes import FakeStage  # noqa: E402

H = {"X-Companion": "1"}


def face_points(turn=0.0, tilt=0.0):
    """A face looking at the camera (fractions of the picture), its nose moved `turn` towards the picture's right
    and its eyes' line sloped by `tilt`."""
    return [[0.42, 0.40 - tilt], [0.58, 0.40 + tilt], [0.5 + turn, 0.48], [0.36, 0.47], [0.64, 0.47], [0.5, 0.62]]


def body_points(arm_up=0.0, seen=0.9):
    """Head and shoulders, the picture's right arm raised by `arm_up` (0 hanging, 1 straight out)."""
    angle = arm_up * math.pi / 2
    shoulder = (0.65, 0.75)
    elbow = (shoulder[0] + 0.12 * math.sin(angle), shoulder[1] + 0.12 * math.cos(angle))
    wrist = (elbow[0] + 0.12 * math.sin(angle), elbow[1] + 0.12 * math.cos(angle))
    points = {"nose": (0.5, 0.48), "shoulder_l": (0.35, 0.75), "elbow_l": (0.33, 0.87), "wrist_l": (0.33, 0.99),
              "shoulder_r": shoulder, "elbow_r": elbow, "wrist_r": wrist, "hip_l": (0.4, 1.2), "hip_r": (0.6, 1.2)}
    return [[x, y, 0.0, 0.1 if name.startswith("hip") else seen] for name, (x, y) in
            ((n, points[n]) for n in mocap.POSE)]


def payload(seconds=3.0, fps=12, turn=0.12, arm=0.0, found=1.0, width=720, height=1280):
    """What a phone sends for a person who turns their head (and raises an arm) and back."""
    count = int(seconds * fps) + 1
    frames = []
    for i in range(count):
        t = i / fps
        s = math.sin(math.pi * min(1.0, max(0.0, (t - 0.5) / (seconds - 1.0)))) if seconds > 1 else 0.0
        if i >= found * count:
            frames.append({"face": None, "blend": None, "pose": None})
            continue
        blend = [0.0] * len(mocap.BLENDSHAPES)
        blend[mocap.BLENDSHAPES.index("mouthSmileLeft")] = blend[mocap.BLENDSHAPES.index("mouthSmileRight")] = 0.6 * s
        frames.append({"face": face_points(turn * s), "blend": blend, "pose": body_points(arm * s)})
    return {"fps": fps, "width": width, "height": height, "frames": frames}


class ConversionTest(unittest.TestCase):
    def test_a_frame_reads_head_face_and_body(self):
        pose = {name: tuple(p) for name, p in zip(mocap.POSE, body_points(arm_up=1.0))}
        face = {i: (x * 720, y * 1280) for i, (x, y) in zip(mocap.FACE_POINTS, face_points(turn=0.1))}
        controls = mocap.frame_controls(face, {"jawOpen": 0.5}, pose)
        self.assertGreater(controls["head_yaw"], 0.5, "the nose towards the picture's right: turned right")
        self.assertAlmostEqual(controls["mouth_open"], 0.6)
        self.assertGreater(controls["arm_right"], 0.8, "an arm straight out")

    def test_arms_the_body_model_cannot_see_are_left_out(self):
        pose = {name: tuple(p) for name, p in zip(mocap.POSE, body_points(arm_up=1.0, seen=0.2))}
        controls = mocap.frame_controls(pose=pose)
        self.assertIn("shoulders", controls, "the shoulders count whenever they are found")
        self.assertNotIn("arm_right", controls)

    def test_mirroring_swaps_sides(self):
        tracks = {"head_yaw": [[0, 0], [1, 0.5], [2, 0]], "arm_left": [[0, 0], [1, 0.8], [2, 0]],
                  "smile": [[0, 0], [1, 0.4], [2, 0]]}
        mirrored = mocap.mirror_tracks(tracks)
        self.assertEqual(mirrored["head_yaw"][1], [1, -0.5])
        self.assertEqual(mirrored["arm_right"], tracks["arm_left"])
        self.assertNotIn("arm_left", mirrored)
        self.assertEqual(mirrored["smile"], tracks["smile"])
        self.assertEqual(mocap.mirror_tracks(mirrored), tracks, "twice is as before")


class LinkTest(unittest.TestCase):
    def test_codes_are_short_and_easy_to_type(self):
        recorder = phone_record.Recorder()
        codes = {recorder.start()["code"] for _ in range(200)}
        self.assertGreater(len(codes), 190)
        for code in codes:
            self.assertEqual(len(code), phone_record.CODE_LENGTH)
            self.assertFalse(set(code) & set("01OIL"), "no letters that look like others")

    def test_a_new_code_ends_the_old_one(self):
        recorder = phone_record.Recorder()
        old = recorder.start()["code"]
        new = recorder.start()["code"]
        self.assertIs(recorder.find(new.lower()), recorder.session, "typed in small letters works too")
        if old != new:
            with self.assertRaises(phone_record.LinkError):
                recorder.find(old)

    def test_a_code_runs_out(self):
        now = [1000.0]
        recorder = phone_record.Recorder(lifetime=60, clock=lambda: now[0])
        code = recorder.start()["code"]
        self.assertTrue(recorder.find(code[:3] + "-" + code[3:]), "a dash or space in it is ignored")
        now[0] += 61
        with self.assertRaises(phone_record.LinkError):
            recorder.find(code)
        self.assertIsNone(recorder.current())

    def test_guessing_codes_is_stopped(self):
        now = [1000.0]
        recorder = phone_record.Recorder(clock=lambda: now[0])
        code = recorder.start()["code"]
        for _ in range(phone_record.WRONG_CODES_A_MINUTE):
            with self.assertRaises(phone_record.LinkError) as caught:
                recorder.find("WRONG1")
            self.assertFalse(caught.exception.locked)
        with self.assertRaises(phone_record.LinkError) as caught:
            recorder.find(code)
        self.assertTrue(caught.exception.locked, "even the right code waits once too many were wrong")
        now[0] += 61
        self.assertTrue(recorder.find(code), "a minute later it opens again")


class PayloadTest(unittest.TestCase):
    def test_a_turn_and_a_smile_become_a_clip(self):
        tracks, moves = phone_record.make_clip(payload())
        self.assertIn("head_yaw", tracks)
        self.assertGreater(max(v for _, v in tracks["head_yaw"]), 0.4)
        self.assertIn("smile", tracks)
        self.assertIn("turns its head", moves)
        entry = phone_record.entry(tracks, kind="gesture", words="look aside")
        self.assertEqual(motions.clean("look_aside", entry)["words"], "look aside")

    def test_a_raised_arm_is_read(self):
        tracks, moves = phone_record.make_clip(payload(arm=1.0, turn=0.0))
        self.assertGreater(max(v for _, v in tracks["arm_right"]), 0.6)
        self.assertIn("moves an arm", moves)

    def test_a_video_with_hardly_anyone_in_it_is_refused(self):
        with self.assertRaisesRegex(ValueError, "found in only"):
            phone_record.make_clip(payload(found=0.3))

    def test_what_a_phone_sends_is_checked(self):
        good = payload()
        bad = []
        for change in (lambda p: p.update(fps=0), lambda p: p.update(fps="12"), lambda p: p.update(width=-1),
                       lambda p: p.update(frames=p["frames"][:1]), lambda p: p["frames"][3].update(face=[[0.5, 0.5]]),
                       lambda p: p["frames"][3].update(blend=[0.5]), lambda p: p["frames"][3]["face"].__setitem__(0, [0.4]),
                       lambda p: p["frames"][3]["face"].__setitem__(0, [float("nan"), 0.4]),
                       lambda p: p["frames"][3]["face"].__setitem__(0, [True, 0.4]),
                       lambda p: p["frames"][3]["blend"].__setitem__(0, 7),
                       lambda p: p["frames"][3]["pose"].__setitem__(0, [0.5, 0.5, 0.0]),
                       lambda p: p["frames"].__setitem__(3, "frame"),
                       lambda p: p.update(frames=p["frames"] * 20)):
            p = json.loads(json.dumps(good))
            change(p)
            try:
                phone_record.frames_from_payload(p)
            except ValueError:
                continue
            bad.append(p)
        self.assertEqual(bad, [], "every change is refused")
        with self.assertRaisesRegex(ValueError, "seconds at most"):
            phone_record.frames_from_payload(payload(seconds=25))

    def test_the_words_name_the_main_movements_only(self):
        track = lambda peak: [[0, 0], [1, peak], [2, 0]]  # noqa: E731
        tracks = {"body_roll": track(1.2), "head_roll": track(-0.8), "eye_smile": track(0.6), "mouth_open": track(0.17),
                  "arm_left_b": track(0.15), "arm_right_b": track(0.15), "gaze_x": track(0.08)}
        self.assertEqual(phone_record.describe(tracks), ["leans", "tilts its head", "smiles with its eyes"])
        self.assertEqual(phone_record.describe({"gaze_x": track(0.1)}), ["looks around"], "a small one is still named")
        self.assertEqual(len(phone_record.describe({k: track(1.0) for k in phone_record.MOVES})), 4)

    def test_busy_tracks_are_thinned_to_what_the_stage_takes(self):
        keys = [[round(i * 0.05, 3), round(0.5 * math.sin(i * 1.3), 3)] for i in range(400)]
        keys[0][1] = keys[-1][1] = 0.0
        thin = phone_record.fit_keys({"head_yaw": keys}, limit=50)["head_yaw"]
        self.assertLessEqual(len(thin), 50)
        self.assertEqual((thin[0], thin[-1]), (keys[0], keys[-1]))

    def test_names(self):
        self.assertEqual(phone_record.gesture_name("Little dance!"), "little_dance")
        self.assertEqual(phone_record.gesture_name("  my 2nd   WAVE "), "my_nd_wave")
        self.assertEqual(phone_record.gesture_name("???"), "my_gesture")
        self.assertEqual(phone_record.gesture_name("_try"), "try")
        self.assertTrue(motions.NAME.fullmatch(phone_record.gesture_name("x" * 90)))


def fake_downloads():
    """Small stand-ins for MediaPipe's files, and the fetcher's table pointed at them."""
    contents = {name: ("stand-in for %s" % name).encode() for name in mocap_assets.FILES}
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode="w:gz") as tar:
        for name, (url, member, _) in mocap_assets.FILES.items():
            if member:
                info = tarfile.TarInfo(member)
                info.size = len(contents[name])
                tar.addfile(info, io.BytesIO(contents[name]))
    package = package.getvalue()
    files = {name: (url, member, hashlib.sha256(contents[name]).hexdigest())
             for name, (url, member, _) in mocap_assets.FILES.items()}
    served = {url: package if member else contents[name] for name, (url, member, _) in mocap_assets.FILES.items()}

    class Opened(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    asked = []

    def opener(url, timeout=None):
        asked.append(url)
        return Opened(served[url])
    patches = [mock.patch.object(mocap_assets, "FILES", files),
               mock.patch.object(mocap_assets, "TASKS_VISION_SHA256", hashlib.sha256(package).hexdigest())]
    return patches, opener, asked, contents


class AssetsTest(unittest.TestCase):
    def test_the_pinned_files_are_the_ones_the_phone_asks_for(self):
        self.assertEqual(set(mocap_assets.FILES), {"vision_bundle.mjs", "vision_wasm_internal.js",
                                                   "vision_wasm_internal.wasm", "face_landmarker.task",
                                                   "pose_landmarker_lite.task"})
        for url, member, digest in mocap_assets.FILES.values():
            self.assertTrue(url.startswith("https://"))
            self.assertRegex(digest, "^[0-9a-f]{64}$")
        with open(os.path.join(os.path.dirname(__file__), "..", "companion", "console", "record.js")) as f:
            page = f.read()
        for name in mocap_assets.FILES:
            if not name.startswith("vision_wasm"):        # those two MediaPipe asks for by itself
                self.assertIn("/r/assets/" + name, page)

    def test_fetching_checks_every_file_and_puts_all_or_nothing_in_place(self):
        patches, opener, asked, contents = fake_downloads()
        with tempfile.TemporaryDirectory() as d, patches[0], patches[1]:
            folder = os.path.join(d, "models", "mocap")       # no models folder yet, as on a card image
            self.assertEqual(sorted(mocap_assets.fetch(folder, opener)), sorted(mocap_assets.FILES))
            self.assertEqual(asked.count(mocap_assets.TASKS_VISION), 1, "the package is downloaded once")
            for name, data in contents.items():
                with open(os.path.join(folder, name), "rb") as f:
                    self.assertEqual(f.read(), data)
            with open(os.path.join(folder, "LICENSE")) as f:
                self.assertIn("Apache License", f.read())
            self.assertEqual(mocap_assets.fetch(folder, opener), [], "nothing more to do")
            self.assertEqual(os.listdir(os.path.dirname(folder)), ["mocap"], "nothing left behind")

            other = os.path.join(d, "models", "other")
            name = "face_landmarker.task"
            url, member, digest = mocap_assets.FILES[name]
            with mock.patch.dict(mocap_assets.FILES, {name: (url, member, "0" * 64)}):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    mocap_assets.fetch(other, opener)
            self.assertFalse(os.path.exists(other), "a wrong file: none of them is kept")
            self.assertEqual(os.listdir(os.path.dirname(folder)), ["mocap"])


class RecordWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.stage.controls = [{"control": c} for c in ("head_yaw", "head_pitch", "head_roll", "smile", "shoulders")]
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, device: None)
        # The tracking, as the installer leaves it (stand-ins: the device only serves them).
        folder = os.path.join(self.dir.name, "models", "mocap")
        os.makedirs(folder)
        for name in mocap_assets.FILES:
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"stand-in " + name.encode())
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.dir.cleanup()

    async def login(self):
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def start(self):
        r = await self.client.post("/api/record", json={}, headers=H)
        self.assertEqual(r.status, 200)
        return await r.json()

    async def test_only_the_owner_asks_for_a_link(self):
        r = await self.client.post("/api/record", json={}, headers=H)
        self.assertEqual(r.status, 401)
        await self.login()
        started = await self.start()
        self.assertEqual(started["state"], "waiting")
        self.assertEqual(started["assets"], "ready")
        self.assertTrue(started["links"][0].endswith("/r/" + started["code"]))
        self.assertTrue(started["short"].endswith("/r"))
        self.assertTrue(started["qr"].startswith("data:image/png;base64,"))
        again = await (await self.client.get("/api/record")).json()
        self.assertEqual(again["code"], started["code"], "the page can show the same link again")

    async def test_a_phone_records_plays_and_keeps_a_gesture(self):
        await self.login()
        code = (await self.start())["code"]
        self.client.session.cookie_jar.clear()           # the phone has no login, only the link

        page = await self.client.get("/r/" + code)
        self.assertEqual(page.status, 200)
        self.assertEqual(page.headers["Content-Security-Policy"], RECORD_CSP)
        self.assertIn("record.js", await page.text())
        self.assertNotIn("wasm-unsafe-eval", (await self.client.get("/")).headers["Content-Security-Policy"],
                         "only the recorder may run WebAssembly")

        info = await (await self.client.get("/r/%s/info" % code.lower())).json()
        self.assertEqual(info["assets"], "ready")
        self.assertEqual(info["layout"]["face_points"], list(mocap.FACE_POINTS))
        self.assertEqual(len(info["layout"]["pose_points"]), len(mocap.POSE))

        r = await self.client.post("/r/%s/motion" % code, json=payload())
        self.assertEqual(r.status, 403, "a phone's requests carry the header too")
        r = await self.client.post("/r/%s/motion" % code, json=payload(), headers=H)
        answer = await r.json()
        self.assertEqual(r.status, 200, answer)
        self.assertTrue(answer["played"])
        self.assertTrue(answer["can"])
        self.assertIn("turns its head", answer["moves"])
        tried = [c["define"] for c in self.stage.commands if "define" in c]
        self.assertEqual(tried[-1]["name"], motions.TRY)
        self.assertIn({"gesture": motions.TRY}, self.stage.commands)
        yaw = max(v for _, v in tried[-1]["tracks"]["head_yaw"])

        r = await self.client.post("/r/%s/play" % code, json={"mirror": True}, headers=H)
        self.assertEqual(r.status, 200)
        mirrored = [c["define"] for c in self.stage.commands if "define" in c][-1]
        self.assertAlmostEqual(min(v for _, v in mirrored["tracks"]["head_yaw"]), -yaw)

        r = await self.client.post("/r/%s/save" % code, json={"name": "Look aside!", "kind": "gesture",
                                                               "words": "look [aside]", "mirror": True}, headers=H)
        answer = await r.json()
        self.assertEqual(r.status, 200, answer)
        self.assertEqual(answer["name"], "look_aside")
        kept = self.device.custom_motions.all()["look_aside"]
        self.assertEqual(kept["words"], "look aside")
        self.assertAlmostEqual(min(v for _, v in kept["tracks"]["head_yaw"]), -yaw, msg="kept as it was played")
        self.assertEqual([c["define"]["name"] for c in self.stage.commands if "define" in c][-1], "look_aside")
        self.assertIn("look_aside", self.stage.gestures)

        r = await self.client.post("/r/%s/save" % code, json={"name": "look aside"}, headers=H)
        self.assertEqual((r.status, (await r.json())["exists"]), (409, "added"))
        r = await self.client.post("/r/%s/save" % code, json={"name": "wave"}, headers=H)
        self.assertEqual((r.status, (await r.json())["exists"]), (409, "built in"))
        r = await self.client.post("/r/%s/save" % code, json={"name": "wave", "kind": "idle", "replace": True},
                                   headers=H)
        self.assertEqual((await r.json())["replaced"], "built in")
        self.assertEqual(self.device.custom_motions.all()["wave"]["kind"], "idle")
        r = await self.client.post("/r/%s/save" % code, json={"name": "hop", "kind": "reaction"}, headers=H)
        self.assertEqual(r.status, 400)

        await self.login_again()
        status = await (await self.client.get("/api/record")).json()
        self.assertEqual((status["state"], status["saved"]), ("saved", ["look_aside", "wave"]))
        await self.client.delete("/api/record", headers=H)
        self.client.session.cookie_jar.clear()
        r = await self.client.get("/r/%s/info" % code)
        self.assertEqual(r.status, 404, "a stopped link opens nothing")

    async def login_again(self):
        r = await self.client.post("/api/login", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def test_what_cannot_be_used_is_said(self):
        await self.login()
        code = (await self.start())["code"]
        r = await self.client.post("/r/%s/motion" % code, json=payload(found=0.2), headers=H)
        self.assertEqual(r.status, 400)
        self.assertIn("found in only", await r.text())
        r = await self.client.post("/r/%s/save" % code, json={"name": "x"}, headers=H)
        self.assertEqual(r.status, 409, "nothing to keep yet")
        r = await self.client.post("/r/%s/motion" % code, data=b"x" * (phone_record.MAX_BODY + 1),
                                   headers=dict(H, **{"Content-Type": "application/json"}))
        self.assertEqual(r.status, 413)
        r = await self.client.post("/r/%s/motion" % code, json=payload(arm=1.0, turn=0.0), headers=H)
        self.assertFalse((await r.json())["can"], "this character has no arms to raise")

    async def test_wrong_codes(self):
        r = await self.client.get("/r/ABCDEF/info")
        self.assertEqual(r.status, 404)
        self.assertIn("ask for a new one", await r.text())
        for _ in range(phone_record.WRONG_CODES_A_MINUTE):
            await self.client.get("/r/ABCDEF/info")
        r = await self.client.get("/r/ABCDEF/info")
        self.assertEqual(r.status, 429)
        page = await self.client.get("/r")
        self.assertEqual(page.status, 200, "the page that asks for the code is always there")

    async def test_the_tracking_files_are_served_and_nothing_else(self):
        r = await self.client.get("/r/assets/vision_wasm_internal.wasm")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Content-Type"], "application/wasm")
        self.assertEqual(r.headers["Cache-Control"], "no-cache")
        self.assertEqual(await r.read(), b"stand-in vision_wasm_internal.wasm")
        r = await self.client.get("/r/assets/vision_bundle.mjs")
        self.assertTrue(r.headers["Content-Type"].startswith("text/javascript"))
        for name in ("LICENSE", "..%2Fconfig%2Fsecrets.json", "other.task"):
            r = await self.client.get("/r/assets/" + name)
            self.assertEqual(r.status, 404, name)

    async def test_a_device_without_the_tracking_fetches_it_when_asked(self):
        folder = os.path.join(self.dir.name, "models", "mocap")
        for name in os.listdir(folder):
            os.remove(os.path.join(folder, name))
        os.rmdir(folder)
        r = await self.client.get("/r/assets/vision_bundle.mjs")
        self.assertEqual(r.status, 503)
        patches, opener, asked, contents = fake_downloads()
        real_fetch = mocap_assets.fetch
        with patches[0], patches[1], mock.patch.object(mocap_assets, "fetch",
                                                       lambda folder: real_fetch(folder, opener)):
            await self.login()
            started = await self.start()
            self.assertIn(started["assets"], ("fetching", "ready"))
            for _ in range(200):
                status = await (await self.client.get("/api/record")).json()
                if status["assets"] != "fetching":
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(status["assets"], "ready")
            r = await self.client.get("/r/assets/face_landmarker.task")
            self.assertEqual(await r.read(), contents["face_landmarker.task"])


if __name__ == "__main__":
    unittest.main()
