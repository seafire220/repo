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


def _stamp(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def weatherapi_doc(start=None, hours=48, temps=None, codes=None, wet=None, current=True):
    """A made-up WeatherAPI.com forecast.json answer: hourly from the hour before `start` (seconds), with its
    current weather. `temps`, `codes` (WeatherAPI.com condition codes) and `wet` (mm) are functions of the time."""
    import time
    start = int((start or time.time()) // 3600 * 3600)
    temp = temps or (lambda t: 18.0)
    code = codes or (lambda t: 1003)
    rain = wet or (lambda t: 0.0)
    days = {}
    for k in range(hours):
        t = start + k * 3600
        days.setdefault(_stamp(t)[:10], []).append({"time_epoch": t, "temp_c": temp(t), "is_day": 1,
                                                     "condition": {"code": code(t)}, "precip_mm": rain(t)})
    doc = {"location": {"name": "Made-up town"},
           "forecast": {"forecastday": [{"date": d, "hour": h} for d, h in sorted(days.items())]}}
    if current:
        doc["current"] = {"last_updated_epoch": start + 600, "temp_c": temp(start), "is_day": 1,
                          "condition": {"code": code(start)}, "precip_mm": rain(start)}
    return doc


def openweather_docs(start=None, temps=None, ids=None, wet=None):
    """A made-up OpenWeather current weather and five-day forecast (three-hourly from `start`). `ids` are
    OpenWeather condition ids; `wet` (mm in the three hours before each entry)."""
    import time
    start = int((start or time.time()) // 3600 * 3600)
    temp = temps or (lambda t: 18.0)
    cid = ids or (lambda t: 802)
    rain = wet or (lambda t: 0.0)
    current = {"dt": start + 600, "main": {"temp": temp(start)}, "weather": [{"id": cid(start), "icon": "03d"}],
               "rain": {"1h": rain(start)}}
    entries = []
    for k in range(40):
        t = start + 3600 + k * 3 * 3600
        entries.append({"dt": t, "main": {"temp": temp(t)}, "weather": [{"id": cid(t), "icon": "03d"}],
                        "rain": {"3h": rain(t)}, "dt_txt": _stamp(t)})
    return current, {"cod": "200", "cnt": len(entries), "list": entries}


class FakeFetch:
    """Stands in for the internet when the weather is fetched (weather.Weather's `fetch`): answers as OpenWeather or
    WeatherAPI.com do, with made-up forecasts, and keeps each ask (the address and the headers)."""

    def __init__(self, status=200, weatherapi=None, openweather=None):
        self.status = status
        self.weatherapi = weatherapi
        self.openweather = openweather
        self.asked = []
        self.fail = False
        self.error = None                  # a WeatherAPI.com error code to answer with

    async def __call__(self, url, headers):
        import json
        self.asked.append((url, dict(headers)))
        if self.fail:
            raise OSError("no internet at " + url)
        if self.status != 200:
            return self.status, {}, json.dumps({"error": {"code": self.error or 0, "message": "no: " + url}}).encode()
        if "api.weatherapi.com/v1/forecast.json" in url:
            return 200, {}, json.dumps(self.weatherapi or weatherapi_doc()).encode()
        current, forecast = self.openweather or openweather_docs()
        if "api.openweathermap.org/data/2.5/weather" in url:
            return 200, {}, json.dumps(current).encode()
        if "api.openweathermap.org/data/2.5/forecast" in url:
            return 200, {}, json.dumps(forecast).encode()
        return 404, {}, b"{}"
