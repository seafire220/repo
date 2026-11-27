"""Settings, secrets and the console password, kept in files on the device.

Settings are ordinary choices (which provider, which voice, volume). Secrets
are API keys. They live in a separate file readable only by the service
user, are never returned by the web API (only whether each is set), and are
never written to logs or character packages.
"""
import copy
import hashlib
import hmac
import json
import os
import secrets as pysecrets
import tempfile
import threading

DEFAULT_SETTINGS = {
    # provider "selfhosted": a model on the owner's own computer or server; `server` says which kind
    # (providers.LOCAL_SERVERS) and `context` how much text it takes, in tokens.
    "llm": {"provider": "", "model": "", "base_url": "", "temperature": 0.8, "max_tokens": 400, "server": "ollama",
            "context": 0},
    "stt": {"provider": "local", "model": "", "base_url": "", "language": "en"},
    # The voice: "local" speaks on the device for free (a Piper voice; `voice` is a speaker number).
    "tts": {"provider": "local", "model": "", "voice": "", "base_url": "", "speed": 1.0},
    "listening": {"mode": "auto", "end_silence": 0.8, "max_turn": 30.0, "voice_interrupt": False},
    # gestures: the character may choose gestures as it speaks; random_gestures: its mood starts
    # gestures by itself now and then.
    "conversation": {"history_turns": 12, "memory": True, "web_search": True, "gestures": True,
                     "random_gestures": True},
    "active_character": "",
    # Which drawing to show when a character has both: "live2d" (the Live2D-format copy, played
    # with Ayagami) or "inochi2d" (the Inochi2D puppet). Either is used when it is the only one.
    "character_format": "live2d",
    "volume": 0.8,
    # Speaking first (docs/31-memory.md): when someone seems to be back after `after_hours`, the character
    # greets them, outside quiet hours (from `quiet_from` to `quiet_to` o'clock). Off unless the owner turns it on.
    "speak_first": {"on": False, "after_hours": 3, "quiet_from": 22, "quiet_to": 8},
    # Software updates (docs/32-updates.md): look for them now and then, and install them at night.
    "updates": {"check": True, "auto_install": True},
    # What the character says, written on the screen; `heard` also shows what it heard; `size` is
    # "normal" or "large".
    "captions": {"on": False, "heard": False, "size": "normal"},
    # The microphone, off when the owner says so (the button held, or the settings page). A mute
    # switch, if the device has one, wins over this.
    "microphone": {"muted": False},
    # The screen: dark (or dim) at night, from `sleep_from` to `sleep_to` o'clock, when nobody is
    # talking to the character; `brightness` in per cent, where the screen's backlight can be set.
    "screen": {"sleep": False, "sleep_from": 23, "sleep_to": 7, "how": "off", "brightness": 100},
    # Rules for every reply, for homes with children (docs/28-user-guide.md).
    "family_safe": {"on": False},
    # Saying the character's name starts listening, as the button does; `phrase` "" means "hey" and
    # its name; `sensitivity` "low", "normal" or "high".
    "wake_word": {"on": False, "phrase": "", "sensitivity": "normal"},
}

# The secret names the device accepts: one key per provider.
SECRET_NAMES = (
    "openai", "anthropic", "openai_compatible", "elevenlabs",
    "cartesia", "fishaudio", "inworld", "minimax", "deepgram", "google_tts",
    "groq", "openrouter", "search", "local_llm",
)


