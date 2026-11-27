"""Provider adapters: language models, speech recognition and voices.

Every adapter takes its key from the device's secret store at the moment it
is built and never logs it. Each has `test()` for the console's test button.

Language models stream text: `async for piece in llm.stream(system, messages)`.
Speech recognition takes 16-bit mono PCM and returns text.
Voices stream 16-bit mono PCM at `tts.rate` samples per second.
"""
import asyncio
import base64
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import urllib.parse
import wave

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)


class ProviderError(Exception):
    """A provider failed. `retry` says whether trying again might help."""

    def __init__(self, message, retry=False):
        super().__init__(message)
        self.retry = retry


# Known OpenAI-compatible services, so the owner only has to pick a name and paste a key.
OPENAI_COMPATIBLE = {
    "openai": {"base_url": "https://api.openai.com/v1", "secret": "openai"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "groq"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "secret": "openrouter"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "secret": "openai_compatible"},
    "custom": {"base_url": "", "secret": "openai_compatible"},
}


# Language-model servers people run on their own computers: the port each listens on unless told
# otherwise, and the path of its OpenAI-compatible API (Ollama is spoken to in its own API, which lets
# the device set the model's context size and keep it loaded between replies).
LOCAL_SERVERS = {
    "ollama": {"label": "Ollama", "port": 11434, "path": ""},
    "lmstudio": {"label": "LM Studio", "port": 1234, "path": "/v1"},
    "llamacpp": {"label": "llama.cpp server", "port": 8080, "path": "/v1"},
    "vllm": {"label": "vLLM", "port": 8000, "path": "/v1"},
    "koboldcpp": {"label": "KoboldCpp", "port": 5001, "path": "/v1"},
    "textgen": {"label": "text-generation-webui", "port": 5000, "path": "/v1"},
    "jan": {"label": "Jan", "port": 1337, "path": "/v1"},
    "gpt4all": {"label": "GPT4All", "port": 4891, "path": "/v1"},
    "openai": {"label": "Other (OpenAI-compatible)", "port": None, "path": "/v1"},
}
# A model on a home computer can take a while to start answering (it may first be loaded from disk).
LOCAL_PATIENCE = 120.0
# How much text (in tokens) the model is given at most, unless the owner says otherwise.
LOCAL_CONTEXT = 8192
CLOUD_CONTEXT = 32000


def local_address(address, kind):
    """A server's base address from what the owner typed ("192.168.1.20", "laptop.local:1234",
    "http://host:8080/v1" all do), with the kind's usual port and path where none is given."""
    info = LOCAL_SERVERS.get(kind) or LOCAL_SERVERS["openai"]
    text = (address or "").strip()
    if not text:
        raise ProviderError("type the address of the computer the model runs on")
    if not re.match(r"(?i)^https?://", text):
        text = "http://" + text
    try:
        parts = urllib.parse.urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ProviderError("that does not look like a computer's address: %s" % address)
    if not host:
        raise ProviderError("that does not look like a computer's address: %s" % address)
    port = port or info["port"] or (443 if parts.scheme.lower() == "https" else 80)
    path = parts.path.rstrip("/")
    if kind == "ollama":
        path = re.sub(r"/(v1|api)$", "", path)
    elif not path:
        path = info["path"]
    shown = "[%s]" % host if ":" in host else host
    return "%s://%s:%d%s" % (parts.scheme.lower(), shown, port, path)


