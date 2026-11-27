"""Stand-ins for providers, the stage and the speaker, for tests and the virtual device."""
import asyncio
import array
import math

from companion.providers import ProviderError


class FakeLLM:
    def __init__(self, reply="Hello there. How are you today?", pieces=7, delay=0.0, fail=None, hang=False):
        self.reply = reply
        self.pieces = pieces
        self.temperature, self.max_tokens = 0.8, 400       # as the real clients start
        self.delay = delay
        self.fail = fail
        self.hang = hang
        self.calls = []

    async def stream(self, system, messages):
        """Streams `reply`; if `reply` is a list, each call takes the next one."""
        self.calls.append((system, list(messages)))
        reply = self.reply
        if isinstance(reply, list):
            reply = reply[min(len(self.calls) - 1, len(reply) - 1)]
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail == "start":
            raise ProviderError("the fake model failed", retry=True)
        step = max(1, len(reply) // self.pieces)
        for i in range(0, len(reply), step):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield reply[i:i + step]
            if self.fail == "middle" and i > len(reply) // 2:
                raise ProviderError("the fake model broke off")


class FakeTTS:
    rate = 24000

    def __init__(self, seconds_per_char=0.01, fail_on=None):
        self.seconds_per_char = seconds_per_char
        self.fail_on = fail_on
        self.texts = []

    async def stream(self, text):
        if self.fail_on and self.fail_on in text:
            raise ProviderError("the fake voice failed")
        self.texts.append(text)
        n = int(self.rate * self.seconds_per_char * len(text))
        samples = array.array("h", (int(8000 * math.sin(i / 7.0)) for i in range(n)))
        data = samples.tobytes()
        for i in range(0, len(data), 4800):
            yield data[i:i + 4800]
            await asyncio.sleep(0)


class FakeStage:
    def __init__(self):
        self.commands = []
        self.params = set()
        self.gestures = ["nod", "shake", "bow", "tilt", "wave"]
        self.emotion_map = {}
        self.param_info = []        # as the stage reports when it is ready
        self.controls = []
        self.actions = ["look_around", "yawn", "stretch"]
        self.disabled = set()

    def send(self, command):
        self.commands.append(command)

    def state(self, name):
        self.commands.append({"state": name})

    def mouth(self, level):
        self.commands.append({"mouth": level})

    def express(self, emotion, intensity=1.0):
        self.commands.append({"express": emotion})

    def random_gestures(self, on):
        self.commands.append({"random_gestures": on})

    def perform(self, performance):
        self.commands.append({"perform": performance})

    def syllable(self, k):
        self.commands.append({"syllable": k})

    def blink(self):
        self.commands.append({"blink": True})

    def gesture(self, name):
        self.commands.append({"gesture": name})

    def temperament(self, energy, expressiveness):
        self.commands.append({"temperament": {"energy": energy, "expressiveness": expressiveness}})

    def gestures_off(self, names):
        self.disabled = set(names)
        self.commands.append({"gestures_off": sorted(self.disabled)})

    def define(self, name, entry):
        self.commands.append({"define": dict(entry, name=name)})
        if entry.get("kind", "gesture") == "gesture" and name not in self.gestures and not name.startswith("_"):
            self.gestures.append(name)
        return True

    def undefine(self, name):
        self.commands.append({"undefine": name})
        return True

    async def describe_library(self, timeout=3.0):
        clips = {g: {"kind": "gesture", "needs": [], "tracks": {"head_pitch": [[0, 0], [0.3, -0.4], [0.6, 0]]},
                     "origin": "built in", "can": True} for g in self.gestures}
        from companion.motions import CONTROLS
        return {"clips": clips, "controls": list(CONTROLS)}

    def clock(self, hour):
        self.commands.append({"clock": round(hour, 3)})

    def poke(self):
        self.commands.append({"poke": True})

    def heard(self, level):
        self.commands.append({"heard": level})

    def states(self):
        return [c["state"] for c in self.commands if "state" in c]
