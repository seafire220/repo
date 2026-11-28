"""Your day (docs/28-user-guide.md): the weather, the morning hello, alarms the character says itself, daily
wake-ups, special days with festive backgrounds, the clock over the character, and the photo frame.

Nothing here asks a weather service: the forecasts are made up (fakes.FakeFetch)."""
import asyncio
import datetime
import gzip
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import backgrounds, conversation, package, special_days, store, weather  # noqa: E402
from companion import device as device_module  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.conversation import Conversation  # noqa: E402
from companion.device import Device, ring_line, time_in_words  # noqa: E402
from companion.memory import Memory  # noqa: E402
from companion.overlays import CLOCK, HINT  # noqa: E402
from companion.providers import ProviderError  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeFetch, FakeLLM, FakeStage, FakeTTS, openweather_docs, weatherapi_doc  # noqa: E402
from test_package import good_files, manifest  # noqa: E402

H = {"X-Companion": "1"}
PERTH = {"name": "Perth", "label": "Perth, Western Australia, Australia", "latitude": -31.9523, "longitude": 115.8613}


def at(hour, minute=0, days=0):
    """A moment today (or `days` later), local time, in seconds."""
    day = datetime.date.today() + datetime.timedelta(days=days)
    return datetime.datetime.combine(day, datetime.time(hour, minute)).timestamp()


ZONES = """# a made-up time zone table, as the operating system's zone1970.tab
AU\t-3157+11551\tAustralia/Perth\tWestern Australia
US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)
GB,GG,IM,JE\t+513030-0000731\tEurope/London
"""