class ThinkFilter:
    """Leaves a reasoning model's thinking (between <think> and </think>) out of what is said, even
    when the tags arrive split across pieces, or the opening one is missing."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buffer = ""
        self.inside = False
        self.said = False

    def feed(self, piece):
        self.buffer += piece
        out = ""
        while True:
            if self.inside:
                end = self.buffer.find(self.CLOSE)
                if end < 0:
                    self.buffer = self.buffer[-(len(self.CLOSE) - 1):]
                    return out
                self.buffer = self.buffer[end + len(self.CLOSE):].lstrip()
                self.inside = False
                continue
            start = self.buffer.find(self.OPEN)
            close = self.buffer.find(self.CLOSE)
            if close >= 0 and (start < 0 or close < start):
                # Thinking that began without its opening tag: before anything was said, it all goes.
                if not self.said and not out:
                    self.buffer = self.buffer[close + len(self.CLOSE):].lstrip()
                else:
                    out += self.buffer[:close]
                    self.buffer = self.buffer[close + len(self.CLOSE):]
                continue
            if start >= 0:
                out += self.buffer[:start]
                self.buffer = self.buffer[start + len(self.OPEN):]
                self.inside = True
                continue
            # Keep back what could be the start of a tag.
            keep = 0
            for k in range(1, min(len(self.CLOSE) - 1, len(self.buffer)) + 1):
                tail = self.buffer[-k:]
                if self.OPEN.startswith(tail) or self.CLOSE.startswith(tail):
                    keep = k
            out += self.buffer[:len(self.buffer) - keep]
            self.buffer = self.buffer[len(self.buffer) - keep:]
            if out.strip():
                self.said = True
            return out

    def flush(self):
        rest, self.buffer = ("" if self.inside else self.buffer), ""
        return rest


def _status_error(service, status, body):
    detail = ""
    try:
        data = json.loads(body)
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            detail = err.get("message") or err.get("detail") or err.get("title") or ""
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
        else:
            detail = str(err)
    except (ValueError, AttributeError):
        detail = body[:200] if isinstance(body, str) else ""
    if status in (401, 403):
        return ProviderError("%s refused the key (%d). Check the key in Settings." % (service, status))
    if status == 404:
        return ProviderError("%s does not know that model or address (404). %s" % (service, detail))
    if status == 429:
        return ProviderError("%s is rate-limiting or out of credit (429). %s" % (service, detail), retry=True)
    if status >= 500:
        # The server's own words matter most from a model on the owner's computer ("model requires more
        # system memory than is available").
        return ProviderError(("%s had a server error (%d). %s" % (service, status, detail[:200])).strip(), retry=True)
    return ProviderError("%s refused the request (%d). %s" % (service, status, detail))


async def iter_sse(response):
    """Yield the data of each server-sent event."""
    buffer = b""
    async for chunk in response.content.iter_any():
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line.startswith(b"data:"):
                yield line[5:].strip().decode("utf-8", "replace")
    tail = buffer.rstrip(b"\r")
    if tail.startswith(b"data:"):
        yield tail[5:].strip().decode("utf-8", "replace")


async def iter_ndjson(response):
    """Yield each non-empty line of a newline-delimited JSON stream (handles lines larger than 64 KiB)."""
    buffer = b""
    async for chunk in response.content.iter_any():
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if line:
                yield line.decode("utf-8", "replace")
    tail = buffer.strip()
    if tail:
        yield tail.decode("utf-8", "replace")


class OpenAICompatibleLLM:
    """Chat completions on any OpenAI-compatible service."""

    def __init__(self, base_url, key, model, temperature=0.8, max_tokens=400, service="The language model"):
        if not base_url:
            raise ProviderError("no address is set for this language model")
        if not model:
            raise ProviderError("no model is chosen for the language model")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.service = service
        # Seconds to wait for the first words, and how much text the model takes (tokens).
        self.patience = None
        self.context = CLOUD_CONTEXT

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    async def stream(self, system, messages):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/chat/completions", json=body, headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    thinking = ThinkFilter()
                    async for data in iter_sse(r):
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        if "error" in event:
                            raise ProviderError("%s: %s" % (self.service, event["error"]), retry=True)
                        for choice in event.get("choices") or []:
                            piece = (choice.get("delta") or {}).get("content")
                            if piece:
                                piece = thinking.feed(piece)
                                if piece:
                                    yield piece
                    rest = thinking.flush()
                    if rest:
                        yield rest
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached (%s)" % (self.service, type(error).__name__), retry=True)

    async def models(self):
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.get(self.base_url + "/models", headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached (%s)" % (self.service, type(error).__name__), retry=True)
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    async def test(self):
        reply = ""
        async for piece in self.stream("Reply with the single word: ready", [{"role": "user", "content": "Are you there?"}]):
            reply += piece
        return "replied: %s" % reply.strip()[:60]


class OllamaLLM:
    """A model on the owner's own computer through Ollama's own API: the device sets how much text the
    model takes (Ollama's default is small, and it cuts longer prompts from the start, persona first)
    and keeps the model loaded between replies."""

    def __init__(self, base_url, model, temperature=0.8, max_tokens=400, context=LOCAL_CONTEXT,
                 service="The model on your computer"):
        if not model:
            raise ProviderError("no model is chosen: press List models, or on that computer run: ollama pull <model>")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.context = int(context or LOCAL_CONTEXT)
        self.service = service
        self.patience = LOCAL_PATIENCE

    async def stream(self, system, messages):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens, "num_ctx": self.context},
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=self.patience)) as session:
            try:
                async with session.post(self.base_url + "/api/chat", json=body) as r:
                    if r.status == 404:
                        raise ProviderError("Ollama has no model called %s. On that computer, run: ollama pull %s"
                                            % (self.model, self.model))
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    thinking = ThinkFilter()
                    async for line in r.content:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("error"):
                            raise ProviderError("%s: %s" % (self.service, event["error"]), retry=True)
                        piece = (event.get("message") or {}).get("content") or ""
                        piece = thinking.feed(piece) if piece else ""
                        if piece:
                            yield piece
                        if event.get("done"):
                            break
                    rest = thinking.flush()
                    if rest:
                        yield rest
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached at %s (%s). Is the computer on, and Ollama "
                                    "open to the network (OLLAMA_HOST=0.0.0.0)?" % (self.service, self.base_url,
                                                                                     type(error).__name__), retry=True)

    async def models(self):
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.get(self.base_url + "/api/tags") as r:
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached (%s)" % (self.service, type(error).__name__), retry=True)
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))

    async def test(self):
        reply = ""
        async for piece in self.stream("Reply with the single word: ready", [{"role": "user", "content": "Are you there?"}]):
            reply += piece
        return "replied: %s" % reply.strip()[:60]


def _own_addresses():
    """This device's addresses on its home network (IPv4), found without sending anything."""
    found = set()
    for probe in ("10.255.255.255", "172.31.255.255", "192.168.255.255", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                found.add(s.getsockname()[0])
        except OSError:
            continue
    return sorted(a for a in found if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback)


def neighbours():
    """The addresses to look at for servers: this computer, and the others on the same home network
    (the 254 addresses around each of this device's own; private networks only)."""
    hosts = ["127.0.0.1"]
    for own in _own_addresses():
        net = ipaddress.ip_network(own + "/24", strict=False)
        hosts += [str(h) for h in net.hosts() if str(h) != own]
    return hosts


async def _port_open(host, port, timeout):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def _identify(session, host, port):
    """What answers on a host's port: an Ollama server, or another OpenAI-compatible one, with its models."""
    base = "http://%s:%d" % (host, port)
    try:
        async with session.get(base + "/api/tags") as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                models = sorted(m.get("name", "") for m in (data or {}).get("models", []) if m.get("name"))
                return {"kind": "ollama", "label": "Ollama", "address": base, "models": models}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError):
        pass
    try:
        async with session.get(base + "/v1/models") as r:
            if r.status in (200, 401):
                data = await r.json(content_type=None) if r.status == 200 else {}
                models = sorted(m.get("id", "") for m in (data or {}).get("data", []) if m.get("id"))
                kind = next((k for k, v in LOCAL_SERVERS.items() if v["port"] == port and k != "ollama"), "openai")
                return {"kind": kind, "label": LOCAL_SERVERS[kind]["label"], "address": base + "/v1", "models": models,
                        "needs_key": r.status == 401}
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError):
        pass
    return None


async def find_servers(hosts=None, ports=None, timeout=0.6, limit=128):
    """Language-model servers on the home network (or on the given hosts): each one's kind, address and
    models. Only private addresses are looked at."""
    ports = ports or sorted({v["port"] for v in LOCAL_SERVERS.values() if v["port"]})
    if hosts is None:
        hosts = neighbours()
    allowed = []
    for h in hosts:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(h, None, family=socket.AF_INET)
            address = infos[0][4][0]
        except (OSError, IndexError):
            continue
        if ipaddress.ip_address(address).is_private:
            allowed.append(address)
    gate = asyncio.Semaphore(limit)

    async def check(host, port):
        async with gate:
            return (host, port) if await _port_open(host, port, timeout) else None

    open_ports = [hp for hp in await asyncio.gather(*(check(h, p) for h in dict.fromkeys(allowed) for p in ports)) if hp]
    found = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
        for result in await asyncio.gather(*(_identify(session, h, p) for h, p in open_ports)):
            if result:
                found.append(result)
    return found


