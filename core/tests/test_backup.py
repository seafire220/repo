"""Backups (everything the owner made, to keep or move) and support reports (nothing private)."""
import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import backup, package, support  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeStage  # noqa: E402
from test_package import good_files, manifest  # noqa: E402
from vrm_sample import sample_vrma  # noqa: E402

H = {"X-Companion": "1"}
KEY = "sk-proj-SECRETSECRETSECRETSECRET1234"


def device_in(folder):
    return Device(folder, speaker=Speaker(command=None, realtime=False), stage=FakeStage())


def furnished(folder):
    """A device someone has used: a character with memory, choices and an animation, keys, settings."""
    d = device_in(folder)
    d.store.set_password("a good password")
    d.store.set_secret("openai", KEY)
    d.characters.install(package.build(good_files(), manifest()))
    d.characters.set_device_settings("aria", {"voice": "17", "temperament": {"liveliness": 0.7}})
    d.animations.add("aria", "little_dance", sample_vrma(), title="A little dance", words="dance")
    m = d.memory("aria")
    m.set_notes("Call me Sam.")
    m.add_turn("I love oolong tea", "Me too!", now=1_800_000_000)
    m.new_conversation()
    m.add_memory("They love oolong tea", "likes and dislikes", 3)
    d.store.update_settings({"volume": 0.4, "speak_first": {"on": True}})
    d.custom_motions.set("deep_nod", {"kind": "gesture", "tracks": {"head_pitch": [[0, 0], [0.3, -0.6], [0.6, 0]]}})
    return d


