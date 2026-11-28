"""One spoken exchange: hear, think, speak, and let it be interrupted.

    words heard ─► language model (streamed) ─► sentences ─► voice (streamed) ─► speaker
                                                  │                                 │
                                                  └─ emotions, timers ─► stage      └─ loudness ─► mouth

The voice starts on the first sentence while the model is still writing the
rest, and the next sentence's audio is fetched while the current one plays.
Pressing the button (or speaking, with echo cancellation) interrupts: the
sound stops at once and what was actually said is remembered.
"""
import asyncio
import datetime
import functools
import logging
import random

from . import family
from .audio import FRAME, tone
from .characters import split_persona
from .performance import Planner, SyllableMarks, even_timing, guess_feeling, syllable_offsets
from .providers import ProviderError
from .search import results_for_model
from .speech_text import SEARCH_RULE, Splitter, speech_rules

# A later sentence's sound is held back (to place its syllables over all of it) only while there is more
# than this many seconds of earlier sound still to be heard.
HOLD_MARGIN = 0.4

log = logging.getLogger("companion.conversation")

FIRST_WORDS_TIMEOUT = 20.0   # seconds to wait for the model's first sentence
MOOD_LINGER = 6.0            # seconds a feeling stays after a reply ends, before calming to neutral
REPLY_TIMEOUT = 90.0         # longest a whole reply may take


def system_prompt(persona, remembered="", now=None, search_tag=False, gestures=("nod", "shake", "bow", "tilt"),
                  family_safe=False, today=""):
    """The model's instructions: the persona, how to speak, the time, what is special about today (`today`: the
    owner's name, special days, the weather, their plans; device.Device.today_text), and what the character
    remembers for this reply (memory.Memory.context: the owner's notes, its memories, the last conversation ...).
    In family-safe mode, its rules follow the persona and a reminder ends the instructions."""
    body, _ = split_persona(persona)
    now = now or datetime.datetime.now()
    rules = speech_rules(gestures) + ("\n" + SEARCH_RULE if search_tag else "")
    parts = [body, family.RULES if family_safe else "", rules, "It is now %s." % now.strftime("%A %d %B %Y, %H:%M"),
             (today or "").strip(), (remembered or "").strip(), family.REMINDER if family_safe else ""]
    return "\n\n".join(p for p in parts if p)


# Characters of text per token, roughly, for fitting what the model is given into its room.
CHARS_PER_TOKEN = 3.2


class Reply:
    """The state of the reply being spoken; read by the console and by interruptions."""

    def __init__(self, heard):
        self.heard = heard
        self.spoken = []
        self.interrupted = False
        self.error = None


