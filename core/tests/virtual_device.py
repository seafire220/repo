"""Run the core with stand-in AI providers and no sound, for trying the console without keys or hardware.

    python tests/virtual_device.py DATA_DIR PORT [HOST [TLS_PORT]]

HOST is 127.0.0.1 unless given: give the computer's network address to reach it from another device, or
to see pages as a phone on a home network does (plain http, not localhost: not a secure context).
TLS_PORT is where the live camera's own HTTPS listens when it is turned on (8443 unless given).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from aiohttp import web  # noqa: E402

from companion.audio import Speaker  # noqa: E402
from companion.device import Device  # noqa: E402
from companion.web import make_app  # noqa: E402
from fakes import FakeLLM, FakeStage, FakeTTS  # noqa: E402


def providers(which, device):
    if which == "llm":
        return FakeLLM("[happy] Hello! I'm a stand-in reply, so the console can be tried without keys. [timer 2m test]")
    if which == "tts":
        return FakeTTS()
    if which == "search":
        return None
    raise RuntimeError("no stand-in for " + which)


async def main(data, port, host="127.0.0.1", tls_port="8443"):
    device = Device(data, speaker=Speaker(command=None), stage=FakeStage(), provider_factory=providers)
    device.tls_port, device.web_host = int(tls_port), host
    device.start()
    runner = web.AppRunner(make_app(device))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print("ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), *sys.argv[3:5]))