class BackupTest(unittest.TestCase):
    def test_a_backup_moves_everything_but_keys_password_and_wifi_to_another_device(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = furnished(a)
            path = os.path.join(a, "backup.zip")
            info = backup.make(old, path)
            self.assertEqual([c["id"] for c in info["characters"]], ["aria"])
            with zipfile.ZipFile(path) as z:
                everything = b"".join(z.read(n) for n in z.namelist())
                names = z.namelist()
            self.assertNotIn(KEY.encode(), everything, "no keys")
            self.assertFalse(any("secrets" in n or "password" in n for n in names))
            self.assertIn("characters/aria/animations/little_dance.vrma", names)

            new = device_in(b)
            done = backup.restore(new, path)
            self.assertEqual((done["characters"][0]["id"], done["skipped"]), ("aria", []))
            self.assertEqual(new.characters.device_settings("aria")["voice"], "17")
            self.assertEqual(list(new.animations.index("aria")), ["little_dance"])
            m = new.memory("aria")
            self.assertEqual(m.notes(), "Call me Sam.")
            self.assertEqual([x["text"] for x in m.memories()], ["They love oolong tea"])
            self.assertEqual(m.history()[0]["user"], "I love oolong tea")
            self.assertEqual(new.store.settings()["volume"], 0.4)
            self.assertTrue(new.store.settings()["speak_first"]["on"])
            self.assertIn("deep_nod", new.custom_motions.all())
            self.assertEqual(new.store.secret_status().get("openai"), False, "keys are put in again")
            self.assertFalse(new.store.has_password())

    def test_a_tampered_backup_is_refused_or_cannot_do_more_than_the_settings_page(self):
        with tempfile.TemporaryDirectory() as a:
            d = furnished(a)
            path = os.path.join(a, "backup.zip")
            backup.make(d, path)
            with zipfile.ZipFile(path) as z:
                parts = {n: z.read(n) for n in z.namelist()}
            for extra in ("../../etc/passwd", "config/secrets.json", "memory/aria/../../x", "characters/aria/run.sh"):
                evil = os.path.join(a, "evil.zip")
                with zipfile.ZipFile(evil, "w") as z:
                    for n, data in list(parts.items()) + [(extra, b"x")]:
                        z.writestr(n, data)
                with self.assertRaisesRegex(backup.BackupError, "refused"):
                    backup.restore(d, evil)
            with self.assertRaisesRegex(backup.BackupError, "not a backup"):
                backup.restore(d, os.path.join(a, "missing.zip"))
            broken = os.path.join(a, "broken.zip")
            with zipfile.ZipFile(broken, "w") as z:
                for n, data in parts.items():
                    z.writestr(n, b"not a package" if n.endswith(".char") else data)
            done = backup.restore(d, broken)
            self.assertEqual(done["characters"], [])
            self.assertTrue(done["skipped"] and "aria" in done["skipped"][0], "checked as an upload is")


class SupportTest(unittest.TestCase):
    def test_the_report_tells_what_helps_and_nothing_private(self):
        with tempfile.TemporaryDirectory() as a:
            d = furnished(a)
            d.store.update_settings({"owner": {"name": "Wilhelmina", "birthday": "1990-03-17"},
                                     "weather": {"place": {"name": "Tumbleton", "label": "Tumbleton, Somewhere",
                                                           "latitude": 12.34, "longitude": 56.78}},
                                     "special_days": {"dates": [{"date": "06-03", "what": "our wedding day"}]},
                                     "alarms": {"wake_ups": [{"time": "06:45", "label": "gym with Jo"}]}})
            d.emit("heard", {"text": "my secret diary entry"})
            d.emit("timer", {"label": "gym with Jo", "did": "rang", "type": "wake"})
            d.emit("error", {"message": "the language model refused the key %s" % KEY})
            data = support.report(d)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                report = json.loads(z.read("report.json"))
                everything = b"".join(z.read(n) for n in z.namelist())
            self.assertEqual(report["characters"][0]["name"], "Aria")
            self.assertEqual(report["characters"][0]["memories"], 1)
            self.assertTrue(report["keys_set"]["openai"])
            self.assertIn("storage_gb", report["system"])
            self.assertNotIn(KEY.encode(), everything)
            self.assertNotIn(b"my secret diary entry", everything)
            self.assertNotIn(b"oolong", everything, "no memories, only how many")
            self.assertNotIn(b"Call me Sam", everything)
            for private in (b"Wilhelmina", b"1990-03-17", b"Tumbleton", b"56.78", b"our wedding day", b"gym with Jo"):
                self.assertNotIn(private, everything, "the owner's name, birthday, town and own days are left out")
            self.assertEqual(report["settings"]["owner"], {"name": True, "birthday": True})
            self.assertEqual(set(report["hardware"]), {"sound_cards", "inputs", "network", "wifi_driver", "screen",
                                                       "frame_rate", "tv_box"})
            self.assertEqual(report["settings"]["alarms"]["wake_ups"][0]["time"], "06:45")


class PageTest(unittest.IsolatedAsyncioTestCase):
    async def test_backup_restore_and_report_on_the_settings_page(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            old = furnished(a)
            client = TestClient(TestServer(make_app(old)))
            await client.start_server()
            try:
                r = await client.get("/api/backup")
                self.assertEqual(r.status, 401)
                await client.post("/api/login", json={"password": "a good password"}, headers=H)
                r = await client.get("/api/backup")
                self.assertIn("companion-backup", r.headers["Content-Disposition"])
                data = await r.read()
                self.assertFalse(os.path.exists(os.path.join(a, ".backup-out.zip")), "nothing left behind")
                r = await client.get("/api/support-report")
                self.assertEqual(r.status, 200)
                self.assertNotIn(KEY.encode(), await r.read())
            finally:
                await client.close()
            new = device_in(b)
            client = TestClient(TestServer(make_app(new)))
            await client.start_server()
            try:
                await client.post("/api/setup", json={"password": "a good password"}, headers=H)
                r = await client.put("/api/backup", data=data, headers=H)
                done = await r.json()
                self.assertEqual([c["id"] for c in done["characters"]], ["aria"])
                self.assertEqual(new.active_id(), "aria")
                r = await client.put("/api/backup", data=b"junk", headers=H)
                self.assertEqual(r.status, 400)
            finally:
                await client.close()


if __name__ == "__main__":
    unittest.main()
