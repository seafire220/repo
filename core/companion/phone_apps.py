"""Face tracking from an app on the owner's phone, for "Copy me" (live_motion.py).

Two protocols, both over the home network's UDP, both made for apps on a computer to receive a phone's
face tracking (the phone does the tracking; the device only listens):

- VTube Studio's, which MeowFace (Android) also speaks. The device asks the phone for data by sending
  {"messageType": "iOSTrackingDataRequest", "time": seconds, "sentBy": name, "ports": [port]} to the
  phone's UDP port 21412, again before `time` runs out; the phone sends JSON to that port: whether a face
  is found, the head's rotation (degrees), and the 52 ARKit blendshapes as [{"k": name, "v": 0..1}].
  (VTube Studio: "3rd Party PC Clients" on, in the first settings tab; it shows the phone's address.)
  Its developer's description and example: github.com/DenchiSoft/VTubeStudioBlendshapeUDPReceiverTest.
- iFacialMocap's (iPhone). The device sends a fixed string to the phone's UDP port 49983; the phone
  sends text to the device's port 49983, 60 times a second: "name&value|...|=head#x,y,z,px,py,pz|..."
  with the blendshapes 0 to 100 and the head's Euler angles in degrees. Its developer's specification:
  ifacialmocap.com/for-developer.

Each frame becomes the stage's controls: the face from the blendshapes (mocap.face_controls, the same
as a video's), the head's turn, nod and tilt from its angles. Which way an app's angles go is not
documented; these follow Apple's ARKit (on which both apps are built), and the settings page can
mirror them and flip up and down.
"""
import asyncio
import ipaddress
import json
import math
import re
import socket

from . import mocap

VTS_PORT = 21412
IFM_PORT = 49983
IFM_HELLO = b"iFacialMocap_sahuasouryya9218sauhuiayeta91555dy3719|sendDataVersion=v2"
APP_NAME = "Character device"
ASK_EVERY = 0.5             # seconds between VTube Studio requests
ASK_FOR = 2.0               # seconds each request asks for
HELLO_EVERY = 3.0           # seconds between iFacialMocap start strings (again, in case the app restarted)
MAX_PACKET = 16 * 1024
APPS = {"vts": "VTube Studio or MeowFace", "ifm": "iFacialMocap"}


def phone_address(text):
    """The phone's address, checked: an IP address on the home network (not the internet)."""
    text = str(text or "").strip()
    try:
        address = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        raise ValueError("type the phone's address as the app shows it, such as 192.168.1.40")
    if not (address.is_private or address.is_link_local or address.is_loopback) or address.is_multicast:
        raise ValueError("that is not an address on your home network: the app shows the right one")
    return str(address)