def pcm_to_wav(pcm, rate):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class OpenAICompatibleSTT:
    """Transcription on an OpenAI-compatible service (OpenAI, Groq and others)."""

    def __init__(self, base_url, key, model, language="en", service="Speech recognition"):
        if not base_url or not model:
            raise ProviderError("speech recognition needs an address and a model")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.language = language
        self.service = service

    async def transcribe(self, pcm, rate=16000):
        form = aiohttp.FormData()
        form.add_field("file", pcm_to_wav(pcm, rate), filename="speech.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        if self.language:
            form.add_field("language", self.language)
        form.add_field("response_format", "json")
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/audio/transcriptions", data=form, headers=headers) as r:
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached (%s)" % (self.service, type(error).__name__), retry=True)
        return (data.get("text") or "").strip()

    async def test(self):
        silence = b"\0\0" * 16000
        await self.transcribe(silence)
        return "accepted a test recording"


class LocalSTT:
    """Speech recognition on the device with sherpa-onnx. Free; no network.

    `model_dir` holds a sherpa-onnx offline model: Moonshine (preprocess,
    encode, uncached_decode, cached_decode, tokens) or Whisper (encoder,
    decoder, tokens).
    """

    def __init__(self, model_dir, threads=2):
        import glob
        import os
        try:
            import sherpa_onnx
        except ImportError:
            raise ProviderError("on-device speech recognition is not installed (sherpa-onnx)")
        if not os.path.isdir(model_dir):
            raise ProviderError("no on-device speech model is installed at %s" % model_dir)

        def find(pattern):
            found = sorted(glob.glob(os.path.join(model_dir, pattern)))
            return found[0] if found else ""

        tokens = find("*tokens.txt")
        if find("preprocess*.onnx"):
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_moonshine(
                preprocessor=find("preprocess*.onnx"), encoder=find("encode*.onnx"),
                uncached_decoder=find("uncached_decode*.onnx"), cached_decoder=find("cached_decode*.onnx"),
                tokens=tokens, num_threads=threads)
        elif find("*encoder*.onnx"):
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=find("*encoder*.onnx"), decoder=find("*decoder*.onnx"), tokens=tokens,
                language="en", task="transcribe", num_threads=threads)
        else:
            raise ProviderError("the on-device speech model at %s is not one this device knows" % model_dir)

    async def transcribe(self, pcm, rate=16000):
        import asyncio
        import numpy as np

        def run():
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            stream = self.recognizer.create_stream()
            stream.accept_waveform(rate, samples)
            self.recognizer.decode_stream(stream)
            return stream.result.text.strip()

        return await asyncio.get_running_loop().run_in_executor(None, run)

    async def test(self):
        await self.transcribe(b"\0\0" * 16000)
        return "the on-device model loaded and ran"


class OpenAICompatibleTTS:
    """Speech on an OpenAI-compatible service, streamed as raw 24 kHz PCM."""

    rate = 24000

    def __init__(self, base_url, key, model, voice, service="The voice service"):
        if not base_url or not model or not voice:
            raise ProviderError("the voice needs an address, a model and a voice name")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.voice = voice
        self.service = service

    async def stream(self, text):
        body = {"model": self.model, "voice": self.voice, "input": text, "response_format": "pcm"}
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/audio/speech", json=body, headers=headers) as r:
                    if r.status != 200:
                        raise _status_error(self.service, r.status, await r.text())
                    async for chunk in r.content.iter_chunked(4800):
                        yield chunk
            except aiohttp.ClientError as error:
                raise ProviderError("%s could not be reached (%s)" % (self.service, type(error).__name__), retry=True)

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


class LocalTTS:
    """A voice on the device: a Piper voice played by sherpa-onnx, with phonemes from espeak-ng.
    Free; no network. `model_dir` holds the voice (`*.onnx`, its `.onnx.json`, `tokens.txt`);
    `voice` picks a speaker by number in voices with several (the VCTK voice has 109)."""

    CHUNK = 0.2      # seconds of sound per piece handed to the speaker

    def __init__(self, model_dir, voice="", speed=1.0, threads=2):
        import glob
        import os
        from . import phonemes
        try:
            import sherpa_onnx
        except ImportError:
            raise ProviderError("the on-device voice is not installed (sherpa-onnx)")
        if not phonemes.espeak_path():
            raise ProviderError("the on-device voice needs espeak-ng, which is not installed")
        models = sorted(glob.glob(os.path.join(model_dir, "*.onnx")))
        if not models or not os.path.exists(os.path.join(model_dir, "tokens.txt")):
            raise ProviderError("no on-device voice is installed at %s" % model_dir)
        from .piper_voice import cluster_symbols
        self.espeak_voice = "en-us"
        description = {}
        try:
            with open(models[0] + ".json", encoding="utf-8") as f:
                description = json.load(f)
        except (OSError, ValueError):
            pass
        self.espeak_voice = (description.get("espeak") or {}).get("voice") or "en-us"
        # Voices from Piper 1.5 on speak vowel pairs as one symbol (piper_voice.py), and say how much
        # variation they were trained to sound best with.
        self.clusters = cluster_symbols(description)
        settings = description.get("inference") or {}
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=models[0], tokens=os.path.join(model_dir, "tokens.txt"),
            data_dir=os.path.join(model_dir, "espeak-ng-data") if os.path.isdir(os.path.join(model_dir, "espeak-ng-data")) else "",
            noise_scale=float(settings.get("noise_scale", 0.667)), noise_scale_w=float(settings.get("noise_w", 0.8)),
            length_scale=float(settings.get("length_scale", 1.0)))
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=threads), max_num_sentences=1))
        self.rate = self.tts.sample_rate
        self.speakers = self.tts.num_speakers
        self.speed = float(speed) if speed else 1.0
        self.catalogue = {}
        try:
            with open(os.path.join(os.path.dirname(__file__), "voices.json"), encoding="utf-8") as f:
                loaded = json.load(f)
            if os.path.basename(models[0]).startswith(loaded.get("model", "?")):
                self.catalogue = loaded
        except (OSError, ValueError):
            pass
        self.sid = 0
        self.set_voice(voice)
        self._sherpa = sherpa_onnx
        self._phonemize = phonemes.phonemize

    def set_voice(self, voice):
        """Choose a speaker by number; anything else takes the default (the surveyed clear voice, or the first)."""
        text = str(voice or "").strip()
        default = int(self.catalogue.get("default", 0)) if self.catalogue else 0
        self.sid = int(text) if text.isdigit() and int(text) < max(self.speakers, 1) else default

    async def voices(self):
        """The surveyed clear voices, for the settings page's list."""
        return [{"id": str(v["id"]), "name": "%s (%d Hz)" % (v["label"], round(v["pitch_hz"]))}
                for v in self.catalogue.get("voices", [])]

    def _speak(self, text):
        import numpy as np
        from .phonemes import PhonemeError
        try:
            phonemes = self._phonemize(text, self.espeak_voice)
        except PhonemeError as error:
            raise ProviderError("the on-device voice could not read the text (%s)" % error)
        if not phonemes:
            return b""
        from .piper_voice import merge_clusters
        phonemes = merge_clusters(phonemes, self.clusters)
        config = self._sherpa.GenerationConfig()
        config.phoneme_codepoints = [[ord(c) for c in phonemes]]
        config.sid = self.sid
        config.speed = self.speed
        audio = self.tts.generate(text, config)
        samples = np.clip(np.asarray(audio.samples, dtype=np.float32), -1.0, 1.0)
        return (samples * 32767).astype(np.int16).tobytes()

    async def full(self, text):
        """A whole sentence's sound at once (it is made whole anyway), so its syllables can be placed."""
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._speak, text)

    async def stream(self, text):
        import asyncio
        pcm = await asyncio.get_running_loop().run_in_executor(None, self._speak, text)
        step = int(self.rate * self.CHUNK) * 2
        for start in range(0, len(pcm), step):
            yield pcm[start:start + step]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing, one two three."):
            size += len(chunk)
        return "spoke %.1f seconds of audio on the device" % (size / 2 / self.rate)


