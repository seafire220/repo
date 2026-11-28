"""The weather where the owner lives: for the morning hello, wake-up alarms, the character's answers and the
clock on the screen (docs/28-user-guide.md, "Your day").

Forecasts come from MET Norway's Locationforecast (api.met.no), which may be used by products that are sold,
with credit (CC BY 4.0; the credit is on the settings page and in the notices). Open-Meteo's free service
was not used: its terms keep it for non-commercial use (docs/decision-log.md, D-074).

Only the place's latitude and longitude go to MET Norway, rounded to two decimals (about a kilometre), with
the software's name and where its makers can be found, as MET Norway's terms ask of every program. A forecast
is kept, and asked for again only once the time MET Norway gives has passed (and never within half an hour),
with If-Modified-Since, as its terms ask. The last one is used for six hours if the internet is down.

The place is chosen on the settings page from a list kept on the device (data/places.tsv.gz: every town of
15,000 people or more, from GeoNames, CC BY 4.0), so what the owner types to find their town never leaves the
device. Until they choose, it is the main city of the device's time zone (from the system's time zone tables).
"""
import asyncio
import datetime
import email.utils
import gzip
import json
import logging
import os
import time
import unicodedata

from .store import write_atomic

log = logging.getLogger("companion.weather")

PLACES_FILE = os.path.join(os.path.dirname(__file__), "data", "places.tsv.gz")
ZONE_TABLES = ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab")
FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
# Who is asking, as MET Norway's terms require: the software's name, and where its makers can be found. A seller
# puts their own website in notices/product.json ("website"), which is used instead (docs/33-finishing.md).
CONTACT = "https://github.com/seafire220/repo"
CREDIT = "Weather forecasts from MET Norway (api.met.no), CC BY 4.0. Towns from GeoNames (geonames.org), CC BY 4.0."
REFRESH = 30 * 60        # never asked again sooner than this for the same place
RETRY = 10 * 60          # after a failed ask, wait this long
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


# ---------------------------------------------------------------- MET Norway's weather symbols

BASE = {"clearsky": ("clear", "clear", 0.0), "fair": ("mostly clear", "fair", 0.3),
        "partlycloudy": ("partly cloudy", "partly", 0.6), "cloudy": ("cloudy", "cloud", 1.0),
        "fog": ("foggy", "fog", 1.5)}
SUNNY = {"clearsky": "sunny", "fair": "mostly sunny"}


def symbol(code):
    """A MET Norway weather symbol in words, the picture that goes with it, and how much it matters in a day's
    summary: "lightrainshowers_day" -> ("light rain showers", "rain", 2.9)."""
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
            if rest.startswith("ss"):              # MET Norway's own spelling: "lightssleet", "lightssnow"
                rest = rest[1:]
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


def parse(doc, now=None):
    """A Locationforecast (compact) document as a list of hours: {"t": seconds, "temp": °C, "sym": the symbol for
    the next hour (or six), "wet": millimetres in the next hour (or a sixth of the next six), "span": hours}."""
    now = now or time.time()
    out = []
    for entry in doc["properties"]["timeseries"]:
        t = datetime.datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).timestamp()
        if t > now + AHEAD:
            break
        data = entry.get("data") or {}
        temp = ((data.get("instant") or {}).get("details") or {}).get("air_temperature")
        nxt, span = data.get("next_1_hours"), 1
        if not nxt:
            nxt, span = data.get("next_6_hours"), 6
        nxt = nxt or {}
        sym = (nxt.get("summary") or {}).get("symbol_code")
        wet = (nxt.get("details") or {}).get("precipitation_amount")
        out.append({"t": round(t), "temp": temp if isinstance(temp, (int, float)) else None, "sym": sym,
                    "wet": round(wet / span, 2) if isinstance(wet, (int, float)) else None, "span": span})
    return out


def _expires(headers, now):
    """When MET Norway says to ask again (within half an hour to six hours from now)."""
    try:
        when = email.utils.parsedate_to_datetime(headers.get("Expires") or headers.get("expires") or "").timestamp()
    except (TypeError, ValueError):
        when = 0.0
    return min(max(when, now + REFRESH), now + KEEP)


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