def write_atomic(path, data, mode=0o644):
    """Write a file so that a power cut leaves either the old or the new file, never half of one."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Store:
    """Everything the device remembers apart from characters and memories."""

    def __init__(self, folder):
        self.folder = folder
        self._lock = threading.Lock()
        os.makedirs(folder, exist_ok=True)
        self.settings_path = os.path.join(folder, "settings.json")
        self.secrets_path = os.path.join(folder, "secrets.json")
        self.password_path = os.path.join(folder, "console-password")

    # --- settings
    def settings(self):
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        return _merge(DEFAULT_SETTINGS, saved)

    def update_settings(self, changes):
        """Merge changes into the settings. Unknown top-level names are refused."""
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError("unknown settings: %s" % ", ".join(sorted(unknown)))
        for key, value in changes.items():
            if isinstance(DEFAULT_SETTINGS[key], dict) and not isinstance(value, dict):
                raise ValueError("%s must be an object" % key)
        if changes.get("character_format", "live2d") not in ("live2d", "inochi2d"):
            raise ValueError("character_format must be live2d or inochi2d")
        first = changes.get("speak_first") or {}
        if "on" in first and not isinstance(first["on"], bool):
            raise ValueError("speak_first.on must be true or false")
        for name, low, high in (("after_hours", 0.5, 168), ("quiet_from", 0, 23), ("quiet_to", 0, 23)):
            if name in first and (not isinstance(first[name], (int, float)) or isinstance(first[name], bool)
                                  or not low <= first[name] <= high):
                raise ValueError("speak_first.%s must be a number from %s to %s" % (name, low, high))
        _check_choices(changes)
        with self._lock:
            merged = _merge(self.settings(), changes)
            write_atomic(self.settings_path, json.dumps(merged, indent=1))
        return merged

    # --- secrets
    def _secrets(self):
        try:
            with open(self.secrets_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def secret(self, name):
        """The key itself, for the provider adapters only."""
        return self._secrets().get(name, "")

    def secret_status(self):
        """Which keys are set, without their values."""
        saved = self._secrets()
        return {name: bool(saved.get(name)) for name in SECRET_NAMES}

    def set_secret(self, name, value):
        if name not in SECRET_NAMES:
            raise ValueError("unknown key name: %s" % name)
        value = (value or "").strip()
        if len(value) > 500 or any(c in value for c in "\r\n\0"):
            raise ValueError("that does not look like an API key")
        with self._lock:
            saved = self._secrets()
            if value:
                saved[name] = value
            else:
                saved.pop(name, None)
            write_atomic(self.secrets_path, json.dumps(saved), mode=0o600)

    # --- console password
    def has_password(self):
        return os.path.exists(self.password_path)

    def set_password(self, password):
        if len(password) < 8:
            raise ValueError("the password must be at least 8 characters")
        salt = pysecrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1)
        write_atomic(self.password_path, (salt.hex() + ":" + digest.hex()), mode=0o600)

    def check_password(self, password):
        try:
            with open(self.password_path, encoding="utf-8") as f:
                salt_hex, digest_hex = f.read().strip().split(":")
        except (OSError, ValueError):
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)


def _check_choices(changes):
    """The newer settings' values: switches true or false, hours 0 to 23, choices from their lists."""
    def switch(group, name):
        value = (changes.get(group) or {}).get(name)
        if value is not None and not isinstance(value, bool):
            raise ValueError("%s.%s must be true or false" % (group, name))

    def choice(group, name, allowed):
        value = (changes.get(group) or {}).get(name)
        if value is not None and value not in allowed:
            raise ValueError("%s.%s must be one of: %s" % (group, name, ", ".join(allowed)))

    def number(group, name, low, high):
        value = (changes.get(group) or {}).get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high):
            raise ValueError("%s.%s must be a number from %s to %s" % (group, name, low, high))

    for group, name in (("captions", "on"), ("captions", "heard"), ("microphone", "muted"), ("screen", "sleep"),
                        ("family_safe", "on"), ("wake_word", "on")):
        switch(group, name)
    choice("captions", "size", ("normal", "large"))
    choice("screen", "how", ("off", "dim"))
    choice("wake_word", "sensitivity", ("low", "normal", "high"))
    number("screen", "sleep_from", 0, 23)
    number("screen", "sleep_to", 0, 23)
    number("screen", "brightness", 10, 100)
    phrase = (changes.get("wake_word") or {}).get("phrase")
    if phrase is not None:
        if not isinstance(phrase, str) or len(phrase) > 40:
            raise ValueError("wake_word.phrase must be a few words")
        letters = phrase.replace(" ", "").replace("'", "").replace("-", "")
        if letters and not letters.isalpha():
            raise ValueError("the wake phrase can only have letters")


def redact(text, store):
    """Remove any stored key from a piece of text before it is logged or shown."""
    if not text:
        return text
    for name in SECRET_NAMES:
        value = store.secret(name)
        if value and len(value) >= 6:
            text = text.replace(value, "[%s key]" % name)
    return text
