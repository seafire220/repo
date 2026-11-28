"""Tests for the console API: login, keys, characters, memory and talking, with stand-in providers."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import package  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.memory import when  # noqa: E402
from companion.web import local_address, make_app  # noqa: E402
from fakes import FakeLLM, FakeStage, FakeTTS  # noqa: E402
from test_package import good_files, manifest  # noqa: E402

H = {"X-Companion": "1"}


def fakes(which, device):
    return {"llm": FakeLLM("[happy] Nice to meet you. I'm Aria."), "tts": FakeTTS()}.get(which)


def conversation_prompt(conversation):
    """The system prompt a conversation would send now."""
    from companion.conversation import system_prompt
    gestures = conversation.gestures if conversation.gestures is not None else conversation.stage.gestures
    gestures = [g for g in gestures if g not in getattr(conversation.stage, "disabled", ())]
    return system_prompt(conversation.persona, "", gestures=gestures)


class WebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=fakes)
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.dir.cleanup()

    async def login(self):
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def test_first_use_sets_a_password_once(self):
        r = await self.client.get("/api/hello")
        self.assertTrue((await r.json())["needs_setup"])
        await self.login()
        r = await self.client.post("/api/setup", json={"password": "another password"}, headers=H)
        self.assertEqual(r.status, 409)

    async def test_the_api_needs_a_login_and_the_header(self):
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 401)
        await self.login()
        r = await self.client.get("/api/status")
        self.assertEqual(r.status, 200)
        r = await self.client.put("/api/settings", json={"volume": 0.5})
        self.assertEqual(r.status, 403, "changes need the X-Companion header")

    async def test_choosing_the_character_drawing_relinks_the_stage(self):
        from test_package import live2d_files
        await self.login()
        files = good_files()
        files.update(live2d_files())
        self.device.characters.install(package.build(files, manifest()))
        self.device.activate("aria")
        self.assertTrue(os.path.realpath(self.device.current_puppet).endswith("model.model3.json"))
        self.stage.commands.clear()
        r = await self.client.put("/api/settings", json={"character_format": "inochi2d"}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertTrue(os.path.realpath(self.device.current_puppet).endswith("puppet.inp"))
        self.assertIn({"quit": True}, self.stage.commands, "the stage restarts with the other drawing")
        r = await self.client.put("/api/settings", json={"character_format": "png"}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.put("/api/settings", json=["not", "an", "object"], headers=H)
        self.assertEqual(r.status, 400)

    async def test_a_zipped_live2d_model_folder_can_be_uploaded(self):
        from test_package import model_folder_zip
        await self.login()
        r = await self.client.post("/api/characters?name=Aria", data=model_folder_zip(), headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["id"], "aria")
        self.assertTrue(os.path.realpath(self.device.current_puppet).endswith("model.model3.json"),
                        "the first character is shown at once, with its Live2D-format model")
        r = await self.client.post("/api/characters", data=b"PK\x05\x06" + b"\0" * 18, headers=H)
        self.assertEqual(r.status, 400)

    async def test_a_live2d_models_own_idle_motions_can_be_turned_down(self):
        from test_package import model_folder_zip
        await self.login()
        r = await self.client.post("/api/characters?name=Aria", data=model_folder_zip(), headers=H)
        cid = (await r.json())["id"]
        with open(self.device.stage_settings) as f:
            settings = json.load(f)
        self.assertEqual(settings.pop("model_motions"), 1.0, "an imported model's motions play as made")
        self.assertEqual(set(settings), {"background"})
        r = await self.client.get("/api/characters/%s/movement" % cid, headers=H)
        own = (await r.json())["own_motions"]
        self.assertEqual(own, {"setting": "full", "idle": ["idle"], "studio": False})
        self.stage.commands.clear()
        r = await self.client.put("/api/characters/%s/own-motions" % cid, json={"setting": "gentle"}, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["own_motions"]["setting"], "gentle")
        self.assertIn({"model_motions": 0.5}, self.stage.commands, "changed on the screen at once")
        with open(self.device.stage_settings) as f:
            self.assertEqual(json.load(f)["model_motions"], 0.5, "and kept for the next start")
        r = await self.client.put("/api/characters/%s/own-motions" % cid, json={"setting": "loud"}, headers=H)
        self.assertEqual(r.status, 400)

    async def test_studio_made_characters_start_without_their_generic_idle_loop(self):
        from test_package import live2d_files
        await self.login()
        files = good_files()
        files.update(live2d_files())
        files["studio/report.json"] = "{}"
        self.device.characters.install(package.build(files, manifest()))
        self.device.activate("aria")
        self.assertEqual(self.device.characters.own_motion_setting("aria"), "off")
        with open(self.device.stage_settings) as f:
            settings = json.load(f)
        self.assertEqual(settings.pop("model_motions"), 0.0)
        self.assertEqual(set(settings), {"background"})
        r = await self.client.get("/api/characters/aria/movement", headers=H)
        self.assertTrue((await r.json())["own_motions"]["studio"])

    async def test_a_vrm_avatar_can_be_uploaded(self):
        from vrm_sample import sample_vrm
        await self.login()
        r = await self.client.post("/api/characters?name=Kit", data=sample_vrm(1), headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["id"], "kit")
        self.assertTrue(os.path.realpath(self.device.current_puppet).endswith("model.vrm"),
                        "the first character is shown at once, with its VRM drawing")
        r = await self.client.get("/api/characters", headers=H)
        listed = (await r.json())["characters"][0]
        self.assertEqual(listed["drawing"], "vrm")
        self.assertIn("Test Maker", listed["licence"])
        r = await self.client.post("/api/characters", data=sample_vrm(1, drop_bone="head"), headers=H)
        self.assertEqual(r.status, 400)
        self.assertIn("head", await r.text())

    async def test_a_3d_avatars_animation_style_and_own_animations(self):
        from vrm_sample import sample_vrm, sample_vrma
        await self.login()
        r = await self.client.post("/api/characters?name=Kit", data=sample_vrm(1), headers=H)
        cid = (await r.json())["id"]
        with open(self.device.stage_settings) as f:
            settings = json.load(f)
        self.assertEqual(settings["vrm_animation"], "full", "full-body animation unless the owner chooses light")
        self.assertEqual(settings["clip_folders"][0], "builtin")
        r = await self.client.get("/api/characters/%s/animations" % cid, headers=H)
        data = await r.json()
        self.assertEqual(data["settings"]["animation"], "full")
        self.assertTrue(data["active"])
        self.assertIn("idle_talk", [x["role"] for x in data["roles"]])
        # A new gesture of its own, and an idle loop.
        self.stage.commands.clear()
        r = await self.client.post("/api/characters/%s/animations/little_dance?title=Dance&words=do+a+little+dance" % cid,
                                   data=sample_vrma(3.0), headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual((await r.json())["kind"], "gesture")
        self.assertIn({"quit": True}, self.stage.commands, "the stage starts again with the new animation")
        r = await self.client.post("/api/characters/%s/animations/idle_happy" % cid, data=sample_vrma(4.0), headers=H)
        self.assertEqual(r.status, 200)
        self.device.use_custom_motions()
        from companion import speech_text
        self.assertEqual(speech_text.CUSTOM_WORDS.get("little_dance"), "do a little dance")
        r = await self.client.post("/api/characters/%s/animations/wave" % cid, data=sample_vrm(1), headers=H)
        self.assertEqual(r.status, 400)
        self.assertIn("VRM Animation", await r.text())
        # Trying one plays its file on the screen.
        self.stage.commands.clear()
        r = await self.client.post("/api/characters/%s/animations/little_dance/try" % cid, headers=H)
        self.assertEqual(r.status, 200)
        tried = [c for c in self.stage.commands if "try_animation" in c]
        self.assertTrue(tried and tried[0]["try_animation"].endswith(os.path.join("animations", "little_dance.vrma")))
        # The light style: no clips play, and the stage is told so.
        r = await self.client.put("/api/characters/%s/animations" % cid, json={"animation": "light", "quality": "lite"}, headers=H)
        self.assertEqual(r.status, 200)
        with open(self.device.stage_settings) as f:
            settings = json.load(f)
        self.assertEqual((settings["vrm_animation"], settings["quality"]), ("light", "lite"))
        r = await self.client.put("/api/characters/%s/animations" % cid, json={"animation": "jumpy"}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.delete("/api/characters/%s/animations/little_dance" % cid, headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.delete("/api/characters/%s/animations/little_dance" % cid, headers=H)
        self.assertEqual(r.status, 404)
        # A 2D character has none of this: its stage settings are only the picture behind it.
        self.device.characters.install(package.build(good_files(), manifest()))
        r = await self.client.get("/api/characters/aria/animations", headers=H)
        self.assertEqual(r.status, 400)
        self.device.activate("aria")
        with open(self.device.stage_settings) as f:
            self.assertEqual(set(json.load(f)), {"background"})

    async def test_turning_off_gestures_by_mood_tells_the_stage(self):
        await self.login()
        r = await self.client.put("/api/settings", json={"conversation": {"random_gestures": False}}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertIn({"random_gestures": False}, self.stage.commands)
        self.assertFalse((await r.json())["conversation"]["random_gestures"])

    async def test_feelings_can_be_matched_to_a_models_own_expressions(self):
        from test_package import model_folder_zip
        await self.login()
        r = await self.client.post("/api/characters?name=Aria", data=model_folder_zip(), headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.get("/api/characters/aria/emotions")
        data = await r.json()
        self.assertEqual(data["expressions"], ["Smile"])
        self.assertEqual(data["map"], {"happy": "Smile"}, "guessed from the expression's name")
        self.assertEqual(self.device.stage.emotion_map, {"happy": "Smile"}, "the active character's map is in use")
        r = await self.client.put("/api/characters/aria/emotions", json={"map": {"happy": "", "embarrassed": "Smile"}},
                                  headers=H)
        self.assertEqual((await r.json())["map"], {"embarrassed": "Smile"})
        self.assertEqual(self.device.stage.emotion_map, {"embarrassed": "Smile"})
        for bad in ({"map": {"bored": "Smile"}}, {"map": {"happy": "Nope"}}, {"map": "Smile"}):
            r = await self.client.put("/api/characters/aria/emotions", json=bad, headers=H)
            self.assertEqual(r.status, 400, bad)

    async def test_a_persona_can_be_written_from_a_few_questions(self):
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        form = {"name": "Mika", "personality": "Cheerful and teasing.", "likes": "astronomy, baking",
                "speaking": "Casual and warm.", "greeting": "Oh! You're here!"}
        r = await self.client.put("/api/characters/aria/persona", json={"form": form}, headers=H)
        self.assertEqual(r.status, 200)
        written = (await r.json())["persona"]
        self.assertIn("You are Mika", written)
        self.assertEqual(self.device.characters.persona("aria"), written)
        r = await self.client.get("/api/characters/aria/persona")
        data = await r.json()
        self.assertEqual(data["form"]["likes"], "astronomy, baking", "the answers are kept for next time")
        for bad in ({"name": ""}, {"name": "Mika", "mood": "x"}, {"name": "Mika", "story": "x" * 5000}, "Mika"):
            r = await self.client.put("/api/characters/aria/persona", json={"form": bad}, headers=H)
            self.assertEqual(r.status, 400, bad)

    async def test_wrong_passwords_are_slowed_and_then_locked_out(self):
        await self.login()
        await self.client.post("/api/logout", headers=H)
        codes = []
        for _ in range(6):
            r = await self.client.post("/api/login", json={"password": "nope"}, headers=H)
            codes.append(r.status)
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)

    async def test_keys_are_write_only(self):
        await self.login()
        r = await self.client.put("/api/keys/openai", json={"value": "sk-secret-value-123456"}, headers=H)
        self.assertEqual(await r.json(), {**{k: False for k in (await r.json())}, "openai": True})
        for path in ("/api/keys", "/api/settings", "/api/status"):
            text = await (await self.client.get(path)).text()
            self.assertNotIn("sk-secret", text, path)
        r = await self.client.put("/api/keys/../../etc", json={"value": "x"}, headers=H)
        self.assertIn(r.status, (400, 404))

    async def test_a_character_is_uploaded_edited_talked_to_and_removed(self):
        await self.login()
        data = package.build(good_files(), manifest())
        r = await self.client.post("/api/characters", data=data, headers=H)
        self.assertEqual((await r.json())["id"], "aria")
        self.assertEqual(os.path.realpath(self.device.current_puppet),
                         os.path.realpath(self.device.characters.file("aria", "visual/puppet.inp")))
        r = await self.client.post("/api/characters", data=b"not a package", headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.put("/api/characters/aria/persona", json={"persona": "# Aria\n\nYou are Aria.\n"}, headers=H)
        self.assertEqual(r.status, 200)

        events = []
        self.device.subscribers.add(q := asyncio.Queue())
        r = await self.client.post("/api/say", json={"text": "hello"}, headers=H)
        self.assertEqual(r.status, 200)
        while True:
            event = await asyncio.wait_for(q.get(), 5)
            events.append(event)
            if event["kind"] == "replied":
                break
        self.assertEqual(events[-1]["text"], "Nice to meet you. I'm Aria.")
        self.assertIn({"express": "happy"}, self.stage.commands)

        memory = await (await self.client.get("/api/memory/aria")).json()
        self.assertEqual(memory["open"]["turns_now"][-1]["user"], "hello", "the conversation going on")
        await self.client.put("/api/memory/aria", json={"notes": "- Says hello a lot\n"}, headers=H)
        self.assertIn("hello a lot", (await (await self.client.get("/api/memory/aria")).json())["notes"])
        exported = await (await self.client.get("/api/characters/aria/export")).read()
        self.assertNotIn(b"hello a lot", exported, "memories never leave in a package")
        r = await self.client.delete("/api/memory/aria", headers=H)
        m = await (await self.client.get("/api/memory/aria")).json()
        self.assertEqual((m["open"], m["sessions"], m["memories"], m["notes"]), (None, [], [], ""))
        r = await self.client.delete("/api/characters/aria", headers=H)
        self.assertEqual((await (await self.client.get("/api/characters")).json())["characters"], [])

    async def test_movement_settings_are_checked_saved_and_given_to_the_stage(self):
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate("aria")
        self.stage.param_info = [{"name": "ParamArmLA", "min": [-10, 0], "max": [10, 0], "default": [0, 0]},
                                 {"name": "ParamArmL", "min": [0, 0], "max": [30, 0], "default": [0, 0]}]
        self.stage.controls = [{"control": "arm_left", "param": "ParamArmLA", "min": -10, "max": 10, "rest": 0}]
        self.stage.gestures = ["nod", "wave"]
        m = await (await self.client.get("/api/characters/aria/movement")).json()
        self.assertTrue(m["active"] and m["ready"])
        self.assertIn({"control": "arm_left_b", "label": "Left elbow"}, m["controls"])
        self.assertEqual(m["current"][0]["param"], "ParamArmLA")
        for body, status in (({"controls": {"arm_left": {"param": "Nope"}}}, 400),
                             ({"controls": {"tail": {"param": "ParamArmL"}}}, 400),
                             ({"controls": {"arm_left": {}}}, 400), ({"controls": []}, 400)):
            r = await self.client.put("/api/characters/aria/movement", json=body, headers=H)
            self.assertEqual(r.status, status, body)
        self.stage.commands.clear()
        r = await self.client.put("/api/characters/aria/movement", headers=H,
                                  json={"controls": {"arm_left": {"param": "ParamArmL", "invert": True}, "smile": None}})
        self.assertEqual(r.status, 200, await r.text())
        with open(self.device.movement_map) as f:
            self.assertEqual(json.load(f), {"arm_left": {"param": "ParamArmL", "invert": True}, "smile": None})
        self.assertIn({"quit": True}, self.stage.commands, "the stage starts again with the settings")
        self.assertNotIn("ParamArmL", json.dumps(self.device.characters.export("aria").hex()),
                         "settings stay on the device")
        r = await self.client.delete("/api/characters/aria/movement", headers=H)
        self.assertEqual(r.status, 200)
        with open(self.device.movement_map) as f:
            self.assertEqual(json.load(f), {})

    async def test_a_character_card_is_read_into_a_persona_to_review(self):
        from test_cards import CARD, png_card
        r = await self.client.post("/api/cards/read", data=png_card(CARD), headers=H)
        self.assertEqual(r.status, 401, "only after logging in")
        await self.login()
        r = await self.client.post("/api/cards/read", data=png_card(CARD), headers=H)
        self.assertEqual(r.status, 200, await r.text())
        card = await r.json()
        self.assertEqual(card["name"], "Mika")
        self.assertIn("## Greeting", card["persona"])
        self.assertEqual(card["greeting"], "Oh! You're here! I just made cinnamon rolls. Want one?")
        r = await self.client.post("/api/cards/read", data=b"not a card", headers=H)
        self.assertEqual(r.status, 400)
        leaky = dict(CARD, data=dict(CARD["data"], description="My key is sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"))
        r = await self.client.post("/api/cards/read", data=png_card(leaky), headers=H)
        self.assertEqual(r.status, 400, "a card carrying a key is not used")

    async def test_temperament_and_gestures_switched_off_are_saved_and_given_to_the_stage(self):
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate("aria")
        m = await (await self.client.get("/api/characters/aria/movement")).json()
        self.assertIsNone(m["temperament"], "nothing chosen yet")
        self.assertEqual(set(m["suggested"]), {"liveliness", "expressiveness"})
        self.stage.commands.clear()
        r = await self.client.put("/api/characters/aria/temperament", headers=H,
                                  json={"liveliness": 0.9, "expressiveness": 1.7, "gestures_off": ["wave", "yawn"]})
        self.assertEqual(r.status, 200, await r.text())
        saved = await r.json()
        self.assertEqual(saved["temperament"], {"liveliness": 0.9, "expressiveness": 1.0})
        self.assertIn({"temperament": {"energy": 1.32, "expressiveness": 1.4}}, self.stage.commands, "at once")
        self.assertIn({"gestures_off": ["wave", "yawn"]}, self.stage.commands)
        for bad in ({"gestures_off": "wave"}, {"gestures_off": ["Wave!"]}, ["no"]):
            r = await self.client.put("/api/characters/aria/temperament", json=bad, headers=H)
            self.assertEqual(r.status, 400, bad)
        # The conversation is not offered a gesture switched off, and the stage gets the settings again when ready.
        self.stage.gestures = ["nod", "wave"]
        conversation = self.device.make_conversation()
        self.assertAlmostEqual(conversation.planner.expressiveness, 1.4)
        self.assertNotIn("[wave]", conversation_prompt(conversation))
        self.stage.commands.clear()
        self.device._stage_event({"event": "ready", "gestures": ["nod", "wave"]})
        self.assertIn({"gestures_off": ["wave", "yawn"]}, self.stage.commands)
        self.assertNotIn("temperament", json.dumps(self.device.characters.export("aria").hex()))

    async def test_the_live_view_shows_what_the_character_is_doing(self):
        await self.login()
        logged = len(self.device.log)
        self.device._stage_event({"event": "heartbeat", "fps": 59.8, "state": "idle", "mood": "happy",
                                  "playing": ["wave"], "channels": {"arm_right": "wave"}, "drives": {"boredom": 0.4},
                                  "budget": {"own": 3, "big": 1, "mood": 2, "idle": 0}, "secret": "x"})
        self.device._stage_event({"event": "gesture", "name": "wave", "cause": "command", "played": "greet"})
        self.device._stage_event({"event": "gesture_skipped", "name": "nod", "cause": "speech",
                                  "why": "enough gestures this minute"})
        a = await (await self.client.get("/api/animation")).json()
        self.assertEqual(a["status"]["channels"], {"arm_right": "wave"})
        self.assertNotIn("secret", a["status"], "only what the page shows")
        self.assertEqual([(g["event"], g["name"]) for g in a["log"]], [("gesture", "wave"), ("gesture_skipped", "nod")])
        self.assertEqual(a["log"][0]["played"], "greet")
        self.assertEqual(len(self.device.log), logged, "frequent updates stay out of the device's log")

    async def test_gestures_are_changed_tried_and_added_on_the_device(self):
        from companion import speech_text
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        lib = await (await self.client.get("/api/motions")).json()
        self.assertTrue(lib["ready"])
        self.assertIn("nod", lib["clips"])
        dance = {"kind": "gesture", "needs": ["head_roll"], "words": "do a little dance",
                 "tracks": {"head_roll": [[0, 0], [0.3, 0.4], [0.6, -0.4], [0.9, 0]]}}
        self.stage.commands.clear()
        r = await self.client.post("/api/motions/try", json=dance, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertEqual(self.stage.commands[-2]["define"]["name"], "_try")
        self.assertEqual(self.stage.commands[-1], {"gesture": "_try"}, "played on the screen")
        self.assertFalse(self.device.custom_motions.all(), "a try is not kept")
        r = await self.client.put("/api/motions/little_dance", json=dance, headers=H)
        self.assertEqual(r.status, 200, await r.text())
        self.assertIn("little_dance", self.device.custom_motions.all())
        self.assertEqual(speech_text.parse_tag("little_dance"), ("action", {"kind": "gesture", "name": "little_dance"}))
        conversation = self.device.make_conversation()
        self.assertIn("[little_dance] do a little dance", conversation_prompt(conversation))
        for name, body in (("_try", dance), ("Dance", dance), ("dance", dict(dance, tracks={"tail": [[0, 0], [1, 0]]}))):
            r = await self.client.put("/api/motions/%s" % name, json=body, headers=H)
            self.assertEqual(r.status, 400, name)
        # The stage gets the device's gestures again whenever it starts.
        self.stage.commands.clear()
        self.device._stage_event({"event": "ready", "gestures": ["nod"]})
        self.assertIn("little_dance", [c["define"]["name"] for c in self.stage.commands if "define" in c])
        r = await self.client.delete("/api/motions/little_dance", headers=H)
        self.assertEqual(r.status, 200)
        self.assertIn({"undefine": "little_dance"}, self.stage.commands)
        self.assertIsNone(speech_text.parse_tag("little_dance"), "gone from the conversation too")

    async def test_a_personality_suggests_the_first_temperament(self):
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        r = await self.client.put("/api/characters/aria/persona", headers=H,
                                  json={"persona": "Shy and quiet, a little awkward; speaks softly and rarely smiles."})
        self.assertEqual(r.status, 200)
        t = self.device.characters.device_settings("aria")["temperament"]
        self.assertLess(t["liveliness"], 0.5)
        self.assertLess(t["expressiveness"], 0.5)
        await self.client.put("/api/characters/aria/temperament", json={"liveliness": 0.8, "expressiveness": 0.8}, headers=H)
        await self.client.put("/api/characters/aria/persona", json={"persona": "Bubbly and dramatic!"}, headers=H)
        self.assertEqual(self.device.characters.device_settings("aria")["temperament"]["liveliness"], 0.8,
                         "the owner's choice is kept")

    async def test_the_movement_preview_moves_only_real_parameters_within_range(self):
        await self.login()
        self.stage.param_info = [{"name": "ParamArmLA", "min": [-10, 0], "max": [10, 0], "default": [0, 0]}]
        self.stage.gestures = ["wave"]
        self.stage.commands.clear()
        r = await self.client.post("/api/stage/preview", json={"set": {"ParamArmLA": 25}, "ease": 0.3}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.stage.commands[-1], {"set": {"ParamArmLA": 10.0}, "ease": 0.3}, "held within its range")
        for body in ({"set": {"Nope": 1}}, {"set": {"ParamArmLA": "high"}}, {"gesture": "backflip"}, {"spin": True}):
            r = await self.client.post("/api/stage/preview", json=body, headers=H)
            self.assertEqual(r.status, 400, body)
        await self.client.post("/api/stage/preview", json={"gesture": "wave"}, headers=H)
        await self.client.post("/api/stage/preview", json={"clear": True}, headers=H)
        self.assertEqual(self.stage.commands[-2:], [{"gesture": "wave"}, {"clear": True, "ease": 0.3}])

    async def test_the_memory_tab_shows_and_changes_everything_remembered(self):
        from companion import memory as memory_module
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        memory = self.device.memory("aria")
        old = time.time() - 40 * 86400
        first = memory.add_turn("I love green tea", "Me too!", now=old)
        second = memory.add_turn("we planned a picnic", "How fun!")
        memory.edit_session(first, title="Tea", summary="We talked about tea.")
        m = await (await self.client.get("/api/memory/aria")).json()
        self.assertEqual([(s["id"], s["when"], s["title"]) for s in m["sessions"]], [(first, when(old), "Tea")])
        self.assertEqual((m["open"]["id"], m["open"]["turns_now"][0]["user"]), (second, "we planned a picnic"))
        self.assertIn("their people", m["categories"])
        r = await self.client.get("/api/memory/aria/sessions/%d" % first)
        self.assertEqual([t["user"] for t in (await r.json())["turns"]], ["I love green tea"], "every word, from the archive")

        # Conversations: change one, start afresh, forget one.
        r = await self.client.put("/api/memory/aria/sessions/%d" % first, json={"summary": "We talked about green tea."}, headers=H)
        self.assertEqual(r.status, 200)
        for path, body, status in ((str(first), {"summary": "  "}, 400), (str(first), ["no"], 400),
                                   ("77", {"summary": "x"}, 404), ("abc", {"summary": "x"}, 404)):
            r = await self.client.put("/api/memory/aria/sessions/" + path, json=body, headers=H)
            self.assertEqual(r.status, status, path)
        r = await self.client.post("/api/memory/aria/new", headers=H)
        self.assertEqual(await r.json(), {"ok": True, "ended": second})
        r = await self.client.delete("/api/memory/aria/sessions/%d" % second)
        self.assertEqual(r.status, 403, "changes need the X-Companion header")
        r = await self.client.delete("/api/memory/aria/sessions/%d" % second, headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.delete("/api/memory/aria/sessions/%d" % second, headers=H)
        self.assertEqual(r.status, 404)
        self.assertNotIn("picnic", json.dumps(memory.history()), "its words are gone too")

        # Memories: add, pin and date, forget.
        r = await self.client.post("/api/memory/aria/items", json={"text": "Their sister Kim lives in Perth",
                                                                    "category": "their people", "importance": 4}, headers=H)
        item = await r.json()
        self.assertEqual((item["category"], item["importance"], item["source"]), ("their people", 4, "owner"))
        for body in ({"text": "Kim again", "category": "gossip"}, {"text": "A picnic", "when": "Saturday"}, {"text": " "},
                     {"text": "Fine then", "importance": "very"}, ["no"]):
            r = await self.client.post("/api/memory/aria/items", json=body, headers=H)
            self.assertEqual(r.status, 400, body)
        r = await self.client.put("/api/memory/aria/items/%d" % item["id"], json={"pinned": True, "when": "2026-12-01"}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertEqual([(x["pinned"], x["when"]) for x in memory.memories()], [(True, "2026-12-01")])
        r = await self.client.put("/api/memory/aria/items/999", json={"pinned": True}, headers=H)
        self.assertEqual(r.status, 404)
        await self.client.put("/api/memory/aria/items/%d" % item["id"], json={"status": "retired"}, headers=H)
        m = await (await self.client.get("/api/memory/aria")).json()
        self.assertEqual(([x["id"] for x in m["memories"]], [x["id"] for x in m["retired"]]), ([], [item["id"]]),
                         "no longer in mind, but the owner sees it")
        r = await self.client.put("/api/memory/aria/items/%d" % item["id"], json={"status": "lost"}, headers=H)
        self.assertEqual(r.status, 400)
        await self.client.put("/api/memory/aria/items/%d" % item["id"], json={"status": "active"}, headers=H)
        self.assertEqual([x["id"] for x in memory.memories()], [item["id"]], "restored")
        r = await self.client.delete("/api/memory/aria/items/%d" % item["id"], headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.delete("/api/memory/aria/items/%d" % item["id"], headers=H)
        self.assertEqual(r.status, 404)

        # The owner's notes and the diary.
        r = await self.client.put("/api/memory/aria", json={"notes": "Call me Sam."}, headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.put("/api/memory/aria", json={"notes": ["no"]}, headers=H)
        self.assertEqual(r.status, 400)
        memory_module._write_jsonl(memory.diary_path, [{"day": "2026-11-18", "t": 0, "text": "A quiet day."}])
        r = await self.client.put("/api/memory/aria/diary/2026-11-18", json={"text": "A lovely quiet day."}, headers=H)
        self.assertEqual(r.status, 200)
        for day, body, status in (("2026-11-18", {"text": " "}, 400), ("2026-11-17", {"text": "Hello"}, 404)):
            r = await self.client.put("/api/memory/aria/diary/" + day, json=body, headers=H)
            self.assertEqual(r.status, status, day)
        m = await (await self.client.get("/api/memory/aria")).json()
        self.assertEqual((m["notes"], m["diary"][0]["text"]), ("Call me Sam.", "A lovely quiet day."))
        exported = await (await self.client.get("/api/characters/aria/export")).read()
        for words in (b"green tea", b"Call me Sam", b"lovely quiet"):
            self.assertNotIn(words, exported, "memory never leaves in a package")
        r = await self.client.delete("/api/memory/aria/diary/2026-11-18", headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.delete("/api/memory/aria/diary/2026-11-18", headers=H)
        self.assertEqual(r.status, 404)

    async def test_a_conversation_is_summed_up_once_it_is_over_however_short(self):
        from unittest import mock
        from companion import memory as memory_module
        self.device.characters.install(package.build(good_files(), manifest()))
        llm = FakeLLM("TITLE: Tea\nSUMMARY: They said hello and we talked about tea.\nMOOD: cheerful\n"
                      "ADD likes and dislikes | 3 | They love tea | -")
        self.device.provider_factory = lambda which, device: {"llm": llm, "tts": FakeTTS()}.get(which)
        memory = self.device.memory("aria")
        memory.add_turn("hello, I love tea", "Tea is lovely!")
        self.assertIsNone(await self.device.keep_memory("aria"))
        self.assertEqual(llm.calls, [], "nothing to do while the conversation goes on")
        with mock.patch.object(memory_module, "QUIET_GAP", 0):
            watch = asyncio.ensure_future(self.device.memory_watch(every=0.01))
            try:
                for _ in range(300):
                    await asyncio.sleep(0.01)
                    if memory.sessions() and memory.sessions()[-1].get("done"):
                        break
            finally:
                watch.cancel()
        self.assertEqual([(s["title"], s["summary"], s["why"]) for s in memory.sessions()],
                         [("Tea", "They said hello and we talked about tea.", "quiet")])
        self.assertEqual([x["text"] for x in memory.memories()], ["They love tea"])
        self.assertEqual((llm.temperature, llm.max_tokens), (0.3, 700), "careful note-taking, with room for a full answer")

    async def test_a_conversation_ends_on_time_even_without_a_working_model(self):
        await self.login()
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.provider_factory = lambda which, device: {"llm": FakeLLM(fail="start")}.get(which)
        memory = self.device.memory("aria")
        memory.add_turn("hello", "hi", now=time.time() - 3600)
        m = await (await self.client.get("/api/memory/aria")).json()
        self.assertIsNone(m["open"], "an hour quiet: over")
        self.assertEqual([(s["why"], s["done"]) for s in m["sessions"]], [("quiet", False)], "waiting to be summed up")
        memory.add_turn("hello again", "hi", now=time.time() - 3600)
        memory.new_conversation()
        memory.add_turn("and again", "hi", now=time.time() - 1800)
        await self.device.keep_memory("aria")
        self.assertIsNone(memory.open_session(), "the background step ends it before asking the model anything")

    async def test_a_failed_memory_update_is_reported_and_retried_later(self):
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.provider_factory = lambda which, device: {"llm": FakeLLM(fail="start")}.get(which)
        memory = self.device.memory("aria")
        memory.add_turn("hello", "hi")
        memory.new_conversation()
        self.device.subscribers.add(q := asyncio.Queue())
        await self.device.keep_memory("aria")
        self.assertIn("memory was not brought up to date", (await asyncio.wait_for(q.get(), 1))["message"])
        self.assertGreater(self.device.memory_retry, time.time() + 600)
        self.assertFalse(memory.sessions()[-1]["done"], "kept for the next try")
        self.assertIsNone(await self.device.keep_memory("aria"), "not again straight after every reply")
        self.assertTrue(q.empty())
        self.device.store.update_settings({"conversation": {"memory": False}})
        await self.device.keep_memory("aria", force=True)
        self.assertTrue(q.empty(), "with memory off, nothing is asked")

    async def test_an_inochi2d_model_is_added_on_its_own(self):
        from test_package import puppet_bytes
        await self.login()
        data = puppet_bytes({"meta": {"name": "Mochi", "artist": "Rin"}, "nodes": {"name": "Root"},
                             "param": [{"name": "Head:: Yaw-Pitch"}]})
        r = await self.client.post("/api/characters", data=data, headers=H)
        self.assertEqual(await r.json(), {"id": "mochi"})
        listed = (await (await self.client.get("/api/characters")).json())["characters"]
        self.assertEqual([c["name"] for c in listed], ["Mochi"])
        r = await self.client.post("/api/characters", data=data[:20], headers=H)
        self.assertEqual(r.status, 400, "a damaged puppet")

    async def test_the_button_toggles_listening(self):
        await self.login()
        r = await self.client.post("/api/button", headers=H)
        self.assertTrue((await r.json())["listening"])
        self.assertEqual(self.stage.states()[-1], "listening")
        self.assertIn({"poke": True}, self.stage.commands, "someone is there")
        r = await self.client.post("/api/button", headers=H)
        self.assertFalse((await r.json())["listening"])

    async def test_the_stage_is_told_the_time_of_day_when_ready(self):
        self.device._stage_event({"event": "ready", "gestures": []})
        clocks = [c["clock"] for c in self.stage.commands if "clock" in c]
        now = time.localtime()
        self.assertEqual(len(clocks), 1)
        self.assertAlmostEqual(clocks[0], now.tm_hour + now.tm_min / 60.0, delta=0.05)

    async def test_timers_are_set_and_rung(self):
        self.device.action({"kind": "timer", "seconds": 0.05, "label": "tea"})
        self.assertIn("tea", self.device.timers)
        with open(os.path.join(self.dir.name, "timers.json")) as f:
            self.assertEqual(json.load(f)[0]["label"], "tea")
        await asyncio.sleep(0.3)
        self.assertNotIn("tea", self.device.timers)
        self.assertTrue(any(e["kind"] == "timer" and e.get("did") == "rang" for e in self.device.log))

    async def test_speech_recognition_is_built_once_and_reused(self):
        built = []
        from companion import providers
        original = providers.build_stt
        providers.build_stt = lambda settings, store, model_dir: built.append(1) or object()
        try:
            device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=FakeStage())
            first, second = device.build("stt"), device.build("stt")
            self.assertIs(first, second)
            device.store.update_settings({"stt": {"provider": "groq"}})
            device.build("stt")
            self.assertEqual(len(built), 2, "rebuilt only when the settings change")
        finally:
            providers.build_stt = original

    async def test_speaking_over_the_character_interrupts_it_only_when_allowed(self):
        import array

        class Mic:
            def __init__(self, frames):
                self.list = frames

            async def frames(self):
                for f in self.list:
                    yield f
                    await asyncio.sleep(0)
                await asyncio.sleep(3600)

        quiet = array.array("h", [0] * 320).tobytes()
        voice = array.array("h", [9000, -9000] * 160).tobytes()
        for allowed in (False, True):
            device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=FakeStage(),
                            microphone=Mic([quiet] * 20 + [voice] * 10))
            device.store.update_settings({"listening": {"voice_interrupt": allowed}})
            device.listening, device.state = True, "speaking"
            calls = []
            device.interrupt = lambda: calls.append(1)
            task = asyncio.ensure_future(device.listen_loop())
            await asyncio.sleep(0.3)
            task.cancel()
            self.assertEqual(bool(calls), allowed)

    async def test_the_character_hears_how_loud_the_person_is_while_listening(self):
        import array

        class Mic:
            async def frames(self):
                quiet = array.array("h", [30, -30] * 160).tobytes()
                voice = array.array("h", [9000, -9000] * 160).tobytes()
                for f in [quiet] * 30 + [voice] * 10 + [quiet] * 10:
                    yield f
                    await asyncio.sleep(0)
                await asyncio.sleep(3600)

        stage = FakeStage()
        device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=stage, microphone=Mic())
        device.listening, device.state = True, "listening"
        task = asyncio.ensure_future(device.listen_loop())
        await asyncio.sleep(0.3)
        task.cancel()
        levels = [c["heard"] for c in stage.commands if "heard" in c]
        self.assertEqual(len(levels), 50, "every 20 ms frame, while listening")
        self.assertLess(max(levels[:30]), 0.05, "the room's noise is quiet")
        self.assertGreater(max(levels[30:40]), 0.8, "speech is loud")

    def test_only_local_addresses_are_served(self):
        for ok in ("192.168.1.20", "10.0.0.5", "127.0.0.1", "fe80::1", "172.16.4.4"):
            self.assertTrue(local_address(ok), ok)
        for bad in ("8.8.8.8", "1.1.1.1", "2606:4700::1111", "", "nonsense"):
            self.assertFalse(local_address(bad), bad)


if __name__ == "__main__":
    unittest.main()
