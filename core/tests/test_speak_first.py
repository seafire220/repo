"""Speaking first: when someone seems to be back after a while, the character greets them, from what it
remembers; only when the owner turns it on, outside quiet hours, never over a conversation."""
import array
import asyncio
import datetime
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from companion import device as device_module, package  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.conversation import Conversation  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.memory import Memory  # noqa: E402
from fakes import FakeLLM, FakeStage, FakeTTS  # noqa: E402
from test_package import good_files, manifest  # noqa: E402

# No quiet hours, for tests; the morning hello (whatever the hour the tests run at) is tested in test_your_day.py.
ALWAYS = {"on": True, "after_hours": 3, "quiet_from": 0, "quiet_to": 0, "morning": False}
QUIET = array.array("h", [30, -30] * 160).tobytes()
VOICE = array.array("h", [9000, -9000] * 160).tobytes()


class Mic:
    def __init__(self, frames):
        self.list = frames

    async def frames(self):
        for f in self.list:
            yield f
            await asyncio.sleep(0)
        await asyncio.sleep(3600)


class ReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_speaking_first_is_kept_as_a_turn_where_only_the_character_spoke(self):
        with tempfile.TemporaryDirectory() as d:
            memory = Memory(d)
            llm, events = FakeLLM("Welcome back! How did the driving test go?"), []
            conv = Conversation(llm=llm, tts=FakeTTS(), speaker=Speaker(command=None, realtime=False), stage=FakeStage(),
                                memory=memory, persona="You are Aria.", on_event=lambda k, data: events.append(k))
            reply = await conv.respond("", prompt="(Greet them: they are back.)")
            self.assertIsNone(reply.error)
            self.assertEqual(llm.calls[0][1][-1], {"role": "user", "content": "(Greet them: they are back.)"})
            self.assertEqual([(t["user"], t["reply"]) for t in memory.history()],
                             [("", "Welcome back! How did the driving test go?")])
            self.assertIn("speaking first", events)
            self.assertNotIn("heard", events)
            _, messages = memory.context("I passed!")
            self.assertEqual([m["role"] for m in messages], ["user", "assistant"], "the person's turn always comes first")
            self.assertEqual(messages[1]["content"], "Welcome back! How did the driving test go?")


class GreetingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.llm = FakeLLM("Welcome back! How did your exam go?")
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, d: {"llm": self.llm, "tts": FakeTTS()}.get(which))
        self.device.characters.install(package.build(good_files(), manifest()))

    async def asyncTearDown(self):
        self.dir.cleanup()

    def talked(self, hours_ago):
        self.device.memory().add_turn("good luck wishes please", "Good luck!", now=time.time() - hours_ago * 3600)

    async def test_it_greets_only_when_turned_on_and_after_a_while_apart(self):
        self.talked(5)
        self.assertIsNone(await self.device.greet(), "off unless the owner turns it on")
        self.device.store.update_settings({"speak_first": ALWAYS})
        reply = await self.device.greet()
        self.assertEqual(reply.spoken, ["Welcome back!", "How did your exam go?"])
        prompt = self.llm.calls[0][1][-1]["content"]
        self.assertIn("after about 5 hours without talking", prompt)
        self.assertIn("Your last conversation with them was", self.llm.calls[0][0], "it knows how long it has been")
        self.assertEqual((self.device.listening, self.device.state), (True, "listening"), "it listens for an answer")
        self.assertGreater(self.device.answer_until, time.time())
        self.assertEqual(self.device.memory().history()[-1]["user"], "")
        self.device.listening, self.device.state = False, "idle"
        self.assertIsNone(await self.device.greet(), "not again straight away")

    async def test_not_soon_after_talking_in_quiet_hours_or_over_a_conversation(self):
        self.device.store.update_settings({"speak_first": ALWAYS})
        self.talked(1)
        self.assertIsNone(await self.device.greet(), "they talked an hour ago")
        self.talked(10)
        self.device.memory().clear()
        self.talked(10)
        self.device.store.update_settings({"speak_first": {"quiet_from": 22, "quiet_to": 8}})
        late = datetime.datetime.now().replace(hour=23, minute=0).timestamp()
        early = datetime.datetime.now().replace(hour=6, minute=30).timestamp()
        self.assertIsNone(await self.device.greet(now=late))
        self.assertIsNone(await self.device.greet(now=early))
        self.device.state = "speaking"
        self.assertIsNone(await self.device.greet(now=datetime.datetime.now().replace(hour=15).timestamp()))
        self.assertEqual(self.llm.calls, [])

    async def test_a_plan_whose_day_has_come_is_there_to_ask_about(self):
        self.device.store.update_settings({"speak_first": ALWAYS})
        memory = self.device.memory()
        memory.add_turn("my exam is tomorrow", "You'll do great!", now=time.time() - 30 * 3600)
        memory.add_memory("They have a biology exam", "plans and events", 4,
                          (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), now=time.time() - 30 * 3600)
        await self.device.greet()
        self.assertIn("Plans whose day has come", self.llm.calls[0][0])
        self.assertIn("biology exam", self.llm.calls[0][0])

    async def test_someone_speaking_nearby_brings_a_greeting_and_no_answer_closes_the_ear(self):
        self.device.store.update_settings({"speak_first": ALWAYS})
        self.talked(6)
        greeted = []

        async def greet():
            greeted.append(1)
        self.device.greet = greet
        self.device.microphone = Mic([QUIET] * 30 + [VOICE] * 60 + [QUIET] * 60)
        task = asyncio.ensure_future(self.device.listen_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        self.assertEqual(greeted, [1], "a second or so of speech-like sound")

        greeted.clear()
        self.device.microphone = Mic([QUIET] * 30 + [VOICE] * 10 + [QUIET] * 60)
        task = asyncio.ensure_future(self.device.listen_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        self.assertEqual(greeted, [], "a cough is not someone back")

        self.device.listening, self.device.state = True, "listening"
        self.device.answer_until = time.time() - 1
        self.device.microphone = Mic([QUIET] * 5)
        task = asyncio.ensure_future(self.device.listen_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        self.assertEqual((self.device.listening, self.device.state), (False, "idle"))

    def test_the_settings_are_checked(self):
        for bad in ({"on": "yes"}, {"after_hours": 0}, {"quiet_from": 25}, {"after_hours": True}):
            with self.assertRaises(ValueError):
                self.device.store.update_settings({"speak_first": bad})
        self.assertFalse(self.device.store.settings()["speak_first"]["on"])
        self.assertEqual(device_module.ANSWER_WINDOW, 30.0)


if __name__ == "__main__":
    unittest.main()