def _angle(value):
    """Degrees, between -180 and 180 (an app may send 350 for -10)."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("not a number")
    return (value + 180.0) % 360.0 - 180.0


def _score(value, scale=1.0):
    value = float(value) / scale
    if not math.isfinite(value):
        raise ValueError("not a number")
    return max(0.0, min(1.0, value))


def parse_vts(data):
    """A VTube Studio (or MeowFace) packet: {"face", "blend", "pitch", "yaw", "roll"}, or None."""
    try:
        message = json.loads(data[:MAX_PACKET].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    if not message.get("FaceFound"):
        return {"face": False}
    try:
        rotation = message.get("Rotation") or {}
        blend = {}
        for item in message.get("BlendShapes") or []:
            name = str(item.get("k", ""))
            if name:
                blend[name[0].lower() + name[1:]] = _score(item.get("v", 0))
        return {"face": True, "blend": blend, "pitch": _angle(rotation.get("x", 0)),
                "yaw": _angle(rotation.get("y", 0)), "roll": _angle(rotation.get("z", 0))}
    except (TypeError, ValueError, AttributeError):
        return None


_SIDE = re.compile(r"_([LR])$")


def parse_ifm(data):
    """An iFacialMocap packet: {"face", "blend", "pitch", "yaw", "roll"}, or None."""
    try:
        text = data[:MAX_PACKET].decode("ascii")
    except UnicodeDecodeError:
        return None
    blend, head = {}, None
    for item in text.split("|"):
        item = item.strip()
        if not item:
            continue
        if "#" in item:
            name, _, numbers = item.partition("#")
            if name.lstrip("=") == "head":
                try:
                    head = [float(n) for n in numbers.split(",")[:3]]
                except ValueError:
                    return None
            continue
        if "&" in item:
            name, _, value = item.rpartition("&")
        else:
            name, _, value = item.rpartition("-")
        name = re.sub(r"[^A-Za-z_]", "", name)
        if not name:
            continue
        name = _SIDE.sub(lambda m: "Left" if m.group(1) == "L" else "Right", name)
        try:
            blend[name] = _score(value, 100.0)
        except ValueError:
            continue
    if head is None or len(head) < 3:
        return None
    try:
        pitch, yaw, roll = (_angle(v) for v in head[:3])
    except ValueError:
        return None
    return {"face": bool(blend), "blend": blend, "pitch": pitch, "yaw": yaw, "roll": roll}


def app_controls(frame):
    """An app's frame as the stage's controls (not yet measured from rest): the face from its
    blendshapes, the head from its angles, in the same units a video gives (mocap.head_controls: a turn
    of 30 degrees is about 1, a tilt of 20 degrees is 1)."""
    if not frame or not frame.get("face"):
        return {}
    controls = mocap.face_controls(frame["blend"]) if frame.get("blend") else {}
    # ARKit: a positive yaw turns the face to the person's own left, a positive pitch looks down, a positive
    # roll tilts it to their right shoulder. The controls are as a video shows it (mocap.py).
    controls["head_yaw"] = max(-1.2, min(1.2, frame["yaw"] / 30.0))
    controls["head_pitch"] = max(-1.2, min(1.2, -frame["pitch"] / 30.0))
    controls["head_roll"] = max(-1.2, min(1.2, -frame["roll"] / 20.0))
    return controls


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self, link):
        self.link = link

    def datagram_received(self, data, addr):
        self.link._received(data, addr)

    def error_received(self, exc):
        self.link.error = str(exc)


class AppLink:
    """Face tracking from an app on the phone at `address`, handed to `on_frame(controls)`."""

    def __init__(self, app, address, on_frame, local_port=None, phone_port=None):
        if app not in APPS:
            raise ValueError("the app is VTube Studio (or MeowFace) or iFacialMocap")
        self.app = app
        self.address = phone_address(address)
        self.on_frame = on_frame
        self.local_port = local_port      # tests choose; VTube Studio's is any free port; iFacialMocap's is fixed
        self.phone_port = phone_port or (IFM_PORT if app == "ifm" else VTS_PORT)
        self.transport = None
        self.task = None
        self.frames = 0
        self.error = ""

    async def start(self):
        loop = asyncio.get_running_loop()
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        port = self.local_port if self.local_port is not None else (IFM_PORT if self.app == "ifm" else 0)
        try:
            self.transport, _ = await loop.create_datagram_endpoint(
                lambda: _Receiver(self), local_addr=("::" if family == socket.AF_INET6 else "0.0.0.0", port),
                family=family)
        except OSError as error:
            raise OSError("the device could not listen for the app (%s)" % (error.strerror or error))
        self.task = asyncio.ensure_future(self._ask())
        return self

    @property
    def port(self):
        return self.transport.get_extra_info("sockname")[1] if self.transport else None

    def _send(self, data, port):
        try:
            self.transport.sendto(data, (self.address, port))
        except OSError as error:
            self.error = str(error)

    async def _ask(self):
        """Keep asking the phone for its tracking (the apps stop sending when nobody asks)."""
        while self.transport is not None:
            if self.app == "vts":
                request = {"messageType": "iOSTrackingDataRequest", "time": ASK_FOR, "sentBy": APP_NAME,
                           "ports": [self.port]}
                self._send(json.dumps(request).encode(), self.phone_port)
                await asyncio.sleep(ASK_EVERY)
            else:
                self._send(IFM_HELLO, self.phone_port)
                await asyncio.sleep(HELLO_EVERY)

    def _received(self, data, addr):
        if addr[0].split("%")[0] != self.address:        # only the phone the owner named
            return
        frame = parse_vts(data) if self.app == "vts" else parse_ifm(data)
        if frame is None:
            return
        self.frames += 1
        self.on_frame(app_controls(frame))

    async def stop(self):
        if self.task:
            self.task.cancel()
        if self.transport:
            self.transport.close()
        self.transport = None