# Voice services that support custom voice cloning from a recording.
VOICE_CLONING_PROVIDERS = {
    "elevenlabs": {
        "label": "ElevenLabs",
        "secret": "elevenlabs",
        "default_model": "eleven_flash_v2_5",
        "max_bytes": 20 * 1024 * 1024,
        "formats": ("wav", "mp3", "m4a", "ogg", "flac", "webm"),
        "languages": False,
    },
    "cartesia": {
        "label": "Cartesia",
        "secret": "cartesia",
        "default_model": "sonic-3.6",
        "max_bytes": 16 * 1024 * 1024,
        "formats": ("wav", "mp3", "ogg", "flac", "webm"),
        "languages": True,
    },
    "fishaudio": {
        "label": "Fish Audio",
        "secret": "fishaudio",
        "default_model": "s2.1-pro",
        "max_bytes": 20 * 1024 * 1024,
        # The formats Fish Audio documents for voice samples.
        "formats": ("wav", "mp3", "flac"),
        "languages": False,
    },
    "inworld": {
        "label": "Inworld AI",
        "secret": "inworld",
        "default_model": "inworld-tts-2",
        "max_bytes": 12 * 1024 * 1024,
        # Inworld's API takes WAV and MP3 samples (its web page takes WebM too), and uses the first 30 s.
        "formats": ("wav", "mp3"),
        "languages": True,
    },
    "minimax": {
        "label": "MiniMax",
        "secret": "minimax",
        "default_model": "speech-2.8-turbo",
        "max_bytes": 20 * 1024 * 1024,
        "formats": ("wav", "mp3", "m4a"),
        "languages": False,
    },
}

# Voice services with voices to choose from, but no cloning through their API.
VOICE_ONLY_PROVIDERS = {
    "deepgram": {"label": "Deepgram", "secret": "deepgram"},
    "google": {"label": "Google Cloud", "secret": "google_tts"},
}

TTS_PROVIDERS = ("local",) + tuple(VOICE_CLONING_PROVIDERS) + tuple(VOICE_ONLY_PROVIDERS) + tuple(OPENAI_COMPATIBLE)


def _bearer(key):
    key = (key or "").strip()
    return key if key.lower().startswith("bearer ") else "Bearer " + key


def _inworld_auth(key):
    key = (key or "").strip()
    return key if key.lower().startswith(("basic ", "bearer ")) else "Basic " + key


def sniff_audio(data):
    """Identify an audio recording from its header bytes -> (extension, mime_type)."""
    if not data or len(data) < 4:
        raise ProviderError("send a WAV, MP3, M4A, OGG, FLAC or WebM audio recording")
    if data[:4] == b"RIFF":
        if len(data) >= 12 and data[8:12] not in (b"WAVE", b"\0\0\0\0"):
            raise ProviderError("that RIFF file is not a WAV audio recording")
        return "wav", "audio/wav"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3", "audio/mpeg"
    if data[:4] == b"fLaC":
        return "flac", "audio/flac"
    if data[:4] == b"OggS":
        return "ogg", "audio/ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm", "audio/webm"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a", "audio/mp4"
    raise ProviderError("send a WAV, MP3, M4A, OGG, FLAC or WebM audio recording")


def _cartesia_lang(language):
    code = (language or "en").strip().lower().replace("_", "-").split("-")[0]
    if not code or code == "auto" or not re.fullmatch(r"[a-z]{2}", code):
        return "en"
    return code


def _inworld_lang(language):
    """Inworld's `languageCode`: a locale such as "en", "ja" or "en-GB" ("en" picks its default accent), or
    None to let Inworld detect the language from the recording. (The older `langCode` enum knows only 15
    languages.)"""
    raw = (language or "en").strip().replace("_", "-")
    if not raw or raw.lower() == "auto":
        return None
    parts = raw.split("-")
    if not re.fullmatch(r"[A-Za-z]{2,3}", parts[0]):
        return None
    return "-".join([parts[0].lower()] + [p.upper() if len(p) == 2 else p for p in parts[1:]])


