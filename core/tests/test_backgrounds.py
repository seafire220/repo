"""Backgrounds (backgrounds.py): the built-in pictures, the owner's own, the choices, the stage being told,
the settings page's part, and backups. Every picture here is made up by the tests."""
import io
import json
import os
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import backgrounds, backup, package  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeStage  # noqa: E402
from test_package import good_files, manifest  # noqa: E402

H = {"X-Companion": "1"}


def photo(size=(3000, 2000), orientation=None, gps=True, fmt="JPEG", mode="RGB"):
    """A made-up photo: left half red, right half blue, with a camera's orientation and location if asked."""
    im = Image.new(mode, size, (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30))
    im.paste((30, 30, 200) if mode == "RGB" else (30, 30, 200, 0), (size[0] // 2, 0, size[0], size[1]))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if gps:
        exif[0x8825] = {1: "S", 2: (33.0, 52.0, 0.0), 3: "E", 4: (151.0, 12.0, 0.0)}
    out = io.BytesIO()
    if fmt == "JPEG":
        im.save(out, "JPEG", exif=exif.tobytes())
    else:
        im.save(out, fmt)
    return out.getvalue()


class PicturesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.bg = backgrounds.Backgrounds(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_each_built_in_picture_is_drawn_the_same_every_time(self):
        for name in backgrounds.BUILTIN:
            a = backgrounds.draw_builtin(name, size=(108, 192))
            b = backgrounds.draw_builtin(name, size=(108, 192))
            self.assertEqual((a.size, a.mode), ((108, 192), "RGB"), name)
            self.assertEqual(a.tobytes(), b.tobytes(), name)
        path = self.bg.path("night")
        with Image.open(path) as im:
            self.assertEqual(im.size, backgrounds.SIZE)
        self.assertEqual(self.bg.path("night"), path, "drawn once, then kept")

    def test_the_time_of_day_picks_the_picture(self):
        self.assertEqual([self.bg.resolve("auto", h) for h in (6, 13, 18, 23, 2)],
                         ["dawn", "day", "sunset", "night", "night"])
        self.assertIsNone(self.bg.resolve("none"))
        self.assertIsNone(self.bg.resolve("not_one"))
        self.assertEqual(self.bg.resolve("room"), "room")

    def test_a_photo_is_turned_upright_fitted_and_kept_without_where_it_was_taken(self):
        picture = self.bg.add(photo(orientation=6), title="Our beach!")
        self.assertRegex(picture, r"^my_our_beach_[0-9a-f]{4}$")
        with Image.open(self.bg.path(picture)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertLessEqual(max(im.size), backgrounds.LONGEST)
            self.assertGreater(im.height, im.width, "turned upright (the camera said it was on its side)")
            self.assertEqual(len(im.getexif()), 0, "nothing of the camera's is kept")
        self.assertEqual(self.bg.index()[picture]["title"], "Our beach!")
        self.assertEqual(Image.open(io.BytesIO(self.bg.thumbnail(picture))).size, backgrounds.THUMB)

    def test_see_through_pictures_are_flattened(self):
        picture = self.bg.add(photo(size=(800, 600), fmt="PNG", mode="RGBA", gps=False))
        with Image.open(self.bg.path(picture)) as im:
            self.assertEqual(im.mode, "RGB")
            pixel = im.getpixel((700, 300))
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(pixel, (16, 16, 20))),
                            "the see-through half is the plain colour (JPEG may move it a level): %s" % (pixel,))

    def test_what_is_refused(self):
        with self.assertRaises(backgrounds.BackgroundError):
            self.bg.add(b"not a picture at all")
        with self.assertRaises(backgrounds.BackgroundError):
            self.bg.add(photo(size=(150, 150)))
        with self.assertRaises(backgrounds.BackgroundError):
            self.bg.add(b"x" * (backgrounds.MAX_BYTES + 1))
        for _ in range(backgrounds.MAX_PICTURES):
            self.bg.add(photo(size=(300, 300), gps=False))
        with self.assertRaises(backgrounds.BackgroundError):
            self.bg.add(photo(size=(300, 300), gps=False))

    def test_choices_for_a_character(self):
        settings = {"background": {"choice": "room", "dim": 0.2, "parallax": False}}
        self.assertEqual(self.bg.choice_for(settings, {}), ("room", 0.2, False))
        self.assertEqual(self.bg.choice_for(settings, {"background": {"choice": "night", "dim": None}}), ("night", 0.2, False))
        self.assertEqual(self.bg.choice_for(settings, {"background": {"choice": "default", "dim": 0.5}}), ("room", 0.5, False))
        spec = self.bg.stage_spec(settings, {})
        self.assertEqual((spec["dim"], spec["parallax"]), (0.2, 0.0))
        self.assertTrue(spec["path"].endswith("room.jpg"))
        self.assertIsNone(self.bg.stage_spec({"background": {"choice": "none"}}, {}))
        picture = self.bg.add(photo(size=(400, 400), gps=False))
        mine = {"background": {"choice": picture}}
        self.assertEqual(self.bg.choice_for(settings, mine)[0], picture)
        self.assertTrue(self.bg.remove(picture))
        self.assertEqual(self.bg.choice_for(settings, mine)[0], "auto", "a removed picture falls back")
        self.assertFalse(self.bg.remove(picture))


class DeviceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage)
        self.cid = self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate(self.cid)

    def tearDown(self):
        self.dir.cleanup()

    def sent(self):
        return [c["background"] for c in self.stage.commands if isinstance(c, dict) and "background" in c]

    def test_the_stage_settings_have_the_background(self):
        self.device.store.update_settings({"background": {"choice": "studio"}})
        self.device.write_stage_settings(self.cid)
        with open(self.device.stage_settings) as f:
            spec = json.load(f)["background"]
        self.assertTrue(spec["path"].endswith("studio.jpg"))
        self.device.write_stage_settings()
        with open(self.device.stage_settings) as f:
            self.assertNotIn("background", json.load(f), "a card has none")

    def test_a_change_is_shown_at_once_and_only_once(self):
        self.device.write_stage_settings(self.cid)
        self.device.store.update_settings({"background": {"choice": "paper"}})
        self.assertTrue(self.device.show_background())
        self.assertFalse(self.device.show_background(), "nothing new to say")
        self.assertTrue(self.sent()[-1]["path"].endswith("paper.jpg"))
        self.device.store.update_settings({"background": {"choice": "none"}})
        self.device.show_background()
        self.assertIsNone(self.sent()[-1])


class WebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, device: None)
        self.cid = self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate(self.cid)
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.dir.cleanup()

    async def test_choosing_adding_and_removing(self):
        info = await (await self.client.get("/api/backgrounds")).json()
        self.assertEqual([p["id"] for p in info["pictures"][:3]], ["auto", "none", "studio"])
        self.assertEqual(info["default"]["choice"], "auto")
        thumb = await self.client.get("/api/backgrounds/sunset/thumb")
        self.assertEqual((thumb.status, thumb.content_type), (200, "image/jpeg"))
        r = await self.client.put("/api/backgrounds/default", json={"choice": "blossom", "dim": 0.3}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.device.store.settings()["background"]["choice"], "blossom")
        self.assertTrue(self.stage.commands[-1]["background"]["path"].endswith("blossom.jpg"))
        self.assertEqual(self.stage.commands[-1]["background"]["dim"], 0.3)
        r = await self.client.put("/api/backgrounds/default", json={"choice": "nonsense"}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.put("/api/backgrounds/default", json={"dim": 3}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.post("/api/backgrounds?title=Garden", data=photo(size=(1200, 900)), headers=H)
        self.assertEqual(r.status, 200, await r.text())
        added = (await r.json())["added"]
        r = await self.client.put("/api/characters/%s/background" % self.cid, json={"choice": added}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertTrue(self.stage.commands[-1]["background"]["path"].endswith(added + ".jpg"))
        own = await (await self.client.get("/api/characters/%s/background" % self.cid)).json()
        self.assertEqual(own["choice"], added)
        r = await self.client.delete("/api/backgrounds/" + added, headers=H)
        self.assertEqual(r.status, 200)
        self.assertTrue(self.stage.commands[-1]["background"]["path"].endswith(
            backgrounds.AUTO[backgrounds.part_of_day(__import__("time").localtime().tm_hour)] + ".jpg"),
            "the character goes back to the default")
        r = await self.client.post("/api/backgrounds", data=b"nonsense", headers=H)
        self.assertEqual(r.status, 400)

    async def test_the_owner_must_be_logged_in(self):
        await self.client.post("/api/logout", headers=H)
        r = await self.client.get("/api/backgrounds")
        self.assertEqual(r.status, 401)


class BackupTest(unittest.TestCase):
    def test_pictures_and_choices_come_back(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = Device(a, speaker=Speaker(command=None, realtime=False), stage=FakeStage())
            cid = first.characters.install(package.build(good_files(), manifest()))
            picture = first.backgrounds.add(photo(size=(900, 1600), gps=False), title="Lake")
            first.characters.set_device_settings(cid, {"background": {"choice": picture, "dim": 0.1}})
            first.store.update_settings({"background": {"choice": "room"}})
            path = os.path.join(a, "backup.zip")
            info = backup.make(first, path)
            self.assertEqual(info["backgrounds"], 1)
            second = Device(b, speaker=Speaker(command=None, realtime=False), stage=FakeStage())
            done = backup.restore(second, path)
            self.assertEqual(done["skipped"], [])
            self.assertEqual(second.backgrounds.index()[picture]["title"], "Lake")
            restored = done["characters"][0]["id"]
            own = second.characters.device_settings(restored)["background"]
            self.assertEqual(own["choice"], picture)
            self.assertEqual(second.store.settings()["background"]["choice"], "room")


if __name__ == "__main__":
    unittest.main()
