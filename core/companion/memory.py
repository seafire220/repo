"""What a character remembers, kept on the device and shown to the owner (docs/31-memory.md).

People remember talks as wholes (when they were, what they were about, how they went) and know lasting
things about each other. So does the character. Per character:

  history.jsonl   the recent turns word for word (the last 400), each marked with its conversation
  archive/        every turn ever said, a file a month: the settings page shows any conversation again
  sessions.jsonl  the conversations that have ended: when, why it ended, a title, a summary, how the
                  person seemed
  memories.jsonl  what it knows: each memory one short sentence with a category, how much it matters
                  (1 to 5), when it was learnt and last changed; a plan or event has its date, and some
                  are to be asked about once that date has passed
  diary.jsonl     an entry for each day they talked, written in the character's own voice
  notes.md        what the owner asks it always to remember (the model never changes this)
  state.json      the conversation going on now, and what has been done

**Conversations.** One ends after 20 quiet minutes, three minutes after a goodbye, when a new day begins
(4 am) between two of its turns, or when the owner starts a new one. The next word begins another.

**After a conversation** the model (in the background, when the device is not talking) writes its title
and summary and says which memories to add, change or retire: what was said wins over what was
remembered. A **long conversation** has its early part summed up as it goes, so the model always has
the gist of it and its latest turns word for word. **At night** (or the next day) the character writes
its diary entry for the day.

**For each reply** the model gets: the owner's notes; the memories that matter now (the most important,
the pinned, and those whose words match what was just said); plans to ask about; how long since they
last talked and what about (with the last words, if it was within two hours); yesterday's diary on the
day's first conversation; older conversations that match; and this conversation, all within the room
the model has (a small model on a home computer gets less). Nothing here is ever put in a character
package.
"""
import datetime
import json
import math
import os
import re
import threading
import time
import zlib

from .store import write_atomic

try:
    import numpy as np
except ImportError:
    np = None

KEEP_TURNS = 400              # word for word, in history.jsonl (all of them are in the archive)
NOTES_LIMIT = 4000
KEEP_SESSIONS = 5000           # conversations' summaries (about four years of daily talks)
SUMMARY_LIMIT = 700
TITLE_LIMIT = 60
MEMORY_LIMIT = 220            # characters in one memory
KEEP_MEMORIES = 600
DIARY_LIMIT = 1600

QUIET_GAP = 20 * 60           # this long without a word ends a conversation
GOODBYE_GRACE = 3 * 60        # after a goodbye, a word within this long carries the same conversation on
NEW_DAY_HOUR = 4              # a pause across 4 am of at least NEW_DAY_PAUSE begins a new conversation
NEW_DAY_PAUSE = 5 * 60
CARRY_OVER = 2 * 3600         # a conversation that ended this recently: its last words open the next
CARRY_TURNS = 3
KEEP_VERBATIM = 12            # this conversation's latest turns, word for word (the owner can change it)
ROLL_STEP = 12                # once this many more turns than that are not summed up, the early ones are
DIARY_HOUR = 21               # the day's diary is written from this hour, once it has gone quiet
RECALL_MEMORIES = 8
RECALL_SESSIONS = 3
# Matching by meaning (meaning.py), as well as by words: up to MEANING_MEMORIES memories whose meaning is close
# to what was said (at least MEANING_MIN alike, and within MEANING_SPREAD of the closest); conversations at
# least MEANING_MIN_SESSION alike count with their words. At most LIVE_VECTORS new vectors are made during a
# reply; the background work makes the rest.
MEANING_MEMORIES = 4
MEANING_MIN = 0.18                    # chosen on core/tests/test_meaning.py's remarks: 27 of 30 right, little noise
MEANING_SPREAD = 0.15
MEANING_MIN_SESSION = 0.25
LIVE_VECTORS = 64

SHOW_ORDER = ("notes", "memories", "older", "follow", "last", "now")

CATEGORIES = ("about them", "their people", "likes and dislikes", "work and study", "plans and events",
              "health and wellbeing", "our relationship", "about me", "other")

GOODBYE = re.compile(r"(?i)(\bgood ?bye\b|\bbye\b|\bgood ?night\b|\bnight night\b|\bsee (you|ya)\b|"
                     r"\btalk (to you )?(later|soon|tomorrow)\b|\bcatch you later\b|\bgot(ta| to) go\b|"
                     r"\bi('m| am) off\b|\bi (have|need) to go\b|\bsleep well\b|\bsweet dreams\b)")

# Words too common to say what something is about.
STOPWORDS = set("""a about after again all also am an and any are as at be because been before being but by can
could did do does doing don't for from had has have having he her here hers him his how i i'm if in into is it it's
its just know like me more most my no not now of off oh ok okay on once only or other our out over really said say
she so some such than that that's the their them then there these they this those through to too up us very was we
were what when where which while who why will with would yeah yes you you're your yours
remember remembered tell told talk talks talked talking think thought thing things something anything want wanted
going gonna got get make made let well sure maybe mean right good great nice much many lot little bit kind sort way
day time last first back still even ever never always sometimes person character conversation
hello hi hey hiya bye goodbye thanks thank please sorry guess morning mornings afternoon evening night tonight
today tomorrow yesterday weekend week love loves loved loving hate hates hated enjoy enjoys enjoyed favourite
favorite prefer prefers preferred""".split())     # greetings, times and feelings: how, not what about

WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|')*")


def _words(text):
    """The words that say what a text is about, each reduced to a rough stem ("baking", "baked" -> "bak")."""
    out = []
    for w in WORD.findall((text or "").lower().replace("’", "'")):
        w = w.strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        if w in STOPWORDS or len(w) < 3:
            continue
        out.append(stem(w))
    return out


