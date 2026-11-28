"""Special days (docs/28-user-guide.md, "Your day"): the owner's birthday, the day each character arrived (every
year, and its hundredth day), days the owner adds, and some holidays.

On a special day the character is told about it in its instructions (it wishes them well the first time they
talk that day, or in the morning hello), and the screen can show a festive background (backgrounds.FESTIVE)
all day.
"""
import datetime

# Holidays: (name, festive background). Fixed dates, then Easter, worked out for each year.
FIXED = {(1, 1): ("New Year's Day", "fireworks"), (2, 14): ("Valentine's Day", "hearts"),
         (10, 31): ("Halloween", "spooky"), (12, 24): ("Christmas Eve", "snowy"), (12, 25): ("Christmas Day", "snowy"),
         (12, 31): ("New Year's Eve", "fireworks")}
EASTER = ("Easter Sunday", "spring")


def easter(year):
    """Easter Sunday in the Gregorian calendar (the anonymous algorithm, as Meeus gives it)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    return datetime.date(year, month, (h + l - 7 * m + 33 * month + 19) % 32)


def holiday(day):
    """(name, background) of a holiday on `day`, or None."""
    if day == easter(day.year):
        return EASTER
    return FIXED.get((day.month, day.day))


def _yearly(text, day):
    """Whether a yearly date ("MM-DD", or the month and day of "YYYY-MM-DD") falls on `day`; 29 February
    falls on 28 February in other years."""
    month, date = int(text[-5:-3]), int(text[-2:])
    if (month, date) == (2, 29) and not _leap(day.year):
        return (day.month, day.day) == (2, 28)
    return (day.month, day.day) == (month, date)


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _ordinal(n):
    words = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
             9: "ninth", 10: "tenth"}
    if n in words:
        return words[n]
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


def on(day, settings, arrived=None):
    """What is special about `day` (a date): a list of {"kind", "what", "line", "background"}, the most
    personal first. `arrived`: the date the character on the screen arrived, if known."""
    opts = settings.get("special_days") or {}
    if not opts.get("on", True):
        return []
    out = []
    owner = settings.get("owner") or {}
    birthday = owner.get("birthday") or ""
    if birthday and _yearly(birthday, day):
        age = ""
        if len(birthday) == 10 and day.year > int(birthday[:4]):
            age = " (they are %d today)" % (day.year - int(birthday[:4]))
        out.append({"kind": "birthday", "what": "their birthday", "background": "party",
                    "line": "Today is their birthday%s. Wish them a happy birthday the first time you talk today, "
                            "warmly and in your own way, perhaps with a cheerful gesture." % age})
    if arrived:
        days = (day - arrived).days
        if days > 0 and _yearly(arrived.isoformat(), day) and day.year > arrived.year:
            years = day.year - arrived.year
            out.append({"kind": "arrival", "what": "the day you arrived", "background": "party",
                        "line": "Today it is %s since you came to live with them (you arrived on %d %s). "
                                "You may mention it, fondly, once today."
                                % ("a year" if years == 1 else "%d years" % years, arrived.day,
                                   arrived.strftime("%B %Y"))})
        elif days + 1 in (100, 1000):
            out.append({"kind": "arrival", "what": "your %s day together" % _ordinal(days + 1), "background": "party",
                        "line": "Today is your %s day together (you arrived on %d %s). You may mention it, fondly, "
                                "once today." % (_ordinal(days + 1), arrived.day, arrived.strftime("%B %Y"))})
    for entry in opts.get("dates") or []:
        text, what = str(entry.get("date") or ""), " ".join(str(entry.get("what") or "").split())
        if not what or len(text) not in (5, 10):
            continue
        once = len(text) == 10 and entry.get("once") is True
        try:
            if (once and text == day.isoformat()) or (not once and _yearly(text, day)):
                years = day.year - int(text[:4]) if len(text) == 10 and not once else 0
                out.append({"kind": "theirs", "what": what, "background": "party",
                            "line": "Today is a day they asked you to remember: %s%s. Mention it kindly the first "
                                    "time you talk today." % (what, " (%d years since %s)" % (years, text[:4])
                                                              if years > 0 else "")})
        except ValueError:
            continue
    if opts.get("holidays", True):
        found = holiday(day)
        if found:
            out.append({"kind": "holiday", "what": found[0], "background": found[1],
                        "line": "Today is %s. You may wish them a happy %s, once, if it fits." % (found[0], found[0])})
    return out


def festive(specials, settings):
    """The festive background for the day, or None (none special, or the owner turned them off)."""
    if not (settings.get("special_days") or {}).get("festive", True):
        return None
    return next((s["background"] for s in specials if s.get("background")), None)
