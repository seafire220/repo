"""Gestures recorded with a phone's camera (docs/26-animation-director.md, "Gestures from a phone").

The owner asks for a recording on the settings page (Animation tab), which shows a QR code and a short link
with a one-time code. The phone opens it on the home network, with no password: the code stands for one, for
one recording, for 30 minutes. The phone films the gesture with its own camera app (a page on the home
network may not use the camera directly, but it may ask for a video), and its browser finds the face and
body in the video with MediaPipe (mocap_assets.py), so the video stays on the phone: only the landmarks
come to the device. The device turns them into a clip, as Character Studio does (mocap.py), and plays it on
the screen at once; the phone names it and saves it with the other gestures.
"""
import collections
import math
import re
import secrets
import time

from . import mocap, motions

CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0 and O, 1, I and L to mix up when typed
CODE_LENGTH = 6
LIFETIME = 30 * 60
FPS_RANGE = (2.0, 30.0)
MAX_SECONDS = 20.0
MAX_FRAMES = 601
MAX_BODY = 4 * 1024 * 1024
WRONG_CODES_A_MINUTE = 30
FOUND_AT_LEAST = 0.5          # the share of frames a person must be found in
# What the phone sends for each frame, in this order (it is told, so the two cannot drift apart): MediaPipe's
# face points, its face blendshapes by name, and its body points.
LAYOUT = {"face_points": list(mocap.FACE_POINTS), "blendshapes": list(mocap.BLENDSHAPES),
          "pose_points": list(mocap.POSE.values())}

# What each control is, in words, for the phone to say what the character learned.
MOVES = {"head_yaw": "turns its head", "head_pitch": "nods", "head_roll": "tilts its head",
         "gaze_x": "looks around", "gaze_y": "looks up or down", "brows": "moves its brows", "smile": "smiles",
         "eye_smile": "smiles with its eyes", "mouth_open": "opens its mouth", "shoulders": "shrugs",
         "body_roll": "leans", "arm_left": "moves an arm", "arm_left_b": "moves an arm",
         "arm_right": "moves an arm", "arm_right_b": "moves an arm"}


class LinkError(Exception):
    """A phone's code that opens nothing: wrong, used up or out of time (`locked`: too many wrong ones)."""

    def __init__(self, message, locked=False):
        super().__init__(message)
        self.locked = locked


class Recorder:
    """One recording at a time: the code a phone uses, what it has sent, and what came of it."""

    def __init__(self, lifetime=LIFETIME, clock=time.time):
        self.lifetime = lifetime
        self.clock = clock
        self.session = None
        self.wrong = collections.deque()

    def start(self):
        """A new code (any earlier one stops working)."""
        now = self.clock()
        self.session = {"code": "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH)),
                        "expires": now + self.lifetime, "state": "waiting", "entry": None, "tracks": None,
                        "saved": None}
        return self.session

    def end(self):
        self.session = None

    def current(self):
        """The recording under way, or None once it has run out of time."""
        if self.session and self.session["expires"] <= self.clock():
            self.session = None
        return self.session

    def find(self, code):
        """The recording a phone's code opens. Raises LinkError otherwise; after too many wrong codes in a
        minute, none is accepted for a while, so a code cannot be found by trying them all."""
        now = self.clock()
        while self.wrong and self.wrong[0] < now - 60:
            self.wrong.popleft()
        if len(self.wrong) >= WRONG_CODES_A_MINUTE:
            raise LinkError("too many wrong codes: wait a minute, then open the link again", locked=True)
        session = self.current()
        given = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
        if session and secrets.compare_digest(given, session["code"]):
            return session
        self.wrong.append(now)
        raise LinkError("this link has ended: ask for a new one on the device's settings page (Animation tab)")


