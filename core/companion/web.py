"""The web console's server: a password-protected API on the home network.

Rules:
- Only addresses on the local network are served.
- A password is set on first use; after that every API call needs a login.
- Keys can be set or cleared, never read back.
- Requests that change anything must carry the header `X-Companion: 1`,
  which a web page on another site cannot add, so it cannot act for you.
"""
import asyncio
import base64
import datetime
import io
import ipaddress
import json
import os
import re
import secrets
import time
import urllib.parse

from aiohttp import web

from . import animations, backgrounds, cards, motions, package, persona, providers, screen, speech_text, temperament, vrm
from . import body_motion, live_motion, mocap_assets, phone_apps, phone_record, special_days, tls
from . import weather as weather_module
from . import memory as memory_module
from .memory import when
from . import backup as backup_module
from . import support as support_module
from . import updates as updates_module
from .network import NetworkError
from .providers import ProviderError
from .store import write_atomic
from .stage_link import CONTROL_NAMES, CONTROLS

SESSION_COOKIE = "companion_session"
SESSION_HOURS = 12
MAX_UPLOAD = package.MAX_TOTAL + 1024 * 1024
STATIC = os.path.join(os.path.dirname(__file__), "console")
OPEN_PATHS = {"/api/login", "/api/setup", "/api/hello", "/api/health"}
# The page a phone records a gesture with (phone_record.py) may run MediaPipe's WebAssembly; nothing else may.
RECORD_CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; img-src 'self' data: blob:; "
              "media-src 'self' blob:; style-src 'self' 'unsafe-inline'")
PHONE_ONLY = web.AppKey("phone_only", bool)      # the device's own HTTPS (make_phone_app)


def local_address(remote):
    try:
        ip = ipaddress.ip_address((remote or "").split("%")[0])
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


class _Stage:
    """The stage as it is now (the device can replace its link)."""

    def __init__(self, device):
        self.device = device

    def send(self, command):
        return self.device.stage.send(command)


