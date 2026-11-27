"""Tests for multi-provider custom voice cloning, streaming TTS, per-character voice selection, and console APIs."""
import base64
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from companion import backup, package, providers  # noqa: E402
from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.providers import (  # noqa: E402
    CartesiaTTS,
    ElevenLabsTTS,
    FishAudioTTS,
    InworldTTS,
    MiniMaxTTS,
    OpenAICompatibleTTS,
    ProviderError,
)
from companion.store import Store, redact  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeLLM, FakeStage  # noqa: E402
from test_package import good_files, manifest  # noqa: E402

H = {"X-Companion": "1"}

SAMPLE_WAV = providers.pcm_to_wav(b"\x01\x00\x02\x00" * 400, 24000)
SAMPLE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
SAMPLE_MP3_SYNC = b"\xff\xfb\x90\x00" + b"\x00" * 64
SAMPLE_FLAC = b"fLaC" + b"\x00" * 64
SAMPLE_OGG = b"OggS" + b"\x00" * 64
SAMPLE_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
SAMPLE_M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64


async def start_mock(routes):
    app = web.Application(client_max_size=30 * 1024 * 1024)
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    server = TestServer(app)
    await server.start_server()
    return server, str(server.make_url("")).rstrip("/")


class AudioSniffAndSecurityTest(unittest.TestCase):
    def test_sniff_audio_identifies_supported_formats_and_rejects_junk(self):
        self.assertEqual(providers.sniff_audio(SAMPLE_WAV), ("wav", "audio/wav"))
        self.assertEqual(providers.sniff_audio(SAMPLE_MP3), ("mp3", "audio/mpeg"))
        self.assertEqual(providers.sniff_audio(SAMPLE_MP3_SYNC), ("mp3", "audio/mpeg"))
        self.assertEqual(providers.sniff_audio(SAMPLE_FLAC), ("flac", "audio/flac"))
        self.assertEqual(providers.sniff_audio(SAMPLE_OGG), ("ogg", "audio/ogg"))
        self.assertEqual(providers.sniff_audio(SAMPLE_WEBM), ("webm", "audio/webm"))
        self.assertEqual(providers.sniff_audio(SAMPLE_M4A), ("m4a", "audio/mp4"))
        for bad in (b"", b"abc", b"RIFF\x00\x00\x00\x00WEBP1234", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, b"hello world"):
            with self.assertRaises(ProviderError):
                providers.sniff_audio(bad)

    def test_new_voice_keys_are_stored_redacted_and_detected_in_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            samples = {
                "cartesia": "sk_car_TEST1234567890abcdef",
                "fishaudio": "fish-secret-token-9876543210",
                "inworld": "aW53b3JsZC1iYXNpYy1rZXktc2VjcmV0",
                "minimax": "minimax-jwt-secret-abcdef123456",
            }
            for name, val in samples.items():
                store.set_secret(name, val)
            status = store.secret_status()
            for name, val in samples.items():
                self.assertTrue(status[name])
                self.assertEqual(store.secret(name), val)
                self.assertNotIn(val, redact("failed with " + val, store))
                self.assertIn("[%s key]" % name, redact("failed with " + val, store))
        self.assertTrue(package.contains_secret("my key is sk_car_1234567890abcdef1234"))
        self.assertTrue(package.contains_secret("sk_" + "a1b2c3d4" * 4))
        self.assertFalse(package.contains_secret("just normal dialogue about skating"))