def _minimax_voice_id(name):
    """Build a valid unique MiniMax voice_id: 8-256 chars, starts with a letter, ends with alnum."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "voice")).strip("_-")[:32]
    if not slug or not slug[0].isalpha():
        slug = "v_" + slug if slug else "voice"
    return "c_%s_%s" % (slug, secrets.token_hex(4))


def _minimax_check(data):
    """Raise ProviderError when MiniMax reports a non-zero business status inside HTTP 200."""
    if not isinstance(data, dict):
        return
    base = data.get("base_resp") or {}
    code = base.get("status_code")
    if code in (None, 0):
        return
    msg = base.get("status_msg") or "request failed"
    if code in (1004, 2049):
        raise ProviderError("MiniMax refused the key (%s). Check the key in Settings." % msg)
    if code in (1000, 1001, 1002, 1039):
        raise ProviderError("MiniMax is busy or rate-limiting (%s: %s)" % (code, msg), retry=True)
    raise ProviderError("MiniMax refused the request (%s: %s)" % (code, msg))


class ElevenLabsTTS:
    """ElevenLabs voices, including cloned ones, streamed as raw 24 kHz PCM."""

    rate = 24000
    base_url = "https://api.elevenlabs.io/v1"

    def __init__(self, key, voice="", model="eleven_flash_v2_5", require_voice=True):
        if not key:
            raise ProviderError("no ElevenLabs key is set")
        if require_voice and not voice:
            raise ProviderError("no ElevenLabs voice is chosen")
        self.key = key
        self.voice = voice or ""
        self.model = model or "eleven_flash_v2_5"

    async def stream(self, text):
        if not self.voice:
            raise ProviderError("no ElevenLabs voice is chosen")
        vid = urllib.parse.quote(self.voice, safe="")
        url = "%s/text-to-speech/%s/stream?output_format=pcm_%d" % (self.base_url, vid, self.rate)
        body = {"text": text, "model_id": self.model}
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(url, json=body, headers={"xi-api-key": self.key}) as r:
                    if r.status != 200:
                        raise _status_error("ElevenLabs", r.status, await r.text())
                    async for chunk in r.content.iter_chunked(4800):
                        yield chunk
            except aiohttp.ClientError as error:
                raise ProviderError("ElevenLabs could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.get(self.base_url + "/voices", headers={"xi-api-key": self.key}) as r:
                    if r.status != 200:
                        raise _status_error("ElevenLabs", r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("ElevenLabs could not be reached (%s)" % type(error).__name__, retry=True)
        items = [v for v in (data or {}).get("voices", []) if v.get("voice_id")]
        items.sort(key=lambda v: 0 if v.get("category") == "cloned" else 1)
        return [{"id": v["voice_id"],
                 "name": "%s (cloned)" % v.get("name", v["voice_id"]) if v.get("category") == "cloned"
                 else v.get("name", v["voice_id"])} for v in items]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice_elevenlabs(key, name, sample_wav, filename="sample.wav", content_type="audio/wav", base_url=None):
    """Make a cloned voice on ElevenLabs from a sample; returns its voice id."""
    form = aiohttp.FormData()
    form.add_field("name", (name or "Voice")[:100])
    form.add_field("files", sample_wav, filename=filename, content_type=content_type)
    url = (base_url or ElevenLabsTTS.base_url).rstrip("/") + "/voices/add"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.post(url, data=form, headers={"xi-api-key": key}) as r:
                if r.status not in (200, 201):
                    raise _status_error("ElevenLabs", r.status, await r.text())
                data = await r.json(content_type=None)
        except aiohttp.ClientError as error:
            raise ProviderError("ElevenLabs could not be reached (%s)" % type(error).__name__, retry=True)
    voice_id = (data or {}).get("voice_id")
    if not voice_id:
        raise ProviderError("ElevenLabs did not return a voice id")
    return voice_id


class CartesiaTTS:
    """Cartesia Sonic voices, including instant clones, streamed as raw 24 kHz PCM."""

    rate = 24000
    base_url = "https://api.cartesia.ai"
    api_version = "2026-08-14"

    def __init__(self, key, voice="", model="sonic-3.6", require_voice=True):
        if not key:
            raise ProviderError("no Cartesia key is set")
        if require_voice and not voice:
            raise ProviderError("no Cartesia voice is chosen")
        self.key = key
        self.voice = voice or ""
        self.model = model or "sonic-3.6"

    def _headers(self):
        return {"Authorization": _bearer(self.key), "Cartesia-Version": self.api_version}

    async def stream(self, text):
        if not self.voice:
            raise ProviderError("no Cartesia voice is chosen")
        body = {
            "model_id": self.model,
            "transcript": text,
            "voice": self.voice,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.rate,
            },
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/tts/bytes", json=body, headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("Cartesia", r.status, await r.text())
                    async for chunk in r.content.iter_chunked(4800):
                        yield chunk
            except aiohttp.ClientError as error:
                raise ProviderError("Cartesia could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        # The owner's own voices (their clones) are asked for separately: the library's first page of 100
        # need not include them.
        raw = []
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                for query in ("is_owner=true&limit=100", "limit=100"):
                    async with session.get(self.base_url + "/voices?" + query, headers=self._headers()) as r:
                        if r.status != 200:
                            raise _status_error("Cartesia", r.status, await r.text())
                        data = await r.json(content_type=None)
                    raw += data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except aiohttp.ClientError as error:
                raise ProviderError("Cartesia could not be reached (%s)" % type(error).__name__, retry=True)
        items, seen = [], set()
        for v in raw:
            if isinstance(v, dict) and v.get("id") and v["id"] not in seen:
                seen.add(v["id"])
                items.append(v)
        items.sort(key=lambda v: 0 if v.get("is_owner") else 1)
        return [{"id": v["id"],
                 "name": "%s (yours)" % v.get("name", v["id"]) if v.get("is_owner")
                 else v.get("name", v["id"])} for v in items]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice_cartesia(key, name, sample, language="en", filename="sample.wav", content_type="audio/wav",
                               base_url=None):
    """Make an instant cloned voice on Cartesia; returns its voice id."""
    form = aiohttp.FormData()
    form.add_field("clip", sample, filename=filename, content_type=content_type)
    form.add_field("name", (name or "Voice")[:100])
    form.add_field("language", _cartesia_lang(language))
    form.add_field("access", "private")
    headers = {"Authorization": _bearer(key), "Cartesia-Version": CartesiaTTS.api_version}
    url = (base_url or CartesiaTTS.base_url).rstrip("/") + "/voices/clone"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.post(url, data=form, headers=headers) as r:
                if r.status not in (200, 201):
                    raise _status_error("Cartesia", r.status, await r.text())
                data = await r.json(content_type=None)
        except aiohttp.ClientError as error:
            raise ProviderError("Cartesia could not be reached (%s)" % type(error).__name__, retry=True)
    voice_id = (data or {}).get("id")
    if not voice_id:
        raise ProviderError("Cartesia did not return a voice id")
    return voice_id


class FishAudioTTS:
    """Fish Audio voices, including cloned voice models, streamed as raw 24 kHz PCM."""

    rate = 24000
    base_url = "https://api.fish.audio"

    def __init__(self, key, voice="", model="s2.1-pro", require_voice=True):
        if not key:
            raise ProviderError("no Fish Audio key is set")
        if require_voice and not voice:
            raise ProviderError("no Fish Audio voice is chosen")
        self.key = key
        self.voice = voice or ""
        self.model = model or "s2.1-pro"

    async def stream(self, text):
        if not self.voice:
            raise ProviderError("no Fish Audio voice is chosen")
        headers = {"Authorization": _bearer(self.key), "model": self.model}
        body = {
            "text": text,
            "reference_id": self.voice,
            "format": "pcm",
            "sample_rate": self.rate,
            "latency": "balanced",
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/v1/tts", json=body, headers=headers) as r:
                    if r.status != 200:
                        raise _status_error("Fish Audio", r.status, await r.text())
                    async for chunk in r.content.iter_chunked(4800):
                        yield chunk
            except aiohttp.ClientError as error:
                raise ProviderError("Fish Audio could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        headers = {"Authorization": _bearer(self.key)}
        seen, out = set(), []
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                for query, tag in (("self=true&page_size=50", " (yours)"), ("page_size=50&sort_by=task_count", "")):
                    async with session.get("%s/model?%s" % (self.base_url, query), headers=headers) as r:
                        if r.status != 200:
                            if out:
                                break
                            raise _status_error("Fish Audio", r.status, await r.text())
                        data = await r.json(content_type=None)
                    for item in (data or {}).get("items", []):
                        vid = item.get("_id") or item.get("id")
                        if not vid or vid in seen:
                            continue
                        seen.add(vid)
                        title = item.get("title") or vid
                        out.append({"id": vid, "name": title + tag})
            except aiohttp.ClientError as error:
                raise ProviderError("Fish Audio could not be reached (%s)" % type(error).__name__, retry=True)
        return out

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice_fishaudio(key, name, sample, filename="sample.wav", content_type="audio/wav", base_url=None):
    """Create a private cloned voice model on Fish Audio; returns its model id."""
    form = aiohttp.FormData()
    form.add_field("type", "tts")
    form.add_field("title", (name or "Voice")[:100])
    form.add_field("train_mode", "fast")
    form.add_field("visibility", "private")
    form.add_field("enhance_audio_quality", "true")
    form.add_field("voices", sample, filename=filename, content_type=content_type)
    url = (base_url or FishAudioTTS.base_url).rstrip("/") + "/model"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.post(url, data=form, headers={"Authorization": _bearer(key)}) as r:
                if r.status not in (200, 201):
                    raise _status_error("Fish Audio", r.status, await r.text())
                data = await r.json(content_type=None)
        except aiohttp.ClientError as error:
            raise ProviderError("Fish Audio could not be reached (%s)" % type(error).__name__, retry=True)
    voice_id = (data or {}).get("_id") or (data or {}).get("id")
    if not voice_id:
        raise ProviderError("Fish Audio did not return a voice id")
    return voice_id


class InworldTTS:
    """Inworld AI Realtime TTS voices, including instant clones, streamed as raw 24 kHz PCM."""

    rate = 24000
    base_url = "https://api.inworld.ai"

    def __init__(self, key, voice="", model="inworld-tts-2", require_voice=True):
        if not key:
            raise ProviderError("no Inworld AI key is set")
        if require_voice and not voice:
            raise ProviderError("no Inworld AI voice is chosen")
        self.key = key
        self.voice = voice or ""
        self.model = model or "inworld-tts-2"

    def _headers(self):
        return {
            "Authorization": _inworld_auth(self.key),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def stream(self, text):
        if not self.voice:
            raise ProviderError("no Inworld AI voice is chosen")
        body = {
            "text": text,
            "voiceId": self.voice,
            "modelId": self.model,
            "audioConfig": {
                "audioEncoding": "PCM",
                "sampleRateHertz": self.rate,
            },
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/tts/v1/voice:stream", json=body, headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("Inworld AI", r.status, await r.text())
                    async for line in iter_ndjson(r):
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("error"):
                            err = event["error"]
                            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                            code = err.get("code") if isinstance(err, dict) else None
                            raise ProviderError("Inworld AI: %s" % msg, retry=code in (8, 13, 14))
                        result = event.get("result") if isinstance(event, dict) else None
                        payload = result if isinstance(result, dict) else (event if isinstance(event, dict) else {})
                        b64 = payload.get("audioContent")
                        if b64:
                            try:
                                pcm = base64.b64decode(b64)
                            except ValueError:
                                continue
                            if pcm:
                                yield pcm
            except aiohttp.ClientError as error:
                raise ProviderError("Inworld AI could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        headers = {"Authorization": _inworld_auth(self.key)}
        found = []
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                token = ""
                for _ in range(5):                      # up to 2,500 voices
                    query = {"pageSize": "500"}
                    if token:
                        query["pageToken"] = token
                    async with session.get(self.base_url + "/voices/v1/voices", params=query, headers=headers) as r:
                        if r.status == 404 and not found:
                            async with session.get(self.base_url + "/tts/v1/voices", headers=headers) as r2:
                                if r2.status != 200:
                                    raise _status_error("Inworld AI", r2.status, await r2.text())
                                data = await r2.json(content_type=None)
                            found += (data or {}).get("voices", [])
                            break
                        if r.status != 200:
                            raise _status_error("Inworld AI", r.status, await r.text())
                        data = await r.json(content_type=None)
                    found += (data or {}).get("voices", [])
                    token = (data or {}).get("nextPageToken") or ""
                    if not token:
                        break
            except aiohttp.ClientError as error:
                raise ProviderError("Inworld AI could not be reached (%s)" % type(error).__name__, retry=True)
        items = [v for v in found if isinstance(v, dict) and v.get("voiceId")]
        is_custom = lambda v: bool(v.get("isCustom") or v.get("owned") or v.get("source") in ("IVC", "PVC", "TVD"))
        items.sort(key=lambda v: 0 if is_custom(v) else 1)
        return [{"id": v["voiceId"],
                 "name": "%s (yours)" % (v.get("displayName") or v["voiceId"]) if is_custom(v)
                 else (v.get("displayName") or v["voiceId"])} for v in items]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice_inworld(key, name, sample, language="en", base_url=None):
    """Make an instant cloned voice on Inworld AI; returns its voiceId."""
    body = {
        "displayName": (name or "Voice")[:100],
        "voiceSamples": [{"audioData": base64.b64encode(sample).decode("ascii")}],
        "audioProcessingConfig": {"removeBackgroundNoise": False},
    }
    code = _inworld_lang(language)
    if code:
        body["languageCode"] = code
    headers = {"Authorization": _inworld_auth(key), "Content-Type": "application/json"}
    url = (base_url or InworldTTS.base_url).rstrip("/") + "/voices/v1/voices:clone"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.post(url, json=body, headers=headers) as r:
                if r.status not in (200, 201):
                    raise _status_error("Inworld AI", r.status, await r.text())
                data = await r.json(content_type=None)
        except aiohttp.ClientError as error:
            raise ProviderError("Inworld AI could not be reached (%s)" % type(error).__name__, retry=True)
    voice = (data or {}).get("voice") if isinstance(data, dict) else None
    voice_id = (voice.get("voiceId") if isinstance(voice, dict) else None) or (
        data.get("voiceId") if isinstance(data, dict) else None)
    if not voice_id:
        raise ProviderError("Inworld AI did not return a voice id")
    return voice_id


class MiniMaxTTS:
    """MiniMax Speech voices, including rapid clones, streamed as raw 24 kHz PCM."""

    rate = 24000
    base_url = "https://api.minimax.io/v1"

    def __init__(self, key, voice="", model="speech-2.8-turbo", require_voice=True):
        if not key:
            raise ProviderError("no MiniMax key is set")
        if require_voice and not voice:
            raise ProviderError("no MiniMax voice is chosen")
        self.key = key
        self.voice = voice or ""
        self.model = model or "speech-2.8-turbo"

    def _headers(self):
        return {"Authorization": _bearer(self.key), "Content-Type": "application/json"}

    async def stream(self, text):
        if not self.voice:
            raise ProviderError("no MiniMax voice is chosen")
        body = {
            "model": self.model,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "voice_setting": {
                "voice_id": self.voice,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": self.rate,
                "format": "pcm",
                "channel": 1,
            },
            "output_format": "hex",
        }
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/t2a_v2", json=body, headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("MiniMax", r.status, await r.text())
                    if "json" in (r.content_type or "").lower():
                        data = await r.json(content_type=None)
                        _minimax_check(data)
                        hex_audio = ((data or {}).get("data") or {}).get("audio") or ""
                        if hex_audio:
                            yield bytes.fromhex(hex_audio)
                        return
                    emitted = False
                    async for raw in iter_sse(r):
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        _minimax_check(event)
                        chunk_data = (event.get("data") or {}) if isinstance(event, dict) else {}
                        hex_audio = chunk_data.get("audio") or ""
                        status = chunk_data.get("status")
                        # Without exclude_aggregated_audio, MiniMax repeats all audio on status == 2.
                        if status == 2 and emitted:
                            continue
                        if hex_audio:
                            try:
                                pcm = bytes.fromhex(hex_audio)
                            except ValueError:
                                continue
                            if pcm:
                                emitted = True
                                yield pcm
            except aiohttp.ClientError as error:
                raise ProviderError("MiniMax could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/get_voice", json={"voice_type": "all"},
                                        headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("MiniMax", r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("MiniMax could not be reached (%s)" % type(error).__name__, retry=True)
        _minimax_check(data)
        out, seen = [], set()
        for group, tag in (("voice_cloning", " (cloned)"), ("voice_generation", " (designed)"), ("system_voice", "")):
            for v in (data or {}).get(group) or []:
                vid = v.get("voice_id") if isinstance(v, dict) else None
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                label = v.get("voice_name") or vid
                out.append({"id": vid, "name": label + tag})
        return out

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice_minimax(key, name, sample, filename="sample.wav", content_type="audio/wav", base_url=None):
    """Upload a voice sample and create a rapid cloned voice on MiniMax; returns its voice_id."""
    base = (base_url or MiniMaxTTS.base_url).rstrip("/")
    form = aiohttp.FormData()
    form.add_field("purpose", "voice_clone")
    form.add_field("file", sample, filename=filename, content_type=content_type)
    auth = {"Authorization": _bearer(key)}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        try:
            async with session.post(base + "/files/upload", data=form, headers=auth) as r:
                if r.status not in (200, 201):
                    raise _status_error("MiniMax", r.status, await r.text())
                uploaded = await r.json(content_type=None)
            _minimax_check(uploaded)
            file_id = ((uploaded or {}).get("file") or {}).get("file_id")
            if not file_id:
                raise ProviderError("MiniMax did not return a file id for the uploaded sample")
            voice_id = _minimax_voice_id(name)
            body = {
                "file_id": file_id,
                "voice_id": voice_id,
                "need_noise_reduction": True,
                "need_volume_normalization": True,
            }
            async with session.post(base + "/voice_clone", json=body,
                                    headers={**auth, "Content-Type": "application/json"}) as r:
                if r.status not in (200, 201):
                    raise _status_error("MiniMax", r.status, await r.text())
                cloned = await r.json(content_type=None)
            _minimax_check(cloned)
            await _minimax_first_use(session, base, auth, voice_id)
        except aiohttp.ClientError as error:
            raise ProviderError("MiniMax could not be reached (%s)" % type(error).__name__, retry=True)
    return voice_id


async def _minimax_first_use(session, base, auth, voice_id):
    """MiniMax keeps a cloned voice only once it has spoken (it deletes one unused for 7 days), so the new
    voice says one word now. A failure here is not the clone's: the character's first words use it too."""
    body = {"model": "speech-2.8-turbo", "text": "Hello.", "stream": False,
            "voice_setting": {"voice_id": voice_id},
            "audio_setting": {"sample_rate": 24000, "format": "pcm", "channel": 1}}
    try:
        async with session.post(base + "/t2a_v2", json=body, headers={**auth, "Content-Type": "application/json"}) as r:
            if r.status == 200:
                _minimax_check(await r.json(content_type=None))
    except (ProviderError, aiohttp.ClientError, ValueError):
        pass


def _token(key):
    key = (key or "").strip()
    return key if key.lower().startswith(("token ", "bearer ")) else "Token " + key


def _deepgram_voices():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "deepgram_voices.json"),
                  encoding="utf-8") as f:
            return json.load(f).get("voices", [])
    except (OSError, ValueError):
        return []


class DeepgramTTS:
    """Deepgram's voices, streamed as raw 24 kHz PCM: Flux TTS (its newest, /v2/speak) and Aura-2 (/v1/speak).
    The voice is the model's name, such as flux-hannah-en or aura-2-thalia-en. No cloning."""

    rate = 24000
    base_url = "https://api.deepgram.com"
    default_voice = "flux-hannah-en"

    def __init__(self, key, voice="", model="", require_voice=True):
        if not key:
            raise ProviderError("no Deepgram key is set")
        self.key = key
        self.voice = (voice or model or self.default_voice).strip()

    async def stream(self, text):
        version = "v2" if self.voice.startswith("flux-") else "v1"
        params = {"model": self.voice, "encoding": "linear16", "container": "none", "sample_rate": str(self.rate)}
        headers = {"Authorization": _token(self.key)}
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post("%s/%s/speak" % (self.base_url, version), params=params, json={"text": text},
                                        headers=headers) as r:
                    if r.status != 200:
                        raise _status_error("Deepgram", r.status, await r.text())
                    async for chunk in r.content.iter_chunked(4800):
                        yield chunk
            except aiohttp.ClientError as error:
                raise ProviderError("Deepgram could not be reached (%s)" % type(error).__name__, retry=True)

    async def voices(self):
        return [dict(v) for v in _deepgram_voices()]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