def stem(w):
    """A very small stemmer, enough to match a word's plural and verb forms."""
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            if suffix == "es" and not w[:-2].endswith(("s", "x", "z", "ch", "sh")):
                continue                      # "bakes": only the "s" goes
            if suffix == "s" and w.endswith("ss"):
                break
            w = w[:-len(suffix)] + keep
            if suffix in ("ing", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]                    # "running" -> "run", but "falling" -> "fall"
            break
    if w.endswith("y") and len(w) > 3:
        w = w[:-1] + "i"
    elif w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def when(t, now=None):
    """A date in words: today, yesterday, 3 days ago, or the date."""
    now = now or time.time()
    day = datetime.date.fromtimestamp(t)
    days = (datetime.date.fromtimestamp(now) - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "%d days ago" % days
    return "%d %s" % (day.day, day.strftime("%B %Y"))


def part_of_day(t):
    h = datetime.datetime.fromtimestamp(t).hour
    return "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening" if 17 <= h < 22 else "night"


def said_when(t, now=None):
    """When something was said, as people put it: "25 minutes ago", "this morning (3 hours ago)",
    "last night", "yesterday evening", "on Monday evening (2 days ago)", "on 2 March (5 weeks ago)"."""
    now = now or time.time()
    gap = max(0, now - t)
    if gap < 3600:
        return _ago(gap)
    part = part_of_day(t)
    days = (talk_day(now) - talk_day(t)).days
    if days <= 0:
        return "%s (%s)" % ("earlier tonight" if part == "night" else "this " + part, _ago(gap))
    if days == 1:
        return "last night" if part == "night" else "yesterday " + part
    stamp = datetime.datetime.fromtimestamp(t)
    if days < 7:
        return "on %s %s (%d days ago)" % (stamp.strftime("%A"), part, days)
    if days < 60:
        weeks = round(days / 7)
        ago = "%d week%s ago" % (weeks, "" if weeks == 1 else "s")
    elif days < 365:
        ago = "%d months ago" % round(days / 30.4)
    else:
        ago = "over a year ago" if days < 730 else "%d years ago" % (days // 365)
    return "on %d %s (%s)" % (stamp.day, stamp.strftime("%B %Y"), ago)


def date_words(iso, now=None):
    """A memory's date for the model: "Saturday 21 November 2026, yesterday", "..., in 3 days"."""
    try:
        day = datetime.date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    days = (day - datetime.date.fromtimestamp(now or time.time())).days
    near = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days) or (
        "in %d days" % days if 0 < days < 14 else "%d days ago" % -days if -14 < days < 0 else "")
    return "%s %d %s%s" % (day.strftime("%A"), day.day, day.strftime("%B %Y"), ", " + near if near else "")


def talk_day(t):
    """The day a moment belongs to, the way people count it: until 4 am it is still the day before."""
    return (datetime.datetime.fromtimestamp(t) - datetime.timedelta(hours=NEW_DAY_HOUR)).date()


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    out.append(item)
    except OSError:
        pass
    return out


def _write_jsonl(path, items):
    write_atomic(path, "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))


class Index:
    """Finds the texts whose words best match a question; rarer words count for more."""

    def __init__(self, texts):
        self.docs = [set(_words(t)) for t in texts]
        self.counts = {}
        for doc in self.docs:
            for w in doc:
                self.counts[w] = self.counts.get(w, 0) + 1

    def scores(self, question):
        words = set(_words(question))
        n = len(self.docs)
        out = []
        for i, doc in enumerate(self.docs):
            shared = words & doc
            if shared:
                out.append((sum(math.log(1 + n / self.counts[w]) for w in shared), i))
        return out


class Memory:
    def __init__(self, folder, meaning=None):
        self.folder = folder
        self.meaning = meaning if np is not None else None       # a meaning.Meaning, or None: words alone
        self.vectors_path = os.path.join(folder, "vectors.npz")
        self._vectors = None
        os.makedirs(folder, exist_ok=True)
        self.history_path = os.path.join(folder, "history.jsonl")
        self.notes_path = os.path.join(folder, "notes.md")
        self.sessions_path = os.path.join(folder, "sessions.jsonl")
        self.memories_path = os.path.join(folder, "memories.jsonl")
        self.diary_path = os.path.join(folder, "diary.jsonl")
        self.state_path = os.path.join(folder, "state.json")
        self.archive_dir = os.path.join(folder, "archive")
        self._lock = threading.RLock()
        self._indexes = {}
        self._upgrade()

    # ----- files
    def _state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        write_atomic(self.state_path, json.dumps(state, ensure_ascii=False))

    def _history_lines(self):
        try:
            with open(self.history_path, encoding="utf-8") as f:
                return [l for l in f.read().splitlines() if l.strip()]
        except OSError:
            return []

    def history(self):
        """The recent turns, oldest first: {"n", "t", "s" (its conversation), "user", "reply"}."""
        return _jsonl(self.history_path)

    def last_turn_time(self):
        history = self.history()
        return history[-1].get("t", 0) if history else 0

    def _upgrade(self):
        """Memory kept by the first version (notes written by the model, a summary per stretch of
        conversation) becomes memories and ended conversations, once."""
        with self._lock:
            state = self._state()
            if state.get("version", 1) >= 2:
                return
            old_episodes = os.path.join(self.folder, "episodes.jsonl")
            sessions, memories = [], []
            if os.path.exists(old_episodes):
                last = 0
                for k, e in enumerate(_jsonl(old_episodes), 1):
                    if not isinstance(e.get("summary"), str) or not isinstance(e.get("id"), int):
                        continue
                    sessions.append({"id": k, "start": int(e.get("t", 0)), "end": int(e.get("t", 0)), "first": last + 1,
                                     "last": e["id"], "turns": max(0, e["id"] - last), "why": "earlier", "title": "",
                                     "summary": _clean(e["summary"], SUMMARY_LIMIT), "mood": "", "done": True})
                    last = e["id"]
                os.replace(old_episodes, old_episodes + ".v1")
            notes = self.notes()
            if notes.strip() and not state.get("owner_notes"):
                # The first version's notes were the model's: they become memories; the owner's own
                # notes start empty.
                now = int(time.time())
                for line in notes.splitlines():
                    text = _clean(re.sub(r"^\s*[-*•]\s*", "", line), MEMORY_LIMIT)
                    if len(text) > 2:
                        memories.append({"id": len(memories) + 1, "text": text, "category": "about them",
                                         "importance": 3, "created": now, "updated": now, "source": "notes"})
                write_atomic(self.notes_path + ".v1", notes)
                write_atomic(self.notes_path, "")
            if sessions:
                _write_jsonl(self.sessions_path, sessions)
            if memories:
                _write_jsonl(self.memories_path, memories)
            # Turns already summed up belong to the conversations above; those since, to an open one.
            history = self.history()
            done = state.get("summarised", 0)
            open_session = None
            if history:
                for turn in history:
                    owner = next((s["id"] for s in sessions if s["first"] <= turn.get("n", 0) <= s["last"]), None)
                    if owner is None and turn.get("n", 0) > done:
                        if open_session is None:
                            open_session = {"id": len(sessions) + 1, "start": turn.get("t", 0), "first": turn.get("n", 0),
                                            "turns": 0, "rolling": "", "rolled": turn.get("n", 1) - 1}
                        owner = open_session["id"]
                        open_session.update(end=turn.get("t", 0), last=turn.get("n", 0), turns=open_session["turns"] + 1)
                    turn["s"] = owner or 0
                _write_jsonl(self.history_path, history)
                self._archive(history)
            # Diary entries begin with the days after this (none for days long past).
            self._save_state({"version": 2, "open": open_session, "next": len(sessions) + 2,
                              "diary_from": (talk_day(time.time()) - datetime.timedelta(days=1)).isoformat()
                              if sessions else ""})

    def _archive(self, turns):
        """Add turns to the archive, a file a month."""
        os.makedirs(self.archive_dir, exist_ok=True)
        by_month = {}
        for turn in turns:
            month = datetime.date.fromtimestamp(turn.get("t", 0)).strftime("%Y-%m")
            by_month.setdefault(month, []).append(turn)
        for month, items in by_month.items():
            with open(os.path.join(self.archive_dir, month + ".jsonl"), "a", encoding="utf-8") as f:
                for turn in items:
                    f.write(json.dumps(turn, ensure_ascii=False) + "\n")

    # ----- the owner's notes
    def notes(self):
        try:
            with open(self.notes_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def set_notes(self, text):
        text = (text or "").replace("\r\n", "\n")[:NOTES_LIMIT]
        with self._lock:
            write_atomic(self.notes_path, text)
            state = self._state()
            state["owner_notes"] = True
            self._save_state(state)

    # ----- conversations
    def sessions(self):
        """The conversations that have ended, oldest first."""
        return _jsonl(self.sessions_path)

    def open_session(self):
        return self._state().get("open")

    def _ends(self, session, now):
        """Why the open conversation is over by `now` (None if a word now would carry it on)."""
        if not session:
            return None
        end = session.get("end", 0)
        if session.get("goodbye") and now - session["goodbye"] > GOODBYE_GRACE:
            return "goodbye"
        if now - end > QUIET_GAP:
            return "quiet"
        if now - end > NEW_DAY_PAUSE and talk_day(now) != talk_day(end):
            return "new day"
        return None

    def _close(self, state, why):
        """End the open conversation (it waits to be summed up)."""
        session = state.get("open")
        if not session:
            return None
        closed = {k: session.get(k) for k in ("id", "start", "end", "first", "last", "turns")}
        closed.update(why=why, title="", summary="", mood="", done=False, rolling=session.get("rolling", ""))
        with open(self.sessions_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(closed, ensure_ascii=False) + "\n")
        sessions = self.sessions()
        if len(sessions) > KEEP_SESSIONS:
            # Years of conversations: the oldest summaries go (their words stay in the archive, and what was
            # learnt from them in the memories), so each reply stays quick.
            _write_jsonl(self.sessions_path, sessions[-KEEP_SESSIONS:])
        state["open"] = None
        return closed

    def close_stale(self, now=None):
        """End the open conversation if it is over (quiet, a goodbye, a new day). Returns it if so."""
        now = now or time.time()
        with self._lock:
            state = self._state()
            why = self._ends(state.get("open"), now)
            if not why:
                return None
            closed = self._close(state, why)
            self._save_state(state)
            return closed

    def new_conversation(self):
        """The owner (or the person) starts afresh: the conversation going on ends now."""
        with self._lock:
            state = self._state()
            closed = self._close(state, "started afresh")
            self._save_state(state)
            return closed

    def add_turn(self, user, reply, interrupted=False, now=None):
        """Keep a turn: in the conversation going on, or a new one if that one is over. Returns its
        conversation's number."""
        # Turns are numbered, not only timed: a device without a clock battery can start with the wrong time.
        now = int(now or time.time())
        with self._lock:
            state = self._state()
            why = self._ends(state.get("open"), now)
            if why:
                self._close(state, why)
            history = self.history()
            n = (history[-1].get("n", 0) if history else 0) + 1
            session = state.get("open")
            if not session:
                sid = max(state.get("next", 1), max((s.get("id", 0) for s in self.sessions()), default=0) + 1)
                session = {"id": sid, "start": now, "first": n, "turns": 0, "rolling": "", "rolled": n - 1}
                state["next"] = sid + 1
            turn = {"n": n, "t": now, "s": session["id"], "user": user, "reply": reply}
            if interrupted:
                turn["interrupted"] = True
            session.update(end=now, last=n, turns=session.get("turns", 0) + 1)
            if user and GOODBYE.search(user):
                session["goodbye"] = now
            else:
                session.pop("goodbye", None)
            state["open"] = session
            lines = self._history_lines() + [json.dumps(turn, ensure_ascii=False)]
            write_atomic(self.history_path, "\n".join(lines[-KEEP_TURNS:]) + "\n")
            self._archive([turn])
            self._save_state(state)
            return session["id"]

    def transcript(self, sid):
        """Every turn of a conversation, from the archive."""
        out = []
        try:
            months = sorted(os.listdir(self.archive_dir))
        except OSError:
            return out
        for month in months:
            out += [t for t in _jsonl(os.path.join(self.archive_dir, month)) if t.get("s") == sid]
        return sorted(out, key=lambda t: t.get("n", 0))

    def edit_session(self, sid, title=None, summary=None):
        with self._lock:
            sessions = self.sessions()
            found = False
            for s in sessions:
                if s.get("id") == sid:
                    found = True
                    if title is not None:
                        s["title"] = _clean(title, TITLE_LIMIT)
                    if summary is not None:
                        s["summary"] = _clean(summary, SUMMARY_LIMIT)
                    s["done"] = True                      # the owner's words stand
            if found:
                _write_jsonl(self.sessions_path, sessions)
            return found

    def forget_session(self, sid):
        """Forget a conversation: its summary and every word of it (memories learnt from it stay, for
        the owner to remove separately). False if not found."""
        with self._lock:
            sessions = self.sessions()
            state = self._state()
            is_open = (state.get("open") or {}).get("id") == sid
            if not is_open and not any(s.get("id") == sid for s in sessions):
                return False
            _write_jsonl(self.sessions_path, [s for s in sessions if s.get("id") != sid])
            self._forget_vector("s", sid)
            if is_open:
                state["open"] = None
                self._save_state(state)
            _write_jsonl(self.history_path, [t for t in self.history() if t.get("s") != sid])
            try:
                months = os.listdir(self.archive_dir)
            except OSError:
                months = []
            for month in months:
                path = os.path.join(self.archive_dir, month)
                turns = _jsonl(path)
                kept = [t for t in turns if t.get("s") != sid]
                if len(kept) != len(turns):
                    _write_jsonl(path, kept)
            return True

    def session_turns(self, sid):
        """The turns of a conversation still in the recent history (word for word)."""
        return [t for t in self.history() if t.get("s") == sid]

    # ----- memories
    def memories(self, everything=False):
        """What it knows, oldest first (retired ones only with `everything`)."""
        items = _jsonl(self.memories_path)
        return items if everything else [m for m in items if m.get("status", "active") == "active"]

    def _save_memories(self, items):
        _write_jsonl(self.memories_path, items)

    def add_memory(self, text, category="other", importance=3, when_date=None, follow_up=False, source="owner",
                   pinned=False, now=None):
        """Keep a memory; one that says nearly the same as an active one updates that one instead.
        Returns its entry, or None if it says nothing."""
        text = _clean(text, MEMORY_LIMIT)
        if len(text) < 3:
            return None
        now = int(now or time.time())
        category = category if category in CATEGORIES else "other"
        with self._lock:
            items = self.memories(everything=True)
            words = set(_words(text))
            for m in items:
                if m.get("status", "active") != "active":
                    continue
                theirs = set(_words(m.get("text", "")))
                if words and theirs and len(words & theirs) / len(words | theirs) >= 0.75:
                    m.update(text=text, updated=now, importance=max(m.get("importance", 3), int(importance)))
                    if when_date:
                        m["when"] = when_date
                    self._save_memories(items)
                    return m
            entry = {"id": max((m.get("id", 0) for m in items), default=0) + 1, "text": text, "category": category,
                     "importance": max(1, min(5, int(importance))), "created": now, "updated": now, "source": source}
            if when_date:
                entry["when"] = when_date
            if follow_up:
                entry["follow_up"] = True
            if pinned:
                entry["pinned"] = True
            items.append(entry)
            # Keep the number within reason: the least important, longest unchanged go first; pinned stay.
            active = [m for m in items if m.get("status", "active") == "active"]
            if len(active) > KEEP_MEMORIES:
                spare = sorted((m for m in active if not m.get("pinned")),
                               key=lambda m: (m.get("importance", 3), m.get("updated", 0)))
                drop = {m["id"] for m in spare[:len(active) - KEEP_MEMORIES]}
                items = [m for m in items if m.get("id") not in drop]
            self._save_memories(items)
            return entry

    def edit_memory(self, mid, now=None, **changes):
        """Change a memory (text, category, importance, when, pinned, follow_up); None to retire it.
        False if not found."""
        with self._lock:
            items = self.memories(everything=True)
            for m in items:
                if m.get("id") != mid:
                    continue
                if "text" in changes and changes["text"] is not None:
                    text = _clean(changes["text"], MEMORY_LIMIT)
                    if len(text) < 3:
                        return False
                    m["text"] = text
                if changes.get("category") in CATEGORIES:
                    m["category"] = changes["category"]
                if "importance" in changes and changes["importance"] is not None:
                    m["importance"] = max(1, min(5, int(changes["importance"])))
                for flag in ("pinned", "follow_up"):
                    if flag in changes and changes[flag] is not None:
                        if changes[flag]:
                            m[flag] = True
                        else:
                            m.pop(flag, None)
                if "when" in changes:
                    if changes["when"]:
                        m["when"] = changes["when"]
                    else:
                        m.pop("when", None)
                if changes.get("status") in ("active", "retired", "done"):
                    m["status"] = changes["status"]
                m["updated"] = int(now or time.time())
                self._save_memories(items)
                return True
            return False

    def forget_memory(self, mid):
        with self._lock:
            items = self.memories(everything=True)
            kept = [m for m in items if m.get("id") != mid]
            if len(kept) == len(items):
                return False
            self._save_memories(kept)
            self._forget_vector("m", mid)
            return True

    def follow_ups(self, now=None):
        """Plans whose day has come, to ask how they went: a dated plan or event once its day has
        passed, unless it was changed since (they said how it went); and what the model asked to
        follow up, from its date. Each at most twice, within a fortnight."""
        now = now or time.time()
        today = datetime.date.fromtimestamp(now)
        out = []
        for m in self.memories():
            if m.get("asked", 0) >= 2:
                continue
            try:
                due = datetime.date.fromisoformat(m.get("when") or "")
            except ValueError:
                continue
            if today > due + datetime.timedelta(days=14):
                continue
            if m.get("follow_up"):
                if due <= today:
                    out.append(m)
            elif m.get("category") == "plans and events" and due < today:
                after = datetime.datetime.combine(due + datetime.timedelta(days=1), datetime.time()).timestamp()
                if m.get("updated", 0) < after:
                    out.append(m)
        return out

    def plans_on(self, day):
        """What they planned for a day (a date): the plans and events dated that day, not those only to ask about."""
        iso = day.isoformat()
        return [m["text"] for m in self.memories()
                if m.get("when") == iso and m.get("category") == "plans and events" and not m.get("follow_up")]

    def _mark_asked(self, ids):
        if not ids:
            return
        with self._lock:
            items = self.memories(everything=True)
            for m in items:
                if m.get("id") in ids:
                    m["asked"] = m.get("asked", 0) + 1
            self._save_memories(items)

    # ----- the diary
    def diary(self):
        return _jsonl(self.diary_path)

    def set_diary(self, day, text):
        """Change (or, with no text, remove) a day's entry. False if there is none."""
        with self._lock:
            entries = self.diary()
            found = any(e.get("day") == day for e in entries)
            if found:
                text = (text or "").strip()[:DIARY_LIMIT]
                _write_jsonl(self.diary_path, [dict(e, text=text) if e.get("day") == day else e
                                               for e in entries if text or e.get("day") != day])
            return found

    def clear(self):
        """Forget everything: conversations, memories, the diary and the owner's notes."""
        with self._lock:
            self._vectors = None
            for path in (self.history_path, self.notes_path, self.sessions_path, self.memories_path, self.diary_path,
                         self.state_path, self.vectors_path):
                if os.path.exists(path):
                    os.remove(path)
            if os.path.isdir(self.archive_dir):
                for name in os.listdir(self.archive_dir):
                    os.remove(os.path.join(self.archive_dir, name))
            self._save_state({"version": 2, "open": None, "next": 1})

    # ----- what the model is given
    def context(self, heard, now=None, room=24000, verbatim=KEEP_VERBATIM):
        """For a reply to `heard`: text about the past for the model's instructions, and this
        conversation so far as chat messages, within `room` characters in all."""
        now = now or time.time()
        state = self._state()
        session = state.get("open")
        sessions = self.sessions()
        last = sessions[-1] if sessions else None
        if self._ends(session, now):
            last, session = session, None     # it is over: the next word begins a new conversation
        history = self.history()
        parts = []
        notes = self.notes().strip()
        if notes:
            parts.append(("notes", "What they asked you always to remember:\n" + notes[:3000]))

        # Memories: the pinned, the most important, and those that match what was just said (plans to
        # ask about come below, when their day has come).
        due = self.follow_ups(now)[:3] if not session else []
        due_ids = {m["id"] for m in due}
        memories = [m for m in self.memories() if not m.get("follow_up") and m["id"] not in due_ids]
        if memories:
            question = heard + " " + " ".join(t.get("user", "") for t in history[-2:] if t.get("s") == (session or {}).get("id"))
            index = Index([m["text"] + " " + m.get("category", "") for m in memories])
            matched = [i for _, i in sorted(index.scores(question), reverse=True)[:RECALL_MEMORIES]]
            matched += [i for i in self._meant(question, memories) if i not in matched]
            core = sorted(range(len(memories)), key=lambda i: (-memories[i].get("importance", 3), -memories[i].get("updated", 0)))
            chosen = [i for i in range(len(memories)) if memories[i].get("pinned")]
            chosen += [i for i in core if memories[i].get("importance", 3) >= 4 and i not in chosen][:10]
            chosen += [i for i in matched if i not in chosen]
            lines = []
            for i in sorted(chosen, key=lambda i: memories[i].get("created", 0)):
                m = memories[i]
                dated = date_words(m.get("when"), now)
                lines.append("- [%s] %s%s" % (m.get("category", "other"), m["text"], (" (%s)" % dated) if dated else ""))
            if lines:
                parts.append(("memories", "What you remember about them (bring things up only when they fit):\n" + "\n".join(lines)))

        # Plans to ask about, on a conversation's first reply.
        asked = [m["id"] for m in due]
        if due:
            parts.append(("follow", "Plans whose day has come; ask how they went, once, early in this conversation:\n" + "\n".join(
                ("- " + m["text"]) if m.get("follow_up") else ("- %s \u2014 %s" % (m["text"], date_words(m.get("when"), now)))
                for m in due)))

        # How long since they last talked, and about what; the day's first conversation.
        carried = []
        if not session and (last or history):
            end = (last or {}).get("end") or (history[-1].get("t", 0) if history else 0)
            lines = []
            if last:
                if now - end < CARRY_OVER:
                    carried = [t for t in history if t.get("s") == last.get("id")][-CARRY_TURNS:]
                title = (", “%s”" % last["title"]) if last.get("title") else ""
                summary = last.get("summary") or last.get("rolling") or ""
                if not summary and not carried:
                    # Not summed up yet (the device was busy or off): how it ended, at least.
                    ending = [t for t in history if t.get("s") == last.get("id")][-2:]
                    summary = ("it ended with:\n" + _lines(ending).replace("Character:", "You:")) if ending else ""
                lines.append("Your last conversation with them was %s%s%s" % (
                    said_when(end, now), title, (": " + summary) if summary else "."))
                if carried:
                    lines.append("Its last few words are below, before what they say now.")
            if talk_day(now) != talk_day(end):
                lines.append("This is the first time you have talked today.")
                entry = next((e for e in reversed(self.diary()) if e.get("day") != talk_day(now).isoformat()), None)
                if entry and entry.get("text") and _days_since(entry.get("day"), now) <= 7:
                    lines.append("From your diary (%s): %s" % (entry["day"], _clean(entry["text"], 600)))
            parts.append(("last", "\n".join(lines)))
        elif session:
            lines = ["This conversation began %s." % _ago(now - session.get("start", now))]
            if session.get("rolling"):
                lines.append("Earlier in it: " + session["rolling"])
            parts.append(("now", "\n".join(lines)))

        # Older conversations that match.
        older = [s for s in sessions if s is not last]
        if older:
            best = self._match_sessions(older, heard, RECALL_SESSIONS)
            if best:
                lines = []
                for i in sorted(best):
                    s = older[i]
                    title = (", “%s”" % s["title"]) if s.get("title") else ""
                    lines.append("- %s%s: %s" % (said_when(s.get("end") or s.get("start", 0), now), title, s.get("summary", "")))
                parts.append(("older", "Earlier conversations that may matter now:\n" + "\n".join(lines)))

        # The latest last, nearest what is said now; within the room, older conversations go first,
        # then plans to ask about, then the less important memories.
        parts.sort(key=lambda p: SHOW_ORDER.index(p[0]))
        text = _fit(parts, room // 3)

        # This conversation word for word since its early part was summed up (so nothing falls between
        # the summary and the words), and the carried-over last words.
        if session:
            turns = [t for t in history if t.get("s") == session["id"] and t.get("n", 0) > session.get("rolled", 0)]
            turns = turns[-(max(verbatim, 2) + ROLL_STEP + 6):]      # if summing up keeps failing
        else:
            turns = carried
        messages = []
        left = room - len(text)
        for turn in reversed(turns):
            pair = []
            if turn.get("user"):
                pair.append({"role": "user", "content": turn["user"]})
            if turn.get("reply"):
                pair.append({"role": "assistant", "content": turn["reply"]})
            size = sum(len(m["content"]) for m in pair)
            if messages and size > left:
                break
            messages = pair + messages
            left -= size
        if messages and messages[0]["role"] == "assistant":
            # The character spoke first (some services need the first message to be the person's).
            messages = [{"role": "user", "content": "(You spoke first.)"}] + messages
        self._mark_asked(asked)
        return text, messages

    def _session_index(self, sessions):
        try:
            stat = os.stat(self.sessions_path)
            changed = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            changed = None
        key = (changed, len(sessions), sessions[-1].get("id") if sessions else 0)
        cached = self._indexes.get("sessions")
        if not cached or cached[0] != key:
            cached = (key, Index([s.get("title", "") + " " + s.get("summary", "") for s in sessions]))
            self._indexes["sessions"] = cached
        return cached[1]

    def recall(self, heard, limit=RECALL_SESSIONS):
        """The conversations that best match `heard` (up to `limit`, by words and meaning), and the last one,
        oldest first."""
        sessions = self.sessions()
        if not sessions:
            return []
        older = sessions[:-1]
        best = self._match_sessions(older, heard, limit) if older else []
        return [older[i] for i in sorted(best)] + [sessions[-1]]

    def _match_sessions(self, sessions, heard, limit):
        """Which conversations best match `heard`: by their words (rarer words counting for more), and by
        meaning when the model is there. Returns their positions, best first."""
        scores = {}
        for words, i in self._session_index(sessions).scores(heard):
            scores[i] = words / (words + 4.0)                  # words, made 0 to 1, to add to meaning
        if self.meaning is not None and heard.strip():
            sims = self._similarity(heard, "s", sessions, [_session_text(s) for s in sessions])
            if sims is not None:
                for i in np.argsort(-sims)[:limit * 2]:
                    if sims[i] >= MEANING_MIN_SESSION:
                        scores[int(i)] = scores.get(int(i), 0.0) + float(sims[i])
        ranked = sorted(scores.items(), key=lambda x: (-x[1], -x[0]))
        return [i for i, _ in ranked[:limit]]

    def _meant(self, question, memories):
        """Memories close in meaning to the question (positions, closest first), when the model is there."""
        if self.meaning is None or not question.strip():
            return []
        sims = self._similarity(question, "m", memories, [m["text"] for m in memories])
        if sims is None:
            return []
        order = np.argsort(-sims)[:MEANING_MEMORIES]
        best = float(sims[order[0]]) if len(order) else 0.0
        return [int(i) for i in order if sims[i] >= MEANING_MIN and sims[i] >= best - MEANING_SPREAD]

    def _similarity(self, question, kind, items, texts, limit=LIVE_VECTORS):
        """How alike in meaning the question is to each item (-1 to 1), or None if it has no known words."""
        query = self.meaning.embed([question])[0]
        if not query.any():
            return None
        return self._vectors_for(kind, [item["id"] for item in items], texts, limit) @ query

    # ----- vectors for matching by meaning (vectors.npz: one per memory and conversation, made once)
    def _vector_store(self):
        if self._vectors is None:
            self._vectors = {"m": {}, "s": {}}
            try:
                with np.load(self.vectors_path) as data:
                    for kind in ("m", "s"):
                        for i, k, v in zip(data[kind + "_ids"], data[kind + "_keys"], data[kind + "_vecs"]):
                            self._vectors[kind][int(i)] = (int(k), v)
            except (OSError, ValueError, KeyError):
                pass
        return self._vectors

    def _vectors_for(self, kind, ids, texts, limit=None):
        """The vectors for these items, making those missing or out of date (at most `limit` now; the rest
        count as unlike anything until the background work makes them)."""
        store = self._vector_store()[kind]
        keys = [zlib.crc32(t.encode("utf-8")) for t in texts]
        missing = [j for j, (i, k) in enumerate(zip(ids, keys)) if store.get(i, (None,))[0] != k]
        if limit is not None:
            missing = missing[:limit]
        if missing:
            made = self.meaning.embed([texts[j] for j in missing]).astype(np.float16)
            for j, vector in zip(missing, made):
                store[ids[j]] = (keys[j], vector)
            self._save_vectors()
        out = np.zeros((len(ids), self.meaning.size), dtype=np.float32)
        for j, (i, k) in enumerate(zip(ids, keys)):
            entry = store.get(i)
            if entry is not None and entry[0] == k:
                out[j] = entry[1]
        return out

    def refresh_vectors(self):
        """Make every missing vector and drop those of things forgotten (in the background)."""
        if self.meaning is None:
            return 0
        with self._lock:
            store = self._vector_store()
            memories, sessions = self.memories(), self.sessions()
            before = sum(len(v) for v in store.values())
            for kind, items in (("m", memories), ("s", sessions)):
                keep = {item["id"] for item in items}
                for i in [i for i in store[kind] if i not in keep]:
                    del store[kind][i]
            made = len([1 for m in memories if store["m"].get(m["id"], (None,))[0] != zlib.crc32(m["text"].encode("utf-8"))])
            made += len([1 for s in sessions if store["s"].get(s["id"], (None,))[0] != zlib.crc32(_session_text(s).encode("utf-8"))])
            self._vectors_for("m", [m["id"] for m in memories], [m["text"] for m in memories])
            self._vectors_for("s", [s["id"] for s in sessions], [_session_text(s) for s in sessions])
            if sum(len(v) for v in store.values()) != before:
                self._save_vectors()
            return made

    def _forget_vector(self, kind, item_id):
        if self.meaning is None and not os.path.exists(self.vectors_path):
            return
        if np is None:
            return
        store = self._vector_store()
        if store[kind].pop(item_id, None) is not None:
            self._save_vectors()

    def _save_vectors(self):
        arrays = {}
        size = self.meaning.size if self.meaning is not None else 256
        for kind in ("m", "s"):
            items = self._vectors[kind]
            arrays[kind + "_ids"] = np.array(list(items), dtype=np.int64)
            arrays[kind + "_keys"] = np.array([items[i][0] for i in items], dtype=np.int64)
            arrays[kind + "_vecs"] = np.array([items[i][1] for i in items], dtype=np.float16).reshape(len(items), size)
        tmp = os.path.join(self.folder, ".vectors-new.npz")
        np.savez(tmp, **arrays)
        os.replace(tmp, self.vectors_path)

    # ----- keeping it up to date (in the background, with the language model)
    def needs_work(self, now=None, keep=KEEP_VERBATIM):
        """Whether there is anything for `process` to do now (`keep`: the turns given word for word)."""
        now = now or time.time()
        state = self._state()
        session = state.get("open")
        if self._ends(session, now):
            return True
        if session and session.get("last", 0) - session.get("rolled", 0) > max(keep, 2) + ROLL_STEP:
            return True
        if any(not s.get("done") for s in self.sessions()):
            return True
        return self._diary_due(now) is not None

    async def process(self, llm, now=None, name="", persona="", limit=3, keep=KEEP_VERBATIM, rules=""):
        """Do what is due: end a conversation that is over; sum up the early part of a long one (all but
        its latest `keep` turns); for each ended conversation, a title, a summary and changes to the
        memories; the diary. `rules` are added to every instruction (family-safe mode).
        Returns what was done: {"summed": n, "memories": n, "diary": day or None}."""
        self._rules = rules or ""
        now = now or time.time()
        done = {"summed": 0, "memories": 0, "diary": None}
        self.close_stale(now)
        await self._roll(llm, max(keep, 2))
        for session in [s for s in self.sessions() if not s.get("done")][:limit]:
            changed = await self._sum_up(llm, session, now, name)
            if changed is None:
                break                        # the memory was forgotten meanwhile
            done["summed"] += 1
            done["memories"] += changed
        day = self._diary_due(now)
        if day is not None and not any(not s.get("done") for s in self.sessions()):
            if await self._write_diary(llm, day, now, name, persona):
                done["diary"] = day.isoformat()
        self.refresh_vectors()
        return done

    async def _ask(self, llm, prompt, system="You keep a character's memory. Follow the format exactly."):
        rules = getattr(self, "_rules", "")
        if rules:
            system = system + "\n" + rules
        text = ""
        async for piece in llm.stream(system, [{"role": "user", "content": prompt}]):
            text += piece
        return text

    async def _roll(self, llm, keep):
        """Sum up the early part of a long conversation still going on, keeping its latest `keep` turns."""
        state = self._state()
        session = state.get("open")
        if not session or session.get("last", 0) - session.get("rolled", 0) <= keep + ROLL_STEP:
            return
        turns = [t for t in self.history() if t.get("s") == session["id"] and t.get("n", 0) > session.get("rolled", 0)]
        fold = turns[:-keep]
        if not fold:
            return
        prompt = ROLL_PROMPT.format(earlier=session.get("rolling") or "(nothing yet)", conversation=_lines(fold))
        summary = _clean(_strip_label(await self._ask(llm, prompt)), SUMMARY_LIMIT)
        if not summary:
            return
        with self._lock:
            state = self._state()
            current = state.get("open")
            if not current or current.get("id") != session["id"]:
                return                       # it ended meanwhile; its summary covers it
            current["rolling"] = summary
            current["rolled"] = fold[-1].get("n", current.get("rolled", 0))
            self._save_state(state)

    async def _sum_up(self, llm, session, now, name):
        """Title, summary, mood and memory changes for an ended conversation. Returns the number of
        memories changed, or None if the conversation was forgotten meanwhile."""
        turns = self.transcript(session["id"]) or self.session_turns(session["id"])
        memories = self.memories()
        text = " ".join("%s %s" % (t.get("user", ""), t.get("reply", "")) for t in turns)
        index = Index([m["text"] for m in memories])
        related = sorted(index.scores(text), reverse=True)[:40]
        shown = [memories[i] for _, i in related]
        # Plans whose day has come (in the month before), whatever words were said: what a conversation settles.
        day = datetime.date.fromtimestamp(session.get("start", now))
        month = (day - datetime.timedelta(days=31)).isoformat()
        shown += [m for m in memories if m.get("when") and month <= m["when"] <= day.isoformat() and m not in shown
                  and (m.get("follow_up") or m.get("category") == "plans and events")][:10]
        shown += [m for m in memories if m.get("importance", 3) >= 4 and m not in shown][:15]
        known = "\n".join("#%d [%s] %s%s" % (m["id"], m.get("category", "other"), m["text"],
                                             " (%s)" % m["when"] if m.get("when") else "") for m in shown) or "(none yet)"
        start = datetime.datetime.fromtimestamp(session.get("start", now))
        passed = ["#%d" % m["id"] for m in shown if m.get("category") == "plans and events" and m.get("when")
                  and m["when"] <= start.date().isoformat()]
        passed = ("\nPlans above whose day has come: %s. If the conversation says how one went, UPDATE it to say "
                  "what happened.\n" % ", ".join(passed)) if passed else ""
        end = datetime.datetime.fromtimestamp(session.get("end", now))
        conversation = _lines(turns[-80:])
        if session.get("rolling") and len(turns) > 80:
            conversation = "(Earlier: %s)\n%s" % (session["rolling"], conversation)
        prompt = SUM_UP_PROMPT.format(name=name or "the character", known=known, passed=passed, date=start.strftime("%A %d %B %Y"),
                                      start=start.strftime("%H:%M"), end=end.strftime("%H:%M"),
                                      conversation=conversation or "(nothing was said)",
                                      categories=", ".join(CATEGORIES), today=datetime.date.fromtimestamp(now).isoformat())
        answer = await self._ask(llm, prompt)
        result = parse_sum_up(answer)
        if not (result["title"] or result["summary"] or result["ops"]):
            # A small model may answer with a plain paragraph instead of the form: that is its summary.
            result["summary"] = _strip_label(answer)
        summary = result["summary"] or session.get("rolling", "")
        if not summary.strip():
            # Nothing from the model: their own first words, so the conversation can still be found.
            said = [t.get("user", "") for t in turns if t.get("user")][:3]
            summary = ("They said: " + " / ".join(said)) if said else ""
        with self._lock:
            sessions = self.sessions()
            target = next((s for s in sessions if s.get("id") == session["id"]), None)
            if target is None:
                return None
            target["title"] = _clean(result["title"], TITLE_LIMIT)
            target["summary"] = _clean(summary, SUMMARY_LIMIT)
            target["mood"] = _clean(result["mood"], 40)
            target["done"] = True
            target.pop("rolling", None)
            _write_jsonl(self.sessions_path, sessions)
        changed = 0
        visible = {m["id"] for m in shown}
        counts = {}
        for op in result["ops"]:
            kind = "add" if op["op"] in ("add", "follow") else op["op"]
            counts[kind] = counts.get(kind, 0) + 1
            if counts[kind] > MAX_CHANGES:
                continue
            if op["op"] == "add":
                if self.add_memory(op["text"], op["category"], op["importance"], op.get("when"), source=session["id"],
                                   now=now):
                    changed += 1
            elif op["op"] == "follow":
                if self.add_memory(op["text"], "plans and events", 3, op.get("when"), follow_up=True,
                                   source=session["id"], now=now):
                    changed += 1
            elif op["op"] == "update" and op["id"] in visible:
                # A question to follow up that the model rewrites has had its answer: no longer to ask.
                if self.edit_memory(op["id"], now=now, text=op["text"], follow_up=False,
                                    **({"when": op["when"]} if op.get("when") else {})):
                    changed += 1
            elif op["op"] == "update":
                # A number it was never shown: a small model's way of saying something new.
                if self.add_memory(op["text"], _guess_category(op["text"], op.get("when")), 3, op.get("when"),
                                   source=session["id"], now=now):
                    changed += 1
            elif op["op"] == "forget" and op["id"] in visible and not _mixed_up(op, shown):
                if self.edit_memory(op["id"], now=now, status="retired"):
                    changed += 1
        return changed

    def _diary_due(self, now):
        """The day whose diary entry is due now (they talked that day, it has no entry yet, and the day
        is over or it is evening and quiet), or None."""
        state = self._state()
        written = {e.get("day") for e in self.diary()}
        today = talk_day(now)
        quiet = (state.get("open") is None) or (now - (state.get("open") or {}).get("end", now) > 30 * 60)
        days = sorted({talk_day(s.get("start", 0)) for s in self.sessions() if s.get("start")}, reverse=True)
        for day in days[:3]:
            if day.isoformat() in written or day.isoformat() <= state.get("diary_from", "") or (today - day).days > 3:
                continue
            if day < today or (day == today and quiet and datetime.datetime.fromtimestamp(now).hour >= DIARY_HOUR):
                return day
        return None

    async def _write_diary(self, llm, day, now, name, persona):
        sessions = [s for s in self.sessions() if talk_day(s.get("start", 0)) == day]
        if not sessions:
            return False
        lines = []
        for s in sessions:
            start = datetime.datetime.fromtimestamp(s.get("start", 0)).strftime("%H:%M")
            title = ("“%s”: " % s["title"]) if s.get("title") else ""
            lines.append("- %s %s%s" % (start, title, s.get("summary", "")))
        learnt = [m["text"] for m in self.memories() if talk_day(m.get("created", 0)) == day and m.get("source") != "owner"]
        earlier = [e for e in self.diary() if e.get("day", "") < day.isoformat()]
        previous = earlier[-1] if earlier else None
        gap = ""
        if previous:
            days = (day - datetime.date.fromisoformat(previous["day"])).days
            if days > 1:
                gap = "Before today you had not talked with them for %d days.\n" % (days - 1)
        prompt = DIARY_PROMPT.format(
            persona=(persona or "").strip()[:1500], name=name or "the character", day=day.strftime("%A %d %B %Y"),
            conversations="\n".join(lines), learnt="\n".join("- " + t for t in learnt[:10]) or "(nothing new)",
            previous=("Your previous entry (%s): %s" % (previous["day"], _clean(previous.get("text", ""), 700))) if previous else
            "This is your first diary entry.", gap=gap)
        text = _strip_label(await self._ask(llm, prompt, system="You write a diary in your own voice.")).strip()[:DIARY_LIMIT]
        if len(text) < 20:
            return False
        with self._lock:
            entries = [e for e in self.diary() if e.get("day") != day.isoformat()]
            entries.append({"day": day.isoformat(), "t": int(now), "text": text})
            entries.sort(key=lambda e: e.get("day", ""))
            _write_jsonl(self.diary_path, entries)
        return True


def _ago(seconds):
    seconds = max(0, seconds)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return "%d minutes ago" % round(seconds / 60)
    if seconds < 2 * 86400:
        hours = round(seconds / 3600)
        return "%d hour%s ago" % (hours, "" if hours == 1 else "s")
    return "%d days ago" % round(seconds / 86400)


def _session_text(session):
    """What a conversation is about, for matching by meaning."""
    return ("%s. %s" % (session.get("title", ""), session.get("summary", ""))).strip(" .")


def _days_since(day, now):
    """Days from an ISO date to the talk day of `now` (a large number for a date that cannot be read)."""
    try:
        return (talk_day(now) - datetime.date.fromisoformat(day or "")).days
    except ValueError:
        return 10 ** 6


def _lines(turns):
    out = []
    for turn in turns:
        if turn.get("user"):
            out.append("Person: %s" % turn["user"])
        if turn.get("reply"):
            out.append("Character: %s" % turn["reply"])
    return "\n".join(out)


def _fit(parts, room):
    """The parts' text, dropping whole parts (least needed first) and then memory lines until it fits."""
    order = ["older", "follow", "memories", "last", "now", "notes"]
    parts = list(parts)
    text = "\n\n".join(p for _, p in parts if p)
    for kind in order:
        if len(text) <= room:
            break
        if kind == "memories":
            # Shorten the memories from the end (the most recently learnt first goes last) rather than drop them all.
            for k, (name, body) in enumerate(parts):
                if name == "memories":
                    lines = body.split("\n")
                    while len(text) > room and len(lines) > 2:
                        lines.pop(1)
                        parts[k] = (name, "\n".join(lines))
                        text = "\n\n".join(p for _, p in parts if p)
            continue
        parts = [(name, body) for name, body in parts if name != kind]
        text = "\n\n".join(p for _, p in parts if p)
    return text[:max(room, 0)] if len(text) > room else text


def _strip_label(text):
    """A model's answer without a label it may put first ("Summary:", "**Diary entry**")."""
    text = (text or "").strip()
    text = re.sub(r"^\s*(\*\*|#+\s*)?(summary|diary entry|entry|updated summary)\s*[:*]*\s*", "", text, flags=re.I)
    return text.strip().strip("*").strip()


ROLL_PROMPT = """A conversation is going on. Here is what you have summed up of its early part so far:
{earlier}

And the turns since, which are to be added to that summary:
{conversation}

Write the updated summary of the conversation so far, in two to five plain sentences, keeping names,
facts, plans and how the person felt. Reply with the summary only."""

SUM_UP_PROMPT = """You keep the long-term memory of {name}, a character, about the person they talk with.

What {name} remembers about them so far (those that may matter here):
{known}
{passed}
The conversation ({date}, {start} to {end}; today is {today}):
{conversation}

Reply in exactly this form and nothing else:
TITLE: a few words naming what it was about
SUMMARY: one to three sentences, as the character would remember it: what was talked about, anything that happened or was planned, how the person felt
MOOD: one or two words for how the person seemed
Then the changes to the memories, one per line. Look carefully: most conversations teach something new
or show that something remembered has changed.
ADD category | importance 1-5 | the memory | the date of the plan or event (YYYY-MM-DD), or - if it is not one
UPDATE #number | the memory as it is now
FORGET #number | why (no longer true, or they asked you to forget)
FOLLOW UP YYYY-MM-DD | what to ask them about after that date

Each memory is one short sentence: about them, starting with They or Their; about {name} (category
"about me", what {name} said about themself, to stay consistent), starting with I. Give dates, not
words like today or last week, which go out of date.
Categories: {categories}.
Importance: 5 who they are (name, family), 4 what matters a lot to them, 3 useful, 2 minor, 1 trivial.
Keep only lasting things (names, relationships, likes, plans and events with their dates, how they want
to be treated, promises made). Not small talk. Never invent anything. Check each memory above against
what was said: if this conversation shows it is out of date (someone moved, a plan happened or was
called off), UPDATE or FORGET it rather than adding another."""

DIARY_PROMPT = """{persona}

You are {name}. Write your diary entry for {day}, in your own voice (first person, as yourself, in
your own way of speaking), about your day with the person you talk with (about them, not to them:
they never read it): what happened, what it meant
to you, how you feel about it, anything you look forward to. 80 to 160 words. Plain text, no heading,
no date. Only what was said in these conversations: you hear them but cannot see them, so say nothing
about how they looked, and invent nothing.

{gap}Today's conversations:
{conversations}

New things you learnt about them today:
{learnt}

{previous}"""

ADD_LINE = re.compile(r"(?i)^add\b[\s:]*(.+)$")
UPDATE_LINE = re.compile(r"(?i)^update\b[\s:]*#?\s*(\d+)\b\s*(.*)$")
FORGET_LINE = re.compile(r"(?i)^(?:forget|remove|retire)\b[\s:]*#?\s*(\d+)\b(.*)$")
FOLLOW_LINE = re.compile(r"(?i)^follow[\s-]*up\b(.*)$")
PLACEHOLDER = re.compile(r"\b[Yy]{4}-[Mm]{2}-[Dd]{2}\b")
TEMPLATE_WORDS = {"the memory", "the memory as it is now", "the memory, one short sentence", "why", "what to ask",
                  "one short sentence", "-", "none", "n/a"}
MAX_CHANGES = 8                       # of each kind, from one conversation: a model stuck in a loop adds no more
SEPARATORS = " |:-\u2013\u2014"
FIELD_LINE = re.compile(r"(?i)^(title|summary|mood)\s*:\s*(.*)$")


def parse_sum_up(text):
    """The title, summary, mood and memory changes in the model's answer (allowing for markdown it adds)."""
    out = {"title": "", "summary": "", "mood": "", "ops": []}
    for raw in (text or "").splitlines():
        line = PLACEHOLDER.sub("", re.sub(r"^[\s>*#_\-•]+", "", raw).replace("**", "").replace("__", "")).strip()
        line = re.sub(r"(?i)^add\s*\|\s*", "ADD ", line)           # "ADD | category | ...": the category one field on
        if not line:
            continue
        m = FIELD_LINE.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
            continue
        m = ADD_LINE.match(line)
        if m:
            op = _parse_add(m.group(1))
            if op:
                out["ops"].append(op)
            continue
        m = UPDATE_LINE.match(line)
        if m:
            text, day = _tail(_drop_category(m.group(2).strip(SEPARATORS)))
            if _meaningful(text):
                op = {"op": "update", "id": int(m.group(1)), "text": text}
                if day:
                    op["when"] = day
                out["ops"].append(op)
            continue
        m = FORGET_LINE.match(line)
        if m:
            out["ops"].append({"op": "forget", "id": int(m.group(1)), "why": m.group(2).strip(SEPARATORS)})
            continue
        m = FOLLOW_LINE.match(line)
        if m:
            rest = m.group(1)
            found = re.search(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", rest)
            text = (rest[:found.start()] + " | " + rest[found.end():]) if found else rest
            text = " ".join(part.strip(SEPARATORS) for part in text.split("|") if part.strip(SEPARATORS))
            if _meaningful(text):             # a date alone: its plan comes up by itself
                op = {"op": "follow", "text": text}
                if found and _valid_date(found.group(0)):
                    op["when"] = found.group(0)
                out["ops"].append(op)
    return out


CATEGORY_WORDS = {"likes": "likes and dislikes", "dislikes": "likes and dislikes", "people": "their people",
                  "plans": "plans and events", "events": "plans and events", "health": "health and wellbeing",
                  "wellbeing": "health and wellbeing", "work": "work and study", "study": "work and study",
                  "relationship": "our relationship", "me": "about me", "them": "about them"}
GUESSES = (("about me", r"^(i|i'm|i am|my)\b"),
           ("their people", r"\b(sister|brother|mum|mom|mother|dad|father|parents?|wife|husband|partner|girlfriend|boyfriend|"
                            r"friends?|son|daughter|kids|children|cousin|aunt|uncle|grand\w+|boss|colleague|family|neighbou?r)\b"),
           ("about them", r"\b(name|called|birthday|born|live[sd]?|from|age|years old)\b"),
           ("work and study", r"\b(job|work\w*|study\w*|school|universit\w+|college|exam|course|office|career|degree)\b"),
           ("health and wellbeing", r"\b(health\w*|sick|ill|doctor|sleep\w*|anxious|anxiety|stress\w*|diet|vegetarian|vegan|"
                                    r"allerg\w+|exercise|nervous|tired)\b"),
           ("likes and dislikes", r"\b(like[sd]?|love[sd]?|enjoy\w*|favou?rite|hate[sd]?|dislike[sd]?|prefer\w*|fan of)\b"))


def _category_of(field):
    """The category a field of an ADD line names, if it names one ("likes", "[their people]", "about them 4")."""
    low = field.lower().strip(" []().:")
    if low in CATEGORIES:
        return low
    if low in CATEGORY_WORDS:
        return CATEGORY_WORDS[low]
    if len(low.split()) <= 4:
        return next((c for c in CATEGORIES if len(low) >= 4 and (low in c or c in low)), None)
    return None


def _guess_category(text, when):
    """A category for a memory a model gave none for: by its words, or a plan if it has a date."""
    low = text.lower()
    for category, pattern in GUESSES[:2]:
        if re.search(pattern, low):
            return category
    if when:
        return "plans and events"
    return next((c for c, pattern in GUESSES[2:] if re.search(pattern, low)), "other")


def _parse_add(rest):
    """An ADD line's fields, in whatever order and dress a model wrote them ("category | importance 4 | the
    memory | 2026-11-13" with the form's own labels, a number glued to the category, a field missing):
    the category, the importance, the date, and the longest remaining field as the memory. None if it
    says nothing."""
    category, importance, when, texts = None, None, None, []
    for field in rest.split("|"):
        field = field.strip()
        low = field.lower().strip(" .[]()")
        if not low or low in ("-", "none", "n/a", "na", "category"):
            continue
        number = re.fullmatch(r"(?:importance\s*:?\s*)?([1-5])(?:\s*/\s*5)?", low)
        if number:
            importance = importance or int(number.group(1))
            continue
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", low):
            when = when or (low if _valid_date(low) else None)
            continue
        glued = re.fullmatch(r"(.*?)\s+([1-5])", low)
        named = _category_of(glued.group(1) if glued else low)
        if named and not category:
            category = named
            if glued:
                importance = importance or int(glued.group(2))
            continue
        texts.append(field)
    text, day = _tail(max(texts, key=len) if texts else "")
    stripped = _drop_category(text)               # "Their people: Kim is ...": the category written into it
    if stripped != text:
        category = category or _category_of(text[:len(text) - len(stripped)])
        text = stripped
    if not _meaningful(text):
        return None
    when = when or day
    op = {"op": "add", "category": category or _guess_category(text, when), "importance": importance or 3, "text": text}
    if when:
        op["when"] = when
    return op


def _meaningful(text):
    """Whether a memory a model wrote says something: not a category's name, not the form's own words, not a
    single word."""
    plain = (text or "").strip(" .").lower()
    return (len(plain.split()) >= 2 and len(plain) >= 8 and plain not in CATEGORIES and plain not in TEMPLATE_WORDS
            and not plain.startswith(("category |", "importance")))


def _mixed_up(op, shown):
    """Whether a FORGET's reason is plainly about another memory than its number's (a small model mixed
    the numbers up): it shares no word with its own memory, and two or more with another one shown."""
    why = set(_words(op.get("why", "")))
    if not why:
        return False
    target = next((m for m in shown if m["id"] == op["id"]), None)
    if target is None or why & set(_words(target["text"])):
        return False
    return any(len(why & set(_words(m["text"]))) >= 2 for m in shown if m["id"] != op["id"])


def _drop_category(text):
    """A memory's text without the category a model may repeat before it ("plans and events: ...")."""
    low = text.lower()
    for c in sorted(CATEGORIES, key=len, reverse=True):
        if low.startswith("[" + c + "]"):
            return text[len(c) + 2:].strip(SEPARATORS)
        if low.startswith(c) and low[len(c):len(c) + 1] in ("|", ":", "]"):
            return text[len(c) + 1:].strip(SEPARATORS)
    return text


def _tail(text):
    """A memory's text without a date or dash a model put after it, and that date (or None)."""
    text = text.strip()
    m = (re.search(r"\s*\|\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|-|none|n/?a)\s*$", text, re.I)
         or re.search(r"\s*\(([0-9]{4}-[0-9]{2}-[0-9]{2})\)\s*$", text) or re.search(r"\s+-$", text))
    if not m:
        return text, None
    day = m.group(1) if m.lastindex and _valid_date(m.group(1)) else None
    return text[:m.start()].strip(), day


def _valid_date(text):
    try:
        datetime.date.fromisoformat(text)
        return True
    except ValueError:
        return False
