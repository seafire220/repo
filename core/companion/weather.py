"""The weather where the owner lives: for the morning hello, wake-up alarms, the character's answers and the
clock on the screen (docs/28-user-guide.md, "Your day").

Like the AI services, the weather comes from a service the owner chooses, with the owner's own key, kept with
the other keys (store.py): OpenWeather or WeatherAPI.com, both free for this much use (docs/decision-log.md,
D-075). The device sums each service's forecast up the same way, in words and pictures of its own.

Only the place's latitude and longitude, rounded to two decimals (about a kilometre), and the key go to the
service. The forecast is kept, and asked for again at most every half an hour, only when something will say or
show it; the last one is used for six hours if the internet is down. A refused key is not tried again for an
hour, unless the owner saves a new one. Nothing logged holds the key or the address it was sent to.

The place is chosen on the settings page from a list kept on the device (data/places.tsv.gz: every town of
15,000 people or more, from GeoNames, CC BY 4.0), so what the owner types to find their town never leaves the
device. Until they choose, it is the main city of the device's time zone (from the system's time zone tables).
"""
import asyncio
import datetime
import gzip
import hashlib
import json
import logging
import os
import time
import unicodedata
import urllib.parse

from .store import write_atomic

log = logging.getLogger("companion.weather")

PLACES_FILE = os.path.join(os.path.dirname(__file__), "data", "places.tsv.gz")
ZONE_TABLES = ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab")
# The weather services: their names, where the owner gets a key, and the credit each asks to be shown.
PROVIDERS = {
    "openweather": {"name": "OpenWeather", "site": "https://openweathermap.org",
                    "credit": "Weather data provided by OpenWeather"},
    "weatherapi": {"name": "WeatherAPI.com", "site": "https://www.weatherapi.com",
                   "credit": "Powered by WeatherAPI.com"},
}
TOWNS_CREDIT = "Towns from GeoNames (geonames.org), CC BY 4.0."
REFRESH = 30 * 60        # never asked again sooner than this for the same place
RETRY = 10 * 60          # after a failed ask, wait this long
KEY_RETRY = 3600         # after a refused key, wait this long (or until a new key is saved)
KEEP = 6 * 3600          # a forecast is used for this long after it was fetched
AHEAD = 3 * 86400        # how much of the forecast is kept
TIMEOUT = 12.0
LARGEST = 2 << 20        # bytes; a forecast is about 40 KB
WET = 0.3                # millimetres of rain (or snow, as water) in an hour that count as a wet hour
# Countries that count temperatures in Fahrenheit (for "auto").
FAHRENHEIT = {"US", "LR", "BS", "BZ", "KY", "PW", "FM", "MH"}


class WeatherError(Exception):
    pass


def fold(text):
    """Text for matching names: no accents, no capitals, letters and digits only, single spaces."""
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text if not unicodedata.combining(c)).split())


# ---------------------------------------------------------------- places

def _degrees(text, whole):
    """An ISO 6709 coordinate from the time zone tables (±DDMM, ±DDMMSS, ±DDDMM, ±DDDMMSS) in degrees."""
    sign = -1.0 if text[0] == "-" else 1.0
    digits = text[1:]
    seconds = int(digits[whole + 2:] or 0)
    return sign * (int(digits[:whole]) + int(digits[whole:whole + 2]) / 60.0 + seconds / 3600.0)