class VoiceCloningProvidersTest(unittest.IsolatedAsyncioTestCase):
    async def test_elevenlabs_stream_voices_and_clone(self):
        seen = {}

        async def tts_stream(request):
            seen["tts_headers"] = dict(request.headers)
            seen["tts_query"] = dict(request.query)
            seen["tts_body"] = await request.json()
            seen["tts_voice"] = request.match_info["vid"]
            return web.Response(body=b"\x01\x02" * 600, content_type="audio/pcm")

        async def list_voices(request):
            seen["list_key"] = request.headers.get("xi-api-key")
            return web.json_response({"voices": [
                {"voice_id": "pre-1", "name": "Rachel", "category": "premade"},
                {"voice_id": "cln-1", "name": "Aria", "category": "cloned"},
            ]})

        async def add_voice(request):
            reader = await request.multipart()
            parts = {}
            async for part in reader:
                parts[part.name] = (part.filename, part.headers.get("Content-Type"), await part.read())
            seen["clone_parts"] = parts
            seen["clone_key"] = request.headers.get("xi-api-key")
            return web.json_response({"voice_id": "el_cloned_42"})

        server, base = await start_mock([
            ("POST", "/text-to-speech/{vid}/stream", tts_stream),
            ("GET", "/voices", list_voices),
            ("POST", "/voices/add", add_voice),
        ])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(ElevenLabsTTS, "base_url", base):
            # Voices can be listed before choosing a voice id.
            tts_list = ElevenLabsTTS("el-key", "", require_voice=False)
            listed = await tts_list.voices()
            self.assertEqual(seen["list_key"], "el-key")
            self.assertEqual(listed[0], {"id": "cln-1", "name": "Aria (cloned)"})
            self.assertEqual(listed[1], {"id": "pre-1", "name": "Rachel"})

            # Cloning supports MP3 as well as WAV.
            vid = await providers.clone_voice("elevenlabs", "el-key", "Aria", SAMPLE_MP3)
            self.assertEqual(vid, "el_cloned_42")
            self.assertEqual(seen["clone_key"], "el-key")
            self.assertEqual(seen["clone_parts"]["files"][0], "sample.mp3")
            self.assertEqual(seen["clone_parts"]["files"][1], "audio/mpeg")
            self.assertEqual(seen["clone_parts"]["name"][2], b"Aria")

            # Streaming PCM 24 kHz.
            tts = ElevenLabsTTS("el-key", vid, "eleven_flash_v2_5")
            pcm = b"".join([c async for c in tts.stream("Hello!")])
            self.assertEqual(len(pcm), 1200)
            self.assertEqual(seen["tts_voice"], "el_cloned_42")
            self.assertEqual(seen["tts_query"]["output_format"], "pcm_24000")
            self.assertEqual(seen["tts_body"]["model_id"], "eleven_flash_v2_5")

    async def test_cartesia_stream_voices_clone_and_validation(self):
        seen = {}

        async def tts_bytes(request):
            seen["tts_headers"] = dict(request.headers)
            seen["tts_body"] = await request.json()
            return web.Response(body=b"\x10\x20" * 500, content_type="application/octet-stream")

        async def list_voices(request):
            seen["list_headers"] = dict(request.headers)
            return web.json_response({"data": [
                {"id": "pub-1", "name": "Narrator", "is_owner": False},
                {"id": "own-1", "name": "Mika", "is_owner": True},
            ]})

        async def clone(request):
            seen["clone_headers"] = dict(request.headers)
            reader = await request.multipart()
            parts = {}
            async for part in reader:
                parts[part.name] = (part.filename, await part.read())
            seen["clone_parts"] = parts
            return web.json_response({"id": "cartesia-voice-uuid-1"}, status=201)

        server, base = await start_mock([
            ("POST", "/tts/bytes", tts_bytes),
            ("GET", "/voices", list_voices),
            ("POST", "/voices/clone", clone),
        ])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(CartesiaTTS, "base_url", base):
            vid = await providers.clone_voice("cartesia", "sk_car_abc", "Mika", SAMPLE_OGG, language="ja-JP")
            self.assertEqual(vid, "cartesia-voice-uuid-1")
            self.assertEqual(seen["clone_headers"]["Authorization"], "Bearer sk_car_abc")
            self.assertEqual(seen["clone_headers"]["Cartesia-Version"], CartesiaTTS.api_version)
            self.assertEqual(seen["clone_parts"]["clip"][0], "sample.ogg")
            self.assertEqual(seen["clone_parts"]["language"][1], b"ja")
            self.assertEqual(seen["clone_parts"]["access"][1], b"private")

            # M4A is rejected before hitting the API.
            with self.assertRaises(ProviderError) as ctx:
                await providers.clone_voice("cartesia", "sk_car_abc", "Mika", SAMPLE_M4A)
            self.assertIn("Cartesia accepts", str(ctx.exception))

            tts = CartesiaTTS("sk_car_abc", vid)
            pcm = b"".join([c async for c in tts.stream("Hi there.")])
            self.assertEqual(len(pcm), 1000)
            self.assertEqual(seen["tts_body"]["model_id"], "sonic-3.6")
            self.assertEqual(seen["tts_body"]["voice"], vid)
            self.assertEqual(seen["tts_body"]["output_format"],
                             {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24000})

            listed = await CartesiaTTS("sk_car_abc", "", require_voice=False).voices()
            self.assertEqual(listed[0], {"id": "own-1", "name": "Mika (yours)"})
            self.assertEqual(listed[1], {"id": "pub-1", "name": "Narrator"})

    async def test_fishaudio_stream_voices_and_clone(self):
        seen = {}

        async def tts_stream(request):
            seen["tts_headers"] = dict(request.headers)
            seen["tts_body"] = await request.json()
            return web.Response(body=b"\x03\x04" * 400, content_type="audio/pcm")

        async def list_models(request):
            if request.query.get("self") == "true":
                return web.json_response({"items": [{"_id": "mine-1", "title": "My Aria"}]})
            return web.json_response({"items": [
                {"_id": "mine-1", "title": "My Aria"},
                {"_id": "pub-2", "title": "Popular Voice"},
            ]})

        async def create_model(request):
            seen["clone_auth"] = request.headers.get("Authorization")
            reader = await request.multipart()
            parts = {}
            async for part in reader:
                parts[part.name] = (part.filename, await part.read())
            seen["clone_parts"] = parts
            return web.json_response({"_id": "fish_model_99"}, status=201)

        server, base = await start_mock([
            ("POST", "/v1/tts", tts_stream),
            ("GET", "/model", list_models),
            ("POST", "/model", create_model),
        ])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(FishAudioTTS, "base_url", base):
            vid = await providers.clone_voice("fishaudio", "fish-key", "Aria", SAMPLE_WAV)
            self.assertEqual(vid, "fish_model_99")
            self.assertEqual(seen["clone_auth"], "Bearer fish-key")
            self.assertEqual(seen["clone_parts"]["type"][1], b"tts")
            self.assertEqual(seen["clone_parts"]["visibility"][1], b"private")
            self.assertEqual(seen["clone_parts"]["train_mode"][1], b"fast")
            self.assertEqual(seen["clone_parts"]["voices"][0], "sample.wav")

            tts = FishAudioTTS("fish-key", vid, "s2.1-pro-free")
            pcm = b"".join([c async for c in tts.stream("Testing Fish.")])
            self.assertEqual(len(pcm), 800)
            self.assertEqual(seen["tts_headers"]["model"], "s2.1-pro-free")
            self.assertEqual(seen["tts_body"]["reference_id"], vid)
            self.assertEqual(seen["tts_body"]["format"], "pcm")
            self.assertEqual(seen["tts_body"]["sample_rate"], 24000)

            listed = await FishAudioTTS("fish-key", "", require_voice=False).voices()
            self.assertEqual(listed, [
                {"id": "mine-1", "name": "My Aria (yours)"},
                {"id": "pub-2", "name": "Popular Voice"},
            ])

    async def test_inworld_large_ndjson_stream_fallback_voices_and_clone(self):
        seen = {}
        # A 70 KiB PCM chunk becomes >93 KiB in Base64, exceeding aiohttp's default 64 KiB readline limit.
        big_pcm = bytes(range(256)) * 280

        async def voice_stream(request):
            seen["stream_auth"] = request.headers.get("Authorization")
            seen["stream_body"] = await request.json()
            if seen["stream_body"]["text"] == "FAIL":
                line = json.dumps({"error": {"code": 14, "message": "temporarily unavailable"}}) + "\n"
                return web.Response(text=line, content_type="application/x-ndjson")
            lines = (
                json.dumps({"result": {"audioContent": base64.b64encode(big_pcm).decode("ascii")}}) + "\n"
                + json.dumps({"result": {"audioContent": base64.b64encode(b"\x07\x08\x09\x0a").decode("ascii")}})
            )
            return web.Response(text=lines, content_type="application/x-ndjson")

        async def voices_fallback(request):
            return web.json_response({"voices": [
                {"voiceId": "Ashley", "displayName": "Ashley", "isCustom": False},
                {"voiceId": "ws__mika", "displayName": "Mika", "source": "IVC"},
            ]})

        async def clone(request):
            seen["clone_auth"] = request.headers.get("Authorization")
            seen["clone_body"] = await request.json()
            return web.json_response({"voice": {"voiceId": "ws__aria_clone"}})

        server, base = await start_mock([
            ("POST", "/tts/v1/voice:stream", voice_stream),
            ("GET", "/tts/v1/voices", voices_fallback),
            ("POST", "/voices/v1/voices:clone", clone),
        ])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(InworldTTS, "base_url", base):
            vid = await providers.clone_voice("inworld", "rawBase64Creds==", "Aria", SAMPLE_MP3, language="ja")
            self.assertEqual(vid, "ws__aria_clone")
            self.assertEqual(seen["clone_auth"], "Basic rawBase64Creds==")
            self.assertEqual(seen["clone_body"]["languageCode"], "ja")
            self.assertNotIn("langCode", seen["clone_body"])
            self.assertFalse(seen["clone_body"]["audioProcessingConfig"]["removeBackgroundNoise"])
            self.assertEqual(base64.b64decode(seen["clone_body"]["voiceSamples"][0]["audioData"]), SAMPLE_MP3)
            # Inworld's API takes WAV and MP3 only.
            with self.assertRaises(ProviderError):
                await providers.clone_voice("inworld", "rawBase64Creds==", "Aria", SAMPLE_WEBM)
            # "auto" leaves the language for Inworld to detect.
            await providers.clone_voice("inworld", "rawBase64Creds==", "Aria", SAMPLE_WAV, language="auto")
            self.assertNotIn("languageCode", seen["clone_body"])

            # Already-prefixed "Basic ..." key should not be double-prefixed.
            tts = InworldTTS("Basic rawBase64Creds==", vid)
            pcm = b"".join([c async for c in tts.stream("Big chunk test.")])
            self.assertEqual(seen["stream_auth"], "Basic rawBase64Creds==")
            self.assertEqual(pcm, big_pcm + b"\x07\x08\x09\x0a")

            # In-stream error raises retryable ProviderError.
            with self.assertRaises(ProviderError) as ctx:
                _ = [c async for c in tts.stream("FAIL")]
            self.assertTrue(ctx.exception.retry)
            self.assertIn("temporarily unavailable", str(ctx.exception))

            # /voices/v1/voices 404 falls back to /tts/v1/voices and puts custom voices first.
            listed = await InworldTTS("rawBase64Creds==", "", require_voice=False).voices()
            self.assertEqual(listed[0], {"id": "ws__mika", "name": "Mika (yours)"})
            self.assertEqual(listed[1], {"id": "Ashley", "name": "Ashley"})

    async def test_minimax_sse_stream_business_errors_voices_and_two_step_clone(self):
        seen = {}
        chunk1 = b"\x11\x22" * 100
        chunk2 = b"\x33\x44" * 100

        async def upload(request):
            reader = await request.multipart()
            parts = {}
            async for part in reader:
                parts[part.name] = (part.filename, await part.read())
            seen["upload_parts"] = parts
            return web.json_response({"file": {"file_id": 123456789}, "base_resp": {"status_code": 0, "status_msg": "success"}})

        async def voice_clone(request):
            seen["clone_body"] = await request.json()
            return web.json_response({"base_resp": {"status_code": 0, "status_msg": "success"}})

        async def t2a(request):
            body = await request.json()
            seen["t2a_body"] = body
            seen.setdefault("spoken", []).append((body["text"], body["voice_setting"]["voice_id"], body.get("stream")))
            if body["text"] == "BAD_KEY":
                return web.json_response({"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}})
            sse = (
                "data: " + json.dumps({"data": {"audio": chunk1.hex(), "status": 1}, "base_resp": {"status_code": 0}}) + "\n\n"
                + "data: " + json.dumps({"data": {"audio": chunk2.hex(), "status": 1}, "base_resp": {"status_code": 0}}) + "\n\n"
                # Simulated aggregated status==2 repeat that must be skipped.
                + "data: " + json.dumps({"data": {"audio": (chunk1 + chunk2).hex(), "status": 2}, "base_resp": {"status_code": 0}}) + "\n\n"
            )
            return web.Response(text=sse, content_type="text/event-stream")

        async def get_voice(request):
            seen["get_voice_body"] = await request.json()
            return web.json_response({
                "base_resp": {"status_code": 0},
                "system_voice": [{"voice_id": "English_CalmWoman", "voice_name": "Calm Woman"}],
                "voice_cloning": [{"voice_id": "c_Aria_1234abcd", "voice_name": "Aria"}],
            })

        server, base = await start_mock([
            ("POST", "/files/upload", upload),
            ("POST", "/voice_clone", voice_clone),
            ("POST", "/t2a_v2", t2a),
            ("POST", "/get_voice", get_voice),
        ])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(MiniMaxTTS, "base_url", base):
            vid = await providers.clone_voice("minimax", "mm-key", "Aria!", SAMPLE_M4A)
            self.assertTrue(vid.startswith("c_Aria_"))
            self.assertGreaterEqual(len(vid), 8)
            self.assertEqual(seen["upload_parts"]["purpose"][1], b"voice_clone")
            self.assertEqual(seen["upload_parts"]["file"][0], "sample.m4a")
            self.assertEqual(seen["clone_body"]["file_id"], 123456789)
            self.assertEqual(seen["clone_body"]["voice_id"], vid)
            self.assertEqual(seen["spoken"], [("Hello.", vid, False)],
                             "a new clone speaks once, or MiniMax deletes it after 7 days")

            tts = MiniMaxTTS("mm-key", vid)
            pcm = b"".join([c async for c in tts.stream("Hello MiniMax!")])
            self.assertEqual(pcm, chunk1 + chunk2, "duplicate status=2 audio must not be repeated")
            self.assertEqual(seen["t2a_body"]["stream_options"], {"exclude_aggregated_audio": True})
            self.assertEqual(seen["t2a_body"]["audio_setting"]["sample_rate"], 24000)

            with self.assertRaises(ProviderError) as ctx:
                _ = [c async for c in tts.stream("BAD_KEY")]
            self.assertIn("refused the key", str(ctx.exception))

            listed = await MiniMaxTTS("mm-key", "", require_voice=False).voices()
            self.assertEqual(listed[0], {"id": "c_Aria_1234abcd", "name": "Aria (cloned)"})
            self.assertEqual(listed[1], {"id": "English_CalmWoman", "name": "Calm Woman"})


class DeviceAndConsoleVoiceCloningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.stage = FakeStage()
        self.device = Device(self.dir.name, speaker=Speaker(command=None, realtime=False), stage=self.stage)
        self.client = TestClient(TestServer(make_app(self.device)))
        await self.client.start_server()
        r = await self.client.post("/api/setup", json={"password": "a good password"}, headers=H)
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.dir.cleanup()

    def test_build_tts_per_character_override_does_not_leak_model_or_voice(self):
        self.device.store.update_settings({
            "tts": {"provider": "elevenlabs", "model": "eleven_multilingual_v2", "voice": "el-default"},
        })
        self.device.store.set_secret("elevenlabs", "el-key")
        self.device.store.set_secret("cartesia", "sk_car_key")
        self.device.store.set_secret("fishaudio", "fish-key")
        self.device.store.set_secret("openai", "sk-openai-key")

        # Default inherits ElevenLabs model & voice.
        t_el = self.device.build("tts")
        self.assertIsInstance(t_el, ElevenLabsTTS)
        self.assertEqual((t_el.model, t_el.voice), ("eleven_multilingual_v2", "el-default"))

        # Character override to Cartesia uses Cartesia's default model, not ElevenLabs's.
        t_car = self.device.build("tts", provider="cartesia", voice="car-voice-1")
        self.assertIsInstance(t_car, CartesiaTTS)
        self.assertEqual((t_car.model, t_car.voice), ("sonic-3.6", "car-voice-1"))

        # Character override to OpenAI gets gpt-4o-mini-tts default model.
        t_oai = self.device.build("tts", provider="openai", voice="coral")
        self.assertIsInstance(t_oai, OpenAICompatibleTTS)
        self.assertEqual((t_oai.model, t_oai.voice), ("gpt-4o-mini-tts", "coral"))

        # make_conversation picks up the active character's voice_provider and voice.
        self.device.characters.install(package.build(good_files(), manifest()))
        self.device.characters.set_device_settings("aria", {"voice": "fish-v1", "voice_provider": "fishaudio"})
        self.device.store.update_settings({"active_character": "aria", "llm": {"provider": "openai", "model": "gpt-4.1-mini"}})
        conv = self.device.make_conversation()
        self.assertIsInstance(conv.tts, FishAudioTTS)
        self.assertEqual(conv.tts.voice, "fish-v1")

    async def test_console_multi_provider_cloning_persona_and_voices_endpoints(self):
        files = good_files()
        files["voice/sample.wav"] = SAMPLE_WAV
        self.device.characters.install(package.build(files, manifest()))

        # Persona reports package sample and cloning providers with key status.
        r = await self.client.get("/api/characters/aria/persona")
        data = await r.json()
        self.assertTrue(data["has_voice_sample"])
        by_id = {p["id"]: p for p in data["cloning_providers"]}
        self.assertEqual(set(by_id), {"elevenlabs", "cartesia", "fishaudio", "inworld", "minimax"})
        self.assertFalse(by_id["cartesia"]["has_key"])

        # Setting an unknown voice_provider is rejected.
        r = await self.client.put("/api/characters/aria/persona", json={"voice_provider": "bogus"}, headers=H)
        self.assertEqual(r.status, 400)
        r = await self.client.put("/api/characters/aria/persona",
                                  json={"voice": "v-1", "voice_provider": "inworld"}, headers=H)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.device.characters.device_settings("aria")["voice_provider"], "inworld")

        # Cloning without key returns 400 naming the provider.
        r = await self.client.post("/api/characters/aria/voice?provider=cartesia", data=SAMPLE_MP3, headers=H)
        self.assertEqual(r.status, 400)
        self.assertIn("Cartesia", await r.text())

        # Save Cartesia & Fish Audio keys via console API.
        for name, val in (("cartesia", "sk_car_test_secret_1"), ("fishaudio", "fish_test_secret_2")):
            r = await self.client.put("/api/keys/" + name, json={"value": val}, headers=H)
            self.assertEqual(r.status, 200)
            self.assertTrue((await r.json())[name])

        # Mock Cartesia + Fish Audio servers and exercise cloning + /api/voices?provider=
        async def cartesia_clone(request):
            return web.json_response({"id": "car-cloned-9"})

        async def cartesia_voices(request):
            return web.json_response({"data": [{"id": "car-cloned-9", "name": "Aria", "is_owner": True}]})

        async def fish_clone(request):
            return web.json_response({"_id": "fish-sample-7"}, status=201)

        server, base = await start_mock([
            ("POST", "/voices/clone", cartesia_clone),
            ("GET", "/voices", cartesia_voices),
            ("POST", "/model", fish_clone),
        ])
        self.addAsyncCleanup(server.close)

        with mock.patch.object(CartesiaTTS, "base_url", base), mock.patch.object(FishAudioTTS, "base_url", base):
            # Clone uploaded MP3 with Cartesia.
            r = await self.client.post("/api/characters/aria/voice?provider=cartesia&language=fr",
                                       data=SAMPLE_MP3, headers=H)
            self.assertEqual(r.status, 200, await r.text())
            self.assertEqual(await r.json(), {"voice": "car-cloned-9", "provider": "cartesia"})
            saved = self.device.characters.device_settings("aria")
            self.assertEqual((saved["voice"], saved["voice_provider"]), ("car-cloned-9", "cartesia"))

            # List voices for Cartesia even though device default TTS is still local and voice is blank.
            r = await self.client.get("/api/voices?provider=cartesia")
            self.assertEqual(r.status, 200)
            listed = await r.json()
            self.assertTrue(listed["ok"])
            self.assertEqual(listed["voices"], [{"id": "car-cloned-9", "name": "Aria (yours)"}])

            # Clone directly from the character package's voice/sample.wav with Fish Audio.
            r = await self.client.post("/api/characters/aria/voice?provider=fishaudio&use_package_sample=1",
                                       data=b"", headers=H)
            self.assertEqual(r.status, 200, await r.text())
            self.assertEqual(await r.json(), {"voice": "fish-sample-7", "provider": "fishaudio"})

            # Bad audio format returns 400 (not 502).
            r = await self.client.post("/api/characters/aria/voice?provider=cartesia", data=b"not audio", headers=H)
            self.assertEqual(r.status, 400)
            r = await self.client.post("/api/characters/aria/voice?provider=cartesia", data=SAMPLE_M4A, headers=H)
            self.assertEqual(r.status, 400)

        # Backup never includes any of the new voice cloning keys.
        zip_path = os.path.join(self.dir.name, "backup.zip")
        backup.make(self.device, zip_path)
        with open(zip_path, "rb") as f:
            blob = f.read()
        self.assertNotIn(b"sk_car_test_secret_1", blob)
        self.assertNotIn(b"fish_test_secret_2", blob)

    async def test_console_cloning_defaults_missing_sample_and_upstream_errors(self):
        self.device.characters.install(package.build(good_files(), manifest()))
        r = await self.client.get("/api/characters/aria/persona")
        self.assertFalse((await r.json())["has_voice_sample"])

        # Missing package sample -> 400.
        self.device.store.set_secret("elevenlabs", "el-key")
        r = await self.client.post("/api/characters/aria/voice?use_package_sample=1", data=b"", headers=H)
        self.assertEqual(r.status, 400)
        self.assertIn("no voice sample", await r.text())

        # Omitting ?provider= uses active cloning provider (Cartesia) when set as device TTS,
        # and returns 502 if the upstream API refuses the request.
        self.device.store.set_secret("cartesia", "sk_car_key")
        self.device.store.update_settings({"tts": {"provider": "cartesia"}})

        async def fail_clone(request):
            return web.json_response({"error": {"message": "clip too short"}}, status=422)

        server, base = await start_mock([("POST", "/voices/clone", fail_clone)])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(CartesiaTTS, "base_url", base):
            r = await self.client.post("/api/characters/aria/voice", data=SAMPLE_WAV, headers=H)
            self.assertEqual(r.status, 502)
            self.assertIn("clip too short", await r.text())

        # Oversized recording for Inworld (>12 MB) is rejected cleanly before network I/O.
        with self.assertRaises(ProviderError) as ctx:
            await providers.clone_voice("inworld", "iw-key", "Aria", SAMPLE_WAV + b"\0" * (12 * 1024 * 1024))
        self.assertIn("12 MB", str(ctx.exception))



class MoreVoiceServicesTest(unittest.IsolatedAsyncioTestCase):
    """Deepgram and Google Cloud (voices without cloning), and the voice lists' completeness."""

    async def test_deepgram_flux_and_aura_voices_stream_raw_pcm(self):
        seen = []

        async def speak(request):
            seen.append((request.path, dict(request.query), request.headers.get("Authorization"), await request.json()))
            return web.Response(body=b"\x01\x02" * 300, content_type="application/octet-stream")

        server, base = await start_mock([("POST", "/v1/speak", speak), ("POST", "/v2/speak", speak)])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(providers.DeepgramTTS, "base_url", base):
            tts = providers.DeepgramTTS("dg-key", "")
            self.assertEqual(tts.voice, "flux-hannah-en", "a voice to start with")
            pcm = b"".join([c async for c in tts.stream("Hello there.")])
            self.assertEqual(len(pcm), 600)
            path, query, auth, body = seen[-1]
            self.assertEqual(path, "/v2/speak", "Flux voices are on /v2")
            self.assertEqual(query, {"model": "flux-hannah-en", "encoding": "linear16", "container": "none",
                                     "sample_rate": "24000"})
            self.assertEqual(auth, "Token dg-key")
            self.assertEqual(body, {"text": "Hello there."})
            tts = providers.DeepgramTTS("Token dg-key", "aura-2-thalia-en")
            _ = [c async for c in tts.stream("Hi.")]
            self.assertEqual(seen[-1][0], "/v1/speak", "Aura-2 voices are on /v1")
            self.assertEqual(seen[-1][2], "Token dg-key", "a key pasted with its Token prefix is not doubled")
        voices = await providers.DeepgramTTS("dg-key").voices()
        ids = [v["id"] for v in voices]
        self.assertGreater(len(voices), 60)
        self.assertIn("flux-hannah-en", ids)
        self.assertIn("aura-2-thalia-en", ids)
        self.assertEqual(len(ids), len(set(ids)))

    async def test_google_voice_wav_header_is_removed_and_voices_listed_best_first(self):
        seen = {}
        pcm = b"\x05\x06" * 2400

        async def synthesize(request):
            seen["key"] = request.headers.get("X-Goog-Api-Key")
            seen["body"] = await request.json()
            return web.json_response({"audioContent": base64.b64encode(providers.pcm_to_wav(pcm, 24000)).decode()})

        async def voices(request):
            seen["voices_query"] = dict(request.query)
            return web.json_response({"voices": [
                {"name": "en-US-Standard-A", "ssmlGender": "MALE", "languageCodes": ["en-US"]},
                {"name": "en-GB-Chirp3-HD-Aoede", "ssmlGender": "FEMALE", "languageCodes": ["en-GB"]},
                {"name": "en-AU-Neural2-B", "ssmlGender": "MALE", "languageCodes": ["en-AU"]},
            ]})

        async def wrong_rate(request):
            return web.json_response({"audioContent": base64.b64encode(providers.pcm_to_wav(pcm, 16000)).decode()})

        server, base = await start_mock([("POST", "/text:synthesize", synthesize), ("GET", "/voices", voices)])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(providers.GoogleTTS, "base_url", base):
            tts = providers.GoogleTTS("goog-key", "en-GB-Chirp3-HD-Aoede")
            got = b"".join([c async for c in tts.stream("Hello.")])
            self.assertEqual(got, pcm, "the samples alone, without the WAV header")
            self.assertEqual(seen["key"], "goog-key")
            self.assertEqual(seen["body"]["voice"], {"languageCode": "en-GB", "name": "en-GB-Chirp3-HD-Aoede"})
            self.assertEqual(seen["body"]["audioConfig"], {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000})
            listed = await tts.voices()
            self.assertEqual(seen["voices_query"], {"languageCode": "en"})
            self.assertEqual([v["id"] for v in listed], ["en-GB-Chirp3-HD-Aoede", "en-AU-Neural2-B", "en-US-Standard-A"])
            self.assertEqual(listed[0]["name"], "Aoede (en-GB woman, Chirp3 HD)")
        server2, base2 = await start_mock([("POST", "/text:synthesize", wrong_rate)])
        self.addAsyncCleanup(server2.close)
        with mock.patch.object(providers.GoogleTTS, "base_url", base2):
            with self.assertRaises(ProviderError):
                _ = [c async for c in providers.GoogleTTS("goog-key").stream("Hello.")]

    async def test_cartesia_lists_the_owners_clones_even_beyond_the_librarys_first_page(self):
        queries = []

        async def list_voices(request):
            queries.append(dict(request.query))
            if request.query.get("is_owner") == "true":
                return web.json_response({"data": [{"id": "own-9", "name": "Aria", "is_owner": True}]})
            return web.json_response({"data": [{"id": "pub-%d" % i, "name": "Voice %d" % i, "is_owner": False}
                                               for i in range(100)]})

        server, base = await start_mock([("GET", "/voices", list_voices)])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(CartesiaTTS, "base_url", base):
            listed = await CartesiaTTS("sk_car_abc", "", require_voice=False).voices()
        self.assertEqual(listed[0], {"id": "own-9", "name": "Aria (yours)"})
        self.assertEqual(len(listed), 101)
        self.assertIn({"is_owner": "true", "limit": "100"}, queries)

    async def test_inworld_reads_every_page_of_voices(self):
        queries = []

        async def list_voices(request):
            queries.append(dict(request.query))
            if request.query.get("pageToken") == "p2":
                return web.json_response({"voices": [{"voiceId": "ws__mine", "displayName": "Mine", "source": "IVC"}]})
            return web.json_response({"voices": [{"voiceId": "Ashley", "displayName": "Ashley", "source": "SYSTEM"}],
                                      "nextPageToken": "p2"})

        server, base = await start_mock([("GET", "/voices/v1/voices", list_voices)])
        self.addAsyncCleanup(server.close)
        with mock.patch.object(InworldTTS, "base_url", base):
            listed = await InworldTTS("creds", "", require_voice=False).voices()
        self.assertEqual(listed, [{"id": "ws__mine", "name": "Mine (yours)"}, {"id": "Ashley", "name": "Ashley"}])
        self.assertEqual(queries, [{"pageSize": "500"}, {"pageSize": "500", "pageToken": "p2"}])

    def test_every_voice_service_is_built_from_the_settings_with_its_own_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            for name in ("deepgram", "google_tts"):
                store.set_secret(name, name + "-key-123456")
            settings = store.settings()
            settings["tts"] = {"provider": "deepgram", "voice": "aura-2-thalia-en", "model": "", "base_url": "", "speed": 1}
            tts = providers.build_tts(settings, store)
            self.assertIsInstance(tts, providers.DeepgramTTS)
            self.assertEqual((tts.key, tts.voice), ("deepgram-key-123456", "aura-2-thalia-en"))
            tts = providers.build_tts(settings, store, provider="google")
            self.assertIsInstance(tts, providers.GoogleTTS)
            self.assertEqual((tts.key, tts.voice), ("google_tts-key-123456", "en-US-Chirp3-HD-Aoede"))
            self.assertIn("deepgram", providers.TTS_PROVIDERS)
            self.assertIn("google", providers.TTS_PROVIDERS)
            self.assertNotIn("deepgram-key-123456", redact("key deepgram-key-123456 used", store))

if __name__ == "__main__":
    unittest.main()