def user_agent():
    """How the device introduces itself to MET Norway: the software's name and version, and where its makers can
    be found (the maker's website from notices/product.json, once the seller has filled it in)."""
    from . import notices
    website = str(notices.product().get("website") or "").strip()
    return "CompanionDevice/%s %s" % (_version(), website if website and len(website) < 200 else CONTACT)


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
    """The forecast for the owner's place, asked for when due and kept in DATA/weather.json."""

    def __init__(self, data_dir, fetch=None, places=None):
        self.path = os.path.join(data_dir, "weather.json")
        self.fetch = fetch or _http_get
        self.places = places or Places()
        self._lock = asyncio.Lock()
        self.asks = 0                    # how many times MET Norway was asked (for tests and the support report)

    @staticmethod
    def on(settings):
        return bool((settings.get("weather") or {}).get("on"))

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

    def cached(self, place, now=None):
        """The forecast kept for this place, while it is fresh enough to use; asks nothing."""
        now = now or time.time()
        data = self._saved()
        if not place or data.get("key") != _key(place) or not data.get("hours") or now - data.get("fetched", 0) > KEEP:
            return None
        return data

    def due(self, settings, zone, now=None):
        """Whether it is time to ask MET Norway again (the weather on, a place known, the last forecast old)."""
        now = now or time.time()
        place = self.place(settings, zone) if self.on(settings) else None
        if place is None:
            return False
        data = self._saved()
        if data.get("key") != _key(place):
            return now >= data.get("failed", 0) + RETRY or data.get("failed_key") != _key(place)
        if now < data.get("failed", 0) + RETRY:
            return False
        return now >= max(data.get("expires", 0), data.get("fetched", 0) + REFRESH)

    async def refresh(self, settings, zone, now=None):
        """Ask for the forecast if it is due. Returns the forecast to use now, or None (off, no place, or none
        fetched for six hours)."""
        if not self.on(settings):
            return None
        place = self.place(settings, zone)
        if place is None:
            return None
        async with self._lock:
            now = now or time.time()
            if not self.due(settings, zone, now):
                return self.cached(place, now)
            data = self._saved()
            same = data.get("key") == _key(place)
            url = "%s?lat=%.2f&lon=%.2f" % (FORECAST_URL, place["latitude"], place["longitude"])
            headers = {"User-Agent": user_agent(), "Accept": "application/json"}
            if same and data.get("modified"):
                headers["If-Modified-Since"] = data["modified"]
            self.asks += 1
            try:
                status, got, body = await self.fetch(url, headers)
                if status == 304 and same and data.get("hours"):
                    data.update(fetched=now, expires=_expires(got, now))
                elif status == 200:
                    hours = parse(json.loads(body), now)
                    if not hours:
                        raise WeatherError("the forecast had no hours in it")
                    data = {"key": _key(place), "place": place["name"], "fetched": now, "expires": _expires(got, now),
                            "modified": got.get("Last-Modified") or got.get("last-modified") or "", "hours": hours}
                else:
                    raise WeatherError("MET Norway answered %s" % status)
            except (WeatherError, ValueError, KeyError, TypeError, OSError, asyncio.TimeoutError) as error:
                log.warning("the weather could not be fetched: %s", error)
                data.update(failed=now, failed_key=_key(place))
            except Exception as error:                  # aiohttp's own errors, among others
                log.warning("the weather could not be fetched: %s: %s", type(error).__name__, error)
                data.update(failed=now, failed_key=_key(place))
            write_atomic(self.path, json.dumps(data))
            return self.cached(place, now)

    def now(self, settings, zone, now=None):
        """The weather summed up from the kept forecast ({"now", "today", "tomorrow", ...}), or None."""
        if not self.on(settings):
            return None
        place = self.place(settings, zone)
        forecast = self.cached(place, now)
        return summary(forecast, now, self.units(settings, zone)) if forecast else None


def _key(place):
    return "%.2f,%.2f" % (place["latitude"], place["longitude"])