def _wav_pcm(data, rate):
    """The 16-bit mono samples inside a WAV file, which must be at `rate`."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != rate:
                raise ProviderError("the voice service sent %d-channel %d-bit audio at %d Hz, not 16-bit mono at %d Hz"
                                    % (w.getnchannels(), w.getsampwidth() * 8, w.getframerate(), rate))
            return w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        raise ProviderError("the voice service's audio could not be read")


class GoogleTTS:
    """Google Cloud Text-to-Speech voices (Chirp 3 HD and the others) as 24 kHz PCM. The key is a Google Cloud
    API key with the Text-to-Speech API enabled. No cloning (Google's custom voices need its approval)."""

    rate = 24000
    base_url = "https://texttospeech.googleapis.com/v1"
    default_voice = "en-US-Chirp3-HD-Aoede"

    def __init__(self, key, voice="", model="", require_voice=True):
        if not key:
            raise ProviderError("no Google Cloud key is set")
        self.key = key
        self.voice = (voice or self.default_voice).strip()

    def _headers(self):
        return {"X-Goog-Api-Key": self.key.strip()}

    async def stream(self, text):
        language = "-".join(self.voice.split("-")[:2]) or "en-US"
        body = {"input": {"text": text}, "voice": {"languageCode": language, "name": self.voice},
                "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": self.rate}}
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.post(self.base_url + "/text:synthesize", json=body, headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("Google Cloud", r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("Google Cloud could not be reached (%s)" % type(error).__name__, retry=True)
        try:
            audio = base64.b64decode((data or {}).get("audioContent") or "")
        except ValueError:
            raise ProviderError("Google Cloud's audio could not be read")
        # LINEAR16 comes as a WAV file (with its header); the device wants the samples alone.
        pcm = _wav_pcm(audio, self.rate) if audio[:4] == b"RIFF" else audio
        for start in range(0, len(pcm), 4800):
            yield pcm[start:start + 4800]

    async def voices(self):
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            try:
                async with session.get(self.base_url + "/voices", params={"languageCode": "en"},
                                       headers=self._headers()) as r:
                    if r.status != 200:
                        raise _status_error("Google Cloud", r.status, await r.text())
                    data = await r.json(content_type=None)
            except aiohttp.ClientError as error:
                raise ProviderError("Google Cloud could not be reached (%s)" % type(error).__name__, retry=True)
        out = []
        for v in (data or {}).get("voices", []):
            name = v.get("name") if isinstance(v, dict) else None
            if not name:
                continue
            parts = name.split("-")
            kind = " ".join(parts[2:-1]) if len(parts) > 3 else ""
            who = {"FEMALE": "woman", "MALE": "man"}.get(v.get("ssmlGender"), "voice")
            out.append({"id": name, "name": "%s (%s %s%s)" % (parts[-1], "-".join(parts[:2]), who,
                                                              ", " + kind if kind else ""),
                        "_rank": 0 if "Chirp3" in name else 1 if ("Chirp" in name or "Studio" in name) else 2})
        out.sort(key=lambda v: (v["_rank"], v["id"]))
        return [{"id": v["id"], "name": v["name"]} for v in out]

    async def test(self):
        size = 0
        async for chunk in self.stream("Testing."):
            size += len(chunk)
        return "spoke %.1f seconds of audio" % (size / 2 / self.rate)


async def clone_voice(provider, key, name, sample, language="en"):
    """Clone a voice on the chosen service from an audio recording; returns its voice id."""
    info = VOICE_CLONING_PROVIDERS.get(provider)
    if not info:
        raise ProviderError("choose a voice cloning service: %s" % ", ".join(
            v["label"] for v in VOICE_CLONING_PROVIDERS.values()))
    if not key:
        raise ProviderError("set a %s key first (under AI services)" % info["label"])
    ext, content_type = sniff_audio(sample)
    if ext not in info["formats"]:
        raise ProviderError("%s accepts %s recordings for voice cloning (got %s)" % (
            info["label"], ", ".join(f.upper() for f in info["formats"]), ext.upper()))
    if len(sample) > info["max_bytes"]:
        raise ProviderError("%s accepts recordings up to %d MB" % (info["label"], info["max_bytes"] // (1024 * 1024)))
    filename = "sample." + ext
    if provider == "elevenlabs":
        return await clone_voice_elevenlabs(key, name, sample, filename=filename, content_type=content_type)
    if provider == "cartesia":
        return await clone_voice_cartesia(key, name, sample, language=language, filename=filename, content_type=content_type)
    if provider == "fishaudio":
        return await clone_voice_fishaudio(key, name, sample, filename=filename, content_type=content_type)
    if provider == "inworld":
        return await clone_voice_inworld(key, name, sample, language=language)
    if provider == "minimax":
        return await clone_voice_minimax(key, name, sample, filename=filename, content_type=content_type)
    raise ProviderError("unsupported voice cloning service: %s" % provider)


def build_llm(settings, store):
    """The language model the settings describe."""
    llm = settings["llm"]
    provider = llm.get("provider", "")
    if provider == "anthropic":
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(store.secret("anthropic"), llm.get("model", ""), llm.get("max_tokens", 400),
                            llm.get("effort", "low"),
                            web_search=bool(settings.get("conversation", {}).get("web_search", True)))
    if provider == "selfhosted":
        kind = llm.get("server") or "ollama"
        if kind not in LOCAL_SERVERS:
            raise ProviderError("unknown kind of server: %s" % kind)
        base = local_address(llm.get("base_url", ""), kind)
        context = int(llm.get("context") or LOCAL_CONTEXT)
        if kind == "ollama":
            return OllamaLLM(base, llm.get("model", ""), llm.get("temperature", 0.8), llm.get("max_tokens", 400), context)
        adapter = OpenAICompatibleLLM(base, store.secret("local_llm"), llm.get("model", ""), llm.get("temperature", 0.8),
                                      llm.get("max_tokens", 400), service="The model on your computer")
        adapter.patience = LOCAL_PATIENCE
        adapter.context = context
        return adapter
    if provider in OPENAI_COMPATIBLE:
        known = OPENAI_COMPATIBLE[provider]
        adapter = OpenAICompatibleLLM(llm.get("base_url") or known["base_url"], store.secret(known["secret"]),
                                      llm.get("model", ""), llm.get("temperature", 0.8), llm.get("max_tokens", 400))
        if llm.get("context"):
            adapter.context = int(llm["context"])
        return adapter
    raise ProviderError("no language model is chosen yet")


def build_stt(settings, store, model_dir):
    stt = settings["stt"]
    provider = stt.get("provider", "local")
    if provider == "local":
        return LocalSTT(model_dir)
    if provider in OPENAI_COMPATIBLE:
        known = OPENAI_COMPATIBLE[provider]
        return OpenAICompatibleSTT(stt.get("base_url") or known["base_url"], store.secret(known["secret"]),
                                   stt.get("model", ""), stt.get("language", "en"))
    raise ProviderError("no speech recognition is chosen yet")


def build_tts(settings, store, voice=None, model_dir=None, provider=None, require_voice=True):
    tts = settings["tts"]
    default_provider = tts.get("provider", "")
    active = provider or default_provider
    same_provider = active == default_provider
    chosen = voice if voice is not None else (tts.get("voice", "") if same_provider else "")
    model = tts.get("model", "") if same_provider else ""
    if active == "local":
        return LocalTTS(model_dir or "", chosen, tts.get("speed", 1.0))
    if active == "elevenlabs":
        return ElevenLabsTTS(store.secret("elevenlabs"), chosen, model, require_voice=require_voice)
    if active == "cartesia":
        return CartesiaTTS(store.secret("cartesia"), chosen, model, require_voice=require_voice)
    if active == "fishaudio":
        return FishAudioTTS(store.secret("fishaudio"), chosen, model, require_voice=require_voice)
    if active == "inworld":
        return InworldTTS(store.secret("inworld"), chosen, model, require_voice=require_voice)
    if active == "minimax":
        return MiniMaxTTS(store.secret("minimax"), chosen, model, require_voice=require_voice)
    if active == "deepgram":
        return DeepgramTTS(store.secret("deepgram"), chosen)
    if active == "google":
        return GoogleTTS(store.secret("google_tts"), chosen)
    if active in OPENAI_COMPATIBLE:
        known = OPENAI_COMPATIBLE[active]
        base = (tts.get("base_url") if same_provider else "") or known["base_url"]
        if not model and active == "openai" and not same_provider:
            model = "gpt-4o-mini-tts"
        return OpenAICompatibleTTS(base, store.secret(known["secret"]), model, chosen)
    raise ProviderError("no voice service is chosen yet")