def places_file(folder):
    rows = ["@AU\tAustralia", "@GB\tUnited Kingdom", "@US\tUnited States",
            "Perth\tWestern Australia\tAU\t-31.95\t115.86\t2000000\tAustralia/Perth",
            "Perth\tScotland\tGB\t56.40\t-3.43\t47000\tEurope/London",
            "Perth Amboy\tNew Jersey\tUS\t40.51\t-74.27\t52000\tAmerica/New_York",
            "Portland\tOregon\tUS\t45.52\t-122.68\t650000\tAmerica/Los_Angeles",
            "Portland\tMaine\tUS\t43.66\t-70.26\t68000\tAmerica/New_York",
            "Zürich\tZurich\tCH\t47.37\t8.55\t340000\tEurope/Zurich"]
    path = os.path.join(folder, "places.tsv.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("# made up\n" + "\n".join(rows) + "\n")
    return path


class PlacesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.zones = os.path.join(self.dir.name, "zone1970.tab")
        with open(self.zones, "w") as f:
            f.write(ZONES)
        self.original = weather.ZONE_TABLES
        weather.ZONE_TABLES = (self.zones,)

    def tearDown(self):
        weather.ZONE_TABLES = self.original
        self.dir.cleanup()

    def test_the_time_zone_gives_a_city_and_customs(self):
        perth = weather.zone_info("Australia/Perth")
        self.assertEqual((perth["name"], perth["countries"]), ("Perth", ["AU"]))
        self.assertAlmostEqual(perth["latitude"], -31.95, places=2)
        self.assertAlmostEqual(perth["longitude"], 115.85, places=2)
        york = weather.zone_info("America/New_York")
        self.assertEqual((york["name"], round(york["latitude"], 1), round(york["longitude"], 1)), ("New York", 40.7, -74.0))
        self.assertIsNone(weather.zone_info("Etc/UTC"))
        self.assertIsNone(weather.zone_info(""))
        self.assertEqual((weather.guess_units("America/New_York"), weather.guess_units("Europe/London")),
                         ("fahrenheit", "celsius"))
        self.assertEqual((weather.guess_12_hour("Australia/Perth"), weather.guess_12_hour("Europe/London")), (True, False))

    def test_towns_are_found_by_name_best_first(self):
        places = weather.Places(places_file(self.dir.name))
        found = places.search("perth", zone="Europe/London")
        self.assertEqual([p["label"] for p in found][:2], ["Perth, Scotland, United Kingdom",
                                                            "Perth, Western Australia, Australia"],
                         "the exact name first, then the device's own time zone")
        self.assertEqual(places.search("perth")[0]["region"], "Western Australia", "else the largest")
        self.assertEqual([p["region"] for p in places.search("Perth, scot")], ["Scotland"], "narrowed by the region")
        self.assertEqual(places.search("portland", zone="America/New_York")[0]["region"], "Maine")
        self.assertEqual(places.search("zurich")[0]["name"], "Zürich", "without accents")
        self.assertEqual(places.search("p"), [], "two letters at least")
        self.assertEqual(weather.Places(os.path.join(self.dir.name, "missing.gz")).search("perth"), [])

    def test_the_list_on_the_device_has_the_world_s_towns(self):
        places = weather.Places()
        self.assertGreater(len(places._load()), 30000)
        self.assertEqual(places.search("sydney", zone="Australia/Sydney")[0]["label"], "Sydney, New South Wales, Australia")


def hour_of(t):
    return datetime.datetime.fromtimestamp(t).hour


class ForecastTest(unittest.TestCase):
    def test_symbols_in_words(self):
        self.assertEqual(weather.symbol("clearsky_day")[:2], ("sunny", "clear"))
        self.assertEqual(weather.symbol("clearsky_night")[:2], ("clear", "clear-night"))
        self.assertEqual(weather.symbol("lightrainshowers_day")[:2], ("light rain showers", "rain"))
        self.assertEqual(weather.symbol("lightsleetshowersandthunder")[:2], ("light sleet showers and thunder", "thunder"))
        self.assertEqual(weather.symbol("heavysnow")[:2], ("heavy snow", "snow"))
        self.assertEqual(weather.symbol("haze")[:2], ("hazy", "fog"))
        self.assertEqual(weather.symbol("nonsense")[:2], ("", "cloud"))
        self.assertGreater(weather.symbol("rain")[2], weather.symbol("cloudy")[2])

    def test_each_service_s_conditions_in_the_device_s_words(self):
        self.assertEqual([weather.openweather_symbol(i, icon) for i, icon in ((800, "01d"), (800, "01n"), (802, "03d"),
                                                                              (804, "04d"), (501, "10d"), (520, "09d"),
                                                                              (601, "13d"), (211, "11d"), (741, "50d"),
                                                                              (799, ""))],
                         ["clearsky_day", "clearsky_night", "partlycloudy_day", "overcast", "rain", "lightrainshowers",
                          "snow", "rainandthunder", "fog", "fog"])
        self.assertEqual([weather.weatherapi_symbol(c, d) for c, d in ((1000, 1), (1000, 0), (1003, 1), (1183, 1),
                                                                       (1243, 1), (1276, 0), (1213, 1), (1135, 1),
                                                                       (1012, 1), (9999, 1))],
                         ["clearsky_day", "clearsky_night", "partlycloudy_day", "lightrain", "rainshowers",
                          "heavyrainandthunder", "lightsnow", "fog", "haze", "cloudy"])
        self.assertEqual(set(weather.WEATHERAPI) & {1000, 1282}, {1000, 1282})

    def test_openweather_as_its_documentation_shows_it(self):
        current = {"coord": {"lon": 10.99, "lat": 44.34}, "weather": [{"id": 501, "main": "Rain",
                   "description": "moderate rain", "icon": "10d"}], "main": {"temp": 25.33}, "rain": {"1h": 3.16},
                   "dt": 1661870592, "cod": 200}
        forecast = {"cod": "200", "list": [
            {"dt": 1661871600, "main": {"temp": 24.1}, "weather": [{"id": 500, "icon": "10d"}], "rain": {"3h": 0.26}},
            {"dt": 1661882400, "main": {"temp": 21.5}, "weather": [{"id": 500, "icon": "10n"}], "rain": {"3h": 0.57}},
            {"dt": 1661893200, "main": {"temp": 19.0}, "weather": [{"id": 804, "icon": "04n"}]}]}
        hours = weather.parse_openweather(current, forecast, now=1661870600)
        self.assertEqual([(h["t"], h["temp"], h["sym"], h["span"]) for h in hours],
                         [(1661870592, 25.33, "rain", 1), (1661871600, 24.1, "lightrain", 3),
                          (1661882400, 21.5, "lightrain", 3), (1661893200, 19.0, "overcast", 3)])
        self.assertEqual([h["wet"] for h in hours], [3.16, 0.19, 0.0, 0.0],
                         "each entry's rain is the next one's (it is for the three hours before it)")

    def test_a_day_summed_up(self):
        now = at(7)
        doc = weatherapi_doc(start=now, hours=40, temps=lambda t: 12 + (hour_of(t) - 7) * 0.8,
                             codes=lambda t: 1189 if 15 <= hour_of(t) < 17 else 1003,
                             wet=lambda t: 1.2 if 15 <= hour_of(t) < 17 else 0.0)
        hours = weather.parse_weatherapi(doc, now)
        s = weather.summary({"hours": hours, "place": "Perth"}, now + 600)
        self.assertEqual((s["now"]["temp"], s["now"]["words"], s["now"]["icon"]), (12, "partly cloudy", "partly"))
        self.assertEqual((s["today"]["low"], s["today"]["high"]), (12, 25))
        self.assertEqual((s["today"]["words"], s["today"]["wet_from"]), ("partly cloudy", "3 pm"),
                         "two hours of rain in a cloudy day: when it comes")
        long_rain = weatherapi_doc(start=now, hours=40, codes=lambda t: 1189 if 11 <= hour_of(t) < 20 else 1006,
                                   wet=lambda t: 1.0 if 11 <= hour_of(t) < 20 else 0.0)
        wet = weather.summary({"hours": weather.parse_weatherapi(long_rain, now)}, now)
        self.assertEqual((wet["today"]["words"], wet["today"]["wet_from"]), ("rain", None),
                         "rain matters more than clouds when it lasts")
        line = weather.describe(s)
        self.assertTrue(line.startswith("The weather in Perth (degrees Celsius): 12 and partly cloudy now"), line)
        self.assertIn("tomorrow", line)
        f = weather.summary({"hours": hours, "place": "Perth"}, now + 600, units="fahrenheit")
        self.assertEqual(f["now"]["temp"], 54)
        self.assertIn("degrees Fahrenheit", weather.describe(f))
        cold = weather.summary({"hours": weather.parse_weatherapi(weatherapi_doc(start=now, temps=lambda t: -3.4), now)}, now)
        self.assertTrue(weather.spoken(cold).startswith("It's minus 3 degrees"), weather.spoken(cold))
        self.assertIsNone(weather.summary({"hours": hours}, now + 5 * 86400), "too old to say anything")

    def test_a_three_hourly_forecast_too(self):
        now = at(7)
        current, forecast = openweather_docs(start=now, temps=lambda t: 10 + (hour_of(t) - 7) * 0.5,
                                             wet=lambda t: 1.5 if hour_of(t) in (16, 17, 18) else 0.0)
        s = weather.summary({"hours": weather.parse_openweather(current, forecast, now), "place": "Perth"}, now + 900)
        self.assertEqual(s["now"]["temp"], 10)
        self.assertEqual(s["today"]["words"], "partly cloudy")
        self.assertIn(s["today"]["wet_from"], ("2 pm", "3 pm", "4 pm"), "rain in the afternoon, within the three hours")
        self.assertIsNotNone(s["tomorrow"])

    def test_rain_later_in_a_dry_day_is_told_with_its_hour(self):
        now = at(7)
        doc = weatherapi_doc(start=now, hours=30, wet=lambda t: 0.6 if hour_of(t) == 15 else 0.0)
        s = weather.summary({"hours": weather.parse_weatherapi(doc, now)}, now)
        self.assertEqual(s["today"]["wet_from"], "3 pm")
        self.assertIn("rain from about 3 pm", weather.spoken(s))


KEY = "test-weather-key-1234567890"


class WeatherTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.fetch = FakeFetch()
        self.keys = {"weatherapi": KEY, "openweather": KEY}
        self.weather = weather.Weather(self.dir.name, fetch=self.fetch, secret=lambda name: self.keys.get(name, ""))
        self.settings = {"weather": {"provider": "weatherapi", "place": dict(PERTH), "units": "auto"}}

    async def asyncTearDown(self):
        self.dir.cleanup()

    async def test_without_a_service_or_its_key_nothing_is_asked(self):
        self.assertIsNone(await self.weather.refresh({"weather": {"provider": "", "place": PERTH}}, ""))
        self.assertIsNone(await self.weather.refresh({"weather": {"provider": "met", "place": PERTH}}, ""))
        self.assertIsNone(await self.weather.refresh({"weather": {"provider": "weatherapi", "place": None}}, "Etc/UTC"))
        self.keys.clear()
        self.assertFalse(self.weather.on(self.settings), "chosen, but no key")
        self.assertIsNone(await self.weather.refresh(self.settings, ""))
        self.assertEqual(self.fetch.asked, [])

    async def test_weatherapi_asked_once_then_kept_for_half_an_hour(self):
        got = await self.weather.refresh(self.settings, "")
        self.assertEqual(len(got["hours"]), 49, "48 hours and the weather now")
        url, headers = self.fetch.asked[0]
        self.assertEqual(url, "https://api.weatherapi.com/v1/forecast.json?key=%s&q=-31.95,115.86&days=2&aqi=no"
                              "&alerts=no" % KEY, "only the key and the position, to two decimals")
        self.assertTrue(headers["User-Agent"].startswith("CompanionDevice/"))
        await self.weather.refresh(self.settings, "")
        await self.weather.refresh(self.settings, "", now=time.time() + 25 * 60)
        self.assertEqual(len(self.fetch.asked), 1, "not again within half an hour")
        await self.weather.refresh(self.settings, "", now=time.time() + 31 * 60)
        self.assertEqual(len(self.fetch.asked), 2)
        self.assertEqual(self.weather.now(self.settings, "")["place"], "Perth")
        with open(os.path.join(self.dir.name, "weather.json")) as f:
            self.assertNotIn(KEY, f.read(), "the key is not kept with it")

    async def test_openweather_takes_two_asks(self):
        self.settings["weather"]["provider"] = "openweather"
        got = await self.weather.refresh(self.settings, "")
        self.assertEqual([u.split("?")[0] for u, _ in self.fetch.asked],
                         ["https://api.openweathermap.org/data/2.5/weather", "https://api.openweathermap.org/data/2.5/forecast"])
        self.assertIn("lat=-31.95&lon=115.86&units=metric&appid=" + KEY, self.fetch.asked[0][0])
        self.assertEqual(got["hours"][0]["span"], 1)
        self.assertEqual(got["hours"][1]["span"], 3)
        self.assertIsNotNone(self.weather.now(self.settings, ""))

    async def test_a_refused_key_is_said_plainly_and_never_logged(self):
        self.fetch.status = 401
        with self.assertLogs("companion.weather", "WARNING") as logs:
            self.assertIsNone(await self.weather.refresh(self.settings, ""))
        self.assertEqual(self.weather.problem(self.settings, ""), "WeatherAPI.com refused the key")
        self.assertFalse(any(KEY in line for line in logs.output), "the key is never logged")
        await self.weather.refresh(self.settings, "", now=time.time() + 30 * 60)
        self.assertEqual(len(self.fetch.asked), 1, "a refused key waits an hour")
        self.keys["weatherapi"] = KEY + "-new"
        self.fetch.status = 200
        self.assertIsNotNone(await self.weather.refresh(self.settings, ""), "a new key is tried at once")
        self.assertEqual(self.weather.problem(self.settings, ""), "")
        self.settings["weather"]["provider"] = "openweather"
        self.fetch.status = 401
        await self.weather.refresh(self.settings, "")
        self.assertIn("a couple of hours", self.weather.problem(self.settings, ""))
        self.fetch.status, self.fetch.error = 403, 2007
        self.settings["weather"]["provider"] = "weatherapi"
        self.keys["weatherapi"] = KEY + "-third"
        await self.weather.refresh(self.settings, "")
        self.assertEqual(self.weather.problem(self.settings, ""), "this month's calls at WeatherAPI.com are used up")

    async def test_without_internet_the_last_forecast_is_used_for_a_while(self):
        await self.weather.refresh(self.settings, "")
        self.fetch.fail = True
        later = time.time() + 2 * 3600
        with self.assertLogs("companion.weather", "WARNING") as logs:
            self.assertIsNotNone(await self.weather.refresh(self.settings, "", now=later))
        self.assertFalse(any(KEY in line for line in logs.output), "a network error's address (with the key) is not logged")
        self.assertIn("could not be reached", self.weather.problem(self.settings, ""))
        self.assertEqual(len(self.fetch.asked), 2)
        await self.weather.refresh(self.settings, "", now=later + 60)
        self.assertEqual(len(self.fetch.asked), 2, "after a failure it waits before asking again")
        self.assertIsNone(self.weather.cached(self.settings, "", time.time() + 7 * 3600), "too old after six hours")

    async def test_a_new_place_or_service_is_asked_for_at_once(self):
        await self.weather.refresh(self.settings, "")
        moved = {"weather": {"provider": "weatherapi", "place": {"name": "Perth", "label": "Perth, Scotland",
                                                                   "latitude": 56.4, "longitude": -3.43}}}
        self.assertIsNone(self.weather.cached(moved, "", time.time()), "not the old town's")
        await self.weather.refresh(moved, "")
        self.assertEqual(len(self.fetch.asked), 2)
        self.assertIn("q=56.40,-3.43", self.fetch.asked[1][0])
        moved["weather"]["provider"] = "openweather"
        self.assertIsNone(self.weather.cached(moved, "", time.time()), "not the other service's")

    async def test_a_strange_answer_is_not_kept(self):
        self.fetch.weatherapi = {"forecast": {"forecastday": []}}
        self.assertIsNone(await self.weather.refresh(self.settings, ""))
        self.assertIn("no temperatures", self.weather.problem(self.settings, ""))
        self.fetch.weatherapi = {"current": "nonsense"}
        self.assertIsNone(await self.weather.refresh(self.settings, "", now=time.time() + 3600))
        self.assertIn("unexpected", self.weather.problem(self.settings, ""))


class SpecialDaysTest(unittest.TestCase):
    def on(self, iso, settings, arrived=None):
        return [(s["kind"], s["what"]) for s in special_days.on(datetime.date.fromisoformat(iso), settings, arrived)]

    def test_easter(self):
        self.assertEqual([special_days.easter(y).isoformat() for y in (2024, 2025, 2026, 2027, 2038)],
                         ["2024-03-31", "2025-04-20", "2026-04-05", "2027-03-28", "2038-04-25"])

    def test_birthdays_with_and_without_the_year(self):
        with_year = {"owner": {"birthday": "1996-02-29"}}
        self.assertEqual(self.on("2028-02-29", with_year), [("birthday", "their birthday")])
        self.assertEqual(self.on("2026-02-28", with_year), [("birthday", "their birthday")], "28 February in other years")
        line = special_days.on(datetime.date(2026, 2, 28), with_year)[0]["line"]
        self.assertIn("they are 30 today", line)
        plain = special_days.on(datetime.date(2026, 7, 9), {"owner": {"birthday": "07-09"}})[0]["line"]
        self.assertNotIn("they are", plain)
        self.assertEqual(self.on("2026-07-10", {"owner": {"birthday": "07-09"}}), [])

    def test_the_day_the_character_arrived(self):
        arrived = datetime.date(2026, 11, 28)
        self.assertEqual(self.on("2026-11-28", {}, arrived), [], "not the day itself")
        self.assertEqual(self.on("2027-03-07", {}, arrived), [("arrival", "your 100th day together")])
        self.assertEqual(self.on("2027-11-28", {}, arrived), [("arrival", "the day you arrived")])
        self.assertIn("2 years since", special_days.on(datetime.date(2028, 11, 28), {}, arrived)[0]["line"])

    def test_their_own_days_and_holidays(self):
        settings = {"special_days": {"dates": [{"date": "2016-06-03", "what": "our wedding anniversary"},
                                               {"date": "2026-12-01", "what": "moving house", "once": True},
                                               {"date": "07-14", "what": "Mum's birthday"}]}}
        self.assertEqual(self.on("2026-06-03", settings), [("theirs", "our wedding anniversary")])
        self.assertIn("10 years since 2016", special_days.on(datetime.date(2026, 6, 3), settings)[0]["line"])
        self.assertEqual(self.on("2026-12-01", settings), [("theirs", "moving house")])
        self.assertEqual(self.on("2027-12-01", settings), [], "only that year")
        self.assertEqual(self.on("2027-07-14", settings), [("theirs", "Mum's birthday")])
        self.assertEqual(self.on("2026-12-25", {}), [("holiday", "Christmas Day")])
        self.assertEqual(self.on("2026-04-05", {}), [("holiday", "Easter Sunday")])
        self.assertEqual(self.on("2026-12-25", {"special_days": {"holidays": False}}), [])
        self.assertEqual(self.on("2026-12-25", {"special_days": {"on": False}}), [])

    def test_the_festive_background(self):
        birthday_at_christmas = {"owner": {"birthday": "12-25"}}
        found = special_days.on(datetime.date(2026, 12, 25), birthday_at_christmas)
        self.assertEqual([s["kind"] for s in found], ["birthday", "holiday"], "the most personal first")
        self.assertEqual(special_days.festive(found, birthday_at_christmas), "party")
        self.assertIsNone(special_days.festive(found, {"special_days": {"festive": False}}))
        self.assertIsNone(special_days.festive([], {}))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = store.Store(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_good_ones_are_kept(self):
        saved = self.store.update_settings({
            "owner": {"name": "Sam", "birthday": "02-29"},
            "weather": {"provider": "openweather", "place": PERTH, "units": "celsius"},
            "alarms": {"wake_ups": [{"time": "06:45", "days": [0, 1, 2, 3, 4], "label": "work"}]},
            "special_days": {"dates": [{"date": "2026-12-01", "what": "moving house", "once": True}]},
            "clock": {"show": "idle", "hours": "24"}, "background": {"every": 30}})
        wake = saved["alarms"]["wake_ups"][0]
        self.assertRegex(wake["id"], r"^[0-9a-f]{8}$", "each wake-up is given an id")
        self.assertTrue(wake["on"])
        self.assertEqual(self.store.update_settings({"weather": {"place": None}})["weather"]["place"], None,
                         "back to the time zone's city")

    def test_bad_ones_are_refused(self):
        for bad in ({"owner": {"birthday": "13-01"}}, {"owner": {"birthday": "1850-01-01"}}, {"owner": {"name": "x" * 41}},
                    {"owner": {"name": "a\nb"}}, {"weather": {"units": "kelvin"}}, {"weather": {"provider": "met"}},
                    {"weather": {"place": {"name": "x", "latitude": 91, "longitude": 0}}},
                    {"alarms": {"wake_ups": [{"time": "24:00"}]}}, {"alarms": {"wake_ups": [{"time": "07:00", "days": []}]}},
                    {"alarms": {"wake_ups": [{"time": "07:00", "days": [7]}]}},
                    {"alarms": {"wake_ups": [{"time": "07:00"}] * 11}},
                    {"special_days": {"dates": [{"date": "12-25", "what": ""}]}},
                    {"special_days": {"dates": [{"date": "12-01", "what": "moving", "once": True}]}},
                    {"clock": {"show": "sometimes"}}, {"background": {"every": 0}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.store.update_settings(bad)


class ConversationTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_model_is_told_about_today(self):
        with tempfile.TemporaryDirectory() as d:
            llm = FakeLLM("Good morning!")
            conv = Conversation(llm=llm, tts=FakeTTS(), speaker=Speaker(command=None, realtime=False), stage=FakeStage(),
                                memory=Memory(d), persona="You are Aria.", today=lambda: "Today is Christmas Day.")
            await conv.respond("hello")
            system = llm.calls[0][0]
            self.assertIn("Today is Christmas Day.", system)
            self.assertLess(system.index("It is now"), system.index("Today is Christmas Day."))

            def broken():
                raise RuntimeError("no")
            conv.today = broken
            with self.assertLogs("companion.conversation", "ERROR"):
                reply = await conv.respond("hello again")
            self.assertIsNone(reply.error, "a failure there never spoils a reply")

    async def test_a_fixed_line_needs_no_language_model(self):
        with tempfile.TemporaryDirectory() as d:
            tts, events = FakeTTS(), []
            conv = Conversation(llm=None, tts=FakeTTS(), speaker=Speaker(command=None, realtime=False), stage=FakeStage(),
                                memory=Memory(d), persona="You are Aria.", on_event=lambda k, data: events.append(k))
            reply = await conv.say_line("Good morning, Sam! It's seven o'clock.", "happy", tts=tts)
            self.assertEqual(reply.spoken, ["Good morning, Sam! It's seven o'clock."])
            self.assertEqual(tts.texts, ["Good morning, Sam! It's seven o'clock."], "in the voice given")
            self.assertIn("saying", events)


class WordsTest(unittest.TestCase):
    def test_times_as_people_say_them(self):
        self.assertEqual([time_in_words(at(h, m)) for h, m in ((7, 0), (7, 15), (7, 30), (7, 45), (7, 5), (7, 40), (7, 3),
                                                                 (7, 59), (0, 20), (12, 0))],
                         ["seven o'clock", "quarter past seven", "half past seven", "quarter to eight", "five past seven",
                          "twenty to eight", "3 minutes past seven", "1 minute to eight", "twenty past twelve",
                          "twelve o'clock"])

    def test_fixed_lines(self):
        self.assertEqual(ring_line("wake", "wake up", at(7), "Sam", 0, "It's 14 degrees and sunny."),
                         "Good morning, Sam! It's seven o'clock, time to get up. It's 14 degrees and sunny.")
        self.assertEqual(ring_line("wake", "", at(7, 5), "", 1), "Time to get up, it's five past seven.")
        self.assertEqual(ring_line("timer", "pasta", at(7), "Sam"), "Sam! Your pasta timer is done.")
        self.assertEqual(ring_line("timer", "timer", at(7)), "Your timer is done.")
        self.assertEqual(ring_line("alarm", "the dentist", at(9, 30), "Sam"), "Sam! It's half past nine: time for the dentist.")


class DeviceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.llm = FakeLLM("Good morning! Rise and shine.")
        self.tts = FakeTTS()
        self.stage = FakeStage()
        self.made = {"llm": lambda: self.llm, "tts": lambda: self.tts}
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, d: self.made[which]() if which in self.made else None)
        self.fetch = FakeFetch()
        self.device.weather.fetch = self.fetch
        self.device.store.set_secret("weatherapi", KEY)
        self.cid = self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate(self.cid)
        self.saved = (dict(device_module.RING_AGAIN), device_module.ANSWER_WINDOW, device_module.RING_WAIT)
        device_module.RING_AGAIN.update({"wake": 0.05, "alarm": 0.05, "timer": 0.05})
        device_module.ANSWER_WINDOW = 0.05

    async def asyncTearDown(self):
        device_module.RING_AGAIN.update(self.saved[0])
        device_module.ANSWER_WINDOW = self.saved[1]
        self.device.stop_ringing()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.sleep(0.01)
        self.dir.cleanup()

    def overlays(self, slot):
        return [c["overlay"] for c in self.stage.commands if isinstance(c, dict) and "overlay" in c
                and c["overlay"]["slot"] == slot]

    async def test_arrival_is_kept_when_a_character_is_installed(self):
        self.assertEqual(self.device.characters.device_settings(self.cid)["arrived"], datetime.date.today().isoformat())
        self.assertEqual(self.device.arrival(self.cid), datetime.date.today())

    async def test_today_for_the_character(self):
        self.device.store.update_settings({"owner": {"name": "Sam", "birthday": datetime.date.today().strftime("%m-%d")},
                                           "weather": {"provider": "weatherapi", "place": PERTH}})
        self.device.memory().add_memory("Dentist at 10", "plans and events", 3, datetime.date.today().isoformat())
        await self.device.weather_now()
        text = self.device.today_text()
        self.assertIn("The person you live with is called Sam.", text)
        self.assertIn("Today is their birthday", text)
        self.assertIn("The weather in Perth (degrees", text)
        self.assertIn("Their plans for today, from what they told you: Dentist at 10.", text)
        self.device.store.update_settings({"weather": {"provider": ""}, "owner": {"name": "", "birthday": ""}})
        self.assertNotIn("weather", self.device.today_text())

    async def test_the_morning_hello(self):
        self.device.store.update_settings({"speak_first": {"on": True, "quiet_from": 0, "quiet_to": 0},
                                           "weather": {"provider": "weatherapi", "place": PERTH}})
        self.device.memory().add_turn("good night", "Sleep well!", now=at(22, days=-1))
        reply = await self.device.greet(now=at(7, 30))
        self.assertIsNotNone(reply)
        system, messages = self.llm.calls[0]
        self.assertIn("It is morning", messages[-1]["content"])
        self.assertIn("The weather in Perth", system, "it was fetched first, to be mentioned")
        self.assertEqual(len(self.fetch.asked), 1)
        self.device.listening, self.device.state = False, "idle"
        self.assertIsNone(await self.device.greet(now=at(8)), "once a morning")

    async def test_no_morning_hello_after_talking_this_morning_or_when_turned_off(self):
        self.device.store.update_settings({"speak_first": {"on": True, "quiet_from": 0, "quiet_to": 0}})
        self.device.memory().add_turn("morning!", "Morning!", now=at(6, 30))
        self.assertIsNone(await self.device.greet(now=at(7, 30)), "they talked an hour ago, this morning")
        self.device.memory().clear()
        self.device.memory().add_turn("good night", "Sleep well!", now=at(22, days=-1))
        self.device.store.update_settings({"speak_first": {"morning": False}})
        reply = await self.device.greet(now=at(7, 30))
        self.assertIsNotNone(reply, "nine hours apart: the usual greeting")
        self.assertNotIn("It is morning", self.llm.calls[0][1][-1]["content"])
        self.assertIn("without talking", self.llm.calls[0][1][-1]["content"])

    async def test_an_alarm_is_said_by_the_character_and_called_again_louder(self):
        self.device.store.update_settings({"owner": {"name": "Sam"}, "volume": 0.5})
        volumes = []
        played = self.device.speaker.play

        async def play(*args, **kwargs):
            volumes.append(self.device.speaker.volume)
            return await played(*args, **kwargs)
        self.device.speaker.play = play
        task = self.device._ring("wake up", "wake")
        self.assertIn("wake up", self.device.rings)
        await asyncio.sleep(0.01)
        self.assertEqual(self.overlays(HINT)[-1]["anchor"], "center", "a hint: press the button to stop it")
        await asyncio.wait_for(task, 10)
        self.assertIn("wake-up alarm they set", self.llm.calls[0][1][-1]["content"], "first in its own words")
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(len(self.tts.texts), 2 + 4, "two sentences, then four more calls in fixed lines")
        self.assertIn("Sam", self.tts.texts[2])
        self.assertGreater(volumes[-1], volumes[0], "louder each time")
        self.assertLessEqual(max(volumes), 1.0)
        self.assertEqual(self.device.speaker.volume, 0.5, "the volume is put back")
        self.assertEqual(self.device.rings, {})
        events = [e.get("did") for e in self.device.log if e["kind"] == "timer"]
        self.assertEqual(events[-1], "not answered")

    async def test_the_button_stops_it_and_listens(self):
        self.device.store.update_settings({"alarms": {"repeat": True}})
        task = self.device._ring("tea", "timer")
        await asyncio.sleep(0.02)
        self.device.button()
        await asyncio.wait([task], timeout=2)
        self.assertTrue(task.done())
        self.assertEqual(self.device.rings, {})
        self.assertTrue(self.device.listening, "it listens: for 'five more minutes', say")
        self.assertIn("alarm stopped", [e.get("did") for e in self.device.log if e["kind"] == "button"])

    async def test_answering_stops_it(self):
        task = self.device._ring("tea", "timer")
        await asyncio.sleep(0.02)
        self.device._conversation_event("heard", {"text": "thanks"})
        await asyncio.sleep(0.01)
        self.assertTrue(task.done())
        self.assertIn("answered", [e.get("did") for e in self.device.log if e["kind"] == "timer"])

    async def test_without_the_language_model_it_says_a_fixed_line(self):
        self.llm.fail = "start"
        await asyncio.wait_for(self.device._ring("pasta", "timer", times=1), 10)
        self.assertEqual(self.tts.texts, ["Your pasta timer is done."])

    async def test_without_any_voice_it_plays_tones(self):
        self.made = {"llm": lambda: (_ for _ in ()).throw(ProviderError("no model chosen"))}
        tones = []

        async def notice():
            tones.append(1)
        self.device.notice = notice
        await asyncio.wait_for(self.device._ring("pasta", "timer", times=1), 10)
        self.assertEqual(len(tones), 3)

    async def test_daily_wake_ups(self):
        monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
        seven = datetime.datetime.combine(monday, datetime.time(7, 0)).timestamp()
        self.device.store.update_settings({"alarms": {"wake_ups": [
            {"id": "w1", "time": "07:00", "days": [0, 1, 2, 3, 4], "label": "work"},
            {"id": "w2", "time": "09:00", "days": [5, 6], "label": "", "on": False}]}})
        self.device._ring = lambda label, kind, times=None: rung.append((label, kind))
        rung = []
        self.assertEqual(self.device.wake_step(seven - 60), [])
        self.assertEqual(self.device.wake_step(seven + 30), ["work"])
        self.assertEqual(self.device.wake_step(seven + 90), [], "once a day")
        self.assertEqual(rung, [("work", "wake")])
        with open(os.path.join(self.dir.name, "wake-ups.json")) as f:
            self.assertEqual(json.load(f), {"w1": monday.isoformat()})
        self.assertEqual(self.device.wake_step(seven + 86400 + 10 * 60), [], "not ten minutes late (the device was off)")
        self.assertEqual(self.device.wake_step(seven + 5 * 86400 + 30), [], "not on Saturday")
        nxt = self.device.next_wake_up(seven + 60)
        self.assertEqual((nxt["label"], nxt["at"]), ("work", round(seven + 86400)))

    async def test_the_clock_shows_while_nobody_is_talking(self):
        self.device.store.update_settings({"clock": {"show": "idle", "hours": "24"},
                                           "weather": {"provider": "weatherapi", "place": PERTH}})
        await self.device.weather_now()
        shown = self.device.clock_step(at(7, 42))
        self.assertEqual(shown[0], "07:42")
        self.assertEqual(shown[3][1], 18, "with the weather")
        self.assertEqual(self.overlays(CLOCK)[-1]["anchor"], "top_left")
        self.assertIsNone(self.device.clock_step(at(7, 42))[0:0] or None, "nothing new: not drawn again")
        count = len(self.overlays(CLOCK))
        self.device.set_state("speaking")
        self.assertIsNone(self.overlays(CLOCK)[-1]["image"], "hidden while it talks")
        self.assertEqual(len(self.overlays(CLOCK)), count + 1)
        self.device.set_state("idle")
        self.assertIsNotNone(self.overlays(CLOCK)[-1]["image"], "back after")
        self.device.store.update_settings({"clock": {"show": "always"}})
        self.device.set_state("speaking")
        self.assertIsNotNone(self.overlays(CLOCK)[-1]["image"], "always")
        self.device.store.update_settings({"clock": {"show": "off"}})
        self.device.clock_step()
        self.assertIsNone(self.overlays(CLOCK)[-1]["image"])

    async def test_a_festive_background_on_special_days(self):
        christmas = datetime.datetime(datetime.date.today().year, 12, 25, 12).timestamp()
        ordinary = datetime.datetime(datetime.date.today().year, 12, 27, 12).timestamp()
        self.device.store.update_settings({"background": {"choice": "studio"}})
        self.assertTrue(self.device.background_spec(self.cid, now=christmas)["path"].endswith("snowy.jpg"))
        self.assertTrue(self.device.background_spec(self.cid, now=ordinary)["path"].endswith("studio.jpg"))
        self.device.store.update_settings({"special_days": {"festive": False}})
        self.assertTrue(self.device.background_spec(self.cid, now=christmas)["path"].endswith("studio.jpg"))
        self.device.store.update_settings({"special_days": {"festive": True}, "background": {"choice": "none"}})
        self.assertIsNone(self.device.background_spec(self.cid, now=christmas), "no picture means none")

    async def test_the_photo_frame(self):
        from test_backgrounds import photo
        self.device.store.update_settings({"background": {"choice": "photos", "every": 10}})
        self.assertTrue(self.device.background_spec(self.cid, now=at(13))["path"].endswith("day.jpg"),
                        "by the time of day until there are pictures")
        first = self.device.backgrounds.add(photo(size=(400, 600), gps=False), title="one")
        second = self.device.backgrounds.add(photo(size=(400, 600), gps=False), title="two")
        start = (time.time() // 600 + 1) * 600
        shown = [os.path.basename(self.device.background_spec(self.cid, now=start + k * 600)["path"]) for k in range(3)]
        self.assertEqual(len(set(shown[:2])), 2, "a new one every ten minutes")
        self.assertEqual(shown[0], shown[2], "then round again")
        self.assertEqual(set(shown), {first + ".jpg", second + ".jpg"})


class WebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage,
                             provider_factory=lambda which, d: {"llm": FakeLLM("Your tea is ready."), "tts": FakeTTS()}.get(which))
        self.device.weather.fetch = FakeFetch()
        self.cid = self.device.characters.install(package.build(good_files(), manifest()))
        self.device.activate(self.cid)
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        self.device.stop_ringing()
        await self.client.close()
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.sleep(0.01)
        self.dir.cleanup()

    async def test_the_day_page(self):
        r = await self.client.get("/api/places?q=perth")
        found = (await r.json())["places"]
        self.assertIn("Perth, Western Australia, Australia", [p["label"] for p in found])
        r = await self.client.post("/api/weather/check", headers=H)
        self.assertEqual(r.status, 409, "no weather service chosen")
        r = await self.client.put("/api/settings", json={"weather": {"provider": "openweather", "place": {
            k: found[0][k] for k in ("name", "label", "latitude", "longitude")}}}, headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/weather/check", headers=H)
        self.assertEqual((r.status, await r.text()), (409, "add the weather service's key first"))
        r = await self.client.put("/api/keys/openweather", json={"value": KEY}, headers=H)
        self.assertEqual(r.status, 200)
        r = await self.client.post("/api/weather/check", headers=H)
        day = await r.json()
        self.assertTrue(day["weather_said"].startswith("The weather in Perth"), day)
        self.assertEqual((day["provider"], day["key_set"], day["problem"]), ("openweather", True, ""))
        self.assertEqual(day["providers"]["openweather"]["credit"], "Weather data provided by OpenWeather")
        self.assertNotIn(KEY, json.dumps(day), "the key is never sent back")
        keys = await (await self.client.get("/api/keys")).json()
        self.assertTrue(keys["openweather"])
        self.assertFalse(keys["weatherapi"])
        self.assertEqual(day["ringing"], [])
        thumb = await self.client.get("/api/backgrounds/fireworks/thumb")
        self.assertEqual(thumb.status, 200)

    async def test_alarms_from_the_page(self):
        self.device.action({"kind": "timer", "seconds": 600, "label": "tea"})
        day = await (await self.client.get("/api/day")).json()
        self.assertEqual([t["label"] for t in day["timers"]], ["tea"])
        r = await self.client.delete("/api/timers/tea", headers=H)
        self.assertEqual((await r.json())["timers"], [])
        self.assertEqual((await self.client.delete("/api/timers/tea", headers=H)).status, 404)
        r = await self.client.post("/api/alarms/try", headers=H)
        self.assertEqual((await r.json())["ringing"], ["wake up"])
        self.assertEqual((await self.client.post("/api/alarms/try", headers=H)).status, 409)
        await asyncio.sleep(0.01)
        r = await self.client.post("/api/alarms/stop", headers=H)
        stopped = await r.json()
        self.assertTrue(stopped["stopped"])
        self.assertEqual(stopped["ringing"], [])

    async def test_the_owner_must_be_logged_in(self):
        await self.client.post("/api/logout", headers=H)
        for path in ("/api/day", "/api/places?q=perth"):
            self.assertEqual((await self.client.get(path)).status, 401)


if __name__ == "__main__":
    unittest.main()