def zone_info(zone, tables=ZONE_TABLES):
    """The main place of a time zone, from the system's tables: {"name", "latitude", "longitude",
    "countries"}, or None (no zone, an alias the tables do not list, or no tables)."""
    if not zone:
        return None
    for table in tables:
        try:
            with open(table, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if line.startswith("#") or len(parts) < 3 or parts[2] != zone:
                        continue
                    coords = parts[1]
                    split = max(coords.rfind("+"), coords.rfind("-"))
                    return {"name": zone.rsplit("/", 1)[-1].replace("_", " "),
                            "latitude": round(_degrees(coords[:split], 2), 2),
                            "longitude": round(_degrees(coords[split:], 3), 2),
                            "countries": parts[0].split(",")}
        except (OSError, ValueError, IndexError):
            continue
    return None


# Countries where clocks usually show 12 hours, with am and pm (for the clock's "auto").
TWELVE_HOUR = {"US", "CA", "AU", "NZ", "IN", "PH", "PK", "BD", "EG", "SA", "MY", "CO", "HN", "SV", "NI", "JO"}


def guess_units(zone):
    info = zone_info(zone)
    return "fahrenheit" if info and set(info["countries"]) & FAHRENHEIT else "celsius"


def guess_12_hour(zone):
    info = zone_info(zone)
    return bool(info and set(info["countries"]) & TWELVE_HOUR)


class Places:
    """The towns kept on the device, to find the owner's by name."""

    def __init__(self, path=PLACES_FILE):
        self.path = path
        self._rows = None
        self.countries = {}

    def _load(self):
        if self._rows is not None:
            return self._rows
        rows, countries = [], {}
        try:
            with gzip.open(self.path, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    if line.startswith("@"):
                        if len(parts) == 2:
                            countries[parts[0][1:]] = parts[1]
                        continue
                    if len(parts) != 7:
                        continue
                    try:
                        lat, lon, people = float(parts[3]), float(parts[4]), int(parts[5])
                    except ValueError:
                        continue
                    rows.append((parts[0], parts[1], parts[2], lat, lon, people, parts[6], fold(parts[0])))
        except (OSError, EOFError) as error:
            log.warning("the list of towns could not be read (%s): %s", self.path, error)
        self._rows, self.countries = rows, countries
        return rows

    def search(self, query, zone="", limit=8):
        """Towns whose name starts with what was typed, best first: the exact name, then those in the
        device's time zone, then in its country, then the largest. "Perth, Scotland" narrows by the region
        or the country."""
        pieces = [fold(p) for p in str(query or "")[:80].split(",")]
        name, narrow = pieces[0], [p for p in pieces[1:] if p]
        if len(name) < 2:
            return []
        rows = self._load()
        info = zone_info(zone)
        countries = set(info["countries"]) if info else set()
        found = []
        for row in rows:
            if not row[7].startswith(name):
                continue
            if narrow:
                where = " ".join((fold(row[1]), fold(self.countries.get(row[2], "")), row[2].casefold()))
                if not all(n in where for n in narrow):
                    continue
            found.append(((row[7] == name, bool(zone) and row[6] == zone, row[2] in countries, row[5]), row))
        found.sort(key=lambda item: item[0], reverse=True)
        return [self._place(row) for _, row in found[:limit]]

    def _place(self, row):
        country = self.countries.get(row[2], row[2])
        return {"name": row[0], "region": row[1], "country": country, "latitude": row[3], "longitude": row[4],
                "label": ", ".join(p for p in (row[0], row[1], country) if p)}


# ---------------------------------------------------------------- weather in words

# The device's own names for the weather (MET Norway's symbol names, which say everything a day's summary needs);
# each service's conditions are turned into them.
BASE = {"clearsky": ("clear", "clear", 0.0), "fair": ("mostly clear", "fair", 0.3),
        "partlycloudy": ("partly cloudy", "partly", 0.6), "cloudy": ("cloudy", "cloud", 1.0),
        "overcast": ("overcast", "cloud", 1.1), "haze": ("hazy", "fog", 1.2), "mist": ("misty", "fog", 1.3),
        "smoke": ("smoky", "fog", 1.4), "dust": ("dusty", "fog", 1.4), "fog": ("foggy", "fog", 1.5)}
SUNNY = {"clearsky": "sunny", "fair": "mostly sunny"}
BY_TIME = ("clearsky", "fair", "partlycloudy")      # these have a day and a night picture


def symbol(code):
    """The weather in words, the picture that goes with it, and how much it matters in a day's summary:
    "lightrainshowers_day" -> ("light rain showers", "rain", 2.9)."""
    base, _, when = str(code or "").partition("_")
    if base in BASE:
        words, icon, weight = BASE[base]
        if when == "night" and icon in ("clear", "fair", "partly"):
            icon += "-night"
        elif base in SUNNY:
            words = SUNNY[base]
        return words, icon, weight
    rest, strength = base, ""
    for s in ("light", "heavy"):
        if rest.startswith(s):
            strength, rest = s, rest[len(s):]
    thunder = rest.endswith("andthunder")
    rest = rest[:-len("andthunder")] if thunder else rest
    showers = rest.endswith("showers")
    rest = rest[:-len("showers")] if showers else rest
    if rest not in ("rain", "sleet", "snow"):
        return "", "cloud", 1.0
    words = " ".join(w for w in (strength, rest, "showers" if showers else "") if w) + (" and thunder" if thunder else "")
    weight = {"rain": 3.0, "sleet": 3.5, "snow": 4.0}[rest] + {"light": -0.7, "heavy": 1.0, "": 0.0}[strength] \
        - (0.4 if showers else 0.0) + (3.0 if thunder else 0.0)
    return words, "thunder" if thunder else "snow" if rest in ("snow", "sleet") else "rain", weight


def _timed(code, day):
    return "%s_%s" % (code, "day" if day else "night") if code in BY_TIME else code


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# OpenWeather's condition ids (openweathermap.org/weather-conditions).
OPENWEATHER = {
    200: "lightrainandthunder", 201: "rainandthunder", 202: "heavyrainandthunder", 210: "lightrainandthunder",
    211: "rainandthunder", 212: "heavyrainandthunder", 221: "rainandthunder", 230: "lightrainandthunder",
    231: "lightrainandthunder", 232: "rainandthunder",
    300: "lightrain", 301: "lightrain", 302: "rain", 310: "lightrain", 311: "lightrain", 312: "rain",
    313: "lightrainshowers", 314: "rainshowers", 321: "lightrainshowers",
    500: "lightrain", 501: "rain", 502: "heavyrain", 503: "heavyrain", 504: "heavyrain", 511: "sleet",
    520: "lightrainshowers", 521: "rainshowers", 522: "heavyrainshowers", 531: "rainshowers",
    600: "lightsnow", 601: "snow", 602: "heavysnow", 611: "sleet", 612: "lightsleetshowers", 613: "sleetshowers",
    615: "lightsleet", 616: "sleet", 620: "lightsnowshowers", 621: "snowshowers", 622: "heavysnowshowers",
    701: "mist", 711: "smoke", 721: "haze", 731: "dust", 741: "fog", 751: "dust", 761: "dust", 762: "dust",
    771: "cloudy", 781: "heavyrainandthunder",
    800: "clearsky", 801: "fair", 802: "partlycloudy", 803: "cloudy", 804: "overcast",
}

# WeatherAPI.com's condition codes (weatherapi.com/docs/weather_conditions.json).
WEATHERAPI = {
    1000: "clearsky", 1003: "partlycloudy", 1006: "cloudy", 1009: "overcast", 1012: "haze", 1015: "dust",
    1018: "dust", 1021: "dust", 1024: "dust", 1027: "dust", 1030: "mist", 1033: "smoke", 1036: "smoke",
    1039: "haze", 1042: "haze", 1045: "dust", 1048: "dust",
    1063: "lightrainshowers", 1066: "lightsnowshowers", 1069: "lightsleetshowers", 1072: "lightsleet",
    1087: "rainandthunder", 1114: "snow", 1117: "heavysnow", 1135: "fog", 1147: "fog",
    1150: "lightrain", 1153: "lightrain", 1168: "lightsleet", 1171: "sleet", 1180: "lightrain", 1183: "lightrain",
    1186: "rain", 1189: "rain", 1192: "heavyrain", 1195: "heavyrain", 1198: "lightsleet", 1201: "sleet",
    1204: "lightsleet", 1207: "sleet", 1210: "lightsnow", 1213: "lightsnow", 1216: "snow", 1219: "snow",
    1222: "heavysnow", 1225: "heavysnow", 1237: "sleet", 1240: "lightrainshowers", 1243: "rainshowers",
    1246: "heavyrainshowers", 1249: "lightsleetshowers", 1252: "sleetshowers", 1255: "lightsnowshowers",
    1258: "snowshowers", 1261: "lightsleetshowers", 1264: "sleetshowers", 1273: "lightrainandthunder",
    1276: "heavyrainandthunder", 1279: "lightsnowandthunder", 1282: "heavysnowandthunder",
}


def openweather_symbol(condition, icon=""):
    """An OpenWeather condition id (and its icon, "10d" or "10n", for day or night) in the device's names."""
    try:
        cid = int(condition)
    except (TypeError, ValueError):
        cid = 0
    code = OPENWEATHER.get(cid) or {2: "rainandthunder", 3: "lightrain", 5: "rain", 6: "snow", 7: "fog"}.get(cid // 100, "cloudy")
    return _timed(code, not str(icon or "").endswith("n"))


def weatherapi_symbol(condition, is_day=1):
    try:
        code = WEATHERAPI.get(int(condition), "cloudy")
    except (TypeError, ValueError):
        code = "cloudy"
    return _timed(code, bool(is_day))


def _in_order(hours):
    """Hours by time; an observation (now) after a forecast hour of the same moment, so it is the one used."""
    return sorted(hours, key=lambda h: (h["t"], 1 if h.get("now") else 0))


def parse_openweather(current, forecast, now=None):
    """OpenWeather's current weather and its five-day, three-hourly forecast as the device's hours: {"t": seconds,
    "temp": °C, "sym": the weather (the device's names), "wet": millimetres an hour, "span": hours it holds for}.
    A forecast entry's rain is for the three hours before it, so each entry takes the next one's."""
    now = now or time.time()
    out = []
    if current:
        w = (current.get("weather") or [{}])[0]
        wet = sum(_number((current.get(k) or {}).get("1h")) or 0.0 for k in ("rain", "snow"))
        out.append({"t": int(current["dt"]), "temp": _number((current.get("main") or {}).get("temp")),
                    "sym": openweather_symbol(w.get("id"), w.get("icon")), "wet": round(wet, 2), "span": 1, "now": True})
    entries = (forecast or {}).get("list") or []
    for i, e in enumerate(entries):
        t = int(e["dt"])
        if t > now + AHEAD:
            break
        w = (e.get("weather") or [{}])[0]
        after = entries[i + 1] if i + 1 < len(entries) else {}
        wet = sum(_number((after.get(k) or {}).get("3h")) or 0.0 for k in ("rain", "snow"))
        out.append({"t": t, "temp": _number((e.get("main") or {}).get("temp")),
                    "sym": openweather_symbol(w.get("id"), w.get("icon")), "wet": round(wet / 3, 2), "span": 3})
    return _in_order(out)


def parse_weatherapi(doc, now=None):
    """WeatherAPI.com's forecast (with its current weather) as the device's hours (see parse_openweather)."""
    now = now or time.time()
    out = []
    current = (doc or {}).get("current") or {}
    if current:
        out.append({"t": int(current.get("last_updated_epoch") or now), "temp": _number(current.get("temp_c")),
                    "sym": weatherapi_symbol((current.get("condition") or {}).get("code"), current.get("is_day", 1)),
                    "wet": round(_number(current.get("precip_mm")) or 0.0, 2), "span": 1, "now": True})
    for day in ((doc or {}).get("forecast") or {}).get("forecastday") or []:
        for h in day.get("hour") or []:
            t = int(h["time_epoch"])
            if t > now + AHEAD:
                continue
            out.append({"t": t, "temp": _number(h.get("temp_c")),
                        "sym": weatherapi_symbol((h.get("condition") or {}).get("code"), h.get("is_day", 1)),
                        "wet": round(_number(h.get("precip_mm")) or 0.0, 2), "span": 1})
    return _in_order(out)


class KeyRefused(WeatherError):
    """The service refused the owner's key (wrong, not working yet, or turned off)."""


def _refused(provider, status, body):
    """Why a service said no, in the device's own words (never the service's text, which may repeat the request)."""
    name = PROVIDERS[provider]["name"]
    code = None
    try:
        code = (json.loads(body or b"{}").get("error") or {}).get("code")
    except (ValueError, AttributeError):
        pass
    if status == 401 or code in (1002, 2006):
        later = " (a new OpenWeather key can take a couple of hours to start working)" if provider == "openweather" else ""
        return KeyRefused("%s refused the key%s" % (name, later))
    if code == 2008:
        return KeyRefused("%s has turned this key off" % name)
    if code == 2007:
        return WeatherError("this month's calls at %s are used up" % name)
    if status == 429:
        return WeatherError("%s asked the device to slow down" % name)
    return WeatherError("%s answered %s" % (name, status))


HEADERS = {"Accept": "application/json"}


async def _ask(fetch, provider, url):
    status, _, body = await fetch(url, dict(HEADERS, **{"User-Agent": "CompanionDevice/%s" % _version()}))
    if status != 200:
        raise _refused(provider, status, body)
    return json.loads(body)


async def fetch_openweather(fetch, key, place, now):
    query = "?lat=%.2f&lon=%.2f&units=metric&appid=%s" % (place["latitude"], place["longitude"], urllib.parse.quote(key, safe=""))
    base = "https://api.openweathermap.org/data/2.5/"
    current = await _ask(fetch, "openweather", base + "weather" + query)
    forecast = await _ask(fetch, "openweather", base + "forecast" + query)
    return parse_openweather(current, forecast, now)


async def fetch_weatherapi(fetch, key, place, now):
    url = "https://api.weatherapi.com/v1/forecast.json?key=%s&q=%.2f,%.2f&days=2&aqi=no&alerts=no" % (
        urllib.parse.quote(key, safe=""), place["latitude"], place["longitude"])
    return parse_weatherapi(await _ask(fetch, "weatherapi", url), now)


FETCH = {"openweather": fetch_openweather, "weatherapi": fetch_weatherapi}


def clock_words(t):
    """A time as people say it: "7 am", "3:30 pm"."""
    local = datetime.datetime.fromtimestamp(t)
    hour = (local.hour + 11) % 12 + 1
    return "%d%s %s" % (hour, ":%02d" % local.minute if local.minute else "", "am" if local.hour < 12 else "pm")


def _degree(celsius, units):
    return round(celsius * 9 / 5 + 32) if units == "fahrenheit" else round(celsius)


def _day(hours, units):
    """A stretch of hours summed up: {"high", "low", "words", "icon", "wet_from"} or None."""
    temps = [h["temp"] for h in hours if h["temp"] is not None]
    marks = [h for h in hours if h["sym"]]
    if not temps or not marks:
        return None
    counts = {}
    for h in marks:
        counts[h["sym"].partition("_")[0]] = counts.get(h["sym"].partition("_")[0], 0) + h["span"]
    total = sum(counts.values())
    # What matters most among what lasts a while (two hours, or a sixth of the time); else the commonest.
    lasting = [s for s, n in counts.items() if n >= max(2, total / 6)]
    main = max(lasting, key=lambda s: symbol(s)[2]) if lasting else max(counts, key=counts.get)
    shown = next(h["sym"] for h in marks if h["sym"].partition("_")[0] == main)
    words, icon, _ = symbol(shown)
    wet = [h for h in hours if (h["wet"] or 0) >= WET]
    wet_from = None
    if wet and len(wet) < 0.8 * len(hours) and symbol(main)[2] < 2:
        wet_from = clock_words(wet[0]["t"])
    return {"high": _degree(max(temps), units), "low": _degree(min(temps), units), "words": words, "icon": icon,
            "wet_from": wet_from}


def summary(forecast, now=None, units="celsius"):
    """The weather now, for the rest of today and tomorrow, from a forecast kept by Weather: a dict, or None."""
    now = now or time.time()
    hours = (forecast or {}).get("hours") or []
    started = [h for h in hours if h["t"] <= now]
    current = started[-1] if started else (hours[0] if hours else None)
    if current is None or current["temp"] is None or abs(current["t"] - now) > 3 * 3600:
        return None
    local = datetime.datetime.fromtimestamp(now)
    midnight = datetime.datetime.combine(local.date() + datetime.timedelta(days=1), datetime.time()).timestamp()
    rest = [h for h in hours if current["t"] <= h["t"] < midnight]
    tomorrow = [h for h in hours if midnight + 6 * 3600 <= h["t"] < midnight + 22 * 3600]
    words, icon, _ = symbol(current["sym"])
    return {"place": forecast.get("place", ""), "units": units,
            "now": {"temp": _degree(current["temp"], units), "words": words, "icon": icon},
            "today": _day(rest, units) if len(rest) >= 2 else None,
            "tomorrow": _day(tomorrow, units)}


def _span(day):
    return "%d to %d" % (day["low"], day["high"]) if day["low"] != day["high"] else "around %d" % day["high"]


def describe(s):
    """The weather for the character's instructions, in a sentence."""
    if not s:
        return ""
    parts = ["%d and %s now" % (s["now"]["temp"], s["now"]["words"] or "dry")]
    t = s.get("today")
    if t:
        parts.append("the rest of today %s, %s%s" % (t["words"], _span(t),
                                                    ", rain likely from about " + t["wet_from"] if t["wet_from"] else ""))
    if s.get("tomorrow"):
        parts.append("tomorrow %s, %s" % (s["tomorrow"]["words"], _span(s["tomorrow"])))
    unit = "Fahrenheit" if s["units"] == "fahrenheit" else "Celsius"
    return "The weather in %s (degrees %s): %s." % (s["place"] or "their town", unit, "; ".join(parts))


def _say_number(n):
    return "minus %d" % -n if n < 0 else "%d" % n


def spoken(s):
    """The weather as a short line to say aloud (an alarm, when the language model cannot be reached)."""
    if not s:
        return ""
    t = s.get("today")
    line = "It's %s degrees and %s" % (_say_number(s["now"]["temp"]), s["now"]["words"] or "dry")
    if t:
        line += ", with a high of %s" % _say_number(t["high"])
        if t["wet_from"]:
            line += " and rain from about %s" % t["wet_from"]
        elif t["words"] != s["now"]["words"]:
            line += ", %s later" % t["words"]
    return line + "."


def _version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


async def _http_get(url, headers):
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT), trust_env=True) as session:
        async with session.get(url, headers=headers) as r:
            body = await r.content.read(LARGEST + 1)
            if len(body) > LARGEST:
                raise WeatherError("the forecast is larger than expected")
            return r.status, dict(r.headers), body


class Weather:
    """The forecast for the owner's place, from the service they chose, asked for when due and kept in
    DATA/weather.json. `secret(name)` gives a key (store.Store.secret)."""

    def __init__(self, data_dir, fetch=None, places=None, secret=None):
        self.path = os.path.join(data_dir, "weather.json")
        self.fetch = fetch or _http_get
        self.places = places or Places()
        self.secret = secret or (lambda name: "")
        self._lock = asyncio.Lock()
        self.asks = 0                    # how many times a service was asked (for tests)

    @staticmethod
    def provider(settings):
        chosen = (settings.get("weather") or {}).get("provider") or ""
        return chosen if chosen in PROVIDERS else ""

    def on(self, settings):
        """A service is chosen and its key saved."""
        provider = self.provider(settings)
        return bool(provider and self.secret(provider))

    def place(self, settings, zone):
        """The place the weather is for: the owner's choice, else the time zone's city; or None."""
        chosen = (settings.get("weather") or {}).get("place") or {}
        lat, lon = chosen.get("latitude"), chosen.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool):
            name = str(chosen.get("name") or "your town")
            return {"name": name, "label": str(chosen.get("label") or name), "latitude": float(lat),
                    "longitude": float(lon), "from": "chosen"}
        info = zone_info(zone)
        if info:
            return {"name": info["name"], "label": "%s (the time zone's city)" % info["name"],
                    "latitude": info["latitude"], "longitude": info["longitude"], "from": "time zone"}
        return None

    @staticmethod
    def units(settings, zone):
        units = (settings.get("weather") or {}).get("units", "auto")
        return units if units in ("celsius", "fahrenheit") else guess_units(zone)

    def _kept_for(self, settings, zone):
        """What the kept forecast must be for: the service, its key (a short fingerprint, never the key) and the
        place; None when there is no weather."""
        provider = self.provider(settings)
        key = self.secret(provider) if provider else ""
        place = self.place(settings, zone) if key else None
        if place is None:
            return None
        print_ = hashlib.sha256(("%s:%s" % (provider, key)).encode()).hexdigest()[:12]
        return "%s|%s|%.2f,%.2f" % (provider, print_, place["latitude"], place["longitude"])

    def _saved(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def forget(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def cached(self, settings, zone, now=None):
        """The forecast kept, while it is fresh enough to use and for what is chosen now; asks nothing."""
        now = now or time.time()
        kept_for = self._kept_for(settings, zone)
        data = self._saved()
        if not kept_for or data.get("key") != kept_for or not data.get("hours") or now - data.get("fetched", 0) > KEEP:
            return None
        return data

    def due(self, settings, zone, now=None):
        """Whether it is time to ask the service again."""
        now = now or time.time()
        kept_for = self._kept_for(settings, zone)
        if kept_for is None:
            return False
        data = self._saved()
        if data.get("failed_key") == kept_for and now < data.get("failed", 0) + (
                KEY_RETRY if data.get("key_refused") else RETRY):
            return False
        return data.get("key") != kept_for or now >= data.get("fetched", 0) + REFRESH

    def problem(self, settings, zone):
        """Why the last ask for what is chosen now failed (with no forecast since), in words; or ""."""
        data = self._saved()
        kept_for = self._kept_for(settings, zone)
        if kept_for and data.get("failed_key") == kept_for and data.get("failed", 0) > (
                data.get("fetched", 0) if data.get("key") == kept_for else 0):
            return str(data.get("problem") or "")
        return ""

    async def refresh(self, settings, zone, now=None):
        """Ask for the forecast if it is due. Returns the forecast to use now, or None (no service, key or place,
        or none fetched for six hours)."""
        if self._kept_for(settings, zone) is None:
            return None
        async with self._lock:
            now = now or time.time()
            if not self.due(settings, zone, now):
                return self.cached(settings, zone, now)
            provider = self.provider(settings)
            place = self.place(settings, zone)
            kept_for = self._kept_for(settings, zone)
            data = self._saved()
            self.asks += 1
            name = PROVIDERS[provider]["name"]
            try:
                hours = await FETCH[provider](self.fetch, self.secret(provider), place, now)
                if not any(h["temp"] is not None for h in hours):
                    raise WeatherError("%s sent a forecast with no temperatures" % name)
                data = {"key": kept_for, "place": place["name"], "provider": provider, "fetched": now, "hours": hours}
            except WeatherError as error:            # the device's own words: no address, no key
                log.warning("the weather could not be fetched: %s", error)
                data.update(failed=now, failed_key=kept_for, problem=str(error), key_refused=isinstance(error, KeyRefused))
            except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                log.warning("the weather could not be fetched: %s sent something unexpected", name)
                data.update(failed=now, failed_key=kept_for, problem="%s sent something unexpected" % name,
                            key_refused=False)
            except Exception as error:               # the network; its message can hold the address, and the key
                log.warning("the weather could not be fetched: %s (%s)", name, type(error).__name__)
                data.update(failed=now, failed_key=kept_for, key_refused=False,
                            problem="%s could not be reached: is the device on the internet?" % name)
            write_atomic(self.path, json.dumps(data))
            return self.cached(settings, zone, now)

    def now(self, settings, zone, now=None):
        """The weather summed up from the kept forecast ({"now", "today", "tomorrow", ...}), or None."""
        forecast = self.cached(settings, zone, now)
        return summary(forecast, now, self.units(settings, zone)) if forecast else None