class Conversation:
    def __init__(self, *, llm, tts, speaker, stage, memory, persona, history_turns=12,
                 on_event=None, on_action=None, on_audio=None, search=None, gestures=None, on_caption=None,
                 family_safe=False, today=None):
        self.llm = llm
        self.tts = tts
        self.speaker = speaker
        self.stage = stage
        self.memory = memory
        self.persona = persona
        self.history_turns = history_turns
        self.on_event = on_event or (lambda kind, data: None)
        self.on_action = on_action or (lambda action: None)
        self.on_audio = on_audio
        self.search = search            # async query -> results, for models without their own search
        # Gestures the character can make (told to the model); by default whatever the stage reports.
        self.gestures = gestures
        # Called with each sentence as its sound starts to be heard (captions on the screen).
        self.on_caption = on_caption
        # Family-safe mode: its rules in the instructions, and strong swearing taken out of what is said.
        self.family_safe = family_safe
        # Called for what is special about today (special days, the weather, their plans), for the instructions.
        self.today = today
        self.current = None
        self._task = None
        self.mood_linger = MOOD_LINGER
        self._calm = None
        # Mouth shapes and movements that go with each sentence's meaning (performance.py).
        self.planner = Planner(random.Random())

    def _calm_later(self):
        """Let the reply's feeling stay a little, then return to neutral (unless a new reply starts)."""
        async def calm():
            await asyncio.sleep(self.mood_linger)
            self.stage.express("neutral")
        self._calm = asyncio.ensure_future(calm())

    def _stop_calming(self):
        if self._calm is not None and not self._calm.done():
            self._calm.cancel()
        self._calm = None

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    def interrupt(self):
        """Stop speaking now. Safe to call at any time."""
        if self.current is not None:
            self.current.interrupted = True
        self.speaker.stop()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def start(self, heard, prompt=None):
        """Begin replying to what was heard (or, with `prompt` and nothing heard, speaking first), in the
        background. Returns the task."""
        self.interrupt()
        self._task = asyncio.ensure_future(self.respond(heard, prompt=prompt))
        return self._task

    async def respond(self, heard, prompt=None):
        """Reply to what was heard. With `prompt`, nothing was heard: the character speaks first, the
        model being told why in `prompt` (kept in memory as a turn where only the character spoke)."""
        self._stop_calming()
        reply = self.current = Reply(heard)
        self.on_event("speaking first" if prompt else "heard", {"text": heard})
        self.stage.state("thinking")
        gestures = self.gestures if self.gestures is not None else getattr(self.stage, "gestures", ("nod", "shake", "bow", "tilt"))
        gestures = [g for g in gestures if g not in getattr(self.stage, "disabled", ())]
        today = self._today()
        base = system_prompt(self.persona, "", search_tag=self.search is not None, gestures=gestures,
                             family_safe=self.family_safe, today=today)
        asked = prompt or heard
        remembered, past = self.memory.context(heard, room=self.room(len(base) + len(asked)), verbatim=self.history_turns)
        messages = past + [{"role": "user", "content": prompt or heard}]
        system = system_prompt(self.persona, remembered, search_tag=self.search is not None, gestures=gestures,
                               family_safe=self.family_safe, today=today)
        writer = None
        try:
            queries = []
            for round_ in range(2):          # a reply, and at most one answer from search results
                sentences = asyncio.Queue()
                writer = asyncio.ensure_future(self._write(system, messages, sentences, reply))
                queries = []
                await asyncio.wait_for(self._speak(sentences, reply, queries), max(REPLY_TIMEOUT, self.patience + 60))
                if not (queries and self.search) or round_ == 1:
                    break
                self.stage.state("thinking")
                self.on_event("searching", {"query": queries[0]})
                found = await asyncio.wait_for(self.search(queries[0]), 25)
                messages = messages + [{"role": "assistant", "content": " ".join(reply.spoken) or "Let me check."},
                                       {"role": "user", "content": results_for_model(queries[0], found)}]
        except asyncio.TimeoutError:
            reply.error = "the reply took too long"
        except asyncio.CancelledError:
            reply.interrupted = True
        except ProviderError as error:
            reply.error = str(error)
        finally:
            if writer is not None:
                writer.cancel()
            if reply.interrupted:
                self.speaker.stop()
            self.stage.mouth(0.0)
            if reply.interrupted or reply.error:
                self.stage.express("neutral")
            else:
                self._calm_later()
            said = " ".join(reply.spoken).strip()
            if said or reply.interrupted:
                self.memory.add_turn(heard, said, interrupted=reply.interrupted)
            if reply.error:
                log.warning("reply failed: %s", reply.error)
                self.on_event("error", {"message": reply.error})
                await self._notice()
            self.stage.state("idle")
            self.on_event("replied", {"text": said, "interrupted": reply.interrupted, "error": reply.error})
            self.current = None
        return reply

    def _today(self):
        """What is special about today, for the instructions; nothing if it cannot be had (never a failed reply)."""
        if self.today is None:
            return ""
        try:
            return self.today() or ""
        except Exception:
            log.exception("could not say what is special about today")
            return ""

    async def _write(self, system, messages, sentences, reply):
        """Read the model's stream and queue up sentences to speak."""
        splitter = Splitter()
        try:
            async for piece in self.llm.stream(system, messages):
                for item in splitter.feed(piece):
                    await sentences.put(item)
            for item in splitter.flush():
                await sentences.put(item)
            await sentences.put(None)
        except ProviderError as error:
            await sentences.put(error)
        except asyncio.CancelledError:
            raise
        except Exception as error:             # a provider bug must not leave the character stuck
            log.exception("language model stream failed")
            await sentences.put(ProviderError("the language model failed (%s)" % type(error).__name__))

    def room(self, used):
        """Characters left for memories and this conversation, in what the model can read at once."""
        tokens = getattr(self.llm, "context", None) or 32000
        reply = getattr(self.llm, "max_tokens", 400) or 400
        return max(2000, min(int((tokens - reply) * CHARS_PER_TOKEN) - used - 1500, 60000))

    @property
    def patience(self):
        """Seconds to wait for the model's first words (longer for a model on the owner's own computer)."""
        return getattr(self.llm, "patience", None) or FIRST_WORDS_TIMEOUT

    async def _next(self, sentences, first):
        item = await asyncio.wait_for(sentences.get(), self.patience if first else max(REPLY_TIMEOUT, self.patience))
        if isinstance(item, ProviderError):
            raise item
        return item

    async def _speak(self, sentences, reply, queries=None):
        """Speak sentences as they come, fetching the next one's audio while one plays."""
        try:
            item = await self._next(sentences, True)
        except asyncio.TimeoutError:
            raise ProviderError("the language model did not answer in time", retry=True)
        audio_queue = asyncio.Queue(maxsize=64)
        # Seconds of sound queued, and heard: what is still to be heard is how long a later sentence's
        # sound may be held back to place its syllables over all of it (see _queue_sound).
        self._queued, self._played = 0.0, 0.0

        async def fetch_all(first_item):
            try:
                item, first, tagged, feeling = first_item, True, False, None
                while item is not None:
                    text, emotion, actions = item
                    if self.family_safe and text:
                        text = family.soften(text)
                    if emotion:
                        tagged = True
                    elif text and not tagged:
                        # The model tagged no feeling in this reply (yet): guess one from the words.
                        guess = guess_feeling(text)
                        emotion = guess if guess and guess != feeling else None
                    if emotion:                  # the mood first, so a gesture asked for plays on top of it
                        feeling = emotion
                        await audio_queue.put(("emotion", emotion))
                    asked = []
                    for action in actions:
                        if action.get("kind") == "search":
                            if queries is not None:
                                queries.append(action["query"])
                        elif action.get("kind") == "gesture":
                            asked.append(action["name"])
                            await audio_queue.put(("gesture", action["name"]))   # in step with the speech
                        else:
                            self.on_action(action)
                    if text:
                        plan = self._plan(text, asked, first)
                        first = False
                        if plan:
                            await audio_queue.put(("perform", {"syllables": plan["syllables"],
                                                               "events": plan["events"]}))
                        await self._queue_sound(text, plan, audio_queue)
                    item = await self._next(sentences, False)
                await audio_queue.put(None)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                await audio_queue.put(("error", ProviderError("the language model stopped part-way", retry=True)))
            except Exception as error:      # passed to the player, which stops and reports it
                await audio_queue.put(("error", error))

        fetcher = asyncio.ensure_future(fetch_all(item))

        async def audio():
            while True:
                entry = await audio_queue.get()
                if entry is None:
                    return
                kind, value = entry
                if kind == "error":
                    raise value if isinstance(value, ProviderError) else ProviderError(
                        "the voice failed (%s)" % type(value).__name__)
                if kind == "audio":
                    yield value
                elif kind == "emotion":          # markers: each runs as the sound before it is heard
                    yield functools.partial(self.stage.express, value)
                elif kind == "gesture":
                    if hasattr(self.stage, "gesture"):
                        yield functools.partial(self.stage.gesture, value)
                elif kind == "perform":
                    if hasattr(self.stage, "perform"):
                        yield functools.partial(self.stage.perform, value)
                elif kind == "syllable":
                    if hasattr(self.stage, "syllable"):
                        yield functools.partial(self.stage.syllable, value)
                elif kind == "text":
                    reply.spoken.append(value)
                    self.on_event("saying", {"text": value})
                    self.stage.state("speaking")
                    if self.on_caption is not None:      # shown as the sentence's sound starts
                        yield functools.partial(self.on_caption, value)

        try:
            def heard(level):
                self._played += FRAME
                self.stage.mouth(level)

            await self.speaker.play(audio(), self.tts.rate, heard, self.on_audio)
        finally:
            fetcher.cancel()

    def _plan(self, text, asked, first):
        """The sentence's performance, or None (the stage then moves the mouth with the loudness alone)."""
        try:
            available = self.gestures if self.gestures is not None else getattr(self.stage, "gestures", ())
            available = [g for g in available or () if g not in getattr(self.stage, "disabled", ())]
            return self.planner.plan(text, available or (), asked=asked, first=first,
                                     strokes=getattr(self.stage, "strokes", None))
        except Exception:
            log.exception("could not plan a sentence's performance")
            return None

    async def _put_audio(self, audio_queue, data):
        self._queued += len(data) / 2 / self.tts.rate
        await audio_queue.put(("audio", data))

    async def _queue_whole(self, text, pcm, timing, audio_queue):
        """Queue a whole sentence's sound, its syllables placed over all of it."""
        await audio_queue.put(("text", text))
        try:
            offsets = [o * 2 for o in syllable_offsets(pcm, self.tts.rate, timing)] if timing else []
        except Exception:
            log.exception("could not place a sentence's syllables")
            offsets = []
        step = int(self.tts.rate * 0.2) * 2
        cuts = sorted(set([0] + [o for o in offsets if 0 <= o < len(pcm)]))
        marks = {o: k for k, o in enumerate(offsets)}
        for i, cut in enumerate(cuts):
            end = cuts[i + 1] if i + 1 < len(cuts) else len(pcm)
            if cut in marks:
                await audio_queue.put(("syllable", marks[cut]))
            for piece in range(cut, end, step):
                await self._put_audio(audio_queue, pcm[piece:min(end, piece + step)])

    async def _pump(self, text, incoming):
        """The voice's sound for a sentence, chunk by chunk, into a queue (then None, or the error)."""
        try:
            async for chunk in self.tts.stream(text):
                await incoming.put(chunk)
            await incoming.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await incoming.put(error)

    async def _queue_sound(self, text, plan, audio_queue):
        """Queue a sentence's sound, with a mark where each of its syllables is heard.

        Syllables are placed best over a sentence's whole sound. The device's own voice makes it whole
        anyway. A voice that streams has its later sentences held back until all of their sound has
        come, as long as the sound before them is still being heard (so the voice never waits for it);
        otherwise its syllables are marked as the sound streams in."""
        count = len(plan["syllables"]) if plan else 0
        timing = (plan.get("timing") or even_timing(count)) if count else None
        whole = getattr(self.tts, "full", None)
        if whole and count:
            await self._queue_whole(text, await whole(text), timing, audio_queue)
            return
        incoming = asyncio.Queue()
        pump = asyncio.ensure_future(self._pump(text, incoming))
        try:
            held = []
            waiting = self._queued - self._played - HOLD_MARGIN
            if count and waiting > 0:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + waiting
                while loop.time() < deadline:
                    try:
                        item = await asyncio.wait_for(incoming.get(), deadline - loop.time())
                    except asyncio.TimeoutError:
                        break
                    if isinstance(item, Exception):
                        raise item
                    if item is None:
                        await self._queue_whole(text, b"".join(held), timing, audio_queue)
                        return
                    held.append(item)
            # It streams on: marks as the sound comes (what was held back goes first).
            try:
                marks = SyllableMarks(self.tts.rate, timing) if count else None
            except Exception:
                log.exception("could not follow a sentence's syllables")
                marks = None
            started = False

            async def out(chunk):
                nonlocal started
                if not started:                      # counted as said only once its sound exists
                    await audio_queue.put(("text", text))
                    started = True
                for piece in (marks.feed(chunk) if marks else [chunk]):
                    if isinstance(piece, bytes):
                        await self._put_audio(audio_queue, piece)
                    else:
                        await audio_queue.put(piece)

            for chunk in held:
                await out(chunk)
            while True:
                item = await incoming.get()
                if isinstance(item, Exception):
                    raise item
                if item is None:
                    break
                await out(item)
            for piece in (marks.flush() if marks else []):
                await self._put_audio(audio_queue, piece)
        finally:
            pump.cancel()

    async def _notice(self):
        """A short tone when something failed, so a silent device is never a mystery."""
        try:
            await self.speaker.play(tone(), 24000, lambda level: None, self.on_audio)
        except Exception:
            pass

    async def say_line(self, text, emotion=None, tts=None):
        """Say a fixed line, without the language model (an alarm when the model cannot be reached), in this
        conversation's voice or in `tts`. Returns the Reply; raises ProviderError if the voice fails."""
        self._stop_calming()
        reply = self.current = Reply("")
        sentences = asyncio.Queue()
        for item in [(text, emotion, []), None]:
            await sentences.put(item)
        voice = self.tts
        if tts is not None:
            self.tts = tts
        try:
            await self._speak(sentences, reply)
        except asyncio.CancelledError:
            reply.interrupted = True
            self.speaker.stop()
        finally:
            self.tts = voice
            self.stage.mouth(0.0)
            self.stage.state("idle")
            self.current = None
            if emotion:
                self._calm_later()
        return reply

    def speak_line(self, text, emotion=None, tts=None):
        """say_line in the background, interruptible like a reply. Returns the task."""
        self.interrupt()
        self._task = asyncio.ensure_future(self.say_line(text, emotion, tts))
        return self._task

    async def greet(self):
        """Say the persona's greeting, if it has one."""
        _, greeting = split_persona(self.persona)
        if not greeting:
            return
        reply = Reply("")
        sentences = asyncio.Queue()
        for item in [(greeting, "happy", []), None]:
            await sentences.put(item)
        try:
            await self._speak(sentences, reply)
        except ProviderError as error:
            self.on_event("error", {"message": str(error)})
        finally:
            self._calm_later()
            self.stage.state("idle")