class Console:
    def __init__(self, device):
        self.device = device
        self.sessions = {}
        self.failures = {}
        self.recorder = phone_record.Recorder()
        self.mocap_fetch = None             # fetching the phone's tracking, when the device does not have it
        self.mocap_error = ""
        # "Copy me": the character moves as a person does, from a phone (live_motion.py).
        self.live = live_motion.LiveMotion(_Stage(device), speaking=lambda: getattr(device, "state", "") == "speaking",
                                           changed=self._live_changed)
        self.app_link = None                # a face-tracking app on a phone (phone_apps.py), while connected
        self.app_error = ""
        self.tls_runner = None              # the device's own HTTPS for the live camera (tls.py), while on
        self.tls_port = None
        self.tls_cert = None
        self.tls_error = ""

    # ----- middleware
    @web.middleware
    async def guard(self, request, handler):
        if not local_address(request.remote):
            raise web.HTTPForbidden(text="this device only answers on its home network")
        path = request.path
        if request.app.get(PHONE_ONLY) and not path.startswith(("/r", "/static/", "/licences")):
            raise web.HTTPNotFound()                 # the device's own HTTPS serves the phone's recorder alone
        self.device.last_use = time.time()
        if path.startswith(("/api/", "/r/")):
            # (A phone recording a gesture needs no login: its link's one-time code stands for one.)
            if request.method not in ("GET", "HEAD") and request.headers.get("X-Companion") != "1":
                raise web.HTTPForbidden(text="missing X-Companion header")
            if path.startswith("/api/") and path not in OPEN_PATHS and not self._session_ok(request):
                raise web.HTTPUnauthorized(text="please log in")
        response = await handler(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'")
        return response

    def _session_ok(self, request):
        token = request.cookies.get(SESSION_COOKIE, "")
        expires = self.sessions.get(token)
        if not expires or expires < time.time():
            self.sessions.pop(token, None)
            return False
        return True

    def _new_session(self, response):
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.time() + SESSION_HOURS * 3600
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", max_age=SESSION_HOURS * 3600)
        return response

    # ----- login
    async def hello(self, request):
        return web.json_response({"needs_setup": not self.device.store.has_password(),
                                  "logged_in": self._session_ok(request)})

    async def setup(self, request):
        if self.device.store.has_password():
            raise web.HTTPConflict(text="a password is already set")
        body = await request.json()
        try:
            self.device.store.set_password(body.get("password", ""))
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        return self._new_session(web.json_response({"ok": True}))

    async def login(self, request):
        ip = request.remote
        count, until = self.failures.get(ip, (0, 0))
        if until > time.time():
            raise web.HTTPTooManyRequests(text="too many attempts; wait %d seconds" % (until - time.time()))
        body = await request.json()
        if not self.device.store.check_password(body.get("password", "")):
            count += 1
            self.failures[ip] = (count, time.time() + 30 if count >= 5 else 0)
            await asyncio.sleep(0.5)
            raise web.HTTPUnauthorized(text="wrong password")
        self.failures.pop(ip, None)
        return self._new_session(web.json_response({"ok": True}))

    async def logout(self, request):
        self.sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE)
        return response

    async def password(self, request):
        body = await request.json()
        if not self.device.store.check_password(body.get("old", "")):
            raise web.HTTPUnauthorized(text="the current password is wrong")
        try:
            self.device.store.set_password(body.get("new", ""))
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.sessions.clear()
        return self._new_session(web.json_response({"ok": True}))

    # ----- status and events
    async def status(self, request):
        return web.json_response(self.device.status())

    async def animation(self, request):
        """What the character is doing now: its state, mood, drives, gestures and budget, and its latest
        gestures (with those skipped, and why)."""
        return web.json_response({"status": self.device.animation, "log": self.device.gesture_log[-60:]})

    async def events(self, request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await response.prepare(request)
        queue = asyncio.Queue()
        self.device.subscribers.add(queue)
        try:
            for event in self.device.log[-30:]:
                await response.write(("data: %s\n\n" % json.dumps(event)).encode())
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                    await response.write(("data: %s\n\n" % json.dumps(event)).encode())
                except asyncio.TimeoutError:
                    await response.write(b": keep-alive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.device.subscribers.discard(queue)
        return response

    # ----- settings and keys
    async def get_settings(self, request):
        return web.json_response(self.device.store.settings())

    async def put_settings(self, request):
        changes = await request.json()
        if not isinstance(changes, dict):
            raise web.HTTPBadRequest(text="settings must be an object")
        old = self.device.store.settings()
        if old.get("family_safe", {}).get("on") and (changes.get("family_safe") or {}).get("on") is False:
            raise web.HTTPForbidden(text="turning family-safe mode off needs the password again")
        if self.device.mic_switch and (changes.get("microphone") or {}).get("muted") is False:
            raise web.HTTPBadRequest(text="the mute switch is on: slide it to turn the microphone on")
        try:
            merged = self.device.store.update_settings(changes)
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.conversation = None
        self.device.settings_changed(old, merged)
        if merged.get("character_format") != old.get("character_format"):
            self.device.show_character()          # the other drawing of the same character, if it has one
        random_now = merged["conversation"].get("random_gestures", True)
        if random_now != old["conversation"].get("random_gestures", True) and hasattr(self.device.stage, "random_gestures"):
            self.device.stage.random_gestures(random_now)
        return web.json_response(merged)

    async def get_keys(self, request):
        return web.json_response(self.device.store.secret_status())

    async def put_key(self, request):
        body = await request.json()
        try:
            self.device.store.set_secret(request.match_info["name"], body.get("value", ""))
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.conversation = None
        return web.json_response(self.device.store.secret_status())

    async def test_provider(self, request):
        which = request.match_info["which"]
        if which not in ("llm", "stt", "tts"):
            raise web.HTTPNotFound()
        try:
            result = await asyncio.wait_for(self.device.build(which).test(), 60)
        except (ProviderError, asyncio.TimeoutError) as error:
            return web.json_response({"ok": False, "message": str(error) or "timed out"})
        return web.json_response({"ok": True, "message": result})

    async def models(self, request):
        try:
            names = await asyncio.wait_for(self.device.build("llm").models(), 30)
        except (ProviderError, asyncio.TimeoutError, AttributeError) as error:
            return web.json_response({"ok": False, "message": str(error) or "timed out", "models": []})
        return web.json_response({"ok": True, "models": names})

    async def find_llm(self, request):
        """Language-model servers on the home network, or at the address the owner typed, to choose from."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        typed = str((body or {}).get("host") or "").strip() if isinstance(body, dict) else ""
        hosts, ports = None, None
        if typed:
            try:
                parts = urllib.parse.urlsplit(typed if "://" in typed else "http://" + typed)
                hosts = [parts.hostname] if parts.hostname else None
                if parts.port:
                    ports = sorted({v["port"] for v in providers.LOCAL_SERVERS.values() if v["port"]} | {parts.port})
            except ValueError:
                raise web.HTTPBadRequest(text="that does not look like a computer's address")
        try:
            found = await asyncio.wait_for(providers.find_servers(hosts=hosts, ports=ports), 25)
        except asyncio.TimeoutError:
            found = []
        return web.json_response({"servers": found})

    async def voices(self, request):
        provider = (request.query.get("provider") or "").strip().lower() or None
        if provider and provider not in providers.TTS_PROVIDERS:
            return web.json_response({"ok": False, "message": "unknown voice service: %s" % provider, "voices": []})
        try:
            tts = self.device.build("tts", provider=provider, require_voice=False)
            names = await asyncio.wait_for(tts.voices(), 30)
        except AttributeError:
            return web.json_response({"ok": False, "message": "this voice service has no voice list", "voices": []})
        except (ProviderError, asyncio.TimeoutError) as error:
            return web.json_response({"ok": False, "message": str(error) or "timed out", "voices": []})
        return web.json_response({"ok": True, "voices": names})

    # ----- characters
    async def list_characters(self, request):
        return web.json_response({"active": self.device.active_id(), "characters": self.device.characters.list()})

    async def upload_character(self, request):
        data = await request.read()
        replace = request.query.get("replace") == "1"
        try:
            if package.is_live2d_zip(data):
                # A buyer's own Live2D-format model, zipped as it comes for VTube Studio and the like.
                data = package.from_live2d_zip(io.BytesIO(data), name=request.query.get("name") or None)
            elif package.is_inochi(data):
                # An Inochi2D puppet the owner rigged (in Inochi Creator, for example).
                data = package.from_inochi(data, name=request.query.get("name") or None)
            elif vrm.is_vrm(data):
                # A VRM avatar (3D): its textures are scaled down for this device, which takes a while.
                data = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: package.from_vrm(data, name=request.query.get("name") or None,
                                                   texture_side=vrm.device_texture_side()))
            cid = self.device.characters.install(data, replace=replace)
        except package.PackageError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.emit("character", {"id": cid, "did": "installed"})
        if len(self.device.characters.ids()) == 1:
            self.device.activate(cid)
        return web.json_response({"id": cid})

    def _cid(self, request):
        cid = request.match_info["cid"]
        if cid not in self.device.characters.ids():
            raise web.HTTPNotFound(text="no such character")
        return cid

    async def activate(self, request):
        self.device.activate(self._cid(request))
        return web.json_response({"ok": True})

    async def delete_character(self, request):
        cid = self._cid(request)
        if cid == self.device.active_id() and len(self.device.characters.ids()) > 1:
            other = next(c for c in self.device.characters.ids() if c != cid)
            self.device.activate(other)
        self.device.characters.delete(cid)
        return web.json_response({"ok": True})

    async def preview(self, request):
        cid = self._cid(request)
        for name, kind in (("preview.png", "image/png"), ("preview.webp", "image/webp")):
            path = self.device.characters.file(cid, name)
            if os.path.exists(path):
                return web.FileResponse(path, headers={"Content-Type": kind})
        raise web.HTTPNotFound()

    async def export(self, request):
        cid = self._cid(request)
        data = self.device.characters.export(cid)
        return web.Response(body=data, content_type="application/octet-stream",
                            headers={"Content-Disposition": 'attachment; filename="%s.char"' % cid})

    async def get_persona(self, request):
        cid = self._cid(request)
        device = self.device.characters.device_settings(cid)
        keys = self.device.store.secret_status()
        cloning = [{
            "id": pid,
            "label": info["label"],
            "has_key": bool(keys.get(info["secret"])),
            "formats": list(info["formats"]),
            "max_mb": info["max_bytes"] // (1024 * 1024),
            "languages": bool(info["languages"]),
        } for pid, info in providers.VOICE_CLONING_PROVIDERS.items()]
        return web.json_response({
            "persona": self.device.characters.persona(cid),
            "device": device,
            "form": device.get("persona_form") or {},
            "questions": list(persona.NAMES),
            "has_voice_sample": os.path.isfile(self.device.characters.file(cid, "voice/sample.wav")),
            "cloning_providers": cloning,
        })

    async def get_emotions(self, request):
        cid = self._cid(request)
        from .speech_text import EMOTIONS
        return web.json_response({"emotions": [e for e in EMOTIONS if e != "neutral"],
                                  "expressions": self.device.characters.expressions(cid),
                                  "map": self.device.characters.emotion_map(cid)})

    async def put_emotions(self, request):
        """Choose which of the character's own expressions shows each emotion ("" for none)."""
        cid = self._cid(request)
        from .speech_text import EMOTIONS
        body = await request.json()
        wanted = body.get("map") if isinstance(body, dict) else None
        known = set(self.device.characters.expressions(cid))
        if not isinstance(wanted, dict) or any(k not in EMOTIONS or not isinstance(v, str) or (v and v not in known)
                                               for k, v in wanted.items()):
            raise web.HTTPBadRequest(text="map must name our emotions and the character's own expressions")
        self.device.characters.set_device_settings(cid, {"emotions": wanted})
        if cid == self.device.active_id():
            self.device.use_emotion_map(cid)
        return web.json_response({"map": self.device.characters.emotion_map(cid)})

    async def read_card(self, request):
        """A character card (PNG or JSON) as a persona to review; nothing is saved."""
        data = await request.read()
        try:
            card = cards.read_card(data)
        except cards.CardError as error:
            raise web.HTTPBadRequest(text=str(error))
        text, greeting = cards.to_persona(card)
        if package.contains_secret(text):
            raise web.HTTPBadRequest(text="the card appears to contain an API key; it was not used")
        return web.json_response({"name": card["name"], "persona": text, "greeting": greeting, "tags": card["tags"],
                                  "creator": card["creator"]})

    # ----- full-body animation for 3D (VRM) avatars
    def _vrm_cid(self, request):
        cid = self._cid(request)
        if self.device.characters.drawing(cid) != "vrm":
            raise web.HTTPBadRequest(text="only 3D (VRM) avatars have full-body animations")
        return cid

    def _stage_gestures(self):
        link = self.device.stage
        return list(getattr(link, "gestures", []) or []) + list(getattr(link, "actions", []) or [])

    def _restage(self, cid):
        """Show the character again with its new settings, if it is the one on the screen."""
        self.device.vrm_failures.pop(cid, None)
        if cid == self.device.active_id():
            self.device.show_character()

    async def get_animations(self, request):
        cid = self._vrm_cid(request)
        out = self.device.animations.describe(cid, self._stage_gestures())
        out["active"] = cid == self.device.active_id()
        return web.json_response(out)

    async def put_animations(self, request):
        """The animation style (full or light), how much the drawing may cost, and the shot."""
        cid = self._vrm_cid(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send animation, quality and shot")
        try:
            settings = self.device.animations.set_settings(cid, body)
        except animations.AnimationError as error:
            raise web.HTTPBadRequest(text=str(error))
        self._restage(cid)
        return web.json_response({"settings": settings})

    async def add_animation(self, request):
        """Keep a VRM Animation clip for the character, in the role named (an idle loop, a gesture it
        replaces, or a new gesture with the words that say what it means)."""
        cid = self._vrm_cid(request)
        role = request.match_info["role"]
        data = await request.read()
        try:
            entry = self.device.animations.add(cid, role, data, title=request.query.get("title", ""),
                                               words=request.query.get("words"), gestures=self._stage_gestures())
        except animations.AnimationError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.emit("animation_added", {"id": cid, "role": role})
        self._restage(cid)
        return web.json_response(dict(entry, role=role))

    async def delete_animation(self, request):
        cid = self._vrm_cid(request)
        if not self.device.animations.remove(cid, request.match_info["role"]):
            raise web.HTTPNotFound(text="no such animation")
        self._restage(cid)
        return web.json_response({"ok": True})

    # ----- backgrounds: a picture behind the character (backgrounds.py)
    def _backgrounds_status(self):
        cid = self.device.active_id()
        return {"default": self.device.store.settings()["background"],
                "pictures": self.device.backgrounds.pictures(), "active": cid}

    async def get_backgrounds(self, request):
        return web.json_response(self._backgrounds_status())

    async def background_thumbnail(self, request):
        picture = request.match_info["picture"]
        if picture not in backgrounds.BUILTIN and picture not in backgrounds.FESTIVE and \
                picture not in self.device.backgrounds.index():
            raise web.HTTPNotFound(text="no such background")
        data = await asyncio.get_running_loop().run_in_executor(None, self.device.backgrounds.thumbnail, picture)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    async def add_background(self, request):
        """Keep a picture of the owner's to use as a background."""
        data = await request.read()
        try:
            picture = await asyncio.get_running_loop().run_in_executor(
                None, self.device.backgrounds.add, data, request.query.get("title", ""))
        except backgrounds.BackgroundError as error:
            raise web.HTTPBadRequest(text=str(error))
        return web.json_response(dict(self._backgrounds_status(), added=picture))

    async def delete_background(self, request):
        """Remove a picture of the owner's; characters that used it go back to the default."""
        if not self.device.backgrounds.remove(request.match_info["picture"]):
            raise web.HTTPNotFound(text="no such background of yours")
        self.device.show_background()
        return web.json_response(self._backgrounds_status())

    async def put_background_default(self, request):
        """The background behind every character without one of its own: which, how dim, and whether it
        moves a little as the character turns."""
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send choice, dim and parallax")
        changes = {k: body[k] for k in ("choice", "dim", "parallax", "every") if k in body}
        if "choice" in changes and not self.device.backgrounds.known(changes["choice"]):
            raise web.HTTPBadRequest(text="there is no background called %s" % changes["choice"])
        try:
            self.device.store.update_settings({"background": changes})
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.show_background()
        return web.json_response(self._backgrounds_status())

    async def get_character_background(self, request):
        cid = self._cid(request)
        own = self.device.characters.device_settings(cid).get("background") or {}
        return web.json_response({"choice": own.get("choice") or "default", "dim": own.get("dim")})

    async def put_character_background(self, request):
        """A character's own background ("default": the device's), and how dim, if not the device's."""
        cid = self._cid(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send choice and dim")
        choice = body.get("choice", "default")
        if choice != "default" and not (isinstance(choice, str) and self.device.backgrounds.known(choice)):
            raise web.HTTPBadRequest(text="there is no background called %s" % choice)
        dim = body.get("dim")
        if dim is not None and (isinstance(dim, bool) or not isinstance(dim, (int, float)) or not 0 <= dim <= 0.8):
            raise web.HTTPBadRequest(text="dim is a number from 0 to 0.8")
        self.device.characters.set_device_settings(cid, {"background": {"choice": choice, "dim": dim}})
        if cid == self.device.active_id():
            self.device.show_background()
        return web.json_response({"choice": choice, "dim": dim})

    # ----- your day: the weather, special days, wake-ups and timers (docs/28-user-guide.md, "Your day")
    def _day_status(self):
        device = self.device
        settings = device.store.settings()
        zone = device.timezone()
        cid = device.active_id()
        specials = device.specials(cid=cid) if cid else special_days.on(datetime.date.today(), settings)
        weather = device.weather.now(settings, zone)
        arrived = device.arrival(cid) if cid else None
        provider = device.weather.provider(settings)
        return {
            "timezone": zone,
            "place": device.weather.place(settings, zone),
            "units": device.weather.units(settings, zone),
            "provider": provider,
            "key_set": bool(provider and device.store.secret(provider)),
            "providers": {k: {"name": v["name"], "site": v["site"], "credit": v["credit"]}
                          for k, v in weather_module.PROVIDERS.items()},
            "weather": weather,
            "weather_said": weather_module.describe(weather),
            "problem": device.weather.problem(settings, zone),
            "credit": weather_module.TOWNS_CREDIT,
            "specials": [{"kind": x["kind"], "what": x["what"]} for x in specials],
            "festive": special_days.festive(specials, settings),
            "arrived": arrived.isoformat() if arrived else None,
            "next_wake_up": device.next_wake_up(),
            "timers": [{"label": k, "due": round(v[0]), "kind": v[2]} for k, v in sorted(device.timers.items(),
                                                                                         key=lambda kv: kv[1][0])],
            "ringing": sorted(device.rings),
            "twelve_hour": weather_module.guess_12_hour(zone),
        }

    async def get_day(self, request):
        return web.json_response(self._day_status())

    async def find_places(self, request):
        """Towns whose name starts with what was typed, from the list kept on the device: nothing is sent anywhere."""
        found = await asyncio.get_running_loop().run_in_executor(
            None, self.device.weather.places.search, request.query.get("q", "")[:80], self.device.timezone())
        return web.json_response({"places": found})

    async def check_weather(self, request):
        """Fetch the forecast now (if it is due), and show what the character would say."""
        settings = self.device.store.settings()
        if not self.device.weather.provider(settings):
            raise web.HTTPConflict(text="choose a weather service first")
        if not self.device.weather.on(settings):
            raise web.HTTPConflict(text="add the weather service's key first")
        if self.device.weather.place(settings, self.device.timezone()) is None:
            raise web.HTTPConflict(text="choose your town first")
        await self.device.weather_now(wait=15.0)
        self.device.clock_step()
        status = self._day_status()
        if status["weather"] is None and not status["problem"]:
            status["problem"] = "no weather yet; it tries again in ten minutes"
        return web.json_response(status)

    async def stop_alarm(self, request):
        """Stop an alarm that is ringing (as the device's button does)."""
        stopped = self.device.stop_ringing()
        return web.json_response(dict(self._day_status(), stopped=stopped))

    async def try_wake_up(self, request):
        """Ring a wake-up now, once, to hear how it sounds."""
        if self.device.rings:
            raise web.HTTPConflict(text="an alarm is ringing; stop it first")
        self.device._ring("wake up", "wake", times=1)
        return web.json_response(self._day_status())

    async def cancel_timer(self, request):
        """Cancel a timer or alarm set by voice."""
        if not self.device.cancel_timer(request.match_info["label"]):
            raise web.HTTPNotFound(text="there is no such timer or alarm")
        return web.json_response(self._day_status())

    async def try_animation(self, request):
        """Play one of the character's added clips once on the screen."""
        cid = self._vrm_cid(request)
        if cid != self.device.active_id():
            raise web.HTTPConflict(text="put the character on the screen first")
        try:
            path = self.device.animations.path(cid, request.match_info["role"])
        except KeyError:
            raise web.HTTPNotFound(text="no such animation")
        if self.device.stage.send({"try_animation": path}) is False:
            raise web.HTTPServiceUnavailable(text="the character's screen is not running")
        return web.json_response({"ok": True})

    # ----- movement settings: which of the character's own parameters each kind of movement uses
    async def get_movement(self, request):
        cid = self._cid(request)
        link = self.device.stage
        active = cid == self.device.active_id()
        ready = active and bool(getattr(link, "param_info", None))
        saved = self.device.characters.device_settings(cid).get("controls") or {}
        return web.json_response({
            "active": active, "ready": ready,
            "controls": [{"control": key, "label": label} for key, label in CONTROLS],
            "current": list(getattr(link, "controls", [])) if ready else [],
            "params": list(getattr(link, "param_info", [])) if ready else [],
            "gestures": list(getattr(link, "gestures", [])) if ready else [],
            "actions": list(getattr(link, "actions", [])) if ready else [],
            "saved": saved,
            "own_motions": self._own_motions(cid),
            **self._temperament(cid)})

    def _own_motions(self, cid):
        chars = self.device.characters
        if chars.drawing(cid) != "live2d":
            return None
        idle = chars.own_motions(cid)
        if not idle:
            return None
        return {"setting": chars.own_motion_setting(cid), "idle": idle, "studio": chars.made_in_studio(cid)}

    async def put_own_motions(self, request):
        """How much of a Live2D-format model's own idle motions shows: full, gentle or off."""
        from .characters import OWN_MOTIONS
        cid = self._cid(request)
        body = await request.json()
        setting = body.get("setting") if isinstance(body, dict) else None
        if setting not in OWN_MOTIONS:
            raise web.HTTPBadRequest(text="setting is full, gentle or off")
        if self._own_motions(cid) is None:
            raise web.HTTPBadRequest(text="this character has no idle motions of its own")
        self.device.characters.set_device_settings(cid, {"own_motions": setting})
        if cid == self.device.active_id():
            self.device.write_stage_settings(cid)
            self.device.stage.send({"model_motions": OWN_MOTIONS[setting]})
        return web.json_response({"ok": True, "own_motions": self._own_motions(cid)})

    def _temperament(self, cid):
        settings = self.device.characters.device_settings(cid)
        saved = settings.get("temperament")
        return {"temperament": temperament.clean(saved) if saved else None,
                "suggested": temperament.suggest(self.device.characters.persona(cid)),
                "gestures_off": list(settings.get("gestures_off") or [])}

    # ----- the gesture editor
    async def get_motions(self, request):
        """Every gesture and idle action as the stage has it now, for the editor."""
        link = self.device.stage
        library = await link.describe_library() if hasattr(link, "describe_library") else None
        if not library:
            return web.json_response({"ready": False})
        clips = library["clips"]
        for name, entry in self.device.custom_motions.all().items():
            if name in clips and entry.get("words"):
                clips[name]["words"] = entry["words"]
        return web.json_response({"ready": True, "clips": clips, "controls": library.get("controls") or list(motions.CONTROLS),
                                  "channels": list(motions.CHANNELS)})

    def _check_instead(self, entry):
        library = getattr(self.device.stage, "library", None) or {}
        other = entry.get("instead")
        if other and library.get("clips") is not None and other not in library["clips"]:
            raise web.HTTPBadRequest(text="there is no gesture called %s to play instead" % other)

    async def put_motion(self, request):
        """Save a changed or added gesture on the device, and give it to the stage."""
        name = request.match_info["name"]
        if name == motions.TRY:
            raise web.HTTPBadRequest(text="choose another name")
        body = await request.json()
        try:
            entry = motions.clean(name, body)
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self._check_instead(entry)
        self._keep_motion(name, entry)
        return web.json_response({"ok": True, "saved": entry})

    def _keep_motion(self, name, entry):
        """Keep a gesture on the device (checked already), and give it to the stage."""
        self.device.custom_motions.set(name, entry)
        speech_text.set_custom_gestures(self.device.custom_motions.words())
        if hasattr(self.device.stage, "define"):
            self.device.stage.define(name, entry)

    def _try_on_screen(self, entry, character=False):
        """Play a clip on the screen without keeping it; False when the screen is not running (or, with
        `character`, when it shows no character)."""
        if character and not self.device.active_id():
            return False
        link = self.device.stage
        if not hasattr(link, "define") or not link.define(motions.TRY, entry):
            return False
        link.send({"gesture": motions.TRY})
        return True

    async def delete_motion(self, request):
        """Put a changed gesture back as built, or take an added one away."""
        name = request.match_info["name"]
        if not motions.NAME.fullmatch(name):
            raise web.HTTPBadRequest(text="no such gesture")
        self.device.custom_motions.remove(name)
        speech_text.set_custom_gestures(self.device.custom_motions.words())
        if hasattr(self.device.stage, "undefine"):
            self.device.stage.undefine(name)
        return web.json_response({"ok": True})

    async def try_motion(self, request):
        """Play a gesture as it is in the editor, on the device's screen, without keeping it."""
        body = await request.json()
        try:
            entry = motions.clean(motions.TRY, body)
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self._check_instead(entry)
        if not self._try_on_screen(entry):
            raise web.HTTPServiceUnavailable(text="the character's screen is not running")
        return web.json_response({"ok": True})

    async def put_temperament(self, request):
        """Save how lively and expressive the character is, and the gestures it never makes by itself."""
        cid = self._cid(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send liveliness, expressiveness and gestures_off")
        changes = {}
        if "liveliness" in body or "expressiveness" in body:
            changes["temperament"] = temperament.clean(body)
        if "gestures_off" in body:
            off = body["gestures_off"]
            if not isinstance(off, list) or not all(isinstance(g, str) and re.fullmatch(r"[a-z_]{1,40}", g) for g in off):
                raise web.HTTPBadRequest(text="gestures_off is a list of gesture names")
            changes["gestures_off"] = sorted(set(off))[:100]
        self.device.characters.set_device_settings(cid, changes)
        if cid == self.device.active_id():
            self.device.use_character_motion(cid)
        return web.json_response({"ok": True, **self._temperament(cid)})

    async def put_movement(self, request):
        """Save the movement settings ({control: {"param", "invert"} or null to switch it off}) and show the
        character with them. Controls left out keep the stage's own choice."""
        cid = self._cid(request)
        body = await request.json()
        controls = body.get("controls") if isinstance(body, dict) else None
        if not isinstance(controls, dict):
            raise web.HTTPBadRequest(text="send the controls to set")
        active = cid == self.device.active_id()
        known = {p.get("name") for p in getattr(self.device.stage, "param_info", [])} if active else set()
        clean = {}
        for key, spec in controls.items():
            if key not in CONTROL_NAMES:
                raise web.HTTPBadRequest(text="unknown movement: %s" % key)
            if spec is None:
                clean[key] = None
                continue
            if not isinstance(spec, dict) or not isinstance(spec.get("param"), str) or not spec["param"].strip():
                raise web.HTTPBadRequest(text="%s needs one of the character's parameters" % CONTROL_NAMES[key])
            if known and spec["param"] not in known:
                raise web.HTTPBadRequest(text="this character has no parameter called %s" % spec["param"])
            entry = {"param": spec["param"][:100]}
            if spec.get("invert"):
                entry["invert"] = True
            clean[key] = entry
        self.device.characters.set_device_settings(cid, {"controls": clean})
        if active:
            self.device.show_character()          # writes the settings for the stage and starts it again
        return web.json_response({"ok": True, "saved": clean})

    async def reset_movement(self, request):
        cid = self._cid(request)
        self.device.characters.set_device_settings(cid, {"controls": {}})
        if cid == self.device.active_id():
            self.device.show_character()
        return web.json_response({"ok": True})

    async def stage_preview(self, request):
        """Move the character on the screen while its movement is being set up: hold some parameters
        for a moment ({"set": {name: value or null}}), play a gesture, or hand everything back."""
        link = self.device.stage
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send set, gesture or clear")
        if "set" in body:
            params = {p.get("name"): p for p in getattr(link, "param_info", [])}
            values = body["set"]
            if not isinstance(values, dict) or not values or len(values) > 8:
                raise web.HTTPBadRequest(text="set names up to 8 parameters")
            out = {}
            for name, value in values.items():
                p = params.get(name)
                if p is None:
                    raise web.HTTPBadRequest(text="the character has no parameter called %s" % name)
                if value is None:
                    out[name] = None
                    continue
                if not isinstance(value, (int, float)) or value != value:
                    raise web.HTTPBadRequest(text="%s needs a number" % name)
                lo, hi = sorted((float(p.get("min", [0])[0]), float(p.get("max", [1])[0])))
                out[name] = min(max(float(value), lo), hi)
            ease = body.get("ease", 0.3)
            ease = min(max(float(ease), 0.0), 2.0) if isinstance(ease, (int, float)) else 0.3
            link.send({"set": out, "ease": ease})
        elif "gesture" in body:
            if body["gesture"] not in getattr(link, "gestures", ()):
                raise web.HTTPBadRequest(text="this character cannot make that gesture")
            link.send({"gesture": body["gesture"]})
        elif body.get("clear"):
            link.send({"clear": True, "ease": 0.3})
        else:
            raise web.HTTPBadRequest(text="send set, gesture or clear")
        return web.json_response({"ok": True})

    async def put_persona(self, request):
        cid = self._cid(request)
        body = await request.json()
        try:
            written = None
            if "form" in body:
                # The guided form: the answers are kept, so the form can be opened again.
                form = persona.clean(body["form"])
                written = persona.build(form)
                self.device.characters.set_persona(cid, written)
                self.device.characters.set_device_settings(cid, {"persona_form": form})
            elif "persona" in body:
                self.device.characters.set_persona(cid, str(body["persona"]))
            if ("form" in body or "persona" in body) and not self.device.characters.device_settings(cid).get("temperament"):
                # A first temperament from the personality (a shy character moves less); the sliders change it.
                suggested = temperament.suggest(self.device.characters.persona(cid))
                self.device.characters.set_device_settings(cid, {"temperament": suggested})
                if cid == self.device.active_id():
                    self.device.use_character_motion(cid)
            if "voice" in body:
                self.device.characters.set_device_settings(cid, {"voice": str(body["voice"])[:200]})
            if "voice_provider" in body:
                vp = str(body["voice_provider"] or "").strip().lower()
                if vp and vp not in providers.TTS_PROVIDERS:
                    raise ValueError("unknown voice service: %s" % vp)
                self.device.characters.set_device_settings(cid, {"voice_provider": vp})
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.conversation = None
        return web.json_response({"ok": True, "persona": written} if written else {"ok": True})

    async def clone_voice(self, request):
        cid = self._cid(request)
        default_provider = self.device.store.settings()["tts"].get("provider", "")
        provider = (request.query.get("provider") or (
            default_provider if default_provider in providers.VOICE_CLONING_PROVIDERS else "elevenlabs")).strip().lower()
        info = providers.VOICE_CLONING_PROVIDERS.get(provider)
        if not info:
            raise web.HTTPBadRequest(text="unknown voice cloning service: %s" % provider)
        key = self.device.store.secret(info["secret"])
        if not key:
            raise web.HTTPBadRequest(text="set a %s key first (under AI services)" % info["label"])
        language = (request.query.get("language") or "en").strip()[:20]
        if request.query.get("use_package_sample") == "1":
            path = self.device.characters.file(cid, "voice/sample.wav")
            if not os.path.isfile(path):
                raise web.HTTPBadRequest(text="this character has no voice sample of its own")
            with open(path, "rb") as f:
                sample = f.read()
        else:
            sample = await request.read()
        try:
            ext, _ = providers.sniff_audio(sample)
        except ProviderError as error:
            raise web.HTTPBadRequest(text=str(error))
        if ext not in info["formats"]:
            raise web.HTTPBadRequest(text="%s accepts %s recordings for voice cloning (got %s)" % (
                info["label"], ", ".join(f.upper() for f in info["formats"]), ext.upper()))
        if len(sample) > info["max_bytes"]:
            raise web.HTTPBadRequest(text="%s accepts recordings up to %d MB" % (
                info["label"], info["max_bytes"] // (1024 * 1024)))
        name = self.device.characters.manifest(cid).get("name", cid)
        try:
            voice = await providers.clone_voice(provider, key, name, sample, language=language)
        except ProviderError as error:
            raise web.HTTPBadGateway(text=str(error))
        self.device.characters.set_device_settings(cid, {"voice": voice, "voice_provider": provider})
        self.device.conversation = None
        return web.json_response({"voice": voice, "provider": provider})

    # ----- memory
    async def get_memory(self, request):
        """Everything a character remembers, for the Memory tab: the owner's notes, its memories, its
        conversations (newest first), the one going on, and its diary."""
        memory = self.device.memory(self._cid(request))
        now = time.time()
        memory.close_stale(now)               # one gone quiet is shown as ended, waiting to be summed up
        sessions = memory.sessions()
        open_session = memory.open_session()
        return web.json_response({
            "notes": memory.notes(),
            "memories": sorted(memory.memories(), key=lambda m: (-m.get("pinned", False), -m.get("importance", 3), -m.get("updated", 0))),
            "retired": [m for m in memory.memories(everything=True) if m.get("status", "active") != "active"][-100:][::-1],
            "categories": list(memory_module.CATEGORIES),
            "sessions": [dict(s, when=when(s.get("start", 0), now)) for s in reversed(sessions[-300:])],
            "session_count": len(sessions),
            "open": dict(open_session, when=when(open_session.get("start", 0), now),
                         turns_now=memory.session_turns(open_session["id"])) if open_session else None,
            "diary": list(reversed(memory.diary()[-200:])),
        })

    async def put_memory(self, request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("notes", ""), str):
            raise web.HTTPBadRequest(text="send the notes as text")
        self.device.memory(self._cid(request)).set_notes(body.get("notes", ""))
        return web.json_response({"ok": True})

    async def clear_memory(self, request):
        self.device.memory(self._cid(request)).clear()
        return web.json_response({"ok": True})

    async def new_conversation(self, request):
        """Start afresh: the conversation going on ends now (it is summed up as usual)."""
        cid = self._cid(request)
        closed = self.device.memory(cid).new_conversation()
        asyncio.ensure_future(self.device.keep_memory(cid))
        return web.json_response({"ok": True, "ended": closed["id"] if closed else None})

    def _number(self, request, name="id"):
        try:
            return int(request.match_info[name])
        except ValueError:
            raise web.HTTPNotFound()

    async def get_session(self, request):
        memory = self.device.memory(self._cid(request))
        return web.json_response({"turns": memory.transcript(self._number(request))})

    async def put_session(self, request):
        memory = self.device.memory(self._cid(request))
        body = await request.json()
        if not isinstance(body, dict) or not any(isinstance(body.get(k), str) and body[k].strip() for k in ("summary", "title")):
            raise web.HTTPBadRequest(text="a summary needs some words")
        if not memory.edit_session(self._number(request), title=body.get("title"), summary=body.get("summary")):
            raise web.HTTPNotFound(text="no such conversation")
        return web.json_response({"ok": True})

    async def delete_session(self, request):
        if not self.device.memory(self._cid(request)).forget_session(self._number(request)):
            raise web.HTTPNotFound(text="no such conversation")
        return web.json_response({"ok": True})

    def _memory_fields(self, body):
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send the memory's text and category")
        fields = {}
        if "text" in body:
            fields["text"] = str(body["text"] or "")
        if body.get("category") is not None:
            if body["category"] not in memory_module.CATEGORIES:
                raise web.HTTPBadRequest(text="the category is one of: " + ", ".join(memory_module.CATEGORIES))
            fields["category"] = body["category"]
        if body.get("importance") is not None:
            try:
                fields["importance"] = max(1, min(5, int(body["importance"])))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="importance is 1 to 5")
        if "when" in body:
            day = str(body["when"] or "")
            if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise web.HTTPBadRequest(text="a date is written 2026-11-21")
            fields["when"] = day or None
        for flag in ("pinned", "follow_up"):
            if flag in body:
                fields[flag] = bool(body[flag])
        if body.get("status") is not None:
            if body["status"] not in ("active", "retired"):
                raise web.HTTPBadRequest(text="a memory is active or retired")
            fields["status"] = body["status"]
        return fields

    async def add_memory_item(self, request):
        memory = self.device.memory(self._cid(request))
        fields = self._memory_fields(await request.json())
        entry = memory.add_memory(fields.get("text", ""), fields.get("category", "other"), fields.get("importance", 3),
                                  fields.get("when"), follow_up=fields.get("follow_up", False), source="owner",
                                  pinned=fields.get("pinned", False))
        if not entry:
            raise web.HTTPBadRequest(text="a memory needs some words")
        return web.json_response(entry)

    async def put_memory_item(self, request):
        memory = self.device.memory(self._cid(request))
        if not memory.edit_memory(self._number(request), **self._memory_fields(await request.json())):
            raise web.HTTPNotFound(text="no such memory")
        return web.json_response({"ok": True})

    async def delete_memory_item(self, request):
        if not self.device.memory(self._cid(request)).forget_memory(self._number(request)):
            raise web.HTTPNotFound(text="no such memory")
        return web.json_response({"ok": True})

    async def put_diary(self, request):
        memory = self.device.memory(self._cid(request))
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
            raise web.HTTPBadRequest(text="a diary entry needs some words (or remove it)")
        if not memory.set_diary(request.match_info["day"], body["text"]):
            raise web.HTTPNotFound(text="no diary entry for that day")
        return web.json_response({"ok": True})

    async def delete_diary(self, request):
        if not self.device.memory(self._cid(request)).set_diary(request.match_info["day"], ""):
            raise web.HTTPNotFound(text="no diary entry for that day")
        return web.json_response({"ok": True})

    # ----- software updates (docs/32-updates.md)
    async def health(self, request):
        """Open to the device itself: the installer asks it whether a new version started properly."""
        return web.json_response({"ok": True, "version": updates_module.current_version()})

    async def get_update(self, request):
        status = self.device.updates.status()
        status["settings"] = self.device.store.settings().get("updates", {})
        status["busy"] = self.device.updating
        return web.json_response(status)

    async def check_update(self, request):
        try:
            await self.device.updates.check()
        except updates_module.UpdateError as error:
            raise web.HTTPBadGateway(text=str(error))
        return await self.get_update(request)

    async def install_update(self, request):
        """Install the update that is ready, or fetch the one found and then install it (in the
        background: the page follows along)."""
        status = self.device.updates.status()
        if not status["ready"] and not status["found"]:
            raise web.HTTPBadRequest(text="there is no update to install")

        async def go():
            try:
                await self.device.install_update()
            except updates_module.UpdateError:
                pass                                # reported in the device's log and the update's status
        asyncio.ensure_future(go())
        return web.json_response({"ok": True})

    async def upload_update(self, request):
        """An update file the owner chooses (for a device without internet, or from support)."""
        path = os.path.join(self.device.updates.folder, "upload.part")
        size = 0
        with open(path, "wb") as f:
            async for chunk in request.content.iter_chunked(1024 * 1024):
                size += len(chunk)
                if size > updates_module.MAX_UPDATE:
                    f.close()
                    os.remove(path)
                    raise web.HTTPRequestEntityTooLarge(max_size=updates_module.MAX_UPDATE, actual_size=size)
                f.write(chunk)
        try:
            manifest = self.device.updates.accept(path)
        except updates_module.UpdateError as error:
            raise web.HTTPBadRequest(text=str(error))
        return web.json_response({"ok": True, "version": manifest["version"], "notes": manifest.get("notes", "")})

    async def rollback_update(self, request):
        try:
            self.device.updates.request_rollback()
        except updates_module.UpdateError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.show_card(screen.message_card("Updating", "Going back to the previous version…"))
        return web.json_response({"ok": True})

    async def put_update_settings(self, request):
        body = await request.json()
        if not isinstance(body, dict) or not all(isinstance(body.get(k, True), bool) for k in ("check", "auto_install")):
            raise web.HTTPBadRequest(text="send check and auto_install as true or false")
        current = self.device.store.settings().get("updates", {})
        self.device.store.update_settings({"updates": {"check": body.get("check", current.get("check", True)),
                                                       "auto_install": body.get("auto_install", current.get("auto_install", True))}})
        return await self.get_update(request)

    # ----- support report, backup and restore
    async def support_report(self, request):
        data = await asyncio.get_running_loop().run_in_executor(None, support_module.report, self.device)
        name = "companion-support-%s.zip" % time.strftime("%Y%m%d-%H%M")
        return web.Response(body=data, content_type="application/zip",
                            headers={"Content-Disposition": 'attachment; filename="%s"' % name})

    async def get_backup(self, request):
        """Everything the owner made, in one file (keys, the password and Wi-Fi are never in it)."""
        path = os.path.join(self.device.data_dir, ".backup-out.zip")
        try:
            await asyncio.get_running_loop().run_in_executor(None, backup_module.make, self.device, path)
            response = web.StreamResponse(headers={
                "Content-Type": "application/zip", "Content-Length": str(os.path.getsize(path)),
                "Content-Disposition": 'attachment; filename="companion-backup-%s.zip"' % time.strftime("%Y%m%d")})
            await response.prepare(request)
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            if os.path.exists(path):
                os.remove(path)

    async def put_backup(self, request):
        """Restore a backup: its characters, memories, choices and settings (keys are put in again)."""
        path = os.path.join(self.device.data_dir, ".backup-in.zip")
        size = 0
        try:
            with open(path, "wb") as f:
                async for chunk in request.content.iter_chunked(1024 * 1024):
                    size += len(chunk)
                    if size > backup_module.MAX_BACKUP:
                        raise web.HTTPRequestEntityTooLarge(max_size=backup_module.MAX_BACKUP, actual_size=size)
                    f.write(chunk)
            try:
                done = await asyncio.get_running_loop().run_in_executor(None, backup_module.restore, self.device, path)
            except backup_module.BackupError as error:
                raise web.HTTPBadRequest(text=str(error))
        finally:
            if os.path.exists(path):
                os.remove(path)
        self.device.conversation = None
        cid = self.device.active_id()
        if cid:
            self.device.activate(cid)
        self.device.emit("backup", {"did": "restored", "characters": [c["id"] for c in done["characters"]]})
        return web.json_response(done)

    # ----- talking from the console
    async def say(self, request):
        body = await request.json()
        asyncio.ensure_future(self.device.say(str(body.get("text", ""))))
        return web.json_response({"ok": True})

    async def press(self, request):
        self.device.button()
        return web.json_response({"ok": True, "state": self.device.state, "listening": self.device.listening})

    async def interrupt(self, request):
        self.device.stop_ringing()
        self.device.interrupt()
        return web.json_response({"ok": True})

    async def microphone(self, request):
        """Turn the microphone off or on (a mute switch, while on, wins)."""
        body = await request.json()
        muted = body.get("muted")
        if not isinstance(muted, bool):
            raise web.HTTPBadRequest(text="muted must be true or false")
        if self.device.mic_switch and not muted:
            raise web.HTTPBadRequest(text="the mute switch is on: slide it to turn the microphone on")
        self.device.set_muted(muted)
        return web.json_response({"muted": self.device.muted, "switch": self.device.mic_switch})

    async def storage_seen(self, request):
        """The owner has seen that the device's storage had to be made again (and restored a backup)."""
        try:
            os.remove(os.path.join(self.device.data_dir, "storage-recovered"))
        except OSError:
            pass
        return web.json_response({"ok": True})

    async def family_safe(self, request):
        """Family-safe mode on, or (with the settings-page password again) off."""
        body = await request.json()
        on = body.get("on")
        if not isinstance(on, bool):
            raise web.HTTPBadRequest(text="on must be true or false")
        if not on and not self.device.store.check_password(str(body.get("password", ""))):
            raise web.HTTPUnauthorized(text="the password is wrong")
        old = self.device.store.settings()
        merged = self.device.store.update_settings({"family_safe": {"on": on}})
        self.device.conversation = None
        self.device.settings_changed(old, merged)
        self.device.emit("device", {"family_safe": on})
        return web.json_response({"on": on})

    # ----- Wi-Fi
    async def wifi_status(self, request):
        wifi = self.device.wifi
        if wifi is None:
            return web.json_response({"available": False})
        try:
            connected = await wifi.connected()
            networks = await wifi.networks()
            address = await wifi.address() if connected else ""
        except NetworkError as error:
            return web.json_response({"available": True, "error": str(error), "networks": []})
        return web.json_response({"available": True, "connected": connected, "address": address,
                                  "hotspot": bool(wifi.hotspot), "networks": networks})

    async def wifi_join(self, request):
        if self.device.wifi is None:
            raise web.HTTPNotFound(text="this device has no Wi-Fi control")
        body = await request.json()
        try:
            await self.device.join_wifi(str(body.get("ssid", "")), str(body.get("password", "")))
        except NetworkError as error:
            raise web.HTTPBadRequest(text=str(error))
        return web.json_response({"ok": True})

    # ----- device
    async def set_timezone(self, request):
        body = await request.json()
        try:
            await self.device.set_timezone(str(body.get("zone", "")))
        except (ValueError, RuntimeError) as error:
            raise web.HTTPBadRequest(text=str(error))
        return web.json_response({"timezone": self.device.timezone()})

    async def reset(self, request):
        body = await request.json()
        if not self.device.store.check_password(str(body.get("password", ""))):
            raise web.HTTPUnauthorized(text="the password is wrong")
        if body.get("confirm") != "RESET":
            raise web.HTTPBadRequest(text="type RESET to confirm")
        asyncio.ensure_future(self.device.factory_reset())
        return web.json_response({"ok": True})

    async def index(self, request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    # ----- licences and notices (open to anyone on the home network, like the leaflet in a box)
    async def licences(self, request):
        from . import notices, updates
        names = {key: title for key, (title, _) in notices.DOCUMENTS.items()}
        return web.Response(text=notices.index_page(names, updates.current_version()), content_type="text/html")

    async def licence(self, request):
        from . import notices
        name = request.match_info["name"]
        if name not in notices.DOCUMENTS:
            raise web.HTTPNotFound(text="no such notice")
        make = getattr(notices, notices.DOCUMENTS[name][1])
        if name == "models":
            folders = [(n, self.device.model_dir(n)) for n in ("stt", "tts", "meaning", "wake", "mocap")]
            text = await asyncio.get_running_loop().run_in_executor(None, make, folders)
        else:
            text = await asyncio.get_running_loop().run_in_executor(None, make)
        return web.Response(text=text, content_type="text/plain", charset="utf-8")

    # ----- gestures recorded with a phone's camera (phone_record.py)
    def _mocap_state(self):
        """Whether the device has the tracking a phone needs: ready, fetching, failed or missing."""
        if not mocap_assets.missing(self.device.model_dir("mocap")):
            return "ready"
        if self.mocap_fetch is not None and not self.mocap_fetch.done():
            return "fetching"
        return "failed" if self.mocap_error else "missing"

    def _fetch_mocap(self):
        """Fetch the tracking in the background, if the device does not have it (one that got phone recording
        through an update: updates do not carry models)."""
        if self._mocap_state() in ("ready", "fetching"):
            return
        self.mocap_error = ""
        folder = os.path.join(self.device.data_dir, "models", "mocap")
        self.mocap_fetch = asyncio.get_running_loop().run_in_executor(None, mocap_assets.fetch, folder)

        def done(future):
            error = None if future.cancelled() else future.exception()
            if future.cancelled() or error is not None:
                self.mocap_error = ("the device could not download what a phone needs to record (%d MB): check its "
                                    "internet connection, then try again" % mocap_assets.SIZE_MB)
            self._record_changed()
        self.mocap_fetch.add_done_callback(done)

    def _record_status(self, session):
        """A recording as the settings page shows it (no code: the page that asked for it has that)."""
        if not session:
            return {"active": False}
        assets = self._mocap_state()
        return {"active": True, "state": session["state"], "moves": session.get("moves") or [],
                "whole_body": bool(session.get("body_clip")),
                "seconds": session.get("seconds"), "saved": list(session.get("saved") or []),
                "expires_in": max(0, int(session["expires"] - time.time())), "assets": assets,
                "assets_message": self.mocap_error if assets == "failed" else ""}

    def _record_changed(self):
        self.device.publish("recording", self._record_status(self.recorder.current()))

    async def _phone_links(self, request):
        """The addresses a phone on the home network can open the device at: its Wi-Fi address first (the
        settings page may be open over USB, which a phone cannot reach), then the one this page used. Over
        the device's own HTTPS when the live camera is on."""
        host = request.host or ""
        match = re.fullmatch(r"(\[[^\]]*\]|[^:]*)(?::(\d+))?", host)
        name, port = (match.group(1), match.group(2)) if match else (host, None)
        scheme = "http"
        suffix = ":%s" % port if port and port != "80" else ""
        if self.tls_runner is not None:
            scheme = "https"
            suffix = "" if self.tls_port == 443 else ":%d" % self.tls_port
        names = []
        wifi = getattr(self.device, "wifi", None)
        address = getattr(self.device, "lan_address", "")      # a computer standing in for the device
        if wifi is not None:
            try:
                address = await wifi.address()
            except Exception:  # noqa: BLE001 (no address is no link, not a failure)
                address = ""
        if address:
            names.append(address)
        if name and name not in names:
            names.append(name)
        if wifi is not None and "companion.local" not in names:
            names.append("companion.local")
        local = [n for n in names if n == "localhost" or n.startswith(("127.", "[::1]"))]
        return ["%s://%s%s" % (scheme, n, suffix) for n in [n for n in names if n not in local] + local]

    def _phone_session(self, request):
        try:
            return self.recorder.find(request.match_info.get("code"))
        except phone_record.LinkError as error:
            if error.locked:
                raise web.HTTPTooManyRequests(text=str(error))
            raise web.HTTPNotFound(text=str(error))

    async def _phone_json(self, request):
        if request.content_length is None or request.content_length > phone_record.MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=phone_record.MAX_BODY,
                                                actual_size=request.content_length or 0)
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="send JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send an object")
        return body

    def _can_make(self, entry):
        """Whether the character on the screen has the controls the clip needs (True when not known)."""
        have = {c.get("control") for c in getattr(self.device.stage, "controls", None) or [] if isinstance(c, dict)}
        if not have:
            return True
        return all(any(part in have for part in need.split("|")) for need in entry.get("needs") or [])

    async def _motion_origin(self, name):
        """Whether a gesture of that name exists: "added" on this device, "built in", or None."""
        if name in self.device.custom_motions.all():
            return "added"
        link = self.device.stage
        library = getattr(link, "library", None)
        if not library and hasattr(link, "describe_library"):
            library = await link.describe_library()
        if name in ((library or {}).get("clips") or {}) or name in (getattr(link, "gestures", None) or []) \
                or name in (getattr(link, "actions", None) or []):
            return "built in"
        return None

    # The owner's side, on the settings page.
    async def start_recording(self, request):
        """A new link for a phone (any earlier one stops working), with its QR code."""
        session = self.recorder.start()
        links = await self._phone_links(request)
        session["links"] = [base + "/r/" + session["code"] for base in links]
        session["short"] = links[0] + "/r"
        picture = screen.qr_code(session["links"][0])
        if picture is not None:
            out = io.BytesIO()
            picture.save(out, "PNG")
            session["qr"] = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
        self._fetch_mocap()
        self._record_changed()
        return web.json_response(self._recording(session))

    def _recording(self, session):
        out = self._record_status(session)
        if session:
            out.update(code=session["code"], links=session["links"], short=session["short"], qr=session.get("qr"))
        return out

    async def get_recording(self, request):
        return web.json_response(self._recording(self.recorder.current()))

    async def end_recording(self, request):
        self.recorder.end()
        self._record_changed()
        return web.json_response({"ok": True})

    # The phone's side: no login, the code in the link.
    async def record_page(self, request):
        response = web.FileResponse(os.path.join(STATIC, "record.html"))
        # Its own host for the live camera's WebSocket ('self' alone does not cover wss: in every browser).
        host = request.host if re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?|\[[0-9A-Fa-f:.%]+\](:\d+)?", request.host or "") else ""
        connect = "; connect-src 'self' wss://%s ws://%s" % (host, host) if host else ""
        response.headers["Content-Security-Policy"] = RECORD_CSP + connect
        return response

    async def record_info(self, request):
        session = self._phone_session(request)
        if session["state"] == "waiting":
            session["state"] = "opened"
            self._record_changed()
        if self._mocap_state() == "missing":
            self._fetch_mocap()
        status = self._record_status(session)
        cid = self.device.active_id()
        try:
            character = self.device.characters.manifest(cid).get("name", "") if cid else ""
        except (OSError, ValueError):
            character = ""
        body = bool(cid) and self._body_wanted(cid)
        return web.json_response({"assets": status["assets"], "assets_message": status["assets_message"],
                                  "expires_in": status["expires_in"], "max_seconds": phone_record.MAX_SECONDS,
                                  "character": character, "layout": phone_record.LAYOUT,
                                  # A 3D avatar moving all its bones: the whole body is recorded, with the
                                  # larger pose model (its 3D points are steadier).
                                  "body": body,
                                  "pose_model": "pose_landmarker_full.task" if body else "pose_landmarker_lite.task"})

    async def record_asset(self, request):
        name = request.match_info["name"]
        if name not in mocap_assets.FILES:
            raise web.HTTPNotFound(text="no such file")
        path = os.path.join(self.device.model_dir("mocap"), name)
        if not os.path.isfile(path):
            raise web.HTTPServiceUnavailable(text="the device is still fetching what a phone needs to record")
        response = web.FileResponse(path)
        response.content_type = mocap_assets.CONTENT_TYPES[os.path.splitext(name)[1]]
        # The files never change for a name (each is checked against its SHA-256): the phone may keep them,
        # asking again only whether they changed.
        response.headers["Cache-Control"] = "no-cache"
        return response

    def _body_wanted(self, cid):
        """Whether a recording for this character is of the whole body: a 3D avatar playing clips."""
        try:
            return (self.device.characters.drawing(cid) == "vrm"
                    and self.device.animations.settings(cid)["animation"] == "full")
        except (OSError, ValueError, KeyError):
            return False

    def _try_body(self, data):
        """Play a whole-body clip once on the screen, without keeping it."""
        path = os.path.join(self.device.data_dir, "phone-body.vrma")
        write_atomic(path, data)
        return self.device.stage.send({"try_animation": path}) is not False

    async def _record_body(self, session, body, cid):
        """A whole-body clip from the phone's 3D points, played on the screen at once. Raises BodyError when
        too little of the body was seen."""
        mirror = bool(body.get("mirror"))
        data, report = await asyncio.get_running_loop().run_in_executor(None, body_motion.make, body, mirror)
        said = body_motion.describe(report)
        session.update(body=body, body_clip=data, body_report=report, body_said=said, body_cid=cid,
                       tracks=None, moves=[said], seconds=report["seconds"], state="ready")
        played = self._try_body(data)
        self._record_changed()
        return web.json_response({"ok": True, "whole_body": True, "scope": report["scope"], "said": said,
                                  "report": report, "moves": [said], "seconds": report["seconds"],
                                  "played": played, "can": True})

    async def record_motion(self, request):
        """The landmarks of a filmed gesture: made into a clip, played on the screen at once. For a 3D avatar
        playing clips, the whole body (body_motion.py); if too little of the body was seen for that, the
        face and arms as for any character, saying why."""
        session = self._phone_session(request)
        body = await self._phone_json(request)
        cid = self.device.active_id()
        note = ""
        if cid and self._body_wanted(cid) and any(isinstance(f, dict) and f.get("body")
                                                  for f in body.get("frames") or []):
            try:
                return await self._record_body(session, body, cid)
            except body_motion.BodyError as error:
                note = "Not enough of your body was seen for a whole-body movement (%s), so this is your head, " \
                       "face and arms." % error
        for key in ("body", "body_clip", "body_report", "body_said", "body_cid"):
            session.pop(key, None)
        try:
            tracks, moves = await asyncio.get_running_loop().run_in_executor(None, phone_record.make_clip, body)
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        seconds = max(keys[-1][0] for keys in tracks.values())
        session.update(tracks=tracks, moves=moves, seconds=seconds, state="ready")
        entry = phone_record.entry(tracks, bool(body.get("mirror")))
        played = self._try_on_screen(motions.clean(motions.TRY, entry), character=True)
        self._record_changed()
        return web.json_response({"ok": True, "moves": moves, "seconds": seconds, "played": played,
                                  "can": self._can_make(entry), "note": note})

    async def record_play(self, request):
        """Play the gesture again (swapped left for right, if asked)."""
        session = self._phone_session(request)
        body = await self._phone_json(request)
        if session.get("body_clip"):
            # The whole body again, worked out afresh if it is to be swapped left for right.
            if bool(body.get("mirror")) != bool(session["body"].get("mirror")):
                session["body"] = dict(session["body"], mirror=bool(body.get("mirror")))
                try:
                    data, report = await asyncio.get_running_loop().run_in_executor(
                        None, body_motion.make, session["body"], bool(body.get("mirror")))
                except body_motion.BodyError as error:
                    raise web.HTTPBadRequest(text=str(error))
                session.update(body_clip=data, body_report=report, body_said=body_motion.describe(report))
            return web.json_response({"ok": True, "played": self._try_body(session["body_clip"]), "can": True,
                                      "said": session["body_said"]})
        if not session.get("tracks"):
            raise web.HTTPConflict(text="send a gesture first")
        entry = phone_record.entry(session["tracks"], bool(body.get("mirror")))
        played = self._try_on_screen(motions.clean(motions.TRY, entry), character=True)
        return web.json_response({"ok": True, "played": played, "can": self._can_make(entry)})

    async def record_save(self, request):
        """Keep the gesture with the others, under the name the phone gave it."""
        session = self._phone_session(request)
        body = await self._phone_json(request)
        if session.get("body_clip"):
            return await self._save_body(session, body)
        if not session.get("tracks"):
            raise web.HTTPConflict(text="send a gesture first")
        name = phone_record.gesture_name(body.get("name"))
        kind = body.get("kind") or "gesture"
        if kind not in ("gesture", "idle"):
            raise web.HTTPBadRequest(text="keep it as a gesture or as an idle action")
        words = " ".join(str(body.get("words") or "").replace("[", " ").replace("]", " ").split())[:80]
        origin = await self._motion_origin(name)
        if origin and not body.get("replace"):
            return web.json_response({"exists": origin, "name": name}, status=409)
        try:
            entry = motions.clean(name, phone_record.entry(session["tracks"], bool(body.get("mirror")), kind,
                                                           words or None))
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        self._keep_motion(name, entry)
        session["state"] = "saved"
        session["saved"] = list(session.get("saved") or []) + [name]
        self._record_changed()
        return web.json_response({"ok": True, "name": name, "kind": kind, "replaced": origin})

    async def _save_body(self, session, body):
        """Keep a whole-body clip as the 3D avatar's own animation (animations.py): a new gesture asked for by
        its words, or in place of one it has (after asking)."""
        cid = session.get("body_cid")
        if cid != self.device.active_id():
            raise web.HTTPConflict(text="the character it was recorded for is no longer on the screen")
        name = phone_record.gesture_name(body.get("name"))
        words = " ".join(str(body.get("words") or "").replace("[", " ").replace("]", " ").split())[:80]
        gestures = self._stage_gestures()
        if name in self.device.animations.index(cid):
            origin = "added"
        elif name in gestures or name in animations.builtin_names():
            origin = "built in"
        else:
            origin = None
        if origin and not body.get("replace"):
            return web.json_response({"exists": origin, "name": name}, status=409)
        try:
            entry = self.device.animations.add(cid, name, session["body_clip"], title=name.replace("_", " "),
                                               words=words or None, gestures=gestures)
        except animations.AnimationError as error:
            raise web.HTTPBadRequest(text=str(error))
        self.device.emit("animation_added", {"id": cid, "role": name})
        self._restage(cid)
        session["state"] = "saved"
        session["saved"] = list(session.get("saved") or []) + [name]
        self._record_changed()
        return web.json_response({"ok": True, "name": name, "kind": entry.get("kind"), "replaced": origin,
                                  "whole_body": True})

    # ----- "Copy me": the character moves as a person does, live (live_motion.py)
    def _live_changed(self):
        self.device.publish("live", self._live_status())

    def _live_status(self):
        settings = self.device.store.settings()["live"]
        link = self.app_link
        return {
            "camera": settings["camera"],
            "https": {"on": self.tls_runner is not None, "port": self.tls_port, "error": self.tls_error,
                      "fingerprint": tls.fingerprint(self.tls_cert) if self.tls_cert else "",
                      "available": getattr(self.device, "tls_port", None) is not None and tls.available()},
            "app": {"connected": link is not None, "app": link.app if link else settings["app"],
                    "address": link.address if link else settings["app_address"],
                    "frames": link.frames if link else 0, "error": self.app_error,
                    "port": phone_apps.IFM_PORT if (link.app if link else settings["app"]) == "ifm" else phone_apps.VTS_PORT},
            "live": self.live.status(),
        }

    async def _start_tls(self):
        """Serve the phone's recorder over the device's own HTTPS (for its live camera)."""
        if self.tls_runner is not None:
            return
        port = getattr(self.device, "tls_port", None)
        if port is None:
            raise OSError("this device does not offer the live camera")
        addresses = [getattr(self.device, "lan_address", "")]
        wifi = getattr(self.device, "wifi", None)
        if wifi is not None:
            try:
                addresses.append(await wifi.address())
            except Exception:  # noqa: BLE001 (the certificate names the device without it)
                pass
        folder = os.path.join(self.device.data_dir, "config", "tls")
        cert, key, _ = await asyncio.get_running_loop().run_in_executor(None, tls.ensure, folder, addresses)
        runner = web.AppRunner(make_phone_app(self), access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, getattr(self.device, "web_host", "0.0.0.0"), port,
                               ssl_context=tls.server_context(cert, key))
            await site.start()
        except OSError:
            await runner.cleanup()
            raise
        self.tls_runner, self.tls_port, self.tls_cert, self.tls_error = runner, runner.addresses[0][1], cert, ""

    async def _stop_tls(self):
        runner, self.tls_runner, self.tls_port = self.tls_runner, None, None
        if runner is not None:
            await runner.cleanup()

    async def on_startup(self, app):
        if self.device.store.settings()["live"]["camera"]:
            try:
                await self._start_tls()
            except OSError as error:
                self.tls_error = str(error)

    async def on_cleanup(self, app):
        await self._stop_app()
        await self._stop_tls()

    async def get_live(self, request):
        return web.json_response(self._live_status())

    async def put_live(self, request):
        """The live camera on or off, and how the character copies (like a mirror; an app's nods flipped)."""
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send camera, mirror or flip")
        changes = {k: body[k] for k in ("camera", "mirror", "flip") if k in body}
        try:
            self.device.store.update_settings({"live": changes})
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        if "mirror" in changes:
            self.live.set_mirror(changes["mirror"])
        if "flip" in changes:
            self.live.set_flip(changes["flip"])
        if changes.get("camera") is True:
            try:
                await self._start_tls()
            except OSError as error:
                self.tls_error = str(error)
                self.device.store.update_settings({"live": {"camera": False}})
                raise web.HTTPServiceUnavailable(text="the live camera could not start: %s" % error)
        elif changes.get("camera") is False:
            await self._stop_tls()
        self._live_changed()
        return web.json_response(self._live_status())

    async def _stop_app(self):
        link, self.app_link = self.app_link, None
        if link is not None:
            await link.stop()
            self.live.end("app")

    async def connect_app(self, request):
        """Connect to a face-tracking app on the owner's phone, and copy its face."""
        body = await request.json()
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="send the app and the phone's address")
        app = body.get("app", "vts")
        try:
            address = phone_apps.phone_address(body.get("address"))
            if app not in phone_apps.APPS:
                raise ValueError("the app is VTube Studio (or MeowFace) or iFacialMocap")
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        await self._stop_app()
        settings = self.device.store.update_settings({"live": {"app": app, "app_address": address}})["live"]
        link = phone_apps.AppLink(app, address, lambda controls: self.live.frame(controls, "app"),
                                  local_port=getattr(self.device, "app_port", None),
                                  phone_port=getattr(self.device, "phone_port", None))
        try:
            await link.start()
        except OSError as error:
            self.app_error = str(error)
            raise web.HTTPServiceUnavailable(text=str(error))
        self.app_link, self.app_error = link, ""
        self.live.begin("app", phone_apps.APPS[app], mirror=settings["mirror"], flip=settings["flip"])
        return web.json_response(self._live_status())

    async def disconnect_app(self, request):
        await self._stop_app()
        return web.json_response(self._live_status())

    async def live_recentre(self, request):
        self.live.recentre()
        return web.json_response(self._live_status())

    async def live_record(self, request):
        """Record what the character copies, up to 20 seconds, as a gesture for the editor."""
        body = await request.json()
        action = body.get("action") if isinstance(body, dict) else None
        try:
            if action == "start":
                self.live.start_recording()
                return web.json_response(self._live_status())
            if action != "stop":
                raise ValueError("start or stop")
            tracks = self.live.stop_recording()
        except ValueError as error:
            raise web.HTTPBadRequest(text=str(error))
        entry = phone_record.entry(tracks)
        self._try_on_screen(motions.clean(motions.TRY, entry), character=True)
        return web.json_response({"entry": entry, "moves": phone_record.describe(tracks),
                                  "seconds": max(keys[-1][0] for keys in tracks.values()),
                                  "can": self._can_make(entry)})

    async def record_live(self, request):
        """The phone's live camera, copied as it moves (a WebSocket from the recorder page)."""
        session = self._phone_session(request)
        origin = request.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text="the live movement comes from the recorder's own page")
        ws = web.WebSocketResponse(heartbeat=10.0, max_msg_size=64 * 1024)
        await ws.prepare(request)
        await self._stop_app()                       # one person copied at a time: the newest
        self.live.begin("browser", "the phone's live camera", mirror=self.device.store.settings()["live"]["mirror"])
        told = 0.0
        try:
            async for message in ws:
                if message.type != web.WSMsgType.TEXT:
                    continue
                if self.recorder.current() is not session:
                    break                            # the owner stopped the link
                try:
                    data = json.loads(message.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if "mirror" in data:
                    self.live.set_mirror(bool(data["mirror"]))
                    self.device.store.update_settings({"live": {"mirror": bool(data["mirror"])}})
                if data.get("recentre"):
                    self.live.recentre()
                if "frame" in data:
                    try:
                        width, height = phone_record.picture_size(data)
                        controls = phone_record.frame_controls(data["frame"], width, height)
                    except ValueError:
                        continue
                    if not self.live.frame(controls, "browser"):
                        break                        # something else is copied now
                if time.monotonic() - told > 0.5:
                    told = time.monotonic()
                    await ws.send_json(self.live.status())
        finally:
            self.live.end("browser")
        return ws


def _phone_routes(r, console):
    """The pages and requests of the phone's recorder (no login: the link's code stands for one)."""
    r.add_get("/r", console.record_page)
    r.add_get("/r/", console.record_page)
    r.add_get("/r/assets/{name}", console.record_asset)
    r.add_get("/r/{code}", console.record_page)
    r.add_get("/r/{code}/info", console.record_info)
    r.add_get("/r/{code}/live", console.record_live)
    r.add_post("/r/{code}/motion", console.record_motion)
    r.add_post("/r/{code}/play", console.record_play)
    r.add_post("/r/{code}/save", console.record_save)


def make_phone_app(console):
    """What the device's own HTTPS serves, for a phone's live camera: the recorder, and nothing else (the
    settings page stays on the home network's plain address)."""
    app = web.Application(middlewares=[console.guard], client_max_size=phone_record.MAX_BODY + 64 * 1024)
    app[PHONE_ONLY] = True
    _phone_routes(app.router, console)
    app.router.add_get("/licences", console.licences)
    app.router.add_get("/licences/{name}.txt", console.licence)
    if os.path.isdir(STATIC):
        app.router.add_static("/static/", STATIC)
    return app


def make_app(device):
    console = Console(device)
    app = web.Application(middlewares=[console.guard], client_max_size=MAX_UPLOAD)
    r = app.router
    r.add_get("/", console.index)
    r.add_get("/licences", console.licences)
    r.add_get("/licences/{name}.txt", console.licence)
    r.add_get("/api/hello", console.hello)
    r.add_post("/api/setup", console.setup)
    r.add_post("/api/login", console.login)
    r.add_post("/api/logout", console.logout)
    r.add_post("/api/password", console.password)
    r.add_get("/api/status", console.status)
    r.add_get("/api/events", console.events)
    r.add_get("/api/animation", console.animation)
    r.add_get("/api/settings", console.get_settings)
    r.add_put("/api/settings", console.put_settings)
    r.add_post("/api/microphone", console.microphone)
    r.add_post("/api/family-safe", console.family_safe)
    r.add_post("/api/storage-recovered/seen", console.storage_seen)
    r.add_get("/api/keys", console.get_keys)
    r.add_put("/api/keys/{name}", console.put_key)
    r.add_post("/api/test/{which}", console.test_provider)
    r.add_get("/api/models", console.models)
    r.add_post("/api/llm/find", console.find_llm)
    r.add_get("/api/voices", console.voices)
    r.add_get("/api/characters", console.list_characters)
    r.add_post("/api/characters", console.upload_character)
    r.add_post("/api/characters/{cid}/activate", console.activate)
    r.add_delete("/api/characters/{cid}", console.delete_character)
    r.add_get("/api/characters/{cid}/preview", console.preview)
    r.add_get("/api/characters/{cid}/export", console.export)
    r.add_get("/api/characters/{cid}/persona", console.get_persona)
    r.add_put("/api/characters/{cid}/persona", console.put_persona)
    r.add_post("/api/characters/{cid}/voice", console.clone_voice)
    r.add_get("/api/characters/{cid}/emotions", console.get_emotions)
    r.add_put("/api/characters/{cid}/emotions", console.put_emotions)
    r.add_get("/api/characters/{cid}/movement", console.get_movement)
    r.add_put("/api/characters/{cid}/movement", console.put_movement)
    r.add_delete("/api/characters/{cid}/movement", console.reset_movement)
    r.add_put("/api/characters/{cid}/temperament", console.put_temperament)
    r.add_put("/api/characters/{cid}/own-motions", console.put_own_motions)
    r.add_get("/api/characters/{cid}/animations", console.get_animations)
    r.add_put("/api/characters/{cid}/animations", console.put_animations)
    r.add_post("/api/characters/{cid}/animations/{role}", console.add_animation)
    r.add_delete("/api/characters/{cid}/animations/{role}", console.delete_animation)
    r.add_post("/api/characters/{cid}/animations/{role}/try", console.try_animation)
    r.add_get("/api/characters/{cid}/background", console.get_character_background)
    r.add_put("/api/characters/{cid}/background", console.put_character_background)
    r.add_get("/api/backgrounds", console.get_backgrounds)
    r.add_get("/api/day", console.get_day)
    r.add_get("/api/places", console.find_places)
    r.add_post("/api/weather/check", console.check_weather)
    r.add_post("/api/alarms/stop", console.stop_alarm)
    r.add_post("/api/alarms/try", console.try_wake_up)
    r.add_delete("/api/timers/{label}", console.cancel_timer)
    r.add_post("/api/backgrounds", console.add_background)
    r.add_put("/api/backgrounds/default", console.put_background_default)
    r.add_get("/api/backgrounds/{picture}/thumb", console.background_thumbnail)
    r.add_delete("/api/backgrounds/{picture}", console.delete_background)
    r.add_get("/api/motions", console.get_motions)
    r.add_post("/api/motions/try", console.try_motion)
    r.add_put("/api/motions/{name}", console.put_motion)
    r.add_delete("/api/motions/{name}", console.delete_motion)
    r.add_get("/api/record", console.get_recording)
    r.add_post("/api/record", console.start_recording)
    r.add_delete("/api/record", console.end_recording)
    _phone_routes(r, console)
    r.add_get("/api/live", console.get_live)
    r.add_put("/api/live", console.put_live)
    r.add_post("/api/live/app", console.connect_app)
    r.add_delete("/api/live/app", console.disconnect_app)
    r.add_post("/api/live/recentre", console.live_recentre)
    r.add_post("/api/live/record", console.live_record)
    app.on_startup.append(console.on_startup)
    app.on_cleanup.append(console.on_cleanup)
    r.add_post("/api/stage/preview", console.stage_preview)
    r.add_post("/api/cards/read", console.read_card)
    r.add_get("/api/memory/{cid}", console.get_memory)
    r.add_put("/api/memory/{cid}", console.put_memory)
    r.add_delete("/api/memory/{cid}", console.clear_memory)
    r.add_post("/api/memory/{cid}/new", console.new_conversation)
    r.add_get("/api/memory/{cid}/sessions/{id}", console.get_session)
    r.add_put("/api/memory/{cid}/sessions/{id}", console.put_session)
    r.add_delete("/api/memory/{cid}/sessions/{id}", console.delete_session)
    r.add_post("/api/memory/{cid}/items", console.add_memory_item)
    r.add_put("/api/memory/{cid}/items/{id}", console.put_memory_item)
    r.add_delete("/api/memory/{cid}/items/{id}", console.delete_memory_item)
    r.add_put("/api/memory/{cid}/diary/{day}", console.put_diary)
    r.add_delete("/api/memory/{cid}/diary/{day}", console.delete_diary)
    r.add_post("/api/say", console.say)
    r.add_post("/api/button", console.press)
    r.add_post("/api/interrupt", console.interrupt)
    r.add_post("/api/timezone", console.set_timezone)
    r.add_post("/api/reset", console.reset)
    r.add_get("/api/health", console.health)
    r.add_get("/api/support-report", console.support_report)
    r.add_get("/api/backup", console.get_backup)
    r.add_put("/api/backup", console.put_backup)
    r.add_get("/api/update", console.get_update)
    r.add_post("/api/update/check", console.check_update)
    r.add_post("/api/update/install", console.install_update)
    r.add_put("/api/update/file", console.upload_update)
    r.add_post("/api/update/rollback", console.rollback_update)
    r.add_put("/api/update/settings", console.put_update_settings)
    r.add_get("/api/wifi", console.wifi_status)
    r.add_post("/api/wifi", console.wifi_join)
    if os.path.isdir(STATIC):
        r.add_static("/static/", STATIC)
    return app
