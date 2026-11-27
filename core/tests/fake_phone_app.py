"""A stand-in for a face-tracking app on a phone, speaking VTube Studio's protocol (which MeowFace speaks
too), for trying "Copy me" without a phone (core/companion/phone_apps.py):

    python3 tests/fake_phone_app.py [HOST]

It listens on HOST (all addresses unless given), UDP port 21412. Once a device asks, it streams 30 frames a
second to the port the device named, for as long as the request said: the head turning from side to
side and nodding, a smile now and then. Type HOST as the phone's address on the settings page.
"""
import asyncio
import json
import math
import sys

PORT = 21412
SHAPES = ("EyeBlinkLeft", "EyeBlinkRight", "JawOpen", "MouthSmileLeft", "MouthSmileRight", "BrowInnerUp")


def packet(t):
    """The frame `t` seconds after it was first asked: still for a second and a half, then moving."""
    moving = max(0.0, t - 1.5)
    smile = 0.7 if int(moving / 3) % 2 else 0.0
    blink = 1.0 if (t % 4.0) < 0.12 else 0.0
    values = {"EyeBlinkLeft": blink, "EyeBlinkRight": blink, "JawOpen": 0.0, "MouthSmileLeft": smile,
              "MouthSmileRight": smile, "BrowInnerUp": 0.0}
    return {"Timestamp": int(t * 1000), "Hotkey": -1, "FaceFound": True,
            "Rotation": {"x": 8.0 * math.sin(1.3 * moving), "y": 25.0 * math.sin(0.9 * moving), "z": 0.0},
            "Position": {"x": 0.0, "y": 0.0, "z": 0.0}, "EyeLeft": {"x": 0.0, "y": 0.0, "z": 0.0},
            "EyeRight": {"x": 0.0, "y": 0.0, "z": 0.0},
            "BlendShapes": [{"k": k, "v": values[k]} for k in SHAPES]}


class Phone(asyncio.DatagramProtocol):
    def __init__(self):
        self.to, self.until, self.started, self.task = None, 0.0, None, None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            request = json.loads(data)
            assert request["messageType"] == "iOSTrackingDataRequest"
            port, seconds = int(request["ports"][0]), float(request["time"])
        except (ValueError, KeyError, IndexError, TypeError, AssertionError):
            return
        loop = asyncio.get_running_loop()
        if self.to != (addr[0], port):
            print("asked by %s for port %d" % (addr[0], port), flush=True)
            self.started = loop.time()
        self.to, self.until = (addr[0], port), loop.time() + min(10.0, max(0.5, seconds))
        if self.task is None:
            self.task = asyncio.ensure_future(self.stream())

    async def stream(self):
        loop = asyncio.get_running_loop()
        while True:
            if loop.time() < self.until:
                self.transport.sendto(json.dumps(packet(loop.time() - self.started)).encode(), self.to)
            await asyncio.sleep(1 / 30.0)


async def main(host):
    await asyncio.get_running_loop().create_datagram_endpoint(Phone, local_addr=(host, PORT))
    print("ready", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"))
