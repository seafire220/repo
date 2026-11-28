"""The device: ties settings, characters, memory, providers, sound, the button and the stage together."""
import asyncio
import datetime
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time

from . import animations, backgrounds, family, package, providers, special_days
from . import weather as weather_module
from .audio import Detector, Microphone, Speaker, tone
from .backlight import Backlight
from .characters import Characters, split_persona
from .conversation import Conversation
from .memory import Memory, talk_day
from . import motions, screen, speech_text, temperament
from . import meaning as meaning_module
from . import updates as updates_module
from . import wakeword
from .network import NetworkError, WiFi
from .overlays import CAPTION, CLOCK, HEARD, HINT, Overlays
from .providers import ProviderError
from .stage_link import StageLink
from .store import Store, redact, write_atomic

log = logging.getLogger("companion")

UPDATE_CHECK_EVERY = 12 * 3600
# Models that came with the device, on its read-only system partition (an image built with power-cut
# protection, docs/33-finishing.md); those an update brings, in DATA/models, are preferred.
FACTORY_MODELS = os.environ.get("COMPANION_FACTORY_MODELS", "/opt/companion/models")
# On such a device the time zone is kept on the data partition: /etc/localtime points here.
TIME_DIR = "/data/system/time"
LOCALTIME = "/etc/localtime"
ZONEINFO = "/usr/share/zoneinfo"
# Speaking first: what the model is told, how long it listens for an answer, and how much speech-like
# sound means someone is there.
GREETING_PROMPT = ("(Nothing has been said yet. You hear someone nearby, most likely them, after %s without "
                   "talking. Speak first: greet them warmly and briefly, one or two short sentences, in your own "
                   "way. If a plan of theirs has had its day, you may ask how it went.)")
ANSWER_WINDOW = 30.0
PRESENCE_SECONDS = 0.8
# The morning hello (speak_first.morning): the first time someone is heard in the morning, whenever they last talked.
MORNING_PROMPT = ("(Nothing has been said yet. It is morning, and you hear someone nearby, most likely them, for the "
                  "first time today. Speak first: wish them good morning, warmly and in your own way, in two or three "
                  "short sentences. Mention the weather simply, as a friend would, and one of their plans for today if "
                  "they have any. If today is a special day for them, wish them well for it.)")
MORNING_HOURS = (5, 12)
WEATHER_WAIT = 6.0            # seconds the morning hello and a wake-up wait for the weather to come

# Alarms and timers, said by the character itself (docs/28-user-guide.md, "Your day"): what the model is told;
# how long it listens for an answer before calling again; how many times it calls; how much louder each time.
RING_PROMPTS = {
    "wake": ("(The wake-up alarm they set for %(time)s has just gone off, and they may still be asleep. Wake them gently "
             "but clearly, in your own way, in two or three short sentences: good morning, the time, the weather today, "
             "and their first plan if they have one. If they ask for a few more minutes, set a timer for it.)"),
    "alarm": ("(The alarm they asked you to set for %(time)s, \"%(label)s\", has just gone off. Tell them, briefly, in "
              "one or two short sentences.)"),
    "timer": "(The \"%(label)s\" timer they asked you to set has just finished. Tell them, briefly, in one short sentence.)",
}
RING_AGAIN = {"wake": 60.0, "alarm": 60.0, "timer": 30.0}
RING_TIMES = {"wake": 5, "alarm": 5, "timer": 3}
RING_LOUDER = 0.1
RING_WAIT = 20.0              # an alarm waits at most this long for a reply in progress to end
WAKE_LATE = 5 * 60            # a daily wake-up still rings this long after its time (a restart just then)
MEMORY_TEMPERATURE = 0.3      # the background memory work: summing up, memories, the diary
MEMORY_MAX_TOKENS = 700

# The button, held: from MUTE_HOLD seconds the microphone goes off (or on) when it is let go; from
# RESET_WARN the screen warns that holding on forgets the Wi-Fi and the settings-page password, which
# happens when it is let go after RESET_HOLD.
MUTE_HOLD = 2.0
RESET_WARN = 7.0
RESET_HOLD = 12.0

# The screen at night (docs/28-user-guide.md): it sleeps in its hours once nothing has happened for
# SCREEN_IDLE seconds, and wakes for the button, the wake word, an alarm or speech.
SCREEN_IDLE = 90.0
DIM_BACKLIGHT = 0.12          # how bright a dimmed screen's backlight is, where it can be set

# The wake word (docs/28-user-guide.md): after it, how long it listens for the request; after the reply,
# how long it listens for more without the wake word again.
WAKE_WINDOW = 8.0
FOLLOW_UP_WINDOW = 10.0

# Memory is kept up to date in the background (memory.Memory.process): a conversation is summed up, and
# its memories changed, once it is over; a long one's early part as it goes; the diary at night.



# What the settings page shows of the stage's heartbeat.
ANIMATION_FIELDS = ("fps", "state", "mood", "playing", "channels", "budget", "drives", "update_ms", "draw_ms")