def _number(value, low, high, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValueError("%s is not a number from %s to %s" % (what, low, high))
    return float(value)


def frames_from_payload(body):
    """Per-frame controls and the frame rate, from what the phone sends:

        {"fps": 12, "width": 720, "height": 1280, "frames": [
            {"face": [[x, y], ...] (mocap.FACE_POINTS, in order) or null,
             "blend": [score, ...] (mocap.BLENDSHAPES, in order) or null,
             "pose": [[x, y, z, visibility], ...] (mocap.POSE's joints, in order) or null}, ...]}

    Points are MediaPipe's: fractions of the picture's width and height (z of its width)."""
    if not isinstance(body, dict):
        raise ValueError("send the landmarks as an object")
    fps = _number(body.get("fps"), FPS_RANGE[0], FPS_RANGE[1], "fps")
    width, height = picture_size(body)
    raw = body.get("frames")
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("send the landmarks of at least two frames")
    if len(raw) > MAX_FRAMES or (len(raw) - 1) / fps > MAX_SECONDS + 0.5:
        raise ValueError("a gesture can be %d seconds at most" % MAX_SECONDS)
    return [frame_controls(f, width, height, i) for i, f in enumerate(raw)], fps


def picture_size(body):
    """The picture's width and height from what a phone sends, checked."""
    return _number(body.get("width"), 16, 8192, "width"), _number(body.get("height"), 16, 8192, "height")


def frame_controls(f, width, height, i=0):
    """One frame of what a phone sends (as in frames_from_payload), checked, as the stage's controls."""
    if not isinstance(f, dict):
        raise ValueError("frame %d is not an object" % i)
    face = blend = pose = None
    if f.get("face") is not None:
        points = f["face"]
        if not isinstance(points, list) or len(points) != len(mocap.FACE_POINTS):
            raise ValueError("frame %d: the face needs %d points" % (i, len(mocap.FACE_POINTS)))
        face = {}
        for index, p in zip(mocap.FACE_POINTS, points):
            if not isinstance(p, list) or len(p) != 2:
                raise ValueError("frame %d: a face point is [x, y]" % i)
            face[index] = (_number(p[0], -2, 3, "x") * width, _number(p[1], -2, 3, "y") * height)
    if f.get("blend") is not None:
        scores = f["blend"]
        if not isinstance(scores, list) or len(scores) != len(mocap.BLENDSHAPES):
            raise ValueError("frame %d: the face needs %d expression scores" % (i, len(mocap.BLENDSHAPES)))
        blend = {name: _number(v, 0, 1, "an expression score") for name, v in zip(mocap.BLENDSHAPES, scores)}
    if f.get("pose") is not None:
        joints = list(mocap.POSE)
        points = f["pose"]
        if not isinstance(points, list) or len(points) != len(joints):
            raise ValueError("frame %d: the body needs %d points" % (i, len(joints)))
        pose = {}
        for name, p in zip(joints, points):
            if not isinstance(p, list) or len(p) != 4:
                raise ValueError("frame %d: a body point is [x, y, z, visibility]" % i)
            pose[name] = (_number(p[0], -2, 3, "x") * width, _number(p[1], -2, 3, "y") * height,
                          _number(p[2], -5, 5, "z") * width, _number(p[3], 0, 1, "visibility"))
    return mocap.frame_controls(face, blend, pose)


def fit_keys(tracks, limit=motions.MAX_KEYS):
    """Tracks with at most `limit` keys each: a busy one is thinned until it fits."""
    out = {}
    for name, keys in tracks.items():
        epsilon = mocap.SIMPLIFY
        while len(keys) > limit:
            epsilon *= 1.5
            keys = [[t, v] for t, v in mocap._simplify([tuple(k) for k in keys], epsilon)]
        out[name] = keys
    return out


def make_clip(body):
    """The clip's tracks from the phone's landmarks, and what it does in words. Raises ValueError."""
    frames, fps = frames_from_payload(body)
    found = sum(1 for f in frames if f) / len(frames)
    if found < FOUND_AT_LEAST:
        raise ValueError("a person was found in only %d%% of the video: film again facing the camera, head and "
                         "shoulders in view, in good light" % round(100 * found))
    tracks = fit_keys(mocap.clip_from_frames(frames, fps))
    return tracks, describe(tracks)


def describe(tracks, most=4):
    """What the clip does, in words: its main movements, the biggest first (not every small one)."""
    peaks = {name: max(abs(v) for _, v in keys) for name, keys in tracks.items()}
    enough = max(0.25, 0.35 * max(peaks.values()))
    seen = []
    for name in sorted(peaks, key=lambda n: -peaks[n]):
        words = MOVES.get(name, name.replace("_", " "))
        if (peaks[name] >= enough or not seen) and words not in seen and len(seen) < most:
            seen.append(words)
    return seen


def entry(tracks, mirror=False, kind="gesture", words=None):
    """The library entry for the tracks, mirrored if asked, checked as the gesture editor checks one."""
    if mirror:
        tracks = mocap.mirror_tracks(tracks)
    return mocap.entry_for(tracks, kind, words)


def gesture_name(label):
    """A gesture's name from what the owner called it: "Little dance!" -> little_dance."""
    name = re.sub(r"[^a-z]+", "_", str(label or "").lower()).strip("_")[:40].strip("_")
    return name or "my_gesture"