class Device:
    def __init__(self, data_dir, stage_socket=None, speaker=None, microphone=None, stage=None,
                 provider_factory=None, wifi=None, restart=None, run=None):
        self.data_dir = data_dir
        self.store = Store(os.path.join(data_dir, "config"))
        self.characters = Characters(os.path.join(data_dir, "characters"))
        self.memory_dir = os.path.join(data_dir, "memory")
        self.current_puppet = os.path.join(data_dir, "current.inp")
        self.movement_map = os.path.join(data_dir, "current-map.json")   # read by the stage at start
        # A 3D avatar's animation style, clip folders, drawing cost and shot, also read by the stage at start.
        self.stage_settings = os.path.join(data_dir, "current-stage.json")
        self.animations = animations.Animations(self.characters)
        self.backgrounds = backgrounds.Backgrounds(data_dir)
        self.background_sent = None      # the picture the stage was last told to show
        self.weather = weather_module.Weather(data_dir, secret=self.store.secret)
        self._weather_task = None        # a forecast being fetched in the background
        self.rings = {}                  # alarms and timers ringing now: label -> task
        self._answered = None            # set when someone answers a ringing alarm (speaks, or presses the button)
        self._ring_lock = None           # one alarm speaks at a time
        self.clock_shown = None          # what the clock over the character shows now (None: hidden)
        # Gestures and idle actions changed or added on the settings page, for every character.
        self.custom_motions = motions.CustomMotions(os.path.join(data_dir, "config", "custom-motions.json"))
        speech_text.set_custom_gestures(self.custom_motions.words())
        self.speaker = speaker or Speaker()
        self.microphone = microphone
        self.stage = stage or StageLink(stage_socket or "/run/companion/stage.sock", self._stage_event)
        self.provider_factory = provider_factory
        self.subscribers = set()
        self.animation = {}                 # the stage's latest report of what the character is doing
        self.gesture_log = []               # its latest gestures, moods and skipped gestures
        self.state = "idle"
        self.listening = False
        self.recording = None
        self.conversation = None
        self.timers = {}
        self.started = time.time()
        self.log = []
        self.wifi = wifi
        self.card = os.path.join(data_dir, "card.inp")
        # How to restart the service after a reset (systemd starts it again), and
        # how to run system commands; both replaceable in tests.
        self.restart = restart or (lambda: os._exit(0))
        self.run = run or _run_command
        self.stage_seen = time.time()
        # A 3D avatar the stage keeps failing with: when the stage was last asked to start again (so a
        # lost connection then is expected), and how many times it failed since the avatar was shown.
        self.stage_restarted = 0.0
        self.stage_ready_at = 0.0
        self.vrm_failures = {}
        self.last_use = 0.0                 # when the settings page was last used
        self.last_talk = 0.0                # when the character last replied
        self.summarising = False            # a memory update is under way
        self.memory_retry = 0.0             # after a failed memory update, wait until then
        self.updates = updates_module.Updates(data_dir)
        self.last_greeting = 0.0            # when the character last spoke first
        self.answer_until = 0.0             # after speaking first, it listens for an answer until then
        self.updating = False               # an update is being fetched or handed to the installer
        # Pictures over the character, made in the folder beside the stage's socket (in memory on the device).
        socket = stage_socket or (None if stage is not None else "/run/companion/stage.sock")
        self.overlays = Overlays(os.path.join(os.path.dirname(socket), "overlays") if socket
                                 else os.path.join(data_dir, "overlays"), self.stage)
        self.mic_switch = None              # a mute switch's position: None with no switch, True for off
        self._mute_choice = bool(self.store.settings().get("microphone", {}).get("muted"))
        self._hold = None                   # the button's hold hints, while it is held
        self.backlight = Backlight()
        self.screen_mode = "on"             # "on", "dim" or "off"
        self.last_activity = time.time()    # the button, speech, the wake word: someone is there
        self._wake = None                   # the wake-word listener, once built for the words set now
        self._wake_for = None               # what it was built for (words, sensitivity)
        self._wake_building = False
        self.wake_error = ""
        self._wake_session = False          # listening because of the wake word (follow-ups, then stop)
        self._strip_wake = False            # the next utterance starts with the wake words

    # ----- events for the console
    def emit(self, kind, data=None):
        event = {"t": round(time.time(), 2), "kind": kind}
        event.update(data or {})
        if "message" in event:
            event["message"] = redact(event["message"], self.store)
        self.log = (self.log + [event])[-200:]
        for queue in list(self.subscribers):
            if queue.qsize() < 500:
                queue.put_nowait(event)

    def publish(self, kind, data=None):
        """A live event for open settings pages that is not kept in the log (frequent ones)."""
        event = {"t": round(time.time(), 2), "kind": kind}
        event.update(data or {})
        for queue in list(self.subscribers):
            if queue.qsize() < 500:
                queue.put_nowait(event)

    def _stage_event(self, event):
        self.stage_seen = time.time()
        kind = event.get("event")
        if kind == "heartbeat":
            # What the character is doing, for the settings page's live view.
            self.animation = {k: event.get(k) for k in ANIMATION_FIELDS}
            self.publish("animation", {"status": self.animation})
        elif kind in ("gesture", "gesture_skipped", "gesture_waiting", "emotion"):
            entry = {"t": round(time.time(), 2), "event": kind, "name": event.get("name") or event.get("emotion")}
            entry.update({k: event[k] for k in ("cause", "played", "why") if event.get(k)})
            self.gesture_log = (self.gesture_log + [entry])[-60:]
            self.publish("animation", {"gesture": entry})
        if kind == "quality":
            self._quality_stepped(event)
        # Stopping soon after it started, unasked, is a failure (a crash or running out of memory).
        lost = kind == "lost" and time.time() - self.stage_restarted > 10.0 and time.time() - self.stage_ready_at < 60.0
        if kind == "fatal" or lost:
            self._stage_failed(event.get("message") or "the screen program stopped")
        if kind in ("ready", "size") and event.get("width") and event.get("height"):
            self.overlays.screen = (int(event["width"]), int(event["height"]))
            self.clock_shown = None               # drawn again for this screen (and after the stage started again)
        if kind == "ready":
            self.stage_ready_at = time.time()
            # A stage that started again has nothing over the character and a lit screen.
            self.overlays.shown.clear()
            if self.muted:
                self.overlays.microphone(True)
            if self.screen_mode != "on":
                self.stage.send({"screen": self.screen_mode})
        if event.get("event") in ("error", "fatal"):
            self.emit("stage_error", {"message": event.get("message", "")})
        elif event.get("event") == "ready":
            if hasattr(self.stage, "random_gestures"):
                self.stage.random_gestures(self.store.settings()["conversation"].get("random_gestures", True))
            self.use_emotion_map()
            self.use_custom_motions()
            self.use_character_motion()
            self.tell_the_time()
            try:
                self.clock_step()
            except Exception:
                log.exception("the clock could not be shown")

    def tell_the_time(self):
        """Tell the stage the local time of day (the character is sleepy at night)."""
        if hasattr(self.stage, "clock"):
            now = time.localtime()
            self.stage.clock(now.tm_hour + now.tm_min / 60.0 + now.tm_sec / 3600.0)

    async def clock_watch(self, every=15.0, stage_every=600.0):
        """Keep the screen in step with the time: the stage's time of day (the character is sleepy at night; the
        clock may be changed while it runs), the background (the time of day's, a special day's, the next of the
        owner's pictures: changed only while nobody is talking), the clock over the character and the weather it
        shows, and the daily wake-ups. Wakes every `every` seconds, and just after each minute."""
        told = time.time()
        while True:
            await asyncio.sleep(max(1.0, min(every, 60.5 - time.time() % 60)))
            now = time.time()
            for step in (self.wake_step, self._background_step, self.clock_step, self._weather_step):
                try:
                    step(now)
                except Exception:
                    log.exception("the clock watch failed at %s", step.__name__)
            if now - told >= stage_every:
                told = now
                self.tell_the_time()

    def _quiet(self):
        """Nobody is talking to the character, and no alarm is ringing."""
        return self.state == "idle" and not self.rings and not (self.conversation is not None and self.conversation.busy)

    def _background_step(self, now=None):
        if self._quiet():
            self.show_background()

    async def stage_watch(self, patience=25.0):
        """If the character cannot be shown (the stage keeps failing to start), show a card instead.

        A puppet the stage cannot load makes it exit before it can report
        why, so the sign is that it never connects.
        """
        while True:
            await asyncio.sleep(5.0)
            if getattr(self.stage, "writer", None) is not None:
                self.stage_seen = time.time()
                continue
            showing = os.path.realpath(self.current_puppet)
            if time.time() - self.stage_seen > patience and showing != os.path.realpath(self.card):
                # A 3D avatar gets a second try, drawn lightly, before the card.
                if self._stage_failed("the screen program did not start"):
                    self.stage_seen = time.time()
                    continue
                self.emit("error", {"message": "the character could not be shown; showing a message instead"})
                self.show_card(screen.message_card(
                    "Character problem", "This character could not be shown. Choose another one, or add it "
                    "again, on the settings page."))
                self.stage_seen = time.time()

    # ----- time zone, resets
    def timezone(self):
        try:
            return os.path.relpath(os.path.realpath(LOCALTIME), os.path.realpath(ZONEINFO))
        except ValueError:
            return ""

    async def set_timezone(self, zone):
        if not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}", zone or "") or \
                not os.path.isfile(os.path.join("/usr/share/zoneinfo", zone)):
            raise ValueError("unknown time zone: %s" % zone)
        link = LOCALTIME
        if os.path.islink(link) and os.readlink(link).startswith(TIME_DIR + "/"):
            # A read-only system: the zone is a link on the data partition, which /etc/localtime points at.
            target = os.path.join(TIME_DIR, "localtime")
            if os.path.lexists(target + ".new"):
                os.remove(target + ".new")
            os.symlink(os.path.join(ZONEINFO, zone), target + ".new")
            os.replace(target + ".new", target)
            time.tzset()
        else:
            await self.run("timedatectl", "set-timezone", zone)
        self.emit("device", {"timezone": zone})

    async def forget_wifi(self):
        """Forget every saved Wi-Fi network (not the setup hotspot)."""
        if self.wifi is None:
            return
        try:
            out = await self.run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show")
        except Exception:
            return
        from .network import HOTSPOT_NAME, split_terse
        for line in out.splitlines():
            name, kind = (split_terse(line) + ["", ""])[:2]
            if kind.endswith("wireless") and name != HOTSPOT_NAME:
                try:
                    await self.run("nmcli", "connection", "delete", name)
                except Exception:
                    pass

    async def setup_reset(self):
        """Long press of the button: forget the settings-page password and Wi-Fi, keep characters and keys."""
        self.interrupt()
        if os.path.exists(self.store.password_path):
            os.remove(self.store.password_path)
        await self.forget_wifi()
        self.emit("device", {"did": "setup reset"})
        self.show_card(screen.message_card("Setup reset", "The settings-page password and Wi-Fi were "
                                           "forgotten. Starting again…"))
        await asyncio.sleep(2.0)
        self.restart()

    async def factory_reset(self):
        """Remove everything the owner added: characters, memories, keys, settings, timers, Wi-Fi."""
        self.interrupt()
        await self.forget_wifi()
        keep = {"models", "updates"}           # the software's, not the owner's: kept like the speech models
        for name in os.listdir(self.data_dir):
            if name in keep:
                continue
            path = os.path.join(self.data_dir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        self.emit("device", {"did": "factory reset"})
        self.show_card(screen.message_card("Reset", "Everything was removed. Starting again…"))
        await asyncio.sleep(2.0)
        self.restart()

    # ----- providers, rebuilt from the settings for each conversation
    def build(self, which, **extra):
        if self.provider_factory:
            return self.provider_factory(which, self)
        settings = self.store.settings()
        if which == "llm":
            return providers.build_llm(settings, self.store)
        if which == "stt":
            # Kept between uses: the on-device model takes about 200 MB and
            # seconds to load, so it is loaded once and reused until the
            # speech settings or keys change.
            key = (json.dumps(settings["stt"], sort_keys=True), json.dumps(self.store.secret_status(), sort_keys=True))
            if getattr(self, "_stt_key", None) != key:
                self._stt = None
                self._stt = providers.build_stt(settings, self.store, self.model_dir("stt"))
                self._stt_key = key
            return self._stt
        if which == "tts":
            active_provider = extra.get("provider") or settings["tts"].get("provider", "")
            if active_provider == "local":
                # The on-device voice is kept loaded too (about 110 MB, a second to load); only the
                # speaker changes from character to character.
                model_dir = self.model_dir("tts")
                key = json.dumps({"provider": "local", "speed": settings["tts"].get("speed", 1.0), "dir": model_dir},
                                 sort_keys=True)
                if getattr(self, "_tts_key", None) != key:
                    self._tts = None
                    self._tts = providers.build_tts(settings, self.store, provider="local", model_dir=model_dir)
                    self._tts_key = key
                default_voice = settings["tts"].get("voice", "") if settings["tts"].get("provider") == "local" else ""
                self._tts.set_voice(extra.get("voice") or default_voice)
                return self._tts
            return providers.build_tts(settings, self.store, **extra)
        raise ValueError(which)

    def model_dir(self, name):
        """A model's folder: one an update brought (DATA/models), else the one the device came with."""
        mine = os.path.join(self.data_dir, "models", name)
        factory = os.path.join(FACTORY_MODELS, name)
        return mine if os.path.isdir(mine) or not os.path.isdir(factory) else factory

    # ----- characters
    def active_id(self):
        cid = self.store.settings().get("active_character", "")
        ids = self.characters.ids()
        if cid in ids:
            return cid
        return ids[0] if ids else ""

    def memory(self, cid=None):
        cid = cid or self.active_id()
        return Memory(os.path.join(self.memory_dir, cid or "_none"),
                      meaning=meaning_module.load(self.model_dir("meaning")))

    def activate(self, cid):
        if cid not in self.characters.ids():
            raise KeyError(cid)
        self.interrupt()
        self.vrm_failures.pop(cid, None)          # chosen again: a fresh start
        self.store.update_settings({"active_character": cid})
        self._link_puppet(cid)
        self.use_emotion_map(cid)
        self.conversation = None
        self.reload_wake()                   # its name is the wake word
        self.restart_stage()                 # the service manager starts it again with the new puppet
        self.emit("character", {"id": cid})

    def use_emotion_map(self, cid=None):
        """Tell the stage link which of the character's own expressions shows each emotion."""
        cid = cid or self.active_id()
        if cid and hasattr(self.stage, "emotion_map"):
            self.stage.emotion_map = self.characters.emotion_map(cid)

    def use_custom_motions(self):
        """Give the stage the gestures and idle actions changed or added on this device (and tell the
        language model about them, with the new gestures a 3D avatar's own clips add)."""
        words = self.custom_motions.words()
        cid = self.active_id()
        if cid:
            words.update(self.animations.words(cid))
        speech_text.set_custom_gestures(words)
        if hasattr(self.stage, "define"):
            for name, entry in self.custom_motions.all().items():
                self.stage.define(name, entry)

    def use_character_motion(self, cid=None):
        """Tell the stage the character's temperament, and the gestures it never makes by itself."""
        cid = cid or self.active_id()
        saved = self.characters.device_settings(cid) if cid else {}
        energy, expressiveness = temperament.for_stage(saved.get("temperament"))
        off = [str(g)[:60] for g in saved.get("gestures_off") or []][:100]
        if hasattr(self.stage, "temperament"):
            self.stage.temperament(energy, expressiveness)
        if hasattr(self.stage, "gestures_off"):
            self.stage.gestures_off(off)
        if self.conversation is not None:
            self.conversation.planner.expressiveness = expressiveness

    def _link_puppet(self, cid):
        """Point the stage at the character's drawing. The stage tells the formats apart by content."""
        inochi = ("visual/puppet.inp", "visual/puppet.inx")
        live2d = (package.LIVE2D_MODEL,)
        prefer_inochi = self.store.settings().get("character_format") == "inochi2d"
        # A VRM avatar (3D) is the character's only drawing when it has one.
        for name in (package.VRM_MODEL,) + ((inochi + live2d) if prefer_inochi else (live2d + inochi)):
            target = self.characters.file(cid, name)
            if os.path.exists(target):
                # The owner's movement settings name the drawing's parameters; both drawings of a character
                # use the same names, and the stage keeps its own choice for any it lacks.
                self.write_movement_map(cid)
                self.write_stage_settings(cid)
                self._point_stage_at(target)
                return
        self.write_movement_map()
        self.write_stage_settings()
        write_atomic(self.card, screen.card_puppet(screen.message_card(
            "Can't show this character",
            "Its package has no drawing this device can show. Try installing it again.")))
        self._point_stage_at(self.card)

    def show_card(self, png):
        """Show a message on the screen instead of the character."""
        self.write_movement_map()
        self.write_stage_settings()
        write_atomic(self.card, screen.card_puppet(png))
        self._point_stage_at(self.card)
        self.restart_stage()

    def show_character(self):
        cid = self.active_id()
        if cid:
            self._link_puppet(cid)
            self.restart_stage()
        return cid

    def restart_stage(self):
        """Ask the stage to start again (the service manager does), with what it is to show now."""
        self.stage_restarted = time.time()
        self.stage.send({"quit": True})

    def _stage_failed(self, why):
        """The stage stopped, or never started, while showing a 3D avatar: draw it lighter once; if that
        fails too, show a card saying it is too much for this device. Other characters are left to the
        service manager (it starts the stage again) and to stage_watch."""
        cid = self.active_id()
        if not cid or self.characters.drawing(cid) != "vrm" or os.path.realpath(self.current_puppet) == os.path.realpath(self.card):
            return False
        failures = self.vrm_failures.get(cid, 0) + 1
        self.vrm_failures[cid] = failures
        quality = self.animations.settings(cid)["quality"]
        if quality != "lite":
            self.animations.set_settings(cid, {"quality": "lite"})
            self.characters.set_device_settings(cid, {"vrm_failsafe": {"why": str(why)[:200], "at": int(time.time())}})
            self.write_stage_settings(cid)
            self.emit("error", {"message": "the 3D avatar stopped the screen (%s); showing it with a lighter drawing" % str(why)[:120]})
            return True
        if failures >= 2:
            self.emit("error", {"message": "the 3D avatar is too much for this device, even drawn lightly; showing a message instead"})
            self.show_card(screen.message_card(
                "Too detailed for this device",
                "This 3D avatar needs more than this device has, even drawn lightly. Choose another character, "
                "or export it with fewer polygons and smaller textures and add it again."))
            return True
        return False

    def _quality_stepped(self, event):
        """The stage drew a 3D avatar more simply because frames came too slowly: keep a note for the
        settings page."""
        cid = self.active_id()
        if not cid:
            return
        seen = {"level": int(event.get("level") or 0), "change": str(event.get("change") or "")[:80],
                "fps": event.get("fps"), "at": int(time.time())}
        self.characters.set_device_settings(cid, {"vrm_quality_seen": seen})
        self.emit("stage_quality", {"message": "the screen was too slow, so the avatar is now " + seen["change"]})

    def write_movement_map(self, cid=None):
        """The movement settings of the character on the screen (none for a message card), for the stage."""
        controls = self.characters.device_settings(cid).get("controls") if cid else None
        write_atomic(self.movement_map, json.dumps(controls if isinstance(controls, dict) else {}, indent=1))

    def write_stage_settings(self, cid=None):
        """The stage settings of the character on the screen (a 3D avatar's animation style and the
        like, and the picture behind it); none for a card."""
        settings = self.animations.stage_settings(cid) if cid else {}
        if cid:
            settings["background"] = self.background_spec(cid)
            self.background_sent = settings["background"]
        write_atomic(self.stage_settings, json.dumps(settings, indent=1))

    def background_spec(self, cid, hour=None, now=None):
        """What the stage draws behind this character now (backgrounds.py), or None: on a special day, its festive
        picture (unless the owner turned them off, or chose no picture at all)."""
        try:
            own = self.characters.device_settings(cid)
        except (OSError, ValueError):
            own = {}
        settings = self.store.settings()
        festive = special_days.festive(self.specials(now, cid), settings)
        return self.backgrounds.stage_spec(settings, own, hour, festive=festive, now=now)

    def show_background(self, force=False):
        """Tell the stage at once which picture goes behind the character on the screen (after a change of
        choice, or as the time of day moves on), and keep the stage settings for its next start."""
        cid = self.active_id()
        if not cid or os.path.realpath(self.current_puppet) == os.path.realpath(self.card):
            return False
        spec = self.background_spec(cid)
        if spec == self.background_sent and not force:
            return False
        self.write_stage_settings(cid)
        self.stage.send({"background": spec})
        return True

    def _point_stage_at(self, target):
        tmp = self.current_puppet + ".new"
        if os.path.lexists(tmp):
            os.remove(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, self.current_puppet)

    def make_conversation(self):
        cid = self.active_id()
        if not cid:
            raise ProviderError("no character is installed yet")
        char_settings = self.characters.device_settings(cid)
        voice = char_settings.get("voice") or None
        voice_provider = char_settings.get("voice_provider") or None
        settings = self.store.settings()
        self.speaker.volume = float(settings.get("volume", 0.8))
        conversation = Conversation(
            llm=self.build("llm"), tts=self.build("tts", voice=voice, provider=voice_provider),
            speaker=self.speaker, stage=self.stage, memory=self.memory(cid),
            persona=self.characters.persona(cid),
            history_turns=int(settings["conversation"].get("history_turns", 12)),
            on_event=self._conversation_event, on_action=self.action, search=self._search_function(settings),
            gestures=None if settings["conversation"].get("gestures", True) else [], on_caption=self._caption,
            family_safe=bool(settings.get("family_safe", {}).get("on")), today=self.today_text)
        # A reserved character's words call for fewer gestures; an expressive one's for more.
        conversation.planner.expressiveness = temperament.for_stage(char_settings.get("temperament"))[1]
        return conversation

    # ----- today: the owner, special days, the weather, their plans (docs/28-user-guide.md, "Your day")
    def arrival(self, cid):
        """The day a character arrived on this device: kept in its device settings when it is installed (or,
        for one installed before that was kept, the day its files were written)."""
        try:
            return datetime.date.fromisoformat(self.characters.device_settings(cid).get("arrived") or "")
        except (TypeError, ValueError):
            pass
        try:
            written = os.path.getmtime(self.characters.file(cid, "manifest.json"))
        except OSError:
            return None
        day = datetime.date.fromtimestamp(written)
        try:
            self.characters.set_device_settings(cid, {"arrived": day.isoformat()})
        except OSError:
            pass
        return day

    def specials(self, now=None, cid=None):
        """What is special about today (special_days.on) for the character on the screen, or `cid`."""
        now = now or time.time()
        cid = cid or self.active_id()
        return special_days.on(datetime.date.fromtimestamp(now), self.store.settings(),
                               self.arrival(cid) if cid else None)

    def today_text(self, now=None):
        """What the character is told about today, for its instructions: the owner's name, special days, the
        weather (as last fetched; fetched again in the background when it is due) and their plans for today."""
        now = now or time.time()
        settings = self.store.settings()
        lines = []
        name = " ".join(str((settings.get("owner") or {}).get("name") or "").split())
        if name:
            lines.append("The person you live with is called %s." % name)
        cid = self.active_id()
        lines += [s["line"] for s in self.specials(now, cid)]
        if self.weather.on(settings):
            weather_line = weather_module.describe(self.weather.now(settings, self.timezone(), now))
            if weather_line:
                lines.append(weather_line)
            self._weather_step(now, needed=True)
        if cid:
            memory = self.conversation.memory if self.conversation is not None else self.memory(cid)
            plans = memory.plans_on(datetime.date.fromtimestamp(now))
            if plans:
                lines.append("Their plans for today, from what they told you: %s." % "; ".join(plans[:6]))
        return "\n".join(lines)

    def _weather_step(self, now=None, needed=False):
        """Fetch the forecast in the background when it is due and something shows it: the clock with the
        weather, on a lit screen, or (`needed`) a reply that is told it."""
        settings = self.store.settings()
        if not self.weather.on(settings) or (self._weather_task is not None and not self._weather_task.done()):
            return
        clock = settings.get("clock") or {}
        shown = clock.get("show", "off") != "off" and clock.get("weather", True) and self.screen_mode != "off"
        if (needed or shown) and self.weather.due(settings, self.timezone(), now):
            self._weather_task = asyncio.ensure_future(self.weather.refresh(settings, self.timezone()))

    async def weather_now(self, wait=WEATHER_WAIT):
        """The weather summed up, fetching it first if it is due (waiting at most `wait` seconds); or None."""
        settings = self.store.settings()
        if not self.weather.on(settings):
            return None
        try:
            await asyncio.wait_for(self.weather.refresh(settings, self.timezone()), wait)
        except asyncio.TimeoutError:
            pass
        return self.weather.now(settings, self.timezone())

    def _search_function(self, settings):
        """Search for models without their own: only with a search key, and not for Claude (it has its own)."""
        if not settings["conversation"].get("web_search", True) or settings["llm"].get("provider") == "anthropic":
            return None
        if self.provider_factory:
            return self.provider_factory("search", self)
        key = self.store.secret("search")
        if not key:
            return None
        from .search import TavilySearch
        return TavilySearch(key).search

    def _conversation_event(self, kind, data):
        if kind in ("heard", "speaking first", "saying"):
            self.activity()
        if kind == "heard" and self.rings:
            self.stop_ringing("answered")         # they spoke to it: the alarm has done its job
        if kind == "heard":
            self.set_state("thinking")
            captions = self.store.settings().get("captions", {})
            if captions.get("on") and captions.get("heard") and (data or {}).get("text"):
                self.overlays.heard(data["text"])
                self.clock_step()                 # the clock makes room for what it heard
        elif kind == "saying":
            self.set_state("speaking")
        elif kind == "replied":
            self.last_talk = time.time()
            self.last_activity = time.time()
            if self._wake_session and self.listening:
                self.answer_until = time.time() + FOLLOW_UP_WINDOW     # a follow-up without the wake word
            self.set_state("listening" if self.listening else "idle")
            self.overlays.caption_done()
            asyncio.ensure_future(self.keep_memory())
        self.emit(kind, data)

    def _caption(self, text):
        """A sentence's sound starts: write it on the screen, if the owner wants captions."""
        settings = self.store.settings()
        captions = settings.get("captions", {})
        if captions.get("on") and text:
            self.overlays.caption(text, size=captions.get("size", "normal"),
                                  speed=float(settings["tts"].get("speed") or 1.0))

    async def keep_memory(self, cid=None, now=None, force=False):
        """Bring a character's memory up to date if anything is due: a conversation that is over summed
        up (with its memories), the early part of a long one, the diary. One at a time, never while the
        character is talking, and after a failure not again for a while (unless `force`)."""
        settings = self.store.settings()["conversation"]
        if not settings.get("memory", True) or self.summarising:
            return None
        if (self.conversation is not None and self.conversation.busy) or (not force and time.time() < self.memory_retry):
            return None
        cid = cid or self.active_id()
        if not cid:
            return None
        memory = self.memory(cid)
        memory.close_stale(now)               # needs no model: a conversation ends on time even without one
        keep = int(settings.get("history_turns", 12))
        if not memory.needs_work(now, keep=keep):
            return None
        self.summarising = True
        try:
            name = self.characters.manifest(cid).get("name", "")
            persona, _ = split_persona(self.characters.persona(cid))
            llm = self.build("llm")
            # Careful note-taking, not lively talk: a low temperature, and room for a full answer.
            if isinstance(getattr(llm, "temperature", None), (int, float)):
                llm.temperature = min(llm.temperature, MEMORY_TEMPERATURE)
            if isinstance(getattr(llm, "max_tokens", None), int):
                llm.max_tokens = max(llm.max_tokens, MEMORY_MAX_TOKENS)
            rules = family.MEMORY_RULE if self.store.settings().get("family_safe", {}).get("on") else ""
            done = await memory.process(llm, now=now, name=name, persona=persona, keep=keep, rules=rules)
            if done["summed"] or done["diary"]:
                self.emit("memory", dict(done, character=cid))
            return done
        except ProviderError as error:
            self.memory_retry = time.time() + 15 * 60
            self.emit("error", {"message": "the memory was not brought up to date: %s" % error})
            return None
        finally:
            self.summarising = False

    async def memory_watch(self, every=60.0):
        """Keep every character's memory up to date: conversations that have gone quiet are summed up
        (so short chats, and the last turns before a restart or a change of character, are remembered
        too), and each day's diary is written."""
        while True:
            await asyncio.sleep(every)
            try:
                for cid in self.characters.ids():
                    await self.keep_memory(cid)
            except Exception:
                log.exception("memory watch failed")

    # ----- software updates (docs/32-updates.md)
    async def install_update(self):
        """Fetch the update found (if not already) and ask the installer to install it. The service
        restarts on the new version; if that does not start properly, the installer goes back."""
        if self.updating:
            return None
        self.updating = True
        try:
            status = self.updates.status()
            if not status["ready"]:
                found = status["found"] or await self.updates.check()
                if not found:
                    raise updates_module.UpdateError("there is no newer version")
                self.emit("update", {"did": "downloading", "version": found["version"]})
                await self.updates.download(found)
            version = self.updates.request_install()
            self.emit("update", {"did": "installing", "version": version})
            self.show_card(screen.message_card("Updating", "Installing version %s…" % version))
            return version
        except updates_module.UpdateError as error:
            self.emit("error", {"message": "update: %s" % error})
            raise
        finally:
            self.updating = False

    async def update_step(self, now=None):
        """One pass of keeping the software up to date: look for an update twice a day, fetch it, and
        install it at night (2 to 5 am) when nobody has talked for half an hour. Returns what it did."""
        now = now or time.time()
        settings = self.store.settings().get("updates", {})
        status = self.updates.status()
        if not settings.get("check", True) or not status["online"] or self.updating:
            return None
        try:
            found = status["found"]
            if now - (status.get("checked") or 0) >= UPDATE_CHECK_EVERY:
                found = await self.updates.check()
                if found:
                    self.emit("update", {"did": "found", "version": found["version"]})
            if not settings.get("auto_install", True) or not (found or status["ready"]):
                return "found" if found else None
            hour = datetime.datetime.fromtimestamp(now).hour
            quiet = now - self.last_talk > 30 * 60 and self.state == "idle"
            if 2 <= hour < 5 and quiet:
                await self.install_update()
                return "installing"
            if found and not status["ready"]:
                self.updating = True
                try:
                    await self.updates.download(found)
                finally:
                    self.updating = False
                return "downloaded"
            return "waiting"
        except updates_module.UpdateError as error:
            self.emit("error", {"message": "update: %s" % error})
            return None

    async def update_watch(self, every=30 * 60):
        await asyncio.sleep(10 * 60 + random.random() * 600)      # devices sold together do not all ask at once
        while True:
            try:
                await self.update_step()
            except Exception:
                log.exception("update watch failed")
            await asyncio.sleep(every)

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.emit("state", {"state": state})
            try:
                self.clock_step()            # the clock goes while someone talks to the character, and comes back
            except Exception:
                log.exception("the clock could not be shown")

    # ----- talking
    async def say(self, text):
        """Reply to typed or recognised words."""
        text = (text or "").strip()[:2000]
        if not text:
            return None
        try:
            self.conversation = self.conversation or self.make_conversation()
        except ProviderError as error:
            self.emit("error", {"message": str(error)})
            await self.notice()
            return None
        return await self.conversation.start(text)

    # ----- speaking first (docs/31-memory.md)
    async def greet(self, now=None):
        """Speak first, when someone seems to be back after a while: a short greeting, from what the
        character remembers (how long it has been, plans whose day has come). Only when the owner has
        turned it on, outside quiet hours, at most once a while, and never over a conversation."""
        now = now or time.time()
        opts = self.store.settings().get("speak_first", {})
        if not opts.get("on") or self.muted or self.state not in ("idle",) or \
                (self.conversation is not None and self.conversation.busy):
            return None
        if within_hours(now, opts.get("quiet_from", 22), opts.get("quiet_to", 8)):
            return None
        cid = self.active_id()
        if not cid or self.rings:
            return None
        last = max(self.memory(cid).last_turn_time(), self.last_greeting)
        apart = now - last
        hour = datetime.datetime.fromtimestamp(now).hour
        # The first time it hears someone in the morning, it says good morning, however short the night was.
        morning = bool(opts.get("morning", True)) and MORNING_HOURS[0] <= hour < MORNING_HOURS[1] and \
            (not last or talk_day(last) != talk_day(now))
        if not morning and apart < float(opts.get("after_hours", 3)) * 3600:
            return None
        try:
            self.conversation = self.conversation or self.make_conversation()
        except ProviderError:
            return None
        self.last_greeting = now
        days = apart / 86400
        gap = ("about %d days" % round(days)) if days >= 1.5 else ("about %d hours" % max(1, round(apart / 3600))) \
            if apart < 30 * 86400 else "a long time"
        self.emit("speaking first", {"apart": round(apart), "morning": morning})
        if morning:
            await self.weather_now()           # the weather it mentions, fetched now if it is due
            if self.conversation is None or self.conversation.busy or self.rings:
                return None                    # someone started talking meanwhile
        task = self.conversation.start("", prompt=MORNING_PROMPT if morning else GREETING_PROMPT % gap)
        reply = await task
        if reply is not None and not reply.error and not reply.interrupted and \
                self.store.settings()["listening"].get("mode", "auto") == "auto" and not self.listening:
            # Listen a little while for an answer, so they can just reply.
            self.listening = True
            self.answer_until = time.time() + ANSWER_WINDOW
            self.set_state("listening")
            self.stage.state("listening")
        return reply

    async def hear(self, pcm):
        """Recognise a recording and reply to it."""
        self.set_state("thinking")
        self.stage.state("thinking")
        try:
            text = await self.build("stt").transcribe(pcm)
        except ProviderError as error:
            self.emit("error", {"message": "speech recognition: %s" % error})
            self.set_state("listening" if self.listening else "idle")
            self.stage.state("listening" if self.listening else "idle")
            await self.notice()
            return None
        if self._strip_wake and text:
            # It woke in the middle of this: "hey Mika, what time is it?" asks the question at once;
            # "hey Mika" alone waits for it.
            self._strip_wake = False
            text = wakeword.after_wake(text, self._wake_for[0] if self._wake_for else [])
            if not text:
                self.answer_until = time.time() + WAKE_WINDOW
        if not text:
            self.set_state("listening" if self.listening else "idle")
            self.stage.state("listening" if self.listening else "idle")
            return None
        return await self.say(text)

    def interrupt(self):
        if self.conversation is not None:
            self.conversation.interrupt()

    async def notice(self):
        try:
            await self.speaker.play(tone(), 24000, lambda level: None)
        except Exception:
            pass

    # ----- the button
    def button(self):
        """One press. Interrupts speech; otherwise turns listening on or off (or ends a push-to-talk recording)."""
        mode = self.store.settings()["listening"].get("mode", "auto")
        self.activity()
        if hasattr(self.stage, "poke"):
            self.stage.poke()                 # someone is there
        if self.rings:
            # An alarm is ringing: it stops, and the character listens (to "five more minutes", say).
            self.stop_ringing()
            self.emit("button", {"did": "alarm stopped"})
            if mode == "auto" and not self.muted:
                self.listening = True
                self._wake_session = False
                self.answer_until = time.time() + ANSWER_WINDOW
                self.set_state("listening")
                self.stage.state("listening")
            return
        if self.conversation is not None and self.conversation.busy:
            self.interrupt()
            self.emit("button", {"did": "interrupt"})
            return
        if self.muted:
            self.overlays.hint(self._mute_hint(), seconds=4.0)
            self.emit("button", {"did": "nothing: the microphone is off"})
            return
        if mode == "push":
            if self.recording is None:
                self.recording = []
                self.set_state("listening")
                self.stage.state("listening")
                self.emit("button", {"did": "record"})
            else:
                pcm, self.recording = b"".join(self.recording), None
                self.emit("button", {"did": "send"})
                asyncio.ensure_future(self.hear(pcm))
            return
        self.listening = not self.listening
        self._wake_session = False
        self.answer_until = 0.0
        self.set_state("listening" if self.listening else "idle")
        self.stage.state("listening" if self.listening else "idle")
        self.emit("button", {"did": "listen on" if self.listening else "listen off"})

    async def listen_loop(self):
        """Feed the microphone to the speech detector while listening is on."""
        if self.microphone is None:
            return
        settings = self.store.settings()["listening"]
        detector = Detector(float(settings.get("end_silence", 0.8)), float(settings.get("max_turn", 30.0)))
        # Someone seems to be there (for speaking first): speech-like sound, from its loudness alone.
        presence = Detector(0.6, 8.0)
        while True:
            if self.muted:
                await asyncio.sleep(0.25)       # nothing is recorded while the microphone is off
                continue
            frames = self.microphone.frames()
            try:
                async for frame in frames:
                    if self.muted:
                        break                   # stops the recording program (below)
                    if self.recording is not None:
                        self.recording.append(frame)
                        self._heard(detector.loudness(frame))
                        continue
                    busy = self.state in ("thinking", "speaking")
                    barge_in = self.store.settings()["listening"].get("voice_interrupt", False)
                    if self.answer_until and self.listening and not detector.speaking and time.time() > self.answer_until \
                            and self.state == "listening":
                        self.answer_until = 0.0     # nothing more said: stop listening, as before
                        self._wake_session = False
                        self.listening = False
                        self.set_state("idle")
                        self.stage.state("idle")
                    if not self.listening or (busy and not barge_in):
                        detector.reset()          # without echo cancellation, ignore our own voice
                        wake = self.wake_listener() if not busy and not self.listening else None
                        speak_first = not busy and self.store.settings().get("speak_first", {}).get("on")
                        if wake is not None or speak_first:
                            # The sound in the room, utterance by utterance (presence, and what was
                            # being said when the wake words came).
                            sound = presence.feed(frame)
                            if speak_first and sound and len(sound) / 2 / 16000 >= PRESENCE_SECONDS:
                                asyncio.ensure_future(self.greet())
                        else:
                            presence.reset()
                        if wake is not None and wake.feed(frame):
                            detector = self._woken(presence)
                            presence = Detector(0.6, 8.0)
                            wake.reset()
                        continue
                    if detector.speaking:
                        self.answer_until = 0.0     # they answered: carry on as usual
                    was_speaking = detector.speaking
                    pcm = detector.feed(frame)
                    if self.state == "listening":
                        # The character hears the person: it looks up as they start, and nods in their pauses.
                        self._heard(detector.loudness(frame))
                    if busy and detector.speaking and not was_speaking:
                        # The person started talking over the character: stop it and keep listening.
                        self.interrupt()
                        self.emit("button", {"did": "interrupted by voice"})
                    if pcm:
                        asyncio.ensure_future(self.hear(pcm))
            except Exception as error:
                self.emit("error", {"message": "microphone: %s" % error})
            finally:
                try:
                    await frames.aclose()
                except Exception:
                    pass
            if not self.muted:
                await asyncio.sleep(2.0)

    def _heard(self, level):
        if hasattr(self.stage, "heard"):
            self.stage.heard(level)

    # ----- the wake word (docs/28-user-guide.md)
    def wake_words(self):
        """The words the wake word listens for now ([] when it is off)."""
        opts = self.store.settings().get("wake_word", {})
        cid = self.active_id()
        if not opts.get("on") or not cid:
            return []
        return wakeword.phrases(self.characters.manifest(cid).get("name", ""), opts.get("phrase", ""))

    def wake_listener(self):
        """The wake-word listener, or None (off, the microphone off, or not ready yet). Built in the
        background the first time, and again when the words or the sensitivity change."""
        if self.muted or self._wake_for is False:
            return None
        if self._wake is not None and self._wake_for is not None:
            return self._wake
        if self._wake_for is None and not self._wake_building:
            self._wake_building = True
            asyncio.ensure_future(self._build_wake())
        return None

    async def _build_wake(self):
        try:
            words = self.wake_words()
            sensitivity = self.store.settings().get("wake_word", {}).get("sensitivity", "normal")
            if not words:
                self._wake, self._wake_for, self.wake_error = None, False, ""
                return
            try:
                self._wake = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: wakeword.WakeWord(self.model_dir("wake"), words, sensitivity))
                self._wake_for, self.wake_error = (words, sensitivity), ""
                self.emit("device", {"wake_word": "listening for " + ", ".join(w.title() for w in words)})
            except wakeword.WakeWordError as error:
                self._wake, self._wake_for, self.wake_error = None, False, str(error)
                self.emit("error", {"message": "the wake word cannot listen: %s" % error})
        finally:
            self._wake_building = False

    def reload_wake(self):
        """The wake word's settings, or the character (its name), changed: build it again when next needed."""
        self._wake, self._wake_for = None, None

    def _woken(self, presence):
        """The wake words were heard: listen. What was being said goes on (in one breath, "hey Mika, what
        time is it?"); the words themselves are taken off what was heard (hear). Returns the detector to
        carry on with."""
        self.emit("wake word", {"phrase": " / ".join(w.title() for w in (self._wake_for or [[]])[0])})
        self.activity()
        if hasattr(self.stage, "poke"):
            self.stage.poke()
        self.listening = True
        self._wake_session = True
        self.answer_until = time.time() + WAKE_WINDOW
        self.set_state("listening")
        self.stage.state("listening")
        settings = self.store.settings()["listening"]
        detector = Detector(float(settings.get("end_silence", 0.8)), float(settings.get("max_turn", 30.0)))
        if presence.speaking:
            detector.speaking, detector.frames, detector.noise = True, list(presence.frames), presence.noise
            self._strip_wake = True
        return detector

    # ----- the button held: the microphone off or on, or a setup reset
    def button_down(self):
        """The button went down. While it is held, the screen says what letting go will do."""
        self.activity()
        if self._hold is not None:
            self._hold.cancel()
        self._hold = asyncio.ensure_future(self._hold_hints())

    async def _hold_hints(self):
        try:
            await asyncio.sleep(MUTE_HOLD)
            self.overlays.hint("Let go to turn the microphone %s" % ("on" if self.muted else "off"))
            await asyncio.sleep(RESET_WARN - MUTE_HOLD)
            self.overlays.hint("Keep holding to forget the Wi-Fi and the settings-page password")
            await asyncio.sleep(RESET_HOLD - RESET_WARN)
            self.overlays.hint("Let go now to forget the Wi-Fi and the settings-page password")
        except asyncio.CancelledError:
            pass

    def button_up(self, held):
        """The button was let go after `held` seconds: a press; from MUTE_HOLD the microphone off or on;
        from RESET_HOLD a setup reset; in between, nothing."""
        if self._hold is not None:
            self._hold.cancel()
            self._hold = None
        if held < MUTE_HOLD:
            self.overlays.hide(HINT)
            self.button()
        elif held < RESET_WARN:
            self.overlays.hide(HINT)
            if self.mic_switch:
                self.overlays.hint("The mute switch is on: slide it to turn the microphone on", seconds=4.0)
            else:
                self.set_muted(not self._mute_choice)
                self.overlays.hint("Microphone off" if self.muted else "Microphone on", seconds=2.0)
        elif held < RESET_HOLD:
            self.overlays.hint("Nothing was changed", seconds=2.0)
        else:
            self.overlays.hide(HINT)
            asyncio.ensure_future(self.setup_reset())

    # ----- the microphone off (docs/28-user-guide.md)
    @property
    def muted(self):
        return bool(self.mic_switch) or self._mute_choice

    def _mute_hint(self):
        if self.mic_switch:
            return "The microphone is off. Slide the mute switch to turn it on."
        return "The microphone is off. Hold the button for two seconds to turn it on."

    def set_muted(self, muted):
        """Turn the microphone off or on, as the owner asks (a mute switch, while on, wins). Kept across
        restarts."""
        self._mute_choice = bool(muted)
        self.store.update_settings({"microphone": {"muted": self._mute_choice}})
        self._apply_mute()

    def set_switch(self, on):
        """The mute switch moved (True: the microphone off)."""
        self.mic_switch = bool(on)
        self._apply_mute()
        self.overlays.hint("Microphone off" if self.muted else "Microphone on", seconds=2.0)

    def _apply_mute(self):
        muted = self.muted
        if muted:
            # Nothing is heard from now on: listening stops, and so does the recording program.
            self.listening = False
            self.recording = None
            self.answer_until = 0.0
            if self.state == "listening":
                self.set_state("idle")
                self.stage.state("idle")
        self.overlays.microphone(muted)
        self.emit("microphone", {"muted": muted, "switch": self.mic_switch})

    # ----- the screen at night (docs/28-user-guide.md)
    def activity(self):
        """Someone is there (the button, speech, the wake word, an alarm): a sleeping screen wakes."""
        self.last_activity = time.time()
        if self.screen_mode != "on":
            self.set_screen("on")

    def set_screen(self, mode):
        """Light the screen, dim it or darken it: the stage draws a veil, and the backlight follows where
        it can be set."""
        self.screen_mode = mode
        self.stage.send({"screen": mode})
        brightness = float(self.store.settings().get("screen", {}).get("brightness", 100)) / 100.0
        self.backlight.set({"on": brightness, "dim": DIM_BACKLIGHT, "off": 0.0}[mode])
        self.emit("screen", {"screen": mode})

    def screen_step(self, now=None):
        """Sleep in the screen's hours once nothing has happened for a while; otherwise stay lit."""
        now = now or time.time()
        opts = self.store.settings().get("screen", {})
        want = "on"
        if opts.get("sleep") and within_hours(now, opts.get("sleep_from", 23), opts.get("sleep_to", 7)):
            busy = self.state != "idle" or self.listening or (self.conversation is not None and self.conversation.busy)
            if not busy and now - self.last_activity >= SCREEN_IDLE:
                want = opts.get("how", "off")
        if want != self.screen_mode:
            self.set_screen(want)
        return want

    async def screen_watch(self, every=15.0):
        while True:
            await asyncio.sleep(every)
            try:
                self.screen_step()
            except Exception:
                log.exception("screen watch failed")

    def settings_changed(self, old, new):
        """Put changed settings into effect (the settings page saved them)."""
        if old.get("microphone") != new.get("microphone"):
            self._mute_choice = bool(new.get("microphone", {}).get("muted"))
            self._apply_mute()
        if old.get("screen") != new.get("screen"):
            if self.screen_mode == "on":
                self.backlight.set(float(new["screen"].get("brightness", 100)) / 100.0)
            self.screen_step()
        if old.get("wake_word") != new.get("wake_word") or old.get("active_character") != new.get("active_character"):
            self.reload_wake()
        captions = new.get("captions", {})
        if not captions.get("on"):
            self.overlays.hide(CAPTION)
            self.overlays.hide(HEARD)
        elif not captions.get("heard"):
            self.overlays.hide(HEARD)
        if any(old.get(k) != new.get(k) for k in ("clock", "weather")):
            self.clock_shown = None if old.get("clock") != new.get("clock") else self.clock_shown
            self.clock_step()
            self._weather_step(needed=True)
        if any(old.get(k) != new.get(k) for k in ("special_days", "owner", "background")):
            self.show_background()

    # ----- timers and alarms
    def action(self, action):
        kind = action.get("kind")
        label = action.get("label") or kind
        if kind == "timer":
            self._schedule(label, time.time() + action["seconds"], "timer")
        elif kind == "alarm":
            hour, minute = (int(x) for x in action["time"].split(":"))
            now = datetime.datetime.now()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due += datetime.timedelta(days=1)
            self._schedule(label, due.timestamp(), "alarm")
        elif kind == "cancel":
            for key in [k for k in self.timers if label.lower() in k.lower()]:
                self.timers.pop(key)[1].cancel()
                self.emit("timer", {"label": key, "did": "cancelled"})
        self._save_timers()

    def _schedule(self, label, due, kind):
        if label in self.timers:
            self.timers.pop(label)[1].cancel()
        handle = asyncio.get_running_loop().call_later(max(0.0, due - time.time()), self._ring, label, kind)
        self.timers[label] = (due, handle, kind)
        self.emit("timer", {"label": label, "due": round(due), "type": kind})

    def _ring(self, label, kind, times=None):
        """A timer or alarm has come (or a daily wake-up, kind "wake"): the character says so itself, and again,
        a little louder each time, until someone answers (docs/28-user-guide.md, "Your day"). Returns the task."""
        self.timers.pop(label, None)
        self._save_timers()
        if kind == "alarm" and "wake" in label.lower():
            kind = "wake"
        self.emit("timer", {"label": label, "did": "rang", "type": kind})
        if label in self.rings:
            return self.rings[label]
        task = asyncio.ensure_future(self._ringing(label, kind, times))
        self.rings[label] = task
        return task

    async def _ringing(self, label, kind, times=None):
        settings = self.store.settings()
        if times is None:
            times = RING_TIMES.get(kind, 3) if (settings.get("alarms") or {}).get("repeat", True) else 1
        if self._answered is None:
            self._answered, self._ring_lock = asyncio.Event(), asyncio.Lock()
        self._answered.clear()
        answered = False
        try:
            for attempt in range(times):
                self.activity()
                self.overlays.hint(ring_hint(label, kind))
                await self._wait_quiet()
                await self._ring_call(label, kind, attempt)
                last = attempt + 1 == times
                if self._answered.is_set() or await self._wait_answer(ANSWER_WINDOW if last else RING_AGAIN.get(kind, 30.0)):
                    answered = True
                    break
            if not answered:
                self.emit("timer", {"label": label, "did": "not answered", "type": kind})
        except asyncio.CancelledError:
            pass                                  # stopped: the button, or they spoke
        finally:
            self.speaker.volume = float(self.store.settings().get("volume", 0.8))
            if self.rings.get(label) is asyncio.current_task():
                self.rings.pop(label, None)
            if not self.rings:
                self.overlays.hide(HINT)

    async def _wait_quiet(self, limit=RING_WAIT):
        """Let a reply in progress end (for up to `limit` seconds) before an alarm speaks over it."""
        end = time.time() + limit
        while self.conversation is not None and self.conversation.busy and time.time() < end:
            await asyncio.sleep(0.25)

    async def _ring_call(self, label, kind, attempt):
        """One call: the first in the character's own words, from the language model (the time, and for a
        wake-up the weather and the day's plans); a fixed line when that fails, and for the calls after it; in
        the device's own voice if the character's cannot be reached; tones if no voice can."""
        settings = self.store.settings()
        name = " ".join(str((settings.get("owner") or {}).get("name") or "").split())
        now = time.time()
        weather_line = ""
        if kind == "wake" and attempt == 0:
            weather_line = weather_module.spoken(await self.weather_now())
        louder = RING_LOUDER * attempt if kind != "timer" else 0.0
        async with self._ring_lock:
            try:
                self.conversation = self.conversation or self.make_conversation()
            except ProviderError as error:
                self.emit("error", {"message": "the alarm: %s" % error})
                self.conversation = None
            self.speaker.volume = min(1.0, float(settings.get("volume", 0.8)) + louder)
            if attempt == 0 and self.conversation is not None:
                reply = await self.conversation.start("", prompt=RING_PROMPTS[kind] % {
                    "time": weather_module.clock_words(now), "label": label})
                if reply is not None and (reply.interrupted or (reply.spoken and not reply.error)):
                    return
            if await self._say_fixed(ring_line(kind, label, now, name, attempt, weather_line)):
                return
            for _ in range(3):
                await self.notice()

    def _voices(self):
        """The voices an alarm can be said in, best first: the character's own, then the device's (no internet)."""
        cid = self.active_id()
        own = self.characters.device_settings(cid) if cid else {}
        out = []
        for extra in ({"voice": own.get("voice") or None, "provider": own.get("voice_provider") or None},
                      {"provider": "local"}):
            try:
                voice = self.build("tts", **extra)
            except Exception as error:                 # no key, no model on this machine
                log.info("no voice for the alarm (%s): %s", extra.get("provider") or "the usual one", error)
                continue
            if voice is not None and all(voice is not v for v in out):
                out.append(voice)
        return out

    async def _say_fixed(self, line):
        """Say a line without the language model. False if no voice could say it."""
        cid = self.active_id()
        for voice in self._voices():
            talker = self.conversation or Conversation(
                llm=None, tts=voice, speaker=self.speaker, stage=self.stage, memory=self.memory(cid),
                persona=self.characters.persona(cid) if cid else "", on_event=self._conversation_event,
                on_caption=self._caption)
            try:
                reply = await talker.say_line(line, "happy", tts=voice)
            except ProviderError as error:
                self.emit("error", {"message": "the alarm's voice: %s" % error})
                continue
            if reply.spoken or reply.interrupted:
                self.emit("replied", {"text": " ".join(reply.spoken), "interrupted": reply.interrupted, "error": None})
                self.last_talk = time.time()
                self.set_state("listening" if self.listening else "idle")
                self.overlays.caption_done()
                return True
        return False

    async def _wait_answer(self, seconds):
        """Listen for an answer after a call. True if someone answered (they spoke, or pressed the button)."""
        if not self.muted and self.store.settings()["listening"].get("mode", "auto") == "auto" and not self.listening:
            self.listening = True
            self.answer_until = time.time() + seconds
            self.set_state("listening")
            self.stage.state("listening")
        try:
            await asyncio.wait_for(self._answered.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            if self.listening and self.answer_until and time.time() >= self.answer_until - 0.5 \
                    and not self._answered.is_set() and self.state == "listening":
                self.listening, self.answer_until = False, 0.0
                self.set_state("idle")
                self.stage.state("idle")

    def stop_ringing(self, why="stopped"):
        """Someone answered: every alarm ringing stops. Returns whether one was."""
        if not self.rings:
            return False
        if self._answered is not None:
            self._answered.set()
        rings, self.rings = self.rings, {}
        for label, task in rings.items():
            if task is not asyncio.current_task():
                task.cancel()
            self.emit("timer", {"label": label, "did": why})
        if why != "answered":
            self.interrupt()
        self.overlays.hide(HINT)
        return True

    # ----- daily wake-ups (settings "alarms")
    def _wake_rung_path(self):
        return os.path.join(self.data_dir, "wake-ups.json")

    def wake_step(self, now=None):
        """Ring each daily wake-up whose time has come: on its days, once a day, and only within five minutes
        of its time (one missed while the device was off is not rung hours late). Returns the labels rung."""
        now = now or time.time()
        local = datetime.datetime.fromtimestamp(now)
        wake_ups = (self.store.settings().get("alarms") or {}).get("wake_ups") or []
        try:
            with open(self._wake_rung_path(), encoding="utf-8") as f:
                rung = json.load(f)
        except (OSError, ValueError):
            rung = {}
        today, out = local.date().isoformat(), []
        for w in wake_ups:
            try:
                hour, minute = (int(x) for x in str(w.get("time", "")).split(":"))
                due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                continue
            key = str(w.get("id") or w.get("time"))
            if not w.get("on", True) or local.weekday() not in (w.get("days") or range(7)) or rung.get(key) == today:
                continue
            if due <= local < due + datetime.timedelta(seconds=WAKE_LATE):
                rung[key] = today
                out.append(w.get("label") or "wake-up at %s" % w.get("time"))
        if out:
            write_atomic(self._wake_rung_path(), json.dumps(rung))
            for label in out:
                self._ring(label, "wake")
        return out

    def next_wake_up(self, now=None):
        """The next daily wake-up: {"label", "at"} (seconds), or None."""
        now = now or time.time()
        local = datetime.datetime.fromtimestamp(now)
        best = None
        for w in (self.store.settings().get("alarms") or {}).get("wake_ups") or []:
            if not w.get("on", True):
                continue
            try:
                hour, minute = (int(x) for x in str(w.get("time", "")).split(":"))
            except ValueError:
                continue
            for ahead in range(8):
                day = local + datetime.timedelta(days=ahead)
                at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if at > local and day.weekday() in (w.get("days") or range(7)):
                    if best is None or at.timestamp() < best["at"]:
                        best = {"label": w.get("label") or "wake-up", "at": round(at.timestamp())}
                    break
        return best

    # ----- the clock over the character (settings "clock")
    def clock_step(self, now=None):
        """Show, change or hide the clock over the character: the time, the date and the weather; while nobody
        is talking ("idle") or all the time ("always"). Returns what it shows, or None."""
        now = now or time.time()
        settings = self.store.settings()
        opts = settings.get("clock") or {}
        show = opts.get("show", "off")
        wanted = show == "always" or (show == "idle" and self._quiet() and not self.listening)
        if HEARD in self.overlays.shown or self.screen_mode == "off" or not self.active_id() or \
                os.path.realpath(self.current_puppet) == os.path.realpath(self.card):
            wanted = False
        if not wanted:
            if self.clock_shown is not None:
                self.overlays.hide(CLOCK)
                self.clock_shown = None
            return None
        local = datetime.datetime.fromtimestamp(now)
        hours = opts.get("hours", "auto")
        twelve = hours == "12" or (hours == "auto" and weather_module.guess_12_hour(self.timezone()))
        shown_time = "%d:%02d" % ((local.hour + 11) % 12 + 1, local.minute) if twelve else "%02d:%02d" % (local.hour, local.minute)
        suffix = ("am" if local.hour < 12 else "pm") if twelve else ""
        date = "%s %d %s" % (local.strftime("%a"), local.day, local.strftime("%b"))
        glance = None
        if opts.get("weather", True) and self.weather.on(settings):
            got = self.weather.now(settings, self.timezone(), now)
            if got:
                glance = (got["now"]["icon"], got["now"]["temp"])
        key = (shown_time, suffix, date, glance, tuple(self.overlays.screen))
        if key != self.clock_shown:
            self.overlays.clock(shown_time, suffix, date, glance)
            self.clock_shown = key
        return key

    def cancel_timer(self, label):
        """Cancel one timer or alarm set by voice, by its exact label. False if there is none."""
        entry = self.timers.pop(label, None)
        if entry is None:
            return False
        entry[1].cancel()
        self._save_timers()
        self.emit("timer", {"label": label, "did": "cancelled"})
        return True

    def _save_timers(self):
        data = [{"label": k, "due": v[0], "kind": v[2]} for k, v in self.timers.items()]
        write_atomic(os.path.join(self.data_dir, "timers.json"), json.dumps(data))

    def restore_timers(self):
        try:
            with open(os.path.join(self.data_dir, "timers.json"), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        for item in saved:
            if item["due"] > time.time() - 60:
                self._schedule(item["label"], item["due"], item.get("kind", "timer"))

    # ----- status for the console
    def status(self):
        cid = self.active_id()
        settings = self.store.settings()
        return {
            "state": self.state,
            "listening": self.listening,
            "character": cid,
            "characters": len(self.characters.ids()),
            "providers": {k: settings[k].get("provider", "") for k in ("llm", "stt", "tts")},
            "keys": self.store.secret_status(),
            "timers": [{"label": k, "due": round(v[0]), "kind": v[2]} for k, v in self.timers.items()],
            "ringing": sorted(self.rings),
            "uptime": round(time.time() - self.started),
            "stage_connected": getattr(self.stage, "writer", True) is not None,
            "timezone": self.timezone(),
            "muted": self.muted,
            "mute_switch": self.mic_switch,
            "screen": self.screen_mode,
            "backlight": self.backlight.available,
            "usb": usb_status(),
            # The data partition was damaged beyond repair and made again (device/readonly/companion-data-check).
            "storage_recovered": os.path.exists(os.path.join(self.data_dir, "storage-recovered")),
            "wake_word": {"words": [w.title() for w in self.wake_words()],
                          "ready": self._wake is not None, "error": self.wake_error},
            # timedatectl needs systemd running (true on the device, not in every test machine)
            "can_set_timezone": shutil.which("timedatectl") is not None and os.path.isdir("/run/systemd/system"),
        }

    def start(self):
        """Start background work. Call from inside the event loop."""
        if hasattr(self.stage, "start"):
            self.stage.start()
        cid = self.active_id()
        if cid and not os.path.exists(self.current_puppet):
            self._link_puppet(cid)
        if not cid:
            write_atomic(self.card, screen.card_puppet(screen.setup_card(getattr(self, "setup_link", None))))
            self._point_stage_at(self.card)
        self.restore_timers()
        if self.microphone is not None:
            asyncio.ensure_future(self.listen_loop())
        if self.wifi is not None:
            asyncio.ensure_future(self.network_watch())
        if isinstance(self.stage, StageLink):
            asyncio.ensure_future(self.stage_watch())
        asyncio.ensure_future(self.memory_watch())
        asyncio.ensure_future(self.clock_watch())
        if getattr(self, "updates", True):
            asyncio.ensure_future(self.update_watch())
        asyncio.ensure_future(self.screen_watch())
        self.backlight.set(float(self.store.settings().get("screen", {}).get("brightness", 100)) / 100.0)

    # ----- Wi-Fi
    async def network_watch(self, wait=40.0, retry=180.0, busy=120.0):
        """Keep the device on a network, opening a setup hotspot when it has none.

        With no network a while after starting, the device opens its setup
        hotspot. If it has joined a home network before (the usual case after
        a power cut, when the router starts more slowly than the device), it
        closes the hotspot every few minutes to try that network again, unless
        someone is using the settings page through the hotspot right now.
        """
        while True:
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    if await self.wifi.connected():
                        await self._connected()
                        return
                except NetworkError as error:
                    self.emit("error", {"message": "network: %s" % error})
                    return
                await asyncio.sleep(2.0)
            try:
                saved = await self.wifi.saved_networks()
                if self.wifi.hotspot is None:
                    suffix = (await self._serial())[-4:].upper() or "0000"
                    ssid, password = await self.wifi.start_hotspot(suffix)
                    self.emit("network", {"hotspot": ssid})
                    self.show_card(screen.hotspot_card(ssid, password))
            except NetworkError as error:
                self.emit("error", {"message": "could not open the setup hotspot: %s" % error})
                return
            if not saved:
                return                      # a new device: wait in the hotspot for the owner
            while True:
                await asyncio.sleep(retry)
                if self.wifi.hotspot is None:
                    return                  # the owner joined a network meanwhile
                if time.time() - self.last_use > busy:
                    break
            await self.wifi.stop_hotspot()  # let NetworkManager try the saved networks again
            self.emit("network", {"retrying": saved})

    async def _connected(self):
        address = await self.wifi.address()
        self.emit("network", {"connected": True, "address": address})
        if not self.active_id():
            self.show_card(screen.setup_card(address))

    async def _serial(self):
        try:
            with open("/proc/device-tree/serial-number", "rb") as f:
                return f.read().strip(b"\0").decode() or "0000"
        except OSError:
            return "%04x" % (os.getpid() & 0xffff)

    async def join_wifi(self, ssid, password):
        self.show_card(screen.message_card("Joining Wi-Fi", "Connecting to %s…" % ssid))
        try:
            await self.wifi.join(ssid, password)
        except NetworkError as error:
            ssid_hs, pw = await self.wifi.start_hotspot((await self._serial())[-4:].upper() or "0000")
            self.show_card(screen.hotspot_card(ssid_hs, pw))
            raise
        await self._connected()
        if self.active_id():
            self.show_character()


USB_ADDRESS = "10.12.194.1"


def usb_status(net="/sys/class/net/usb0"):
    """The USB connection to a computer (device/usb/companion-usb-gadget): whether the board has one, and
    whether a computer is connected now."""
    if not os.path.isdir(net):
        return {"available": False}
    try:
        with open(os.path.join(net, "carrier")) as f:
            connected = f.read().strip() == "1"
    except OSError:                  # the link is down: no computer
        connected = False
    return {"available": True, "connected": connected, "address": "http://%s" % USB_ADDRESS}


def within_hours(now, start, end):
    """Whether the hour at `now` is from `start` until `end` o'clock (which may pass midnight; equal
    hours mean never)."""
    hour = datetime.datetime.fromtimestamp(now).hour
    start, end = int(start), int(end)
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


HOUR_WORDS = ("twelve", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven")
FIVES = {5: "five", 10: "ten", 20: "twenty", 25: "twenty-five"}


def time_in_words(t):
    """A time as people say it aloud: "seven o'clock", "quarter past seven", "twenty to eight", "3 minutes past seven"."""
    local = datetime.datetime.fromtimestamp(t)
    hour, after, minute = HOUR_WORDS[local.hour % 12], HOUR_WORDS[(local.hour + 1) % 12], local.minute
    if minute == 0:
        return "%s o'clock" % hour
    if minute in (15, 30, 45):
        return {15: "quarter past %s" % hour, 30: "half past %s" % hour, 45: "quarter to %s" % after}[minute]
    if minute % 5 == 0:
        return "%s past %s" % (FIVES[minute], hour) if minute < 30 else "%s to %s" % (FIVES[60 - minute], after)
    if minute < 30:
        return "%d minute%s past %s" % (minute, "" if minute == 1 else "s", hour)
    return "%d minute%s to %s" % (60 - minute, "" if minute == 59 else "s", after)


def ring_line(kind, label, when, name="", attempt=0, weather_line=""):
    """What the character says when an alarm rings without the language model (it failed), and when it calls again."""
    t = time_in_words(when)
    call, comma = (name + "! ") if name else "", (", " + name) if name else ""
    plain = (label or "").strip().lower() in ("", "timer", "alarm", "wake up", "wake-up")
    if kind == "wake":
        if attempt == 0:
            return "Good morning%s! It's %s, time to get up.%s" % (comma, t, (" " + weather_line) if weather_line else "")
        again = ["%sTime to get up, it's %s." % (call, t), "Wakey wakey%s! It's %s." % (comma, t),
                 "%sAre you awake? It's already %s." % (call, t), "Rise and shine%s! It's %s." % (comma, t)]
        return again[(attempt - 1) % len(again)]
    if kind == "timer":
        what = "Your timer" if plain else "Your %s timer" % label
        return "%s%s is done." % (call, what) if attempt == 0 else "%s%s has finished!" % (call, what)
    return "%sIt's %s%s." % (call, t, "" if plain else ": time for " + label)


def ring_hint(label, kind):
    what = {"wake": "Wake-up", "timer": "Timer"}.get(kind, "Alarm")
    plain = (label or "").strip().lower() in ("", "timer", "alarm", "wake up", "wake-up")
    return "%s%s. Press the button to stop it." % (what, "" if plain else ": " + label)


async def _run_command(*args, timeout=30):
    """Run a system command; return its output or raise RuntimeError with its message."""
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("%s is not installed on this machine" % args[0])
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("%s took too long" % args[0])
    if proc.returncode != 0:
        raise RuntimeError(err.decode("utf-8", "replace").strip() or "%s failed" % args[0])
    return out.decode("utf-8", "replace")
